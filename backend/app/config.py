"""Application configuration, loaded from environment / .env."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Nebius Token Factory (OpenAI-compatible endpoint) ---
    nebius_api_key: str = ""
    nebius_base_url: str = "https://api.tokenfactory.nebius.com/v1"
    nebius_model: str = "Qwen/Qwen2.5-72B-Instruct"

    # --- Tavily (used later by the Resolver for doc lookups) ---
    tavily_api_key: str = ""

    # --- GitHub pull-request integration ---
    github_repository: str = ""
    github_token: str = ""
    github_seated_users: str = ""

    # --- App ---
    default_base_branch: str = "main"
    # Where cloned demo repos are cached.
    workspace_dir: str = ".workspace"

    # Test argv is configured by the server operator, never by an API caller.
    resolver_test_command: list[str] = []
    resolver_test_timeout: int = Field(60, ge=1, le=600)
    resolver_max_file_bytes: int = Field(100_000, ge=1)
    resolver_max_conflicts: int = Field(20, ge=1)

    @property
    def nebius_ready(self) -> bool:
        return bool(self.nebius_api_key)

    @property
    def tavily_ready(self) -> bool:
        return bool(self.tavily_api_key)

    @property
    def seated_github_users(self) -> set[str]:
        return {
            username.strip().lower()
            for username in self.github_seated_users.split(",")
            if username.strip()
        }


settings = Settings()
