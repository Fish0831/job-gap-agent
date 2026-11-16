"""職務類別：職缺檔的 role:、匯出樣本、依類別篩選、網頁上選職務。全部用假的 Claude。"""

import json
from datetime import datetime
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from job_agent.market import aggregate, to_markdown
from job_agent.roles import ROLE_HINTS, ROLES, filter_role, role_of
from job_agent.samples import export_samples, load_roles, load_samples

APP = Path(__file__).parent.parent / "app.py"
JOB = {"title": "AI Engineer", "company": "ACME", "requirements": [
    {"skill": "Python", "original_text": "熟悉 Python", "category": "language", "required": True}]}


def write_job(folder, name, role):
    f = folder / name
    head = "title: AI Engineer\ncompany: ACME\n" + (f"role: {role}\n" if role else "")
    f.write_text(head + "---\n熟悉 Python\n", encoding="utf-8")
    return f


def test_every_role_has_a_hint():
    assert set(ROLE_HINTS) == set(ROLES)  # 新增類別時忘了寫說明 → 這裡會擋


def test_role_of_reads_header_and_rejects_bad_values(tmp_path):
    assert role_of(write_job(tmp_path, "02.txt", "ml")) == "ml"
    with pytest.raises(ValueError, match="03.txt"):              # 錯誤訊息要告訴我是哪個檔案
        role_of(write_job(tmp_path, "03.txt", "ML"))             # 大小寫打錯
    with pytest.raises(ValueError, match="04.txt"):
        role_of(write_job(tmp_path, "04.txt", None))             # 忘了寫


def test_filter_role(tmp_path):
    files = [write_job(tmp_path, "01.txt", "ai-app"), write_job(tmp_path, "02.txt", "ml"),
             write_job(tmp_path, "03.txt", "ai-app")]
    assert [f.name for f in filter_role(files, "ai-app")] == ["01.txt", "03.txt"]


def test_export_stops_before_calling_claude_when_a_role_is_missing(tmp_path, monkeypatch, fake):
    monkeypatch.chdir(tmp_path)
    files = [write_job(tmp_path, "01.txt", "ai-app"), write_job(tmp_path, "02.txt", None)]
    client = fake(JOB)
    with pytest.raises(ValueError, match="02.txt"):
        export_samples(files, tmp_path / "jobs.json", client=client)
    assert client.calls == 0                                     # 一份都還沒拆 → 沒花錢


def test_load_samples_by_role_and_old_format(tmp_path):
    out = tmp_path / "jobs.json"
    out.write_text(json.dumps([{"job_id": "01", "role": "ai-app", "job": JOB},
                               {"job_id": "02", "role": "ml", "job": JOB},
                               {"job_id": "03", "job": JOB}]),     # 舊格式：沒有 role
                   encoding="utf-8")
    assert [jid for jid, _ in load_samples(out)] == ["01", "02", "03"]
    assert [jid for jid, _ in load_samples(out, role="ml")] == ["02"]
    assert load_roles(out) == {"01": "ai-app", "02": "ml", "03": ""}


def test_market_markdown_title_shows_role():
    md = to_markdown([], aggregate([]), created_at=datetime(2026, 11, 16), role_label="ML 工程師")
    assert md.startswith("# 市場缺口報告：ML 工程師（0 份職缺）")


def samples_file(tmp_path, roles):
    (tmp_path / "samples").mkdir()
    data = [{"job_id": f"{i:02d}", "role": r, "job": JOB} for i, r in enumerate(roles, 1)]
    (tmp_path / "samples" / "jobs.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_app_role_choice_filters_samples(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    samples_file(tmp_path, ["ai-app", "ai-app", "ai-app", "ml", "ai-product"])  # ml、ai-product 太少
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not at.exception
    radio = at.radio[0]
    assert radio.options == ["全部職務（5 份）", "AI 應用／Agent 工程師（3 份）"]  # 少於 3 份的類別不列
    assert len(at.dataframe[0].value) == 5                       # 預設全部

    radio.set_value("ai-app").run()
    assert len(at.dataframe[0].value) == 3                       # 只剩 ai-app 的樣本
    assert ROLE_HINTS["ai-app"] in [c.value for c in at.caption]


def test_app_hides_role_choice_for_old_samples(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "samples").mkdir()
    (tmp_path / "samples" / "jobs.json").write_text(
        json.dumps([{"job_id": "01", "job": JOB}]), encoding="utf-8")  # 還沒標職務類別
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not at.exception
    assert len(at.radio) == 0                                    # 不顯示選項，照舊全部比對