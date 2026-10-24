"""Small, dependency-free FSRS-7 inference port (not the optimizer).

Port of open-spaced-repetition/fsrs-rs scalar dual-trace kernel at
4bc0a0979f95dd01cc653031b6f2a01429e4c32b, BSD-3-Clause.
See assets/FSRS-LICENSE.txt. Python uses double precision; oracle tests compare
with the upstream f32 implementation within numerical tolerance.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import exp, isfinite, log

MODEL_ID = "fsrs7-dual-34-4bc0a097"
UPSTREAM_REVISION = "4bc0a0979f95dd01cc653031b6f2a01429e4c32b"
PARAMETERS = (
    .1104, 2.2395, 3.9221, 11.7841, 6.1686, .6457, 3.6807, 1.9795,
    0., 1.3826, .7024, .5999, .8146, .6398, 1., 1.3207, .6707,
    3.8668, .4416, .0934, 1.8631, .6162, 1.0869, .1567, .0801,
    .2421, .9464, .1433, .7145, 0., .5667, .3734, .5333, .3048,
)
S_MIN, S_MAX = .0001, 36500.
DESIRED_RETENTION = .9


def clamp(x, low, high):
    return max(low, min(high, x))


@dataclass(frozen=True)
class MemoryState:
    stability: float
    difficulty: float
    stability_fast: float

    def __post_init__(self):
        if (not all(isfinite(v) for v in (self.stability, self.difficulty, self.stability_fast))
                or not S_MIN <= self.stability <= S_MAX
                or not S_MIN <= self.stability_fast <= S_MAX
                or not 1 <= self.difficulty <= 10):
            raise ValueError("Invalid FSRS-7 memory state")


def _components(state):
    w = PARAMETERS
    s, d, sf = state.stability, state.difficulty, state.stability_fast
    decay1 = -clamp(w[23] * sf ** (w[33] - .3), .01, .95)
    factor1 = exp(min(log(w[25]) / decay1, 60)) - 1
    decay2 = -clamp(w[24], .01, .95)
    factor2 = w[26] ** (1 / decay2) - 1
    a = w[27] * sf ** (-w[29])
    b = w[28] * s ** w[30] * exp((d - 5) * (w[31] - .5))
    return decay1, factor1 / sf, decay2, factor2 * exp((d - 5) * (w[32] - .3)) / s, a, b


def retrievability(state, elapsed_days):
    if not isfinite(elapsed_days) or elapsed_days < 0:
        raise ValueError("Invalid elapsed time")
    d1, f1, d2, f2, a, b = _components(state)
    return ((a * (1 + f1 * elapsed_days) ** d1 + b * (1 + f2 * elapsed_days) ** d2)
            / (a + b)) * (1 - 2e-5) + 1e-5


def _stability(s, d, r, rating, start):
    w = PARAMETERS
    fail = min(s, w[start+3] * ((s+1) ** w[start+4] - 1) * exp((1-r) * w[start+5]))
    success = s * (exp(w[start] - 1.5) * (11-d) * s ** (-w[start+1])
                   * (exp((1-r) * w[start+2]) - 1)
                   * (w[start+6] if rating == 2 else 1)
                   * (w[start+7] if rating == 4 else 1) + 1)
    return clamp(fail if rating == 1 else max(fail, success), S_MIN, S_MAX)


def next_state(previous, rating, elapsed_days=0.):
    if rating not in (1, 2, 3, 4) or not isfinite(elapsed_days) or elapsed_days < 0:
        raise ValueError("Invalid FSRS review")
    w = PARAMETERS
    if previous is None:
        s = clamp(w[rating-1], S_MIN, S_MAX)
        return MemoryState(s, clamp(w[4] - exp(w[5] * (rating-1)) + 1, 1, 10), clamp(s*.8, S_MIN, S_MAX))
    r = retrievability(previous, elapsed_days)
    slow = _stability(previous.stability, previous.difficulty, r, rating, 7)
    d1, f1, _, _, _, _ = _components(previous)
    fast = _stability(previous.stability_fast, previous.difficulty,
                      (1 + f1 * elapsed_days) ** d1, rating, 15)
    if rating == 1:
        fast = min(fast, slow * .8)
    delta = -w[6] * (rating-3) * (r+.1 if rating == 1 else 1)
    difficulty = .01 * (w[4] - exp(w[5] * 3) + 1) + .99 * (
        previous.difficulty + (10-previous.difficulty) * delta / 9)
    return MemoryState(slow, clamp(difficulty, 1, 10), clamp(fast, S_MIN, S_MAX))


def interval(state, retention=DESIRED_RETENTION):
    """Solve the official dual-trace curve; bounded bisection, no day rounding."""
    if not isfinite(retention) or not .0001 <= retention <= .9999:
        raise ValueError("Invalid desired retention")
    if retention >= .9999:
        return 0.
    d1, f1, d2, f2, a, b = _components(state)
    def recall(t):
        return ((a * (1+f1*t)**d1 + b * (1+f2*t)**d2)/(a+b))*(1-2e-5)+1e-5
    low, high = 0., max(state.stability, state.stability_fast, 1.)
    while recall(high) > retention and high < S_MAX:
        high = min(high * 2, S_MAX)
    for _ in range(50):
        mid = (low + high) * .5
        if recall(mid) > retention:
            low = mid
        else:
            high = mid
    return clamp((low+high)*.5, 0, S_MAX)
