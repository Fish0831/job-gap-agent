"""網頁 demo：選目標職務 → 上傳履歷 → profile 草稿 → 和那一類的樣本職缺比對 → 市場缺口報告。

執行：streamlit run app.py
這個檔案只負責「畫面」：讀檔、轉草稿、比對、統計全部呼叫 job_agent 裡現有的函式。

保護（別人用的是我的 API key）：
- 每個連線最多分析 MAX_RUNS_PER_SESSION 次（重新整理頁面就會重置，所以只是擋手滑）
- 全站每日預算 DAILY_BUDGET_USD（真正的上限；伺服器重開會歸零，所以 Console 也要設花費上限）
- 上傳檔案大小上限在 .streamlit/config.toml；履歷字數上限在 resume.MAX_CHARS
- 不存執行軌跡（SAVE_TRAJECTORIES=0）、不存履歷，結果只放在這個連線的記憶體
"""

import os
import threading
import time
from datetime import date

os.environ["SAVE_TRAJECTORIES"] = "0"  # 網頁版一律不存軌跡（個資）

import altair as alt
import streamlit as st
from anthropic import APIError

from job_agent.llm import LLMError, model_for
from job_agent.market import SkillStat, aggregate, priority, rank_jobs, role_fit, strengths, top_gaps
from job_agent.pipeline import analyze
from job_agent.resume import profile_from_text, read_resume_bytes, status_of, to_yaml
from job_agent.roles import ROLE_HINTS, ROLES
from job_agent.samples import SAMPLES_FILE, load_roles, load_samples

MAX_RUNS_PER_SESSION = 3
MIN_ROLE_SAMPLES = 3     # 某個職務類別至少要有幾份樣本，才放進選項／算「最接近的類別」（太少統計沒意義）
TOP_FITS = 5             # 「最適合你的職缺」列幾份
ALL_ROLES = "全部職務"
STATUS_TEXT = {"have": "有", "partial": "部分", "missing": "沒有"}
STATUS_COLOR = {"missing": "#E24B4A", "partial": "#EF9F27"}  # 長條圖：沒有＝紅、部分＝橘

st.set_page_config(page_title="job-gap-agent｜履歷缺口分析", page_icon="🧭", layout="wide")


# ===== 設定與上限 =====

def load_secrets() -> None:
    """部署時 key 放在 Streamlit 的 secrets；本機沒有 secrets 檔就用 .env（llm.py 會讀）。"""
    try:
        for name in ("ANTHROPIC_API_KEY", "DAILY_BUDGET_USD"):
            if name in st.secrets:
                os.environ.setdefault(name, str(st.secrets[name]))
    except Exception:  # 本機沒有 .streamlit/secrets.toml
        pass


@st.cache_resource
def _budget() -> dict:
    """全站共用（所有使用者同一份）的每日花費紀錄。"""
    return {"day": "", "spent": 0.0, "lock": threading.Lock()}


def budget_left() -> float:
    b = _budget()
    with b["lock"]:
        if b["day"] != date.today().isoformat():  # 換日 → 歸零
            b["day"], b["spent"] = date.today().isoformat(), 0.0
        return float(os.getenv("DAILY_BUDGET_USD", "1.0")) - b["spent"]


def add_spent(cost: float) -> None:
    b = _budget()
    with b["lock"]:
        b["spent"] += cost


def est_cost(n_jobs: int) -> float:
    """預估一次分析的費用。實測：履歷約 $0.0024＋每份職缺約 $0.001 → 抓兩倍寬。"""
    return 0.005 + 0.002 * n_jobs


@st.cache_data
def get_samples():
    return load_samples()


@st.cache_data
def get_roles():
    return load_roles()


# ===== 分析（呼叫 job_agent）=====

