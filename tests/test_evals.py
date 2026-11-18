"""評估程式的測試（evals/）。全部用假的 Claude 和手做的小資料：不連網、不花錢、答案可以手算。"""

import json
import math

import pytest

from evals import extract_eval as ee
from evals import fit_eval, samples_eval, stability
from evals.metrics import (
    flip_rate,
    jaccard,
    kendall_tau,
    overlap_at_k,
    pct,
    spearman,
    wilson,
)
from job_agent.market import Run
from job_agent.schemas import JobRequirements, MatchResult, Profile, Requirement


def req(skill, required=True, text="", category="other"):
    return Requirement(skill=skill, original_text=text or skill, category=category, required=required)


def write_samples(tmp_path, jobs):
    """jobs：[(job_id, role, [Requirement])] → samples/jobs.json"""
    (tmp_path / "samples").mkdir(exist_ok=True)
    data = [{"job_id": j, "role": r, "job": {"title": f"Job {j}", "company": "ACME",
                                            "requirements": [x.model_dump() for x in reqs]}} for j, r, reqs in jobs]
    path = tmp_path / "samples" / "jobs.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


# ===== metrics =====

def test_metrics_basic():
    assert jaccard({1, 2}, {2, 3}) == 1 / 3 and jaccard(set(), set()) == 1.0
    assert overlap_at_k(list("abcde"), list("xbcay"), k=3) == 2
    assert flip_rate({"a": ["have", "have"], "b": ["have", "partial"]}) == 0.5
    assert pct(16, 23) == "16/23 (0.70)"


def test_kendall_and_spearman():
    assert kendall_tau([1, 2, 3], [10, 20, 30]) == 1.0
    assert kendall_tau([1, 2, 3], [3, 2, 1]) == -1.0
    assert 0 < kendall_tau([1, 2, 2, 3], [1, 2, 3, 4]) < 1     # 有同分
    assert spearman([1, 2, 3], [2, 4, 9]) == pytest.approx(1.0)
    assert math.isnan(spearman([1, 1, 1], [1, 2, 3]))          # 全部同分 → 算不出來


def test_wilson_interval_contains_rate():
    lo, hi = wilson(16, 23)
    assert lo < 16 / 23 < hi and 0 <= lo and hi <= 1


# ===== samples_eval（L0）=====

def test_samples_eval_flags_and_top10(tmp_path):
    path = write_samples(tmp_path, [
        ("01", "ai-app", [req("Python"), req("RAG"), req("Requirement Analysis", category="soft")]),  # 100% 必備
        ("02", "ai-app", [req("Python"), req("RAG", required=False), req("Docker", required=False)]),
        ("03", "ml", [req("Python"), req("PyTorch"), req("LLM API"), req("LLM")]),
    ])
    r = samples_eval.evaluate(path, batch=1, synonyms={})
    assert [p["job_id"] for p in r["per_job"] if p["flag"] == "100% 必備"] == ["01", "03"]
    assert r["soft_tagged"] == [("01", "Requirement Analysis")]          # 名單外的 soft → 列出來（會保留）
    assert r["top10"][0] == ("Python", 6, 3) and r["top10"][1] == ("RAG", 3, 1)
    assert r["distinct"] == 7 and r["singletons"] == 5                   # Python、RAG 出現 2 份以上
    assert ("LLM", "LLM API", "包含") in r["candidates"]
    assert r["batch"]["top10_churn"] >= 0 and r["batch"]["new_total"] == 3   # 03 帶進 PyTorch、LLM API、LLM
    assert "樣本檢查" in samples_eval.to_markdown(r)


# ===== extract_eval（L0＋L1）=====

BODY = """【工作內容】
開發 RAG 系統
【條件要求】
熟悉 Python 或 C#/.NET
熟悉 Docker
【涵蓋技術】
LLM / VLM / ASR
【加分條件】
有 Kubernetes 經驗者佳"""


def test_grounded_ignores_spaces_and_fullwidth():
    assert ee.grounded(req("Docker", text="熟悉　Ｄｏｃｋｅｒ"), BODY)
    assert not ee.grounded(req("Rust", text="熟悉 Rust"), BODY)


def test_or_lint_finds_either_or_split_into_two_required():
    job = JobRequirements(title="x", requirements=[
        req("Python", text="Python 或 C#/.NET"), req("C#/.NET", text="Python 或 C#/.NET"), req("Docker", text="熟悉 Docker")])
    found = ee.or_lint(job, BODY)
    assert len(found) == 1 and found[0]["skills"] == ["Python", "C#/.NET"]


def test_required_lint_flags_overview_and_preferred():
    job = JobRequirements(title="x", requirements=[
        req("VLM", text="LLM / VLM / ASR"),                                # 涵蓋技術卻標必備 → 可疑
        req("Kubernetes", text="有 Kubernetes 經驗者佳"),                  # 者佳卻標必備 → 可疑
        req("Docker", required=False, text="熟悉 Docker"),                  # 條件要求卻標加分 → 可疑
        req("Python", text="Python 或 C#/.NET")])                          # 正常
    flagged = {x["skill"]: x["marked"] for x in ee.required_lint(job, BODY)}
    assert flagged == {"VLM": "必備", "Kubernetes": "必備", "Docker": "加分"}


