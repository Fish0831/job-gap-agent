from types import SimpleNamespace

import pytest

from job_agent.extract import read_job
from job_agent.llm import LLMError, call_structured
from job_agent.schemas import JobRequirements


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


class FakeClient:
    """假的 Claude：不連網路，直接回傳指定的內容。"""

    def __init__(self, tool_input, stop_reason="tool_use"):
        block = SimpleNamespace(type="tool_use", input=tool_input)
        usage = SimpleNamespace(input_tokens=1000, output_tokens=200)
        self.response = SimpleNamespace(content=[block], usage=usage, stop_reason=stop_reason)
        self.messages = self

    def create(self, **kwargs):
        return self.response


def call(client):
    return call_structured(system="s", user="u", schema=JobRequirements,
                           tool_name="t", tool_description="d", client=client)


def test_call_structured_returns_validated_object(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # 軌跡檔存到暫存資料夾
    good = {"title": "AI Engineer", "requirements": [
        {"skill": "Python", "original_text": "熟悉 Python", "category": "language", "required": True}]}
    job, stats = call(FakeClient(good))
    assert job.requirements[0].skill == "Python"
    assert stats["cost_usd"] == pytest.approx(0.0002)  # 1000×0.10 + 200×0.50，再除以一百萬


def test_call_structured_rejects_bad_format(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bad = {"title": "AI Engineer", "requirements": [{"skill": "Python", "category": "database"}]}
    with pytest.raises(Exception):  # pydantic.ValidationError
        call(FakeClient(bad))


def test_call_structured_rejects_truncated_answer(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(LLMError):
        call(FakeClient({"title": "x", "requirements": []}, stop_reason="max_tokens"))
