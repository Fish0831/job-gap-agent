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