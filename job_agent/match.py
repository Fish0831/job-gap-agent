"""③ 比對：JobRequirements + Profile → list[MatchResult]。

A. 去重（程式）       同一個技能只留一筆，有必備就算必備
B. 名稱完全對上（程式） 職缺技能 = profile 的 name 或 aliases → 不呼叫 LLM
C. 語意對應（LLM）     剩下的一次丟給 Claude：對應到哪個 profile 技能、same / related / none
D. 檢查＋決定狀態（程式）LLM 給的技能名稱要真的存在；evidence 和 level 從 profile 複製
"""

import sys
from typing import Literal

from pydantic import BaseModel, Field

from job_agent.llm import call_structured, model_for
from job_agent.schemas import JobRequirements, MatchResult, Profile, ProfileSkill, Requirement

Relation = Literal["same", "related", "none"]


# ===== C 段給 LLM 用的格式 =====

class SkillMapping(BaseModel):
    requirement_skill: str = Field(description="The job requirement skill, copied exactly from the list")
    profile_skill: str | None = Field(
        description="Exact name of the matching skill from the candidate's skill list, or null if none"
    )
    relation: Relation = Field(
        description="same = the same skill under another name; related = adjacent skill that partly transfers; none = no match"
    )
    reason: str = Field(description="One short sentence in Traditional Chinese explaining the judgement")


class MappingResult(BaseModel):
    mappings: list[SkillMapping]


SYSTEM = """You map job requirement skills to a candidate's skill list.

For each requirement skill, pick at most one skill from the candidate's list and a relation:
- "same": the same skill under a different name, abbreviation, or language (e.g. "K8s" vs "Kubernetes", "LLM Application Development" vs "LLM API Integration").
- "related": a different but adjacent skill that partly transfers (e.g. requirement "LangChain", candidate has "AI Agent").
- "none": nothing in the list is meaningfully related. Set profile_skill to null.

Rules:
- Return exactly one mapping per requirement skill, copying requirement_skill exactly.
- profile_skill must be copied exactly from the candidate's list. Never invent a skill.
- Be conservative: if unsure between "same" and "related", choose "related"; if unsure between "related" and "none", choose "none".
- Judge only whether the skills match, not how good the candidate is.
- Write `reason` in Traditional Chinese (繁體中文), one short sentence. Keep skill names in English."""


# ===== A. 去重 =====

def dedupe(requirements: list[Requirement]) -> list[Requirement]:
    """同一個 skill（不分大小寫）只留一筆；只要有一筆是必備，就算必備。"""
    kept: dict[str, Requirement] = {}
    for r in requirements:
        key = r.skill.strip().lower()
        if key not in kept:
            kept[key] = r
        elif r.required and not kept[key].required:
            kept[key] = r  # 換成必備那一筆（original_text 也跟著換）
    return list(kept.values())


# ===== B. 名稱完全對上 =====

def build_index(profile: Profile) -> dict[str, ProfileSkill]:
    """把 name 和每個 alias 都轉小寫，指到同一個 ProfileSkill。"""
    index = {}
    for s in profile.skills:
        for name in [s.name, *s.aliases]:
            index[name.strip().lower()] = s
    return index


def exact_match(requirements, profile) -> tuple[dict[str, ProfileSkill], list[Requirement]]:
    """回傳（對上的：職缺技能 → ProfileSkill, 沒對上的需求）。"""
    index = build_index(profile)
    matched, unmatched = {}, []
    for r in requirements:
        hit = index.get(r.skill.strip().lower())
        if hit:
            matched[r.skill] = hit
        else:
            unmatched.append(r)
    return matched, unmatched


# ===== C. 語意對應 =====

def semantic_map(unmatched: list[Requirement], profile: Profile, client=None, model: str | None = None) -> tuple[MappingResult, dict]:
    skills = "\n".join(
        f"- {s.name}" + (f" (also called: {', '.join(s.aliases)})" if s.aliases else "")
        for s in profile.skills
    )
    reqs = "\n".join(f"- {r.skill}  (posting says: {r.original_text})" for r in unmatched)
    user = f"Candidate's skill list:\n{skills}\n\nJob requirement skills to map:\n{reqs}"
    return call_structured(
        system=SYSTEM,
        user=user,
        schema=MappingResult,
        name="match",
        model=model or model_for("match"),
        client=client,
    )


# ===== D. 檢查＋決定狀態 =====

