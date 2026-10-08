import anthropic
import httpx2
import openai
import pytest
from google.genai import errors as genai_errors

from tgagent.agent.errors import ProviderError, classify


def response(status: int, url: str, headers: dict[str, str] | None = None) -> httpx2.Response:
    return httpx2.Response(status, request=httpx2.Request("POST", url), headers=headers)


def openai_error(status: int, message: str, headers: dict[str, str] | None = None) -> openai.APIStatusError:
    error = openai.APIStatusError(
        message, response=response(status, "https://api.groq.com", headers), body=None
    )
    if status == 429:
        error = openai.RateLimitError(
            message, response=response(status, "https://api.groq.com", headers), body=None
        )
    return error


def anthropic_error(status: int, message: str) -> anthropic.APIStatusError:
    return anthropic.APIStatusError(
        message, response=response(status, "https://api.anthropic.com"), body=None
    )


def gemini_error(code: int, status: str, message: str) -> genai_errors.APIError:
    return genai_errors.APIError(code, {"error": {"code": code, "status": status, "message": message}})


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (
            openai_error(429, "Rate limit reached for model openai/gpt-oss-120b on tokens per minute"),
            "rate_limited",
        ),
        (openai_error(429, "You exceeded your current quota, please check your plan and billing"), "quota"),
        (openai_error(402, "Insufficient Balance"), "quota"),
        (anthropic_error(400, "Your credit balance is too low to access the Anthropic API"), "quota"),
        (openai_error(403, "unsupported_country_region_territory"), "region"),
        (
            gemini_error(400, "FAILED_PRECONDITION", "User location is not supported for the API use."),
            "region",
        ),
        (openai_error(401, "Incorrect API key provided"), "auth"),
        (anthropic_error(400, "prompt is too long: 210000 tokens > 200000 maximum"), "too_long"),
        (openai_error(400, "This model's maximum context length is 131072 tokens"), "too_long"),
        (
            anthropic_error(400, "Unsupported document file format: application/pkcs7-signature"),
            "bad_attachment",
        ),
        (anthropic_error(529, "Overloaded"), "unavailable"),
        (gemini_error(503, "UNAVAILABLE", "The model is overloaded. Please try again later."), "unavailable"),
        (
            gemini_error(429, "RESOURCE_EXHAUSTED", "Resource has been exhausted (e.g. check quota)."),
            "rate_limited",
        ),
        (
            openai.APIConnectionError(request=httpx2.Request("POST", "https://api.deepseek.com")),
            "unavailable",
        ),
        (RuntimeError("something odd"), "other"),
    ],
)
def test_provider_errors_are_classified(error: Exception, kind: str) -> None:
    assert classify(error).kind == kind


def test_retry_after_is_kept() -> None:
    error = openai_error(429, "Rate limit reached", headers={"retry-after": "17"})

    assert classify(error).retry_after == 17


def test_provider_errors_pass_through() -> None:
    original = ProviderError("quota", "no money")

    assert classify(original) is original
