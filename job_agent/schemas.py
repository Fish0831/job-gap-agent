"""資料格式：職缺需求、我的技能檔、比對結果、缺口報告。"""

from datetime import datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

Level = Literal["basic", "working", "strong"]
Status = Literal["have", "partial", "missing"]
Category = Literal["language", "framework", "llm", "ml", "infra", "data", "soft", "other"]


# ===== 職缺（LLM 拆解出來的結果）=====

class Requirement(BaseModel):
    skill: str = Field(
        description="Canonical English skill name, e.g. 'Vector Database' for 向量資料庫, 'Kubernetes' for K8s"
    )
    original_text: str = Field(description="The exact phrase from the job posting")
    category: Category
    required: bool = Field(description="True if must-have; False if nice-to-have / preferred / 加分")


class JobRequirements(BaseModel):
    title: str
    company: str | None = None
    min_years: int | None = Field(default=None, ge=0, description="Minimum years of experience, null if not stated")
    requirements: list[Requirement]
    responsibilities: list[str] = Field(default_factory=list)


# ===== 我的技能檔（profile.yaml）=====

class Evidence(BaseModel):
    id: str
    type: Literal["project", "work", "presentation", "research", "award"]
    summary: str
    url: str | None = None


class ProfileSkill(BaseModel):
    name: str
    level: Level
    aliases: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class Profile(BaseModel):
    evidence: list[Evidence]
    skills: list[ProfileSkill]

    @model_validator(mode="after")
    def check_evidence_ids(self):
        ids = {e.id for e in self.evidence}
        unknown = [(s.name, eid) for s in self.skills for eid in s.evidence if eid not in ids]
        if unknown:
            raise ValueError(f"unknown evidence ids: {unknown}")
        return self

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Profile":
        with open(path, encoding="utf-8") as f:
            return cls.model_validate(yaml.safe_load(f))


# ===== 比對結果與報告 =====

class MatchResult(BaseModel):
    skill: str
    required: bool
    status: Status
    evidence: list[str] = Field(default_factory=list)
    level: Level | None = None
    reason: str = ""
    how: Literal["name", "same", "related", "none"] = "none"  # name = 程式名稱對上；其他是 LLM 的判斷
    matched_to: str | None = None  # 對應到 profile 的哪個技能


class GapReport(BaseModel):
    job: JobRequirements
    matches: list[MatchResult]
    top_gaps: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)