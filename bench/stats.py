"""Small statistics helpers for the benchmark. Standard library only.

A rank is the 1-based position of the first gold hit, or None for a miss.
"""
import math
import random


def topk(ranks, k):
    """Share of questions whose first gold hit is within the top k."""
    if not ranks:
        return 0.0
    return sum(1 for r in ranks if r is not None and r <= k) / len(ranks)


def mrr(ranks, cutoff=10):
    """Mean reciprocal rank, a hit below the cutoff counts as a miss."""
    if not ranks:
        return 0.0
    return sum(1.0 / r for r in ranks if r is not None and r <= cutoff) / len(ranks)


def mcnemar_exact(b, c):
    """Two-sided exact McNemar test on the discordant pairs b and c."""
    n = b + c
    if n == 0:
        return 1.0
    m = min(b, c)
    cdf = sum(math.comb(n, i) for i in range(m + 1)) / 2 ** n
    return min(1.0, 2 * cdf)


def bootstrap_ci(diffs, B=10000, alpha=0.05, seed=7):
    """Percentile bootstrap of the mean of paired differences.

    Returns (mean, lo, hi). Questions are resampled with replacement.
    """
    n = len(diffs)
    if n == 0:
        return (0.0, 0.0, 0.0)
    rng = random.Random(seed)
    mean = math.fsum(diffs) / n
    means = []
    for _ in range(B):
        means.append(math.fsum(rng.choices(diffs, k=n)) / n)
    means.sort()
    lo_i = max(0, int(math.floor(alpha / 2 * B)))
    hi_i = min(B - 1, int(math.ceil((1 - alpha / 2) * B)) - 1)
    return (mean, means[lo_i], means[hi_i])


def paired(ranks_a, ranks_b, k):
    """Compare two systems on the same questions at cutoff k.

    b = questions only system a hit, c = questions only system b hit,
    p = exact McNemar p-value of that split.
    """
    if len(ranks_a) != len(ranks_b):
        raise ValueError("rank lists differ in length")
    b = c = 0
    for ra, rb in zip(ranks_a, ranks_b, strict=True):
        ha = ra is not None and ra <= k
        hb = rb is not None and rb <= k
        if ha and not hb:
            b += 1
        elif hb and not ha:
            c += 1
    return {"b": b, "c": c, "p": mcnemar_exact(b, c)}
