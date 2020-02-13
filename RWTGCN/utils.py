import numpy as np
import pandas as pd
import scipy.sparse as sp
import networkx as nx
import os, json, time, random
import torch
from sympy import sieve

def check_and_make_path(to_make):
    if not os.path.exists(to_make):
        os.makedirs(to_make)

def read_edgelist_from_dataframe(filename, full_node_list):
    df = pd.read_csv(filename, sep='\t')
    # dataframe['weight'] = 1.0
    graph = nx.from_pandas_edgelist(df, "from_id", "to_id", edge_attr='weight',
                                    create_using=nx.Graph)
    graph.add_nodes_from(full_node_list)
    return graph

def normalize(mx):
    """Row-normalize sparse matrix"""
    rowsum = np.array(mx.sum(1))
    r_inv = np.power(rowsum, -1).flatten()
    r_inv[np.isinf(r_inv)] = 0.
    r_mat_inv = sp.diags(r_inv)
    mx = r_mat_inv.dot(mx)
    return mx

def sparse_mx_to_torch_sparse_tensor(sparse_mx):
    """Convert a scipy sparse matrix to a torch sparse tensor."""
    sparse_mx = sparse_mx.tocoo().astype(np.float32)
    indices = torch.from_numpy(
        np.vstack((sparse_mx.row, sparse_mx.col)).astype(np.int64))
    values = torch.from_numpy(sparse_mx.data)
    shape = torch.Size(sparse_mx.shape)
    return torch.sparse.FloatTensor(indices, values, shape)


def get_normalize_PPMI_adj(spmat):
    adj = normalize(spmat + sp.eye(spmat.shape[0]))
    adj = sparse_mx_to_torch_sparse_tensor(adj)
    return adj

# def round_func(val):
#     from decimal import Decimal, ROUND_HALF_UP
#     decimal_val = Decimal(val).quantize(Decimal('0.00000001'), rounding=ROUND_HALF_UP) \
#                                 .quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP)
#     return str(decimal_val)

# get unique values from array with tolerance=1e-12
def uniquetol(data_arr, cluster=False):
    idx_arr = np.argsort(data_arr)
    data_num = len(idx_arr)
    idx_order_dict = dict(zip(idx_arr.tolist(), np.ones(data_num).tolist()))
    pos = 0
    value = 1
    max_num = data_num
    while pos < max_num:
        idx_order_dict[idx_arr[pos]] = value
        while(pos + 1 < max_num):
            if np.abs(data_arr[idx_arr[pos]] - data_arr[idx_arr[pos + 1]]) >= 1e-12:
                value += 1
                break
            idx_order_dict[idx_arr[pos + 1]] = value
            pos += 1
        pos += 1
    cluster_dict = dict()
    def map_func(idx):
        label = idx_order_dict[idx]
        if cluster == True:
            if label not in cluster_dict:
                cluster_dict[label] = [idx]
            else:
                cluster_dict[label].append(idx)
        return label
    vfunc = np.vectorize(map_func)
    labels = vfunc(np.arange(data_num))
    if cluster == True:
        return labels, cluster_dict
    return labels

def wl_transform(spadj, labels, cluster=False):
    # the ith entry is equal to the 2 ^ (i - 1)'th prime
    prime_list = [2, 3, 7, 19, 53, 131, 311, 719, 1619, 3671, 8161, 17863, 38873, 84017, 180503,
                  386093, 821641, 1742537, 3681131, 7754077, 16290047]

    label_num = np.max(labels)
    # generate enough primes to have one for each label in the graph
    max_prime = prime_list[int(np.ceil(np.log2(label_num)))]
    primes = list(sieve.primerange(1, max_prime + 1))
    prime_dict = dict(zip(np.arange(1, len(primes) + 1).tolist(), primes))

    def map_func(val, map_dict):
        return np.log(map_dict[val])
    vfunc = np.vectorize(map_func)
    log_primes = vfunc(labels, prime_dict).reshape(-1, 1)

    signatures = labels + spadj.dot(log_primes).reshape(-1)
    #print(signatures)
    # map signatures to integers counting from 1
    try:
        import RWTGCN.preprocessing.helper as helper
        return helper.uniquetol(signatures, cluster=cluster)
    except:
        pass
    return uniquetol(signatures, cluster=cluster)


