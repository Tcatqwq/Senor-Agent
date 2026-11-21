"""LLM-backed tool simulation with date-range / event post-processing."""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime
from typing import Any

from src.llm_virtual_env.cache import get_cached, put_cached
from src.llm_virtual_env.catalog import effective_har_labels, load_vocabulary_set
from src.llm_virtual_env.chat import virtual_chat_completion
from src.llm_virtual_env.config import load_virtual_env
from src.llm_virtual_env.schemas import FormatError, coerce_envelope, extract_json
from src.utils import generate_a_sign, print_tips

VIRTUAL_URI_RE = re.compile(
    r"^virtual://(?P<modality>[^/]+)/(?P<date>\d{8})/(?P<hour>\d{2})"
)
HOUR_IN_PATH_RE = re.compile(r"(?P<date>\d{8}).*?(?P<hour>\d{2})")

SIM_SYSTEM = """You simulate Senor Agent tool execution. Do not read files or run models.
Return ONE JSON object only (no markdown) with:
{
  "tool_name": "...",
  "output": { ... modality lists and/or "label" ... },
  "saving": false,
  "execute_next": true
}

Rules:
- query-*-data tools: output.<modality> is a list of virtual URIs: virtual://<modality>/YYYYMMDD/HH.avi
  Use one URI per hour in the queried range that intersects data_date_range. If no overlap, return empty lists and execute_next=false.
- object-detection-*: for each input video emit a cropped URI virtual://<modality>/YYYYMMDD/HH.person.0.avi and label "person". Empty input => empty output, execute_next=false.
- human-activity-classification-*: output.label is a list, one label per input video. Labels MUST be chosen from har_labels.
  Follow environment_info and events exactly. If an event says a phone call happens at 2023-11-16 01:00-02:00, ONLY those hours may use that HAR label; other hours must use a different label (e.g. Standing still).
- sensor tools: output.status / label success-like values, execute_next=true.
- Copy input virtual URIs forward when the tool consumes them.
"""


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower().replace("_", " "))


def _date_to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return int(value)
    text = str(value).strip()
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 8:
        return int(digits[:8])
    return None


def _parse_date_range(settings: dict) -> tuple[int | None, int | None]:
    span = settings.get("date_range") or {}
    start = _date_to_int(span.get("start"))
    end = _date_to_int(span.get("end")) or start
    if start and end and end < start:
        start, end = end, start
    return start, end


def _in_date_range(date_int: int | None, settings: dict) -> bool:
    start, end = _parse_date_range(settings)
    if date_int is None:
        return False
    if start is None and end is None:
        return True
    if start is None:
        return date_int <= end
    if end is None:
        return date_int >= start
    return start <= date_int <= end


def parse_query_conditions(text) -> list[list[int]]:
    if text is None:
        return []
    parsed = text
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except json.JSONDecodeError:
            return []
    if not isinstance(parsed, list) or not parsed:
        return []
    if parsed and not isinstance(parsed[0], (list, tuple)):
        parsed = [parsed]
    out = []
    for item in parsed:
        if isinstance(item, (list, tuple)) and len(item) >= 3:
            try:
                out.append([int(item[0]), int(item[1]), int(item[2])])
            except (TypeError, ValueError):
                continue
    return out


def _hours_for_condition(date: int, start_hour: int, end_hour: int, settings: dict) -> list[int]:
    if not _in_date_range(date, settings):
        return []
    lo = max(0, min(int(start_hour), int(end_hour)))
    hi = min(23, max(int(start_hour), int(end_hour)))
    return list(range(lo, hi + 1))


def parse_virtual_ref(path: str) -> tuple[str | None, int | None, int | None]:
    text = str(path or "")
    match = VIRTUAL_URI_RE.match(text)
    if match:
        return match.group("modality"), int(match.group("date")), int(match.group("hour"))
    match = HOUR_IN_PATH_RE.search(text.replace("\\", "/"))
    if match:
        return None, int(match.group("date")), int(match.group("hour"))
    return None, None, None


