from src.observation.policies import build_observation, choose_route
from src.utils import print_tips


def observe_node(state):
    print_tips("Observe", text_color="cyan", emoji="eye")
    observation = build_observation(state)
    route = choose_route(state, observation)
    print_tips(
        f"route={route}; {observation.get('summary')}",
        text_color="cyan",
        emoji="eye",
        border=False,
    )

    updates = {
        "observation": observation,
        "route": route,
    }
    if route == "replan":
        updates["replan_count"] = int(state.get("replan_count") or 0) + 1
        updates["planning_feedback"] = observation.get("summary", "")
        updates["remaining_plan"] = []
        updates["results"] = {}
        updates["last_executed_ids"] = []
        updates["modality_decisions"] = []
    return updates
