import os

import yaml
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from src.token_usage import record_langchain_usage


def load_config():
    config_path = os.environ.get("CONFIG_PATH")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.load(f, Loader=yaml.FullLoader)


def get_chat_model(temperature=None):
    config = load_config()
    openai_cfg = config["api_settings"]["vllm"]
    api_key = openai_cfg.get("api_key")
    base_url = openai_cfg.get("base_url")
    model = openai_cfg.get("model")
    if temperature is None:
        temperature = openai_cfg.get("temperature", config["api_settings"].get("temperature", 0))

    placeholder_values = {None, "", "#", "EMPTY", "PUT_YOUR_API_KEY_HERE"}
    if api_key in placeholder_values or not base_url or not model:
        raise ValueError(
            "OpenAI-compatible API is not configured. Fill api_settings.vllm "
            "(api_key, base_url, model) in config/config.yaml."
        )

    return ChatOpenAI(
        model=str(model),
        api_key=str(api_key),
        base_url=str(base_url).rstrip("/"),
        temperature=temperature,
    )


def chat_completion(messages, temperature=None):
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

    model = get_chat_model(temperature=temperature)
    response = model.invoke(lc_messages)
    record_langchain_usage(response)
    return response.content
