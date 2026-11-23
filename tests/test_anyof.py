"""「A 或 B」條件（job_agent/anyof.py）。全部是程式規則：不用 LLM、答案可以手算。
測試用自己編的句子，不放真實職缺原文。"""

import pytest

from job_agent.anyof import (clean_groups, conditions, group_label, has_strong_cue, normalize_groups, or_scope,
                             shares)
from job_agent.schemas import MatchResult, Requirement


def req(skill, text, required=True, any_of=None):
    return Requirement(skill=skill, original_text=text, category="other", required=required, any_of=any_of)


def mr(skill, status, any_of=None, required=True):
    return MatchResult(skill=skill, required=required, status=status, any_of=any_of)


def scope_text(line):
    """or_scope 的範圍換成文字，方便看。"""
    kind, s0, s1, c0, c1 = or_scope(line)
    return kind, line[s0:s1].strip(), line[c0:c1].strip()


# ===== or_scope：「或」的範圍 =====

def test_or_at_end_covers_the_whole_list():
    assert scope_text("會用 Go、Rust 或 Java") == ("strong", "會用 Go、Rust 或 Java", "Rust 或 Java")


def test_or_in_front_covers_only_its_segment():
    kind, scope, _ = scope_text("Go 或 Rust、Docker、Git")
    assert (kind, scope) == ("strong", "Go 或 Rust")


def test_quantifier_covers_the_whole_line():
    assert scope_text("熟悉以下任一語言：Go, Rust")[1] == "熟悉以下任一語言：Go, Rust"
    assert scope_text("Experience with one of Go, Rust, Java")[0] == "strong"


@pytest.mark.parametrize("line", [
    "3 年或以上軟體開發經驗，熟悉 Go、Git",
    "資訊工程或相關科系畢業，熟悉 Go",
    "Bachelor's or Master's degree, with Go and Git experience",
    "熟悉 Go 與 Rust",
])
def test_not_an_either_or(line):
    assert not has_strong_cue(line)


def test_examples_are_weak():
    assert or_scope("熟悉 Go、Rust 等語言")[0] == "weak"
    assert or_scope("tools such as Go and Rust")[0] == "weak"
    assert or_scope("tools such as Go, Rust, or Java")[0] == "weak"     # 舉例清單最後的 or 也不算
    assert or_scope("例如 Go 或 Rust 任一")[0] == "strong"               # 有「任一」量詞 → 還是多選一
    assert or_scope("熟悉 Go 與 Rust") is None


# ===== 群組的程式檢查 =====

def test_normalize_groups_drops_single_member_and_mixed():
    reqs = [req("Go", "x", any_of="g1"),                                       # 只有 1 個成員
            req("Rust", "y", any_of="g2"), req("Java", "y", required=False, any_of="g2"),  # 必備加分混在一起
            req("Kotlin", "z", any_of="g3"), req("Scala", "z", any_of="g3")]   # 正常
    assert [r.any_of for r in normalize_groups(reqs)] == [None, None, None, "g3", "g3"]


def test_clean_groups_keeps_only_grounded_groups():
    text = "熟悉 Go 或 Rust\n熟悉 Docker、Git\n熟悉 Java\n會 Kotlin"
    reqs = [req("Go", "熟悉 Go 或 Rust", any_of="g1"), req("Rust", "熟悉 Go 或 Rust", any_of="g1"),
            req("Docker", "熟悉 Docker、Git", any_of="g2"), req("Git", "熟悉 Docker、Git", any_of="g2"),  # 沒有「或」
            req("Java", "熟悉 Java", any_of="g3"), req("Kotlin", "會 Kotlin", any_of="g3")]           # 不同行
    cleaned, dropped = clean_groups(reqs, text)
    assert [r.any_of for r in cleaned] == ["g1", "g1", None, None, None, None]
    assert {d["any_of"]: d["why"] for d in dropped} == {"g2": "那一行沒有「或／任一」", "g3": "成員的原文不在同一行"}


# ===== 計分 =====

def test_conditions_group_counts_once():
    results = [mr("Go", "have", "g1"), mr("Docker", "partial"), mr("Rust", "missing", "g1")]
    assert [[m.skill for m in c] for c in conditions(results)] == [["Go", "Rust"], ["Docker"]]


@pytest.mark.parametrize("statuses, expected", [
    (("have", "missing"), (0.0, 0.0)),         # 會其中一個 → 條件滿足，兩個都不算缺口
    (("partial", "missing"), (1.0, 0.0)),      # 缺口算在最接近的選項上
    (("partial", "partial"), (0.5, 0.5)),      # 一樣近 → 平分
    (("missing", "missing"), (0.5, 0.5)),      # 都沒有 → 平分
])
def test_shares(statuses, expected):
    results = [mr("Go", statuses[0], "g1"), mr("Rust", statuses[1], "g1"), mr("Docker", "missing")]
    s = shares(results)
    assert (s["go"], s["rust"]) == expected and s["docker"] == 1.0


def test_shares_three_way_all_missing():
    s = shares([mr("Go", "missing", "g1"), mr("Rust", "missing", "g1"), mr("Java", "missing", "g1")])
    assert s["go"] == pytest.approx(1 / 3)


def test_group_label():
    results = [mr("Go", "have", "g1"), mr("Rust", "missing", "g1"), mr("Docker", "partial")]
    assert group_label(results[1], results) == "任一：Go／Rust"
    assert group_label(results[2], results) == ""