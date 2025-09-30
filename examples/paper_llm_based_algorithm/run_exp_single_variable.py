# -*- coding: utf-8 -*-
"""Experiments with one single variable --> metric vs. variable plots"""

import os
import time
from typing import Union
from functools import partial
import copy
import json
import argparse
import numpy as np
import matplotlib.pyplot as plt

import agentscope
from agentscope.agent import DialogAgent
from agentscope.message import Msg

from src.utils import create_timestamp
from src.counting import trial_counting
from src.sorting import trial_sorting
from src.retrieval import trial_retrieval
from src.rag import trial_rag


MODEL_CONFIGS = "model_configs.json"
PATH_OUT = "./out/"


DICT_TRIAL_FUNC = {
    "counting": trial_counting,
    "sorting": trial_sorting,
    "retrieval": partial(trial_retrieval, no_needle=False),
    "retrieval_no_needle": partial(trial_retrieval, no_needle=True),
    "rag": trial_rag,
}


def exp_single_variable(
    base_config: dict,
    variable_name: str,
    lst_variable: list,
    error_metric_names: list,
    cost_metric_names: list,
) -> None:
    """Run multiple trials for each variable value, save results to files"""
    configs_results = []
    config = copy.deepcopy(base_config)
    ntrials = base_config["ntrials"]
    task = base_config["task"]
    llm_model = base_config["llm_model"]
    llm_model_folder_name = llm_model.replace(".", "-")
    n_base = base_config["n"]
    trial_func = DICT_TRIAL_FUNC[task]

    if base_config["save_results"]:
        subfolder = (
            f"exp_{task}_vary_{variable_name}_model_{llm_model_folder_name}"
        )
        if variable_name == "m":
            subfolder += f"_n_{n_base}"
        subfolder += "-" + create_timestamp() + "/data"
        path_folder = os.path.join(PATH_OUT, task, subfolder)
        os.makedirs(path_folder, exist_ok=True)

    if base_config["fix_seeds"]:
        seeds = np.random.randint(low=0, high=10000, size=ntrials)
        seeds = [int(s) for s in seeds]
    else:
        seeds = [None for _ in range(ntrials)]

    # HOTFIX for continuing experiment from where it was paused,
    # or adding more configs to the same experiment
    # seeds = []

    config["seeds"] = str(seeds)

    for variable in lst_variable:
        config[variable_name] = variable

        trials_results = []
        for i in range(ntrials):
            seed = seeds[i]

            wait_time = 5  # in seconds
            num_retries = 0
            max_retries = 5
            while num_retries < max_retries:
                num_retries += 1

                print(f"--- Pausing for {wait_time} seconds... ---")
                time.sleep(wait_time)

                try:
                    trial_result = trial_func(config=config, seed=seed)
                    break
                except Exception:
                    print(
                        "--- Errors occur. "
                        "Increase wait_time and retry... ---",
                    )
                    wait_time *= 2

            print("/// Trial result: ///")
            for k, v in trial_result.items():
                print(f"\t{k}: {v}")
            trials_results.append(trial_result)

        config_result = {
            "config": copy.deepcopy(config),
            "trials_results": trials_results,
        }
        configs_results.append(config_result)

        if base_config["save_results"]:
            filename = f"{variable_name}_" + str(variable) + ".json"
            path_file = os.path.join(path_folder, filename)
            with open(path_file, "w", encoding="utf-8") as file:
                json.dump(config_result, file, indent=4)

    # Plot
    if base_config["save_results"] is False:
        plot_single_variable(
            configs_results=configs_results,
            variable_name=variable_name,
            error_metric_names=error_metric_names,
            cost_metric_names=cost_metric_names,
        )


def plot_single_variable(
    configs_results: Union[list, str],
    variable_name: str,
    error_metric_names: list,
    cost_metric_names: list,
) -> None:
    """
    configs_results: list, or path to folder containing json files
    variable_name: name of the single variable
    error_metric_names: a list of names of error metrics
    cost_metric_names: a list of names of cost metrics
    """
    if isinstance(configs_results, str):
        # load configs_results from files
        lst = []
        for filename in os.scandir(configs_results):
            with open(filename.path, "r", encoding="utf-8") as file:
                config_result = json.load(file)
                lst.append(config_result)
        configs_results = lst

    lst_variable = []
    error_metrics_mean = {name: [] for name in error_metric_names}
    error_metrics_std = {name: [] for name in error_metric_names}
    cost_metrics_mean = {name: [] for name in cost_metric_names}
    cost_metrics_std = {name: [] for name in cost_metric_names}

    for config_result in configs_results:
        config = config_result["config"]  # dict
        lst_variable.append(config[variable_name])

        trials_results = config_result[
            "trials_results"
        ]  # list of trial_result
        for name in error_metric_names:
            lst_error = [rst[name] for rst in trials_results]
            error_metrics_mean[name].append(np.mean(lst_error))
            error_metrics_std[name].append(np.std(lst_error))
        for name in cost_metric_names:
            lst_cost = [rst[name] for rst in trials_results]
            cost_metrics_mean[name].append(np.mean(lst_cost))
            cost_metrics_std[name].append(np.std(lst_cost))

    idx_sort = np.argsort(lst_variable)
    lst_variable = np.array(lst_variable)[idx_sort]
    for name in error_metric_names:
        error_metrics_mean[name] = np.array(error_metrics_mean[name])[idx_sort]
        error_metrics_std[name] = np.array(error_metrics_std[name])[idx_sort]
    for name in cost_metric_names:
        cost_metrics_mean[name] = np.array(cost_metrics_mean[name])[idx_sort]
        cost_metrics_std[name] = np.array(cost_metrics_std[name])[idx_sort]

    num_error_metrics = len(error_metric_names)
    num_cost_metrics = len(cost_metric_names)
    num_metrics = num_error_metrics + num_cost_metrics
    num_subplot_cols = (num_metrics + 1) // 2

    plt.figure()
    for i, name in enumerate(error_metric_names):
        plt.subplot(2, num_subplot_cols, i + 1)
        plt.plot(lst_variable, error_metrics_mean[name], "-o", color="b")
        plt.fill_between(
            lst_variable,
            np.array(error_metrics_mean[name])
            - np.array(error_metrics_std[name]),
            np.array(error_metrics_mean[name])
            + np.array(error_metrics_std[name]),
            alpha=0.3,
            facecolor="b",
        )
        plt.grid(True)
        plt.title(name)
        plt.xlabel(variable_name)
    for i, name in enumerate(cost_metric_names):
        plt.subplot(2, num_subplot_cols, num_error_metrics + i + 1)
        plt.plot(lst_variable, cost_metrics_mean[name], "-o", color="r")
        plt.fill_between(
            lst_variable,
            np.array(cost_metrics_mean[name])
            - np.array(cost_metrics_std[name]),
            np.array(cost_metrics_mean[name])
            + np.array(cost_metrics_std[name]),
            alpha=0.3,
            facecolor="r",
        )
        plt.grid(True)
        plt.title(name)
        plt.xlabel(variable_name)
    # plt.tight_layout(pad=0.1)
    plt.subplots_adjust(
        left=0.1,
        top=0.9,
        right=0.9,
        bottom=0.1,
        hspace=0.5,
        wspace=0.5,
    )
    plt.show()


