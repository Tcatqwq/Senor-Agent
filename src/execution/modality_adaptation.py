"""Per-hour RGB/Depth selection for the video pipelines supported by Demo 1."""

import copy
import fcntl
import json
from datetime import datetime
from itertools import count
from pathlib import Path

import cv2
import numpy as np

from src.execution.batch_executor import ensure_execution_cache, prepare_and_run_tool
from src.responding.formatter import find_interupt, format_results_to_dict
from src.utils import load_cache_with_lock, print_tips


STAGES = ("query-video-data", "object-detection", "human-activity-classification")
THRESHOLDS = {"rgb": (">", 0.2), "depth": ("<", 80)}


def _match_pipeline(plan):
    if len(plan) not in (2, 3):
        return None
    by_name = {step["tool_name"]: step for step in plan}
    stages = STAGES[:len(plan)]
    for modality in THRESHOLDS:
        steps = [by_name.get(f"{stage}-{modality}") for stage in stages]
        if not all(steps) or steps[0].get("dep") != [-1]:
            continue
        root_args = steps[0].get("args") or {}
        if root_args.keys() != {"text"}:
            return None
        if any(step.get("dep") != [parent["id"]] for parent, step in zip(steps, steps[1:])):
            continue
        media_key = f"{modality}_video"
        for step in steps[1:]:
            args = step.get("args") or {}
            value = args.get(media_key)
            if args.keys() - {media_key} or (value is not None and not str(value).startswith("<GENERATED>")):
                return None
        return modality, stages, root_args["text"]
    return None


def _requested_hours(conditions):
    if not isinstance(conditions, list) or not conditions:
        return []
    hours = set()
    for item in conditions:
        if not isinstance(item, (list, tuple)) or len(item) != 3:
            return []
        date, start, end = item
        if not all(isinstance(value, int) for value in item) or not 0 <= start <= end <= 24:
            return []
        try:
            datetime.strptime(str(date), "%Y%m%d")
        except ValueError:
            return []
        hours.update((date, hour) for hour in range(start, min(end, 23) + 1))
    return sorted(hours)


def _measure_video(modality, path):
    capture = cv2.VideoCapture(path)
    values = []
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if modality == "rgb":
                blue, green, red = frame.mean(axis=(0, 1))
                values.append((0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255)
            else:
                # The prototype's threshold uses unsigned residuals from 8-bit frames.
                residual = frame - cv2.GaussianBlur(frame, (5, 5), 0)
                if np.any(residual):
                    values.append(residual.std())
    finally:
        capture.release()
    return float(np.mean(values)) if values else None


class QualityMeasurements:
    def __init__(self, cache_path):
        self.path = Path(cache_path) if cache_path else None
        self.new = {}
        try:
            self.cache = load_cache_with_lock(self.path) if self.path else {}
        except (OSError, json.JSONDecodeError):
            self.cache = {}

    def score(self, modality, path):
        key = f"{modality}-video-quality-assessment"
        cached = self.cache.get(key, {})
        if path not in cached:
            measured = _measure_video(modality, path)
            self.new.setdefault(key, {})[path] = measured
            self.cache.setdefault(key, {})[path] = measured
        return self.cache[key][path]

    def save(self):
        if not self.path or not self.new:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            handle.seek(0)
            try:
                latest = json.load(handle)
            except json.JSONDecodeError:
                latest = {}
            for key, scores in self.new.items():
                latest.setdefault(key, {}).update(scores)
            handle.seek(0)
            handle.truncate()
            json.dump(latest, handle, indent=4)
            handle.flush()


def _attempt(attempt, stages, date, hour, ids, quality, publish):
    modality = attempt["modality"]
    media_key = f"{modality}_video"
    results = {}
    parent = -1
    status = "selected"
    for stage in stages:
        tool = f"{stage}-{modality}"
        call = {"tool": tool, "status": "running"}
        attempt["calls"].append(call)
        publish()
        args = {"text": [[date, hour, hour]]} if parent < 0 else {media_key: f"<GENERATED>-{parent}"}
        try:
            tool_id = next(ids)
            prepare_and_run_tool({"id": tool_id, "tool_name": tool, "dep": [parent], "args": args}, results)
            result = results[tool_id]["tool_calling_result"]
            call["status"] = "called"
            if parent < 0:
                paths = result.get("output", {}).get(media_key) or []
                call["status"] = "available" if paths else "missing"
                status = "selected" if paths else "missing"
                if paths:
                    attempt["cover"] = paths[0]
                    attempt["status"] = "checking"
                    publish()
                    score = quality.score(modality, paths[0])
                    operator, threshold = THRESHOLDS[modality]
                    attempt.update(score=score, operator=operator, threshold=threshold)
                    passed = score is not None and (score > threshold if operator == ">" else score < threshold)
                    if not passed:
                        status = "quality"
            elif result.get("execute_next") is False:
                call["status"] = status = "invalid"
        except (OSError, RuntimeError, ValueError, TypeError, cv2.error) as exc:
            attempt["error"] = str(exc)
            call["status"] = status = "error"
        if status != "selected":
            break
        parent = tool_id
        attempt["status"] = "running"
        publish()

    if status == "selected":
        try:
            formatted = format_results_to_dict(copy.deepcopy(results))
            if not any(formatted.values()) or find_interupt(formatted):
                status = "invalid"
        except (KeyError, TypeError, ValueError) as exc:
            attempt["error"] = f"无法格式化候选推理结果：{exc}"
            status = "error"
    attempt["status"] = status
    return results if status == "selected" else {}


def execute_adaptive_plan(plan, settings, report=None):
    """Return selected tool results and the actual candidate decisions for each hour."""
    pipeline = _match_pipeline(plan)
    if pipeline is None:
        return None
    preferred, stages, conditions = pipeline
    hours = _requested_hours(conditions)
    if not hours:
        return None

    ensure_execution_cache()
    fallback = "depth" if preferred == "rgb" else "rgb"
    quality = QualityMeasurements(settings.get("assessment_cache_path") if settings.get("use_cache") else None)
    results = {}
    decisions = []
    ids = count()
    try:
        for date, hour in hours:
            decision = {"date": date, "hour": hour, "attempts": []}
            decisions.append(decision)

            def publish():
                if report is not None:
                    report("decision", copy.deepcopy(decision))

            for modality in (preferred, fallback):
                attempt = {"modality": modality, "status": "running", "calls": []}
                decision["attempts"].append(attempt)
                selected = _attempt(attempt, stages, date, hour, ids, quality, publish)
                status = attempt["status"]
                reason = attempt.get("error")
                if status == "quality":
                    reason = f"score={attempt['score']}, required {attempt['operator']} {attempt['threshold']}"
                suffix = f" ({reason})" if reason else ""
                print_tips(f"{date} {hour:02d}:00 {modality}: {status}{suffix}", border=False)
                publish()
                if selected:
                    results.update(selected)
                    break
    finally:
        try:
            quality.save()
        except OSError as exc:
            print_tips(f"Could not save video quality measurements: {exc}", border=False)
    return results, decisions
