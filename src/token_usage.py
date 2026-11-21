from contextlib import contextmanager
from contextvars import ContextVar

_active_bucket = ContextVar("token_usage_bucket", default=None)


def empty_usage():
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "llm_calls": 0,
    }


def copy_usage_map(store):
    return {key: dict(value) for key, value in (store or {}).items()}


def add_usage(left, right):
    merged = empty_usage()
    for src in (left, right):
        if not src:
            continue
        merged["prompt_tokens"] += int(src.get("prompt_tokens") or 0)
        merged["completion_tokens"] += int(src.get("completion_tokens") or 0)
        merged["total_tokens"] += int(src.get("total_tokens") or 0)
        merged["llm_calls"] += int(src.get("llm_calls") or 0)
    return merged


def sum_usage_map(store, skip_keys=None):
    skip_keys = skip_keys or set()
    total = empty_usage()
    for key, value in (store or {}).items():
        if key in skip_keys:
            continue
        total = add_usage(total, value)
    return total


def record_usage(prompt_tokens=0, completion_tokens=0, total_tokens=None):
    bucket = _active_bucket.get()
    if bucket is None:
        return
    prompt = int(prompt_tokens or 0)
    completion = int(completion_tokens or 0)
    total = int(total_tokens) if total_tokens is not None else prompt + completion
    bucket["prompt_tokens"] = int(bucket.get("prompt_tokens") or 0) + prompt
    bucket["completion_tokens"] = int(bucket.get("completion_tokens") or 0) + completion
    bucket["total_tokens"] = int(bucket.get("total_tokens") or 0) + total
    bucket["llm_calls"] = int(bucket.get("llm_calls") or 0) + 1


def record_openai_usage(usage):
    if usage is None:
        return
    if isinstance(usage, dict):
        record_usage(
            prompt_tokens=usage.get("prompt_tokens") or usage.get("input_tokens") or 0,
            completion_tokens=usage.get("completion_tokens") or usage.get("output_tokens") or 0,
            total_tokens=usage.get("total_tokens"),
        )
        return
    record_usage(
        prompt_tokens=getattr(usage, "prompt_tokens", None) or getattr(usage, "input_tokens", None) or 0,
        completion_tokens=getattr(usage, "completion_tokens", None) or getattr(usage, "output_tokens", None) or 0,
        total_tokens=getattr(usage, "total_tokens", None),
    )


def record_langchain_usage(response):
    meta = getattr(response, "usage_metadata", None)
    if meta:
        record_openai_usage(meta)
        return
    resp_meta = getattr(response, "response_metadata", None) or {}
    usage = resp_meta.get("token_usage") or resp_meta.get("usage")
    if usage:
        record_openai_usage(usage)
        return
    extra = getattr(response, "additional_kwargs", None) or {}
    record_openai_usage(extra.get("usage") or extra.get("token_usage"))


@contextmanager
def track_tokens(store, stage):
    if store is None:
        yield None
        return
    bucket = store.get(stage)
    if bucket is None:
        bucket = empty_usage()
        store[stage] = bucket
    token = _active_bucket.set(bucket)
    try:
        yield bucket
    finally:
        _active_bucket.reset(token)
