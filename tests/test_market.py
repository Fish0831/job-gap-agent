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


def test_priority_is_demand_times_gap_weight():
    stats = {s.skill: s for s in aggregate(RUNS)}
    assert priority(stats["RAG"]) == (2 * 3) * 2         # 必備 3 份 × 沒有
    assert priority(stats["Docker"]) == (2 * 1 + 1) * 1  # 必備 1 + 加分 1 × 部分
    assert priority(stats["Kubernetes"]) == 1 * 2        # 加分 1 × 沒有
    assert priority(stats["Python"]) == 0                # 有 → 不是缺口


def test_top_gaps_and_strengths():
    stats = aggregate(RUNS)
    assert [s.skill for s in top_gaps(stats)] == ["RAG", "Docker", "Kubernetes"]
    assert [s.skill for s in strengths(stats)] == ["Python"]


def test_markdown_has_sections_and_scope():
    md = to_markdown(RUNS, aggregate(RUNS), cost_usd=0.01, created_at=datetime(2026, 11, 6, 14, 0))
    assert md.startswith("# 市場缺口報告（3 份職缺）")
    assert "| 1 | RAG | 3／3 | 0 | ❌ 沒有 ⚠️ 沒有 2／部分 1 | 12 | 01、02、03 |" in md
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