"""L2 穩定度 S1：拆解固定（samples/jobs.json），比對重跑 N 次 → 量結果跳多少（docs/evaluation.md 第 3 節）。

python -m evals.stability match --n 5            → 花錢：先印估計費用，超過 --budget（預設 US$0.50）就不跑
python -m evals.stability rescore <資料夾>         → 不花錢：用存下來的結果，比較「現在的優先度」和「舊規則」

每次重跑的原始比對結果都存在 reports/eval/<時間>_s1/runs.json（私人，已在 .gitignore），
之後改優先度、fit_score、同義詞表，都可以用同一批資料離線重算，不用再花錢。
數字摘要另外加一行到 evals/history.jsonl（公開，只有數字）。
"""

import argparse
import json
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path

from evals.metrics import kendall_tau, mean, overlap_at_k, pairwise
from evals.samples_eval import OUT_DIR
from job_agent.llm import model_for
from job_agent.market import (
    GAP_WEIGHT,
    Run,
    SkillStat,
    aggregate,
    priority,
    rank_jobs,
    role_fit,
)
from job_agent.normalize import key
from job_agent.pipeline import analyze
from job_agent.samples import SAMPLES_FILE, load_roles, load_samples
from job_agent.schemas import MatchResult, Profile

EST_PER_MATCH = 0.0015      # 每份職缺比對一次約 US$0.001（網頁實測），抓寬一點
HISTORY = Path("evals/history.jsonl")
MIN_ROLE_JOBS = 3


def old_priority(stat: SkillStat) -> int:
    """11/17 以前的規則：需求分數 × 取多數後的狀態權重（rescore 時當對照組）。"""
    return stat.demand * GAP_WEIGHT[stat.status]


def ranked_gaps(stats: list[SkillStat], priority_fn=priority) -> list[tuple[str, int]]:
    """依指定的優先度規則重排缺口 → [(key, 優先度)]，優先度 0 的不算缺口。"""
    rows = [(key(s.skill), priority_fn(s), s.demand, s.skill.lower()) for s in stats]
    rows = [r for r in rows if r[1] > 0]
    return [(k, p) for k, p, _, _ in sorted(rows, key=lambda r: (-r[1], -r[2], r[3]))]


def match_stability(all_runs: list[list[Run]], roles: dict[str, str], priority_fn=priority) -> dict:
    """N 次重跑的比對結果 → 穩定度指標（公式見 docs/evaluation.md 1.2、1.4、1.5）。"""
    statuses: dict[tuple, list[str]] = {}
    for runs in all_runs:
        for run in runs:
            for m in run.results:
                if m.how != "name":  # 名稱對上的由程式決定，不會變
                    statuses.setdefault((run.job_id, key(m.skill)), []).append(m.status)
    flips = [k for k, v in statuses.items() if len(set(v)) > 1]

    all_stats = [aggregate(runs) for runs in all_runs]
    inconsistency = []
    for stats in all_stats:
        multi = [s for s in stats if len(s.statuses) >= 2]
        if multi:
            inconsistency.append(sum(not s.consistent for s in multi) / len(multi))

    ranked = [ranked_gaps(stats, priority_fn) for stats in all_stats]
    tops = [[k for k, _ in r] for r in ranked]
    pr = [dict(r) for r in ranked]

    def tau(a: dict, b: dict) -> float:
        keys = sorted(set(list(a)[:20]) | set(list(b)[:20]))
        return kendall_tau([a.get(k, 0) for k in keys], [b.get(k, 0) for k in keys])

    union10 = {k for t in tops for k in t[:10]}
    jumps = {k: max(p.get(k, 0) for p in pr) - min(p.get(k, 0) for p in pr) for k in union10}
    worst = max(jumps.items(), key=lambda kv: kv[1]) if jumps else ("—", 0)

    fits = [rank_jobs(runs) for runs in all_runs]
    by_job: dict[str, list[float]] = {}
    for fs in fits:
        for f in fs:
            by_job.setdefault(f.job_id, []).append(f.fit)
    best_roles = [(role_fit(fs, roles, MIN_ROLE_JOBS) or [("—", 0, 0)])[0][0] for fs in fits]
    top_role, top_count = Counter(best_roles).most_common(1)[0]

    overlaps = pairwise(tops, overlap_at_k)
    return {
        "n_runs": len(all_runs),
        "status_flip_rate": f"{len(flips)}/{len(statuses)} ({len(flips) / len(statuses):.2f})" if statuses else "0/0",
        "flipped": sorted(flips)[:30],
        "cross_job_inconsistency": round(mean(inconsistency), 3) if inconsistency else 0.0,
        "top10_overlap_mean": round(mean(overlaps), 2) if overlaps else 10.0,
        "top10_overlap_min": min(overlaps) if overlaps else 10,
        "rank_tau": round(mean(pairwise(pr, tau)), 3) if len(pr) > 1 else 1.0,
        "priority_max_jump": f"{worst[1]}（{worst[0]}）",
        "fit_sd_max": round(max((statistics.pstdev(v) for v in by_job.values()), default=0.0), 3),
        "top5_overlap_runs": round(mean(pairwise([[f.job_id for f in fs] for fs in fits],
                                                 lambda a, b: overlap_at_k(a, b, 5))), 2) if len(fits) > 1 else 5.0,
        "role_fit_agreement": f"{top_count}/{len(best_roles)}（{top_role}）",
    }


