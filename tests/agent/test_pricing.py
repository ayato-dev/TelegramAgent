from decimal import Decimal

from anthropic.types.beta import BetaUsage

from tgagent.agent.pricing import TurnUsage, claude_cost, whisper_cost

ITERATION = {"cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}


def usage(**fields: object) -> BetaUsage:
    base: dict[str, object] = {"input_tokens": 0, "output_tokens": 0}
    base.update(fields)
    return BetaUsage.model_validate(base)


def test_small_prompt_uses_cheap_rate_card() -> None:
    turn = TurnUsage()
    turn.add(usage(input_tokens=100_000, output_tokens=1_000_000))

    assert claude_cost("claude-haiku-5-5", turn) == Decimal("0.51")


def test_prompt_over_100k_uses_expensive_rate_card() -> None:
    turn = TurnUsage()
    turn.add(usage(input_tokens=150_000, output_tokens=0))

    assert claude_cost("claude-haiku-5-5", turn) == Decimal("0.075")


def test_cache_tokens_count_towards_prompt_length_and_are_priced() -> None:
    turn = TurnUsage()
    turn.add(usage(input_tokens=1_000, cache_read_input_tokens=1_000_000, cache_creation_input_tokens=0))

    # 1.001M-token prompt is over 100K: cache reads at $0.05/MTok, input at $0.50/MTok
    assert claude_cost("claude-haiku-5-5", turn) == Decimal("0.0505")


def test_iterations_priced_separately() -> None:
    turn = TurnUsage()
    turn.add(
        usage(
            input_tokens=10,
            output_tokens=10,
            iterations=[
                {"type": "compaction", **ITERATION, "input_tokens": 120_000, "output_tokens": 2_000},
                {"type": "message", **ITERATION, "input_tokens": 20_000, "output_tokens": 1_000},
            ],
        )
    )

    compaction = Decimal("0.06") + Decimal("0.005")
    message = Decimal("0.002") + Decimal("0.0005")
    assert claude_cost("claude-haiku-5-5", turn) == compaction + message
    assert turn.input_tokens == 140_000
    assert turn.output_tokens == 3_000


def test_web_searches_billed_per_request() -> None:
    turn = TurnUsage()
    turn.add(usage(server_tool_use={"web_search_requests": 3, "web_fetch_requests": 0}))

    assert turn.web_search_requests == 3
    assert claude_cost("claude-haiku-5-5", turn) == Decimal("0.03")


def test_whisper_bills_at_least_ten_seconds() -> None:
    assert whisper_cost(3) == whisper_cost(10)
    assert whisper_cost(3600) == Decimal("0.111")
