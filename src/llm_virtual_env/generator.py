"""Generate vocabulary / dependencies / grammar for virtual-modality mode."""

from __future__ import annotations

import json
from pathlib import Path

import networkx as nx

from src.llm_virtual_env.chat import virtual_chat_completion
from src.llm_virtual_env.config import (
    DEFAULT_HAR_LABELS,
    INITIAL_INPUT,
    generated_dir,
    load_virtual_env,
    snapshot_world,
)
from src.llm_virtual_env.schemas import (
    FormatError,
    extract_json,
    tool_names_from_vocab,
    validate_generation_payload,
)
from src.utils import print_tips

GENERATION_SYSTEM = """You generate Senor Agent planning files for a simulated sensor environment.
Return ONE JSON object only, no markdown, with keys:
- vocabulary_set: object with exactly these lists: model_tool, db_tool, sensor_tool
- tool_dependencies: list of [from_tool, to_tool] pairs
- har_labels: list of human-activity label strings used in HAR tools

Each tool object MUST have "tool_name" (kebab-case) and "description".
Optional keys: "input" (object), "output" (object), "modality".
Modality values should be the requested media keys (rgb_video, depth_video, thermal_video, audio, text, or other <name>_video).

vocabulary_set format example:
{
  "model_tool": [{"tool_name": "object-detection-rgb", "description": "...", "input": {"rgb_video": "..."}, "output": {"label": "Possible labels: [...]", "rgb_video": "..."}, "modality": "rgb_video"}],
  "db_tool": [{"tool_name": "query-video-data-rgb", "description": "...", "input": {"text": "time range [[YYYYMMDD, start_hour, end_hour]]"}, "output": {"rgb_video": "..."}, "modality": "rgb_video"}],
  "sensor_tool": [{"tool_name": "send-notification", "description": "...", "input": {"text": "..."}, "output": {"status": "..."}}]
}

tool_dependencies must start from "initial-input (tool calling id < 0)" for database/sensor query tools.
Typical RGB chain: initial-input -> query-video-data-rgb -> object-detection-rgb -> human-activity-classification-rgb.
Do the same for other requested modalities.
Every dependency endpoint must be a tool_name in vocabulary_set (or the initial-input sentinel).
HAR tool output.label must list the provided har_labels (or your proposed labels if none were provided).
If har_labels_by_modality is provided, each HAR tool must use that modality's labels.
Only include tools for the requested modalities (plus sensor_tool helpers).
"""


FORMAT_SKELETON = {
    "model_tool": [
        {
            "tool_name": "object-detection-rgb",
            "description": "Detect objects and crop them into separate videos.",
            "input": {"rgb_video": "input videos"},
            "output": {
                "label": "Possible labels: ['person']",
                "rgb_video": "cropped videos",
            },
            "modality": "rgb_video",
        },
        {
            "tool_name": "human-activity-classification-rgb",
            "description": "Recognize human activity in each person crop.",
            "input": {"rgb_video": "person crops"},
            "output": {"label": "Possible labels: ['Standing still']"},
            "modality": "rgb_video",
        },
    ],
    "db_tool": [
        {
            "tool_name": "query-video-data-rgb",
            "description": "Fetch videos by time range [[YYYYMMDD, start_hour, end_hour]].",
            "input": {"text": "time range"},
            "output": {"rgb_video": "retrieved videos"},
            "modality": "rgb_video",
        }
    ],
    "sensor_tool": [
        {
            "tool_name": "send-notification",
            "description": "Send a notification.",
            "input": {"text": "message"},
            "output": {"status": "result"},
        },
        {
            "tool_name": "take-picture",
            "description": "Capture pictures.",
            "input": {},
            "output": {"status": "result"},
        },
    ],
}


def _har_labels(settings: dict) -> list[str]:
    labels = [str(item).strip() for item in (settings.get("har_labels") or []) if str(item).strip()]
    return labels or list(DEFAULT_HAR_LABELS)