def run_analysis(uploaded, samples, role_label: str) -> dict | None:
    start = time.perf_counter()
    with st.status("分析中…", expanded=True) as status:
        st.write("① 讀取履歷")
        text = read_resume_bytes(uploaded.getvalue(), uploaded.name)
        st.write(f"② 整理成技能草稿（Claude {model_for('resume')}，約 15 秒）")
        profile, notes, resume_stats = profile_from_text(text)
        st.write(f"③ 和 {len(samples)} 份樣本職缺比對（同時進行）")
        bar = st.progress(0.0)
        runs, match_cost, failed = analyze(
            profile, samples, on_progress=lambda done, n: bar.progress(done / n, text=f"{done} / {n} 份")
        )
        status.update(label="完成", state="complete", expanded=False)
    cost = resume_stats["cost_usd"] + match_cost
    seconds = time.perf_counter() - start
    add_spent(cost)
    # 只寫進伺服器 logs（Manage app 才看得到）：費用、秒數，不含任何履歷內容
    print(f"[job-gap-agent] analysis done: cost ${cost:.4f}, {seconds:.1f}s, "
          f"role {role_label}, jobs {len(runs)}, failed {len(failed)}", flush=True)
    return {"profile": profile, "notes": notes, "runs": runs, "failed": failed, "cost": cost, "seconds": seconds,
            "role_label": role_label}


# ===== 畫面 =====

def choose_role(samples, roles: dict[str, str]) -> str | None:
    """選目標職務。回傳 role（例如 ai-app），選「全部」回傳 None。樣本太少的類別不放進選項。"""
    counts = {r: sum(roles.get(jid) == r for jid, _ in samples) for r in ROLES}
    options = [None] + [r for r in ROLES if counts[r] >= MIN_ROLE_SAMPLES]
    if len(options) == 1:  # 樣本還沒標職務類別 → 不顯示選項
        return None
    role = st.radio(
        "只看某個職務（選填）", options, horizontal=True,
        format_func=lambda r: f"{ALL_ROLES}（{len(samples)} 份）" if r is None else f"{ROLES[r]}（{counts[r]} 份）",
    )
    st.caption(ROLE_HINTS[role] if role else "不用選：所有樣本職缺一起統計，報告會告訴你最適合哪些職缺、最接近哪一類。")
    return role


def show_samples(samples, roles: dict[str, str]) -> None:
    with st.expander(f"樣本職缺（{len(samples)} 份）"):
        st.caption("作者自己收集的台灣 AI 相關職缺（2026 年 10–11 月）。只保留技能清單，不含職缺原文。"
                   "如果你找的是其他領域的工作，這份報告的參考價值會比較低。")
        st.dataframe(
            [{"編號": jid, "職務": ROLES.get(roles.get(jid, ""), "—"), "職稱": j.title, "公司": j.company or "—",
              "必備技能數": sum(r.required for r in j.requirements)} for jid, j in samples],
            hide_index=True, width="stretch",
        )


def gap_chart(gaps: list[SkillStat]) -> alt.Chart:
    """最該先補的技能：橫向長條圖，顏色＝我的狀態，⚠ ＝各份職缺的判斷不一致。"""
    data = [{
        "技能": s.skill + ("  ⚠" if not s.consistent else ""),
        "優先度": priority(s),
        "狀態": STATUS_TEXT[s.status],
        "必備": len(s.required_in),
        "加分": len(s.preferred_in),
        "各份判斷": "、".join(f"{j} {STATUS_TEXT[st_]}" for j, st_ in sorted(s.statuses.items())),
    } for s in gaps]
    return (
        alt.Chart(alt.Data(values=data))
        .mark_bar(cornerRadiusEnd=4)
        .encode(
            x=alt.X("優先度:Q", title="優先度（需求分數 × 缺口權重）"),
            y=alt.Y("技能:N", sort="-x", title=None,
                    axis=alt.Axis(labelOverlap=False, labelLimit=220, labelFontSize=13)),  # 每個技能名稱都要顯示
            color=alt.Color("狀態:N", scale=alt.Scale(domain=["沒有", "部分"],
                                                       range=[STATUS_COLOR["missing"], STATUS_COLOR["partial"]]),
                            legend=alt.Legend(orient="top", title=None)),
            tooltip=["技能:N", "優先度:Q", "狀態:N", "必備:Q", "加分:Q", "各份判斷:N"],
        )
        .properties(height=max(160, 34 * len(data)))
    )


