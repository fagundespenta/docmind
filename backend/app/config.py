"""Application settings, loaded from environment variables (and an optional .env file)."""

from __future__ import annotations

from functools import cached_property
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

LLMProviderName = Literal["fake", "openai", "anthropic", "gemini"]
EmbeddingProviderName = Literal["fake", "openai", "gemini"]
VectorStoreName = Literal["chroma", "memory"]
Effort = Literal["low", "medium", "high", "xhigh", "max"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    # --- Providers -------------------------------------------------------
    llm_provider: LLMProviderName = "fake"
    embedding_provider: EmbeddingProviderName = "fake"

    openai_api_key: str = ""
    openai_model: str = "gpt-5-mini"
    openai_embedding_model: str = "text-embedding-3-small"

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: Effort = "low"

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    gemini_embedding_model: str = "gemini-embedding-001"

    fake_llm_delay: float = 0.015  # seconds between streamed words in fake mode

    # --- Storage ---------------------------------------------------------
    database_url: str = "sqlite:///./data/docmind.db"
    vector_store: VectorStoreName = "chroma"
    chroma_path: str = "./data/chroma"

    # --- RAG -------------------------------------------------------------
    chunk_size: int = 800
    chunk_overlap: int = 120
    top_k: int = 5
    history_turns: int = 6
    max_upload_mb: int = 20

    # --- API -------------------------------------------------------------
    api_keys: str = ""  # comma-separated; empty disables auth (local dev only)
    rate_limit_per_minute: int = 60
    cors_origins: str = "http://localhost:5173,http://localhost:8080"

    @cached_property
    def api_key_set(self) -> frozenset[str]:
        return frozenset(k.strip() for k in self.api_keys.split(",") if k.strip())

    @cached_property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]
