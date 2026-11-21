"""Planning-side facade: real vs generated vocabulary / grammar / labels."""

from __future__ import annotations

import json
from pathlib import Path

import networkx as nx

from src.llm_virtual_env.config import (
    DEFAULT_HAR_LABELS,
    generated_dir,
    is_enabled,
    is_virtual_modality,
    load_virtual_env,
    real_tool_files_dir,
)


def use_generated_catalog() -> bool:
    return is_virtual_modality()


def generated_files_present() -> bool:
    path = generated_dir()
    required = [
        path / "vocabulary_set.json",
        path / "tool_dependencies.json",
        path / "tool_name_id_mapping.json",
        path / "grammar_rules.graphml",
    ]
    return all(item.exists() for item in required)


def generated_tool_files_dir() -> Path | None:
    if not use_generated_catalog():
        return None
    if not generated_files_present():
        return None
    return generated_dir()


def tool_files_dir() -> Path:
    generated = generated_tool_files_dir()
    if generated is not None:
        return generated
    return real_tool_files_dir()


def _load_json(path: Path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def generated_ready() -> bool:
    return generated_tool_files_dir() is not None


def load_vocabulary_set(path: Path | None = None) -> dict:
    target = (path or tool_files_dir()) / "vocabulary_set.json"
    return _load_json(target)


def load_tool_dependencies(path: Path | None = None) -> list:
    target = (path or tool_files_dir()) / "tool_dependencies.json"
    return _load_json(target)


def load_tool_name_id_mapping(path: Path | None = None) -> dict:
    target = (path or tool_files_dir()) / "tool_name_id_mapping.json"
    return _load_json(target)


def grammar_rules_path(path: Path | None = None) -> Path:
    return (path or tool_files_dir()) / "grammar_rules.graphml"


def load_grammar_rule_strings(path: Path | None = None) -> list[str]:
    mapping = load_tool_name_id_mapping(path)
    inverse = {int(value): key for key, value in mapping.items()}
    graph = nx.read_graphml(grammar_rules_path(path))
    rules = []
    for start_id, end_id in graph.edges:
        start_name = inverse[int(start_id)]
        end_name = inverse[int(end_id)]
        rules.append(f"{start_name} -> {end_name}")
    return rules


def activity_labels_text() -> str | None:
    if not use_generated_catalog():
        return None
    labels_path = generated_dir() / "activity_labels.txt"
    if labels_path.exists():
        return labels_path.read_text(encoding="utf-8")
    settings = load_virtual_env()
    labels = list(settings.get("har_labels") or DEFAULT_HAR_LABELS)
    return "Possible labels: " + json.dumps(labels, ensure_ascii=False)


def effective_modalities() -> list[str] | None:
    if not use_generated_catalog():
        return None
    return list(load_virtual_env().get("modalities") or [])


def effective_har_labels(modality: str | None = None, settings: dict | None = None) -> list[str]:
    settings = settings if settings is not None else load_virtual_env()
    by_mod = settings.get("har_labels_by_modality") or {}
    if modality and isinstance(by_mod, dict):
        key = str(modality).strip()
        candidates = [key]
        if key.endswith("_video"):
            candidates.append(key[: -len("_video")])
        else:
            candidates.append(key + "_video")
        for name in candidates:
            raw = by_mod.get(name)
            labels = [str(item).strip() for item in (raw or []) if str(item).strip()]
            if labels:
                return labels
    labels = [str(item).strip() for item in (settings.get("har_labels") or []) if str(item).strip()]
    if labels:
        return labels
    if use_generated_catalog():
        generated = generated_dir() / "activity_labels.txt"
        if generated.exists():
            text = generated.read_text(encoding="utf-8")
            parsed = _parse_possible_labels(text)
            if parsed:
                return parsed
    return list(DEFAULT_HAR_LABELS)


def _parse_possible_labels(text: str) -> list[str]:
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    if isinstance(payload, list):
        return [str(item) for item in payload]
    return []


def catalog_summary() -> dict:
    settings = load_virtual_env()
    summary = {
        "enabled": False,
        "virtual_modality": bool(settings.get("virtual_modality")),
        "generated_ready": False,
        "generated_dir": str(generated_dir()),
        "tool_files_dir": str(tool_files_dir()),
        "vocabulary": None,
        "dependencies": None,
        "grammar_rules": None,
        "har_labels": effective_har_labels(),
        "har_labels_by_modality": dict(settings.get("har_labels_by_modality") or {}),
        "modalities": list(settings.get("modalities") or []),
        "missing": [],
    }
    summary["enabled"] = is_enabled()
    dest = generated_dir()
    required = {
        "vocabulary_set.json": dest / "vocabulary_set.json",
        "tool_dependencies.json": dest / "tool_dependencies.json",
        "tool_name_id_mapping.json": dest / "tool_name_id_mapping.json",
        "grammar_rules.graphml": dest / "grammar_rules.graphml",
        "activity_labels.txt": dest / "activity_labels.txt",
    }
    summary["missing"] = [name for name, path in required.items() if not path.exists()]
    summary["generated_ready"] = generated_files_present()
    if generated_files_present():
        try:
            summary["vocabulary"] = load_vocabulary_set(dest)
            summary["dependencies"] = load_tool_dependencies(dest)
            summary["grammar_rules"] = load_grammar_rule_strings(dest)
        except Exception as exc:
            summary["error"] = str(exc)
    return summary
