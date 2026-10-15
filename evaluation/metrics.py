"""Document-level metrics. Duplicate chunks never earn repeated relevance gain."""

import math


def score_ranking(ranking: list[str], relevance: dict[str, int]) -> dict[str, float]:
    if not relevance or any(grade <= 0 for grade in relevance.values()):
        raise ValueError("At least one positively graded relevant document is required")
    unique = list(dict.fromkeys(ranking))
    relevant = set(relevance)
    result = {f"recall@{k}": len(set(unique[:k]) & relevant) / len(relevant) for k in (1, 5, 10)}
    result["mrr@10"] = next(
        (1 / position for position, key in enumerate(unique[:10], 1) if key in relevant), 0.0
    )
    dcg = sum(
        (2 ** relevance.get(key, 0) - 1) / math.log2(position + 1)
        for position, key in enumerate(unique[:10], 1)
    )
    ideal = sum(
        (2**grade - 1) / math.log2(position + 1)
        for position, grade in enumerate(sorted(relevance.values(), reverse=True)[:10], 1)
    )
    result["ndcg@10"] = dcg / ideal
    return result


def percentile(values: list[float], quantile: float) -> float:
    """Nearest-rank percentile, including measured failures in latency samples."""
    if not values or not 0 < quantile <= 1:
        raise ValueError("Nonempty samples and 0 < quantile <= 1 are required")
    return sorted(values)[math.ceil(quantile * len(values)) - 1]
