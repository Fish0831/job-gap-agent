from pathlib import Path

from job_agent.match import dedupe, exact_match, match
from job_agent.normalize import key
from job_agent.schemas import JobRequirements, Profile, Requirement

PROFILE = Profile.from_yaml(Path(__file__).parent.parent / "profile.example.yaml")


def req(skill, required=True):
    return Requirement(skill=skill, original_text=skill, category="other", required=required)


def job(*reqs):
    return JobRequirements(title="AI Engineer", requirements=list(reqs))


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
def test_all_exact_skips_llm_and_sets_status(fake):
    client = fake({"mappings": []})
    results, stats = match(job(req("Python"), req("Docker")), PROFILE, client)
    assert client.calls == 0 and stats["cost_usd"] == 0
    python, docker = results
    assert python.status == "have" and python.level == "working"
    assert python.evidence == ["token-cost-estimator", "master-thesis"]  # 從 profile 複製
    assert docker.status == "partial"  # 有這個技能但沒有作品證據


# C + D：LLM 的回答經過程式檢查
def test_semantic_mapping_is_checked(tmp_path, monkeypatch, fake):
    monkeypatch.chdir(tmp_path)  # 軌跡檔存到暫存資料夾
    client = fake({"mappings": [
        {"requirement_skill": "Containerization", "profile_skill": "Docker", "relation": "same", "reason": "x"},
        {"requirement_skill": "PyTorch", "profile_skill": "Reinforcement Learning", "relation": "related", "reason": "x"},
        {"requirement_skill": "Kubernetes", "profile_skill": "Kubernetes", "relation": "same", "reason": "瞎掰"},
        {"requirement_skill": "Go", "profile_skill": None, "relation": "none", "reason": "x"},
    ]})
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

# A. 統一名稱：三家 API 變成一個技能，而且名稱直接對上 profile（不問 LLM）
def test_synonyms_merge_examples_and_match_by_name(fake):
    client = fake({"mappings": []})
    syn = {key("OpenAI API"): "Python", key("Gemini API"): "Python"}  # 用範例 profile 有的技能來測
    results, stats = match(job(req("OpenAI API"), req("Gemini API", required=False), req("python")), PROFILE, client,
                           synonyms=syn)
    assert client.calls == 0
    assert len(results) == 1 and results[0].skill == "Python" and results[0].required is True
    assert results[0].how == "name"


# B. 寫法差異（大小寫、-、空白）也算名稱對上
def test_exact_match_ignores_spelling_differences():
    matched, unmatched = exact_match([req("reinforcement-learning"), req("Fast API")], PROFILE, {})
    assert matched["reinforcement-learning"].name == "Reinforcement Learning"
    assert matched["Fast API"].name == "FastAPI"
    assert unmatched == []