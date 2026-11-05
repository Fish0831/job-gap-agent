"""一個指令跑完 ①拆解 → ③比對 → ④報告。

用法：
    python -m job_agent jobs/02.txt                  一份職缺
    python -m job_agent jobs/02.txt jobs/05.txt      多份
    python -m job_agent jobs                         整個資料夾的 .txt
    python -m job_agent jobs --no-cache              重新拆解（不用快取）
    python -m job_agent jobs --profile my.yaml       指定技能檔
換模型：在 .env 或終端機設定 EXTRACT_MODEL／MATCH_MODEL
"""

import argparse
from pathlib import Path

from anthropic import APIError

from job_agent.extract import extract_requirements
from job_agent.llm import LLMError, model_for
from job_agent.match import match
from job_agent.report import build_report, save_markdown, to_markdown
from job_agent.schemas import Profile


def job_files(paths: list[str]) -> list[Path]:
    """把參數展開成職缺檔清單：資料夾 → 裡面所有 .txt。"""
    files = []
    for p in map(Path, paths):
        files += sorted(p.glob("*.txt")) if p.is_dir() else [p]
    return files


def run_one(path: Path, profile: Profile, use_cache: bool) -> float:
    job, extract_stats = extract_requirements(path, use_cache=use_cache)
    results, match_stats = match(job, profile)
    stats = {**match_stats, "cost_usd": extract_stats["cost_usd"] + match_stats["cost_usd"]}
    report = build_report(job, results)
    out = save_markdown(to_markdown(report, stats), path)

    required = [m for m in results if m.required]
    counts = {s: sum(m.status == s for m in required) for s in ("have", "partial", "missing")}
    cached = "（拆解用快取）" if extract_stats.get("cached") else ""
    print(f"{path.name}  {job.title} @ {job.company}")
    print(f"   必備 {len(required)}：有 {counts['have']}／部分 {counts['partial']}／沒有 {counts['missing']}  {cached}")
    print(f"   優先補強：{'、'.join(report.top_gaps[:3]) or '沒有缺口'}")
    print(f"   報告：{out}   cost ${stats['cost_usd']:.4f}")
    return stats["cost_usd"]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m job_agent", description="職缺技能缺口分析")
    parser.add_argument("jobs", nargs="+", help="職缺檔案或資料夾")
    parser.add_argument("--profile", default="profile.yaml", help="技能檔（預設 profile.yaml）")
    parser.add_argument("--no-cache", action="store_true", help="重新拆解職缺，不用快取")
    args = parser.parse_args(argv)

    profile = Profile.from_yaml(args.profile)
    files = job_files(args.jobs)
    print(f"模型：拆解 {model_for('extract')}／比對 {model_for('match')}　共 {len(files)} 份職缺\n")

    total, failed = 0.0, []
    for path in files:
        try:
            total += run_one(path, profile, use_cache=not args.no_cache)
        except (LLMError, APIError, ValueError) as e:  # 一份失敗不影響其他份
            failed.append(path.name)
            print(f"{path.name}  ❌ 失敗：{e}")
        print()
    print(f"完成 {len(files) - len(failed)} 份、失敗 {len(failed)} 份，總費用 ${total:.4f}")


if __name__ == "__main__":
    main()