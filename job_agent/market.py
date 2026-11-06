"""⑤ 市場缺口報告：把多份職缺的比對結果疊在一起，統計哪些技能最常被要求、我卻還沒有。

全部由程式計算，不呼叫 LLM（比對結果已經在 ③ 做好）：
- aggregate()      依技能彙總：出現在幾份必備、幾份加分、每份我的狀態
- overall_status() 跨職缺的「我的狀態」：取最常見的；平手取比較差的（保守）
- priority()       優先度 = 需求分數 × 缺口權重
- to_markdown()    回傳字串，不直接寫檔 → 之後網頁版可以直接拿來顯示
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from job_agent.normalize import key
from job_agent.report import ICON, REPORT_DIR, _cell
from job_agent.schemas import JobRequirements, MatchResult, Status

GAP_WEIGHT = {"missing": 2, "partial": 1, "have": 0}  # 沒有 > 部分 > 有
WORSE_FIRST = ["missing", "partial", "have"]           # 平手時取前面的（比較差的）
TOP_N = 10


@dataclass
class Run:
    """一份職缺的比對結果。job_id = 職缺檔名（例如 02）。"""
    job_id: str
    job: JobRequirements
    results: list[MatchResult]


@dataclass
class SkillStat:
    skill: str
    required_in: list[str] = field(default_factory=list)   # 哪幾份把它列為必備
    preferred_in: list[str] = field(default_factory=list)  # 哪幾份列為加分
    statuses: dict[str, Status] = field(default_factory=dict)  # 每份職缺我的狀態
    evidence: set[str] = field(default_factory=set)
    level: str | None = None

    @property
    def status(self) -> Status:
        return overall_status(list(self.statuses.values()))

    @property
    def consistent(self) -> bool:
        return len(set(self.statuses.values())) == 1

    @property
    def demand(self) -> int:
        """需求分數：必備算 2、加分算 1。"""
        return 2 * len(self.required_in) + len(self.preferred_in)


# ===== 統計（程式規則）=====

def overall_status(statuses: list[Status]) -> Status:
    """同一個技能在不同職缺可能被判成不同狀態（LLM 判斷有變動）。
    取最常見的；平手時取比較差的 → 寧可多列一個缺口，也不要誤以為自己會。"""
    counts = Counter(statuses)
    return max(WORSE_FIRST, key=lambda s: (counts[s], -WORSE_FIRST.index(s)))


def priority(stat: SkillStat) -> int:
    """數字越大越該先補。我已經「有」的技能是 0。"""
    return stat.demand * GAP_WEIGHT[stat.status]


def aggregate(runs: list[Run]) -> list[SkillStat]:
    """依技能彙總所有職缺。技能名稱在 ③ 已經統一過，這裡再用 key() 合併大小寫差異。"""
    stats: dict[str, SkillStat] = {}
    names: dict[str, Counter] = {}
    for run in runs:
        for m in run.results:
            k = key(m.skill)
            stat = stats.setdefault(k, SkillStat(skill=m.skill))
            names.setdefault(k, Counter())[m.skill] += 1
            (stat.required_in if m.required else stat.preferred_in).append(run.job_id)
            stat.statuses[run.job_id] = m.status
            stat.evidence.update(m.evidence)
            stat.level = stat.level or m.level
    for k, stat in stats.items():
        stat.skill = names[k].most_common(1)[0][0]  # 顯示最常見的寫法
    return sorted(stats.values(), key=lambda s: (-priority(s), -s.demand, s.skill.lower()))


def top_gaps(stats: list[SkillStat], n: int = TOP_N) -> list[SkillStat]:
    return [s for s in stats if priority(s) > 0][:n]


def strengths(stats: list[SkillStat], n: int = TOP_N) -> list[SkillStat]:
    """我最有競爭力的技能：我「有」，而且很多份職缺列為必備。"""
    have = [s for s in stats if s.status == "have" and s.required_in]
    return sorted(have, key=lambda s: (-len(s.required_in), -s.demand, s.skill.lower()))[:n]


# ===== Markdown =====

def _status_cell(stat: SkillStat) -> str:
    text = ICON[stat.status] + (f"（{stat.level}）" if stat.status == "have" and stat.level else "")
    if not stat.consistent:
        counts = Counter(stat.statuses.values())
        text += " ⚠️ " + "／".join(f"{ICON[s].split()[-1]} {counts[s]}" for s in WORSE_FIRST if counts[s])
    return text


def _jobs(ids: list[str]) -> str:
    return "、".join(ids) or "—"


def to_markdown(runs: list[Run], stats: list[SkillStat], cost_usd: float | None = None,
                created_at: datetime | None = None) -> str:
    n = len(runs)
    created_at = created_at or datetime.now()
    lines = [
        f"# 市場缺口報告（{n} 份職缺）",
        "",
        f"- 產生時間：{created_at:%Y-%m-%d %H:%M}",
        f"- 樣本：{n} 份職缺、{len(stats)} 個不同的技能（已統一名稱、排除軟實力）",
        "- 優先度 = 需求分數（必備 2 分、加分 1 分，逐份加總）× 缺口權重（沒有 2、部分 1、有 0）",
        "- 我的狀態：各份職缺的判斷取最常見的，平手取比較差的；⚠️ = 各份判斷不一致",
        "",
        "## 最該先補的技能",
        "",
        "| # | 技能 | 必備 | 加分 | 我的狀態 | 優先度 | 出現在 |",
        "|---|---|---|---|---|---|---|",
    ]
    gaps = top_gaps(stats)
    for i, s in enumerate(gaps, 1):
        lines.append(
            f"| {i} | {_cell(s.skill)} | {len(s.required_in)}／{n} | {len(s.preferred_in)} "
            f"| {_status_cell(s)} | {priority(s)} | {_jobs(s.required_in + s.preferred_in)} |"
        )
    if not gaps:
        lines.append("| | 沒有缺口 | | | | | |")

    lines += [
        "",
        "## 我最有競爭力的技能",
        "",
        "| # | 技能 | 必備 | 我的狀態 | 證據 |",
        "|---|---|---|---|---|",
    ]
    for i, s in enumerate(strengths(stats), 1):
        lines.append(f"| {i} | {_cell(s.skill)} | {len(s.required_in)}／{n} | {_status_cell(s)} "
                     f"| {', '.join(sorted(s.evidence)) or '—'} |")

    shown = [s for s in stats if len(s.statuses) >= 2]
    lines += [
        "",
        f"## 完整統計（出現在 2 份以上的技能，共 {len(shown)} 個；只出現 1 次的 {len(stats) - len(shown)} 個沒有列出）",
        "",
        "| 技能 | 必備 | 加分 | 我的狀態 | 優先度 |",
        "|---|---|---|---|---|",
    ]
    for s in shown:
        lines.append(f"| {_cell(s.skill)} | {len(s.required_in)} | {len(s.preferred_in)} | {_status_cell(s)} | {priority(s)} |")

    mixed = [s for s in stats if not s.consistent]
    lines += ["", f"## 判斷不一致的技能（{len(mixed)} 個）", ""]
    if mixed:
        lines.append("同一個技能在不同職缺被判成不同狀態。可以檢查 profile 的 alias，或把寫法加進 skill_synonyms.yaml。")
        lines.append("")
        for s in mixed:
            detail = "、".join(f"{j} {ICON[st].split()[-1]}" for j, st in s.statuses.items())
            lines.append(f"- **{s.skill}**：{detail}")
    else:
        lines.append("沒有。")

    lines += ["", "## 樣本範圍", "", "| 檔案 | 職稱 | 公司 | 必備項目數 |", "|---|---|---|---|"]
    for r in runs:
        lines.append(f"| {r.job_id} | {_cell(r.job.title)} | {_cell(r.job.company or '—')} "
                     f"| {sum(m.required for m in r.results)} |")
    if cost_usd is not None:
        lines += ["", f"_費用：US${cost_usd:.4f}（拆解有快取的不花錢）_"]
    return "\n".join(lines) + "\n"


def save_markdown(markdown: str) -> Path:
    """存到 reports/market_<時間>.md，回傳路徑。"""
    REPORT_DIR.mkdir(exist_ok=True)
    path = REPORT_DIR / f"market_{datetime.now():%Y%m%d_%H%M%S}.md"
    path.write_text(markdown, encoding="utf-8")
    return path