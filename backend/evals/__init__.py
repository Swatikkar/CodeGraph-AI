from dataclasses import dataclass


@dataclass(frozen=True)
class RankingScore:
    recall_at_k: float
    reciprocal_rank: float


def score_ranking(expected_paths: list[str], ranked_paths: list[str], k: int = 5) -> RankingScore:
    expected = set(expected_paths)
    top_k = ranked_paths[:k]
    recall = len(expected.intersection(top_k)) / max(1, len(expected))
    first_rank = next((index for index, path in enumerate(ranked_paths, start=1) if path in expected), None)
    return RankingScore(recall_at_k=recall, reciprocal_rank=(1 / first_rank if first_rank else 0.0))
