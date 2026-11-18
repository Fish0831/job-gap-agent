"""fit_score 合不合你的直覺（docs/evaluation.md 第 1.5、5 節）。不花錢：用 S1 存下來的結果＋你的評分。

python -m evals.fit_eval reports/eval/<時間>_s1     （預設讀 gold/fit.csv；沒填也能跑結構檢查）

你的評分只有一欄 would_apply：3 = 會投、2 = 可能、1 = 不會投（憑直覺，不用分析技能）。
- 程式的分數：S1 每次重跑的 fit_score 取平均（一次的結果會跳，平均比較穩）
- 看：程式前 5 名裡有幾份你會投、你會投／不會投的職缺平均分數差多少、Spearman ρ、意見差最多的職缺
- 結構檢查（不用評分）：必備數多的職缺是不是系統性偏低；排名是不是主要被「必備／加分」標記決定
"""

import argparse
import csv
from pathlib import Path

from evals.metrics import mean, spearman
from evals.stability import load_runs
from job_agent.market import FIT_WEIGHT, rank_jobs

GOLD_FIT = Path("gold/fit.csv")
LABEL = {3: "會投", 2: "可能", 1: "不會投"}


def load_gold(path: str | Path = GOLD_FIT) -> dict[str, dict]:
    """讀你的評分；沒填的職缺跳過。用 utf-8-sig 讀：Excel 存的 CSV 開頭有 BOM。
    相容舊範本：沒有 would_apply 欄就用 overall_fit（1–5）。"""
    rows = {}
    path = Path(path)
    if not path.exists():
        return rows
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            value = (r.get("would_apply") or r.get("overall_fit") or "").strip()
            if value:
                rows[r["job_id"].strip().zfill(2)] = {"score": float(value), "title": r.get("title", ""),
                                                      "note": r.get("note", "")}
    return rows


def ranks(scores: dict[str, float]) -> dict[str, int]:
    """分數高的排第 1；同分照編號（結果固定）。"""
    return {j: i for i, j in enumerate(sorted(scores, key=lambda j: (-scores[j], j)), 1)}


def evaluate(all_runs, gold: dict[str, dict]) -> dict:
    per_run = [{f.job_id: f.fit for f in rank_jobs(runs)} for runs in all_runs]
    jobs = sorted(set.intersection(*(set(p) for p in per_run)))
    fit = {j: mean([p[j] for p in per_run]) for j in jobs}
    first = {r.job_id: r for r in all_runs[0]}
    n_req = {j: max(1, sum(m.required for m in first[j].results)) for j in jobs}
    # 把所有項目都當必備重算一次：相關性低 → 排名主要被「必備／加分」的標記決定
    all_req = {j: sum(FIT_WEIGHT[m.status] for m in first[j].results) / max(1, len(first[j].results)) for j in jobs}
    xs = lambda d, keys=jobs: [d[j] for j in keys]  # noqa: E731
    r_prog = ranks(fit)
    out = {
        "n_jobs": len(jobs),
        "program_top5": [(j, round(fit[j], 2)) for j in sorted(jobs, key=lambda j: r_prog[j])[:5]],
        "denominator_bias": round(spearman(xs(fit), xs(n_req)), 2),
        "required_sensitivity_rho": round(spearman(xs(fit), xs(all_req)), 2),
        "rated": 0,
    }
    rated = [j for j in jobs if j in gold]
    if not rated:
        return out
    score = {j: gold[j]["score"] for j in rated}
    yes = [fit[j] for j in rated if score[j] >= 3]
    no = [fit[j] for j in rated if score[j] <= 1]
    top5 = [j for j in sorted(rated, key=lambda j: r_prog[j])[:5]]
    r_human = ranks({j: score[j] for j in rated})
    r_prog_rated = ranks({j: fit[j] for j in rated})
    diff = sorted(rated, key=lambda j: (-abs(r_prog_rated[j] - r_human[j]), j))[:3]
    out.update({
        "rated": len(rated),
        "spearman": round(spearman(xs(fit, rated), xs(score, rated)), 2),
        "top5_would_apply": sum(score[j] >= 3 for j in top5),
        "mean_fit_apply": round(mean(yes), 2) if yes else None,
        "mean_fit_not_apply": round(mean(no), 2) if no else None,
        "disagreements": [(j, gold[j]["title"], r_prog_rated[j], LABEL.get(int(score[j]), score[j]), round(fit[j], 2),
                           gold[j]["note"]) for j in diff],
    })
    return out


def to_markdown(r: dict) -> str:
    lines = [f"# fit_score 檢查（{r['n_jobs']} 份職缺）", "", "## 結構檢查（不用評分）", "",
             f"- 和必備數的相關：{r['denominator_bias']}（絕對值 ≤ 0.3；很負 = 必備多的職缺被系統性壓低）",
             f"- 全部當必備重算的相關：{r['required_sensitivity_rho']}（≥ 0.7；太低 = 排名主要被必備／加分標記決定）",
             "- 程式前 5 名：" + "、".join(f"{j}（{f}）" for j, f in r["program_top5"])]
    if not r["rated"]:
        return "\n".join(lines + ["", "gold/fit.csv 還沒填 would_apply，只做結構檢查。"]) + "\n"
    lines += ["", f"## 和你的評分比（{r['rated']} 份）", "",
              f"- 程式前 5 名裡你會投的：{r['top5_would_apply']}／5（目標 ≥ 3）",
              f"- 平均 fit_score：你會投的 {r['mean_fit_apply']}、不會投的 {r['mean_fit_not_apply']}（會投的應該明顯比較高）",
              f"- Spearman ρ：{r['spearman']}（20 份、一個人評分，只看方向）",
              "", "## 意見差最多的 3 份（逐份看原因：技能判斷錯？年資？不想做？）", "",
              "| 職缺 | 職稱 | 程式名次 | 你 | fit_score | 你的備註 |", "|---|---|---|---|---|---|"]
    lines += [f"| {j} | {t} | {a} | {b} | {f} | {n} |" for j, t, a, b, f, n in r["disagreements"]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="fit_score 檢查（不花錢）")
    parser.add_argument("folder", help="S1 的結果資料夾（reports/eval/<時間>_s1）")
    parser.add_argument("--gold", default=str(GOLD_FIT))
    args = parser.parse_args(argv)
    all_runs, _ = load_runs(args.folder)
    md = to_markdown(evaluate(all_runs, load_gold(args.gold)))
    print(md)
    (Path(args.folder) / "fit.md").write_text(md, encoding="utf-8")


if __name__ == "__main__":
    main()