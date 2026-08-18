import os
import sys


BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, BACKEND_DIR)

from service.agent_service.prompts.base.token_calculator import (
    TokenCalculator as PromptTokenCalculator,
)
from service.agent_service.service.compression_service import (
    TokenCalculator as CompressionTokenCalculator,
)


class UnknownModelSettings:
    def get(self, key):
        assert key == "llm:model"
        return "deepseek-v4-flash"


def test_default_context_window_is_one_million_tokens():
    settings = UnknownModelSettings()

    assert PromptTokenCalculator().context_window == 1_000_000
    assert PromptTokenCalculator(settings).context_window == 1_000_000
    assert CompressionTokenCalculator(settings).context_window == 1_000_000
