"""Background LangGraph runner with SSE events. Does not modify run_agent()."""

from __future__ import annotations

import asyncio
import json
import re
import sys
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Optional

from frontend.fsm import stage_payload
from frontend.modality_flow import adaptive_flow, run_flow

ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
SKIP_STREAM_KEYS = {"__start__", "__end__", "START", "END"}


class RunCancelled(Exception):
    """Raised when the user stops a run between graph nodes or tick sleeps."""


def json_safe(value: Any, depth: int = 0, max_depth: int = 8, max_list: int = 40) -> Any:
    if depth > max_depth:
        return "<truncated>"
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        out = {}
        for key, item in list(value.items())[:80]:
            out[str(key)] = json_safe(item, depth + 1, max_depth, max_list)
        if len(value) > 80:
            out["__more__"] = len(value) - 80
        return out
    if isinstance(value, (list, tuple, set)):
        seq = list(value)
        out = [json_safe(item, depth + 1, max_depth, max_list) for item in seq[:max_list]]
        if len(seq) > max_list:
            out.append(f"... {len(seq) - max_list} more")
        return out
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def _strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


class _TeeStream:
    def __init__(self, original, callback):
        self.original = original
        self.callback = callback
        self._buf = ""

    def write(self, data):
        if not isinstance(data, str):
            data = data.decode("utf-8", errors="replace")
        try:
            self.original.write(data)
        except Exception:
            pass
        self._buf += data
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            cleaned = _strip_ansi(line).rstrip()
            if cleaned:
                self.callback(cleaned)

    def flush(self):
        try:
            self.original.flush()
        except Exception:
            pass
        leftover = _strip_ansi(self._buf).rstrip()
        if leftover:
            self.callback(leftover)
        self._buf = ""

    def isatty(self):
        return False