def decide(req: Requirement, skill: ProfileSkill | None, how: str, reason: str) -> MatchResult:
    """狀態規則全部在這裡：LLM 只給關係，證據和熟練度一律從 profile 拿。
    how：name = 程式名稱對上；same／related／none = LLM 的判斷。"""
    if skill is None or how == "none":
        return MatchResult(skill=req.skill, required=req.required, status="missing", reason=reason, how="none")
    if how in ("name", "same") and skill.evidence:
        status = "have"
    else:
        status = "partial"  # 同一個技能但沒作品證據，或只是相關技能
    return MatchResult(
        skill=req.skill, required=req.required, status=status,
        evidence=list(skill.evidence), level=skill.level, reason=reason,
        how=how, matched_to=skill.name,
    )


def match(job: JobRequirements, profile: Profile, client=None, model: str | None = None) -> tuple[list[MatchResult], dict]:
    """model：比對用哪個模型；不給就照 MATCH_MODEL 設定（模型比較工具會指定）。"""
    reqs = dedupe(job.requirements)
    matched, unmatched = exact_match(reqs, profile)

    mappings: dict[str, SkillMapping] = {}
    stats = {"cost_usd": 0.0, "llm_called": False, "exact": len(matched), "semantic": len(unmatched)}
    if unmatched:  # 全部都對上就不花錢
        result, llm_stats = semantic_map(unmatched, profile, client, model)
        stats.update(llm_stats, llm_called=True)
        mappings = {m.requirement_skill.strip().lower(): m for m in result.mappings}

    by_name = {s.name: s for s in profile.skills}
    results = []
    for r in reqs:
        if r.skill in matched:
            skill = matched[r.skill]
            results.append(decide(r, skill, "name", f"名稱對上 {skill.name}"))
            continue
        m = mappings.get(r.skill.strip().lower())
        if m is None:
            results.append(decide(r, None, "none", "LLM 沒有回這個技能"))
        elif m.relation != "none" and m.profile_skill not in by_name:
            # 事實層檢查：LLM 說的技能不在我的 profile 裡 → 不採信
            results.append(decide(r, None, "none", f"LLM 對應到不存在的技能「{m.profile_skill}」，不採用"))
        else:
            skill = by_name.get(m.profile_skill) if m.profile_skill else None
            label = {"same": f"對應 {m.profile_skill}：", "related": f"相關技能 {m.profile_skill}：", "none": ""}
            results.append(decide(r, skill, m.relation, label[m.relation] + m.reason))
    return results, stats


# ===== CLI =====

ICON = {"have": "✅ 有", "partial": "🟡 部分", "missing": "❌ 沒有"}
ORDER = {"missing": 0, "partial": 1, "have": 2}


def print_table(job: JobRequirements, results: list[MatchResult]) -> None:
    print(f"{job.title} @ {job.company}")
    print("-" * 78)
    for m in sorted(results, key=lambda m: (not m.required, ORDER[m.status])):
        tag = "必備" if m.required else "加分"
        status = ICON[m.status] + (f"（{m.level}）" if m.level else "")
        print(f"[{tag}] {m.skill:<26} {status:<16} {m.reason}")
    print("-" * 78)
    for label, req in [("必備", True), ("加分", False)]:
        group = [m for m in results if m.required == req]
        counts = {s: sum(m.status == s for m in group) for s in ICON}
        print(f"{label} {len(group)}：有 {counts['have']}／部分 {counts['partial']}／沒有 {counts['missing']}")


if __name__ == "__main__":
    from job_agent.extract import extract_requirements

    if len(sys.argv) not in (2, 3):
        sys.exit("usage: python -m job_agent.match jobs/02.txt [profile.yaml]")
    profile = Profile.from_yaml(sys.argv[2] if len(sys.argv) == 3 else "profile.yaml")
    job, extract_stats = extract_requirements(sys.argv[1])
    results, match_stats = match(job, profile)
    print_table(job, results)
    total = extract_stats["cost_usd"] + match_stats["cost_usd"]
    print(f"名稱直接對上 {match_stats['exact']} 個、交給 LLM {match_stats['semantic']} 個（{match_stats.get('model', '沒有呼叫 LLM')}）")
    print(f"cost: 拆解 ${extract_stats['cost_usd']:.4f} + 比對 ${match_stats['cost_usd']:.4f} = ${total:.4f}")