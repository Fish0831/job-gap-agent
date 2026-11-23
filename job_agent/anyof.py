"""「A 或 B」條件（ADR-10，docs/naming-and-any-of.md 第 3–5 節）。

職缺常寫「Python 或 C#」「以下任一語言」：名稱照樣拆成一個技能一個項目，但同一組的項目標同一個
any_of 標籤（例如 "g1"），計分時一組只算一個條件。全部是程式規則，不呼叫 LLM：

- or_scope()       一行原文裡有沒有「或」的線索，範圍到哪裡（拆解後檢查、評估的 or_lint 共用）
- normalize_groups() 群組少於 2 個成員、必備加分混在一起 → 取消群組
- clean_groups()   拆解後的第二道防線：成員要在同一行，而且那一行有「或／任一」強線索，不然取消
- conditions()     比對結果 → 條件（沒有群組的技能自己一個條件；一組一個條件）
- shares()         優先度的份額：缺口算在離你最近的選項上（一樣近就平分）

取消群組 = 回到「每個技能各算一個條件」的舊算法 → 錯誤只會往保守的方向走（低估適合度），不會高估。
"""

import re
import unicodedata

from job_agent.normalize import key
from job_agent.schemas import MatchResult, Requirement, Status

# ===== 「或」的線索（Fable 的 or_scope，第 5.2 節）=====

STRONG = re.compile(r"任一|任何一|其中之?一|擇一|至少[一1]|或者?|或是|and/or"
                    r"|\b(?:or|either|one of|any of|at least one)\b", re.I)
QUANT = re.compile(r"任一|任何一|其中之?一|擇一|至少[一1]|\b(?:either|one of|any of|at least one)\b", re.I)
NOT_OR = re.compile(r"或(?:以上|同等|相關|更[高多好]|類似)"
                    r"|\bor (?:above|more|higher|equivalent|related|similar|better)\b"
                    r"|\b(?:bachelor|master|b\.?s|m\.?s|ph\.?d)\S*,? or\b", re.I)
WEAK = re.compile(r"(?<![同平相])等(?![級待於候])|例如|譬如|如[:：]|\b(?:such as|e\.g\.|including)\b", re.I)
SEP = re.compile(r"[、,，;；]")


def nfkc(text: str) -> str:
    """全形轉半形（Ｐｙｔｈｏｎ → Python、：→ :），長度大多不變 → 位置還能對回原文。"""
    return unicodedata.normalize("NFKC", text)


def or_scope(line: str) -> tuple[str, int, int, int, int] | None:
    """回傳（強度, 範圍開始, 範圍結束, 線索那一段的開始, 結束）；沒有線索回傳 None。
    - 有量詞（任一、其中之一、one of…）→ 整行都是選項
    - 「或」在清單最後一段（「A、B 或 C」）→ 整串都是選項
    - 「或」在前面（「A 或 B、C、D」）→ 只有「或」那一段
    - 「或以上」「或相關科系」「Bachelor's or Master's」先遮掉，不算線索"""
    clean = NOT_OR.sub(lambda m: " " * len(m.group()), line)   # 遮掉，位置不變
    cue = STRONG.search(clean)
    if not cue:
        return ("weak", 0, len(line), 0, len(line)) if WEAK.search(clean) else None
    spans, start = [], 0
    for m in SEP.finditer(clean):
        spans.append((start, m.start()))
        start = m.end()
    spans.append((start, len(clean)))
    i = next(k for k, (a, b) in enumerate(spans) if a <= cue.start() <= b)
    whole = QUANT.search(clean) or i == len(spans) - 1          # 「任一…」或「A、B 或 C」
    return ("strong", 0, len(line), *spans[i]) if whole else ("strong", *spans[i], *spans[i])


def has_strong_cue(line: str) -> bool:
    scope = or_scope(nfkc(line))
    return scope is not None and scope[0] == "strong"


# ===== 找原文在第幾行 =====

def norm(text: str) -> str:
    """全形轉半形、小寫、去掉所有空白 → 比較原文時不受排版影響。"""
    return re.sub(r"\s+", "", nfkc(text).lower())


