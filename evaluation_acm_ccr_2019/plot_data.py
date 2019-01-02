# MIT License
#
# Copyright (c) 2016-2018 Matthias Rost, Elias Doehne, Alexander Elvers
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
#

import os
import pickle
from collections import namedtuple
import numpy as np
from alib import solutions, util

from vnep_approx import randomized_rounding_triumvirate, vine, treewidth_model

REQUIRED_FOR_PICKLE = solutions  # this prevents pycharm from removing this import, which is required for unpickling solutions

ReducedOfflineViNEResultCollection = namedtuple(
    "ReducedOfflineViNEResultCollection",
    [
        "total_runtime",
        "profit",
        "mean_runtime_per_request",
        "std_dev_runtime_per_request",
        "num_is_embedded",
        "num_initial_lp_failed",
        "num_node_mapping_failed",
        "num_edge_mapping_failed",
        "embedding_ratio",
        "original_number_requests",
        "num_req_with_profit",
        "load",
    ],
)

ReducedRandRoundSepLPOptDynVMPCollectionResult = namedtuple(
    "ReducedRandRoundSepLPOptDynVMPCollectionResult",
    [
        "lp_time_preprocess",
        "lp_time_optimization",
        "lp_status",
        "lp_profit",
        "lp_generated_columns",
        "best_solution_load",
        "max_node_loads",
        "max_edge_loads",
        "rounding_runtimes",
        "profits",
        "best_solution_embedding_ratio",
    ],
)

logger = util.get_logger(__name__, make_file=False, propagate=True)


class OfflineViNEResultCollectionReducer(object):

    def __init__(self):
        pass

    def reduce_vine_result_collection(self, baseline_solutions_input_pickle_name,
                                      reduced_baseline_solutions_output_pickle_name=None):

        baseline_solutions_input_pickle_path = os.path.join(
            util.ExperimentPathHandler.INPUT_DIR,
            baseline_solutions_input_pickle_name
        )

        if reduced_baseline_solutions_output_pickle_name is None:
            file_basename = os.path.basename(baseline_solutions_input_pickle_path).split(".")[0]
            reduced_baseline_solutions_output_pickle_path = os.path.join(util.ExperimentPathHandler.OUTPUT_DIR,
                                                                         file_basename + "_reduced.pickle")
        else:
            reduced_baseline_solutions_output_pickle_path = os.path.join(util.ExperimentPathHandler.OUTPUT_DIR,
                                                                         baseline_solutions_input_pickle_name)

        logger.info("\nWill read from ..\n\t{} \n\t\tand store reduced data into\n\t{}\n".format(baseline_solutions_input_pickle_path, reduced_baseline_solutions_output_pickle_path))

        logger.info("Reading pickle file at {}".format(baseline_solutions_input_pickle_path))
        with open(baseline_solutions_input_pickle_path, "rb") as input_file:
            scenario_solution_storage = pickle.load(input_file)

        ssd = scenario_solution_storage.algorithm_scenario_solution_dictionary
        ssd_reduced = {}
        for algorithm in ssd.keys():
            logger.info(".. Reducing results of algorithm {}".format(algorithm))
            ssd_reduced[algorithm] = {}
            for scenario_id in ssd[algorithm].keys():
                logger.info("   .. handling scenario {}".format(scenario_id))
                ssd_reduced[algorithm][scenario_id] = {}
                for exec_id in ssd[algorithm][scenario_id].keys():
                    ssd_reduced[algorithm][scenario_id][exec_id] = {}
                    params, scenario = scenario_solution_storage.scenario_parameter_container.scenario_triple[scenario_id]
                    solution_collection = ssd[algorithm][scenario_id][exec_id].get_solution()
                    for vine_settings, result_list in solution_collection.iteritems():
                        ssd_reduced[algorithm][scenario_id][exec_id][vine_settings] = []
                        for (result_index, result) in result_list:
                            solution_object = result.get_solution()
                            mappings = solution_object.request_mapping
                            number_of_embedded_reqs = 0
                            number_of_req_profit = 0
                            number_of_requests = len(solution_object.scenario.requests)

                            load = _initialize_load_dict(scenario)
                            for req in solution_object.scenario.requests:
                                if req.profit > 0.001:
                                    number_of_req_profit += 1
                                req_mapping = mappings[req]
                                if req_mapping is not None and req_mapping.is_embedded:
                                    number_of_embedded_reqs += 1
                                    _compute_mapping_load(load, req, req_mapping)

                            embedding_ratio = number_of_embedded_reqs / float(number_of_requests)

                            num_edge_mapping_failed, num_initial_lp_failed, num_is_embedded, num_node_mapping_failed = self._count_mapping_status(result)
                            assert num_is_embedded == number_of_embedded_reqs

                            reduced = ReducedOfflineViNEResultCollection(
                                load=load,
                                total_runtime=result.total_runtime,
                                profit=result.profit,
                                mean_runtime_per_request=np.mean(result.runtime_per_request.values()),
                                std_dev_runtime_per_request=np.std(result.runtime_per_request.values()),
                                num_is_embedded=num_is_embedded,
                                num_initial_lp_failed=num_initial_lp_failed,
                                num_node_mapping_failed=num_node_mapping_failed,
                                num_edge_mapping_failed=num_edge_mapping_failed,
                                embedding_ratio=embedding_ratio,
                                num_req_with_profit=number_of_req_profit,
                                original_number_requests=number_of_requests,
                            )
                            ssd_reduced[algorithm][scenario_id][exec_id][vine_settings].append(reduced)
        del scenario_solution_storage.scenario_parameter_container.scenario_list
        del scenario_solution_storage.scenario_parameter_container.scenario_triple
        scenario_solution_storage.algorithm_scenario_solution_dictionary = ssd_reduced

        logger.info("Writing result pickle to {}".format(reduced_baseline_solutions_output_pickle_path))
        with open(reduced_baseline_solutions_output_pickle_path, "wb") as f:
            pickle.dump(scenario_solution_storage, f)
        logger.info("All done.")
        return scenario_solution_storage

    def _count_mapping_status(self, solution):
        num_is_embedded = 0
        num_initial_lp_failed = 0
        num_node_mapping_failed = 0
        num_edge_mapping_failed = 0
        for status in solution.mapping_status_per_request.values():
            if status == vine.ViNEMappingStatus.is_embedded:
                num_is_embedded += 1
            elif status == vine.ViNEMappingStatus.initial_lp_failed:
                num_initial_lp_failed += 1
            elif status == vine.ViNEMappingStatus.node_mapping_failed:
                num_node_mapping_failed += 1
            elif status == vine.ViNEMappingStatus.edge_mapping_failed:
                num_edge_mapping_failed += 1
            else:
                raise ValueError("Unexpected mapping status!")
        return num_edge_mapping_failed, num_initial_lp_failed, num_is_embedded, num_node_mapping_failed


