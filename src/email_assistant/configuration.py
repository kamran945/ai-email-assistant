import os
from enum import Enum
from dataclasses import dataclass, fields
from typing import Any, Optional

from langchain_core.runnables import RunnableConfig
from dataclasses import dataclass

from src.email_assistant.utils import load_config


class WriterProvider(Enum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GROQ = "groq"


@dataclass(kw_only=True)
class Configuration:
    """The configurable fields for the chatbot."""

    config_yaml = load_config()

    triage_model = config_yaml.get("triage_model", "gemma2-9b-it")
    draft_response_model = config_yaml.get("draft_response_model", "gemma2-9b-it")
    rewrite_email_model = config_yaml.get("rewrite_email_model", "gemma2-9b-it")
    find_meeting_model = config_yaml.get("find_meeting_model", "gemma2-9b-it")
    reflection_model = config_yaml.get("reflection_model", "gemma2-9b-it")
    email = config_yaml.get("email", "")

    @classmethod
    def from_runnable_config(
        cls, config: Optional[RunnableConfig] = None
    ) -> "Configuration":
        """Create a Configuration instance from a RunnableConfig."""
        configurable = (
            config["configurable"] if config and "configurable" in config else {}
        )
        values: dict[str, Any] = {
            f.name: os.environ.get(f.name.upper(), configurable.get(f.name))
            for f in fields(cls)
            if f.init
        }
        return cls(**{k: v for k, v in values.items() if v})
