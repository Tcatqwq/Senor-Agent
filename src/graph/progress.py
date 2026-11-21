from langgraph.config import get_stream_writer


def report_planning_progress(stage, status, **details):
    """Publish a planning event when the node runs inside a streaming graph."""
    try:
        writer = get_stream_writer()
    except RuntimeError:
        return
    writer({"planning_progress": {"stage": stage, "status": status, **details}})
