"""The statistics of a claim (ENT-S3 AC3; the Accuracy Plan's "proof calculator").

A measured precision is a sample. "99% correct" is PROVEN only when the 95% lower bound of the
measurement is at or above 99% - not when the observed number is. The bound shown is Wilson's,
one sided (z = 1.645), the same one the Accuracy Plan sheet uses: 300 right of 300 gives a
lower bound of 0.99106 and proves 99%; 299 of 300 gives 0.98520 and does not.

Wilson's bound is an approximation, and with no error at all it is a little generous: it would
call 99% proven at 268 of 268, where the sheet's own "accepted values needed with ZERO errors"
is 299. So a claim is called proven only when the EXACT bound (Clopper-Pearson, which gives
that 299) agrees as well - the stricter of the two decides.
"""

from __future__ import annotations

import math

Z95 = 1.645  # one-sided 95%


def wilson_lower(correct: int, n: int, z: float = Z95) -> float | None:
    """The lower end of what the true rate can be, given ``correct`` of ``n``. None for n = 0:
    nothing was measured, so nothing can be said."""
    if n <= 0:
        return None
    if not 0 <= correct <= n:
        raise ValueError(f"correct must be between 0 and n, got {correct} of {n}")
    p = correct / n
    centre = p + z * z / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (centre - spread) / (1 + z * z / n))


def wilson_upper(errors: int, n: int, z: float = Z95) -> float | None:
    """The upper end of what an error rate can be, given ``errors`` in ``n``."""
    low = wilson_lower(n - errors, n, z) if n > 0 else None
    return None if low is None else 1.0 - low


def _tail(correct: int, n: int, p: float) -> float:
    """P(at least ``correct`` right out of ``n``) when the true rate is ``p``."""
    if p <= 0.0:
        return 1.0 if correct == 0 else 0.0
    if p >= 1.0:
        return 1.0
    total = 0.0
    for k in range(correct, n + 1):
        log_term = (
            math.lgamma(n + 1)
            - math.lgamma(k + 1)
            - math.lgamma(n - k + 1)
            + k * math.log(p)
            + (n - k) * math.log1p(-p)
        )
        total += math.exp(log_term)
    return min(1.0, total)


def exact_lower(correct: int, n: int, confidence: float = 0.95) -> float | None:
    """The exact (Clopper-Pearson) one-sided lower bound: the smallest true rate under which
    seeing ``correct`` or more of ``n`` still has a (1 - confidence) chance."""
    if n <= 0:
        return None
    if not 0 <= correct <= n:
        raise ValueError(f"correct must be between 0 and n, got {correct} of {n}")
    if correct == 0:
        return 0.0
    alpha = 1.0 - confidence
    if correct == n:
        return float(alpha ** (1.0 / n))
    low, high = 0.0, 1.0
    for _ in range(60):
        mid = (low + high) / 2
        if _tail(correct, n, mid) < alpha:
            low = mid
        else:
            high = mid
    return low


def needed_with_no_errors(target: float, confidence: float = 0.95) -> int:
    """How many accepted values, ALL correct, a slice needs before ``target`` is proven. With
    any error it is more. 299 for 99% - the Accuracy Plan's own number."""
    if not 0 < target < 1:
        raise ValueError("target must be between 0 and 1")
    return math.ceil(math.log(1.0 - confidence) / math.log(target))


def proves(correct: int, n: int, target: float, z: float = Z95) -> dict[str, object]:
    """One claim, checked: the sample size, the observed rate, the lower bound and the verdict.
    ``proven`` is True only when the LOWER BOUND meets the target - Wilson's, as the Accuracy
    Plan shows it, and the exact one too."""
    low, exact = wilson_lower(correct, n, z), exact_lower(correct, n)
    return {
        "n": n,
        "correct": correct,
        "observed": correct / n if n else None,
        "lower_bound_95": low,
        "lower_bound_95_exact": exact,
        "target": target,
        "proven": bool(low is not None and exact is not None and min(low, exact) >= target),
        "needed_with_no_errors": needed_with_no_errors(target),
    }
