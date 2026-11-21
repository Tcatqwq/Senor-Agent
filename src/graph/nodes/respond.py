import copy
from collections import Counter

from src.execution.batch_executor import DRY_RUN_TOOLS
from src.llm.chat import chat_completion
from src.observation.policies import is_perception_tool
from src.responding.respondent import generate_response
from src.utils import print_tips


def _tool_name(packed):
    if not isinstance(packed, dict):
        return ""
    info = packed.get("tool_calling_information") or {}
    result = packed.get("tool_calling_result") or {}
    return info.get("tool_name") or result.get("tool_name") or ""


def _as_int_key(key):
    try:
        return int(key)
    except (TypeError, ValueError):
        return key


def filter_results_for_formatter(results):
    '''Keep the perception DAG plus initial-input nodes that those tools actually depend on.'''
    if not results:
        return {}

    perception = {}
    needed_deps = set()
    for key, packed in results.items():
        name = _tool_name(packed)
        if name in DRY_RUN_TOOLS or name == "initial input":
            continue
        if not is_perception_tool(name):
            continue
        perception[key] = packed
        info = packed.get("tool_calling_information") or {}
        deps = info.get("dep") or []
        for dep in deps:
            needed_deps.add(_as_int_key(dep))
        # adjust_dep_ids rewrites root dep [-1] to [-(tool_id + 1)]
        int_key = _as_int_key(key)
        if isinstance(int_key, int) and int_key >= 0 and deps:
            first = _as_int_key(deps[0])
            if first == -1:
                needed_deps.add(-1 * (int_key + 1))

    filtered = dict(perception)
    for key, packed in results.items():
        if _tool_name(packed) != "initial input":
            continue
        if _as_int_key(key) in needed_deps:
            filtered[key] = packed
    return filtered


def _counts_summary(labels):
    counts = Counter(str(label) for label in (labels or []))
    if not counts:
        return "none"
    return "; ".join(f"{name} x{n}" for name, n in counts.most_common())


def _llm_answer_from_labels(user_input, observation):
    by_mod = observation.get("labels_by_modality") or {}
    if by_mod:
        summary = "\n".join(
            f"{modality}: {_counts_summary(labels)}"
            for modality, labels in by_mod.items()
        )
    else:
        labels = observation.get("labels") or []
        summary = (
            _counts_summary(labels)
            if labels
            else "No human-activity labels were produced."
        )
    messages = [
        {
            "role": "system",
            "content": (
                "You are Senor Agent. Answer the user's question using activity "
                "classification counts grouped by sensor modality (rgb, event, "
                "thermal, and any others). Discuss each modality separately; do "
                "not mix labels from different modalities into one undifferentiated "
                "conclusion. Be direct and concise. Do not dump the raw label list. "
                "If a phone call is asked about, look for labels such as "
                "'sitting and making a phone call' or 'playing with phone using both hands'."
            ),
        },
        {
            "role": "user",
            "content": (
                f"User question: {user_input}\n"
                f"Activity counts by modality:\n{summary}"
            ),
        },
    ]
    return chat_completion(messages)


def respond_node(state):
    print_tips("Response generation", text_color="green", emoji="speech_balloon")
    if state.get("final_response") in {"Empty Plan", "Grammar Checking Failed", "Command Not Solvable!"}:
        return {"route": "respond"}

    results = state.get("results") or {}
    unavailable = [
        f"{item['date']}-{item['hour']}-{item['hour']}"
        for item in state.get("modality_decisions", [])
        if all(attempt["status"] != "selected" for attempt in item["attempts"])
    ]
    user_input = state["user_input"]
    observation = state.get("observation") or {}
    inserted = state.get("inserted_act_ids") or []

    if not results:
        response = state.get("final_response") or (
            "请求时段内，RGB 和 Depth 均未取得可用的分析结果。" if unavailable else "Empty Plan"
        )
        return {
            "final_response": response,
            "route": "respond",
        }

    formatter_results = filter_results_for_formatter(results)
    try:
        response = generate_response(
            original_execution_results=copy.deepcopy(formatter_results or results),
            user_input=user_input,
            disabled_time_range=unavailable,
        )
    except Exception as exc:
        print_tips(
            f"Formatter failed ({exc}); answering from activity counts via LLM.",
            text_color="yellow",
            emoji="warning",
            border=False,
        )
        response = _llm_answer_from_labels(user_input, observation)

    if inserted:
        response = (
            f"[Proactive act] Triggered {', '.join(inserted)} "
            f"(dry-run) based on observed events "
            f"{observation.get('policy_labels')}.\n\n"
            + response
        )
    return {
        "final_response": response,
        "route": "respond",
    }
