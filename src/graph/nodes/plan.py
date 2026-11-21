from src.graph.progress import report_planning_progress
from src.planning.planner import planning
from src.utils import print_tips


def plan_node(state):
    user_input = state["user_input"]
    feedback = state.get("planning_feedback") or ""
    print_tips("Planning stage", text_color="yellow", emoji="bulb")
    report_planning_progress("plan", "running")

    query = user_input
    if feedback:
        query = (
            f"{user_input}\n\n"
            "[Previous execution observation — revise the tool-calling plan accordingly] "
            f"{feedback}"
        )

    planning_str = planning(query)
    print_tips("Plan string generated", text_color="yellow", emoji="bulb", border=False)
    report_planning_progress("plan", "passed")
    return {
        "plan": planning_str,
        "route": "validate",
    }
