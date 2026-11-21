import atexit
import os
import time
from multiprocessing import get_context

import yaml

from src.utils import print_tips

_DEFAULT_MAX_PARALLEL_TOOLS = 3
_pool = None
_pool_size = None
_atexit_registered = False


def _warmup_job():
    time.sleep(0.1)
    return True


def _max_parallel_tools():
    config_path = os.environ.get("CONFIG_PATH")
    if not config_path:
        return _DEFAULT_MAX_PARALLEL_TOOLS
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.load(f, Loader=yaml.FullLoader) or {}
        value = config.get("graph_settings", {}).get("max_parallel_tools")
        if value is None:
            return _DEFAULT_MAX_PARALLEL_TOOLS
        return max(1, int(value))
    except Exception:
        return _DEFAULT_MAX_PARALLEL_TOOLS


def _register_atexit():
    global _atexit_registered
    if not _atexit_registered:
        atexit.register(close_pool)
        _atexit_registered = True


def get_pool():
    global _pool, _pool_size
    size = _max_parallel_tools()
    if _pool is not None and _pool_size != size:
        close_pool()
    if _pool is None:
        ctx = get_context("spawn")
        _pool = ctx.Pool(processes=size)
        _pool_size = size
        _register_atexit()
        print_tips(
            f"Process pool created with {size} workers.",
            text_color="yellow",
            emoji="white_check_mark",
            border=False,
        )
    return _pool


def warmup_pool(num_dummy_tool_calls=16):
    pool = get_pool()
    jobs = [pool.apply_async(_warmup_job) for _ in range(num_dummy_tool_calls)]
    for job in jobs:
        job.get()
    print_tips("Process pool warmed up.", text_color="yellow", emoji="white_check_mark", border=False)


def close_pool():
    global _pool, _pool_size
    if _pool is None:
        return
    try:
        _pool.close()
        _pool.join()
    except Exception:
        try:
            _pool.terminate()
        except Exception:
            pass
    _pool = None
    _pool_size = None
