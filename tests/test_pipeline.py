"""網頁版用到的部分：樣本匯出、平行比對、頁面能不能打開。全部用假的 Claude。"""

import json
from pathlib import Path

from streamlit.testing.v1 import AppTest

from job_agent.pipeline import analyze
from job_agent.samples import export_samples, load_samples, strip_job
from job_agent.schemas import JobRequirements, Profile

APP = Path(__file__).parent.parent / "app.py"

JOB = {"title": "AI Engineer", "company": "ACME", "responsibilities": ["開發 RAG 系統"], "requirements": [
    {"skill": "Python", "original_text": "熟悉 Python", "category": "language", "required": True},
    {"skill": "LangGraph", "original_text": "會 LangGraph 加分", "category": "framework", "required": False},
]}
PROFILE = Profile.model_validate({
    "evidence": [{"id": "p", "type": "project", "summary": "x"}],
    "skills": [{"name": "Python", "level": "working", "evidence": ["p"]},
               {"name": "AI Agent", "level": "basic", "evidence": ["p"]}],
})
# ③ C 段的假回答：LangGraph 和 AI Agent 是相關技能
MAPPING = {"mappings": [{"requirement_skill": "LangGraph", "profile_skill": "AI Agent",
                         "relation": "related", "reason": "都是 agent"}]}


def test_strip_job_removes_posting_text():
    job = strip_job(JobRequirements.model_validate(JOB))
    assert all(r.original_text == "" for r in job.requirements)  # 職缺原文不公開
    assert job.responsibilities == []
    assert [r.skill for r in job.requirements] == ["Python", "LangGraph"]  # 比對需要的還在


def test_export_and_load_samples(tmp_path, monkeypatch, fake):
    monkeypatch.chdir(tmp_path)
    f = tmp_path / "02.txt"
    f.write_text("title: AI Engineer\ncompany: ACME\n---\n熟悉 Python\n", encoding="utf-8")
    out = export_samples([f], tmp_path / "samples" / "jobs.json", client=fake(JOB))

    assert "熟悉 Python" not in out.read_text(encoding="utf-8")  # 匯出的檔案裡沒有原文
    samples = load_samples(out)
    assert samples[0][0] == "02" and samples[0][1].company == "ACME"


def test_analyze_runs_all_jobs_and_keeps_order(fake):
    job = JobRequirements.model_validate(JOB)
    samples = [("02", job), ("05", job), ("09", job)]
    progress = []
    runs, cost, failed = analyze(PROFILE, samples, client=fake(MAPPING),
                                 on_progress=lambda done, n: progress.append((done, n)))

    assert [r.job_id for r in runs] == ["02", "05", "09"]      # 平行跑完，順序還是照樣本
    assert progress == [(1, 3), (2, 3), (3, 3)]               # 每做完一份回報一次
    assert cost > 0 and failed == []
    status = {m.skill: m.status for m in runs[0].results}
    assert status == {"Python": "have", "LangGraph": "partial"}


def test_analyze_keeps_going_when_a_job_fails(fake):
    job = JobRequirements.model_validate(JOB)
    runs, cost, failed = analyze(PROFILE, [("02", job), ("05", job)],
                                 client=fake(MAPPING, stop_reason="max_tokens"))  # 每次都被截斷 → LLMError
    assert runs == [] and failed == ["02", "05"] and cost == 0


def test_app_opens_and_lists_samples(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "samples").mkdir()
    (tmp_path / "samples" / "jobs.json").write_text(
        json.dumps([{"job_id": "02", "job": JOB}], ensure_ascii=False), encoding="utf-8")
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not at.exception
    assert "履歷技能缺口分析" in at.title[0].value
    assert at.button[0].disabled  # 還沒上傳履歷 → 按鈕不能按


def test_app_without_samples_shows_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not at.exception
    assert "找不到樣本職缺" in at.error[0].value


def test_app_shows_result_page(tmp_path, monkeypatch, fake):
    """假的分析結果放進 session_state → 結果頁（兩個分頁、表格、下載按鈕）都能正常顯示。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "samples").mkdir()
    (tmp_path / "samples" / "jobs.json").write_text(
        json.dumps([{"job_id": "02", "job": JOB}], ensure_ascii=False), encoding="utf-8")
    job = JobRequirements.model_validate(JOB)
    runs, cost, _ = analyze(PROFILE, [("02", job)], client=fake(MAPPING))

    at = AppTest.from_file(str(APP), default_timeout=30)
    at.session_state["result"] = {"profile": PROFILE, "notes": ["送出前遮掉聯絡資料 2 處（email、電話）"],
                                  "runs": runs, "failed": [], "cost": cost}
    at.run()
    assert not at.exception
    assert len(at.tabs) == 2
    assert any("市場缺口報告" in m.value for m in at.markdown)