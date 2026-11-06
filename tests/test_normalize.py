import pytest

from job_agent.normalize import SYNONYMS_FILE, canonical, key, load_synonyms, unify
from job_agent.schemas import Requirement


def req(skill, required=True):
    return Requirement(skill=skill, original_text=skill, category="other", required=required)


# 第 1 層：寫法差異
def test_key_ignores_case_spaces_and_punctuation():
    assert key("Scikit-Learn") == key("scikit-learn") == key("scikit learn") == key("ＳｃｉｋｉｔＬｅａｒｎ")
    assert key("Node.js") == key("NodeJS")
    assert key("C++") != key("C") and key("C#") != key("C")  # + 和 # 不能去掉


# 第 2 層：同義詞表
def test_synonyms_map_variants_to_one_name(tmp_path):
    f = tmp_path / "syn.yaml"
    f.write_text("LLM API Integration: [OpenAI API, Gemini API]\nKubernetes: [K8s]\n", encoding="utf-8")
    syn = load_synonyms(f)
    assert canonical("gemini-api", syn) == "LLM API Integration"
    assert canonical("k8s", syn) == "Kubernetes"
    assert canonical("llm api integration", syn) == "LLM API Integration"  # 統一名稱本身也統一大小寫
    assert canonical("PyTorch", syn) == "PyTorch"                          # 不在表裡 → 不變


def test_same_variant_under_two_names_is_rejected(tmp_path):
    f = tmp_path / "syn.yaml"
    f.write_text("A: [X]\nB: [x]\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_synonyms(f)


def test_missing_file_means_no_synonyms(tmp_path):
    assert load_synonyms(tmp_path / "nope.yaml") == {}


def test_unify_keeps_original_text():
    syn = {key("OpenAI API"): "LLM API Integration"}
    r = unify([Requirement(skill="OpenAI API", original_text="串接 OpenAI", category="llm", required=True)], syn)[0]
    assert r.skill == "LLM API Integration" and r.original_text == "串接 OpenAI"


def test_repo_synonyms_file_loads():
    syn = load_synonyms(SYNONYMS_FILE)  # 公開的 skill_synonyms.yaml 格式正確、沒有重複
    assert canonical("Claude API", syn) == "LLM API Integration"