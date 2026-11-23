from datetime import datetime

from job_agent.market import (Run, aggregate, job_fit, overall_status, priority, rank_jobs, role_fit,
                              strengths, to_markdown, top_gaps)
from job_agent.schemas import JobRequirements, MatchResult


def m(skill, status, required=True, evidence=(), level=None):
    return MatchResult(skill=skill, required=required, status=status, evidence=list(evidence), level=level)


def run(job_id, *results):
    return Run(job_id=job_id, job=JobRequirements(title=f"Job {job_id}", company="ACME", requirements=[]),
               results=list(results))


RUNS = [
    run("01", m("RAG", "missing"), m("Python", "have", evidence=["p1"], level="working"), m("Docker", "partial")),
    run("02", m("rag", "missing"), m("Python", "have", evidence=["p1"], level="working"), m("Docker", "partial", required=False)),
    run("03", m("RAG", "partial"), m("Kubernetes", "missing", required=False)),
]


# 跨職缺狀態：取最常見的，平手取比較差的
def test_overall_status_majority_then_worse():
    assert overall_status(["missing", "missing", "partial"]) == "missing"
    assert overall_status(["have", "have", "partial"]) == "have"
    assert overall_status(["have", "partial"]) == "partial"      # 平手 → 保守
    assert overall_status(["partial", "missing"]) == "missing"


def test_aggregate_counts_and_merges_spelling():
    stats = {s.skill: s for s in aggregate(RUNS)}
    rag = stats["RAG"]                                  # rag 和 RAG 合併，顯示最常見的寫法
    assert rag.required_in == ["01", "02", "03"] and rag.status == "missing" and not rag.consistent
    assert stats["Docker"].required_in == ["01"] and stats["Docker"].preferred_in == ["02"]


def test_priority_sums_each_job():
    stats = {s.skill: s for s in aggregate(RUNS)}
    assert priority(stats["RAG"]) == 2 * 2 + 2 * 2 + 2 * 1  # 01、02 沒有（2×2），03 部分（2×1）→ 10
    assert priority(stats["Docker"]) == (2 * 1 + 1) * 1  # 必備 1 + 加分 1 × 部分
    assert priority(stats["Kubernetes"]) == 1 * 2        # 加分 1 × 沒有
    assert priority(stats["Python"]) == 0                # 有 → 不是缺口


def test_priority_one_flip_moves_little():
    """平手時一次翻轉：舊規則 6 ↔ 12（加倍），新規則只差 2。"""
    def stat_with(statuses):
        runs = [run(j, m("CI/CD", s, required=req)) for (j, s, req) in statuses]
        return {s.skill: s for s in aggregate(runs)}["CI/CD"]
    a = stat_with([("01", "partial", True), ("02", "missing", True)])
    b = stat_with([("01", "missing", True), ("02", "missing", True)])
    assert (priority(a), priority(b)) == (6, 8)


def test_top_gaps_and_strengths():
    stats = aggregate(RUNS)
    assert [s.skill for s in top_gaps(stats)] == ["RAG", "Docker", "Kubernetes"]
    assert [s.skill for s in strengths(stats)] == ["Python"]


def test_markdown_has_sections_and_scope():
    md = to_markdown(RUNS, aggregate(RUNS), cost_usd=0.01, created_at=datetime(2026, 11, 6, 14, 0))
    assert md.startswith("# 市場缺口報告（3 份職缺）")
    assert "| 1 | RAG | 3／3 | 0 | ❌ 沒有 ⚠️ 沒有 2／部分 1 | 10 | 01、02、03 |" in md  # 逐份加總：2×2 + 2×2 + 2×1
    assert "| 1 | Python | 2／3 | ✅ 有（working） | p1 |" in md
    assert "- **RAG**：01 沒有、02 沒有、03 部分" in md
    assert "| 03 | Job 03 | ACME | 1 |" in md

# 適合度：(有 + 0.5 × 部分) ÷ 必備數；只看必備
def test_job_fit_counts_only_required():
    f = job_fit(RUNS[1])                 # 02：RAG 沒有、Python 有（必備）；Docker 部分（加分，不算）
    assert (f.fit, f.n_required, f.missing_required) == (0.5, 2, 1)


def test_job_fit_without_required_uses_all():
    f = job_fit(run("09", m("Go", "partial", required=False), m("Rust", "have", required=False)))
    assert f.fit == 0.75 and f.n_required == 2


def test_rank_jobs_order_and_ties():
    # 01：(0 + 1 + 0.5)/3 = 0.5、缺 1；02：0.5、缺 1；03：(0.5)/1 = 0.5、缺 0 → 03 先（缺口少），01、02 依編號
    assert [f.job_id for f in rank_jobs(RUNS)] == ["03", "01", "02"]


def test_role_fit_average_and_min_jobs():
    fits = rank_jobs(RUNS)
    roles = {"01": "ml", "02": "ml", "03": "ai-app"}
    assert role_fit(fits, roles) == [("ml", 0.5, 2), ("ai-app", 0.5, 1)]   # 同分：份數多的在前
    assert role_fit(fits, roles, min_jobs=2) == [("ml", 0.5, 2)]            # 份數太少的不算
    assert role_fit(fits, {}) == []                                          # 沒有類別 → 空的


# 「A 或 B」（ADR-10）：一組算一個條件、缺口算在最接近的選項上
def g(skill, status, group, required=True, evidence=()):
    return MatchResult(skill=skill, required=required, status=status, evidence=list(evidence), any_of=group)


def test_job_fit_counts_a_group_as_one_condition():
    """必備：Python（有）、C#（沒有）、SQL（有）、Docker（部分），Python 和 C# 同一組。
    舊算法 (1 + 0 + 1 + 0.5) ÷ 4 = 0.625；新算法 (1 + 1 + 0.5) ÷ 3 = 0.833。"""
    r = run("01", g("Python", "have", "g1"), g("C#", "missing", "g1"), m("SQL", "have"), m("Docker", "partial"))
    f = job_fit(r)
    assert (f.fit, f.n_required, f.missing_required) == (0.833, 3, 0)


def test_priority_uses_shares():
    stats = {s.skill: s for s in aggregate([
        run("01", g("Python", "have", "g1"), g("C#", "missing", "g1")),      # 滿足 → C# 不算缺口
        run("02", g("Go", "partial", "g1"), g("Rust", "missing", "g1")),     # 缺口算在 Go 上
        run("03", g("Java", "missing", "g1"), g("Kotlin", "missing", "g1"), g("Scala", "missing", "g1")),
    ])}
    assert priority(stats["C#"]) == 0
    assert (priority(stats["Go"]), priority(stats["Rust"])) == (2, 0)
    assert priority(stats["Java"]) == 1.3                                     # 2 × 2 ÷ 3，取一位小數
    assert stats["C#"].any_of_in == ["01"]


def test_no_groups_means_nothing_changes():
    """沒有群組的資料：份額全部是 1 → 優先度、適合度和舊算法一樣（現有測試不用改就要通過）。"""
    stats = aggregate(RUNS)
    assert all(set(s.shares.values()) == {1.0} and not s.any_of_in for s in stats)
    assert "多選一" not in to_markdown(RUNS, stats, created_at=datetime(2026, 11, 23))


def test_markdown_marks_any_of_jobs():
    runs = [run("01", g("Python", "missing", "g1"), g("C#", "missing", "g1")), run("02", m("Python", "missing"))]
    md = to_markdown(runs, aggregate(runs), created_at=datetime(2026, 11, 23))
    assert "| Python | 2／2（多選一 1） |" in md