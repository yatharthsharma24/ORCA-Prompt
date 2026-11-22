"""Runtime configuration, read from environment variables (prefix ORCA_) or a .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ORCA_", env_file=".env", extra="ignore")

    # --- LLM (Ollama) ---
    ollama_base_url: str = "http://localhost:11434"
    # One model for every agent by default; each role can be overridden.
    model: str = "qwen3:8b"
    planner_model: str | None = None
    specialist_model: str | None = None
    synthesizer_model: str | None = None
    temperature: float = 0.1
    num_ctx: int = 8192
    # qwen3 / gemma4 etc. can "think" before answering. Off by default: slower and
    # the ReAct trace already carries the reasoning. None = don't send the flag.
    think: bool | None = False
    llm_timeout_s: float = 300.0
    keep_alive: str = "30m"

    # --- Agent loop limits ---
    max_plan_steps: int = 4
    max_react_iterations: int = 6
    max_revisions: int = 1
    llm_critic: bool = True  # LLM claim-support check on top of the deterministic number check
    observation_char_limit: int = 2500

    # --- Data ---
    data_db: Path = ROOT / "data" / "orca_demo.sqlite"
    knowledge_dir: Path = ROOT / "data" / "knowledge"
    runs_db: Path = ROOT / "data" / "runs.sqlite"
    sql_row_limit: int = 200

    # --- Live OBIS tool (real occurrence records from api.obis.org) ---
    enable_obis: bool = True
    obis_base_url: str = "https://api.obis.org/v3"
    obis_timeout_s: float = 20.0

    # --- Server ---
    api_key: str | None = None  # if set, /api/ask requires header X-API-Key
    cors_origins: list[str] = Field(default_factory=list)
    max_concurrent_runs: int = 1  # a single local GPU serves one run well
    max_queued_runs: int = 4
    log_level: str = "INFO"

    def model_for(self, role: str) -> str:
        override = {
            "planner": self.planner_model,
            "specialist": self.specialist_model,
            "synthesizer": self.synthesizer_model,
        }.get(role)
        return override or self.model


@lru_cache
def get_settings() -> Settings:
    return Settings()