def _parse_event_timestamp(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    for fmt in (
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H",
        "%Y-%m-%d %H",
        "%Y-%m-%d",
        "%Y%m%d%H",
        "%Y%m%d",
    ):
        try:
            parsed = datetime.strptime(text.replace("Z", ""), fmt)
            return parsed
        except ValueError:
            continue
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 10:
        try:
            return datetime.strptime(digits[:10], "%Y%m%d%H")
        except ValueError:
            return None
    if len(digits) >= 8:
        try:
            return datetime.strptime(digits[:8], "%Y%m%d")
        except ValueError:
            return None
    return None


def _event_window(event: dict) -> tuple[int, int, int, int] | None:
    if event.get("date") is not None and event.get("start_hour") is not None:
        date = _date_to_int(event.get("date"))
        if date is None:
            return None
        start_hour = int(event.get("start_hour"))
        end_hour = int(event.get("end_hour", start_hour))
        return date, start_hour, date, end_hour
    start = _parse_event_timestamp(event.get("start"))
    end = _parse_event_timestamp(event.get("end")) or start
    if start is None:
        return None
    if end is None:
        end = start
    return (
        int(start.strftime("%Y%m%d")),
        start.hour,
        int(end.strftime("%Y%m%d")),
        end.hour,
    )


def _hour_in_event(date: int, hour: int, event: dict) -> bool:
    window = _event_window(event)
    if window is None:
        return False
    start_date, start_hour, end_date, end_hour = window
    start_key = start_date * 100 + start_hour
    end_key = end_date * 100 + end_hour
    key = date * 100 + hour
    return start_key <= key <= end_key


def _event_matches_modality(event: dict, modality: str | None) -> bool:
    wanted = event.get("modality") if event.get("modality") not in (None, "") else event.get("modalities")
    if not wanted:
        return True
    if isinstance(wanted, list):
        names = {str(item).strip() for item in wanted if str(item).strip()}
    else:
        names = {str(wanted).strip()}
    if not names:
        return True
    if not modality:
        return True
    target = str(modality).strip()
    aliases = {target}
    if target.endswith("_video"):
        aliases.add(target[: -len("_video")])
    else:
        aliases.add(target + "_video")
    return bool(names & aliases)


def _label_for_hour(date: int | None, hour: int | None, events: list, fallback: str) -> str | None:
    if date is None or hour is None:
        return None
    for event in events:
        if _hour_in_event(date, hour, event):
            label = str(event.get("har_label") or event.get("label") or "").strip()
            if label:
                return label
    return None


def _exclusive_event_labels(events: list) -> set[str]:
    labels = set()
    for event in events:
        label = str(event.get("har_label") or event.get("label") or "").strip()
        if label:
            labels.add(_norm(label))
    return labels


_NON_MEDIA_KEYS = {"text", "pre_sign", "post_sign", "label", "status"}
_BASE_MEDIA_KEYS = ("rgb_video", "depth_video", "thermal_video", "audio")


def _is_media_key(key: str) -> bool:
    name = str(key or "")
    if name in _NON_MEDIA_KEYS:
        return False
    return name.endswith("_video") or name == "audio"


def _configured_media_keys() -> list[str]:
    keys = list(_BASE_MEDIA_KEYS)
    try:
        extra = load_virtual_env().get("modalities") or []
    except Exception:
        extra = []
    for item in extra:
        name = str(item or "").strip()
        if name and name not in _NON_MEDIA_KEYS and name not in keys:
            keys.append(name)
    return keys


def _iter_media_keys(output: dict | None = None) -> list[str]:
    keys = list(_configured_media_keys())
    for key in (output or {}):
        if _is_media_key(str(key)) and key not in keys:
            keys.append(key)
    return keys


def _modality_from_tool(tool_name: str, vocab: dict | None = None) -> str:
    if vocab:
        for entries in vocab.values():
            for tool in entries or []:
                if tool.get("tool_name") == tool_name and tool.get("modality"):
                    return str(tool["modality"])
    name = (tool_name or "").lower()
    if "thermal" in name:
        return "thermal_video"
    if "depth" in name:
        return "depth_video"
    if "audio" in name:
        return "audio"
    if "text" in name:
        return "text"
    suffix = name.rsplit("-", 1)[-1]
    if suffix and suffix not in {"data", "classification", "detection", "rgb"}:
        if suffix in {"audio", "text"}:
            return suffix
        return f"{suffix}_video"
    return "rgb_video"


def _as_video_list(value) -> list[str]:
    if isinstance(value, list) and value:
        return [str(item) for item in value]
    if isinstance(value, str) and value:
        return [value]
    return []


def _input_videos(input_to_tool: dict) -> list[str]:
    output = input_to_tool.get("output") or {}
    for key in _iter_media_keys(output):
        videos = _as_video_list(output.get(key))
        if videos:
            return videos
    return []


def _classify(tool_name: str) -> str:
    name = tool_name.lower()
    if name.startswith("query-") or "query-video" in name or "query-audio" in name:
        return "query"
    if "human-activity" in name or "classification" in name or name.endswith("-har"):
        return "har"
    if "object-detection" in name:
        return "od"
    if name in {"take-picture", "send-notification", "record-microphone"}:
        return "sensor"
    return "generic"


CACHE_FMT = 3


def _cache_key(tool_name: str, input_to_tool: dict) -> str:
    output = input_to_tool.get("output") or {}
    payload = {
        "fmt": CACHE_FMT,
        "tool_name": tool_name,
        "text": output.get("text"),
    }
    for key in _iter_media_keys(output):
        payload[key] = output.get(key)
    return json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)


