"""Provider failures sorted into the few cases a user can act on."""

from typing import Literal

import anthropic
import httpx2
import openai

ProviderErrorKind = Literal[
    "rate_limited", "quota", "region", "auth", "too_long", "bad_attachment", "unavailable", "other"
]

REGION_MARKERS = (
    "unsupported_country",
    "location is not supported",
    "not available in your country",
    "not supported in your region",
)
QUOTA_MARKERS = (
    "exceeded your current quota",
    "insufficient_quota",
    "insufficient balance",
    "credit balance",
    "billing",
)
LENGTH_MARKERS = ("prompt is too long", "context_length_exceeded", "maximum context length", "context window")
ATTACHMENT_MARKERS = ("document", "image", "file format", "media type", "mime")
UNAVAILABLE_STATUSES = {500, 502, 503, 504, 529}


class ProviderError(Exception):
    def __init__(self, kind: ProviderErrorKind, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.retry_after = retry_after


def _status(exc: BaseException) -> int | None:
    for name in ("status_code", "code"):
        value = getattr(exc, name, None)
        if isinstance(value, int):
            return value
    return None


def _retry_after(exc: BaseException) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    try:
        return float(headers["retry-after"]) if headers and "retry-after" in headers else None
    except ValueError:
        return None


def classify(exc: BaseException) -> ProviderError:
    """Map an Anthropic, OpenAI-compatible or Gemini SDK error to a ProviderError."""
    if isinstance(exc, ProviderError):
        return exc
    text = str(exc).lower()
    status = _status(exc)
    if isinstance(exc, anthropic.APIConnectionError | openai.APIConnectionError | httpx2.TransportError):
        return ProviderError("unavailable", str(exc))
    if status == 401 or (status == 403 and "api key" in text):
        return ProviderError("auth", str(exc))
    if any(marker in text for marker in REGION_MARKERS):
        return ProviderError("region", str(exc))
    if status == 402 or any(marker in text for marker in QUOTA_MARKERS):
        return ProviderError("quota", str(exc))
    if status == 429:
        return ProviderError("rate_limited", str(exc), retry_after=_retry_after(exc))
    if any(marker in text for marker in LENGTH_MARKERS):
        return ProviderError("too_long", str(exc))
    if status == 400 and any(marker in text for marker in ATTACHMENT_MARKERS):
        return ProviderError("bad_attachment", str(exc))
    if status in UNAVAILABLE_STATUSES or "overloaded" in text:
        return ProviderError("unavailable", str(exc))
    return ProviderError("other", str(exc))
