"""pytest 會自動載入這個檔案：放所有測試共用的東西。"""

import json
from types import SimpleNamespace

import pytest


class FakeClient:
    """假的 Claude：不連網、不花錢，直接回傳指定的 JSON；被呼叫幾次記在 calls。"""

    def __init__(self, data, stop_reason="end_turn"):
        block = SimpleNamespace(type="text", text=json.dumps(data, ensure_ascii=False))
        usage = SimpleNamespace(input_tokens=1000, output_tokens=200)
        self.response = SimpleNamespace(content=[block], usage=usage, stop_reason=stop_reason)
        self.messages = self
        self.calls = 0
        self.last_request = None

    def create(self, **kwargs):
        self.calls += 1
        self.last_request = kwargs
        return self.response


@pytest.fixture
def fake():
    """測試裡寫 fake(資料) 就能建立一個假的 Claude。"""
    return FakeClient


@pytest.fixture(autouse=True)
def default_models(monkeypatch):
    """測試一律用預設模型，不受 .env 或終端機裡 EXTRACT_MODEL／MATCH_MODEL／RESUME_MODEL 的影響（費用測試才會準）；
    軌跡開關也回到預設（SAVE_TRAJECTORIES 沒設 = 存）。"""
    for step in ("EXTRACT", "MATCH", "RESUME"):
        monkeypatch.delenv(f"{step}_MODEL", raising=False)
    monkeypatch.delenv("SAVE_TRAJECTORIES", raising=False)