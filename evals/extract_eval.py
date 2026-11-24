"""L0＋L1：拆解的檢查（docs/evaluation.md 第 1.1、2.1、6.4 節）。不花錢，但要用本機私人的 jobs/ 和 cache/。

python -m evals.extract_eval              → 全部職缺：引用的原文存不存在、可疑的必備／加分、「A 或 B」被拆成多個必備
                                            （「或」的範圍判斷和拆解後的檢查共用 job_agent/anyof.py 的 or_scope）
python -m evals.extract_eval --align 15   → 和 gold/extract/15.yaml（人工標準答案）比對：precision、recall、必備標記

快取讀的是目前拆解模型的快取（和 samples 匯出用的同一份）；沒有快取的職缺會跳過（先跑一次 python -m job_agent.samples jobs）。
"""

import argparse
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from evals.metrics import pct
from evals.samples_eval import OUT_DIR, processed
from job_agent.anyof import clean_groups, find_line, nfkc, norm, or_scope
from job_agent.__main__ import job_files
from job_agent.extract import cache_path, read_job
from job_agent.llm import model_for
from job_agent.normalize import canonical, key, load_synonyms
from job_agent.schemas import JobRequirements, Requirement

GOLD_DIR = Path("gold/extract")

PREFERRED = re.compile(r"加分|者佳|尤佳|為佳|優先|plus|preferred|nice to have|bonus", re.IGNORECASE)
OVERVIEW = re.compile(r"涵蓋技術|有機會接觸|你將接觸|將有機會|可以學到|技術棧|tech stack", re.IGNORECASE)
REQUIRED_HDR = re.compile(r"條件要求|必備|必要條件|基本條件|職位條件|應徵條件|requirements|qualifications|must", re.IGNORECASE)
HEADER = re.compile(r"【.*】|\[.*\]|^[|｜▶■◆●]?\s*\S.{0,24}[:：]?$")


def grounded(r: Requirement, body: str) -> bool:
    """模型引用的 original_text 是不是真的出現在職缺原文裡。"""
    q = norm(r.original_text)
    return bool(q) and q in norm(body)


def section_header(lines: list[str], i: int) -> str:
    """往上找最近的段落標題（【工作內容】、條件要求：之類的短行）。"""
    for j in range(i, -1, -1):
        line = lines[j].strip()
        if line and HEADER.search(line) and (REQUIRED_HDR.search(line) or PREFERRED.search(line)
                                             or OVERVIEW.search(line) or "【" in line):
            return line
    return ""


def required_lint(job: JobRequirements, body: str) -> list[dict]:
    """可疑的必備／加分標記（只是列出來給人看，不是指標）：
    - 標必備，但那一行或所在段落像「加分」或「有機會接觸／涵蓋技術」
    - 標加分，但在「條件要求／必備」段落，而且那一行沒有加分字眼"""
    lines = body.splitlines()
    out = []
    for r in job.requirements:
        i = find_line(r, lines)
        if i is None:
            continue
        line, hdr = lines[i], section_header(lines, i)
        if r.required and (PREFERRED.search(line) or PREFERRED.search(hdr) or OVERVIEW.search(hdr)):
            out.append({"skill": r.skill, "marked": "必備", "why": f"段落「{hdr[:20]}」／這一行像加分或概覽", "line": line.strip()[:80]})
        elif not r.required and REQUIRED_HDR.search(hdr) and not PREFERRED.search(line) and not PREFERRED.search(hdr):
            out.append({"skill": r.skill, "marked": "加分", "why": f"在「{hdr[:20]}」段落", "line": line.strip()[:80]})
    return out


def _locate(r: Requirement, line: str, masked: list[str]) -> int | None:
    """技能在這一行的位置：先找技能名稱（英文名稱要整個字對上，C 不會被當成 C#），找到就遮掉；
    找不到（例如中文原文、英文名稱）就用 original_text 的位置。都找不到 → None（不判斷這一項）。"""
    name = re.escape(nfkc(r.skill).lower())
    m = re.search(rf"(?<![a-z0-9]){name}(?![a-z0-9+#])", "".join(masked))
    if m:
        masked[m.start():m.end()] = " " * (m.end() - m.start())
        return m.start()
    q = nfkc(r.original_text).lower().strip()
    pos = line.find(q) if q else -1
    return pos if pos >= 0 else None


