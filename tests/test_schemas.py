import pytest
from pydantic import ValidationError

from job_agent.schemas import JobRequirements, Profile


def test_example_profile_loads():
    profile = Profile.from_yaml("profile.example.yaml")
    assert len(profile.evidence) == 2
    assert len(profile.skills) == 4
    assert profile.skills[0].name == "Python"


def test_unknown_evidence_id_is_rejected():
    data = {
        "evidence": [{"id": "a", "type": "project", "summary": "x"}],
        "skills": [{"name": "Python", "level": "basic", "evidence": ["typo-id"]}],
    }
    with pytest.raises(ValidationError, match="typo-id"):
        Profile.model_validate(data)


def test_invalid_level_is_rejected():
    data = {
        "evidence": [],
        "skills": [{"name": "Python", "level": "expert"}],
    }
    with pytest.raises(ValidationError):
        Profile.model_validate(data)


def test_job_requirements_from_dict():
    job = JobRequirements.model_validate({
        "title": "AI Engineer",
        "requirements": [
            {"skill": "Vector Database", "original_text": "向量資料庫", "category": "llm", "required": True},
            {"skill": "Kubernetes", "original_text": "K8s", "category": "infra", "required": False},
        ],
    })
    assert job.company is None
    assert job.requirements[1].required is False


def test_negative_years_is_rejected():
    with pytest.raises(ValidationError):
        JobRequirements.model_validate({"title": "x", "min_years": -1, "requirements": []})
