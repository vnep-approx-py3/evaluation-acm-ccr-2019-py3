# MIT License
#
# Copyright (c) 2016-2018 Matthias Rost, Elias Doehne
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import itertools
import multiprocessing as mp
import os
import random
import time
import logging

import vnep_approx.treewidth_model as twm
import yaml

from alib import datamodel, util

try:
    import pickle as pickle
except ImportError:
    import pickle

random.seed(0)
logger = logging.getLogger(__name__)

""" This module contains functionality to study the treewidth of random graphs.
    Concretely, it allows to compute the treewidth of a series of graphs defined by a
    parameter space defined in terms of the number of 
    - nodes, 
    - connection probability, and
    - the number of repetitions."""


def run_experiment_from_yaml(parameter_file, output_file_base_name, threads, timeout,remove_intermediate_solutions):
    param_space = yaml.load(parameter_file)
    sg = SimpleTreeDecompositionExperiment(threads, output_file_base_name, timeout, remove_intermediate_solutions)
    sg.start_experiments(param_space)


class SimpleTreeDecompositionExperiment(object):
    """ Generates the full parameter space and executes the experiments given the number of threads passed to the constructor.
    Mostly copied from alib.scenariogeneration, but uses the build_scenario_simple function defined below instead."""

    def __init__(self, threads, output_file_base, timeout=None, remove_process_pickles=False):
        self.threads = threads
        self.output_file_base_name = output_file_base
        self.output_filenames = [
            self.output_file_base_name.format(process_index=process_index)
            for process_index in range(self.threads)
        ]
        self.timeout = timeout
        self.remove_process_pickles = remove_process_pickles

    def start_experiments(self, scenario_parameter_space):
        number_of_repetitions = 1
        if 'scenario_repetition' in scenario_parameter_space:
            number_of_repetitions = scenario_parameter_space['scenario_repetition']
            del scenario_parameter_space['scenario_repetition']

        random_seed_base = 0
        if 'random_seed_base' in scenario_parameter_space:
            random_seed_base = scenario_parameter_space['random_seed_base']
            del scenario_parameter_space['random_seed_base']

        store_graphs_of_treewidth = []
        if 'store_graphs_of_treewidth' in scenario_parameter_space:
            store_graphs_of_treewidth = scenario_parameter_space['store_graphs_of_treewidth']

        store_only_connected_graphs = False
        if 'store_only_connected_graphs' in scenario_parameter_space:
            store_only_connected_graphs = scenario_parameter_space['store_only_connected_graphs']

        processes = [mp.Process(
            target=execute_single_experiment,
            name="worker_{}".format(process_index),
            args=(
                process_index,
                self.threads,
                scenario_parameter_space,
                random_seed_base + process_index,
                number_of_repetitions,
                self.output_filenames[process_index],
                self.timeout,
                store_graphs_of_treewidth,
                store_only_connected_graphs,
            )) for process_index in range(self.threads)]

        for p in processes:
            logger.info("Starting process {}".format(p))
            p.start()

        for p in processes:
            p.join()

        self.combine_results_to_overall_pickle()

    def combine_results_to_overall_pickle(self):
        logger.info("Combining results")
        result_dict = {}
        for fname in self.output_filenames:
            with open(fname, "rb") as f:
                try:
                    while True:
                        result = pickle.load(f)
                        if result.num_nodes not in result_dict:
                            result_dict[result.num_nodes] = {}
                        if result.edge_probability not in result_dict[result.num_nodes]:
                            result_dict[result.num_nodes][result.edge_probability] = []
                        result_dict[result.num_nodes][result.edge_probability].append(result)
                except EOFError:
                    pass

        pickle_file = self.output_file_base_name.format(process_index="aggregated_results")
        logger.info("Writing combined Pickle to {}".format(pickle_file))
        with open(pickle_file, "wb") as f:
            pickle.dump(result_dict, f)

        if self.remove_process_pickles:
            for fname in self.output_filenames:
                if os.path.exists(fname):
                    logger.info("Removing intermediate process result file {}".format(fname))
                    os.remove(fname)


