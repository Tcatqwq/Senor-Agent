import argparse
import os
import time
from datetime import datetime

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT_DIR)
from src.local_setup import prepare_local_files

os.environ["CONFIG_PATH"] = str(prepare_local_files(ROOT_DIR))
os.environ["TASKSENSE_DISABLE_LLM_VIRTUAL"] = "1"

import torch

try:
    torch.multiprocessing.set_start_method("spawn")
except RuntimeError:
    pass

from src.execution.process_pool import close_pool, warmup_pool
from src.graph.graph import run_agent
from src.observation.policies import load_graph_settings
from src.utils import print_conversation_timing, print_tips, visualize_agent_response


def parse_args():
    parser = argparse.ArgumentParser(description="Senor Agent CLI")
    parser.add_argument(
        "--tick-loop",
        action="store_true",
        help="After a wait route, sleep tick_interval_seconds and re-enter observe.",
    )
    parser.add_argument(
        "--query",
        type=str,
        default=None,
        help="Run a single query and exit (non-interactive).",
    )
    return parser.parse_args()


def _print_query_timing(result, conversation_t0, tick_sleep=0.0, tick_sleep_count=0):
    total = time.perf_counter() - conversation_t0
    timings = dict(result.get("timings") or {})
    counts = dict(result.get("timing_counts") or {})
    stage_seconds = {
        key: value
        for key, value in timings.items()
        if key not in {"graph_wall", "total", "tick_sleep"}
    }
    if tick_sleep > 0 or tick_sleep_count:
        stage_seconds["tick_sleep"] = tick_sleep
        counts["tick_sleep"] = tick_sleep_count or counts.get("tick_sleep") or 1
    token_usage = dict(result.get("token_usage") or {})
    print_conversation_timing(stage_seconds, total, counts, token_usage)
    timings["tick_sleep"] = tick_sleep
    timings["total"] = total
    result["timings"] = timings
    result["timing_counts"] = counts
    result["token_usage"] = token_usage
    return result


def run_query(user_input: str, tick_loop: bool = False):
    conversation_t0 = time.perf_counter()
    tick_sleep = 0.0
    tick_sleep_count = 0
    result = run_agent(user_input, tick_loop=tick_loop, start_at="solvability")
    visualize_agent_response(result.get("final_response") or "")
    print_tips(
        f"route={result.get('route')} current_time={result.get('current_time')}",
        text_color="cyan",
        emoji="eye",
        border=False,
    )

    if not tick_loop:
        return _print_query_timing(result, conversation_t0)

    settings = load_graph_settings()
    interval = int(settings.get("tick_interval_seconds", 1800))
    while result.get("route") == "wait":
        waiting_until = result.get("waiting_until") or result.get("next_tick")
        print_tips(
            f"Tick-loop sleeping {interval}s until {waiting_until}",
            text_color="blue",
            emoji="hourglass",
        )
        sleep_t0 = time.perf_counter()
        time.sleep(interval)
        tick_sleep += time.perf_counter() - sleep_t0
        tick_sleep_count += 1
        result["current_time"] = datetime.now().isoformat(timespec="seconds")
        result = run_agent(
            user_input,
            tick_loop=True,
            start_at="observe",
            state=result,
        )
        visualize_agent_response(result.get("final_response") or "")
    return _print_query_timing(result, conversation_t0, tick_sleep, tick_sleep_count)


def main():
    args = parse_args()
    print_tips("Senor Agent", text_color="green", emoji="smiley")
    print_tips(
        "CLI 已关闭 LLM 虚拟环境：传感器查询与 HAR 等工具走真实路径。"
        "如需虚拟环境，请使用 Web UI（参数设置 → LLM虚拟环境）。",
        text_color="yellow",
        emoji="warning",
        border=False,
    )
    warmup_pool()
    try:
        if args.query:
            run_query(args.query, tick_loop=args.tick_loop)
            return

        while True:
            message = input("[ User ]: ")
            if message.strip().lower() in {"exit", "quit"}:
                break
            run_query(message, tick_loop=args.tick_loop)

        print("CLI stopped.")
    finally:
        close_pool()


if __name__ == "__main__":
    main()
