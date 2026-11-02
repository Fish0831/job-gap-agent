from pathlib import Path
from types import SimpleNamespace

from job_agent.match import dedupe, exact_match, match
from job_agent.schemas import JobRequirements, Profile, Requirement

PROFILE = Profile.from_yaml(Path(__file__).parent.parent / "profile.example.yaml")


def req(skill, required=True):
    return Requirement(skill=skill, original_text=skill, category="other", required=required)


def job(*reqs):
    return JobRequirements(title="AI Engineer", requirements=list(reqs))


class FakeClient:
    """假的 Claude：回傳指定的 mapping；被呼叫幾次記在 calls。"""

    def __init__(self, mappings):
        block = SimpleNamespace(type="tool_use", input={"mappings": mappings})
        usage = SimpleNamespace(input_tokens=1000, output_tokens=200)
        self.response = SimpleNamespace(content=[block], usage=usage, stop_reason="tool_use")
        self.messages = self
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        return self.response


# A. 去重
def test_dedupe_keeps_one_and_prefers_required():
    result = dedupe([req("LLM", required=False), req("llm", required=True), req("Python")])
    assert [r.skill for r in result] == ["llm", "Python"]
    assert result[0].required is True


# B. 名稱完全對上（含 alias、不分大小寫）
def test_exact_match_uses_name_and_aliases():
    matched, unmatched = exact_match([req("python"), req("DRL"), req("LangChain")], PROFILE)
    assert matched["python"].name == "Python"
    assert matched["DRL"].name == "Reinforcement Learning"
    assert [r.skill for r in unmatched] == ["LangChain"]


# 全部名稱都對上 → 不呼叫 LLM、不花錢；D 段狀態規則
def test_all_exact_skips_llm_and_sets_status():
    client = FakeClient([])
    results, stats = match(job(req("Python"), req("Docker")), PROFILE, client)
    assert client.calls == 0 and stats["cost_usd"] == 0
    python, docker = results
    assert python.status == "have" and python.level == "working"
    assert python.evidence == ["token-cost-estimator", "master-thesis"]  # 從 profile 複製
    assert docker.status == "partial"  # 有這個技能但沒有作品證據


# C + D：LLM 的回答經過程式檢查
def test_semantic_mapping_is_checked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # 軌跡檔存到暫存資料夾
    client = FakeClient([
        {"requirement_skill": "Containerization", "profile_skill": "Docker", "relation": "same", "reason": "x"},
        {"requirement_skill": "PyTorch", "profile_skill": "Reinforcement Learning", "relation": "related", "reason": "x"},
        {"requirement_skill": "Kubernetes", "profile_skill": "Kubernetes", "relation": "same", "reason": "瞎掰"},
        {"requirement_skill": "Go", "profile_skill": None, "relation": "none", "reason": "x"},
    ])
    results, stats = match(
        job(req("Containerization"), req("PyTorch"), req("Kubernetes"), req("Go"), req("Rust")),
        PROFILE, client,
    )
    status = {m.skill: m.status for m in results}
    assert client.calls == 1 and stats["llm_called"]
    assert status["Containerization"] == "partial"  # same，但 Docker 沒有證據
    assert status["PyTorch"] == "partial"           # related
    assert status["Kubernetes"] == "missing"        # profile 裡沒有 → 不採信
    assert status["Go"] == "missing"
    assert status["Rust"] == "missing"              # LLM 漏回 → 當作沒有
    related = next(m for m in results if m.skill == "PyTorch")
    assert related.evidence == ["master-thesis"] and related.level == "strong"