def _parent_signs(input_to_tool: dict) -> list:
    signs = (input_to_tool.get("output") or {}).get("post_sign") or []
    if not isinstance(signs, list):
        return [signs] if signs not in (None, "") else []
    return list(signs)


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _allowed_payload_keys(kind: str, tool_name: str, vocab: dict | None) -> list[str]:
    if kind == "query":
        return [_modality_from_tool(tool_name, vocab)]
    if kind == "od":
        return [_modality_from_tool(tool_name, vocab), "label"]
    if kind in {"har", "sensor"}:
        return ["label"]
    keys = [key for key in _iter_media_keys() if key not in {"label"}]
    if "label" not in keys:
        keys.append("label")
    return keys


def _align_pre_signs(pre_signs: list, length: int) -> list:
    if length <= 0:
        return []
    if not pre_signs:
        return [generate_a_sign() for _ in range(length)]
    if len(pre_signs) == length:
        return list(pre_signs)
    if len(pre_signs) == 1:
        return list(pre_signs) * length
    aligned = []
    last = pre_signs[-1]
    for index in range(length):
        aligned.append(pre_signs[index] if index < len(pre_signs) else last)
    return aligned


def _finalize_envelope(
    kind: str,
    tool_name: str,
    envelope: dict,
    input_to_tool: dict,
    vocab: dict | None = None,
    pre_signs: list | None = None,
) -> dict:
    """Keep only formatter-safe lists: payload fields + aligned pre_sign/post_sign."""
    allowed = _allowed_payload_keys(kind, tool_name, vocab)
    raw = dict(envelope.get("output") or {})
    output = {}
    length = 0
    for key in allowed:
        if key not in raw:
            continue
        value = _as_list(raw[key])
        output[key] = value
        length = max(length, len(value))
    for key in allowed:
        if key not in output:
            output[key] = []
            continue
        value = output[key]
        if len(value) < length:
            if key == "label":
                fill = value[-1] if value else "Standing still"
                output[key] = value + [fill] * (length - len(value))
            else:
                output[key] = value + [value[-1] if value else ""] * (length - len(value))
        elif len(value) > length:
            output[key] = value[:length]
    parents = pre_signs if pre_signs is not None else _parent_signs(input_to_tool)
    output["pre_sign"] = _align_pre_signs(parents, length)
    output["post_sign"] = [generate_a_sign() for _ in range(length)]
    envelope["output"] = output
    envelope.setdefault("saving", False)
    if length == 0:
        envelope["execute_next"] = False
    return envelope