def or_lint(job: JobRequirements, body: str) -> list[dict]:
    """「A 或 B」的檢查（規則見 docs/design.md 第 5 節）。只看必備項目，每一行：
    1. or_scope 判斷有沒有「或」的線索、範圍到哪裡（「或以上」「或相關科系」不算）
    2. 範圍裡有 2 個以上必備項目，而且「或」那一段裡至少一個 → 下判定：
       split = 強線索、項目沒有標成同一組（很可能被拆錯，目標 0）
       ok    = 強線索、已經是同一組
       maybe = 只有弱線索（等、例如、such as）：可能是舉例，只列出來（待決定 A3）
    另外列出 ungrounded：模型標了群組、但那一行沒有強線索（拆解時會自動取消）。"""
    lines = body.splitlines()
    by_line: dict[int, list[Requirement]] = {}
    for r in job.requirements:
        i = find_line(r, lines)
        if r.required and i is not None:
            by_line.setdefault(i, []).append(r)
    out = []
    for i, items in sorted(by_line.items()):
        line = nfkc(lines[i]).lower()
        scope = or_scope(line)
        if scope is None or len(items) < 2:
            continue
        kind, s0, s1, c0, c1 = scope
        masked = list(line)
        located = [(r, _locate(r, line, masked)) for r in sorted(items, key=lambda r: -len(r.skill))]
        located = sorted(((r, p) for r, p in located if p is not None), key=lambda x: x[1])
        in_scope = [r for r, p in located if s0 <= p <= s1]
        if len(in_scope) < 2 or not any(c0 <= p <= c1 for _, p in located):
            continue
        groups = {r.any_of for r in in_scope}
        verdict = "maybe" if kind == "weak" else ("ok" if len(groups) == 1 and None not in groups else "split")
        out.append({"verdict": verdict, "skills": [r.skill for r in in_scope], "line": lines[i].strip()[:80]})
    for d in clean_groups(job.requirements, body)[1]:
        out.append({"verdict": "ungrounded", "skills": d["skills"], "line": d["why"]})
    return out


# ===== L1：對人工標準答案 =====

class GoldSkill(BaseModel):
    skill: str
    required: bool
    section: Literal["qualification", "duty", "overview", "preferred"] = "qualification"
    accept: list[str] = Field(default_factory=list)


class GoldFP(BaseModel):
    skill: str
    type: Literal["gold_missed", "fabricated", "soft", "split", "dup"]


class GoldJob(BaseModel):
    job_id: str
    skills: list[GoldSkill]
    ignore: list[str] = Field(default_factory=list)
    fp: list[GoldFP] = Field(default_factory=list)

    @classmethod
    def load(cls, job_id: str, folder: Path = GOLD_DIR) -> "GoldJob":
        return cls.model_validate(yaml.safe_load((folder / f"{job_id}.yaml").read_text(encoding="utf-8")))


def align(pred: list[Requirement], gold: GoldJob, synonyms: dict[str, str]) -> dict:
    """用 key(canonical()) 對齊模型和標準答案（gold 的 accept 名稱也算）。
    回傳 tp（模型項目, 標準項目）、fp（模型多抓）、fn（漏抓）、split（同一個標準答案被對到第二次）。"""
    k = lambda name: key(canonical(name, synonyms))
    lookup = {}
    for g in gold.skills:
        for name in [g.skill, *g.accept]:
            lookup.setdefault(k(name), g)
    ignore = {k(n) for n in gold.ignore}
    tp, fp, split, hit = [], [], [], set()
    for r in pred:
        g = lookup.get(k(r.skill))
        if g is None:
            if k(r.skill) not in ignore:
                fp.append(r)
        elif g.skill in hit:
            split.append((r, g))
        else:
            hit.add(g.skill)
            tp.append((r, g))
    fn = [g for g in gold.skills if g.skill not in hit]
    return {"tp": tp, "fp": fp, "fn": fn, "split": split}


def gold_metrics(a: dict, gold: GoldJob) -> dict:
    tp, n_pred, n_gold = len(a["tp"]), len(a["tp"]) + len(a["fp"]) + len(a["split"]), len(gold.skills)
    gold_pref = [(r, g) for r, g in a["tp"] if not g.required]
    gold_req = [(r, g) for r, g in a["tp"] if g.required]
    return {
        "precision": pct(tp, n_pred),
        "recall": pct(tp, n_gold),
        "required_accuracy": pct(sum(r.required == g.required for r, g in a["tp"]), tp),
        "over_required": pct(sum(r.required for r, _ in gold_pref), len(gold_pref)),
        "under_required": pct(sum(not r.required for r, _ in gold_req), len(gold_req)),
        "fabricated": sum(f.type == "fabricated" for f in gold.fp),
        "gold_missed": sum(f.type == "gold_missed" for f in gold.fp),
    }


# ===== 執行 =====

def load_cached(path: Path, model: str) -> JobRequirements | None:
    cache = cache_path(path, model)
    return JobRequirements.model_validate_json(cache.read_text(encoding="utf-8")) if cache.exists() else None


