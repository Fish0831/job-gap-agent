"""評估用的數學：全部是純函式（不呼叫 LLM、不讀檔）→ 用手算得出答案的小例子就能測。"""

import math
import statistics
from collections.abc import Callable, Sequence
from itertools import combinations


def jaccard(a: set, b: set) -> float:
    """交集 ÷ 聯集。兩個都空 → 1（一模一樣）。"""
    return 1.0 if not a and not b else len(a & b) / len(a | b)


def overlap_at_k(a: Sequence, b: Sequence, k: int = 10) -> int:
    """兩個排名的前 k 名有幾個相同。"""
    return len(set(a[:k]) & set(b[:k]))


def pairwise(items: list, fn: Callable) -> list:
    """N 次重跑的所有兩兩配對，各算一次 fn。"""
    return [fn(x, y) for x, y in combinations(items, 2)]


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else float("nan")


def flip_rate(labels: dict) -> float:
    """每個項目在 N 次重跑的結果 → 結果不完全相同的項目比例。"""
    return mean([1.0 if len(set(v)) > 1 else 0.0 for v in labels.values()]) if labels else 0.0


def kendall_tau(x: Sequence[float], y: Sequence[float]) -> float:
    """Kendall τ-b（處理同分）。1 = 排序完全一樣，-1 = 完全相反。"""
    conc = disc = tx = ty = 0
    for i, j in combinations(range(len(x)), 2):
        dx, dy = x[i] - x[j], y[i] - y[j]
        if dx == 0 and dy == 0:
            continue
        if dx == 0:
            tx += 1
        elif dy == 0:
            ty += 1
        elif (dx > 0) == (dy > 0):
            conc += 1
        else:
            disc += 1
    denom = math.sqrt((conc + disc + tx) * (conc + disc + ty))
    return (conc - disc) / denom if denom else 1.0


def spearman(x: Sequence[float], y: Sequence[float]) -> float:
    """Spearman ρ（排名的相關係數）。全部同分沒辦法算 → nan。"""
    try:
        return statistics.correlation(list(x), list(y), method="ranked")
    except statistics.StatisticsError:
        return float("nan")


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """比例 k/n 的 95% 信賴區間（樣本少時比 ±1.96·SE 準）。"""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    center = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, center - half), min(1.0, center + half))


def pct(k: int, n: int) -> str:
    """報比例時一律連同個數：16/23 (0.70)。"""
    return f"{k}/{n} ({k / n:.2f})" if n else "0/0"