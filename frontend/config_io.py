"""Load and save config.yaml for the settings dialog."""

from __future__ import annotations

import os
from copy import deepcopy
from typing import Any

from ruamel.yaml import YAML

PLACEHOLDERS = {None, "", "#", "EMPTY", "PUT_YOUR_API_KEY_HERE"}
SECRET_KEYS = {"api_key", "password"}
RESTART_PATHS = {
    "execution_settings.device",
    "graph_settings.max_parallel_tools",
}

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.indent(mapping=2, sequence=4, offset=2)


def config_path() -> str:
    path = os.environ.get("CONFIG_PATH")
    if not path:
        raise RuntimeError("CONFIG_PATH is not set")
    return path


def load_raw():
    with open(config_path(), "r", encoding="utf-8") as handle:
        return _yaml.load(handle)


def save_raw(data) -> None:
    with open(config_path(), "w", encoding="utf-8") as handle:
        _yaml.dump(data, handle)


def _is_placeholder(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip() in PLACEHOLDERS:
        return True
    return value in PLACEHOLDERS


def mask_secret(value: Any) -> str:
    if _is_placeholder(value):
        return ""
    text = str(value)
    if len(text) <= 8:
        return "********"
    return f"{text[:3]}••••{text[-4:]}"


def _walk_mask(node: Any) -> Any:
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if str(key) in SECRET_KEYS:
                out[key] = {
                    "masked": mask_secret(value),
                    "set": not _is_placeholder(value),
                }
            else:
                out[key] = _walk_mask(value)
        return out
    if isinstance(node, list):
        return [_walk_mask(item) for item in node]
    if hasattr(node, "items") and not isinstance(node, (str, bytes)):
        try:
            return _walk_mask(dict(node))
        except Exception:
            return node
    return node


def public_config() -> dict:
    raw = load_raw()
    api = raw.get("api_settings") or {}
    graph = raw.get("graph_settings") or {}
    execution = raw.get("execution_settings") or {}
    virtual = raw.get("llm_virtual_env") or {}
    virtual_api = virtual.get("api") or {}
    return {
        "api_settings": {
            "llm_name": api.get("llm_name"),
            "temperature": api.get("temperature"),
            "azure": _walk_mask(api.get("azure") or {}),
            "volcengine": _walk_mask(api.get("volcengine") or {}),
            "poe": _walk_mask(api.get("poe") or {}),
            "bedrock": _walk_mask(api.get("bedrock") or {}),
            "vllm": _walk_mask(api.get("vllm") or {}),
            "cohere": _walk_mask(api.get("cohere") or {}),
        },
        "dataset_name": raw.get("dataset_name"),
        "graph_settings": {
            "max_replan": graph.get("max_replan"),
            "max_validate_retries": graph.get("max_validate_retries"),
            "tick_interval_seconds": graph.get("tick_interval_seconds"),
            "max_parallel_tools": graph.get("max_parallel_tools"),
            "dry_run_sensor_tools": graph.get("dry_run_sensor_tools"),
            "act_requires_monitor_intent": graph.get("act_requires_monitor_intent"),
            "monitor_keywords": list(graph.get("monitor_keywords") or []),
            "observation_policies": deepcopy(dict(graph.get("observation_policies") or {})),
        },
        "execution_settings": {
            "use_cache": execution.get("use_cache"),
            "device": execution.get("device"),
            "use_dynamic_plan_adaptation": execution.get("use_dynamic_plan_adaptation"),
            "measure_flops": execution.get("measure_flops"),
        },
        "llm_virtual_env": {
            "enabled": bool(virtual.get("enabled", False)),
            "virtual_modality": bool(virtual.get("virtual_modality", False)),
            "api": _walk_mask(
                {
                    "base_url": virtual_api.get("base_url"),
                    "model": virtual_api.get("model"),
                    "temperature": virtual_api.get("temperature", 0),
                    "api_key": virtual_api.get("api_key"),
                }
            ),
        },
        "restart_hint": (
            "修改 device 或 max_parallel_tools 后需要重启 web 进程才能完全生效"
            "（toolbox 在 import 时缓存这些值）。use_cache 可立即生效。"
        ),
        "restart_paths": sorted(RESTART_PATHS),
    }


def public_virtual_env() -> dict:
    raw = load_raw()
    virtual = raw.get("llm_virtual_env") or {}
    date_range = dict(virtual.get("date_range") or {})
    return {
        "enabled": bool(virtual.get("enabled", False)),
        "virtual_modality": bool(virtual.get("virtual_modality", False)),
        "modalities": list(virtual.get("modalities") or []),
        "date_range": {
            "start": str(date_range.get("start") or ""),
            "end": str(date_range.get("end") or ""),
        },
        "har_labels": list(virtual.get("har_labels") or []),
        "har_labels_by_modality": dict(virtual.get("har_labels_by_modality") or {}),
        "dependencies": list(virtual.get("dependencies") or []),
        "environment_info": virtual.get("environment_info") or "",
        "events": list(virtual.get("events") or []),
        "use_cache": bool(virtual.get("use_cache", True)),
        "cache_path": virtual.get("cache_path"),
        "generated_dir": virtual.get("generated_dir"),
    }


def _set_path(doc, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cursor = doc
    for part in parts[:-1]:
        if cursor.get(part) is None:
            cursor[part] = {}
        cursor = cursor[part]
    cursor[parts[-1]] = value


def _get_path(doc, dotted: str, default=None):
    cursor = doc
    for part in dotted.split("."):
        if not isinstance(cursor, dict) or part not in cursor:
            return default
        cursor = cursor[part]
    return cursor


def _should_skip_secret(incoming: Any, current: Any) -> bool:
    if incoming is None:
        return True
    if isinstance(incoming, str) and incoming.strip() == "":
        return True
    if incoming == mask_secret(current):
        return True
    if isinstance(incoming, dict) and "masked" in incoming:
        return True
    return False


def _coerce(value: Any, current: Any):
    if isinstance(current, bool):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if isinstance(current, int) and not isinstance(current, bool):
        return int(value)
    if isinstance(current, float):
        return float(value)
    return value


def apply_updates(updates: dict) -> dict:
    """Merge allowlisted settings into config.yaml. Empty secrets are left unchanged."""
    raw = load_raw()
    changed = []
    restart_needed = []

    mapping = {
        "api_settings.llm_name": updates.get("api_settings", {}).get("llm_name"),
        "api_settings.temperature": updates.get("api_settings", {}).get("temperature"),
        "dataset_name": updates.get("dataset_name"),
        "graph_settings.max_replan": updates.get("graph_settings", {}).get("max_replan"),
        "graph_settings.max_validate_retries": updates.get("graph_settings", {}).get("max_validate_retries"),
        "graph_settings.tick_interval_seconds": updates.get("graph_settings", {}).get("tick_interval_seconds"),
        "graph_settings.max_parallel_tools": updates.get("graph_settings", {}).get("max_parallel_tools"),
        "graph_settings.dry_run_sensor_tools": updates.get("graph_settings", {}).get("dry_run_sensor_tools"),
        "graph_settings.act_requires_monitor_intent": updates.get("graph_settings", {}).get("act_requires_monitor_intent"),
        "execution_settings.use_cache": updates.get("execution_settings", {}).get("use_cache"),
        "execution_settings.device": updates.get("execution_settings", {}).get("device"),
        "execution_settings.use_dynamic_plan_adaptation": updates.get("execution_settings", {}).get(
            "use_dynamic_plan_adaptation"
        ),
        "execution_settings.measure_flops": updates.get("execution_settings", {}).get("measure_flops"),
        "llm_virtual_env.enabled": (updates.get("llm_virtual_env") or {}).get("enabled"),
    }

    secret_mapping = {
        "api_settings.azure.api_key": (updates.get("api_settings") or {}).get("azure", {}).get("api_key"),
        "api_settings.azure.api_version": (updates.get("api_settings") or {}).get("azure", {}).get("api_version"),
        "api_settings.azure.azure_endpoint": (updates.get("api_settings") or {}).get("azure", {}).get("azure_endpoint"),
        "api_settings.volcengine.api_key": (updates.get("api_settings") or {}).get("volcengine", {}).get("api_key"),
        "api_settings.volcengine.model": (updates.get("api_settings") or {}).get("volcengine", {}).get("model"),
        "api_settings.volcengine.temperature": (updates.get("api_settings") or {}).get("volcengine", {}).get(
            "temperature"
        ),
        "api_settings.poe.api_key": (updates.get("api_settings") or {}).get("poe", {}).get("api_key"),
        "api_settings.bedrock.api_key": (updates.get("api_settings") or {}).get("bedrock", {}).get("api_key"),
        "api_settings.bedrock.region_name": (updates.get("api_settings") or {}).get("bedrock", {}).get("region_name"),
        "api_settings.vllm.api_key": (updates.get("api_settings") or {}).get("vllm", {}).get("api_key"),
        "api_settings.vllm.base_url": (updates.get("api_settings") or {}).get("vllm", {}).get("base_url"),
        "api_settings.vllm.model": (updates.get("api_settings") or {}).get("vllm", {}).get("model"),
        "api_settings.vllm.temperature": (updates.get("api_settings") or {}).get("vllm", {}).get("temperature"),
        "api_settings.cohere.api_key": (updates.get("api_settings") or {}).get("cohere", {}).get("api_key"),
        "llm_virtual_env.api.api_key": (updates.get("llm_virtual_env") or {}).get("api", {}).get("api_key"),
        "llm_virtual_env.api.base_url": (updates.get("llm_virtual_env") or {}).get("api", {}).get("base_url"),
        "llm_virtual_env.api.model": (updates.get("llm_virtual_env") or {}).get("api", {}).get("model"),
        "llm_virtual_env.api.temperature": (updates.get("llm_virtual_env") or {}).get("api", {}).get("temperature"),
    }

    for dotted, value in mapping.items():
        if value is None:
            continue
        current = _get_path(raw, dotted)
        coerced = _coerce(value, current)
        if coerced != current:
            _set_path(raw, dotted, coerced)
            changed.append(dotted)
            if dotted in RESTART_PATHS:
                restart_needed.append(dotted)

    for dotted, value in secret_mapping.items():
        current = _get_path(raw, dotted)
        leaf = dotted.rsplit(".", 1)[-1]
        if leaf in SECRET_KEYS and _should_skip_secret(value, current):
            continue
        if value is None:
            continue
        if isinstance(value, str) and value.strip() == "" and leaf not in SECRET_KEYS:
            continue
        if dotted.endswith(".temperature") or dotted.endswith("temperature"):
            coerced = float(value)
        else:
            coerced = _coerce(value, current) if current is not None else value
        if coerced != current:
            _set_path(raw, dotted, coerced)
            changed.append(dotted)

    graph_updates = updates.get("graph_settings") or {}
    if "monitor_keywords" in graph_updates and graph_updates["monitor_keywords"] is not None:
        keywords = graph_updates["monitor_keywords"]
        if isinstance(keywords, str):
            keywords = [line.strip() for line in keywords.splitlines() if line.strip()]
        _set_path(raw, "graph_settings.monitor_keywords", list(keywords))
        changed.append("graph_settings.monitor_keywords")

    policies = graph_updates.get("observation_policies")
    if isinstance(policies, dict):
        current_policies = dict(_get_path(raw, "graph_settings.observation_policies") or {})
        if "act_on_labels" in policies and policies["act_on_labels"] is not None:
            labels = policies["act_on_labels"]
            if isinstance(labels, str):
                labels = [line.strip() for line in labels.splitlines() if line.strip()]
            current_policies["act_on_labels"] = list(labels)
            changed.append("graph_settings.observation_policies.act_on_labels")
        if "act_tools" in policies and policies["act_tools"] is not None:
            tools = policies["act_tools"]
            if isinstance(tools, str):
                tools = [line.strip() for line in tools.splitlines() if line.strip()]
            current_policies["act_tools"] = list(tools)
            changed.append("graph_settings.observation_policies.act_tools")
        _set_path(raw, "graph_settings.observation_policies", current_policies)

    save_raw(raw)
    return {
        "ok": True,
        "changed": changed,
        "restart_needed": restart_needed,
        "config": public_config(),
    }


def _lines_or_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip()]
    return [value]


def parse_dependencies(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("["):
            import json

            try:
                value = json.loads(stripped)
            except json.JSONDecodeError:
                value = _lines_or_list(stripped)
        else:
            value = _lines_or_list(stripped)
    if not isinstance(value, list):
        return []
    edges = []
    for item in value:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            edges.append([str(item[0]).strip(), str(item[1]).strip()])
            continue
        if isinstance(item, str) and "->" in item:
            start, end = item.split("->", 1)
            edges.append([start.strip(), end.strip()])
    return edges


def parse_har_labels_by_modality(value: Any) -> dict:
    if value is None:
        return {}
    if isinstance(value, str):
        import json

        stripped = value.strip()
        if not stripped:
            return {}
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            return {}
    if not isinstance(value, dict):
        return {}
    out = {}
    for key, labels in value.items():
        name = str(key).strip()
        items = _lines_or_list(labels)
        if name:
            out[name] = items
    return out


def parse_events(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        import json

        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            events = []
            for line in stripped.splitlines():
                line = line.strip()
                if not line:
                    continue
                events.append({"note": line, "har_label": "", "start": "", "end": ""})
            return events
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        if not isinstance(item, dict):
            continue
        event = {
            "start": item.get("start") or "",
            "end": item.get("end") or "",
            "date": item.get("date") or "",
            "start_hour": item.get("start_hour"),
            "end_hour": item.get("end_hour"),
            "modality": item.get("modality") or "",
            "har_label": item.get("har_label") or item.get("label") or "",
            "note": item.get("note") or "",
        }
        if item.get("modalities"):
            event["modalities"] = item.get("modalities")
        out.append(event)
    return out


def apply_virtual_world_updates(updates: dict) -> dict:
    raw = load_raw()
    if raw.get("llm_virtual_env") is None:
        raw["llm_virtual_env"] = {}
    changed = []

    if "virtual_modality" in updates and updates["virtual_modality"] is not None:
        coerced = _coerce(updates["virtual_modality"], bool(_get_path(raw, "llm_virtual_env.virtual_modality", False)))
        _set_path(raw, "llm_virtual_env.virtual_modality", coerced)
        changed.append("llm_virtual_env.virtual_modality")
    if "modalities" in updates and updates["modalities"] is not None:
        _set_path(raw, "llm_virtual_env.modalities", _lines_or_list(updates["modalities"]))
        changed.append("llm_virtual_env.modalities")
    if "har_labels" in updates and updates["har_labels"] is not None:
        _set_path(raw, "llm_virtual_env.har_labels", _lines_or_list(updates["har_labels"]))
        changed.append("llm_virtual_env.har_labels")
    if "har_labels_by_modality" in updates and updates["har_labels_by_modality"] is not None:
        _set_path(raw, "llm_virtual_env.har_labels_by_modality", parse_har_labels_by_modality(updates["har_labels_by_modality"]))
        changed.append("llm_virtual_env.har_labels_by_modality")
    if "use_cache" in updates and updates["use_cache"] is not None:
        _set_path(
            raw,
            "llm_virtual_env.use_cache",
            _coerce(updates["use_cache"], bool(_get_path(raw, "llm_virtual_env.use_cache", True))),
        )
        changed.append("llm_virtual_env.use_cache")
    if "dependencies" in updates and updates["dependencies"] is not None:
        _set_path(raw, "llm_virtual_env.dependencies", parse_dependencies(updates["dependencies"]))
        changed.append("llm_virtual_env.dependencies")
    if "environment_info" in updates and updates["environment_info"] is not None:
        _set_path(raw, "llm_virtual_env.environment_info", str(updates["environment_info"]))
        changed.append("llm_virtual_env.environment_info")
    if "events" in updates and updates["events"] is not None:
        _set_path(raw, "llm_virtual_env.events", parse_events(updates["events"]))
        changed.append("llm_virtual_env.events")
    if "date_range" in updates and updates["date_range"] is not None:
        span = updates["date_range"]
        if isinstance(span, dict):
            current = dict(_get_path(raw, "llm_virtual_env.date_range") or {})
            if span.get("start") is not None:
                current["start"] = str(span.get("start") or "")
            if span.get("end") is not None:
                current["end"] = str(span.get("end") or "")
            _set_path(raw, "llm_virtual_env.date_range", current)
            changed.append("llm_virtual_env.date_range")

    save_raw(raw)
    virtual_on = bool(_get_path(raw, "llm_virtual_env.virtual_modality", False))
    needs_rebuild = False
    if virtual_on:
        from src.llm_virtual_env.generator import catalog_needs_rebuild

        needs_rebuild = catalog_needs_rebuild()
    return {
        "ok": True,
        "changed": changed,
        "needs_rebuild": needs_rebuild,
        "config": public_virtual_env(),
    }
