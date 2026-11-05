"""④ 報告：JobRequirements + list[MatchResult] → GapReport → Markdown。

全部由程式決定，不呼叫 LLM：
- priority()   缺口的優先順序（必備沒有 > 必備部分 > 加分沒有 > 加分部分）
- suggestion() 每個缺口的下一步建議（看狀態和有沒有證據）
- to_markdown() 回傳字串，不直接寫檔 → 之後網頁版可以直接拿來顯示
"""

import sys
from datetime import datetime
from pathlib import Path

from job_agent.schemas import GapReport, JobRequirements, MatchResult

REPORT_DIR = Path("reports")
ICON = {"have": "✅ 有", "partial": "🟡 部分", "missing": "❌ 沒有"}
TOP_N = 5


# ===== 排序和建議（程式規則）=====

def priority(m: MatchResult) -> tuple[int, int]:
    """數字越小越優先。have 不算缺口，不會用到這個函式。"""
    return (0 if m.required else 1, 0 if m.status == "missing" else 1)


def gaps(results: list[MatchResult]) -> list[MatchResult]:
    """所有缺口（missing + partial），依優先順序排好。"""
    return sorted([m for m in results if m.status != "have"], key=priority)


def suggestion(m: MatchResult) -> str:
    if m.status == "missing":
        return "還沒學過 → 先學基本用法，再做一個用到它的小作品"
    if not m.evidence:
        return "會一點但沒有作品證據 → 做一個小作品或寫進現有專案"
    return "只有相關技能 → 在現有作品裡加上這個技能，或做一個小作品"


def build_report(job: JobRequirements, results: list[MatchResult]) -> GapReport:
    return GapReport(job=job, matches=results, top_gaps=[m.skill for m in gaps(results)[:TOP_N]])


# ===== Markdown =====

def _cell(text: str) -> str:
    """表格儲存格裡不能有 | 和換行，不然表格會壞掉。"""
    return text.replace("|", "／").replace("\n", " ")


def _count_line(results: list[MatchResult], required: bool) -> str:
    group = [m for m in results if m.required == required]
    counts = " ／ ".join(f"{ICON[s]} {sum(m.status == s for m in group)}" for s in ICON)
    return f"{'必備' if required else '加分'} {len(group)} 項：{counts}"


def to_markdown(report: GapReport, stats: dict | None = None) -> str:
    job, results = report.job, report.matches
    lines = [
        f"# 缺口報告：{job.title}" + (f" @ {job.company}" if job.company else ""),
        "",
        f"- 產生時間：{report.created_at:%Y-%m-%d %H:%M}",
    ]
    if job.min_years is not None:
        lines.append(f"- 年資要求：{job.min_years} 年以上")
    lines += [f"- {_count_line(results, True)}", f"- {_count_line(results, False)}", ""]

    lines += ["## 建議優先補強", ""]
    top = gaps(results)[:TOP_N]
    if not top:
        lines.append("沒有缺口，所有要求都有作品證明。")
    for i, m in enumerate(top, 1):
        tag = "必備" if m.required else "加分"
        lines.append(f"{i}. **{m.skill}**（{tag}，{ICON[m.status]}）— {suggestion(m)}")
    lines.append("")

    lines += [
        "## 完整比對",
        "",
        "| 類別 | 技能 | 狀態 | 熟練度 | 證據 | 判斷理由 |",
        "|---|---|---|---|---|---|",
    ]
    order = {"missing": 0, "partial": 1, "have": 2}
    for m in sorted(results, key=lambda m: (not m.required, order[m.status])):
        lines.append(
            f"| {'必備' if m.required else '加分'} | {_cell(m.skill)} | {ICON[m.status]} "
            f"| {m.level or '—'} | {', '.join(m.evidence) or '—'} | {_cell(m.reason)} |"
        )

    if stats:
        model = stats.get("model", "沒有呼叫 LLM")
        lines += ["", f"_費用：US${stats['cost_usd']:.4f}，名稱直接對上 {stats['exact']} 項、LLM 判斷 {stats['semantic']} 項（比對模型：{model}）_"]
    return "\n".join(lines) + "\n"


def save_markdown(markdown: str, job_path: str | Path) -> Path:
    """存到 reports/<職缺檔名>_<時間>.md，回傳路徑。"""
    REPORT_DIR.mkdir(exist_ok=True)
    path = REPORT_DIR / f"{Path(job_path).stem}_{datetime.now():%Y%m%d_%H%M%S}.md"
    path.write_text(markdown, encoding="utf-8")
    return path


# ===== CLI =====

if __name__ == "__main__":
    from job_agent.extract import extract_requirements
    from job_agent.match import match
    from job_agent.schemas import Profile

    if len(sys.argv) not in (2, 3):
        sys.exit("usage: python -m job_agent.report jobs/02.txt [profile.yaml]")
    profile = Profile.from_yaml(sys.argv[2] if len(sys.argv) == 3 else "profile.yaml")
    job, extract_stats = extract_requirements(sys.argv[1])
    results, match_stats = match(job, profile)
    stats = {**match_stats, "cost_usd": extract_stats["cost_usd"] + match_stats["cost_usd"]}

    report = build_report(job, results)
    path = save_markdown(to_markdown(report, stats), sys.argv[1])
    print(f"{job.title} @ {job.company}")
    print(f"報告已存到：{path}")
    print("建議優先補強：" + "、".join(report.top_gaps) if report.top_gaps else "沒有缺口")
    print(f"cost: ${stats['cost_usd']:.4f}")