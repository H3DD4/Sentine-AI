"""
Diagnose cross-source fusion bias in `_weighted_rrf`.

Pure arithmetic — no network, no models, no Qdrant. It replays the exact
formula from app/services/retrieval.py against the live source weights and
reports which sources can reach the final top-k at all.

Run:  python scripts/diagnose_fusion_bias.py
"""

from __future__ import annotations

# Mirrors app/services/retrieval.py
RRF_K = 60
FINAL_TOP_K = 8
PER_SOURCE_LIMIT = 15

# Mirrors the registry (app/kb/sources/*.py)
WEIGHTS = {
    "ghostwriter": 1.25,
    "owasp_docs": 1.15,
    "owasp": 1.10,
    "internal": 1.10,
    "nvd": 1.00,
    "mitre": 1.00,
}

# Only sources with vectors can contribute. ghostwriter/internal are empty.
LIVE = {"owasp_docs", "owasp", "nvd", "mitre"}


def rrf_score(weight: float, rank: int) -> float:
    """score(d) = weight / (RRF_K + rank + 1) — the formula under test."""
    return weight / (RRF_K + rank + 1)


def simulate(live: set[str], top_k: int = FINAL_TOP_K) -> list[tuple[str, int, float]]:
    """
    Best case for the low-weight sources: assume EVERY source returns a full
    PER_SOURCE_LIMIT of perfectly relevant documents. If a source cannot place
    a single hit even here, it can never place one.
    """
    scored = [
        (key, rank, rrf_score(WEIGHTS[key], rank))
        for key in live
        for rank in range(PER_SOURCE_LIMIT)
    ]
    scored.sort(key=lambda t: t[2], reverse=True)
    return scored[:top_k]


def breakeven(loser: str, winner: str) -> float:
    """
    The rank the winner must fall past before the loser's rank-0 hit outscores
    it.  If that rank exceeds PER_SOURCE_LIMIT, the loser is structurally
    excluded no matter how relevant its documents are.
    """
    return (WEIGHTS[winner] / WEIGHTS[loser]) * (RRF_K + 1) - (RRF_K + 1)


def main() -> None:
    print("=" * 72)
    print("CROSS-SOURCE FUSION BIAS — weighted RRF")
    print(f"RRF_K={RRF_K}  FINAL_TOP_K={FINAL_TOP_K}  PER_SOURCE_LIMIT={PER_SOURCE_LIMIT}")
    print("=" * 72)

    print("\n1. Final top-8, assuming every live source returns 15 ideal hits")
    print("-" * 72)
    top = simulate(LIVE)
    counts: dict[str, int] = {}
    for i, (key, rank, score) in enumerate(top):
        counts[key] = counts.get(key, 0) + 1
        print(f"  {i + 1}. {key:<14} (its rank {rank})  score={score:.6f}")

    print("\n  Resulting citation counts:")
    for key in sorted(LIVE):
        print(f"    {key:<14} {counts.get(key, 0)}")

    shut_out = sorted(k for k in LIVE if counts.get(k, 0) == 0)
    print(f"\n  Sources that placed ZERO documents: {shut_out or 'none'}")

    print("\n2. How far a rival must fall before a weight-1.0 source gets in")
    print("-" * 72)
    for loser in ("nvd", "mitre"):
        for winner in ("owasp_docs", "owasp"):
            r = breakeven(loser, winner)
            verdict = (
                "UNREACHABLE — that rank exceeds PER_SOURCE_LIMIT"
                if r >= PER_SOURCE_LIMIT
                else f"reachable once {winner} runs out at rank {r:.1f}"
            )
            print(f"  {loser:<6} rank-0 beats {winner:<11} only past rank {r:5.1f}  → {verdict}")

    print("\n3. Same query, weights flattened to 1.0")
    print("-" * 72)
    saved = dict(WEIGHTS)
    for k in WEIGHTS:
        WEIGHTS[k] = 1.0
    flat = simulate(LIVE)
    flat_counts: dict[str, int] = {}
    for key, _, _ in flat:
        flat_counts[key] = flat_counts.get(key, 0) + 1
    for key in sorted(LIVE):
        print(f"    {key:<14} {flat_counts.get(key, 0)}")
    WEIGHTS.update(saved)

    print("\n" + "=" * 72)


if __name__ == "__main__":
    main()