def random_walk(original_graph, structural_graph, node_list,
                walk_dir_path, freq_dir_path, f_name, tensor_dir_path,
                walk_length, walk_time, prob, weight):

    original_graph_dict, structural_graph_dict = {}, {}
    # preprocessing
    for node in node_list:
        original_neighbors = list(original_graph.neighbors(node))
        original_weight = np.array([original_graph[node][neighbor]['weight'] for neighbor in original_neighbors])
        original_graph_dict[node] = {'neighbor': original_neighbors}
        original_graph_dict[node]['weight'] = original_weight / original_weight.sum()

        structural_neighbors = list(structural_graph.neighbors(node))
        structural_weight = np.array([structural_graph[node][neighbor]['weight'] for neighbor in structural_neighbors])
        structural_graph_dict[node] = {'neighbor': structural_neighbors}
        structural_graph_dict[node]['weight'] = structural_weight / structural_weight.sum()

    node_num = len(node_list)
    node2idx_dict = dict(zip(node_list, np.arange(node_num).tolist()))
    step_adj_list, node_count_list, all_count_list = [{}], [[]], [-1]

    num = node_num
    maxnum = walk_length + 1
    for i in range(1, maxnum):
        step_adj_list.append({})
        node_count_list.append({})
        all_count_list.append(0)

    # random walk
    for nidx in range(num):
        for iter in range(walk_time):
            start_node = node_list[nidx]
            eps = 1e-8
            walk = [start_node]
            cnt = 1
            while cnt < maxnum:
                cur = walk[-1]
                rd = np.random.random()
                if rd <= prob + eps:
                    neighbors = original_graph_dict[cur]['neighbor']
                    weights = original_graph_dict[cur]['weight']
                else:
                    neighbors = structural_graph_dict[cur]['neighbor']
                    weights = structural_graph_dict[cur]['weight']
                if len(neighbors) == 0:
                    break
                walk.append(np.random.choice(neighbors, p=weights) if weight else np.random.choice(neighbors))
                cnt += 1

            seq_len = len(walk)
            for i in range(seq_len):
                for j in range(i + 1, seq_len):
                    step = j - i
                    # generate sparse node co-occurrence matrices
                    edge_dict = step_adj_list[step]
                    node_count = node_count_list[step]
                    key = (walk[i], walk[j])
                    edge_dict[key] = edge_dict.get(key, 0) + 1
                    key = (walk[j], walk[i])
                    edge_dict[key] = edge_dict.get(key, 0) + 1

                    node_count[walk[i]] = node_count.get(walk[i], 0) + 1
                    node_count[walk[j]] = node_count.get(walk[j], 0) + 1
                    all_count_list[step] += 2
    del original_graph_dict
    del structural_graph_dict

    node_freq_arr = np.zeros(num, dtype=int)
    for nidx in range(num):
        for idx in range(1, maxnum):
            node_dict = node_count_list[idx]
            if node_list[nidx] in node_dict:
                node_freq_arr[nidx] += node_dict[node_list[nidx]]
    tot_freq = node_freq_arr.sum()
    Z = 0.00001
    neg_node_list = []
    for nidx in range(num):
        rep_num = int(((node_freq_arr[nidx] / tot_freq) ** 0.75) / Z)
        neg_node_list += [nidx] * rep_num
    walk_file_path = os.path.join(freq_dir_path, f_name.split('.')[0] + '.json')
    with open(walk_file_path, 'w') as fp:
        json.dump(neg_node_list, fp)
    del neg_node_list
    del node_freq_arr

    def trans(node):
        return node2idx_dict[node]

    trans_func = np.vectorize(trans)

    edge_set = set()
    for idx in range(1, maxnum):
        edge_dict = step_adj_list[idx]
        node_count = node_count_list[idx]
        all_count = all_count_list[idx]
        edge_set |= set(edge_dict.keys())

        for edge, cnt in edge_dict.items():
            from_node = edge[0]
            to_node = edge[1]
            res = np.log(cnt * all_count / (node_count[from_node] * node_count[to_node]))
            edge_dict[edge] = res if res > 0 else res

        edge_arr = trans_func(np.array(list(edge_dict.keys())))
        weight_arr = np.array(list(edge_dict.values()))
        spmat = sp.coo_matrix((weight_arr, (edge_arr[:, 0], edge_arr[:, 1])), shape=(node_num, node_num))
        sp.save_npz(os.path.join(tensor_dir_path, str(idx) + ".npz"), spmat)

    del step_adj_list
    del node_count_list
    del all_count_list
    walk_file_path = os.path.join(walk_dir_path, f_name.split('.')[0] + '.json')
    edge_list = list(edge_set)
    with open(walk_file_path, 'w') as fp:
        json.dump(edge_list, fp)

