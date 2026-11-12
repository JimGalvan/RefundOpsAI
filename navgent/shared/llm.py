import os

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from navgent.shared import constants


def build_strong_model(model: str) -> BaseChatModel:
    return _build_model(
        model,
        "strong_model",
        constants.STRONG_MODEL_REASONING_EFFORT,
        constants.VULTR_STRONG_MODEL_REASONING_EFFORT,
    )


def build_fast_model(model: str) -> BaseChatModel:
    return _build_model(
        model,
        "fast_model",
        constants.FAST_MODEL_REASONING_EFFORT,
        constants.VULTR_FAST_MODEL_REASONING_EFFORT,
    )


def build_evidence_model(model: str) -> BaseChatModel:
    return _build_model(
        model,
        "evidence_model",
        constants.FAST_MODEL_REASONING_EFFORT,
        constants.VULTR_FAST_MODEL_REASONING_EFFORT,
    )


def _build_model(model: str, setting: str, openai_effort: str, vultr_effort: str) -> BaseChatModel:
    model = model.strip()
    provider, separator, model_id = model.partition(":")
    if not provider.strip() or not separator or not model_id.strip():
        raise RuntimeError(f"{setting} must use provider:model format (for example openai:gpt-5-nano)")
    if provider == constants.VULTR_PROVIDER:
        return _build_vultr_model(model_id.strip(), setting, vultr_effort)
    if model.startswith(constants.OPENAI_PROVIDER_PREFIX):
        return init_chat_model(model, reasoning_effort=openai_effort)
    return init_chat_model(model)


def _build_vultr_model(model_id: str, setting: str, reasoning_effort: str) -> BaseChatModel:
    # Vultr Serverless Inference speaks the OpenAI Chat Completions API, not the Responses API.
    return init_chat_model(
        f"{constants.OPENAI_PROVIDER_PREFIX}{model_id}",
        base_url=_vultr_base_url(),
        api_key=_vultr_api_key(setting),
        use_responses_api=False,
        reasoning_effort=reasoning_effort,
    )


def _vultr_api_key(setting: str) -> str:
    api_key = os.getenv(constants.VULTR_API_KEY_ENV, "").strip()
    if not api_key:
        raise RuntimeError(f"{setting} uses Vultr, so {constants.VULTR_API_KEY_ENV} must be set")
    return api_key


def _vultr_base_url() -> str:
    return os.getenv(constants.VULTR_BASE_URL_ENV, "").strip() or constants.VULTR_INFERENCE_BASE_URL
