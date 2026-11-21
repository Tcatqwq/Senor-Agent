"""Load and query LLM virtual-environment settings."""

from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

DISABLE_ENV_VAR = "TASKSENSE_DISABLE_LLM_VIRTUAL"
PLACEHOLDERS = {None, "", "#", "EMPTY", "PUT_YOUR_API_KEY_HERE"}

DEFAULT_MODALITIES = ["text", "rgb_video", "depth_video"]
INITIAL_INPUT = "initial-input (tool calling id < 0)"

DEFAULT_HAR_LABELS = [
    "Boxing",
    "Pushing",
    "Patting on the shoulder",
    "Shaking hands",
    "Face-to-face conversation",
    "Standing still",
    "Waving arms",
    "Playing with phone using both hands",
    "Rubbing hands",
    "Touching head",
    "Coughing",
    "Jumping with both feet",
    "Stomping feet",
    "Walking",
    "Squatting",
    "Bending over",
    "Throwing things",
    "Picking up things",
    "Kicking the cabinet",
    "Searching for things in the cabinet",
    "Angrily hitting the cabinet",
    "Sitting still",
    "Sitting and making a phone call",
    "Sitting down",
    "Standing up",
    "Raising hand to check time/putting down",
    "Stretching",
    "Taking off/putting on a mask",
    "Lying on a mat",
    "Falling down/getting up",
]


def _config_path() -> str:
    path = os.environ.get("CONFIG_PATH")
    if not path:
        raise RuntimeError("CONFIG_PATH is not set")
    return path


def repo_root() -> Path:
    return Path(_config_path()).resolve().parent.parent


def load_raw_config() -> dict:
    with open(_config_path(), "r", encoding="utf-8") as handle:
        return yaml.load(handle, Loader=yaml.FullLoader) or {}


def cli_disabled() -> bool:
    value = str(os.environ.get(DISABLE_ENV_VAR, "")).strip().lower()
    return value in {"1", "true", "yes", "on"}


def load_virtual_env() -> dict:
    raw = load_raw_config().get("llm_virtual_env") or {}
    api = dict(raw.get("api") or {})
    date_range = dict(raw.get("date_range") or {})
    return {
        "enabled": bool(raw.get("enabled", False)),
        "api": {
            "api_key": api.get("api_key"),
            "base_url": api.get("base_url"),
            "model": api.get("model"),
            "temperature": api.get("temperature", 0),
            "timeout": api.get("timeout", 180),
        },
        "virtual_modality": bool(raw.get("virtual_modality", False)),
        "modalities": list(raw.get("modalities") or DEFAULT_MODALITIES),
        "date_range": {
            "start": str(date_range.get("start") or ""),
            "end": str(date_range.get("end") or date_range.get("start") or ""),
        },
        "har_labels": list(raw.get("har_labels") or []),
        "har_labels_by_modality": dict(raw.get("har_labels_by_modality") or {}),
        "dependencies": list(raw.get("dependencies") or []),
        "environment_info": str(raw.get("environment_info") or ""),
        "events": list(raw.get("events") or []),
        "use_cache": bool(raw.get("use_cache", True)),
        "cache_path": str(
            raw.get("cache_path") or "./external_files/llm_virtual_env/cache.json"
        ),
        "generated_dir": str(
            raw.get("generated_dir") or "./external_files/llm_virtual_env/generated/"
        ),
    }


def is_enabled() -> bool:
    if cli_disabled():
        return False
    return bool(load_virtual_env().get("enabled"))


def is_virtual_modality() -> bool:
    if not is_enabled():
        return False
    return bool(load_virtual_env().get("virtual_modality"))


def is_placeholder(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip() in PLACEHOLDERS:
        return True
    return value in PLACEHOLDERS


def resolve_path(path: str | Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = repo_root() / candidate
    return candidate


def cache_path() -> Path:
    return resolve_path(load_virtual_env()["cache_path"])


def generated_dir() -> Path:
    return resolve_path(load_virtual_env()["generated_dir"])


def real_tool_files_dir() -> Path:
    config = load_raw_config()
    dataset = config.get("dataset_name") or "inlab"
    tool_files = (config.get("planning_settings") or {}).get("tool_files_path") or ""
    return resolve_path(Path(tool_files) / dataset)


def snapshot_world(settings: dict | None = None) -> dict:
    """Fields that affect generated vocab/grammar (not call-result environment)."""
    settings = settings if settings is not None else load_virtual_env()
    return {
        "modalities": list(settings.get("modalities") or []),
        "har_labels": list(settings.get("har_labels") or []),
        "har_labels_by_modality": deepcopy(settings.get("har_labels_by_modality") or {}),
        "dependencies": deepcopy(settings.get("dependencies") or []),
    }