def find_line(r: Requirement, lines: list[str]) -> int | None:
    """original_text 出現在第幾行（找不到 → None）。"""
    q = norm(r.original_text)
    if not q:
        return None
    for i, line in enumerate(lines):
        if q in norm(line) or (len(norm(line)) > 3 and norm(line) in q):
            return i
    return None


# ===== 群組的程式檢查 =====

def normalize_groups(reqs: list[Requirement]) -> list[Requirement]:
    """群組少於 2 個成員、或同一組裡有必備也有加分 → 取消群組（any_of = None）。
    拆解後、統一名稱＋去重之後（例如 C# 和 C#/.NET 併成一個）都要跑一次。"""
    members: dict[str, list[Requirement]] = {}
    for r in reqs:
        if r.any_of:
            members.setdefault(r.any_of, []).append(r)
    bad = {g for g, rs in members.items() if len(rs) < 2 or len({r.required for r in rs}) > 1}
    return [r.model_copy(update={"any_of": None}) if r.any_of in bad else r for r in reqs]


def clean_groups(reqs: list[Requirement], text: str) -> tuple[list[Requirement], list[dict]]:
    """拆解後的檢查（ADR-10 決定 3）。模型標的群組要通過三個條件才算數：
    至少 2 個成員、必備／加分一致、成員的原文在同一行而且那一行有強線索（或、任一、or…）。
    不通過就取消 → 回傳（整理後的需求, 被取消的群組）。"""
    reqs = normalize_groups(reqs)
    lines = text.splitlines()
    members: dict[str, list[Requirement]] = {}
    for r in reqs:
        if r.any_of:
            members.setdefault(r.any_of, []).append(r)
    dropped = []
    for g, rs in members.items():
        found = {find_line(r, lines) for r in rs}
        if len(found) != 1 or None in found:
            dropped.append({"any_of": g, "skills": [r.skill for r in rs], "why": "成員的原文不在同一行"})
        elif not has_strong_cue(lines[found.pop()]):
            dropped.append({"any_of": g, "skills": [r.skill for r in rs], "why": "那一行沒有「或／任一」"})
    bad = {d["any_of"] for d in dropped}
    return [r.model_copy(update={"any_of": None}) if r.any_of in bad else r for r in reqs], dropped


# ===== 計分（market.py、report.py 共用）=====

BETTER_FIRST: list[Status] = ["have", "partial", "missing"]


def best(statuses: list[Status]) -> Status:
    """一組裡最好的狀態：有 > 部分 > 沒有。"""
    return min(statuses, key=BETTER_FIRST.index)


def conditions(results: list[MatchResult]) -> list[list[MatchResult]]:
    """沒有群組的技能自己是一個條件；同一個 any_of 的是一個條件（順序照第一次出現）。"""
    out: list[list[MatchResult]] = []
    groups: dict[str, list[MatchResult]] = {}
    for m in results:
        if m.any_of is None:
            out.append([m])
        elif m.any_of in groups:
            groups[m.any_of].append(m)
        else:
            groups[m.any_of] = [m]
            out.append(groups[m.any_of])
    return out


def shares(results: list[MatchResult]) -> dict[str, float]:
    """每個技能在這份職缺的份額（key(技能) → 0–1），算優先度用。沒有群組的是 1。
    一組裡：最好是「有」→ 全部 0（條件滿足了）；最好是「部分」→ 部分的成員平分 1、其他 0；
    全部「沒有」→ 每個 1 ÷ 成員數（例：必備 {Python, C#} 都沒有 → 各 +2，舊算法各 +4）。"""
    out = {}
    for cond in conditions(results):
        if len(cond) == 1:
            out[key(cond[0].skill)] = 1.0
            continue
        top = best([m.status for m in cond])
        winners = [m for m in cond if m.status == top] if top != "have" else []
        for m in cond:
            out[key(m.skill)] = (1 / len(winners) if m in winners else 0.0) if top != "missing" else 1 / len(cond)
    return out


def group_label(m: MatchResult, results: list[MatchResult]) -> str:
    """報告上顯示的「任一：Python／C#」；沒有群組回傳空字串。"""
    if not m.any_of:
        return ""
    return "任一：" + "／".join(r.skill for r in results if r.any_of == m.any_of)


def group_count(results: list[MatchResult]) -> int:
    return len({m.any_of for m in results if m.any_of})