def show_fit(runs, samples_by_id, roles: dict[str, str]) -> None:
    """最適合你的職缺 Top 5＋你最接近的職務類別。全部由程式計算（market.rank_jobs、role_fit），不花錢。"""
    fits = rank_jobs(runs)
    with st.container(border=True):
        st.markdown("**🎯 最適合你的職缺**")
        close = role_fit(fits, roles, min_jobs=MIN_ROLE_SAMPLES)
        if len(close) >= 2:  # 至少比較兩個類別才有意義（只分析一類時不顯示）
            best, avg, k = close[0]
            others = "、".join(f"{ROLES[r]} {a:.0%}" for r, a, _ in close[1:])
            st.markdown(f"你最接近 **{ROLES[best]}**（{k} 份平均適合度 {avg:.0%}；{others}）")
        st.dataframe(
            [{
                "適合度": f.fit,
                "職稱": samples_by_id[f.job_id].title if f.job_id in samples_by_id else f.job_id,
                "公司": (samples_by_id[f.job_id].company or "—") if f.job_id in samples_by_id else "—",
                "職務": ROLES.get(roles.get(f.job_id, ""), "—"),
                "必備缺口": f"{f.missing_required} / {f.n_required}",
            } for f in fits[:TOP_FITS]],
            column_config={"適合度": st.column_config.ProgressColumn("適合度", min_value=0, max_value=1,
                                                                    format="percent")},
            hide_index=True, width="stretch",
        )
        st.caption("適合度 = (有 + 0.5 × 部分) ÷ 必備技能數，只看必備技能，年資不算進來。"
                   "必備缺口 = 必備技能裡「沒有」的數量。")


def show_report(runs, samples_by_id, role_label: str, roles: dict[str, str]) -> None:
    stats = aggregate(runs)
    n = len(runs)
    required = [s for s in stats if s.required_in]
    counts = {k: sum(s.status == k for s in required) for k in STATUS_TEXT}
    gaps = top_gaps(stats)

    c1, c2, c3 = st.columns(3)
    card = dict(delta_color="off", delta_arrow="off", border=True)  # 小字只是說明，不是漲跌 → 不要箭頭
    c1.metric("比對的職缺", f"{n} 份", role_label, **card)
    c2.metric("必備技能：有／部分／沒有", f"{counts['have']} / {counts['partial']} / {counts['missing']}",
              "各技能跨職缺的判斷", **card)
    c3.metric("最該先補", gaps[0].skill if gaps else "—", f"優先度 {priority(gaps[0])}" if gaps else None, **card)

    show_fit(runs, samples_by_id, roles)

    with st.container(border=True):
        st.markdown("**最該先補的技能**")
        if gaps:
            st.altair_chart(gap_chart(gaps), width="stretch")
            st.caption("優先度 = 需求分數（必備 2、加分 1，逐份加總）× 缺口權重（沒有 2、部分 1）。"
                       "⚠ = 這個技能在不同職缺被判成不同狀態（判斷不穩定），排名可能會跳動。滑鼠移到長條上可以看各份的判斷。")
        else:
            st.success("樣本職缺的技能你都有作品證明了。")

    with st.container(border=True):
        st.markdown("**我最有競爭力的技能**")
        top = strengths(stats)
        st.markdown("　".join(f":green-background[{s.skill} · {len(s.required_in)}/{n} 份必備]" for s in top)
                    or "還沒有被多份職缺列為必備、而且有作品證明的技能。")

    with st.container(border=True):
        st.markdown("**完整統計**（點欄位名稱可以排序）")
        st.dataframe(
            [{
                "技能": s.skill,
                "必備": len(s.required_in),
                "加分": len(s.preferred_in),
                "我的狀態": STATUS_TEXT[s.status] + (" ⚠" if not s.consistent else ""),
                "優先度": priority(s),
                "出現在": "、".join(samples_by_id[j].company or j for j in s.required_in + s.preferred_in
                                    if j in samples_by_id),
            } for s in stats if len(s.required_in) + len(s.preferred_in) >= 2],
            column_config={
                "必備": st.column_config.ProgressColumn("必備（幾份）", min_value=0, max_value=n, format="%d"),
                "優先度": st.column_config.NumberColumn("優先度", format="%d"),
            },
            hide_index=True, width="stretch",
        )
        st.caption("只列出現在 2 份以上職缺的技能。⚠ = 判斷不穩定。")