def _har_labels_by_modality(settings: dict) -> dict:
    raw = settings.get("har_labels_by_modality") or {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key, value in raw.items():
        name = str(key).strip()
        labels = [str(item).strip() for item in (value or []) if str(item).strip()]
        if name and labels:
            out[name] = labels
    return out


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def write_mapping_and_grammar(vocab: dict, dependencies: list, dest: Path) -> dict:
    mapping = {INITIAL_INPUT: -1}
    tool_id = 0
    for category in ("model_tool", "db_tool", "sensor_tool"):
        for tool in vocab.get(category) or []:
            mapping[tool["tool_name"]] = tool_id
            tool_id += 1
    graph = nx.DiGraph()
    for start_name, end_name in dependencies:
        if start_name not in mapping:
            raise FormatError(f"dependency start is not in vocabulary: {start_name}")
        if end_name not in mapping:
            raise FormatError(f"dependency end is not in vocabulary: {end_name}")
        graph.add_edge(mapping[start_name], mapping[end_name])
    dest.mkdir(parents=True, exist_ok=True)
    _write_json(dest / "tool_name_id_mapping.json", mapping)
    nx.write_graphml(graph, dest / "grammar_rules.graphml")
    return mapping


def _write_activity_labels(labels: list[str], dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    dest.joinpath("activity_labels.txt").write_text(
        "Possible labels: " + json.dumps(labels, ensure_ascii=False),
        encoding="utf-8",
    )


def _commit_generation(payload: dict, settings: dict, dest: Path) -> dict:
    vocab = payload["vocabulary_set"]
    deps = payload["tool_dependencies"]
    labels = payload.get("har_labels") or _har_labels(settings)
    dest.mkdir(parents=True, exist_ok=True)
    tmp = dest / ".tmp"
    if tmp.exists():
        for child in tmp.iterdir():
            if child.is_file():
                child.unlink()
    tmp.mkdir(parents=True, exist_ok=True)
    _write_json(tmp / "vocabulary_set.json", vocab)
    _write_json(tmp / "tool_dependencies.json", deps)
    write_mapping_and_grammar(vocab, deps, tmp)
    _write_activity_labels(labels, tmp)
    _write_json(tmp / "meta.json", {"world": snapshot_world(settings), "har_labels": labels})
    for child in tmp.iterdir():
        target = dest / child.name
        if target.exists() and target.is_file():
            target.unlink()
        child.replace(target)
    try:
        tmp.rmdir()
    except OSError:
        pass
    from src.llm_virtual_env.cache import clear_cache

    clear_cache()
    return {
        "ok": True,
        "tool_count": len(tool_names_from_vocab(vocab)),
        "dependency_count": len(deps),
        "har_labels": labels,
        "generated_dir": str(dest),
        "cache_cleared": True,
    }


def _build_user_prompt(settings: dict) -> str:
    return json.dumps(
        {
            "modalities": settings.get("modalities") or [],
            "har_labels": _har_labels(settings),
            "har_labels_by_modality": _har_labels_by_modality(settings),
            "dependencies_hint": settings.get("dependencies") or [],
            "format_skeleton": FORMAT_SKELETON,
            "initial_input": INITIAL_INPUT,
            "instructions": [
                "Copy format_skeleton structure. Do NOT copy long COCO/HAR label dumps.",
                "Emit query-video-data-<mod>, object-detection-<mod>, human-activity-classification-<mod> for each requested *video modality (rgb_video -> rgb, depth_video -> depth, thermal_video -> thermal, etc.).",
                "Use modality field equal to the requested media key (e.g. thermal_video).",
                "HAR output.label must list the provided har_labels (short list, not the skeleton example).",
                "If har_labels_by_modality is set, each HAR tool must use that modality's label list in output.label.",
                "If dependencies_hint is empty, emit initial_input -> query -> object-detection -> HAR per video modality.",
                "If dependencies_hint is non-empty, those edges MUST appear.",
                "Keep send-notification and take-picture in sensor_tool.",
                "vocabulary_set may only contain keys model_tool, db_tool, sensor_tool.",
                "Return compact JSON only.",
            ],
        },
        ensure_ascii=False,
        indent=2,
    )


def rebuild(settings: dict | None = None, retries: int = 1) -> dict:
    settings = settings if settings is not None else load_virtual_env()
    dest = generated_dir()
    last_error = None
    attempts = max(1, retries + 1)
    for attempt in range(attempts):
        try:
            raw = virtual_chat_completion(
                [
                    {"role": "system", "content": GENERATION_SYSTEM},
                    {"role": "user", "content": _build_user_prompt(settings)},
                ]
            )
            parsed = extract_json(raw)
            payload = validate_generation_payload(parsed)
            requested = [
                str(item).strip()
                for item in (settings.get("har_labels") or [])
                if str(item).strip()
            ]
            if requested:
                payload["har_labels"] = requested
            elif not payload["har_labels"]:
                payload["har_labels"] = list(DEFAULT_HAR_LABELS)
            hint = settings.get("dependencies") or []
            if hint:
                names = set(tool_names_from_vocab(payload["vocabulary_set"]))
                extra = []
                for edge in hint:
                    if not isinstance(edge, (list, tuple)) or len(edge) != 2:
                        continue
                    start, end = str(edge[0]).strip(), str(edge[1]).strip()
                    if start not in names and start != INITIAL_INPUT:
                        raise FormatError(f"hint dependency uses unknown tool: {start}")
                    if end not in names:
                        raise FormatError(f"hint dependency uses unknown tool: {end}")
                    extra.append([start, end])
                merged = payload["tool_dependencies"]
                seen = {tuple(item) for item in merged}
                for edge in extra:
                    if tuple(edge) not in seen:
                        merged.append(edge)
                        seen.add(tuple(edge))
                payload["tool_dependencies"] = merged
            return _commit_generation(payload, settings, dest)
        except Exception as exc:
            last_error = exc
            print_tips(
                f"Virtual catalog generate attempt {attempt + 1}/{attempts} failed: {exc}",
                text_color="yellow",
                emoji="warning",
                border=False,
            )
    raise FormatError(f"failed to generate virtual catalog: {last_error}")


def catalog_needs_rebuild(settings: dict | None = None) -> bool:
    settings = settings if settings is not None else load_virtual_env()
    if not settings.get("virtual_modality"):
        return False
    dest = generated_dir()
    if not (dest / "vocabulary_set.json").exists():
        return True
    meta_path = dest / "meta.json"
    try:
        previous = json.loads(meta_path.read_text(encoding="utf-8")).get("world")
        return previous != snapshot_world(settings)
    except Exception:
        return True


def maybe_rebuild(settings: dict | None = None, force: bool = False) -> dict | None:
    settings = settings if settings is not None else load_virtual_env()
    if not settings.get("virtual_modality"):
        return None
    if not force and not catalog_needs_rebuild(settings):
        return {"ok": True, "skipped": True, "reason": "unchanged"}
    return rebuild(settings)
