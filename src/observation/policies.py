import os
import re

import yaml

from src.execution.batch_executor import ready_tool_calls
from src.observation.schema import Observation


PERCEPTION_TOOLS = {
    "query-video-data-rgb",
    "query-video-data-depth",
    "object-detection-rgb",
    "object-detection-depth",
    "human-activity-classification-rgb",
    "human-activity-classification-depth",
}


def is_perception_tool(name: str) -> bool:
    tool = str(name or "")
    if tool in PERCEPTION_TOOLS:
        return True
    return (
        tool.startswith("query-video-data")
        or tool.startswith("query-audio")
        or "object-detection-" in tool
        or "human-activity-classification-" in tool
    )

LABEL_ALIASES = {
    "boxing": "Boxing",
    "push": "Pushing",
    "pushing": "Pushing",
    "kicking cabinet": "Kicking the cabinet",
    "kicking the cabinet": "Kicking the cabinet",
    "angrily hitting the cabinet": "Angrily hitting the cabinet",
    "falling down/getting up": "Falling down/getting up",
    "falling down": "Falling down/getting up",
    "throwing things": "Throwing things",
}


def _norm_label(label):
    return re.sub(r"\s+", " ", str(label).strip().lower().replace("_", " "))


def match_policy_labels(labels, act_on_labels):
    matched = []
    act_norm = {_norm_label(item): item for item in act_on_labels}
    for label in labels:
        normalized = _norm_label(label)
        canonical = LABEL_ALIASES.get(normalized, label)
        if normalized in act_norm:
            matched.append(act_norm[normalized])
            continue
        if canonical in act_on_labels:
            matched.append(canonical)
            continue
        for policy_norm, policy in act_norm.items():
            if policy_norm in normalized or normalized in policy_norm:
                matched.append(policy)
                break
    # preserve order, unique
    unique = []
    for item in matched:
        if item not in unique:
            unique.append(item)
    return unique


def load_graph_settings():
    config_path = os.environ.get("CONFIG_PATH")
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.load(f, Loader=yaml.FullLoader)
    return config.get("graph_settings", {})


def _result_output(packed):
    if not isinstance(packed, dict):
        return {}
    tool_result = packed.get("tool_calling_result") or packed
    return tool_result.get("output", {}) if isinstance(tool_result, dict) else {}


def _tool_name(packed):
    if not isinstance(packed, dict):
        return ""
    info = packed.get("tool_calling_information") or {}
    result = packed.get("tool_calling_result") or {}
    return info.get("tool_name") or result.get("tool_name") or result.get("task") or ""


def modality_from_tool_name(name: str):
    tool = str(name or "")
    try:
        from src.planning.vocabulary_set_manager import get_map_from_tool_name_to_modality

        mapped = get_map_from_tool_name_to_modality().get(tool)
        if mapped:
            return mapped
    except Exception:
        pass
    if "-" in tool:
        suffix = tool.rsplit("-", 1)[-1]
        if suffix:
            return suffix
    return None


def _flatten_har_labels(raw):
    labels = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, list):
                labels.extend(str(x) for x in item)
            elif item is not None:
                labels.append(str(item))
    elif raw:
        labels.append(str(raw))
    return labels


def extract_labels_by_modality(results):
    by_mod = {}
    for packed in (results or {}).values():
        name = _tool_name(packed)
        if "human-activity-classification" not in name:
            continue
        modality = modality_from_tool_name(name) or "unknown"
        output = _result_output(packed)
        by_mod.setdefault(modality, []).extend(_flatten_har_labels(output.get("label", [])))
    return by_mod


def extract_labels(results):
    labels = []
    for items in extract_labels_by_modality(results).values():
        labels.extend(items)
    return labels


def _is_media_key(key: str) -> bool:
    name = str(key or "")
    if name in {"text", "pre_sign", "post_sign", "label", "status"}:
        return False
    return name.endswith("_video") or name == "audio"


def _is_empty_media(output):
    if not isinstance(output, dict):
        return False
    media_keys = [key for key in output if _is_media_key(str(key))]
    if not media_keys:
        media_keys = ["rgb_video", "depth_video", "thermal_video", "audio"]
    for key in media_keys:
        value = output.get(key)
        if isinstance(value, list) and len(value) == 0:
            return True
    labels = output.get("label")
    has_media = any(output.get(key) for key in media_keys)
    if isinstance(labels, list) and len(labels) == 0 and not has_media:
        return True
    return False


def build_observation(state) -> Observation:
    results = state.get("results") or {}
    remaining = state.get("remaining_plan") or []
    last_ids = state.get("last_executed_ids") or []
    last_id_strs = {str(x) for x in last_ids}
    settings = load_graph_settings()
    act_on_labels = settings.get("observation_policies", {}).get("act_on_labels", [])

    labels_by_modality = extract_labels_by_modality(results)
    labels = []
    for items in labels_by_modality.values():
        labels.extend(items)
    policy_labels = match_policy_labels(labels, act_on_labels)

    empty_results = False
    execute_next_failed = False
    last_tool_names = []
    for tool_id, packed in results.items():
        name = _tool_name(packed)
        output = _result_output(packed)
        result = packed.get("tool_calling_result") or {}
        if name.startswith("query-video-data") and _is_empty_media(output):
            empty_results = True
        in_last_batch = (not last_id_strs) or (str(tool_id) in last_id_strs)
        if in_last_batch:
            last_tool_names.append(name)
            if result.get("execute_next") is False:
                execute_next_failed = True

    ready = ready_tool_calls(remaining, results)
    observation: Observation = {
        "empty_results": empty_results,
        "execute_next_failed": execute_next_failed,
        "labels": labels,
        "labels_by_modality": labels_by_modality,
        "remaining_count": len(remaining),
        "ready_count": len(ready),
        "last_tool_names": last_tool_names,
        "policy_hit": len(policy_labels) > 0,
        "policy_labels": policy_labels,
        "summary": (
            f"labels_by_modality={labels_by_modality}; empty={empty_results}; "
            f"remaining={len(remaining)}; ready={len(ready)}; policy={policy_labels}"
        ),
    }
    return observation


def has_monitor_intent(user_input: str) -> bool:
    settings = load_graph_settings()
    keywords = settings.get("monitor_keywords") or []
    text = (user_input or "").lower()
    for keyword in keywords:
        if str(keyword).lower() in text:
            return True
    return False


def remaining_perception(remaining_plan):
    return [
        tool for tool in remaining_plan
        if is_perception_tool(tool.get("tool_name"))
    ]


def choose_route(state, observation: Observation) -> str:
    settings = load_graph_settings()
    max_replan = int(settings.get("max_replan", 2))
    replan_count = int(state.get("replan_count") or 0)
    remaining = state.get("remaining_plan") or []
    inserted = set(state.get("inserted_act_ids") or [])
    user_input = state.get("user_input") or ""

    if observation.get("ready_count", 0) > 0:
        return "continue"

    if observation.get("empty_results") and remaining_perception(remaining) and replan_count < max_replan:
        return "replan"

    if observation.get("policy_hit") and "send-notification" not in inserted:
        require_monitor = settings.get("act_requires_monitor_intent", True)
        if (not require_monitor) or has_monitor_intent(user_input):
            return "act"

    if remaining and observation.get("ready_count", 0) == 0:
        if replan_count < max_replan and (observation.get("empty_results") or observation.get("execute_next_failed")):
            return "replan"
        if remaining_perception(remaining) and replan_count < max_replan:
            return "replan"

    if has_monitor_intent(user_input) and not remaining:
        return "wait"

    return "respond"
