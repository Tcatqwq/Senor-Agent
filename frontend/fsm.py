"""Mermaid finite-state machine for the debug view."""

from __future__ import annotations

NODES = (
    "solvability",
    "plan",
    "validate",
    "execute",
    "observe",
    "act",
    "wait",
    "respond",
    "unsolvable",
    "bad_plan",
)

OBSERVE_EDGES = (
    ("continue", "execute"),
    ("replan", "plan"),
    ("act", "act"),
    ("wait", "wait"),
    ("respond", "respond"),
)

USER_STAGE_LABELS = {
    "solvability": "可解性检查",
    "plan": "规划",
    "validate": "校验计划",
    "execute": "执行",
    "observe": "观察",
    "act": "动作",
    "wait": "等待",
    "respond": "生成回复",
    "unsolvable": "不可求解",
    "bad_plan": "计划失败",
    "done": "完成",
}


def mermaid_diagram(current_node: str | None = None, route: str | None = None) -> str:
    current = (current_node or "").strip()
    chosen = (route or "").strip()
    lines = [
        "stateDiagram-v2",
        "    direction LR",
        "    [*] --> solvability",
        "    solvability --> plan: solvable",
        "    solvability --> unsolvable: unsolvable",
        "    unsolvable --> [*]",
        "    plan --> validate",
        "    validate --> plan: retry",
        "    validate --> execute: ok",
        "    validate --> respond: empty",
        "    validate --> bad_plan: fail",
        "    bad_plan --> [*]",
        "    execute --> observe",
    ]
    for name, target in OBSERVE_EDGES:
        mark = " ★" if current == "observe" and chosen == name else ""
        lines.append(f"    observe --> {target}: {name}{mark}")
    lines.extend(
        [
            "    act --> execute",
            "    wait --> [*]",
            "    respond --> [*]",
            "    classDef current fill:#e8a317,stroke:#8a5a00,color:#1a1408,stroke-width:3px",
            "    classDef idle fill:#232833,stroke:#4a5568,color:#d7d3c8",
            "    classDef chosen fill:#2f5d4a,stroke:#8fd9a8,color:#e8f5ee,stroke-width:2px",
        ]
    )
    idle = [node for node in NODES if node != current]
    if idle:
        lines.append("    class " + ",".join(idle) + " idle")
    if current in NODES:
        lines.append(f"    class {current} current")
    if current == "observe" and chosen:
        target = dict(OBSERVE_EDGES).get(chosen)
        if target and target != current:
            lines.append(f"    class {target} chosen")
    return "\n".join(lines)


def stage_payload(node: str | None, route: str | None = None, observation=None) -> dict:
    return {
        "node": node or "",
        "route": route or "",
        "label": USER_STAGE_LABELS.get(node or "", node or ""),
        "observe_options": [name for name, _ in OBSERVE_EDGES],
        "observation": observation or {},
        "mermaid": mermaid_diagram(node, route),
    }
