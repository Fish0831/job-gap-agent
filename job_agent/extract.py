"""① 拆解職缺：職缺文字檔 → JobRequirements。"""

import sys
from pathlib import Path

from job_agent.llm import call_structured
from job_agent.schemas import JobRequirements

SYSTEM = """You extract structured hiring requirements from a job posting. Postings may be in Chinese or English.

Rules:
- One item per skill. Split lists: "Python、C# 或任一後端語言" becomes separate items for Python and C#.
- `skill`: a short canonical English name (e.g. 向量資料庫 -> "Vector Database", K8s -> "Kubernetes", 大型語言模型 -> "LLM").
- `original_text`: copy the exact phrase from the posting that mentions this skill. Do not translate it.
- `required`: true for must-have requirements; false for items marked as preferred, nice to have, 加分, 者佳, or similar.
- Include technical skills, tools, and concrete abilities (e.g. "Requirement Analysis", "Technical Writing"). Skip generic personality traits such as passion, curiosity, or being a team player.
- Do not add skills that the posting does not mention.
- `min_years`: the minimum years of experience if stated, otherwise null.
- `responsibilities`: short summaries of the main duties, at most 8."""


def read_job(path: str | Path) -> tuple[dict, str]:
    """讀職缺檔：`---` 上面是欄位（key: value），下面是職缺原文。"""
    text = Path(path).read_text(encoding="utf-8")
    header, sep, body = text.partition("\n---\n")
    if not sep:
        raise ValueError(f"{path}: missing '---' separator line")
    meta = {}
    for line in header.splitlines():
        key, _, value = line.partition(":")
        if key.strip():
            meta[key.strip()] = value.strip()
    return meta, body.strip()


def extract_requirements(path: str | Path) -> tuple[JobRequirements, dict]:
    meta, body = read_job(path)
    user = f"Job title: {meta.get('title', '')}\nCompany: {meta.get('company', '')}\n\nJob posting:\n{body}"
    job, stats = call_structured(
        system=SYSTEM,
        user=user,
        schema=JobRequirements,
        tool_name="record_job_requirements",
        tool_description="Record the structured requirements extracted from the job posting.",
    )
    # 職稱和公司以檔案開頭的欄位為準，不用模型猜的
    job.title = meta.get("title") or job.title
    job.company = meta.get("company") or job.company
    return job, stats


def print_summary(job: JobRequirements, stats: dict) -> None:
    print(f"{job.title} @ {job.company}  (min years: {job.min_years})")
    print("-" * 60)
    for r in job.requirements:
        tag = "必備" if r.required else "加分"
        print(f"[{tag}] {r.skill:<28} {r.category:<10} ← {r.original_text}")
    print("-" * 60)
    print(f"requirements: {len(job.requirements)}  "
          f"(必備 {sum(r.required for r in job.requirements)} / 加分 {sum(not r.required for r in job.requirements)})")
    print(f"tokens: in {stats['input_tokens']} / out {stats['output_tokens']}  "
          f"cost: ${stats['cost_usd']:.4f}  time: {stats['seconds']}s")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m job_agent.extract jobs/01.txt")
    job, stats = extract_requirements(sys.argv[1])
    print_summary(job, stats)