# ===== 存檔、讀檔 =====

def save_runs(all_runs: list[list[Run]], out_dir: Path, meta: dict) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    data = {"meta": meta,
            "runs": [[{"job_id": r.job_id, "results": [m.model_dump() for m in r.results]} for r in runs]
                     for runs in all_runs]}
    path = out_dir / "runs.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def load_runs(folder: str | Path, samples_path: str | Path = SAMPLES_FILE) -> tuple[list[list[Run]], dict]:
    """讀 runs.json；職缺本身從 samples 用編號查（runs.json 只存比對結果）。"""
    data = json.loads((Path(folder) / "runs.json").read_text(encoding="utf-8"))
    jobs = dict(load_samples(samples_path))
    all_runs = [[Run(r["job_id"], jobs[r["job_id"]], [MatchResult.model_validate(m) for m in r["results"]])
                 for r in runs if r["job_id"] in jobs] for runs in data["runs"]]
    return all_runs, data["meta"]


def append_history(record: dict, path: Path = HISTORY) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ===== 執行 =====

def run_s1(profile: Profile, samples, n: int, budget: float, client=None, out_root: Path = OUT_DIR) -> dict:
    est = n * len(samples) * EST_PER_MATCH
    print(f"S1：{len(samples)} 份職缺 × {n} 次，估計 US${est:.3f}（上限 US${budget:.2f}）")
    if est > budget:
        raise SystemExit("估計費用超過上限，沒有執行。要跑的話加 --budget 調高上限。")
    all_runs, total, failed = [], 0.0, []
    for i in range(n):
        runs, cost, fail = analyze(profile, samples, client=client)
        all_runs.append(runs)
        total += cost
        failed += fail
        print(f"  第 {i + 1}/{n} 次完成（US${cost:.4f}）", flush=True)
    meta = {"kind": "S1", "time": f"{datetime.now():%Y-%m-%d %H:%M}", "model": model_for("match"),
            "n": n, "n_jobs": len(samples), "cost_usd": round(total, 4), "failed": failed}
    folder = out_root / f"{datetime.now():%Y%m%d_%H%M%S}_s1"
    save_runs(all_runs, folder, meta)
    return {"folder": folder, "all_runs": all_runs, "meta": meta}


def rescore(all_runs: list[list[Run]], roles: dict[str, str]) -> dict[str, dict]:
    return {"現在（逐份加總）": match_stability(all_runs, roles, priority),
            "舊規則（取多數 × 權重）": match_stability(all_runs, roles, old_priority)}


def to_markdown(results: dict[str, dict], meta: dict) -> str:
    names = list(results)
    keys = [k for k in results[names[0]] if k != "flipped"]
    lines = [f"# 穩定度 S1：{meta.get('n_jobs')} 份職缺 × {meta.get('n')} 次（{meta.get('model')}，US${meta.get('cost_usd')}）", "",
             "| 指標 | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    lines += [f"| {k} | " + " | ".join(str(results[n][k]) for n in names) + " |" for k in keys]
    lines += ["", "目標：status_flip_rate ≤ 0.10、top10_overlap 平均 ≥ 8 且最小 ≥ 7、rank_tau ≥ 0.80、"
              "priority_max_jump ≤ 4、fit_sd_max ≤ 0.05、top5_overlap_runs ≥ 4、role_fit_agreement ≥ 0.9",
              "", "## 判斷有變動的（職缺, 技能），最多 30 個", ""]
    lines += [f"- {j}：{s}" for j, s in results[names[0]]["flipped"]] or ["- 沒有"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="穩定度 S1")
    sub = parser.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("match", help="比對重跑 N 次（花錢）")
    m.add_argument("--n", type=int, default=5)
    m.add_argument("--profile", default="profile.yaml")
    m.add_argument("--budget", type=float, default=0.50)
    r = sub.add_parser("rescore", help="用存下來的結果重算（不花錢）")
    r.add_argument("folder")
    args = parser.parse_args(argv)

    roles = load_roles()
    if args.cmd == "match":
        out = run_s1(Profile.from_yaml(args.profile), load_samples(), args.n, args.budget)
        all_runs, meta, folder = out["all_runs"], out["meta"], out["folder"]
    else:
        all_runs, meta = load_runs(args.folder)
        folder = Path(args.folder)
    results = rescore(all_runs, roles)
    md = to_markdown(results, meta)
    print(md)
    (folder / "summary.md").write_text(md, encoding="utf-8")
    if args.cmd == "match":
        now = results["現在（逐份加總）"]
        append_history({**meta, **{k: v for k, v in now.items() if k != "flipped"}})
    print(f"已存到 {folder}")


if __name__ == "__main__":
    main()