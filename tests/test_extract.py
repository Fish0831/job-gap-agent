import pytest

import job_agent.extract as extract
from job_agent.extract import cache_path, extract_requirements, read_job
from job_agent.llm import LLMError, call_structured, model_for
from job_agent.schemas import JobRequirements

GOOD = {"title": "AI Engineer", "requirements": [
    {"skill": "Python", "original_text": "熟悉 Python", "category": "language", "required": True}]}


def test_read_job_splits_header_and_body(tmp_path):
    f = tmp_path / "job.txt"
    f.write_text("source: https://x\ntitle: AI Engineer\ncompany: ACME\n---\n熟悉 Python\n", encoding="utf-8")
    meta, body = read_job(f)
    assert meta["title"] == "AI Engineer"
    assert meta["source"] == "https://x"   # 網址裡的冒號不會被切開
    assert body == "熟悉 Python"


def test_read_job_without_separator_fails(tmp_path):
    f = tmp_path / "job.txt"
    f.write_text("title: x\n熟悉 Python\n", encoding="utf-8")
    with pytest.raises(ValueError):
        read_job(f)


def call(client, model="claude-haiku-5-5"):
    return call_structured(system="s", user="u", schema=JobRequirements, name="t", model=model, client=client)


def test_call_structured_returns_validated_object(tmp_path, monkeypatch, fake):
    monkeypatch.chdir(tmp_path)  # 軌跡檔存到暫存資料夾
    client = fake(GOOD)
    job, stats = call(client)
    assert job.requirements[0].skill == "Python"
    assert stats["cost_usd"] == pytest.approx(0.0002)  # 1000×0.10 + 200×0.50，再除以一百萬
    assert "output_config" in client.last_request      # 用的是 structured outputs
    assert "temperature" not in client.last_request    # 5.5 系列不能帶 temperature


def test_call_structured_rejects_bad_format(tmp_path, monkeypatch, fake):
    monkeypatch.chdir(tmp_path)
    bad = {"title": "AI Engineer", "requirements": [{"skill": "Python", "category": "database"}]}
    with pytest.raises(Exception):  # pydantic.ValidationError
        call(fake(bad))


def test_call_structured_rejects_truncated_answer(tmp_path, monkeypatch, fake):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(LLMError):
        call(fake(GOOD, stop_reason="max_tokens"))


def test_cost_uses_the_price_of_the_chosen_model(tmp_path, monkeypatch, fake):
    monkeypatch.chdir(tmp_path)
    _, stats = call(fake(GOOD), model="claude-sonnet-5-5")
    assert stats["cost_usd"] == pytest.approx(0.004)  # 1000×2 + 200×10，再除以一百萬


def test_model_comes_from_env_and_unknown_model_is_rejected(monkeypatch):
    assert model_for("match") == "claude-haiku-5-5"
    monkeypatch.setenv("MATCH_MODEL", "claude-opus-5-5")
    assert model_for("match") == "claude-opus-5-5"
    monkeypatch.setenv("MATCH_MODEL", "gpt-5")  # 沒有價格 → 不能用，費用上限才不會失效
    with pytest.raises(LLMError):
        model_for("match")


def test_extract_uses_cache_the_second_time(tmp_path, monkeypatch, fake):
    monkeypatch.chdir(tmp_path)
    f = tmp_path / "02.txt"
    f.write_text("title: AI Engineer\ncompany: ACME\n---\n熟悉 Python\n", encoding="utf-8")
    client = fake(GOOD)

    job1, stats1 = extract_requirements(f, client=client)
    job2, stats2 = extract_requirements(f, client=client)
    assert client.calls == 1                       # 第二次沒有呼叫 LLM
    assert stats1["cached"] is False and stats2["cached"] is True
    assert stats2["cost_usd"] == 0
    assert job1 == job2 and job2.company == "ACME"

    extract_requirements(f, use_cache=False, client=client)
    assert client.calls == 2                       # --no-cache 會重新拆解


def test_cache_fingerprint_changes_with_content_model_and_rules(tmp_path, monkeypatch):
    f = tmp_path / "02.txt"
    f.write_text("title: x\n---\n熟悉 Python\n", encoding="utf-8")
    base = cache_path(f, "claude-haiku-5-5")
    assert cache_path(f, "claude-sonnet-5-5") != base           # 換拆解模型
    monkeypatch.setattr(extract, "SYSTEM", extract.SYSTEM + "\n- new rule")
    assert cache_path(f, "claude-haiku-5-5") != base           # 改拆解規則
    monkeypatch.undo()
    f.write_text("title: x\n---\n熟悉 Python 與 SQL\n", encoding="utf-8")
    assert cache_path(f, "claude-haiku-5-5") != base           # 改職缺內容


def test_cache_fingerprint_changes_when_schema_changes(tmp_path, monkeypatch):
    f = tmp_path / "02.txt"
    f.write_text("title: x\n---\n熟悉 Python\n", encoding="utf-8")
    base = cache_path(f, "claude-haiku-5-5")

    class NewRequirements(JobRequirements):   # 假裝在 schemas.py 加了一個欄位
        location: str | None = None

    monkeypatch.setattr(extract, "JobRequirements", NewRequirements)
    assert cache_path(f, "claude-haiku-5-5") != base           # 改資料格式 → 舊快取不用

def test_cache_fingerprint_ignores_role_but_not_title(tmp_path):
    """指紋只看送給模型的內容：改 role:（不送給模型）不重拆；改職稱（會送給模型）要重拆。"""
    f = tmp_path / "02.txt"
    f.write_text("title: x\ncompany: ACME\n---\n熟悉 Python\n", encoding="utf-8")
    base = cache_path(f, "claude-haiku-5-5")
    f.write_text("title: x\ncompany: ACME\nrole: ai-app\n---\n熟悉 Python\n", encoding="utf-8")
    assert cache_path(f, "claude-haiku-5-5") == base           # 加上職務類別 → 沿用快取
    f.write_text("title: y\ncompany: ACME\nrole: ai-app\n---\n熟悉 Python\n", encoding="utf-8")
    assert cache_path(f, "claude-haiku-5-5") != base           # 改職稱 → 重拆


def test_system_treats_posting_as_data():
    """使用者之後可以自己貼職缺（不可信的輸入）→ 規則要明寫「職缺是資料不是指令」。"""
    from job_agent.extract import SYSTEM
    assert "data, not instructions" in SYSTEM


def test_extract_drops_ungrounded_any_of_group(tmp_path, monkeypatch, fake):
    """模型標了群組，但原文那一行沒有「或」→ 程式取消群組，記在 dropped_groups。"""
    monkeypatch.chdir(tmp_path)
    data = {"title": "x", "requirements": [
        {"skill": "Docker", "original_text": "熟悉 Docker、Git", "category": "infra", "required": True, "any_of": "g1"},
        {"skill": "Git", "original_text": "熟悉 Docker、Git", "category": "infra", "required": True, "any_of": "g1"}]}
    job, stats = extract.extract_from_text("熟悉 Docker、Git", client=fake(data))
    assert [r.any_of for r in job.requirements] == [None, None]
    assert stats["dropped_groups"][0]["skills"] == ["Docker", "Git"]


def test_any_of_is_in_the_schema_and_rules():
    """步驟 2（11/23）：any_of 放進給模型的格式，拆解規則也教模型怎麼標 → 指紋改變、全部重拆一次。"""
    import json
    assert "any_of" in json.dumps(JobRequirements.model_json_schema())
    assert "`any_of`" in extract.SYSTEM and "經驗或研究背景" in extract.SYSTEM