def show_draft(result: dict) -> None:
    profile = result["profile"]
    st.caption("從履歷整理出來的草稿。只有出現在專案／工作／研究描述裡的技能才算「有作品」；"
               "只列在技能欄的算「部分」。level 是模型的推測（最高只到 working），請自己確認。")

    st.markdown("**作品（證據）**")
    cols = st.columns(2)
    for i, e in enumerate(profile.evidence):
        with cols[i % 2].container(border=True):
            st.markdown(f"**{e.id}**　:gray[{e.type}]")
            st.caption(e.summary)

    with_ev = [s for s in profile.skills if s.evidence]
    without = [s for s in profile.skills if not s.evidence]
    st.markdown(f"**有作品證明的技能**（{len(with_ev)}）")
    st.markdown("　".join(f":green-background[{s.name} · {s.level}]" for s in with_ev) or "—")
    st.markdown(f"**只列在技能欄的技能**（{len(without)}，報告會算「部分」）")
    st.markdown("　".join(f":orange-background[{s.name}]" for s in without) or "—")

    if result["notes"]:
        with st.expander("程式清理紀錄"):
            st.markdown("\n".join(f"- {n}" for n in result["notes"]))
    st.download_button("下載草稿（profile.yaml）", to_yaml(profile), file_name="profile_draft.yaml",
                       mime="text/yaml")


def show_result(result: dict, samples, roles: dict[str, str]) -> None:
    if result["failed"]:
        st.warning(f"有 {len(result['failed'])} 份職缺比對失敗（{'、'.join(result['failed'])}），"
                   f"報告只包含其他 {len(result['runs'])} 份。")
    tab_report, tab_draft = st.tabs(["📊 市場缺口報告", "📝 技能草稿"])
    with tab_report:
        show_report(result["runs"], dict(samples), result.get("role_label", ALL_ROLES), roles)
    with tab_draft:
        show_draft(result)


def main() -> None:
    load_secrets()
    st.title("🧭 履歷技能缺口分析")
    st.markdown("上傳履歷，看看你的技能和**樣本職缺**的要求差在哪裡：哪些有作品可以證明、哪些只有一部分、"
                "哪些還沒有、最值得先補的技能，以及**最適合你的職缺和職務類別**。")

    if not SAMPLES_FILE.exists():
        st.error(f"找不到樣本職缺 {SAMPLES_FILE}：請先執行 python -m job_agent.samples jobs")
        st.stop()
    samples, roles = get_samples(), get_roles()
    role = choose_role(samples, roles)
    chosen = [(jid, job) for jid, job in samples if role is None or roles.get(jid) == role]
    role_label = ROLES[role] if role else ALL_ROLES
    show_samples(chosen, roles)

    uploaded = st.file_uploader("上傳履歷（PDF、Word、文字檔，最大 10 MB）", type=["pdf", "docx", "txt", "md"])
    st.caption("🔒 履歷不會被儲存。送給 Claude 之前，程式會先遮掉 email 和電話；結果只留在這個瀏覽器分頁，關掉就沒了。")

    used = st.session_state.get("runs_used", 0)
    if st.button("產生缺口報告", type="primary", disabled=uploaded is None):
        if used >= MAX_RUNS_PER_SESSION:
            st.error(f"每次最多分析 {MAX_RUNS_PER_SESSION} 份履歷。")
        elif budget_left() < est_cost(len(chosen)):
            st.error("今天的試用額度已經用完了，請明天再來。")
        else:
            st.session_state["runs_used"] = used + 1
            try:
                st.session_state["result"] = run_analysis(uploaded, chosen, role_label)
            except ValueError as e:  # 檔案格式、字數、掃描檔
                st.error(str(e))
            except (LLMError, APIError) as e:
                st.error(f"呼叫 Claude 失敗，請稍後再試（{type(e).__name__}）")

    if "result" in st.session_state:  # 存在 session_state：按下載按鈕重新整理時結果不會消失
        show_result(st.session_state["result"], samples, roles)  # 用全部樣本查公司名稱：分析後換了職務也查得到


main()