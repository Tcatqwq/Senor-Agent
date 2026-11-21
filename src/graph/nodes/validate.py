import json
import re

from src.graph.progress import report_planning_progress
from src.observation.policies import load_graph_settings
from src.planning.plan_validator import check_plan, normalize_plan
from src.utils import print_tips, visualize_plan


def _coerce_plan_string(planning_str):
    if planning_str is None:
        return "[]"
    if not isinstance(planning_str, str):
        return json.dumps(planning_str)
    text = planning_str.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()
    match = re.search(r"(\[.*\])", text, flags=re.DOTALL)
    if match:
        return match.group(1)
    return text


def validate_node(state):
    planning_str = _coerce_plan_string(state.get("plan"))
    retries = int(state.get("validate_retries") or 0)
    max_retries = int(load_graph_settings().get("max_validate_retries", 3))
    print_tips("Validate plan", text_color="yellow", emoji="white_check_mark")
    report_planning_progress("validate", "running")

    def report(check, status, **details):
        report_planning_progress("validate", status, check=check, **details)

    result = check_plan(planning_str, report=report)
    if not result.get("pass"):
        retries += 1
        error = result.get("error") or "Grammar Checking Failed"
        print_tips(f"Grammar checking failed: {error}", text_color="yellow", emoji="x")
        report_planning_progress("validate", "failed" if retries >= max_retries else "retry")
        if retries >= max_retries:
            return {
                "validate_retries": retries,
                "route": "bad_plan",
                "error": error,
                "final_response": "Grammar Checking Failed",
                "planning_feedback": error,
            }
        return {
            "validate_retries": retries,
            "route": "plan",
            "planning_feedback": error,
        }

    plan = json.loads(planning_str)
    plan = normalize_plan(plan)
    if len(plan) == 0:
        print_tips("Empty plan", text_color="yellow", emoji="warning")
        report_planning_progress("validate", "empty")
        return {
            "plan": [],
            "remaining_plan": [],
            "validate_retries": 0,
            "route": "respond",
            "final_response": "Empty Plan",
        }

    visualize_plan(plan)
    report_planning_progress("validate", "passed", plan=plan)
    return {
        "plan": plan,
        "remaining_plan": list(plan),
        "validate_retries": 0,
        "route": "execute",
    }
