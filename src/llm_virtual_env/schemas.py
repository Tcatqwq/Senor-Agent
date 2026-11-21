"""Format checks for generated vocabulary, dependencies, and tool envelopes."""

from __future__ import annotations

import json
import re
from typing import Any

from src.llm_virtual_env.config import INITIAL_INPUT

VOCAB_CATEGORIES = ("model_tool", "db_tool", "sensor_tool")
REQUIRED_TOOL_KEYS = ("tool_name",)
FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


class FormatError(ValueError):
    pass


def extract_json(text: str) -> Any:
    if not text or not str(text).strip():
        raise FormatError("LLM returned empty content")
    stripped = str(text).strip()
    fenced = FENCE_RE.findall(stripped)
    candidates = list(fenced) + [stripped]
    errors = []
    for candidate in candidates:
        blob = candidate.strip()
        start_obj = blob.find("{")
        start_arr = blob.find("[")
        starts = [index for index in (start_obj, start_arr) if index >= 0]
        if not starts:
            continue
        start = min(starts)
        blob = blob[start:]
        try:
            return json.loads(blob)
        except json.JSONDecodeError as exc:
            errors.append(str(exc))
            decoder = json.JSONDecoder()
            try:
                value, _ = decoder.raw_decode(blob)
                return value
            except json.JSONDecodeError as inner:
                errors.append(str(inner))
    raise FormatError("LLM output is not valid JSON: " + "; ".join(errors[:3]))


def _as_str_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise FormatError(f"{field} must be a list")
    out = []
    for item in value:
        text = str(item).strip()
        if text:
            out.append(text)
    return out


def validate_vocabulary_set(vocab: Any) -> dict:
    if not isinstance(vocab, dict):
        raise FormatError("vocabulary_set must be a JSON object")
    normalized = {}
    tool_names = []
    for category in VOCAB_CATEGORIES:
        entries = vocab.get(category, [])
        if entries is None:
            entries = []
        if not isinstance(entries, list):
            raise FormatError(f"vocabulary_set.{category} must be a list")
        tools = []
        for index, tool in enumerate(entries):
            if not isinstance(tool, dict):
                raise FormatError(f"vocabulary_set.{category}[{index}] must be an object")
            name = str(tool.get("tool_name") or "").strip()
            if not name:
                raise FormatError(f"vocabulary_set.{category}[{index}] is missing tool_name")
            if name in tool_names:
                raise FormatError(f"duplicate tool_name: {name}")
            tool_names.append(name)
            item = {
                "tool_name": name,
                "description": str(
                    tool.get("description") or tool.get("tool_description") or ""
                ),
            }
            if isinstance(tool.get("input"), dict):
                item["input"] = tool["input"]
            if isinstance(tool.get("output"), dict):
                item["output"] = tool["output"]
            modality = tool.get("modality")
            if modality:
                item["modality"] = str(modality)
            tools.append(item)
        normalized[category] = tools
    extra = [key for key in vocab.keys() if key not in VOCAB_CATEGORIES]
    if extra:
        raise FormatError(
            "vocabulary_set has unexpected categories: " + ", ".join(extra)
        )
    if not tool_names:
        raise FormatError("vocabulary_set does not contain any tools")
    return normalized


def validate_dependencies(dependencies: Any, tool_names: set[str]) -> list[list[str]]:
    if dependencies is None:
        dependencies = []
    if not isinstance(dependencies, list):
        raise FormatError("tool_dependencies must be a list of [from, to] pairs")
    allowed = set(tool_names) | {INITIAL_INPUT}
    edges = []
    seen = set()
    for index, edge in enumerate(dependencies):
        if not isinstance(edge, (list, tuple)) or len(edge) != 2:
            raise FormatError(f"tool_dependencies[{index}] must be a 2-element list")
        start = str(edge[0]).strip()
        end = str(edge[1]).strip()
        if not start or not end:
            raise FormatError(f"tool_dependencies[{index}] has empty tool names")
        if start not in allowed:
            raise FormatError(f"dependency start tool is unknown: {start}")
        if end not in allowed:
            raise FormatError(f"dependency end tool is unknown: {end}")
        if end == INITIAL_INPUT:
            raise FormatError("initial-input cannot be a dependency target")
        key = (start, end)
        if key in seen:
            continue
        seen.add(key)
        edges.append([start, end])
    if not edges:
        raise FormatError("tool_dependencies is empty")
    return edges


def tool_names_from_vocab(vocab: dict) -> list[str]:
    names = []
    for category in VOCAB_CATEGORIES:
        for tool in vocab.get(category) or []:
            names.append(tool["tool_name"])
    return names


def validate_generation_payload(payload: Any) -> dict:
    if not isinstance(payload, dict):
        raise FormatError("generation payload must be a JSON object")
    vocab = validate_vocabulary_set(payload.get("vocabulary_set"))
    names = tool_names_from_vocab(vocab)
    deps = validate_dependencies(payload.get("tool_dependencies"), set(names))
    har_labels = _as_str_list(payload.get("har_labels"), "har_labels")
    return {
        "vocabulary_set": vocab,
        "tool_dependencies": deps,
        "har_labels": har_labels,
    }


def coerce_envelope(tool_name: str, result: Any) -> dict:
    if not isinstance(result, dict):
        raise FormatError("tool result must be a JSON object")
    output = result.get("output")
    if output is None:
        output = {key: value for key, value in result.items() if key not in {
            "tool_name", "saving", "execute_next", "task"
        }}
    if not isinstance(output, dict):
        raise FormatError("tool result.output must be an object")
    envelope = {
        "tool_name": str(result.get("tool_name") or tool_name),
        "output": dict(output),
        "saving": bool(result.get("saving", False)),
        "execute_next": bool(result.get("execute_next", True)),
    }
    return envelope
