"""① 拆解職缺：職缺文字檔 → JobRequirements。"""

import hashlib
import sys
from pathlib import Path

from job_agent.llm import call_structured, model_for
from job_agent.schemas import JobRequirements

CACHE_DIR = Path("cache")

SYSTEM = """You extract structured hiring requirements from a job posting. Postings may be in Chinese or English.

Rules:
- One item per skill. Split lists of DIFFERENT skills: "Python、C# 或任一後端語言" becomes separate items for Python and C#.
- But MERGE examples of the SAME kind of thing into one general skill. When a posting names examples in parentheses or after 如／例如／e.g./such as (vendors, providers, or products that serve the same purpose), output ONE item for the general skill, not one item per example. Keep the examples in original_text. E.g. "串接雲端 LLM（OpenAI、Claude、Gemini）" -> one item "LLM API Integration"; "向量資料庫（如 Pinecone、Milvus）" -> one item "Vector Database".
- How to tell them apart: if someone who knows one item could use the others within a few days (same kind of API or service), merge; if each item is a separate skill to learn (programming languages, different frameworks such as PyTorch vs TensorFlow), split.
- `skill`: a short canonical English name (e.g. 向量資料庫 -> "Vector Database", K8s -> "Kubernetes", 大型語言模型 -> "LLM").
- `original_text`: copy the exact phrase from the posting that mentions this skill. Do not translate it.
- `required`: true for must-have requirements; false for items marked as preferred, nice to have, 加分, 者佳, or similar.
- Read the whole posting, including the job description / responsibilities (工作內容), not only the qualifications. Technologies, tools, and techniques the role works with are requirements too (e.g. "開發 AI Agent、串接 MCP" -> "AI Agent", "MCP"). Mark them required=true unless the posting marks them as preferred.
- Include technical skills, tools, and concrete professional abilities (e.g. "Requirement Analysis", "Technical Writing").
- Skip soft skills and generic traits: teamwork, collaboration, communication, problem solving, analytical thinking, learning ability, passion, curiosity. Skip data formats such as JSON or YAML.
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


def extract_from_text(text: str, title: str = "", company: str = "", client=None) -> tuple[JobRequirements, dict]:
    """直接收職缺文字（網頁版會用這個）。"""
    user = f"Job title: {title}\nCompany: {company}\n\nJob posting:\n{text}"
    job, stats = call_structured(
        system=SYSTEM,
        user=user,
        schema=JobRequirements,
        name="extract",
        model=model_for("extract"),
        client=client,
    )
    # 職稱和公司以使用者給的為準，不用模型猜的
    job.title = title or job.title
    job.company = company or job.company
    return job, stats


def cache_path(path: str | Path, model: str) -> Path:
    """快取檔名 = 職缺檔名 + 指紋。職缺內容、拆解模型、拆解規則（SYSTEM）任何一個改了，指紋就變 → 自動重拆。"""
    fingerprint = hashlib.sha256((model + SYSTEM + Path(path).read_text(encoding="utf-8")).encode()).hexdigest()[:8]
    return CACHE_DIR / f"{Path(path).stem}_{fingerprint}.json"


def extract_requirements(path: str | Path, use_cache: bool = True, client=None) -> tuple[JobRequirements, dict]:
    """讀職缺檔 → 拆解。同一份職缺拆過就直接讀快取，不再花錢、結果也固定。"""
    model = model_for("extract")
    cache = cache_path(path, model)
    if use_cache and cache.exists():
        job = JobRequirements.model_validate_json(cache.read_text(encoding="utf-8"))
        return job, {"model": model, "input_tokens": 0, "output_tokens": 0,
                     "cost_usd": 0.0, "seconds": 0.0, "cached": True}

    meta, body = read_job(path)
    job, stats = extract_from_text(body, meta.get("title", ""), meta.get("company", ""), client)
    CACHE_DIR.mkdir(exist_ok=True)
    cache.write_text(job.model_dump_json(indent=2), encoding="utf-8")
    return job, {**stats, "cached": False}


def print_summary(job: JobRequirements, stats: dict) -> None:
    print(f"{job.title} @ {job.company}  (min years: {job.min_years})")
    print("-" * 60)
    for r in job.requirements:
        tag = "必備" if r.required else "加分"
        print(f"[{tag}] {r.skill:<28} {r.category:<10} ← {r.original_text}")
    print("-" * 60)
    print(f"requirements: {len(job.requirements)}  "
          f"(必備 {sum(r.required for r in job.requirements)} / 加分 {sum(not r.required for r in job.requirements)})")
    print(f"model: {stats['model']}{'（快取）' if stats['cached'] else ''}  "
          f"tokens: in {stats['input_tokens']} / out {stats['output_tokens']}  "
          f"cost: ${stats['cost_usd']:.4f}  time: {stats['seconds']}s")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m job_agent.extract jobs/01.txt")
    job, stats = extract_requirements(sys.argv[1])
    print_summary(job, stats)