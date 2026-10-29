"""Ways to pick the coefficient k for the next task (plan §2.3).

Each method is an immutable value: `k()` gives the coefficient for the next task and
`learn()` returns a new method value that has seen one more clean task.
Learned coefficients are clamped to [K_MIN, K_MAX] like production (plan decision 2026-10-29).
"""

import math
from dataclasses import dataclass, replace
from typing import Protocol, Self

from metanoia_ai.domain.kbias import (
    DEFAULT_ALPHA,
    INITIAL_K_BIAS,
    K_MAX,
    K_MIN,
    clamp_k_bias,
    update_k_bias,
)


class Method(Protocol):
    code: str

    def k(self) -> float:
        """Coefficient for the next task, from past tasks only."""
        ...

    def learn(self, t_est: float, t_act: float) -> Self:
        """Return the method after learning one clean task."""
        ...


@dataclass(frozen=True)
class Constant:
    """B0 (k = 1.0, trust the estimate) and B1 (k = 1.3, never learns)."""

    code: str
    value: float

    def k(self) -> float:
        return self.value

    def learn(self, t_est: float, t_act: float) -> Self:
        return self


@dataclass(frozen=True)
class GeometricMean:
    """B2: geometric mean of every learned ratio; INITIAL_K_BIAS before the first one."""

    code: str = "B2"
    log_sum: float = 0.0
    count: int = 0

    def k(self) -> float:
        if self.count == 0:
            return INITIAL_K_BIAS
        return clamp_k_bias(math.exp(self.log_sum / self.count))

    def learn(self, t_est: float, t_act: float) -> Self:
        return replace(
            self, log_sum=self.log_sum + math.log(t_act / t_est), count=self.count + 1
        )


@dataclass(frozen=True)
class LastRatio:
    """B3: ratio of the last learned task; INITIAL_K_BIAS before the first one."""

    code: str = "B3"
    last: float | None = None

    def k(self) -> float:
        return INITIAL_K_BIAS if self.last is None else clamp_k_bias(self.last)

    def learn(self, t_est: float, t_act: float) -> Self:
        return replace(self, last=t_act / t_est)


@dataclass(frozen=True)
class Metanoia:
    """M: the production EMA (`update_k_bias`)."""

    code: str = "M"
    alpha: float = DEFAULT_ALPHA
    k_bias: float = INITIAL_K_BIAS

    def k(self) -> float:
        return self.k_bias

    def learn(self, t_est: float, t_act: float) -> Self:
        return replace(
            self, k_bias=update_k_bias(self.k_bias, t_est, t_act, self.alpha)
        )


@dataclass(frozen=True)
class LogEma:
    """X1 (exploratory, not production): EMA on ln(ratio), K = exp(L), clamped like M."""

    code: str = "X1"
    alpha: float = DEFAULT_ALPHA
    log_k: float = math.log(INITIAL_K_BIAS)

    def k(self) -> float:
        return math.exp(self.log_k)

    def learn(self, t_est: float, t_act: float) -> Self:
        new = self.alpha * math.log(t_act / t_est) + (1 - self.alpha) * self.log_k
        return replace(self, log_k=max(math.log(K_MIN), min(math.log(K_MAX), new)))


def all_methods() -> list[Method]:
    """Every method of plan §2.3, with default alpha and K0."""
    return [
        Constant("B0", 1.0),
        Constant("B1", INITIAL_K_BIAS),
        GeometricMean(),
        LastRatio(),
        Metanoia(),
        LogEma(),
    ]