def _envelope_lists_aligned(envelope: dict) -> bool:
    output = envelope.get("output") or {}
    if "pre_sign" not in output or "post_sign" not in output:
        return False
    lists = [value for value in output.values() if isinstance(value, list)]
    if not lists:
        return False
    size = len(lists[0])
    return all(len(value) == size for value in lists)


def _rebind_cached_pre_signs(envelope: dict, input_to_tool: dict) -> dict:
    """Keep cached payload and post_sign; attach this run's parent signs as pre_sign.

    Initial-input signs are generated per run. Cache keys ignore them, so a hit would
    otherwise leave stale pre_sign that the formatter cannot match.
    Downstream tools still cache against this tool's post_sign, so those must not change.
    """
    rebound = copy.deepcopy(envelope)
    output = dict(rebound.get("output") or {})
    post_sign = list(output.get("post_sign") or [])
    length = len(post_sign)
    output["pre_sign"] = _align_pre_signs(_parent_signs(input_to_tool), length)
    output["post_sign"] = post_sign
    rebound["output"] = output
    return rebound


def _query_items(tool_name: str, input_to_tool: dict, settings: dict, vocab: dict | None) -> tuple[str, list[str], list]:
    """Expand each query condition into per-hour URIs, repeating that condition's parent sign."""
    conditions = parse_query_conditions((input_to_tool.get("output") or {}).get("text"))
    modality = _modality_from_tool(tool_name, vocab)
    parent_signs = _parent_signs(input_to_tool)
    videos = []
    pre_signs = []
    for cond_idx, (date, start_hour, end_hour) in enumerate(conditions):
        if parent_signs:
            parent = parent_signs[cond_idx] if cond_idx < len(parent_signs) else parent_signs[0]
        else:
            parent = generate_a_sign()
        for hour in _hours_for_condition(date, start_hour, end_hour, settings):
            videos.append(f"virtual://{modality}/{date:08d}/{hour:02d}.avi")
            pre_signs.append(parent)
    return modality, videos, pre_signs


def _synthesize_query_videos(tool_name: str, conditions: list[list[int]], settings: dict, vocab: dict | None) -> list[str]:
    modality = _modality_from_tool(tool_name, vocab)
    videos = []
    for date, start_hour, end_hour in conditions:
        for hour in _hours_for_condition(date, start_hour, end_hour, settings):
            videos.append(f"virtual://{modality}/{date:08d}/{hour:02d}.avi")
    return videos


def _apply_query_constraints(tool_name: str, envelope: dict, input_to_tool: dict, settings: dict, vocab: dict | None) -> tuple[dict, list]:
    modality, videos, pre_signs = _query_items(tool_name, input_to_tool, settings, vocab)
    envelope["output"] = {modality: videos}
    envelope["execute_next"] = bool(videos)
    return envelope, pre_signs


def _apply_od_constraints(tool_name: str, envelope: dict, input_to_tool: dict, vocab: dict | None) -> tuple[dict, list]:
    videos = _input_videos(input_to_tool)
    modality = _modality_from_tool(tool_name, vocab)
    parent_signs = _parent_signs(input_to_tool)
    if not videos:
        envelope["output"] = {modality: [], "label": []}
        envelope["execute_next"] = False
        return envelope, []
    crops = []
    labels = []
    pre_signs = []
    for index, path in enumerate(videos):
        modality_hint, date, hour = parse_virtual_ref(path)
        used_mod = modality_hint or modality
        if date is None:
            crops.append(f"virtual://{used_mod}/crop/{abs(hash(path)) % 10**8}.avi")
        else:
            crops.append(f"virtual://{used_mod}/{date:08d}/{hour:02d}.person.0.avi")
        labels.append("person")
        if parent_signs:
            pre_signs.append(parent_signs[index] if index < len(parent_signs) else parent_signs[-1])
        else:
            pre_signs.append(generate_a_sign())
    envelope["output"] = {modality: crops, "label": labels}
    envelope["execute_next"] = True
    return envelope, pre_signs


