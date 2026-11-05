"""模型比較：同一份拆解（快取），換不同模型做比對，逐項列出每個模型的判斷和理由。

用法：
    python -m job_agent.compare jobs/02.txt                     預設比較 llm.py 裡所有有價格的模型
    python -m job_agent.compare jobs/02.txt --models claude-haiku-5-5 claude-sonnet-5-5
結果存到 reports/compare_<職缺檔名>_<時間>.md
"""

import argparse
from datetime import datetime

from anthropic import APIError

from job_agent.extract import extract_requirements
from job_agent.llm import PRICES, LLMError
from job_agent.match import match
from job_agent.report import ICON, REPORT_DIR, _cell
from job_agent.schemas import JobRequirements, MatchResult, Profile


def short(model: str) -> str:
    return model.removeprefix("claude-")  # claude-haiku-5-5 → haiku-5-5


def describe(m: MatchResult) -> str:
    """一格要顯示的內容：狀態＋怎麼對上的。"""
    if m.how == "name":
        return f"{ICON[m.status]}（名稱對上）"
    if m.how == "none":
        return ICON[m.status]
    return f"{ICON[m.status]}（{m.how} → {m.matched_to}）"


def compare(job: JobRequirements, profile: Profile, models: list[str], client=None) -> dict:
    """每個模型跑一次比對，回傳 {模型: (技能 → MatchResult, 用量紀錄)}；失敗的模型記下錯誤。"""
    runs = {}
    for model in models:
        try:
            results, stats = match(job, profile, client=client, model=model)
            runs[model] = ({r.skill: r for r in results}, stats)
        except (LLMError, APIError, ValueError) as e:
            runs[model] = (None, {"error": str(e)})
    return runs


def differing_skills(runs: dict) -> list[str]:
    ok = [res for res, _ in runs.values() if res]
    if not ok:
        return []
    return [s for s in ok[0] if len({describe(res[s]) for res in ok}) > 1]


def _counts(results: dict, required: bool) -> str:
    group = [m for m in results.values() if m.required == required]
    return "／".join(str(sum(m.status == s for m in group)) for s in ("have", "partial", "missing"))


def to_markdown(job: JobRequirements, runs: dict) -> str:
    ok = {m: r for m, r in runs.items() if r[0]}
    diff = differing_skills(runs)
    lines = [
        f"# 模型比較：{job.title} @ {job.company}",
        "",
        f"- 產生時間：{datetime.now():%Y-%m-%d %H:%M}",
        "- 同一份拆解結果（快取），只換「比對」的模型",
        "- 「名稱對上」是程式直接對上，所有模型都一樣；差異只會出現在 LLM 判斷的項目",
        "",
        "## 摘要",
        "",
        "| 模型 | 必備 有／部分／沒有 | 加分 有／部分／沒有 | LLM 判斷幾項 | tokens 輸入／輸出 | 時間 | 費用 |",
        "|---|---|---|---|---|---|---|",
    ]
    for model, (res, st) in runs.items():
        if res is None:
            lines.append(f"| {short(model)} | ❌ 失敗：{_cell(st['error'])} | | | | | |")
            continue
        lines.append(
            f"| {short(model)} | {_counts(res, True)} | {_counts(res, False)} | {st['semantic']} "
            f"| {st.get('input_tokens', 0)}／{st.get('output_tokens', 0)} | {st.get('seconds', 0)}s | ${st['cost_usd']:.4f} |"
        )

    lines += [
        "",
        f"## 逐項比較（⚠️ = 模型判斷不同，共 {len(diff)} 項）",
        "",
        "| | 類別 | 技能 | " + " | ".join(short(m) for m in ok) + " |",
        "|---|---|---|" + "---|" * len(ok),
    ]
    if ok:
        first = next(iter(ok.values()))[0]
        order = sorted(first, key=lambda s: (not first[s].required, s not in diff))
        for s in order:
            cells = " | ".join(describe(res[s]) for res, _ in ok.values())
            lines.append(f"| {'⚠️' if s in diff else ''} | {'必備' if first[s].required else '加分'} | {_cell(s)} | {cells} |")

    lines += ["", "## 判斷不同的項目：各模型的理由", ""]
    if not diff:
        lines.append("所有模型的判斷都一樣。")
    for s in diff:
        lines.append(f"### {s}")
        for model, (res, _) in ok.items():
            lines.append(f"- **{short(model)}**：{describe(res[s])} — {res[s].reason}")
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m job_agent.compare", description="比較不同模型的比對結果")
    parser.add_argument("job", help="一份職缺檔")
    parser.add_argument("--models", nargs="+", default=list(PRICES), help="要比較的模型（預設全部）")
    parser.add_argument("--profile", default="profile.yaml")
    args = parser.parse_args(argv)

    profile = Profile.from_yaml(args.profile)
    job, _ = extract_requirements(args.job)  # 用快取，確保每個模型拿到同一份拆解
    runs = compare(job, profile, args.models)

    REPORT_DIR.mkdir(exist_ok=True)
    stem = args.job.replace("\\", "/").split("/")[-1].removesuffix(".txt")
    path = REPORT_DIR / f"compare_{stem}_{datetime.now():%Y%m%d_%H%M%S}.md"
    path.write_text(to_markdown(job, runs), encoding="utf-8")

    print(f"{job.title} @ {job.company}")
    for model, (res, st) in runs.items():
        if res is None:
            print(f"   {short(model):<12} ❌ 失敗：{st['error']}")
        else:
            print(f"   {short(model):<12} 必備 有／部分／沒有 = {_counts(res, True)}   "
                  f"{st.get('seconds', 0)}s   ${st['cost_usd']:.4f}")
    diff = differing_skills(runs)
    print(f"判斷不同的項目 {len(diff)} 個：{'、'.join(diff) or '無'}")
    print(f"完整比較：{path}")


if __name__ == "__main__":
    main()