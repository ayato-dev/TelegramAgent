from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from anthropic.types.beta import BetaUsage

if TYPE_CHECKING:
    from tgagent.agent.models import ModelSpec

MTOK = Decimal(1_000_000)


@dataclass(frozen=True, slots=True)
class RateCard:
    """USD per million tokens."""

    input: Decimal
    output: Decimal
    cache_write: Decimal
    cache_read: Decimal


@dataclass(frozen=True, slots=True)
class ModelPricing:
    short: RateCard
    long: RateCard
    long_prompt_threshold: int

    def card(self, prompt_tokens: int) -> RateCard:
        return self.long if prompt_tokens > self.long_prompt_threshold else self.short


HAIKU_5_5 = ModelPricing(
    short=RateCard(Decimal("0.10"), Decimal("0.50"), Decimal("0.125"), Decimal("0.01")),
    long=RateCard(Decimal("0.50"), Decimal("2.50"), Decimal("0.625"), Decimal("0.05")),
    long_prompt_threshold=100_000,
)
# Sonnet and Opus 5.5 price the full 1M context the same; cache reads are $0.20 on both.
SONNET_5_5_CARD = RateCard(Decimal("2"), Decimal("10"), Decimal("2.50"), Decimal("0.20"))
OPUS_5_5_CARD = RateCard(Decimal("4"), Decimal("20"), Decimal("5"), Decimal("0.20"))
PRICING = {
    "claude-haiku-5-5": HAIKU_5_5,
    "claude-sonnet-5-5": ModelPricing(SONNET_5_5_CARD, SONNET_5_5_CARD, long_prompt_threshold=1_000_000),
    "claude-opus-5-5": ModelPricing(OPUS_5_5_CARD, OPUS_5_5_CARD, long_prompt_threshold=1_000_000),
}
WEB_SEARCH_PRICE = Decimal("0.01")
WHISPER_PRICE_PER_HOUR = {"whisper-large-v3": Decimal("0.111"), "whisper-large-v3-turbo": Decimal("0.04")}
WHISPER_MIN_BILLED_SECONDS = 10


@dataclass(frozen=True, slots=True)
class IterationUsage:
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int

    @property
    def prompt_tokens(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens


@dataclass(slots=True)
class TurnUsage:
    """Usage across every request of one agent turn, kept per sampling iteration.

    With compaction enabled the API reports each step (compaction, message) separately in
    ``usage.iterations``; each step picks its own rate card by prompt length.
    """

    iterations: list[IterationUsage] = field(default_factory=list)
    web_search_requests: int = 0
    # Billed tools priced per use rather than per token, e.g. OpenAI code containers.
    tool_cost: Decimal = Decimal(0)

    def add_iteration(self, input_tokens: int, output_tokens: int, cache_read: int, cache_write: int) -> None:
        self.iterations.append(IterationUsage(input_tokens, output_tokens, cache_read, cache_write))

    def add(self, usage: BetaUsage) -> None:
        parts: Sequence[Any] = usage.iterations or [usage]
        for part in parts:
            self.iterations.append(
                IterationUsage(
                    input_tokens=part.input_tokens or 0,
                    output_tokens=part.output_tokens or 0,
                    cache_read_tokens=getattr(part, "cache_read_input_tokens", None) or 0,
                    cache_write_tokens=getattr(part, "cache_creation_input_tokens", None) or 0,
                )
            )
        if usage.server_tool_use:
            self.web_search_requests += usage.server_tool_use.web_search_requests or 0

    @property
    def input_tokens(self) -> int:
        return sum(it.input_tokens for it in self.iterations)

    @property
    def output_tokens(self) -> int:
        return sum(it.output_tokens for it in self.iterations)

    @property
    def cache_read_tokens(self) -> int:
        return sum(it.cache_read_tokens for it in self.iterations)

    @property
    def cache_write_tokens(self) -> int:
        return sum(it.cache_write_tokens for it in self.iterations)


def _token_cost(pricing: ModelPricing, usage: TurnUsage) -> Decimal:
    total = Decimal(0)
    for it in usage.iterations:
        card = pricing.card(it.prompt_tokens)
        total += (
            it.input_tokens * card.input
            + it.output_tokens * card.output
            + it.cache_read_tokens * card.cache_read
            + it.cache_write_tokens * card.cache_write
        ) / MTOK
    return total


def claude_cost(model: str, usage: TurnUsage) -> Decimal:
    return _token_cost(PRICING.get(model, HAIKU_5_5), usage) + usage.web_search_requests * WEB_SEARCH_PRICE


def deepseek_peak(now: datetime) -> bool:
    """DeepSeek full-price hours: weekdays 01:00-04:00 and 06:00-10:00 UTC."""
    now = now.astimezone(UTC)
    return now.weekday() < 5 and (1 <= now.hour < 4 or 6 <= now.hour < 10)


def turn_cost(spec: "ModelSpec", usage: TurnUsage, now: datetime | None = None) -> Decimal:
    """USD for one turn; free plans and models without a known price cost nothing."""
    if spec.free or spec.pricing is None:
        return Decimal(0)
    tokens = _token_cost(spec.pricing, usage)
    if spec.off_peak and not deepseek_peak(now or datetime.now(UTC)):
        tokens /= 2
    return tokens + usage.web_search_requests * spec.search_price + usage.tool_cost


def whisper_cost(seconds: float, model: str = "whisper-large-v3") -> Decimal:
    billed = Decimal(max(seconds, WHISPER_MIN_BILLED_SECONDS))
    return (
        billed / Decimal(3600) * WHISPER_PRICE_PER_HOUR.get(model, WHISPER_PRICE_PER_HOUR["whisper-large-v3"])
    )
