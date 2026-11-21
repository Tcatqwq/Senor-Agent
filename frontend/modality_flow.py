"""Build time-based modality graphs from executed tools and the Demo 1 trace."""

import json
import re
from collections import defaultdict
from datetime import datetime

from frontend import resources


MEDIA_TIME = re.compile(r"(?:^|/)(\d{8})[\/_]([01]\d|2[0-3])(?=[._]|$)")
MODALITY_ALIASES = {"dep": "depth", "rgb_video": "rgb", "depth_video": "depth"}


def _date(value):
    try:
        return datetime.strptime(str(value), "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def _hours(conditions):
    if isinstance(conditions, str):
        try:
            conditions = json.loads(conditions)
        except json.JSONDecodeError:
            return
    if isinstance(conditions, list) and conditions and not isinstance(conditions[0], (list, tuple)):
        conditions = [conditions]
    for condition in conditions if isinstance(conditions, list) else []:
        if not isinstance(condition, (list, tuple)) or len(condition) < 3:
            continue
        date = _date(condition[0])
        try:
            start, end = int(condition[1]), int(condition[2])
        except (TypeError, ValueError):
            continue
        if date:
            for hour in range(max(0, start), min(23, end) + 1):
                yield date, hour


def _modality(key):
    return MODALITY_ALIASES.get(key, key.removesuffix("_video"))


def _media(output):
    media = defaultdict(dict)
    for key, paths in output.items():
        if key != "audio" and not key.endswith("_video"):
            continue
        if isinstance(paths, str):
            paths = [paths]
        for path in paths if isinstance(paths, list) else []:
            if not isinstance(path, str) or not path or path.startswith("<GENERATED>"):
                continue
            match = MEDIA_TIME.search(path.replace("\\", "/"))
            if match and (date := _date(match[1])):
                media[_modality(key)].setdefault((date, int(match[2])), path)
            else:
                media[_modality(key)].setdefault((None, None), path)
    return media


def _spans(hours):
    """Group real, consecutive clock hours. Keep unknown time as its own node."""
    groups = []
    for date, hour in sorted(set(hours), key=lambda item: (item[0] or "", item[1] or 0)):
        if date is None:
            groups.append((None, None, None))
        elif groups and groups[-1][0] == date and groups[-1][2] == hour:
            groups[-1] = (date, groups[-1][1], hour + 1)
        else:
            groups.append((date, hour, hour + 1))
    return groups


def _node(node_id, modality, span, status, *, cover=None, tool=None, reason=None):
    date, start, end = span
    return {
        "id": node_id, "modality": modality, "date": date, "start": start, "end": end,
        "status": status, "cover": cover, "tool": tool, "reason": reason,
    }


def _overlaps(left, right):
    if left["date"] is None or right["date"] is None:
        return True
    return (left["date"] == right["date"] and left["start"] < right["end"]
            and right["start"] < left["end"])


def run_flow(results, virtual=False, pending=None):
    nodes = []
    edges = []
    by_call = {}
    for call_id, packed in (results | (pending or {})).items():
        if int(call_id) < 0:
            continue
        call = packed["tool_calling_information"]
        result = packed.get("tool_calling_result") or {}
        preview_status = packed.get("preview_status")
        tool = call.get("tool_name", "")
        args = call.get("args") or {}
        output_media = _media(result.get("output") or {})
        input_media = _media(args)
        dependencies = [str(dep) for dep in call.get("dep", []) if int(dep) >= 0]
        parents = [parent for dep in dependencies for parent in by_call.get(dep, [])]
        suffix = _modality(tool.rsplit("-", 1)[-1])
        query = tool.startswith(("query-video-", "query-audio"))
        media_keys = [key for key in args if key.endswith("_video") or key == "audio"]
        modalities = set(output_media) | {_modality(key) for key in media_keys}
        if query:
            modalities.add("audio" if tool.startswith("query-audio") else suffix)
        elif suffix in {parent["modality"] for parent in parents}:
            modalities.add(suffix)

        call_nodes = []
        dep_results = [results.get(dep, results.get(int(dep), {})) for dep in dependencies]
        blocked = any(item.get("tool_calling_result", {}).get("execute_next") is False for item in dep_results)
        for modality in sorted(modalities):
            files = output_media.get(modality, {})
            by_status = defaultdict(set)
            if query and not blocked and not preview_status:
                requested = set(_hours(args.get("text")))
                by_status["available"].update(files)
                # Unrecognized file names cannot establish which requested hours were missing.
                if (None, None) not in files:
                    by_status["missing"].update(requested - files.keys())
                if not files and not requested:
                    by_status["missing" if result.get("execute_next") is False else "called"].add((None, None))
            else:
                hours = (set(_hours(args.get("text"))) if query and preview_status
                         else set(input_media.get(modality, {})) or set(files))
                if not hours:
                    for parent in parents:
                        if parent["modality"] != modality or not parent["date"]:
                            continue
                        if blocked or parent["status"] in {"available", "called"}:
                            hours.update((parent["date"], hour) for hour in range(parent["start"], parent["end"]))
                status = preview_status or (
                    "skipped" if blocked else "invalid" if result.get("execute_next") is False else "called"
                )
                by_status[status].update(hours or {(None, None)})

            for status, hours in by_status.items():
                for span in _spans(hours):
                    date, start, _ = span
                    cover = files.get((date, start)) if query and not virtual else None
                    if cover and ("://" in cover or modality == "audio"):
                        cover = None
                    node = _node(f"{call_id}-{len(call_nodes)}", modality, span, status,
                                 cover=cover, tool=f"#{call_id} {tool}", reason=packed.get("preview_reason"))
                    call_nodes.append(node)
                    for parent in parents:
                        if (blocked or parent["status"] != "missing") and _overlaps(parent, node):
                            edges.append({"from": parent["id"], "to": node["id"], "kind": "dependency"})
        by_call[str(call_id)] = call_nodes
        nodes.extend(call_nodes)
    return {"nodes": nodes, "edges": edges, "virtual": virtual}


def _decision_signature(decision):
    return tuple(
        (attempt["modality"], attempt["status"], attempt.get("error"),
         tuple((call["tool"], call["status"]) for call in attempt["calls"]))
        for attempt in decision["attempts"]
    )


def adaptive_flow(decisions):
    spans = []
    for decision in decisions:
        if spans:
            last = spans[-1][-1]
            if (decision["date"] == last["date"] and decision["hour"] == last["hour"] + 1
                    and _decision_signature(decision) == _decision_signature(last)):
                spans[-1].append(decision)
                continue
        spans.append([decision])

    nodes = []
    edges = []
    previous = None
    for span in spans:
        start, end = span[0]["hour"], span[-1]["hour"] + 1
        date = _date(span[0]["date"])
        last_attempt = None
        selected = None
        for index, attempt in enumerate(span[0]["attempts"]):
            modality, status = attempt["modality"], attempt["status"]
            reason = attempt.get("error")
            if status == "quality":
                scores = [item["attempts"][index].get("score") for item in span]
                measured = [score for score in scores if score is not None]
                metric = "平均亮度" if modality == "rgb" else "噪声标准差"
                condition = f"需 {attempt['operator']} {attempt['threshold']}"
                if not measured:
                    reason = f"无法从视频取得{metric}（{condition}）。"
                else:
                    limits = f"{min(measured):.4g}"
                    if min(measured) != max(measured):
                        limits += f"–{max(measured):.4g}"
                    reason = f"{metric} {limits}（{condition}）未通过质量筛选。"
            node = _node(f"{date}-{start}-{modality}", modality, (date, start, end), status,
                         cover=attempt.get("cover"), reason=reason)
            node["calls"] = attempt["calls"]
            nodes.append(node)
            if last_attempt:
                edges.append({"from": last_attempt["id"], "to": node["id"], "kind": "fallback"})
            last_attempt = node
            if status == "selected":
                selected = node

        if selected and previous and previous["date"] == date and previous["end"] == start:
            kind = "continuity" if previous["modality"] == selected["modality"] else "change"
            edges.append({"from": previous["id"], "to": selected["id"], "kind": kind})
        previous = selected
    return {"nodes": nodes, "edges": edges, "adaptive": True}


# From Tasksense/slide/src/log_demo1.txt, Candidate 1-1 = RGB, Candidate 1-2 = Depth.
# Each entry lists the selected path and the logged rejections for that interval.
DEMO_INTERVALS = (
    (0, 1, "depth", (("rgb", "quality"),)),
    (1, 2, "depth", (("rgb", "invalid"),)),
    (2, 8, "rgb", ()),
    (8, 9, "rgb", (("depth", "quality"),)),
    (9, 16, "rgb", ()),
    (16, 17, None, (("rgb", "missing"), ("depth", "missing"))),
    (17, 18, "depth", (("rgb", "quality"),)),
    (18, 19, "rgb", ()),
    (19, 20, "depth", (("rgb", "quality"),)),
    (20, 21, "rgb", ()),
    (21, 23, "depth", (("rgb", "quality"),)),
    (23, 24, None, (("rgb", "quality"), ("depth", "missing"))),
)


def demo_flow():
    date = "2023-11-16"
    candidates = {"rgb": "1-1", "depth": "1-2"}
    rejection_reasons = {
        "quality": "质量筛选未通过。",
        "invalid": "完成推理后未得到有效结果。",
        "missing": "查询未返回媒体。",
    }
    nodes = []
    edges = []
    previous = None
    for start, end, chosen, rejected in DEMO_INTERVALS:
        span = date, start, end
        selected = None
        if chosen:
            selected = _node(f"{start}-{chosen}", chosen, span, "selected",
                             cover=resources.sensor_cover_path(chosen, date, start),
                             reason=f"候选 {candidates[chosen]} 执行成功，依次调用视频查询、目标检测和行为分类。")
            nodes.append(selected)
            if previous:
                kind = "continuity" if previous["modality"] == chosen else "change"
                edges.append({"from": previous["id"], "to": selected["id"], "kind": kind})
        for modality, status in rejected:
            cover = resources.sensor_cover_path(modality, date, start) if status != "missing" else None
            rejected_node = _node(f"{start}-{modality}", modality, span, status, cover=cover,
                                  reason=f"候选 {candidates[modality]}：{rejection_reasons[status]}")
            nodes.append(rejected_node)
            if selected and modality == "rgb" and chosen == "depth":
                edges.append({"from": rejected_node["id"], "to": selected["id"], "kind": "fallback"})
        previous = selected
    return {"nodes": nodes, "edges": edges}