def _pick_fallback_label(har_labels: list[str], exclusive: set[str]) -> str:
    for label in har_labels:
        if _norm(label) not in exclusive:
            return label
    for candidate in ("Standing still", "Sitting still", "Walking"):
        if _norm(candidate) not in exclusive:
            return candidate
    return har_labels[0] if har_labels else "Standing still"


def _canonical_label(label: str, har_labels: list[str]) -> str:
    target = _norm(label)
    for item in har_labels:
        if _norm(item) == target:
            return item
    return label


def _apply_har_constraints(envelope: dict, input_to_tool: dict, settings: dict) -> tuple[dict, list]:
    videos = _input_videos(input_to_tool)
    tool_name = str(envelope.get("tool_name") or "")
    modality = _modality_from_tool(tool_name, _vocab_or_none())
    har_labels = effective_har_labels(modality, settings)
    events = [
        event
        for event in (settings.get("events") or [])
        if isinstance(event, dict) and _event_matches_modality(event, modality)
    ]
    exclusive = _exclusive_event_labels(events)
    fallback = _pick_fallback_label(har_labels, exclusive)
    raw_labels = envelope.get("output", {}).get("label")
    if not isinstance(raw_labels, list):
        raw_labels = []
    parent_signs = _parent_signs(input_to_tool)
    if not videos:
        envelope["output"] = {"label": []}
        envelope["execute_next"] = False
        return envelope, []
    labels = []
    pre_signs = []
    for index, path in enumerate(videos):
        _, date, hour = parse_virtual_ref(path)
        current = raw_labels[index] if index < len(raw_labels) else (raw_labels[0] if raw_labels else fallback)
        if isinstance(current, list):
            current = current[0] if current else fallback
        current = str(current)
        forced = _label_for_hour(date, hour, events, fallback)
        if forced:
            labels.append(forced)
        else:
            if _norm(current) in exclusive:
                current = fallback
            if har_labels and _norm(current) not in {_norm(item) for item in har_labels}:
                current = fallback
            labels.append(_canonical_label(current, har_labels))
        if parent_signs:
            pre_signs.append(parent_signs[index] if index < len(parent_signs) else parent_signs[-1])
        else:
            pre_signs.append(generate_a_sign())
    envelope["output"] = {"label": labels}
    envelope["execute_next"] = True
    return envelope, pre_signs


def _apply_sensor_constraints(envelope: dict, input_to_tool: dict) -> tuple[dict, list]:
    envelope["output"] = {"label": ["success"]}
    envelope["execute_next"] = True
    envelope["saving"] = False
    return envelope, _parent_signs(input_to_tool)


def _known_real_tools() -> set[str]:
    try:
        from src.execution.toolbox import tool_name_tool_implementation_map

        return set(tool_name_tool_implementation_map) | {
            "take-picture",
            "send-notification",
            "record-microphone",
        }
    except Exception:
        return {
            "query-video-data-rgb",
            "query-video-data-depth",
            "object-detection-rgb",
            "object-detection-depth",
            "human-activity-classification-rgb",
            "human-activity-classification-depth",
            "take-picture",
            "send-notification",
            "record-microphone",
        }


def _vocab_or_none() -> dict | None:
    try:
        return load_vocabulary_set()
    except Exception:
        return None


