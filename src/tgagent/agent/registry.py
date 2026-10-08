from collections.abc import Callable, Mapping, Sequence

from tgagent.agent.base import LLMRunner
from tgagent.agent.models import ModelSpec, Provider

type RunnerFactory = Callable[[ModelSpec], LLMRunner]


class ProviderRegistry:
    """The models this bot can run, and one runner per model built on first use."""

    def __init__(
        self, models: Sequence[ModelSpec], default_model: str, factories: Mapping[Provider, RunnerFactory]
    ) -> None:
        self._specs = {spec.key: spec for spec in models if spec.provider in factories}
        if default_model not in self._specs:
            raise ValueError(f"default model {default_model} has no configured provider")
        self.default_model = default_model
        self._factories = dict(factories)
        self._runners: dict[str, LLMRunner] = {}

    @property
    def models(self) -> list[ModelSpec]:
        return list(self._specs.values())

    def supports(self, key: str | None) -> bool:
        return key in self._specs

    def runner(self, key: str) -> LLMRunner:
        if key not in self._runners:
            spec = self._specs[key]
            self._runners[key] = self._factories[spec.provider](spec)
        return self._runners[key]
