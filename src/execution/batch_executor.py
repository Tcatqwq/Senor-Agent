import copy
import inspect
import json
import os
import time
from queue import SimpleQueue

import yaml

from src.execution.process_pool import get_pool
from src.execution.toolbox import tool_name_tool_implementation_map
from src.utils import generate_a_sign, pack_result, print_tips


DRY_RUN_TOOLS = {"take-picture", "send-notification", "record-microphone"}


def load_config():
    config_path = os.environ["CONFIG_PATH"]
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.load(f, Loader=yaml.FullLoader)


def _result_key_in(results, tool_id):
    if tool_id in results:
        return tool_id
    if str(tool_id) in results:
        return str(tool_id)
    return None


def deps_satisfied(tool_call, results):
    deps = tool_call.get("dep") or [-1]
    for dep_id in deps:
        try:
            dep_id = int(dep_id)
        except (TypeError, ValueError):
            return False
        if dep_id < 0:
            continue
        if _result_key_in(results, dep_id) is None:
            return False
    return True


def ready_tool_calls(remaining_plan, results):
    ready = []
    for tool_call in remaining_plan:
        if deps_satisfied(tool_call, results):
            ready.append(tool_call)
    return ready


def _dry_run_enabled():
    return bool(load_config().get("graph_settings", {}).get("dry_run_sensor_tools", True))


def _dry_run_sensor_tool(tool_call, input_to_tool):
    tool_name = tool_call["tool_name"]
    print_tips(
        f"Dry-run {tool_name}: skip live sensor I/O.",
        emoji="zap",
        text_color="yellow",
        border=False,
    )
    return {
        "tool_name": tool_name,
        "output": {
            "pre_sign": input_to_tool.get("output", {}).get("post_sign", []),
            "post_sign": [generate_a_sign()],
            "label": ["success"],
            "status": "dry-run-success",
        },
        "saving": False,
        "execute_next": True,
    }


def _invoke_tool(tool_selected, input_to_tool, timing, gflops_of_tool):
    signature = inspect.signature(tool_selected)
    kwargs = {}
    if "args" in signature.parameters:
        kwargs["args"] = input_to_tool
    if "time_of_each_stage_of_tool_execution" in signature.parameters:
        kwargs["time_of_each_stage_of_tool_execution"] = timing
    if "gflops_of_tool" in signature.parameters:
        kwargs["gflops_of_tool"] = gflops_of_tool
    if kwargs:
        return tool_selected(**kwargs)
    return tool_selected(input_to_tool)


def prepare_and_run_tool(tool_call, results_collection):
    input_to_tool = {
        "tool_name": tool_call["tool_name"],
        "output": {
            "pre_sign": [],
            "post_sign": [],
        },
        "saving": True,
        "execute_next": True,
    }

    tool_call_id = tool_call["id"]
    tool_name = tool_call["tool_name"]
    dep = tool_call.get("dep") or [-1]
    args = dict(tool_call.get("args") or {})

    if dep[0] != -1:
        dep_results = []
        for dep_id in dep:
            key = _result_key_in(results_collection, dep_id)
            if key is None:
                raise KeyError(f"Missing dependency result for {dep_id}")
            dep_results.append(results_collection[key])
    else:
        dep_results = []

    execute_next = True
    for dep_result in dep_results:
        if not dep_result["tool_calling_result"].get("execute_next", True):
            execute_next = False
            break

    if execute_next:
        try:
            from src.planning.vocabulary_set_manager import get_all_modalities

            used_modalities = get_all_modalities()
            for modality in used_modalities:
                modality_result = None
                for dep_result in dep_results:
                    output = dep_result["tool_calling_result"]["output"]
                    if modality in output:
                        modality_result = output[modality]
                        input_to_tool["output"][modality] = modality_result
                        input_to_tool["output"]["post_sign"] += output.get("post_sign", [])
                if modality in args:
                    if "<GENERATED>" in str(args[modality]) and modality_result is not None:
                        args[modality] = modality_result
                    if len(input_to_tool["output"]["post_sign"]) == 0:
                        input_to_tool["output"][modality] = args[modality]
                        input_to_tool["output"]["post_sign"] = [
                            generate_a_sign() for _ in range(len(input_to_tool["output"][modality]))
                        ]
        except Exception as exc:
            print(f"Error occurred while preparing the input to the tool: {exc}")

    if dep[0] != -1:
        input_to_tool["execute_next"] = execute_next

    if "text" in args and "text" not in input_to_tool["output"]:
        input_to_tool["output"]["text"] = args["text"]
        if not input_to_tool["output"]["post_sign"]:
            values = args["text"] if isinstance(args["text"], list) else [args["text"]]
            input_to_tool["output"]["post_sign"] = [generate_a_sign() for _ in values]

    tool_call["args"] = args

    from src.llm_virtual_env.config import is_enabled as llm_virtual_enabled

    virtual = llm_virtual_enabled()
    if not virtual and tool_name not in tool_name_tool_implementation_map and tool_name not in DRY_RUN_TOOLS:
        raise ValueError(f"Tool name {tool_name} is not in the tool_name_tool_implementation_map.")

    timing = {
        "tool_name": tool_name,
        "total_time": None,
        "pre_processing_time": None,
        "model_loading_time": None,
        "model_inference_time": None,
    }
    gflops_of_tool = {"tool_name": tool_name, "gflops": None}

    tool_start_time = time.perf_counter()
    if virtual:
        from src.llm_virtual_env.simulator import simulate_tool

        execution_result = simulate_tool(tool_call, input_to_tool)
    elif tool_name in DRY_RUN_TOOLS and _dry_run_enabled():
        execution_result = _dry_run_sensor_tool(tool_call, input_to_tool)
    else:
        tool_selected = tool_name_tool_implementation_map[tool_name]
        execution_result = _invoke_tool(tool_selected, input_to_tool, timing, gflops_of_tool)
    timing["total_time"] = time.perf_counter() - tool_start_time

    if isinstance(execution_result, dict) and "error" in execution_result:
        raise ValueError(f"Error occurred while executing tool {tool_name}: {execution_result['error']}")

    if dep[0] == -1:
        results_collection[-1 * (tool_call_id + 1)] = pack_result(
            {"tool_name": "initial input", "id": -1 * (tool_call_id + 1), "dep": []},
            input_to_tool,
        )

    results_collection[tool_call_id] = pack_result(tool_call, execution_result)
    return tool_call_id


