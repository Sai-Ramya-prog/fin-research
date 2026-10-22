"""
app/config.py
─────────────────────────────────────────────────────────────────────────────
Central configuration loaded from environment variables / .env file.

Usage:
    from app.config import settings

Fails fast with a clear human-readable message when a required variable for
the chosen embedding provider or for Chroma Cloud is missing.
"""

from __future__ import annotations

from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All application settings.  Loaded from the environment (or .env)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Embeddings ────────────────────────────────────────────────────────
    embedding_provider: Literal["gemini", "openai", "fake"] = "gemini"
    embedding_model: str = "gemini-embedding-001"
    embedding_batch_size: int = 32
    embedding_batch_pause_seconds: float = 2.0

    # ── Provider API keys ─────────────────────────────────────────────────
    google_api_key: str = ""
    openai_api_key: str = ""

    # ── Chroma Cloud ──────────────────────────────────────────────────────
    chroma_api_key: str = ""
    chroma_tenant: str = ""
    chroma_database: str = ""
    chroma_collection: str = "financial_docs"

    # ── Relational DB (hosted Postgres) ───────────────────────────────────
    database_url: str = ""

    # ── Cloud object storage ──────────────────────────────────────────────
    storage_backend: Literal["remote", "local"] = "remote"
    storage_endpoint_url: str = ""
    storage_bucket: str = ""
    storage_access_key_id: str = ""
    storage_secret_access_key: str = ""
    storage_region: str = "us-east-1"

    # ── API / CORS ────────────────────────────────────────────────────────
    cors_origins: str = "http://localhost:5173,http://localhost:4173"

    # ─────────────────────────────────────────────────────────────────────
    # Validators
    # ─────────────────────────────────────────────────────────────────────

    @model_validator(mode="after")
    def _check_required_keys(self) -> "Settings":
        errors: list[str] = []

        # ── Embedding provider key ────────────────────────────────────────
        if self.embedding_provider == "gemini":
            if not self.google_api_key:
                errors.append(
                    "GOOGLE_API_KEY is required when EMBEDDING_PROVIDER=gemini.\n"
                    "  → Get a free key at https://aistudio.google.com/app/apikey\n"
                    "  → Then add it to your .env file:  GOOGLE_API_KEY=<your-key>"
                )
        elif self.embedding_provider == "openai":
            if not self.openai_api_key:
                errors.append(
                    "OPENAI_API_KEY is required when EMBEDDING_PROVIDER=openai.\n"
                    "  → Get a key at https://platform.openai.com/api-keys\n"
                    "  → Then add it to your .env file:  OPENAI_API_KEY=<your-key>"
                )
        # "fake" provider requires no key – used for tests / dry-runs.

        # ── Chroma Cloud settings ─────────────────────────────────────────
        chroma_missing = [
            name
            for name, val in [
                ("CHROMA_API_KEY", self.chroma_api_key),
                ("CHROMA_TENANT", self.chroma_tenant),
                ("CHROMA_DATABASE", self.chroma_database),
            ]
            if not val
        ]
        if chroma_missing:
            errors.append(
                f"Missing Chroma Cloud setting(s): {', '.join(chroma_missing)}.\n"
                "  → Sign up at https://trychroma.com and create a tenant + database.\n"
                "  → Copy the API key and add all three values to your .env file."
            )

        # ── Database URL ──────────────────────────────────────────────────
        if not self.database_url:
            errors.append(
                "DATABASE_URL is required.\n"
                "  → Provision a free Postgres instance (e.g. Supabase: https://supabase.com).\n"
                "  → Set DATABASE_URL=postgresql+psycopg://user:pass@host/db?sslmode=require"
            )

        if errors:
            header = (
                "\n"
                "╔══════════════════════════════════════════════════════════════╗\n"
                "║         fin-research — missing configuration                 ║\n"
                "╚══════════════════════════════════════════════════════════════╝\n"
            )
            body = "\n\n".join(f"  ✗ {e}" for e in errors)
            footer = (
                "\n\nCopy .env.example → .env and fill in the missing values, then retry.\n"
            )
            raise ValueError(header + body + footer)

        return self

    # ─────────────────────────────────────────────────────────────────────
    # Convenience helpers
    # ─────────────────────────────────────────────────────────────────────

    @property
    def cors_origins_list(self) -> list[str]:
        """Return CORS_ORIGINS split on commas."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


# Singleton – import this everywhere.
# Validation runs once at import time; a bad config is caught immediately.
settings = Settings()