class RandRoundSepLPOptDynVMPCollectionResultReducer(object):

    def __init__(self):
        pass

    def reduce_dynvmp_result_collection(self,
                                        randround_solutions_input_pickle_name,
                                        reduced_randround_solutions_output_pickle_name=None):

        randround_solutions_input_pickle_path = os.path.join(util.ExperimentPathHandler.INPUT_DIR,
                                                             randround_solutions_input_pickle_name)

        if reduced_randround_solutions_output_pickle_name is None:
            file_basename = os.path.basename(randround_solutions_input_pickle_path).split(".")[0]
            reduced_randround_solutions_output_pickle_path = os.path.join(util.ExperimentPathHandler.OUTPUT_DIR,
                                                                          file_basename + "_reduced.pickle")
        else:
            reduced_randround_solutions_output_pickle_path = os.path.join(util.ExperimentPathHandler.OUTPUT_DIR,
                                                                          randround_solutions_input_pickle_name)

        logger.info("\nWill read from ..\n\t{} \n\t\tand store reduced data into\n\t{}\n".format(
            randround_solutions_input_pickle_path, reduced_randround_solutions_output_pickle_path))

        logger.info("Reading pickle file at {}".format(randround_solutions_input_pickle_path))
        with open(randround_solutions_input_pickle_path, "rb") as f:
            sss = pickle.load(f)

        sss.scenario_parameter_container.scenario_list = None
        sss.scenario_parameter_container.scenario_triple = None

        for alg, scenario_solution_dict in sss.algorithm_scenario_solution_dictionary.iteritems():
            logger.info(".. Reducing results of algorithm {}".format(alg))
            for sc_id, ex_param_solution_dict in scenario_solution_dict.iteritems():
                logger.info("   .. handling scenario {}".format(sc_id))
                for ex_id, solution in ex_param_solution_dict.iteritems():
                    compressed = self.reduce_single_solution(solution)
                    ex_param_solution_dict[ex_id] = compressed

        logger.info("Writing result pickle to {}".format(reduced_randround_solutions_output_pickle_path))
        with open(reduced_randround_solutions_output_pickle_path, "w") as f:
            pickle.dump(sss, f)
        logger.info("All done.")
        return sss

    def reduce_single_solution(self, solution):
        if solution is None:
            return None
        assert isinstance(solution, treewidth_model.RandRoundSepLPOptDynVMPCollectionResult)

        max_node_loads = {}
        max_edge_loads = {}
        rounding_runtimes = {}
        best_solution_embedding_ratio = {}
        profits = {}
        best_solution_load = {}

        for algorithm_sub_parameters, rounding_result_list in solution.solutions.items():
            max_node_loads[algorithm_sub_parameters] = []
            max_edge_loads[algorithm_sub_parameters] = []
            rounding_runtimes[algorithm_sub_parameters] = []
            profits[algorithm_sub_parameters] = []

            best_solution_embedding_ratio[algorithm_sub_parameters] = -1
            best_solution_load[algorithm_sub_parameters] = _initialize_load_dict(solution.scenario)
            for rounding_result in rounding_result_list:
                assert isinstance(rounding_result, treewidth_model.RandomizedRoundingSolution)
                number_of_embedded_reqs = 0
                if rounding_result.solution is not None:
                    # assumes that only the best solution is saved fully, as is the case in the current implementation
                    assert isinstance(rounding_result.solution, solutions.IntegralScenarioSolution)
                    for req, mapping in rounding_result.solution.request_mapping.items():
                        if mapping is not None and mapping.is_embedded:
                            _compute_mapping_load(
                                best_solution_load[algorithm_sub_parameters], req, mapping
                            )
                            number_of_embedded_reqs += 1

                    best_solution_embedding_ratio[algorithm_sub_parameters] = number_of_embedded_reqs / float(len(solution.scenario.requests))

                max_node_loads[algorithm_sub_parameters].append(rounding_result.max_node_load)
                max_edge_loads[algorithm_sub_parameters].append(rounding_result.max_edge_load)
                rounding_runtimes[algorithm_sub_parameters].append(rounding_result.time_to_round_solution)
                profits[algorithm_sub_parameters].append(rounding_result.profit)

        assert isinstance(solution.lp_computation_information, treewidth_model.SeparationLPSolution)
        # TODO Check which information is actually of interest
        # TODO Some of the data can be reduced further (store only mean and std. dev.)
        solution = ReducedRandRoundSepLPOptDynVMPCollectionResult(
            lp_time_preprocess=solution.lp_computation_information.time_preprocessing,
            lp_time_optimization=solution.lp_computation_information.time_optimization,
            lp_status=solution.lp_computation_information.status,
            lp_profit=solution.lp_computation_information.profit,
            lp_generated_columns=solution.lp_computation_information.number_of_generated_mappings,
            best_solution_load=best_solution_load,
            best_solution_embedding_ratio=best_solution_embedding_ratio,
            max_node_loads=max_node_loads,
            max_edge_loads=max_edge_loads,
            rounding_runtimes=rounding_runtimes,
            profits=profits,
        )
        return solution


