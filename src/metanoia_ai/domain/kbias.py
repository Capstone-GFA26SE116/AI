"""FR-17: K_bias learning and the adjusted estimate (T_proposed).

All durations are in minutes. Default values live here; the application
layer may pass other values read from config.
"""

from metanoia_ai.domain.models import ActualTimeSource, TaskType

K_MIN = 0.5
K_MAX = 3.0
INITIAL_K_BIAS = 1.25  # Open: may be seeded from onboarding (Problems #10).
DEFAULT_ALPHA = 0.3
# Open: threshold not agreed yet (Problems #12). Proposed value: > 5x or < 1/5.
DEFAULT_OUTLIER_RATIO = 5.0


def clamp_k_bias(k: float) -> float:
    """Keep K_bias inside [K_MIN, K_MAX]."""
    return max(K_MIN, min(K_MAX, k))


def proposed_minutes(t_est: float, k_bias: float, buffer: float = 0.0) -> float:
    """Return T_proposed = T_est * K_bias + buffer."""
    if t_est <= 0:
        raise ValueError("t_est must be positive")
    if buffer < 0:
        raise ValueError("buffer must not be negative")
    return t_est * clamp_k_bias(k_bias) + buffer


def should_learn(
    task_type: TaskType,
    t_est: float,
    t_act: float | None,
    source: ActualTimeSource,
    user_confirmed: bool = False,
    outlier_ratio: float = DEFAULT_OUTLIER_RATIO,
) -> bool:
    """Return True only when this result is clean enough to update K_bias.

    t_act is None when the user did not answer (UNKNOWN).
    user_confirmed only matters for auto-stopped timers.
    """
    if task_type is TaskType.FIXED:
        return False
    if t_act is None or t_act <= 0 or t_est <= 0:
        return False
    if source is ActualTimeSource.AUTO_TIMEOUT and not user_confirmed:
        return False
    ratio = t_act / t_est
    return 1 / outlier_ratio <= ratio <= outlier_ratio


def update_k_bias(
    k_old: float, t_est: float, t_act: float, alpha: float = DEFAULT_ALPHA
) -> float:
    """Return K_new = alpha * (T_act / T_est) + (1 - alpha) * K_old, clamped."""
    if t_est <= 0 or t_act <= 0:
        raise ValueError("t_est and t_act must be positive")
    if not 0 < alpha <= 1:
        raise ValueError("alpha must be in (0, 1]")
    k_new = alpha * (t_act / t_est) + (1 - alpha) * k_old
    return clamp_k_bias(k_new)
