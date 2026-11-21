"""Isolated OpenAI-compatible client for the virtual-environment LLM."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from src.llm_virtual_env.config import is_placeholder, load_virtual_env
from src.token_usage import record_langchain_usage

DEFAULT_TIMEOUT_SECONDS = 180


def get_virtual_chat_model(temperature=None):
    settings = load_virtual_env()
    api = settings.get("api") or {}
    api_key = api.get("api_key")
    base_url = api.get("base_url")
    model = api.get("model")
    if temperature is None:
        temperature = api.get("temperature", 0)
    if is_placeholder(api_key) or not base_url or not model:
        raise ValueError(
            "LLM虚拟环境 API 未配置。请在设置中填写 llm_virtual_env.api "
            "(api_key, base_url, model)。"
        )
    timeout = api.get("timeout", DEFAULT_TIMEOUT_SECONDS)
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT_SECONDS
    return ChatOpenAI(
        model=str(model),
        api_key=str(api_key),
        base_url=str(base_url).rstrip("/"),
        temperature=temperature,
        timeout=timeout,
        max_retries=0,
        max_tokens=8192,
    )


def virtual_chat_completion(messages, temperature=None) -> str:
    lc_messages = []
    for message in messages:
        role = message.get("role", "user")
        content = message.get("content", "")
        if role == "system":
            lc_messages.append(SystemMessage(content=content))
        elif role == "assistant":
            lc_messages.append(AIMessage(content=content))
        else:
            lc_messages.append(HumanMessage(content=content))
    model = get_virtual_chat_model(temperature=temperature)
    response = model.invoke(lc_messages)
    record_langchain_usage(response)
    return response.content or ""
