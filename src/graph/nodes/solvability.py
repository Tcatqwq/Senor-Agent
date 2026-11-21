from src.graph.progress import report_planning_progress
from src.planning.checker import command_solvability_check
from src.utils import print_tips


def solvability_node(state):
    user_input = state["user_input"]
    print_tips("Solvability check", text_color="yellow", emoji="mag")
    report_planning_progress("solvability", "running")
    flag, response = command_solvability_check(user_input=user_input)
    print_tips(f"Solvability flag: {flag}", text_color="yellow", emoji="mag", border=False)
    status = {"yes": "passed", "no": "failed", "skip": "skipped"}.get(flag, "uncertain")
    report_planning_progress("solvability", status)

    if flag == "no":
        return {
            "solvability_flag": flag,
            "solvability_response": response or "",
            "route": "unsolvable",
            "final_response": "Command Not Solvable!",
        }

    return {
        "solvability_flag": flag,
        "solvability_response": response or "",
        "route": "plan",
    }
