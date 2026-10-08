"""Model catalog: what each model can do and what it costs.

A model is addressed as ``provider:model-id``. Models outside the catalog still work with
their provider's default capabilities and no price.
"""

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal, get_args

from tgagent.agent.pricing import ModelPricing, RateCard

if TYPE_CHECKING:
    from tgagent.config import Settings

Provider = Literal["anthropic", "openai", "gemini", "deepseek", "groq"]
PROVIDERS: tuple[Provider, ...] = get_args(Provider)
Compaction = Literal["server", "client"]

# Model used when DEFAULT_MODEL is not set: the cheapest capable one of the first configured provider.
FALLBACK_MODELS: dict[Provider, str] = {
    "anthropic": "claude-haiku-5-5",
    "openai": "gpt-6-luna",
    "gemini": "gemini-3.1-flash-lite",
    "deepseek": "deepseek-flash",
    "groq": "openai/gpt-oss-120b",
}
# Groq free plan: 8K tokens per minute, so prompts are compacted early and answers kept short.
GROQ_FREE_CONTEXT_TRIGGER = 5_000
GROQ_FREE_MAX_OUTPUT = 2_048


@dataclass(frozen=True, slots=True)
class ModelSpec:
    key: str
    provider: Provider
    model_id: str
    label: str
    vision: bool = False
    pdf: bool = False
    native_audio: bool = False
    youtube: bool = False
    web: bool = False
    code: bool = False
    image_out: bool = False
    compaction: Compaction = "client"
    # Filled in by resolve_model from settings and the provider plan.
    context_trigger: int = 0
    max_output: int = 0
    pricing: ModelPricing | None = None
    search_price: Decimal = Decimal(0)
    free: bool = False
    # DeepSeek: half price outside peak hours.
    off_peak: bool = False


def _card(input_: str, output: str, cache_write: str, cache_read: str) -> RateCard:
    return RateCard(Decimal(input_), Decimal(output), Decimal(cache_write), Decimal(cache_read))


# Haiku 5.5 bills prompts over 100K tokens at the long-context rate; Sonnet and Opus 5.5 price
# the full 1M context the same, with $0.20 cache reads on both.
HAIKU_5_5 = ModelPricing(
    _card("0.10", "0.50", "0.125", "0.01"),
    _card("0.50", "2.50", "0.625", "0.05"),
    long_prompt_threshold=100_000,
)
SONNET_5_5 = ModelPricing(_card("2", "10", "2.50", "0.20"), _card("2", "10", "2.50", "0.20"), 10**9)
OPUS_5_5 = ModelPricing(_card("4", "20", "5", "0.20"), _card("4", "20", "5", "0.20"), 10**9)


def _flat(input_: str, output: str, cache_read: str) -> ModelPricing:
    card = RateCard(Decimal(input_), Decimal(output), Decimal(input_), Decimal(cache_read))
    return ModelPricing(card, card, long_prompt_threshold=10**9)


def _openai(input_: str, output: str, cache_read: str) -> ModelPricing:
    """Above 272K input tokens OpenAI bills input twice and output one and a half times."""
    short = RateCard(Decimal(input_), Decimal(output), Decimal(input_), Decimal(cache_read))
    long = RateCard(short.input * 2, short.output * Decimal("1.5"), short.input * 2, short.cache_read * 2)
    return ModelPricing(short, long, long_prompt_threshold=272_000)


def _defaults(provider: Provider, **caps: Any) -> ModelSpec:
    return ModelSpec(key=provider, provider=provider, model_id="", label="", **caps)


PROVIDER_DEFAULTS: dict[Provider, ModelSpec] = {
    "anthropic": _defaults(
        "anthropic",
        vision=True,
        pdf=True,
        web=True,
        code=True,
        compaction="server",
        search_price=Decimal("0.01"),
    ),
    "openai": _defaults(
        "openai", vision=True, pdf=True, web=True, code=True, image_out=True, search_price=Decimal("0.01")
    ),
    "gemini": _defaults(
        "gemini",
        vision=True,
        pdf=True,
        native_audio=True,
        youtube=True,
        web=True,
        code=True,
        search_price=Decimal("0.014"),
    ),
    "deepseek": _defaults("deepseek", off_peak=True),
    "groq": _defaults("groq"),
}


