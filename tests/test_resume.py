"""履歷 → profile 草稿。全部用假的履歷文字＋假的 Claude，不放真履歷、不花錢。"""

import pytest
import yaml
from anthropic import transform_schema
from docx import Document
from pypdf import PdfWriter

from job_agent.llm import call_structured
from job_agent.resume import (ResumeDraft, check_text, compare_profiles, profile_from_text,
                              read_resume, to_yaml)
from job_agent.schemas import JobRequirements, Profile

FAKE_RESUME = (
    "王小明　AI 應用工程師\n"
    "專案：token-cost-estimator，用 Python 和 FastAPI 寫的 LLM 費用估算工具，有 pytest 測試。\n"
    "碩士論文：用 PyTorch 實作深度強化學習（DQN、PPO）做動態定價。\n"
    "技能：Python、Docker、K8s、溝通能力\n"
)
SYNONYMS = {"k8s": "Kubernetes", "kubernetes": "Kubernetes"}

# 假 LLM 故意犯的錯，看程式有沒有擋下來
DRAFT = {
    "evidence": [
        {"id": "Token Cost Estimator", "type": "project", "summary": "LLM 費用估算工具", "url": None},
        {"id": "master-thesis", "type": "research", "summary": "DRL 動態定價"},
    ],
    "skills": [
        {"name": "Python", "level": "working", "evidence": ["Token Cost Estimator", "master-thesis"]},
        {"name": "python", "level": "basic", "evidence": []},                    # 重複 → 合併
        {"name": "K8s", "level": "basic", "evidence": []},                       # → Kubernetes
        {"name": "Communication", "level": "basic", "evidence": []},             # 軟實力 → 去掉
        {"name": "Docker", "level": "working", "evidence": []},                  # 沒作品 → basic
        {"name": "PyTorch", "level": "working", "evidence": ["thesis-that-does-not-exist", "master-thesis"]},
    ],
}


# ===== A. 讀檔 =====

def test_read_txt_and_reject_unknown_format(tmp_path):
    f = tmp_path / "resume.txt"
    f.write_text(FAKE_RESUME, encoding="utf-8")
    assert "token-cost-estimator" in read_resume(f)
    doc = tmp_path / "resume.doc"
    doc.write_text(FAKE_RESUME, encoding="utf-8")
    with pytest.raises(ValueError):  # 舊版 .doc 不支援
        read_resume(doc)


def test_read_docx_includes_tables(tmp_path):
    d = Document()
    d.add_paragraph(FAKE_RESUME)
    table = d.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "技能"
    table.rows[0].cells[1].text = "LangGraph"
    d.save(tmp_path / "resume.docx")
    text = read_resume(tmp_path / "resume.docx")
    assert "技能 | LangGraph" in text  # 表格裡的字也讀得到


def test_scanned_pdf_without_text_is_rejected(tmp_path):
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)  # 沒有文字的頁面 ≈ 掃描檔
    with open(tmp_path / "scan.pdf", "wb") as f:
        writer.write(f)
    with pytest.raises(ValueError, match="掃描檔"):
        read_resume(tmp_path / "scan.pdf")


def test_check_text_limits():
    with pytest.raises(ValueError):
        check_text("太短")
    with pytest.raises(ValueError):
        check_text("字" * 20_001)
    assert check_text("a\n\n\n\n\nb" + "c" * 200).startswith("a\n\nb")  # 多餘空行壓成一行


# ===== B＋C. 整理＋清理 =====

def test_draft_schema_works_with_structured_outputs():
    schema = transform_schema(ResumeDraft)  # 不支援的格式這裡就會報錯
    level = schema["$defs"]["DraftSkill"]["properties"]["level"]
    assert "strong" not in str(level)       # 格式本身就不允許 strong


def test_profile_from_text_cleans_llm_mistakes(tmp_path, monkeypatch, fake):
    monkeypatch.chdir(tmp_path)
    client = fake(DRAFT)
    profile, notes, stats = profile_from_text(FAKE_RESUME, client=client, synonyms=SYNONYMS)
    skills = {s.name: s for s in profile.skills}

    assert [e.id for e in profile.evidence] == ["token-cost-estimator", "master-thesis"]  # kebab-case
    assert skills["Python"].evidence == ["token-cost-estimator", "master-thesis"]          # id 跟著改
    assert skills["Python"].level == "working"                                            # 合併取高的
    assert "Kubernetes" in skills and "K8s" in skills["Kubernetes"].aliases               # 名稱統一
    assert "Communication" not in skills                                                  # 軟實力
    assert skills["Docker"].level == "basic"                                              # 沒作品 → basic
    assert skills["PyTorch"].evidence == ["master-thesis"]                                # 假 id 刪掉
    assert any("thesis-that-does-not-exist" in n for n in notes)

    assert client.last_request["model"] == "claude-haiku-5-5"
    assert FAKE_RESUME.strip() in client.last_request["messages"][0]["content"]
    assert not (tmp_path / "trajectories").exists()                                       # 履歷不存軌跡


def test_draft_yaml_reads_back_as_profile(fake, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    profile, _, _ = profile_from_text(FAKE_RESUME, client=fake(DRAFT), synonyms=SYNONYMS)
    path = tmp_path / "draft.yaml"
    path.write_text(to_yaml(profile), encoding="utf-8")
    assert Profile.from_yaml(path) == profile  # 存成 YAML 再讀回來一模一樣 → 可以直接 --profile


def test_compare_profiles_matches_by_name_alias_and_synonym():
    draft = Profile.model_validate({"evidence": [{"id": "p", "type": "project", "summary": "x"}], "skills": [
        {"name": "Python", "level": "working", "evidence": ["p"]},
        {"name": "Kubernetes", "level": "basic"},
        {"name": "Excel", "level": "basic"},
    ]})
    ref = Profile.model_validate({"evidence": [], "skills": [
        {"name": "python", "level": "working"},
        {"name": "K8s", "level": "basic"},
        {"name": "SQL", "level": "basic"},
    ]})
    result = compare_profiles(draft, ref, SYNONYMS)
    assert [(d.name, r.name) for d, r in result["both"]] == [("Python", "python"), ("Kubernetes", "K8s")]
    assert [s.name for s in result["only_draft"]] == ["Excel"]
    assert [s.name for s in result["only_ref"]] == ["SQL"]


# ===== llm.py 的軌跡開關 =====

def test_trajectory_switch(tmp_path, monkeypatch, fake):
    monkeypatch.chdir(tmp_path)
    data = {"title": "x", "requirements": []}
    args = dict(system="s", user="u", schema=JobRequirements, name="t", client=fake(data))

    call_structured(**args, save_trajectory=False)
    assert not (tmp_path / "trajectories").exists()       # 參數關掉

    monkeypatch.setenv("SAVE_TRAJECTORIES", "0")
    call_structured(**args)
    assert not (tmp_path / "trajectories").exists()       # 環境變數關掉（網頁版用）

    monkeypatch.delenv("SAVE_TRAJECTORIES")
    call_structured(**args)
    assert len(list((tmp_path / "trajectories").iterdir())) == 1  # 預設照舊會存