class RunSession:
    def __init__(self, query: str, tick_loop: bool, loop: asyncio.AbstractEventLoop):
        self.run_id = uuid.uuid4().hex[:12]
        self.query = query
        self.tick_loop = tick_loop
        self.loop = loop
        self.history: list[dict] = []
        self.modality_snapshots: dict[int, dict] = {}
        self.subscribers: list[asyncio.Queue] = []
        self.lock = threading.Lock()
        self.done = False
        self.error: Optional[str] = None
        self.busy = True
        self.cancelled = False
        self._cancel = threading.Event()

    def request_cancel(self) -> None:
        if self.done:
            return
        self._cancel.set()
        self.emit("log", {"text": "收到终止请求，将在当前步骤结束后停止"})
        self.emit("cancelling", {"run_id": self.run_id})

    def _raise_if_cancelled(self) -> None:
        if self._cancel.is_set():
            raise RunCancelled()

    def emit(self, event: str, data: Any, *, limit_preview: bool = True) -> None:
        item = {"event": event, "data": json_safe(data) if limit_preview else data}
        with self.lock:
            if event == "modality_flow":
                round_index = data["round"]
                if self.modality_snapshots.get(round_index) == item:
                    return
                self.modality_snapshots[round_index] = item
            else:
                self.history.append(item)
            queues = list(self.subscribers)
        for queue in queues:
            self.loop.call_soon_threadsafe(queue.put_nowait, item)

    def subscribe(self) -> tuple[list[dict], asyncio.Queue]:
        queue: asyncio.Queue = asyncio.Queue()
        with self.lock:
            history = [*self.modality_snapshots.values(), *self.history]
            self.subscribers.append(queue)
        return history, queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self.lock:
            if queue in self.subscribers:
                self.subscribers.remove(queue)

    def _emit_plan_if_ready(self, updates: dict) -> None:
        plan = updates.get("plan")
        if isinstance(plan, list):
            self.emit("plan", {"plan": plan})
        elif isinstance(plan, str) and plan.strip().startswith("["):
            try:
                parsed = json.loads(plan)
            except json.JSONDecodeError:
                return
            if isinstance(parsed, list):
                self.emit("plan", {"plan": parsed, "raw": True})

    def _emit_state(self, node: str, accumulated: dict) -> None:
        self.emit(
            "state",
            {
                "node": node,
                "route": accumulated.get("route"),
                "plan": accumulated.get("plan"),
                "remaining_plan": accumulated.get("remaining_plan"),
                "last_executed_ids": accumulated.get("last_executed_ids"),
                "observation": accumulated.get("observation"),
                "replan_count": accumulated.get("replan_count"),
                "validate_retries": accumulated.get("validate_retries"),
                "current_time": accumulated.get("current_time"),
                "waiting_until": accumulated.get("waiting_until"),
                "next_tick": accumulated.get("next_tick"),
                "error": accumulated.get("error"),
                "timings": accumulated.get("timings"),
                "timing_counts": accumulated.get("timing_counts"),
                "token_usage": accumulated.get("token_usage"),
                "inserted_act_ids": accumulated.get("inserted_act_ids"),
                "final_response": accumulated.get("final_response"),
                "results": accumulated.get("results"),
            },
        )

    def _stream_once(self, start_at: str, state: dict) -> dict:
        from src.graph.graph import build_graph
        from src.llm_virtual_env.config import is_enabled as virtual_enabled

        graph = build_graph(start_at=start_at)
        accumulated = dict(state)
        decisions_in_progress = {}
        pending_tools = {}
        completed_tools = {}

        def publish(flow):
            if flow["nodes"]:
                flow["round"] = accumulated.get("replan_count", 0)
                self.emit("modality_flow", flow, limit_preview=False)

        t0 = time.perf_counter()
        self._raise_if_cancelled()
        for mode, chunk in graph.stream(dict(accumulated), {"recursion_limit": 80}, stream_mode=["updates", "custom"]):
            if mode == "custom":
                if isinstance(chunk, dict) and "planning_progress" in chunk:
                    self.emit("planning_progress", {
                        **chunk["planning_progress"],
                        "round": accumulated.get("replan_count", 0),
                        "retry": accumulated.get("validate_retries", 0),
                    }, limit_preview=False)
                    continue
                phase = chunk.get("modality_progress") if isinstance(chunk, dict) else None
                if phase == "decision":
                    decision = chunk["data"]
                    decisions_in_progress[decision["date"], decision["hour"]] = decision
                    publish(adaptive_flow(decisions_in_progress.values()))
                elif phase in {"tool_started", "tool_finished", "tool_failed"} and not accumulated.get("modality_decisions"):
                    data = chunk["data"]
                    tool_id = data["id"]
                    if phase == "tool_started":
                        pending_tools[tool_id] = {"tool_calling_information": data, "preview_status": "running"}
                    elif phase == "tool_finished":
                        pending_tools.pop(tool_id, None)
                        completed_tools[tool_id] = data["result"]
                    elif phase == "tool_failed":
                        pending_tools[tool_id].update(preview_status="error", preview_reason=data["error"])
                    results = (accumulated.get("results") or {}) | completed_tools
                    publish(run_flow(results, virtual=chunk["virtual"], pending=pending_tools))
                continue
            if not isinstance(chunk, dict):
                self._raise_if_cancelled()
                continue
            for node, updates in chunk.items():
                if node in SKIP_STREAM_KEYS:
                    continue
                if not isinstance(updates, dict):
                    updates = {"value": updates}
                accumulated.update(updates)
                route = accumulated.get("route") or updates.get("route") or ""
                observation = accumulated.get("observation") if node == "observe" else None
                self.emit("stage", stage_payload(node, route, observation))
                self._emit_plan_if_ready(updates)
                if node == "execute" and (updates.get("last_executed_ids") or updates.get("modality_decisions")):
                    decisions = accumulated.get("modality_decisions")
                    flow = adaptive_flow(decisions) if decisions else run_flow(
                        updates.get("results") or {}, virtual=virtual_enabled()
                    )
                    publish(flow)
                    decisions_in_progress.clear()
                    pending_tools.clear()
                    completed_tools.clear()
                if updates.get("final_response"):
                    self.emit("response", {"text": updates.get("final_response")})
                self._emit_state(node, accumulated)
            self._raise_if_cancelled()
        wall = time.perf_counter() - t0
        timings = dict(accumulated.get("timings") or {})
        timings["graph_wall"] = timings.get("graph_wall", 0.0) + wall
        accumulated["timings"] = timings
        return accumulated

    def run(self) -> None:
        from src.graph.graph import initial_state
        from src.observation.policies import load_graph_settings

        original_stdout = sys.stdout
        original_stderr = sys.stderr
        tee = _TeeStream(original_stdout, lambda line: self.emit("log", {"text": line}))
        sys.stdout = tee
        sys.stderr = tee
        conversation_t0 = time.perf_counter()
        tick_sleep = 0.0
        tick_sleep_count = 0
        try:
            self.emit("stage", stage_payload("solvability"))
            payload = initial_state(self.query, tick_loop=self.tick_loop)
            result = self._stream_once("solvability", payload)

            if self.tick_loop:
                settings = load_graph_settings()
                interval = int(settings.get("tick_interval_seconds", 1800))
                while result.get("route") == "wait":
                    self._raise_if_cancelled()
                    waiting_until = result.get("waiting_until") or result.get("next_tick")
                    self.emit(
                        "tick",
                        {
                            "sleeping": True,
                            "interval": interval,
                            "waiting_until": waiting_until,
                        },
                    )
                    self.emit(
                        "log",
                        {"text": f"Tick-loop sleeping {interval}s until {waiting_until}"},
                    )
                    sleep_t0 = time.perf_counter()
                    if self._cancel.wait(timeout=interval):
                        tick_sleep += time.perf_counter() - sleep_t0
                        raise RunCancelled()
                    tick_sleep += time.perf_counter() - sleep_t0
                    tick_sleep_count += 1
                    result["current_time"] = datetime.now().isoformat(timespec="seconds")
                    result = self._stream_once("observe", result)

            self._raise_if_cancelled()
            timings = dict(result.get("timings") or {})
            timings["tick_sleep"] = tick_sleep
            timings["total"] = time.perf_counter() - conversation_t0
            counts = dict(result.get("timing_counts") or {})
            if tick_sleep_count:
                counts["tick_sleep"] = tick_sleep_count
            result["timings"] = timings
            result["timing_counts"] = counts
            if result.get("final_response"):
                self.emit("response", {"text": result.get("final_response")})
            self.emit(
                "done",
                {
                    "route": result.get("route"),
                    "final_response": result.get("final_response") or "",
                    "timings": timings,
                    "timing_counts": counts,
                    "token_usage": result.get("token_usage") or {},
                    "current_time": result.get("current_time"),
                },
            )
        except RunCancelled:
            self.cancelled = True
            self.error = "cancelled"
            self.emit("cancelled", {"message": "任务已终止"})
        except Exception as exc:
            self.error = str(exc)
            self.emit("run_error", {"message": str(exc)})
        finally:
            try:
                tee.flush()
            except Exception:
                pass
            sys.stdout = original_stdout
            sys.stderr = original_stderr
            self.done = True
            self.busy = False