def _initialize_load_dict(scenario):
    load = dict([((u, v), 0.0) for (u, v) in scenario.substrate.edges])
    for u in scenario.substrate.nodes:
        for t in scenario.substrate.node[u]['supported_types']:
            load[(t, u)] = 0.0
    return load


def _compute_mapping_load(load, req, req_mapping):
    for i, u in req_mapping.mapping_nodes.iteritems():
        node_demand = req.get_node_demand(i)
        load[(req.get_type(i), u)] += node_demand

    if isinstance(req_mapping, solutions.Mapping):
        _compute_mapping_edge_load_unsplittable(load, req, req_mapping)
    elif isinstance(req_mapping, vine.SplittableMapping):
        _compute_mapping_edge_load_splittable(load, req, req_mapping)
    return load


def _compute_mapping_edge_load_unsplittable(load, req, req_mapping):
    for ij, sedge_list in req_mapping.mapping_edges.iteritems():
        edge_demand = req.get_edge_demand(ij)
        for uv in sedge_list:
            load[uv] += edge_demand


def _compute_mapping_edge_load_splittable(load, req, req_mapping):
    for ij, edge_vars_dict in req_mapping.mapping_edges.iteritems():
        edge_demand = req.get_edge_demand(ij)
        for uv, x in edge_vars_dict.items():
            load[uv] += edge_demand * x