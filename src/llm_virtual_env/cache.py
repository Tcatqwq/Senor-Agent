"""Independent execution cache for the LLM virtual environment."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager

from src.llm_virtual_env.config import cache_path

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


@contextmanager
def _locked(handle, exclusive: bool):
    if fcntl is None:
        yield handle
        return
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
    try:
        yield handle
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _empty() -> dict:
    return {}


def load_cache() -> dict:
    path = cache_path()
    if not path.exists():
        return _empty()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            with _locked(handle, exclusive=False):
                data = json.load(handle) or {}
        return data if isinstance(data, dict) else _empty()
    except (json.JSONDecodeError, OSError):
        return _empty()


def save_cache(cache: dict) -> None:
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(cache if isinstance(cache, dict) else _empty(), handle, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def get_cached(tool_name: str, key: str):
    cache = load_cache()
    bucket = cache.get(tool_name)
    if isinstance(bucket, dict):
        return bucket.get(key)
    return None


def put_cached(tool_name: str, key: str, value: dict) -> None:
    cache = load_cache()
    bucket = cache.get(tool_name)
    if not isinstance(bucket, dict):
        bucket = {}
    bucket[key] = value
    cache[tool_name] = bucket
    save_cache(cache)


def clear_cache() -> None:
    save_cache(_empty())


def list_entries(limit: int = 400) -> dict:
    cache = load_cache()
    items = []
    for tool_name, bucket in cache.items():
        if not isinstance(bucket, dict):
            continue
        for key, value in bucket.items():
            output = (value or {}).get("output") if isinstance(value, dict) else {}
            labels = []
            if isinstance(output, dict):
                raw = output.get("label")
                if isinstance(raw, list):
                    labels = [str(item) for item in raw]
            items.append(
                {
                    "tool": tool_name,
                    "key": key,
                    "labels": labels,
                    "execute_next": bool((value or {}).get("execute_next", True)),
                    "output": output if isinstance(output, dict) else {},
                }
            )
            if len(items) >= limit:
                break
        if len(items) >= limit:
            break
    return {
        "path": str(cache_path()),
        "items": items,
        "total": sum(len(bucket) for bucket in cache.values() if isinstance(bucket, dict)),
        "tools": sorted(str(name) for name in cache.keys()),
    }
