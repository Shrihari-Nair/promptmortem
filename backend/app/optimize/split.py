"""Deterministic stratified splits (by cluster, else category). The held-out part is never shown to the optimizer."""
import random
from collections import defaultdict


def stratified_split(items: list[tuple[str, str]], fraction: float, seed: int = 0) -> tuple[list[str], list[str]]:
    """items = [(id, stratum)]. Returns (rest, held). Every stratum with >= 2 items puts at least one on each side."""
    by: dict[str, list[str]] = defaultdict(list)
    for i, stratum in sorted(items):
        by[stratum].append(i)
    rng = random.Random(seed)
    rest, held = [], []
    for stratum in sorted(by):
        ids = by[stratum]
        rng.shuffle(ids)
        k = 0 if len(ids) < 2 else min(max(1, round(len(ids) * fraction)), len(ids) - 1)
        held += ids[:k]
        rest += ids[k:]
    return sorted(rest), sorted(held)