def test_align_and_gold_metrics():
    gold = ee.GoldJob(job_id="15", skills=[
        ee.GoldSkill(skill="Python", required=True),
        ee.GoldSkill(skill="Vector Database", required=True, accept=["Vector Search"]),
        ee.GoldSkill(skill="LLM", required=False, section="overview"),
        ee.GoldSkill(skill="Kubernetes", required=True)],                 # 模型漏抓
        ignore=["English"], fp=[ee.GoldFP(skill="Rust", type="fabricated")])
    pred = [req("Python"), req("python"), req("Vector Search"), req("LLM"), req("English"), req("Rust")]
    a = ee.align(pred, gold, synonyms={})
    assert [g.skill for _, g in a["tp"]] == ["Python", "Vector Database", "LLM"]
    assert [r.skill for r in a["fp"]] == ["Rust"]                        # English 在 ignore
    assert [g.skill for g in a["fn"]] == ["Kubernetes"] and len(a["split"]) == 1
    m = ee.gold_metrics(a, gold)
    assert m["recall"] == "3/4 (0.75)" and m["over_required"] == "1/1 (1.00)" and m["fabricated"] == 1


# ===== stability（S1）=====

class Cycle:
    """每次呼叫輪流交給下一個假 Claude → 模擬「同樣輸入，LLM 每次回答不同」。"""

    def __init__(self, clients):
        self.clients, self.i, self.messages = clients, 0, self

    @property
    def calls(self):
        return sum(c.calls for c in self.clients)

    def create(self, **kwargs):
        c = self.clients[self.i % len(self.clients)]
        self.i += 1
        return c.create(**kwargs)


PROFILE = Profile.model_validate({"evidence": [{"id": "p", "type": "project", "summary": "x"}],
                                  "skills": [{"name": "AI Agent", "level": "basic", "evidence": ["p"]}]})


def mapping(relation):
    return {"mappings": [{"requirement_skill": "LangGraph", "profile_skill": "AI Agent" if relation != "none" else None,
                          "relation": relation, "reason": "x"}]}


def test_s1_reruns_saves_and_measures_flips(tmp_path, monkeypatch, fake):
    monkeypatch.chdir(tmp_path)
    write_samples(tmp_path, [("01", "ai-app", [req("LangGraph")])])
    samples = stability.load_samples()
    client = Cycle([fake(mapping("related")), fake(mapping("none"))])     # 第一次「部分」、第二次「沒有」
    out = stability.run_s1(PROFILE, samples, n=2, budget=1.0, client=client, out_root=tmp_path / "eval")
    assert client.calls == 2
    assert not (tmp_path / "cache").exists()                            # S1 不碰拆解快取
    all_runs, meta = stability.load_runs(out["folder"])
    assert meta["n"] == 2 and [r[0].results[0].status for r in all_runs] == ["partial", "missing"]
    res = stability.match_stability(all_runs, {"01": "ai-app"})
    assert res["status_flip_rate"] == "1/1 (1.00)" and res["priority_max_jump"] == "2（langgraph）"


def test_s1_budget_guard_stops_before_calling(tmp_path, monkeypatch, fake):
    monkeypatch.chdir(tmp_path)
    write_samples(tmp_path, [("01", "ai-app", [req("LangGraph")])])
    client = fake(mapping("related"))
    with pytest.raises(SystemExit):
        stability.run_s1(PROFILE, stability.load_samples(), n=5, budget=0.0001, client=client, out_root=tmp_path)
    assert client.calls == 0                                            # 先檢查再花錢


def test_rescore_new_rule_jumps_less_than_old():
    job = JobRequirements(title="x", requirements=[])
    def runs(s2):  # CI/CD 在兩份職缺都是必備；第二份的判斷在兩次重跑之間翻轉（部分 ↔ 沒有）
        return [Run("01", job, [MatchResult(skill="CI/CD", required=True, status="partial")]),
                Run("02", job, [MatchResult(skill="CI/CD", required=True, status=s2)])]
    res = stability.rescore([runs("partial"), runs("missing")], {})
    assert res["現在（逐份加總）"]["priority_max_jump"] == "2（ci/cd）"     # 4 ↔ 6
    assert res["舊規則（取多數 × 權重）"]["priority_max_jump"] == "4（ci/cd）"  # 4 ↔ 8：平手取差，整個加倍
    assert "穩定度 S1" in stability.to_markdown(res, {"n": 2, "n_jobs": 2})


# ===== fit_eval =====

def test_fit_eval_would_apply(tmp_path):
    (tmp_path / "fit.csv").write_text("﻿job_id,title,company,role,would_apply,note\n"
                                      "01,A,,,3,\n02,B,,,2,\n03,C,,,1,要 5 年\n04,D,,,,\n", encoding="utf-8")
    gold = fit_eval.load_gold(tmp_path / "fit.csv")
    assert sorted(gold) == ["01", "02", "03"]                            # 沒填的 04 跳過；BOM 不影響
    job = JobRequirements(title="x", requirements=[])
    def run(j, status):
        return Run(j, job, [MatchResult(skill="Python", required=True, status=status)])
    runs = [[run("01", "have"), run("02", "partial"), run("03", "missing"), run("04", "have")]]
    r = fit_eval.evaluate(runs, gold)
    assert r["rated"] == 3 and r["spearman"] == 1.0 and r["top5_would_apply"] == 1
    assert (r["mean_fit_apply"], r["mean_fit_not_apply"]) == (1.0, 0.0)
    assert "和你的評分比" in fit_eval.to_markdown(r)


def test_fit_eval_runs_without_ratings(tmp_path):
    job = JobRequirements(title="x", requirements=[])
    runs = [[Run("01", job, [MatchResult(skill="Python", required=True, status="have")]),
             Run("02", job, [MatchResult(skill="Python", required=True, status="missing")])]]
    r = fit_eval.evaluate(runs, fit_eval.load_gold(tmp_path / "nope.csv"))   # 檔案不存在 → 只做結構檢查
    assert r["rated"] == 0 and r["program_top5"][0] == ("01", 1.0)
    assert "只做結構檢查" in fit_eval.to_markdown(r)