"""Index sensor data, caches, intermediate outputs, tools, and models."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

from frontend.config_io import load_raw

DATE_DIR_RE = re.compile(r"^\d{8}$")
HOUR_FILE_RE = re.compile(r"^(\d{2})\.(avi|mp4|mkv|mov|wav|mp3|m4a)$", re.I)
INTERMEDIATE_RE = re.compile(r"^(\d{8})_(\d{2})_")
MEDIA_EXT = {".avi", ".mp4", ".mkv", ".mov", ".wav", ".mp3", ".m4a", ".jpg", ".jpeg", ".png"}

MODALITY_ALIASES = {
    "rgb": "rgb_video",
    "rgb_video": "rgb_video",
    "dep": "depth_video",
    "depth": "depth_video",
    "depth_video": "depth_video",
    "audio": "audio",
    "text": "text",
}

TOOL_TO_CHECKPOINT = {
    "object-detection-depth": "object_detection_depth",
    "human-activity-classification-rgb": "human_activity_classification_rgb",
    "human-activity-classification-depth": "human_activity_classification_depth",
    "object-detection-rgb": None,
}

CHECKPOINT_TO_TOOL = {value: key for key, value in TOOL_TO_CHECKPOINT.items() if value}


def root_dir() -> Path:
    return Path(os.environ["CONFIG_PATH"]).resolve().parent.parent


def _resolve(path: str | Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root_dir() / candidate
    return candidate.resolve()


def resolve_path(path: str | Path) -> Path:
    return _resolve(path)


def _load_config():
    return load_raw()


def dataset_name(config=None) -> str:
    config = config or _load_config()
    return str(config.get("dataset_name") or "inlab")


def allowed_media_roots(config=None) -> list[Path]:
    config = config or _load_config()
    name = dataset_name(config)
    roots: list[Path] = []
    sensor = ((config.get("execution_settings") or {}).get("sensor_database_path") or {}).get(name) or {}
    for value in sensor.values():
        if value:
            roots.append(_resolve(value))
    intermediate = ((config.get("execution_settings") or {}).get("intermediate_output_path") or {}).get(name)
    if intermediate:
        roots.append(_resolve(intermediate))
    for key in ("execution_cache_path", "embedding_cache_path", "assessment_cache_path", "flops_cache_path"):
        cache_path = (config.get("execution_settings") or {}).get(key)
        if cache_path:
            roots.append(_resolve(cache_path).parent)
    checkpoints = ((config.get("execution_settings") or {}).get("model_checkpoint_paths") or {}).get(name) or {}
    for value in checkpoints.values():
        if value:
            roots.append(_resolve(value).parent)
    unique = []
    seen = set()
    for item in roots:
        text = str(item)
        if text not in seen:
            seen.add(text)
            unique.append(item)
    return unique


def is_allowed_path(path: str | Path, config=None) -> bool:
    try:
        resolved = _resolve(path)
    except Exception:
        return False
    if not resolved.exists():
        return False
    for root in allowed_media_roots(config):
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def parse_media_meta(path: str | Path, modality_hint: str | None = None) -> dict:
    resolved = Path(path)
    parts = [part for part in resolved.parts]
    date = ""
    hour = ""
    modality = MODALITY_ALIASES.get((modality_hint or "").lower(), "")
    for part in parts:
        if DATE_DIR_RE.match(part):
            date = f"{part[0:4]}-{part[4:6]}-{part[6:8]}"
            date_raw = part
            break
    else:
        date_raw = ""
    match = HOUR_FILE_RE.match(resolved.name)
    if match:
        hour = match.group(1)
    else:
        inter = INTERMEDIATE_RE.match(resolved.name)
        if inter:
            date_raw = inter.group(1)
            date = f"{date_raw[0:4]}-{date_raw[4:6]}-{date_raw[6:8]}"
            hour = inter.group(2)
    if not modality:
        for part in parts:
            alias = MODALITY_ALIASES.get(part.lower())
            if alias:
                modality = alias
                break
        name_l = resolved.name.lower()
        if "depth" in name_l:
            modality = "depth_video"
        elif "rgb" in name_l:
            modality = "rgb_video"
        elif name_l.endswith((".wav", ".mp3", ".m4a")):
            modality = "audio"
    resolved_full = _resolve(path)
    return {
        "path": str(resolved_full if resolved_full.exists() or Path(path).is_absolute() else path),
        "name": resolved.name,
        "date": date,
        "date_raw": date_raw,
        "hour": hour,
        "modality": modality or "unknown",
        "suffix": resolved.suffix.lower(),
        "exists": resolved_full.exists(),
        "size": resolved_full.stat().st_size if resolved_full.exists() else 0,
    }


def _match_filters(meta: dict, modality: str | None, date: str | None) -> bool:
    if modality:
        wanted = MODALITY_ALIASES.get(modality.lower(), modality)
        got = MODALITY_ALIASES.get(str(meta.get("modality") or "").lower(), meta.get("modality"))
        if got != wanted:
            return False
    if date:
        compact = date.replace("-", "")
        if meta.get("date_raw") != compact and meta.get("date") != date:
            return False
    return True


def list_sensor_items(modality: str | None = None, date: str | None = None, limit: int = 400) -> dict:
    config = _load_config()
    name = dataset_name(config)
    sensor = ((config.get("execution_settings") or {}).get("sensor_database_path") or {}).get(name) or {}
    items = []
    dates = set()
    modalities = set()
    for folder_key, folder in sensor.items():
        if not folder:
            continue
        root = _resolve(folder)
        hint = MODALITY_ALIASES.get(str(folder_key).lower(), str(folder_key))
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            for filename in sorted(filenames):
                suffix = Path(filename).suffix.lower()
                if suffix not in MEDIA_EXT:
                    continue
                full = Path(dirpath) / filename
                meta = parse_media_meta(full, hint)
                modalities.add(meta["modality"])
                if meta["date"]:
                    dates.add(meta["date"])
                if _match_filters(meta, modality, date):
                    items.append(meta)
    items.sort(key=lambda row: (row.get("date") or "", row.get("hour") or "", row.get("name") or ""))
    return {
        "items": items[:limit],
        "total": len(items),
        "truncated": len(items) > limit,
        "dates": sorted(dates),
        "modalities": sorted(modalities),
    }


def _load_json(path: Path):
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


def list_execution_cache(modality: str | None = None, date: str | None = None, tool: str | None = None, limit: int = 300) -> dict:
    config = _load_config()
    cache_path = _resolve((config.get("execution_settings") or {}).get("execution_cache_path") or "")
    cache = _load_json(cache_path) or {}
    items = []
    tools = sorted(cache.keys())
    dates = set()
    modalities = set()
    for tool_name, entries in cache.items():
        if tool and tool not in tool_name:
            continue
        if not isinstance(entries, dict):
            continue
        for source, payload in entries.items():
            meta = parse_media_meta(source)
            if "thermal" in tool_name:
                meta["modality"] = "thermal_video"
            elif "rgb" in tool_name:
                meta["modality"] = "rgb_video"
            elif "depth" in tool_name:
                meta["modality"] = "depth_video"
            modalities.add(meta["modality"])
            if meta["date"]:
                dates.add(meta["date"])
            if not _match_filters(meta, modality, date):
                continue
            labels = []
            outputs = []
            if isinstance(payload, dict):
                raw_labels = payload.get("label") or []
                if isinstance(raw_labels, list):
                    labels = [str(item) for item in raw_labels[:40]]
                for key, values in payload.items():
                    if str(key) in {"label", "pre_sign", "post_sign", "text", "status"}:
                        continue
                    if not (str(key).endswith("_video") or str(key) == "audio"):
                        continue
                    if isinstance(values, list):
                        outputs.extend(str(item) for item in values[:40])
            items.append(
                {
                    "tool": tool_name,
                    "source": source,
                    "labels": labels,
                    "outputs": outputs,
                    **meta,
                }
            )
    items.sort(key=lambda row: (row.get("tool") or "", row.get("date") or "", row.get("hour") or ""))
    return {
        "items": items[:limit],
        "total": len(items),
        "truncated": len(items) > limit,
        "tools": tools,
        "dates": sorted(dates),
        "modalities": sorted(modalities),
        "path": str(cache_path),
        "exists": cache_path.exists(),
    }


def list_other_caches() -> dict:
    config = _load_config()
    execution = config.get("execution_settings") or {}

    def summarize(path_key: str, kind: str) -> dict:
        path = _resolve(execution.get(path_key) or "")
        data = _load_json(path)
        if data is None:
            return {"kind": kind, "path": str(path), "exists": path.exists(), "count": 0, "keys": []}
        if isinstance(data, dict):
            keys = list(data.keys())
            return {
                "kind": kind,
                "path": str(path),
                "exists": True,
                "count": len(keys),
                "keys": keys[:80],
            }
        return {"kind": kind, "path": str(path), "exists": True, "count": 0, "keys": []}

    embedding = summarize("embedding_cache_path", "embedding")
    data = _load_json(_resolve(execution.get("embedding_cache_path") or ""))
    if isinstance(data, dict):
        embedding["sample_queries"] = list(data.keys())[:40]
        embedding["note"] = "仅列出查询文本，不返回向量"
    return {
        "embedding": embedding,
        "assessment": summarize("assessment_cache_path", "assessment"),
        "flops": summarize("flops_cache_path", "flops"),
    }


def list_intermediate(modality: str | None = None, date: str | None = None, limit: int = 400) -> dict:
    config = _load_config()
    name = dataset_name(config)
    folder = ((config.get("execution_settings") or {}).get("intermediate_output_path") or {}).get(name)
    if not folder:
        return {"items": [], "total": 0, "dates": [], "modalities": [], "path": ""}
    root = _resolve(folder)
    items = []
    dates = set()
    modalities = set()
    if root.exists():
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            for filename in sorted(filenames):
                suffix = Path(filename).suffix.lower()
                if suffix not in MEDIA_EXT:
                    continue
                full = Path(dirpath) / filename
                meta = parse_media_meta(full)
                meta["folder"] = os.path.relpath(dirpath, root)
                modalities.add(meta["modality"])
                if meta["date"]:
                    dates.add(meta["date"])
                if _match_filters(meta, modality, date):
                    items.append(meta)
    items.sort(key=lambda row: (row.get("date") or "", row.get("hour") or "", row.get("name") or ""))
    return {
        "items": items[:limit],
        "total": len(items),
        "truncated": len(items) > limit,
        "dates": sorted(dates),
        "modalities": sorted(modalities),
        "path": str(root),
        "exists": root.exists(),
    }


def list_tools_and_models() -> dict:
    config = _load_config()
    name = dataset_name(config)
    vocab_path = _resolve(
        Path((config.get("planning_settings") or {}).get("tool_files_path") or "") / name / "vocabulary_set.json"
    )
    vocabulary = _load_json(vocab_path) or {}
    impl_names = {}
    runtime_notes = []
    try:
        from src.execution.toolbox import tool_name_tool_implementation_map

        impl_names = {
            tool_name: getattr(fn, "__name__", str(fn))
            for tool_name, fn in tool_name_tool_implementation_map.items()
        }
    except Exception as exc:
        runtime_notes.append(
            {
                "name": "toolbox",
                "note": f"未能加载 tool 实现映射（不影响浏览词表）：{exc}",
            }
        )
    checkpoints = ((config.get("execution_settings") or {}).get("model_checkpoint_paths") or {}).get(name) or {}
    tools = []
    for category, entries in vocabulary.items():
        if not isinstance(entries, list):
            continue
        for tool in entries:
            tool_name = tool.get("tool_name")
            checkpoint_key = TOOL_TO_CHECKPOINT.get(tool_name)
            checkpoint = checkpoints.get(checkpoint_key) if checkpoint_key else None
            tools.append(
                {
                    "category": category,
                    "tool_name": tool_name,
                    "description": tool.get("description") or tool.get("tool_description") or "",
                    "input": tool.get("input") or {},
                    "output": tool.get("output") or {},
                    "modality": tool.get("modality"),
                    "implementation": impl_names.get(tool_name),
                    "checkpoint": checkpoint,
                    "checkpoint_exists": bool(checkpoint and _resolve(checkpoint).exists()) if checkpoint else False,
                }
            )
    models = []
    for key, path in checkpoints.items():
        resolved = _resolve(path) if path else None
        models.append(
            {
                "name": key,
                "tool_name": CHECKPOINT_TO_TOOL.get(key),
                "path": str(path),
                "exists": bool(resolved and resolved.exists()),
                "size": resolved.stat().st_size if resolved and resolved.exists() else 0,
            }
        )
    yolo_note = {
        "name": "object-detection-rgb",
        "note": "RGB 目标检测使用 Ultralytics YOLO + DeepSort，权重在运行时加载（配置中未列出独立 checkpoint）。",
    }
    runtime_notes.append(yolo_note)
    return {
        "tools": tools,
        "models": models,
        "runtime_notes": runtime_notes,
        "vocabulary_path": str(vocab_path),
        "implementation_tools": sorted(impl_names.keys()),
    }


def browse_filters() -> dict:
    sensor = list_sensor_items(limit=0)
    cache = list_execution_cache(limit=0)
    intermediate = list_intermediate(limit=0)
    dates = sorted(set(sensor["dates"]) | set(cache["dates"]) | set(intermediate["dates"]))
    modalities = sorted(set(sensor["modalities"]) | set(cache["modalities"]) | set(intermediate["modalities"]))
    return {
        "dates": dates,
        "modalities": modalities,
        "cache_tools": cache.get("tools") or [],
    }


def first_frame_jpeg(path: str, max_width: int = 320) -> Optional[bytes]:
    import cv2

    resolved = _resolve(path)
    if not is_allowed_path(resolved):
        return None
    capture = cv2.VideoCapture(str(resolved))
    ok, frame = capture.read()
    capture.release()
    if not ok or frame is None:
        return None
    height, width = frame.shape[:2]
    if width > max_width:
        scale = max_width / float(width)
        frame = cv2.resize(frame, (max_width, max(1, int(height * scale))))
    ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    if not ok:
        return None
    return encoded.tobytes()


def sensor_cover_path(modality: str, date: str, hour: int) -> Optional[str]:
    config = _load_config()
    sensor = (config.get("execution_settings") or {}).get("sensor_database_path") or {}
    wanted = MODALITY_ALIASES.get(modality, modality)
    for key, folder in sensor.get(dataset_name(config), {}).items():
        if not folder or MODALITY_ALIASES.get(key, key) != wanted:
            continue
        day = _resolve(folder) / date.replace("-", "")
        for suffix in (".avi", ".mp4", ".mkv", ".mov"):
            clip = day / f"{hour:02d}{suffix}"
            if clip.is_file():
                return str(clip)
    return None