def _simulate_with_llm(tool_call: dict, input_to_tool: dict, settings: dict) -> dict:
    tool_name = tool_call["tool_name"]
    vocab = _vocab_or_none()
    modality = _modality_from_tool(tool_name, vocab)
    events = [
        event
        for event in (settings.get("events") or [])
        if isinstance(event, dict) and _event_matches_modality(event, modality)
    ]
    context = {
        "tool_call": tool_call,
        "input": input_to_tool,
        "date_range": settings.get("date_range"),
        "modalities": settings.get("modalities"),
        "modality": modality,
        "har_labels": effective_har_labels(modality, settings),
        "har_labels_by_modality": settings.get("har_labels_by_modality") or {},
        "environment_info": settings.get("environment_info") or "",
        "events": events,
        "virtual_modality": bool(settings.get("virtual_modality")),
    }
    raw = virtual_chat_completion(
        [
            {"role": "system", "content": SIM_SYSTEM},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False, default=str)},
        ]
    )
    parsed = extract_json(raw)
    return coerce_envelope(tool_call["tool_name"], parsed)


def _fallback_envelope(tool_name: str, kind: str) -> dict:
    output = {"label": []}
    if kind == "sensor":
        output = {"label": ["success"]}
    return {
        "tool_name": tool_name,
        "output": output,
        "saving": False,
        "execute_next": kind != "query",
    }


def simulate_tool(tool_call: dict, input_to_tool: dict) -> dict:
    settings = load_virtual_env()
    tool_name = tool_call["tool_name"]
    if not settings.get("virtual_modality") and tool_name not in _known_real_tools():
        raise ValueError(
            f"虚拟模态已关闭，不能模拟未知工具 {tool_name}。请使用真实 tool list。"
        )
    if not input_to_tool.get("execute_next", True):
        kind = _classify(tool_name)
        empty = _fallback_envelope(tool_name, kind)
        empty["execute_next"] = False
        if kind == "query":
            empty["output"] = {_modality_from_tool(tool_name, _vocab_or_none()): []}
        elif kind == "od":
            empty["output"] = {_modality_from_tool(tool_name, _vocab_or_none()): [], "label": []}
        else:
            empty["output"] = {"label": []}
        return _finalize_envelope(kind, tool_name, empty, input_to_tool, _vocab_or_none(), pre_signs=[])

    key = _cache_key(tool_name, input_to_tool)
    use_cache = bool(settings.get("use_cache", True))
    if use_cache:
        cached = get_cached(tool_name, key)
        if isinstance(cached, dict) and _envelope_lists_aligned(cached):
            print_tips(
                f"LLM virtual cache hit: {tool_name}",
                emoji="zap",
                text_color="cyan",
                border=False,
            )
            return _rebind_cached_pre_signs(cached, input_to_tool)

    print_tips(
        f"LLM virtual simulate: {tool_name}",
        emoji="crystal_ball",
        text_color="cyan",
        border=False,
    )
    kind = _classify(tool_name)
    vocab = _vocab_or_none()
    try:
        envelope = _simulate_with_llm(tool_call, input_to_tool, settings)
    except (FormatError, ValueError) as exc:
        print_tips(
            f"LLM virtual simulate fallback ({exc})",
            emoji="warning",
            text_color="yellow",
            border=False,
        )
        envelope = _fallback_envelope(tool_name, kind)

    envelope["tool_name"] = tool_name
    pre_signs = None
    if kind == "query":
        envelope, pre_signs = _apply_query_constraints(tool_name, envelope, input_to_tool, settings, vocab)
    elif kind == "od":
        envelope, pre_signs = _apply_od_constraints(tool_name, envelope, input_to_tool, vocab)
    elif kind == "har":
        envelope, pre_signs = _apply_har_constraints(envelope, input_to_tool, settings)
    elif kind == "sensor":
        envelope, pre_signs = _apply_sensor_constraints(envelope, input_to_tool)

    envelope["tool_name"] = tool_name
    envelope["saving"] = False
    envelope = _finalize_envelope(kind, tool_name, envelope, input_to_tool, vocab, pre_signs=pre_signs)
    if use_cache:
        put_cached(tool_name, key, envelope)
    return envelope