def parse_args() -> argparse.Namespace:
    """Parse arguments"""
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--save_results",
        type=str,
        default="False",
    )  # workaround for bool type
    parser.add_argument("--ntrials", type=int, default=2)
    parser.add_argument("--task", type=str)
    parser.add_argument("--llm_model", type=str)
    parser.add_argument("--variable_name", type=str)
    parser.add_argument("--lst_variable", type=int, nargs="+")
    parser.add_argument("--n_base", type=int, default=0)

    return parser.parse_args()


def main_exp(args: argparse.Namespace) -> None:
    """Main function for experiments"""
    # --- Parameters ---
    save_results = eval(args.save_results)  # bool
    ntrials = args.ntrials
    task = args.task
    llm_model = args.llm_model
    variable_name = args.variable_name
    lst_variable = args.lst_variable
    n_base = args.n_base

    task_config = {}
    sim_parallel_degree = 4  # degree of parallelism for "simulated" latency
    m_base = 0  # TODO: remove m_base completely?

    if task == "counting":
        error_metric_names = [
            "error_count",
            "error_count_normalized",
        ]

    elif task == "sorting":
        error_metric_names = [
            "error_EM",
            "error_L1_normalized",
            "error_L1_normalized_fuzzy",
            "error_Linf",
            "error_Linf_fuzzy",
            "error_list_length_normalized",
            "error_monotonicity_total",  # sum of non-monotonicity errors
            "error_monotonicity_final",  # non-monotonicity of final solution
        ]
        task_config = {
            # task
            "ndigits": 2,
            # algorithm
            "merge_by_llm": False,
            "merge_method": "hierarchical",  # "hierarchical" / "incremental"
        }

    elif task in ("retrieval", "retrieval_no_needle"):
        error_metric_names = [
            "error_EM",
        ]

    elif task == "rag":
        error_metric_names = [
            "error_EM",
            "error_wrong_digits_normalized",
        ]
        task_config = {
            "len_passcode": 6,
            "llm_to_dist": False,
        }

    cost_metric_names = [  # same for all tasks
        # Latency
        "latency",
        "latency_ideal_parallel",
        "latency_finite_parallel",
        # Non latency
        "llm_calls",
        "prefilling_tokens_total",
        "decoding_tokens_total",
        # "prefilling_length_total",
        # "decoding_length_total",
    ]

    # --- Base config dict ---
    base_config = {
        # task
        "task": task,
        "n": n_base,
        "n_max": n_base,  # used for generating random requests
        # with controlled randomness in vary-n experiment
        # algorithm
        "llm_model": llm_model,
        "m": m_base,
        "nsamples": 1,
        "parallel": False,
        "sim_parallel_degree": sim_parallel_degree,
        # experiment
        "ntrials": ntrials,
        "fix_seeds": True,  # fix seeds for generating request across configs
        "save_results": save_results,
        "activate": True,  # activate LLM before running experiment
    }
    base_config.update(task_config)

    # --- Activate/warmup LLM, for better evaluation of latency ---
    if base_config["activate"]:
        foobar_agent = DialogAgent(
            name="Assistant",
            sys_prompt="You're a helpful assistant.",
            model_config_name=base_config["llm_model"],
            use_memory=False,
        )
        x_request = Msg(
            name="user",
            content="Who are you? Please answer in one short sentence.\n",
            role="user",
        )
        _ = foobar_agent(x_request)
        print("========== LLM activated ==========\n\n")

    # --- Main experiment function ---
    exp_single_variable(
        base_config=base_config,
        variable_name=variable_name,
        lst_variable=lst_variable,
        error_metric_names=error_metric_names,
        cost_metric_names=cost_metric_names,
    )


if __name__ == "__main__":
    args_main = parse_args()
    agentscope.init(model_configs=MODEL_CONFIGS)
    main_exp(args_main)
