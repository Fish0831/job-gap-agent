from job_agent.report import build_report, gaps, save_markdown, suggestion, to_markdown
from job_agent.schemas import JobRequirements, MatchResult


def m(skill, status, required=True, evidence=(), level=None, reason="r"):
    return MatchResult(skill=skill, required=required, status=status,
                       evidence=list(evidence), level=level, reason=reason)


JOB = JobRequirements(title="AI Engineer", company="ACME", requirements=[])
RESULTS = [
    m("Python", "have", evidence=["p1"], level="working"),
    m("SQL", "missing", required=False),
    m("RAG", "partial", level="basic"),               # 有技能但沒證據
    m("MCP", "partial", evidence=["p1"], level="basic"),  # 相關技能
    m("Kubernetes", "missing"),
]


def test_gaps_are_sorted_by_priority():
    # 必備沒有 > 必備部分 > 加分沒有；已經有的不算缺口
    assert [g.skill for g in gaps(RESULTS)] == ["Kubernetes", "RAG", "MCP", "SQL"]


def test_suggestion_depends_on_status_and_evidence():
    assert "還沒學過" in suggestion(m("x", "missing"))
    assert "沒有作品證據" in suggestion(m("x", "partial"))
    assert "相關技能" in suggestion(m("x", "partial", evidence=["p1"]))


def test_markdown_contains_summary_top_gaps_and_table():
    report = build_report(JOB, RESULTS)
    assert report.top_gaps == ["Kubernetes", "RAG", "MCP", "SQL"]
    md = to_markdown(report, {"cost_usd": 0.0012, "exact": 2, "semantic": 3})
    assert md.startswith("# 缺口報告：AI Engineer @ ACME")
    assert "必備 4 項：✅ 有 1 ／ 🟡 部分 2 ／ ❌ 沒有 1" in md
    assert "1. **Kubernetes**（必備，❌ 沒有）" in md
    assert "| 必備 | Python | ✅ 有 | working | p1 | r |" in md
    assert "US$0.0012" in md


def test_pipe_in_reason_does_not_break_table():
    md = to_markdown(build_report(JOB, [m("A", "missing", reason="a | b")]))
    assert "a ／ b" in md


def test_save_markdown_writes_to_reports(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = save_markdown("# hi\n", "jobs/02.txt")
    assert path.parent.name == "reports" and path.name.startswith("02_")
    assert path.read_text(encoding="utf-8") == "# hi\n"