def _entry(provider: Provider, model_id: str, label: str, pricing: ModelPricing, **caps: Any) -> ModelSpec:
    spec = replace(PROVIDER_DEFAULTS[provider], key=f"{provider}:{model_id}", model_id=model_id, label=label)
    return replace(spec, pricing=pricing, **caps)


_ENTRIES = (
    _entry("anthropic", "claude-haiku-5-5", "Claude Haiku 5.5", HAIKU_5_5),
    _entry("anthropic", "claude-sonnet-5-5", "Claude Sonnet 5.5", SONNET_5_5),
    _entry("anthropic", "claude-opus-5-5", "Claude Opus 5.5", OPUS_5_5),
    _entry("openai", "gpt-6-luna", "GPT-6 Luna", _openai("0.10", "0.50", "0.01")),
    _entry("openai", "gpt-6.1-sol", "GPT-6.1 Sol", _openai("2", "10", "0.10")),
    _entry("openai", "gpt-6-astra", "GPT-6 Astra", _openai("10", "50", "1")),
    _entry("gemini", "gemini-3.1-flash-lite", "Gemini 3.1 Flash-Lite", _flat("0.25", "1.50", "0.025")),
    _entry("gemini", "gemini-3.8-flash", "Gemini 3.8 Flash", _flat("0.75", "3.75", "0.075")),
    _entry(
        "gemini",
        "gemini-3.1-pro-preview",
        "Gemini 3.1 Pro",
        ModelPricing(
            RateCard(Decimal(2), Decimal(12), Decimal(2), Decimal("0.20")),
            RateCard(Decimal(4), Decimal(18), Decimal(4), Decimal("0.40")),
            long_prompt_threshold=200_000,
        ),
    ),
    _entry("deepseek", "deepseek-flash", "DeepSeek Flash", _flat("0.30", "1.20", "0.006"), vision=True),
    _entry("deepseek", "deepseek-v4-pro", "DeepSeek V4 Pro", _flat("1.32", "3.96", "0.044")),
    _entry(
        "groq", "openai/gpt-oss-120b", "gpt-oss-120b", _flat("0.15", "0.60", "0.075"), web=True, code=True
    ),
    _entry(
        "groq", "openai/gpt-oss-20b", "gpt-oss-20b", _flat("0.075", "0.30", "0.0375"), web=True, code=True
    ),
)
CATALOG: dict[str, ModelSpec] = {spec.key: spec for spec in _ENTRIES}


def parse_key(key: str) -> tuple[Provider, str]:
    provider, _, model_id = key.partition(":")
    if provider not in PROVIDERS or not model_id:
        raise ValueError(f"model must look like provider:model-id with a known provider, got {key!r}")
    return provider, model_id


def resolve_model(key: str, settings: "Settings") -> ModelSpec:
    """Catalog entry (or provider defaults for an unknown model) adjusted to the configured plans."""
    provider, model_id = parse_key(key)
    spec = CATALOG.get(key) or replace(
        PROVIDER_DEFAULTS[provider], key=key, model_id=model_id, label=model_id
    )
    spec = replace(
        spec, context_trigger=settings.compaction_trigger_tokens, max_output=settings.max_output_tokens
    )
    if provider == "gemini" and not settings.gemini_paid_tier:
        # Google Search grounding is paid-only; the free tier costs nothing.
        spec = replace(spec, web=False, free=True)
    if provider == "groq" and not settings.groq_paid_tier:
        spec = replace(
            spec,
            free=True,
            context_trigger=GROQ_FREE_CONTEXT_TRIGGER,
            max_output=min(spec.max_output, GROQ_FREE_MAX_OUTPUT),
        )
    return spec


def available_models(settings: "Settings") -> list[ModelSpec]:
    """Catalog models of the providers that have an API key, plus the default model."""
    keys = [key for key in CATALOG if settings.api_key(parse_key(key)[0]) is not None]
    if settings.default_model not in keys:
        keys.insert(0, settings.default_model)
    return [resolve_model(key, settings) for key in keys]