def sigmoid(x):
    s = 1 / (1 + np.exp(-x))
    return s

def get_structural_neighbors(color_arr, output_file, cluster_dict, cluster_len_dict, idx2nid_dict,
                             max_neighbor_num):
    structural_edges_dict = dict()
    structural_edge_list = []

    node_num = color_arr.shape[0]
    for i in range(node_num):
        row_arr = color_arr[i, :]
        from_node = idx2nid_dict[i]
        cluster_type = row_arr[0]
        cluster_list = cluster_dict[cluster_type]
        cluster_num = cluster_len_dict[cluster_type]
        if cluster_num == 1:
            continue
        cnt = random.randint(1, min(cluster_num, max_neighbor_num))
        sampled_nodes = random.sample(cluster_list, cnt)

        for j in range(cnt):
            to_idx = sampled_nodes[j]
            to_node = idx2nid_dict[to_idx]
            key = (from_node, to_node)
            if from_node == to_node or key in structural_edges_dict:
                continue
            to_arr = color_arr[to_idx, :]
            weight = sigmoid(row_arr.dot(to_arr))
            structural_edge_list.append([from_node, to_node, weight])

    df_structural_edges = pd.DataFrame(structural_edge_list, columns=['from_id', 'to_id', 'weight'])
    print('edge num: ', df_structural_edges.shape[0])
    df_structural_edges.to_csv(output_file, sep='\t', index=False, header=True, float_format='%.3f')
    return


def separate(info='', sep='=', num=5):
    print()
    if len(info) == 0:
        print(sep * (2 * num))
    else:
        print(sep * num, info, sep * num)
    print()


if __name__ == '__main__':
    A = np.array([[0, 1, 1, 1, 1],
                  [1, 0, 1, 0, 0],
                  [1, 1, 0, 0, 0],
                  [1, 0, 0, 0, 1],
                  [1, 0, 0, 1, 0]])
    spmat = sp.csr_matrix(A)
    labels = np.ones(len(A))
    new_label = wl_transform(spmat, labels)
    print(new_label)

    A = np.array([[0, 1, 0, 1, 0, 0],
                  [1, 0, 1, 0, 0, 0],
                  [0, 1, 0, 1, 0, 0],
                  [1, 0, 1, 0, 1, 0],
                  [0, 0, 0, 1, 0, 1],
                  [0, 0, 0, 0, 1, 0]])
    spmat = sp.csr_matrix(A)
    labels = np.ones(len(A))
    new_label = wl_transform(spmat, labels)
    print(new_label)
    new_label = wl_transform(spmat, new_label)
    print(new_label)