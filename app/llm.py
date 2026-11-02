"""
app/llm.py
─────────────────────────────────────────────────────────────────────────────
One place that talks to the Gemini chat model.

``call_structured(schema, system, user)`` sends a prompt and returns an
instance of the pydantic ``schema`` (Gemini is forced to answer in that
shape). Rate limits are retried with back-off; an exhausted DAILY quota raises
``QuotaExhaustedError`` straight away so the caller can stop cleanly.

Tests replace this function, so no network call is made in the test suite.
"""

from __future__ import annotations

import logging
import random
import time
from typing import TypeVar

from pydantic import BaseModel

from app.config import settings
from app.embeddings import QuotaExhaustedError, _is_daily_quota_error

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_BASE_DELAY_SECONDS = 4.0


def _is_rate_limit(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "429" in text or "resource_exhausted" in text or "rate" in text and "limit" in text


def call_structured(schema: type[T], system: str, user: str) -> T:
    """Ask the chat model and parse the answer into ``schema``."""
    from langchain_google_genai import ChatGoogleGenerativeAI  # lazy: keeps tests light

    llm = ChatGoogleGenerativeAI(
        model=settings.llm_model,
        google_api_key=settings.google_api_key,
        temperature=0,
    ).with_structured_output(schema)

    last: Exception | None = None
    for attempt in range(settings.llm_max_retries + 1):
        try:
            result = llm.invoke([("system", system), ("human", user)])
            if result is None:
                raise ValueError("model returned no structured answer")
            return result
        except Exception as exc:  # noqa: BLE001
            if _is_rate_limit(exc):
                if _is_daily_quota_error(exc):
                    raise QuotaExhaustedError(
                        "Gemini daily quota exhausted for the extraction model. "
                        "It resets about 12:30 pm IST; re-run then."
                    ) from exc
                last = exc
                delay = _BASE_DELAY_SECONDS * (2 ** attempt)
                delay += delay * 0.1 * (2 * random.random() - 1)
                logger.warning("LLM rate-limited (attempt %d). Retrying in %.1fs", attempt + 1, delay)
                time.sleep(max(0.1, delay))
                continue
            # Bad/unparseable output: one more try, then give up.
            last = exc
            if attempt >= 1:
                break
            logger.warning("LLM call failed (%s); retrying once", exc)
    raise RuntimeError(f"LLM call failed: {last}") from last
