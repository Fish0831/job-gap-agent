"""L0：只讀公開的 samples/jobs.json，不花錢（docs/evaluation.md 第 1.1、4、6.2 節）。

python -m evals.samples_eval          → 印出檢查表，並存到 reports/eval/<時間>_samples.md
python -m evals.samples_eval --batch 10 → 「最後 10 份」當成新的一批，看加進來之後變多少

看什麼：
- 每份職缺的必備比例（100% 必備、必備少於 3 個的要人工看）
- 被模型標成 soft、但不在 SOFT_SKILLS 名單的項目（11/17 起會保留，這裡只列出來看）
- 命名切碎：不同名稱數、只出現 1 份的比例、同義詞候選
- 需求前 10 名（demand：必備 2、加分 1；不看 profile、不經過比對 LLM）
- 樣本夠不夠：bootstrap 前 10 名重疊、每加一批前 10 名換幾個、新技能有沒有在舊樣本出現過
"""

import argparse
import difflib
import random
from datetime import datetime
from pathlib import Path

from evals.metrics import mean, overlap_at_k, pct
from job_agent.match import dedupe
from job_agent.normalize import (
    SOFT_SKILLS,
    drop_soft,
    key,
    load_synonyms,
    unify,
)
from job_agent.roles import ROLES
from job_agent.samples import SAMPLES_FILE, load_roles, load_samples
from job_agent.schemas import JobRequirements, Requirement

OUT_DIR = Path("reports/eval")
BOOTSTRAP = 1000
TOP_K = 10


def processed(job: JobRequirements, synonyms: dict[str, str]) -> list[Requirement]:
    """和比對用同一套規則：去軟實力 → 統一名稱 → 同一份職缺重複的合併。"""
    return dedupe(unify(drop_soft(job.requirements), synonyms))


def demand_table(jobs: list[tuple[str, list[Requirement]]]) -> dict[str, dict]:
    """依技能彙總需求：{key: {name, demand, required_in, preferred_in}}。"""
    table: dict[str, dict] = {}
    for jid, reqs in jobs:
        for r in reqs:
            row = table.setdefault(key(r.skill), {"name": r.skill, "demand": 0, "required_in": [], "preferred_in": []})
            row["demand"] += 2 if r.required else 1
            (row["required_in"] if r.required else row["preferred_in"]).append(jid)
    return table


def top_by_demand(table: dict[str, dict], k: int = TOP_K) -> list[str]:
    """需求分數高的在前；同分照名稱排（結果固定）。"""
    return [kk for kk, _ in sorted(table.items(), key=lambda kv: (-kv[1]["demand"], kv[1]["name"].lower()))][:k]


