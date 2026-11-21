"""Initialize ignored machine-local configuration and runtime caches."""

import json
import fcntl
from pathlib import Path

import yaml


def _create_if_empty(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.seek(0)
        if not handle.read(1):
            handle.write(content)
            handle.flush()


def prepare_local_files(root):
    root = Path(root).resolve()
    config_path = root / "config/config.yaml"
    template = root / "config/config.yaml.example"
    _create_if_empty(config_path, template.read_text(encoding="utf-8"))
    with config_path.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    execution = config.get("execution_settings") or {}
    cache_defaults = {
        "execution_cache_path": {
            "object-detection-rgb": {},
            "object-detection-depth": {},
            "human-activity-classification-rgb": {},
            "human-activity-classification-depth": {},
        },
        "embedding_cache_path": {},
        "assessment_cache_path": {
            "rgb-video-quality-assessment": {},
            "depth-video-quality-assessment": {},
        },
        "flops_cache_path": {},
    }
    for key, value in cache_defaults.items():
        configured = execution.get(key)
        if configured:
            _create_if_empty(root / configured, json.dumps(value, indent=2))
    return config_path
