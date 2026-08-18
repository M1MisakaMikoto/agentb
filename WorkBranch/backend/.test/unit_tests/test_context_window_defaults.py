import os
import sys
from copy import deepcopy
from unittest.mock import patch

import pytest


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, BACKEND_DIR)

from service.agent_service.prompts.base.token_calculator import (
    TokenCalculator as PromptTokenCalculator,
)
from service.agent_service.service.compression_service import (
    TokenCalculator as CompressionTokenCalculator,
)
from service.agent_service.service.llm_service import LLMService, FastLLMService
from service.settings_service.settings_service import DEFAULT_SETTINGS


class UnknownModelSettings:
    def get(self, key):
        if key == "llm:context_window":
            raise KeyError(key)
        assert key == "llm:model"
        return "deepseek-v4-flash"


class DictSettings:
    def __init__(self, data):
        self.data = data

    def get(self, key):
        node = self.data
        for part in key.split(":"):
            if part not in node:
                raise KeyError(key)
            node = node[part]
        return node


def test_default_context_window_is_one_million_tokens():
    settings = UnknownModelSettings()

    assert PromptTokenCalculator().context_window == 1_000_000
    assert PromptTokenCalculator(settings).context_window == 1_000_000
    assert CompressionTokenCalculator(settings).context_window == 1_000_000


def test_configured_context_window_is_used_by_token_calculators():
    settings = DictSettings({"llm": {"context_window": 456789}})

    assert PromptTokenCalculator(settings).context_window == 456789
    assert CompressionTokenCalculator(settings).context_window == 456789


@pytest.mark.parametrize("value", [0, -1, True, "1000000"])
def test_invalid_configured_context_window_fails(value):
    settings = DictSettings({"llm": {"context_window": value}})

    with pytest.raises(AssertionError, match="positive integer"):
        PromptTokenCalculator(settings)
    with pytest.raises(AssertionError, match="positive integer"):
        CompressionTokenCalculator(settings)


def test_default_model_limits_match_provider_contract():
    llm = DEFAULT_SETTINGS["llm"]

    assert llm["context_window"] == 1_000_000
    assert llm["max_tokens"] == 393_216
    assert llm["fast_context_window"] == 262_144
    assert llm["fast_max_tokens"] == 32_768


@patch("service.agent_service.service.llm_service.ChatOpenAI")
def test_llm_clients_receive_configured_output_limits(chat_openai):
    data = deepcopy(DEFAULT_SETTINGS)
    data["llm"]["api_key"] = "test-key"
    settings = DictSettings(data)
    LLMService._instance = None
    FastLLMService._instance = None
    try:
        LLMService(settings)._build_llm()
        FastLLMService(settings)._get_llm()
    finally:
        LLMService._instance = None
        FastLLMService._instance = None

    assert chat_openai.call_args_list[0].kwargs["max_tokens"] == 393_216
    assert chat_openai.call_args_list[1].kwargs["max_tokens"] == 32_768
