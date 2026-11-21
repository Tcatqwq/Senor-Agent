"""Background catalog rebuild so HTTP handlers stay non-blocking."""

from __future__ import annotations

import threading
from datetime import datetime, timezone

_lock = threading.Lock()
_state = {
    "status": "idle",
    "error": None,
    "result": None,
    "force": False,
    "started_at": None,
    "finished_at": None,
}


def snapshot() -> dict:
    with _lock:
        return dict(_state)


def start(*, force: bool = False) -> tuple[dict, bool]:
    with _lock:
        if _state["status"] == "running":
            return dict(_state), False
        _state.update(
            {
                "status": "running",
                "error": None,
                "result": None,
                "force": force,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": None,
            }
        )
    thread = threading.Thread(target=_run, kwargs={"force": force}, daemon=True)
    thread.start()
    return snapshot(), True


def _run(*, force: bool) -> None:
    from src.llm_virtual_env.generator import maybe_rebuild, rebuild

    try:
        result = rebuild() if force else maybe_rebuild()
        if result is None:
            status = "skipped"
            result = {"ok": True, "skipped": True, "reason": "virtual_modality_off"}
        elif result.get("skipped"):
            status = "skipped"
        else:
            status = "ok"
        error = None
    except Exception as exc:
        result = None
        status = "error"
        error = str(exc)
    with _lock:
        _state.update(
            {
                "status": status,
                "error": error,
                "result": result,
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
        )
