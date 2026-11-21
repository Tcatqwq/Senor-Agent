from langgraph.config import get_stream_writer

from src.execution.batch_executor import execute_ready_batch, load_config
from src.execution.modality_adaptation import execute_adaptive_plan
from src.llm_virtual_env.config import is_enabled as virtual_enabled
from src.utils import print_tips


def execute_node(state):
    remaining = list(state.get("remaining_plan") or [])
    results = dict(state.get("results") or {})
    print_tips("Execute ready batch", text_color="yellow", emoji="hammer_and_wrench")

    if not remaining:
        return {
            "last_executed_ids": [],
            "route": "observe",
        }

    virtual = virtual_enabled()
    try:
        writer = get_stream_writer()
    except RuntimeError:
        report = None
    else:
        def report(phase, data):
            writer({"modality_progress": phase, "data": data, "virtual": virtual})

    settings = load_config()["execution_settings"]
    if not results and settings.get("use_dynamic_plan_adaptation") and not virtual:
        adapted = execute_adaptive_plan(remaining, settings, report=report)
        if adapted is not None:
            results, decisions = adapted
            return {
                "results": results,
                "remaining_plan": [],
                "last_executed_ids": [key for key in results if key >= 0],
                "modality_decisions": decisions,
                "route": "observe",
            }

    results, still_remaining, executed_ids = execute_ready_batch(remaining, results, report=report)
    print_tips(
        f"Executed ids={executed_ids}; remaining={len(still_remaining)}",
        text_color="yellow",
        emoji="hammer_and_wrench",
        border=False,
    )
    return {
        "results": results,
        "remaining_plan": still_remaining,
        "last_executed_ids": executed_ids,
        "route": "observe",
    }