def lint_all(files: list[Path], model: str) -> dict:
    jobs, skipped = [], []
    for f in files:
        job = load_cached(f, model)
        if job is None:
            skipped.append(f.stem)
            continue
        _, body = read_job(f)
        ung = [r for r in job.requirements if not grounded(r, body)]
        jobs.append({"job_id": f.stem, "n": len(job.requirements), "ungrounded": ung,
                     "required_lint": required_lint(job, body), "or_lint": or_lint(job, body)})
    n = sum(j["n"] for j in jobs)
    return {"jobs": jobs, "skipped": skipped,
            "grounding": pct(n - sum(len(j["ungrounded"]) for j in jobs), n)}


OR_SECTIONS = [
    ("split", "「A 或 B」可能被拆成多個必備（目標 0）"),
    ("ungrounded", "模型標了群組、但沒有根據（拆解時已自動取消）"),
    ("maybe", "可能是舉例（等／例如／such as；只列出來，待決定 A3）"),
    ("ok", "「A 或 B」已經標成同一組"),
]


def lint_markdown(r: dict) -> str:
    lines = ["# 拆解檢查（L0）", "", f"- 引用的原文存在：{r['grounding']}（目標 ≥ 0.98）"]
    if r["skipped"]:
        lines.append(f"- 沒有快取、跳過：{'、'.join(r['skipped'])}")
    for verdict, title in OR_SECTIONS:
        found = [(j["job_id"], o) for j in r["jobs"] for o in j["or_lint"] if o["verdict"] == verdict]
        lines += ["", f"## {title}（{len(found)}）", ""]
        lines += [f"- {jid}：{'、'.join(o['skills'])} ← {o['line']}" for jid, o in found] or ["- 沒有"]
    lines += ["", "## 可疑的必備／加分（人工看過才算數）", ""]
    lines += [f"- {j['job_id']}：{x['skill']}（標{x['marked']}；{x['why']}）← {x['line']}"
              for j in r["jobs"] for x in j["required_lint"]] or ["- 沒有"]
    lines += ["", "## 找不到原文的引用", ""]
    lines += [f"- {j['job_id']}：{u.skill} ← 「{u.original_text[:40]}」" for j in r["jobs"] for u in j["ungrounded"]] or ["- 沒有"]
    return "\n".join(lines) + "\n"


def align_markdown(job_id: str, a: dict, m: dict) -> str:
    lines = [f"# 拆解 vs 標準答案：{job_id}", ""] + [f"- {k}：{v}" for k, v in m.items()]
    lines += ["", "## 必備標記不同", ""]
    lines += [f"- {r.skill}：模型 {'必備' if r.required else '加分'}，標準 {'必備' if g.required else '加分'}（{g.section}）"
              for r, g in a["tp"] if r.required != g.required] or ["- 沒有"]
    lines += ["", "## 模型多抓（到 gold 的 fp 標類型：gold_missed／fabricated／soft／split／dup）", ""]
    lines += [f"- {r.skill} ← 「{r.original_text[:40]}」" for r in a["fp"]] or ["- 沒有"]
    lines += ["", "## 漏抓（標準答案有、模型沒有；名稱不同但其實一樣的，加進 accept）", ""]
    lines += [f"- {g.skill}" for g in a["fn"]] or ["- 沒有"]
    if a["split"]:
        lines += ["", "## 拆太細（同一個標準答案被對到第二次）", ""]
        lines += [f"- {r.skill} → {g.skill}" for r, g in a["split"]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="拆解檢查（不花錢；要本機的 jobs/ 和 cache/）")
    parser.add_argument("jobs", nargs="*", default=["jobs"])
    parser.add_argument("--align", metavar="JOB_ID", help="和 gold/extract/<JOB_ID>.yaml 比對")
    args = parser.parse_args(argv)
    model, files = model_for("extract"), job_files(args.jobs)

    if args.align:
        f = next((f for f in files if f.stem == args.align), None)
        job = load_cached(f, model) if f else None
        if job is None:
            raise SystemExit(f"找不到 {args.align} 的職缺檔或快取")
        synonyms = load_synonyms()
        gold = GoldJob.load(args.align)
        a = align(processed(job, synonyms), gold, synonyms)
        md = align_markdown(args.align, a, gold_metrics(a, gold))
        name = f"extract_{args.align}"
    else:
        md, name = lint_markdown(lint_all(files, model)), "extract"
    print(md)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    from datetime import datetime
    out = OUT_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{name}.md"
    out.write_text(md, encoding="utf-8")
    print(f"已存到 {out}")


if __name__ == "__main__":
    main()