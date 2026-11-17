"""⑤ 市場缺口報告：把多份職缺的比對結果疊在一起，統計哪些技能最常被要求、我卻還沒有。

全部由程式計算，不呼叫 LLM（比對結果已經在 ③ 做好）：
- aggregate()      依技能彙總：出現在幾份必備、幾份加分、每份我的狀態
- overall_status() 跨職缺的「我的狀態」：取最常見的；平手取比較差的（保守）
- priority()       優先度 = 每份職缺各算一次再加總：必備 2／加分 1 × 那一份的缺口權重（沒有 2、部分 1、有 0）
- job_fit()        每份職缺的適合度 = (有 + 0.5 × 部分) ÷ 必備數 → rank_jobs() 排出最適合的職缺
- role_fit()       依職務類別平均適合度 → 使用者最接近哪一類
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
FIT_WEIGHT = {"have": 1.0, "partial": 0.5, "missing": 0.0}  # 適合度：有 1 分、部分 0.5 分
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
    """數字越大越該先補。每份職缺各算一次再加總（必備 2、加分 1，× 那一份我的缺口權重）。
    舊寫法是「需求分數 × 取多數後的狀態權重」：剛好平手時一次判斷翻轉，整個優先度就加倍或減半。
    新寫法：所有職缺判斷一致時結果和舊的一樣；不一致時，一次翻轉只影響那一份（必備差 2、加分差 1）。"""
    return (sum(2 * GAP_WEIGHT[stat.statuses[j]] for j in stat.required_in)
            + sum(GAP_WEIGHT[stat.statuses[j]] for j in stat.preferred_in))


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


# ===== 適合度（程式規則）=====

@dataclass
class JobFit:
    job_id: str
    fit: float              # 0–1：(有 + 0.5 × 部分) ÷ 必備數
    n_required: int
    missing_required: int   # 必備技能裡「沒有」的數量


def job_fit(run: Run) -> JobFit:
    """一份職缺有多適合我：只看必備技能（沒有必備技能的職缺才看全部）。年資不算進來。"""
    req = [r for r in run.results if r.required] or run.results
    fit = sum(FIT_WEIGHT[r.status] for r in req) / len(req) if req else 0.0
    return JobFit(run.job_id, round(fit, 3), len(req), sum(r.status == "missing" for r in req))


def rank_jobs(runs: list[Run]) -> list[JobFit]:
    """適合度高的在前；同分時必備缺口少的在前，再依職缺編號（結果固定）。"""
    return sorted((job_fit(r) for r in runs), key=lambda f: (-f.fit, f.missing_required, f.job_id))


def role_fit(fits: list[JobFit], roles: dict[str, str], min_jobs: int = 1) -> list[tuple[str, float, int]]:
    """依職務類別平均適合度 → [(類別, 平均適合度, 份數)]，高的在前。沒有類別或份數太少的不算。"""
    groups: dict[str, list[float]] = {}
    for f in fits:
        if roles.get(f.job_id):
            groups.setdefault(roles[f.job_id], []).append(f.fit)
    rows = [(r, round(sum(v) / len(v), 3), len(v)) for r, v in groups.items() if len(v) >= min_jobs]
    return sorted(rows, key=lambda x: (-x[1], -x[2], x[0]))


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
                created_at: datetime | None = None, role_label: str | None = None) -> str:
    n = len(runs)
    created_at = created_at or datetime.now()
    lines = [
        f"# 市場缺口報告{'：' + role_label if role_label else ''}（{n} 份職缺）",
        "",
        f"- 產生時間：{created_at:%Y-%m-%d %H:%M}",
        f"- 樣本：{n} 份職缺、{len(stats)} 個不同的技能（已統一名稱、排除軟實力）",
        "- 優先度 = 每份職缺的缺口加總：必備 2 分、加分 1 分，× 那一份的缺口權重（沒有 2、部分 1、有 0）",
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