def bootstrap_top10(jobs: list[tuple[str, list[Requirement]]], n: int = BOOTSTRAP, seed: int = 0) -> tuple[float, int]:
    """把職缺有放回地重抽 n 次 → 每次的前 10 名和全樣本前 10 名重疊幾個。回傳（平均、第 10 百分位）。"""
    if not jobs:
        return float("nan"), 0
    base = top_by_demand(demand_table(jobs))
    rnd = random.Random(seed)
    scores = sorted(overlap_at_k(base, top_by_demand(demand_table(rnd.choices(jobs, k=len(jobs))))) for _ in range(n))
    return mean(scores), scores[n // 10]


def batch_change(jobs: list[tuple[str, list[Requirement]]], batch: int) -> dict:
    """把最後 batch 份當成新的一批：前 10 名換幾個、新一批的技能有多少在舊樣本出現過、新技能有幾個出現 2 份以上。"""
    old, new = jobs[:-batch], jobs[-batch:]
    old_t, all_t = demand_table(old), demand_table(jobs)
    old_keys = set(old_t)
    items = [(key(r.skill), r.required) for _, reqs in new for r in reqs]
    new_counts: dict[str, int] = {}
    for _, reqs in new:
        for k in {key(r.skill) for r in reqs} - old_keys:
            new_counts[k] = new_counts.get(k, 0) + 1
    req_items = [k for k, req in items if req]
    return {
        "top10_churn": TOP_K - overlap_at_k(top_by_demand(old_t), top_by_demand(all_t)),
        "seen_before": pct(sum(k in old_keys for k, _ in items), len(items)),
        "seen_before_required": pct(sum(k in old_keys for k in req_items), len(req_items)),
        "new_recurring": sum(v >= 2 for v in new_counts.values()),
        "new_total": len(new_counts),
    }


def synonym_candidates(names: dict[str, int], ratio: float = 0.85, limit: int = 40) -> list[tuple[str, str, str]]:
    """可能是同一個技能的名稱配對：一個名稱的 key 包含另一個（至少 3 個字元），或字串相似度 ≥ ratio。
    只是候選，會有誤報（例如 LLM／vLLM），由人決定要不要寫進 skill_synonyms.yaml。"""
    keys = {key(n): n for n in names}
    out = []
    ks = sorted(keys)
    for i, a in enumerate(ks):
        for b in ks[i + 1:]:
            short, long_ = sorted((a, b), key=len)
            if len(short) >= 3 and short in long_:
                why = "包含"
            elif difflib.SequenceMatcher(None, a, b).ratio() >= ratio:
                why = "相似"
            else:
                continue
            out.append((keys[a], keys[b], why))
    out.sort(key=lambda t: -(names[t[0]] + names[t[1]]))
    return out[:limit]


def evaluate(path: str | Path = SAMPLES_FILE, batch: int | None = None, synonyms: dict[str, str] | None = None) -> dict:
    synonyms = load_synonyms() if synonyms is None else synonyms
    samples, roles = load_samples(path), load_roles(path)
    jobs = [(jid, processed(job, synonyms)) for jid, job in samples]
    soft = {key(s) for s in SOFT_SKILLS}

    per_job = []
    for jid, job in samples:
        n, req = len(job.requirements), sum(r.required for r in job.requirements)
        flag = "100% 必備" if n and req == n else ("必備少於 3" if req < 3 else "")
        per_job.append({"job_id": jid, "role": roles.get(jid, ""), "items": n, "required": req, "flag": flag})

    soft_tagged = [(jid, r.skill) for jid, job in samples for r in job.requirements
                   if r.category == "soft" and key(r.skill) not in soft]

    table = demand_table(jobs)
    counts = {row["name"]: len(set(row["required_in"] + row["preferred_in"])) for row in table.values()}
    top = top_by_demand(table, TOP_K + 1)
    boundary = (table[top[TOP_K - 1]]["demand"], table[top[TOP_K]]["demand"]) if len(top) > TOP_K else None

    by_role = {}
    for r in ROLES:
        sub = [j for j in jobs if roles.get(j[0]) == r]
        if sub:
            by_role[r] = (len(sub), *bootstrap_top10(sub))

    return {
        "n_jobs": len(samples),
        "n_items": sum(p["items"] for p in per_job),
        "per_job": per_job,
        "soft_tagged": soft_tagged,
        "distinct": len(table),
        "singletons": sum(v == 1 for v in counts.values()),
        "recurring3": sum(v >= 3 for v in counts.values()),
        "top10": [(table[k]["name"], table[k]["demand"], len(table[k]["required_in"])) for k in top[:TOP_K]],
        "boundary": boundary,
        "bootstrap": bootstrap_top10(jobs),
        "bootstrap_by_role": by_role,
        "batch": batch_change(jobs, batch) if batch and len(jobs) > batch else None,
        "candidates": synonym_candidates(counts),
    }


def to_markdown(r: dict) -> str:
    lines = [f"# 樣本檢查（L0）：{r['n_jobs']} 份職缺、{r['n_items']} 個項目", ""]
    flagged = [p for p in r["per_job"] if p["flag"]]
    lines += ["## 必備比例（要人工看的）", ""]
    lines += [f"- {p['job_id']}（{p['role'] or '—'}）：{p['required']}/{p['items']} → {p['flag']}" for p in flagged] or ["- 沒有"]
    lines += ["", "## 模型標成 soft、但不在軟實力名單（現在會保留）", ""]
    lines += [f"- {jid}：{s}" for jid, s in r["soft_tagged"]] or ["- 沒有"]
    lines += ["", "## 命名切碎", "",
              f"- 不同名稱：{r['distinct']}；只出現 1 份：{pct(r['singletons'], r['distinct'])}；出現 3 份以上：{r['recurring3']}"]
    lines += ["", "## 需求前 10 名（必備 2、加分 1）", "", "| # | 技能 | 需求 | 必備份數 |", "|---|---|---|---|"]
    lines += [f"| {i} | {name} | {d} | {req} |" for i, (name, d, req) in enumerate(r["top10"], 1)]
    if r["boundary"]:
        lines += ["", f"第 10 名 {r['boundary'][0]} 分、第 11 名 {r['boundary'][1]} 分（同分時換名次不代表市場變了）"]
    mean_, p10 = r["bootstrap"]
    lines += ["", "## 樣本夠不夠", "", f"- bootstrap 前 10 名重疊：平均 {mean_:.1f}／10、第 10 百分位 {p10}（目標：平均 ≥ 8.5）"]
    lines += [f"- {ROLES[role]}（{n} 份）：平均 {m:.1f}／10（目標 ≥ 7.5）" for role, (n, m, _) in r["bootstrap_by_role"].items()]
    if r["batch"]:
        b = r["batch"]
        lines += [f"- 最後一批加進來：前 10 名換了 {b['top10_churn']} 個（目標 ≤ 1）；"
                  f"技能在舊樣本出現過 {b['seen_before']}，只算必備 {b['seen_before_required']}（目標 ≥ 0.75）；"
                  f"新技能 {b['new_total']} 個，其中出現 2 份以上 {b['new_recurring']} 個"]
    lines += ["", "## 同義詞候選（由你決定要不要寫進 skill_synonyms.yaml；會有誤報）", ""]
    lines += [f"- {a} ／ {b}（{why}）" for a, b, why in r["candidates"]] or ["- 沒有"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="樣本檢查（不花錢）")
    parser.add_argument("--samples", default=str(SAMPLES_FILE))
    parser.add_argument("--batch", type=int, help="把最後幾份當成新的一批，看加進來之後變多少")
    args = parser.parse_args(argv)
    md = to_markdown(evaluate(args.samples, args.batch))
    print(md)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_samples.md"
    out.write_text(md, encoding="utf-8")
    print(f"已存到 {out}")


if __name__ == "__main__":
    main()