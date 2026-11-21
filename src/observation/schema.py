from typing import Any, Dict, List, Optional, TypedDict


class Observation(TypedDict, total=False):
    empty_results: bool
    execute_next_failed: bool
    labels: List[str]
    labels_by_modality: Dict[str, List[str]]
    remaining_count: int
    ready_count: int
    last_tool_names: List[str]
    policy_hit: bool
    policy_labels: List[str]
    summary: str
