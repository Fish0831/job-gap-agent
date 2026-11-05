from pathlib import Path

from job_agent.compare import compare, differing_skills, to_markdown
from job_agent.schemas import JobRequirements, Profile, Requirement

PROFILE = Profile.from_yaml(Path(__file__).parent.parent / "profile.example.yaml")
JOB = JobRequirements(title="AI Engineer", company="ACME", requirements=[
    Requirement(skill="Python", original_text="Python", category="language", required=True),
    Requirement(skill="Containerization", original_text="容器化", category="infra", required=True),
    Requirement(skill="LangChain", original_text="LangChain", category="llm", required=False),
])


def mapping(skill, profile_skill, relation):
    return {"requirement_skill": skill, "profile_skill": profile_skill, "relation": relation, "reason": f"{relation} 的理由"}


class ByModel:
    """依照請求裡的 model，交給不同的假 Claude 回答。"""

    def __init__(self, clients):
        self.clients = clients
        self.messages = self

    def create(self, **kwargs):
        return self.clients[kwargs["model"]].create(**kwargs)


def test_compare_marks_skills_where_models_disagree(tmp_path, monkeypatch, fake):
    monkeypatch.chdir(tmp_path)
    client = ByModel({
        "claude-haiku-5-5": fake({"mappings": [mapping("Containerization", "Docker", "same"),
                                               mapping("LangChain", None, "none")]}),
        "claude-sonnet-5-5": fake({"mappings": [mapping("Containerization", "Docker", "same"),
                                                mapping("LangChain", "Python", "related")]}),
    })
    runs = compare(JOB, PROFILE, ["claude-haiku-5-5", "claude-sonnet-5-5"], client=client)

    assert differing_skills(runs) == ["LangChain"]  # Python 名稱對上、Containerization 兩個一樣
    md = to_markdown(JOB, runs)
    assert "共 1 項" in md
    assert "| ⚠️ | 加分 | LangChain | ❌ 沒有 | 🟡 部分（related → Python） |" in md
    assert "**sonnet-5-5**：🟡 部分（related → Python） — 相關技能 Python：related 的理由" in md
    assert "| haiku-5-5 | 1／1／0 |" in md  # 必備：Python 有、Containerization 部分（Docker 沒證據）


def test_failed_model_does_not_stop_others(tmp_path, monkeypatch, fake):
    monkeypatch.chdir(tmp_path)
    good = fake({"mappings": [mapping("Containerization", "Docker", "same"), mapping("LangChain", None, "none")]})
    runs = compare(JOB, PROFILE, ["claude-haiku-5-5", "gpt-5"], client=good)
    assert runs["claude-haiku-5-5"][0] is not None
    assert runs["gpt-5"][0] is None and "unknown model" in runs["gpt-5"][1]["error"]
    assert "❌ 失敗" in to_markdown(JOB, runs)