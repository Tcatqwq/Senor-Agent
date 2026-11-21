from datetime import datetime, timedelta

from src.observation.policies import load_graph_settings
from src.utils import print_tips


def wait_node(state):
    settings = load_graph_settings()
    interval = int(settings.get("tick_interval_seconds", 1800))
    now = datetime.now()
    next_tick = now + timedelta(seconds=interval)
    now_iso = now.isoformat(timespec="seconds")
    next_iso = next_tick.isoformat(timespec="seconds")
    print_tips(
        f"Wait / clock tick: current_time={now_iso}; next_tick={next_iso}",
        text_color="blue",
        emoji="hourglass",
    )
    return {
        "current_time": now_iso,
        "next_tick": next_iso,
        "waiting_until": next_iso,
        "route": "wait",
        "final_response": (
            f"Monitoring. Clock updated to {now_iso}. "
            f"Next tick at {next_iso} (no sensors attached)."
        ),
    }
