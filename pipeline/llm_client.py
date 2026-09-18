"""
Shared LLM client for Steps 4 and 5.

CLAUDE.md originally specified the Claude API for these steps. Per project
decision, Steps 4-5 instead call an NVIDIA-hosted model through NVIDIA's
OpenAI-compatible endpoint (https://integrate.api.nvidia.com/v1), using the
`openai` SDK pointed at that base_url. Everything downstream (JSON schema,
retry/repair logic, logging) is unchanged by this swap -- only this module
and requirements.txt/.env know which provider is in use.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

import openai
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

NVIDIA_API_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODEL = os.environ.get("NVIDIA_MODEL", "nvidia/nemotron-3-ultra-550b-a55b")

# Errors worth retrying: rate limits, transient server/overload errors, and
# connection hiccups. Everything else (bad auth, bad request, not found) is
# a config problem retrying can't fix, so it's left to propagate immediately.
# NVIDIA's own "Service temporarily overloaded" arrives mid-stream as the
# generic openai.APIError base class (no HTTP status to key off), so that
# base class is included too -- excluding it would silently stop retrying
# the single error this was added for.
_RETRYABLE_ERRORS = (
    openai.APIError,
    openai.RateLimitError,
    openai.InternalServerError,
    openai.APIConnectionError,
    openai.APITimeoutError,
)
_NON_RETRYABLE_ERRORS = (
    openai.AuthenticationError,
    openai.PermissionDeniedError,
    openai.NotFoundError,
    openai.BadRequestError,
)
_MAX_API_ATTEMPTS = 4
_BACKOFF_BASE_SECONDS = 3.0


@dataclass
class LLMResult:
    """The full outcome of one LLM call, kept together for Step 7 logging."""

    content: str
    reasoning: str
    model: str
    system_prompt: str
    user_prompt: str
    temperature: float
    max_tokens: int
    extra: dict = field(default_factory=dict)


def get_client() -> OpenAI:
    api_key = os.environ.get("NVIDIA_API_KEY")
    if not api_key:
        raise RuntimeError(
            "NVIDIA_API_KEY is not set. Copy .env.example to .env at the "
            "project root and fill in your key (see https://build.nvidia.com)."
        )
    return OpenAI(base_url=NVIDIA_API_BASE_URL, api_key=api_key)


def call_llm(
    system_prompt: str,
    user_prompt: str,
    *,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.2,
    top_p: float = 0.95,
    max_tokens: int = 4096,
    enable_thinking: bool = True,
) -> LLMResult:
    """Call the configured LLM and return the full buffered result.

    The response is streamed (as NVIDIA recommends for its reasoning
    models) but fully buffered before returning, since callers need the
    complete text to parse structured JSON out of it. Reasoning/thinking
    tokens (`reasoning_content`, when the model emits them) are captured
    separately from the final answer for logging, but are never part of
    what callers try to parse as JSON.

    Transient failures (rate limits, NVIDIA's "Service temporarily
    overloaded", connection hiccups) are retried with backoff, up to
    `_MAX_API_ATTEMPTS` times; this is separate from -- and lower-level
    than -- the malformed-JSON retry loop the calling agents (Steps 4-5)
    implement, since that one needs to change the prompt and this one
    doesn't.
    """
    client = get_client()

    last_exc: Exception | None = None
    for attempt in range(1, _MAX_API_ATTEMPTS + 1):
        try:
            stream = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
                extra_body={"chat_template_kwargs": {"enable_thinking": enable_thinking}},
                stream=True,
            )

            content_parts: list[str] = []
            reasoning_parts: list[str] = []
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    reasoning_parts.append(reasoning)
                if delta.content is not None:
                    content_parts.append(delta.content)

            return LLMResult(
                content="".join(content_parts),
                reasoning="".join(reasoning_parts),
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except _NON_RETRYABLE_ERRORS:
            raise
        except _RETRYABLE_ERRORS as exc:
            last_exc = exc
            if attempt == _MAX_API_ATTEMPTS:
                raise
            time.sleep(_BACKOFF_BASE_SECONDS * attempt)

    raise last_exc  # pragma: no cover -- unreachable, loop always returns or raises