def _run_tool_job(tool_call, results_snapshot):
    local_results = dict(results_snapshot)
    before = set(local_results)
    tool_id = prepare_and_run_tool(tool_call, local_results)
    delta = {key: local_results[key] for key in local_results if key not in before}
    return tool_id, delta


def ensure_execution_cache():
    config = load_config()
    cache_path = config["execution_settings"]["execution_cache_path"]
    keys = [
        "object-detection-rgb",
        "object-detection-depth",
        "human-activity-classification-rgb",
        "human-activity-classification-depth",
    ]
    cache = {}
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cache = json.load(f) or {}
        except json.JSONDecodeError:
            cache = {}
    changed = False
    for key in keys:
        if key not in cache or not isinstance(cache.get(key), dict):
            cache[key] = {}
            changed = True
    if changed:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=4)


def execute_ready_batch(remaining_plan, results, report=None):
    from src.llm_virtual_env.config import is_enabled as llm_virtual_enabled

    virtual = llm_virtual_enabled()
    if not virtual:
        ensure_execution_cache()
    results_collection = dict(results or {})
    ready = ready_tool_calls(remaining_plan, results_collection)
    executed_ids = []

    if not ready:
        still_remaining = list(remaining_plan)
        return results_collection, still_remaining, executed_ids

    def start(tool_call):
        suffix = " (LLM virtual)" if virtual else ""
        print_tips(
            f"Executing tool {tool_call.get('id')}: {tool_call.get('tool_name')}{suffix}",
            text_color="yellow",
            emoji="hammer_and_wrench",
            border=False,
        )
        if report is not None:
            report("tool_started", copy.deepcopy(tool_call))

    if virtual or len(ready) == 1:
        for tool_call in ready:
            start(tool_call)
            try:
                tool_id = prepare_and_run_tool(tool_call, results_collection)
            except Exception as exc:
                if report is not None:
                    report("tool_failed", {"id": tool_call["id"], "error": str(exc)})
                raise
            executed_ids.append(tool_id)
            if report is not None:
                report("tool_finished", {"id": tool_id, "result": results_collection[tool_id]})
    else:
        names = ", ".join(
            f"{tool_call.get('id')}:{tool_call.get('tool_name')}" for tool_call in ready
        )
        print_tips(
            f"Executing {len(ready)} ready tools in parallel: {names}",
            text_color="yellow",
            emoji="hammer_and_wrench",
            border=False,
        )
        snapshot = dict(results_collection)
        pool = get_pool()
        completed = SimpleQueue()
        for tool_call in ready:
            start(tool_call)
            pool.apply_async(
                _run_tool_job,
                (copy.deepcopy(tool_call), snapshot),
                callback=lambda value: completed.put((value[0], value[1], None)),
                error_callback=lambda error, tool_id=tool_call["id"]: completed.put((tool_id, None, error)),
            )
        outcomes = {}
        for _ in ready:
            tool_id, delta, error = completed.get()
            outcomes[tool_id] = (delta, error)
            if report is not None:
                if error is None:
                    report("tool_finished", {"id": tool_id, "result": delta[tool_id]})
                else:
                    report("tool_failed", {"id": tool_id, "error": str(error)})

        # Keep the plan order in resolved results even when workers finish out of order.
        errors = []
        for tool_call in ready:
            tool_id = tool_call["id"]
            delta, error = outcomes[tool_id]
            if error is None:
                results_collection.update(delta)
                executed_ids.append(tool_id)
            else:
                errors.append(error)
        if errors:
            raise errors[0]

    still_remaining = [
        tool_call for tool_call in remaining_plan
        if tool_call.get("id") not in executed_ids
    ]
    return results_collection, still_remaining, executed_ids