def execute_single_experiment(process_index,
                              num_processes,
                              parameter_space,
                              random_seed,
                              repetitions,
                              out_file,
                              timeout,
                              store_graphs_of_treewidth,
                              store_only_connected_graphs):
    ''' Main function for computing the treewidths of random graphs. This function is called in its own process (see above).
        Each process generates and stores only the results lying in its range.
    '''
    random.seed(random_seed)
    num_nodes_list = parameter_space["number_of_nodes"]
    connection_probabilities_list = parameter_space["probability"]

    graph_generator = SimpleRandomGraphGenerator()

    logger = util.get_logger("worker_{}_pid_{}".format(process_index, os.getpid()), propagate=False, make_file=True)

    for repetition_index, params in enumerate(itertools.product(
            num_nodes_list,
            connection_probabilities_list,
            list(range(repetitions))
    )):
        if repetition_index % num_processes == process_index:
            num_nodes, prob, repetition_index = params
            logger.info("Processing graph with {} nodes and {} prob, rep {} (timeout for computation: {})".format(num_nodes, prob, repetition_index, timeout))
            gen_time_start = time.time()
            graph = graph_generator.generate_graph(num_nodes, prob)
            gen_time = time.time() - gen_time_start

            algorithm_time_start = time.time()
            tree_decomp = twm.compute_tree_decomposition(graph, logger=logger, timeout=timeout)
            algorithm_time = time.time() - algorithm_time_start


            treewidth = None

            if tree_decomp is not None:
                assert tree_decomp.is_tree_decomposition(graph)
                treewidth = tree_decomp.width

            graph_edge_representation = None

            if treewidth is not None and treewidth in store_graphs_of_treewidth:
                #generally interesting graph: compute edge_representation
                graph_edge_representation = graph.get_edge_representation()
                if store_only_connected_graphs:
                    #if we are only interested in connected graphs, then we only store the representation if it is connected
                    if not datamodel.is_connected_undirected_edge_representation(graph_edge_representation):
                        graph_edge_representation = None

            if graph_edge_representation is not None:
                logger.debug("Storing graph of treewidth {} and number of nodes {}.".format(treewidth, num_nodes))


            result = TreeDecompositionAlgorithmResult(
                num_nodes=num_nodes,
                edge_probability=prob,
                repetition_index=repetition_index,
                undirected_graph_edge_representation=graph_edge_representation,
                treewidth=treewidth,
                runtime_treewidth_computation=algorithm_time,
            )
            logger.info("Result: {}".format(result.short_representation()))

            with open(out_file, "ab") as f:
                pickle.dump(result, f)

            del graph
            if tree_decomp is not None:
                del tree_decomp
            if graph_edge_representation is not None:
                del graph_edge_representation


class SimpleRandomGraphGenerator(object):
    """
    This class generates directed graphs uniformly at random by first introducing the selected number of nodes and
    then adding directed (!) edges uniformly at random.
    Mostly copied from alib.scenariogeneration, since much of the original code adds unnecessary complexity (costs, demands, etc)
    """

    EXPECTED_PARAMETERS = [
        "number_of_nodes",
        "probability"
    ]

    def __init__(self):
        pass

    def generate_graph(self, number_of_nodes, connection_probability):
        name = "req"
        undirected_graph = datamodel.UndirectedGraph(name)

        # create nodes
        for i in range(1, number_of_nodes + 1):
            undirected_graph.add_node(str(i))

        # create edges
        for i in undirected_graph.nodes:
            for j in undirected_graph.nodes:
                if int(j) <= int(i):
                    continue #as we are undirected
                if random.random() <= connection_probability:
                    undirected_graph.add_edge(i, j)
        return undirected_graph


class TreeDecompositionAlgorithmResult(object):
    ''' The result of a single tree decomposition computation.

    '''
    def __init__(
            self,
            num_nodes,
            edge_probability,
            repetition_index,
            undirected_graph_edge_representation,
            treewidth,
            runtime_treewidth_computation,
    ):
        #the 3 generation parameters:
        self.num_nodes = num_nodes
        self.edge_probability = edge_probability
        self.repetition_index = repetition_index

        #the randomly generated graph and its treewidth and the runtime for the tree decomposition
        self.undirected_graph_edge_representation = undirected_graph_edge_representation
        self.treewidth = treewidth
        self.runtime_treewidth_computation = runtime_treewidth_computation

    def short_representation(self):
        return "Tree Decomposition Result for |V|: {}, edge probability: {}, repetition index: {}\n\ttreewidth: {}\n\truntime: {}\n".format(
            self.num_nodes,
            self.edge_probability,
            self.repetition_index,
            self.treewidth,
            self.runtime_treewidth_computation,
        )

    def __str__(self):
        return "Tree Decomposition Result for |V|: {}, edge probability: {}, repetition index: {}\n\tgraph: {}\n\ttreewidth: {}\n\truntime: {}\n".format(
            self.num_nodes,
            self.edge_probability,
            self.repetition_index,
            self.undirected_graph_edge_representation,
            self.treewidth,
            self.runtime_treewidth_computation,
        )