class RunManager:
    def __init__(self):
        self._lock = threading.Lock()
        self.current: Optional[RunSession] = None

    def start(self, query: str, tick_loop: bool, loop: asyncio.AbstractEventLoop) -> RunSession:
        query = (query or "").strip()
        if not query:
            raise ValueError("query is empty")
        with self._lock:
            if self.current and not self.current.done:
                raise RuntimeError("A run is already in progress")
            session = RunSession(query, tick_loop, loop)
            self.current = session
        thread = threading.Thread(target=session.run, name=f"tasksense-run-{session.run_id}", daemon=True)
        thread.start()
        return session

    def get(self, run_id: str) -> Optional[RunSession]:
        with self._lock:
            if self.current and self.current.run_id == run_id:
                return self.current
        return None

    def status(self) -> dict:
        with self._lock:
            session = self.current
        if not session:
            return {"busy": False, "run_id": None}
        return {
            "busy": not session.done,
            "done": session.done,
            "run_id": session.run_id,
            "query": session.query,
            "tick_loop": session.tick_loop,
            "error": session.error,
            "cancelled": session.cancelled,
        }

    def cancel(self, run_id: str | None = None) -> dict:
        with self._lock:
            session = self.current
        if not session or session.done:
            raise RuntimeError("没有正在运行的任务")
        if run_id and session.run_id != run_id:
            raise RuntimeError("run_id 与当前任务不匹配")
        session.request_cancel()
        return {"ok": True, "run_id": session.run_id, "cancelling": True}


manager = RunManager()
