"""FR-17: K_bias learning and the adjusted estimate (T_proposed).

All durations are in minutes. Default values live here; the application
layer may pass other values read from config.
"""

from metanoia_ai.domain.models import SkipReason, TaskType

K_MIN = 0.5
K_MAX = 3.0
INITIAL_K_BIAS = 1.3  # Fixed for every new user (Moløkken & Jørgensen, 2003); learned per user after that.
DEFAULT_ALPHA = 0.2
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


def skip_reason(
    task_type: TaskType,
    t_est: float,
    t_act: float | None,
    outlier_ratio: float = DEFAULT_OUTLIER_RATIO,
) -> SkipReason | None:
    """Return why this task must not update K_bias, or None when it is clean.

    t_act is None when there is no usable actual time (UNKNOWN).
    Session checks (running, unconfirmed) are done in `summarize_actual_time`.
    """
    if task_type is TaskType.FIXED:
        return SkipReason.FIXED_TASK
    if t_est <= 0:
        raise ValueError("t_est must be positive")
    if t_act is None or t_act <= 0:
        return SkipReason.NO_ACTUAL_TIME
    ratio = t_act / t_est
    if not 1 / outlier_ratio <= ratio <= outlier_ratio:
        return SkipReason.OUTLIER
    return None


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
