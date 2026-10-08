"""Application configuration, loaded from environment / .env."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Nebius Token Factory (OpenAI-compatible chat completions) ---
    # Key comes only from the environment. Never hardcode it.
    nebius_api_key: str = ""
    # Token Factory endpoint and model. Both can be overridden with
    # NEBIUS_BASE_URL / NEBIUS_MODEL. See app/services/nemotron.py.
    nebius_base_url: str = "https://api.tokenfactory.nebius.com/v1/"
    nebius_model: str = "nvidia/nemotron-3-super-120b-a12b"
    # Display name reported by /api/health.
    nebius_provider: str = "Nebius Token Factory"
    # Set NEBIUS_ENABLED=false to force the pure-git scorer even when a key is set.
    nebius_enabled: bool = True
    # Single attempt. The OpenAI client is constructed with max_retries=0.
    nebius_timeout_seconds: float = 40.0
    # Top-K: after the static prefilter (shared files / symbol overlap), only
    # the K highest-signal pairs may receive a model call per request.
    nebius_max_pairs: int = 5

    # Nemotron 3 Super listed price (USD per 1M tokens). Config constants.
    nemotron_input_usd_per_1m: float = 0.30
    nemotron_output_usd_per_1m: float = 0.90

    # Verify-by-test: only for high-risk pairs (published or model score at or
    # above this), and only on local repos under VERIFY_ALLOWED_ROOTS (default:
    # the bundled demo/ folder). Never on remote clones.
    verify_enabled: bool = True
    verify_min_score: float = 70.0
    # os.pathsep-separated extra roots where generated tests may run.
    verify_allowed_roots: str = ""
    verify_max_attempts: int = 3
    # Seconds for one test-generation model call (reasoning + code).
    verify_llm_timeout_seconds: float = 150.0
    verify_timeout_seconds: float = 30.0
    verify_max_pairs: int = 3

    # --- Tavily (used later by the Resolver for doc lookups) ---
    tavily_api_key: str = ""

    # --- App ---
    default_base_branch: str = "main"
    # Where cloned demo repos are cached.
    workspace_dir: str = ".workspace"

    @property
    def nebius_ready(self) -> bool:
        return self.nebius_enabled and bool(self.nebius_api_key.strip())

    @property
    def tavily_ready(self) -> bool:
        return bool(self.tavily_api_key)


settings = Settings()
