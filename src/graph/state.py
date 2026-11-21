from typing import Any, Optional, TypedDict


class AgentState(TypedDict, total=False):
    user_input: str
    solvability_flag: Optional[str]
    solvability_response: str
    plan: list
    remaining_plan: list
    results: dict
    observation: dict
    route: str
    replan_count: int
    validate_retries: int
    current_time: str
    next_tick: str
    waiting_until: str
    final_response: str
    inserted_act_ids: list
    planning_feedback: str
    error: str
    tick_loop: bool
    last_executed_ids: list
    modality_decisions: list
    timings: dict
    timing_counts: dict
    token_usage: dict
