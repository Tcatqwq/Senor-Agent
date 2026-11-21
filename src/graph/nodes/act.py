from src.observation.policies import load_graph_settings
from src.utils import print_tips


def _next_tool_id(plan, remaining, results):
    ids = [tool.get("id", -1) for tool in (plan or [])]
    ids.extend(tool.get("id", -1) for tool in (remaining or []))
    ids.extend(int(key) for key in results if int(key) >= 0)
    return (max(ids) + 1) if ids else 0


def act_node(state):
    print_tips("Act: insert proactive tools", text_color="magenta", emoji="bell")
    settings = load_graph_settings()
    act_tools = settings.get("observation_policies", {}).get("act_tools") or ["send-notification"]
    observation = state.get("observation") or {}
    labels = observation.get("policy_labels") or observation.get("labels") or []
    remaining = list(state.get("remaining_plan") or [])
    inserted = list(state.get("inserted_act_ids") or [])
    plan = list(state.get("plan") or [])
    results = state.get("results") or {}

    for tool_name in act_tools:
        if tool_name in inserted:
            continue
        new_id = _next_tool_id(plan, remaining, results)
        text = f"Proactive alert: observed {', '.join(labels) or 'target event'}"
        new_tool = {
            "tool_name": tool_name,
            "id": new_id,
            "dep": [-1],
            "args": {"text": [text]},
        }
        remaining.append(new_tool)
        inserted.append(tool_name)
        print_tips(
            f"Inserted {tool_name} id={new_id}",
            text_color="magenta",
            emoji="bell",
            border=False,
        )

    return {
        "remaining_plan": remaining,
        "inserted_act_ids": inserted,
        "route": "execute",
    }
