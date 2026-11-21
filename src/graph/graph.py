import time
from datetime import datetime

from langgraph.graph import END, START, StateGraph

from src.graph.nodes.act import act_node
from src.graph.nodes.execute import execute_node
from src.graph.nodes.observe import observe_node
from src.graph.nodes.plan import plan_node
from src.graph.nodes.respond import respond_node
from src.graph.nodes.solvability import solvability_node
from src.graph.nodes.validate import validate_node
from src.graph.nodes.wait import wait_node
from src.graph.state import AgentState
from src.token_usage import copy_usage_map, track_tokens


def route_after_solvability(state: AgentState):
    if state.get("route") == "unsolvable":
        return END
    return "plan"


def route_after_validate(state: AgentState):
    route = state.get("route")
    if route == "plan":
        return "plan"
    if route == "bad_plan":
        return END
    if route == "respond":
        return "respond"
    return "execute"


def route_after_observe(state: AgentState):
    return state.get("route") or "respond"


def timed_node(name, fn):
    def wrapper(state):
        token_usage = copy_usage_map(state.get("token_usage"))
        t0 = time.perf_counter()
        with track_tokens(token_usage, name):
            updates = fn(state) or {}
        elapsed = time.perf_counter() - t0
        timings = dict(state.get("timings") or {})
        counts = dict(state.get("timing_counts") or {})
        timings[name] = timings.get(name, 0.0) + elapsed
        counts[name] = counts.get(name, 0) + 1
        updates["timings"] = timings
        updates["timing_counts"] = counts
        updates["token_usage"] = token_usage
        return updates

    wrapper.__name__ = getattr(fn, "__name__", name)
    return wrapper


def build_graph(start_at: str = "solvability"):
    builder = StateGraph(AgentState)
    builder.add_node("solvability", timed_node("solvability", solvability_node))
    builder.add_node("plan", timed_node("plan", plan_node))
    builder.add_node("validate", timed_node("validate", validate_node))
    builder.add_node("execute", timed_node("execute", execute_node))
    builder.add_node("observe", timed_node("observe", observe_node))
    builder.add_node("act", timed_node("act", act_node))
    builder.add_node("wait", timed_node("wait", wait_node))
    builder.add_node("respond", timed_node("respond", respond_node))

    builder.add_edge(START, start_at)
    builder.add_conditional_edges("solvability", route_after_solvability)
    builder.add_edge("plan", "validate")
    builder.add_conditional_edges("validate", route_after_validate)
    builder.add_edge("execute", "observe")
    builder.add_conditional_edges(
        "observe",
        route_after_observe,
        {
            "continue": "execute",
            "replan": "plan",
            "act": "act",
            "wait": "wait",
            "respond": "respond",
        },
    )
    builder.add_edge("act", "execute")
    builder.add_edge("wait", END)
    builder.add_edge("respond", END)
    return builder.compile()


def initial_state(user_input: str, tick_loop: bool = False) -> AgentState:
    now = datetime.now().isoformat(timespec="seconds")
    return {
        "user_input": user_input,
        "solvability_flag": None,
        "solvability_response": "",
        "plan": [],
        "remaining_plan": [],
        "results": {},
        "observation": {},
        "route": "",
        "replan_count": 0,
        "validate_retries": 0,
        "current_time": now,
        "next_tick": "",
        "waiting_until": "",
        "final_response": "",
        "inserted_act_ids": [],
        "planning_feedback": "",
        "error": "",
        "tick_loop": tick_loop,
        "last_executed_ids": [],
        "timings": {},
        "timing_counts": {},
        "token_usage": {},
    }


def run_agent(user_input: str, tick_loop: bool = False, start_at: str = "solvability", state=None):
    graph = build_graph(start_at=start_at)
    payload = state if state is not None else initial_state(user_input, tick_loop=tick_loop)
    if payload.get("timings") is None:
        payload["timings"] = {}
    if payload.get("timing_counts") is None:
        payload["timing_counts"] = {}
    if payload.get("token_usage") is None:
        payload["token_usage"] = {}
    t0 = time.perf_counter()
    result = graph.invoke(payload, {"recursion_limit": 80})
    wall = time.perf_counter() - t0
    timings = dict(result.get("timings") or {})
    timings["graph_wall"] = timings.get("graph_wall", 0.0) + wall
    result["timings"] = timings
    return result
