"""網頁 demo：上傳履歷 → profile 草稿 → 和樣本職缺比對 → 市場缺口報告。

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
from datetime import date

os.environ["SAVE_TRAJECTORIES"] = "0"  # 網頁版一律不存軌跡（個資）

import streamlit as st
from anthropic import APIError

from job_agent.llm import LLMError, model_for
from job_agent.market import aggregate, to_markdown
from job_agent.pipeline import analyze
from job_agent.resume import profile_from_text, read_resume_bytes, status_of, to_yaml
from job_agent.samples import SAMPLES_FILE, load_samples

MAX_RUNS_PER_SESSION = 3
EST_COST_PER_RUN = 0.02  # 預估每次的費用（實測約 $0.012，抓寬一點）

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


@st.cache_data
def get_samples():
    return load_samples()


# ===== 分析（呼叫 job_agent）=====

def run_analysis(uploaded, samples) -> dict | None:
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
    add_spent(cost)
    return {"profile": profile, "notes": notes, "runs": runs, "failed": failed, "cost": cost}


# ===== 畫面 =====

def show_samples(samples) -> None:
    with st.expander(f"樣本職缺（{len(samples)} 份）"):
        st.caption("作者自己收集的台灣 AI 相關職缺（2026 年 10–11 月）。只保留技能清單，不含職缺原文。"
                   "如果你找的是其他領域的工作，這份報告的參考價值會比較低。")
        st.dataframe(
            [{"編號": jid, "職稱": j.title, "公司": j.company or "—",
              "必備技能數": sum(r.required for r in j.requirements)} for jid, j in samples],
            hide_index=True, use_container_width=True,
        )


def show_result(result: dict) -> None:
    profile, runs = result["profile"], result["runs"]
    c1, c2, c3 = st.columns(3)
    c1.metric("作品（證據）", len(profile.evidence))
    c2.metric("技能", len(profile.skills), f"有作品 {sum(bool(s.evidence) for s in profile.skills)}", delta_color="off")
    c3.metric("這次費用", f"US${result['cost']:.4f}")
    if result["failed"]:
        st.warning(f"有 {len(result['failed'])} 份職缺比對失敗（{'、'.join(result['failed'])}），報告只包含其他 {len(runs)} 份。")

    tab_report, tab_draft = st.tabs(["📊 市場缺口報告", "📝 技能草稿"])
    with tab_report:
        st.info("⚠️ 表示同一個技能在不同職缺被判成不同狀態（判斷不穩定），排名可能會跳動，請當作參考。")
        st.markdown(to_markdown(runs, aggregate(runs), cost_usd=result["cost"]))
    with tab_draft:
        st.caption("從履歷整理出來的草稿。只有出現在專案／工作／研究描述裡的技能才算「有作品」；"
                   "只列在技能欄的算「部分」。level 是模型的推測（最高只到 working），請自己確認。")
        st.subheader("作品")
        st.dataframe([{"id": e.id, "類型": e.type, "摘要": e.summary} for e in profile.evidence],
                     hide_index=True, use_container_width=True)
        st.subheader("技能")
        st.dataframe(
            [{"技能": s.name, "level": s.level, "報告狀態": status_of(s), "作品": ", ".join(s.evidence) or "—"}
             for s in profile.skills],
            hide_index=True, use_container_width=True,
        )
        if result["notes"]:
            with st.expander("程式清理紀錄"):
                st.markdown("\n".join(f"- {n}" for n in result["notes"]))
        st.download_button("下載草稿（profile.yaml）", to_yaml(profile), file_name="profile_draft.yaml",
                           mime="text/yaml")


def main() -> None:
    load_secrets()
    st.title("🧭 履歷技能缺口分析")
    st.markdown("上傳履歷，看看你的技能和**樣本職缺**的要求差在哪裡：哪些有作品可以證明、哪些只有一部分、"
                "哪些還沒有，以及最值得先補的技能。")

    if not SAMPLES_FILE.exists():
        st.error(f"找不到樣本職缺 {SAMPLES_FILE}：請先執行 python -m job_agent.samples jobs")
        st.stop()
    samples = get_samples()
    show_samples(samples)

    uploaded = st.file_uploader("上傳履歷（PDF、Word、文字檔）", type=["pdf", "docx", "txt", "md"])
    st.caption("🔒 履歷不會被儲存。送給 Claude 之前，程式會先遮掉 email 和電話；結果只留在這個瀏覽器分頁，關掉就沒了。")

    used = st.session_state.get("runs_used", 0)
    if st.button("產生缺口報告", type="primary", disabled=uploaded is None):
        if used >= MAX_RUNS_PER_SESSION:
            st.error(f"每次最多分析 {MAX_RUNS_PER_SESSION} 份履歷。")
        elif budget_left() < EST_COST_PER_RUN:
            st.error("今天的試用額度已經用完了，請明天再來。")
        else:
            st.session_state["runs_used"] = used + 1
            try:
                st.session_state["result"] = run_analysis(uploaded, samples)
            except ValueError as e:  # 檔案格式、字數、掃描檔
                st.error(str(e))
            except (LLMError, APIError) as e:
                st.error(f"呼叫 Claude 失敗，請稍後再試（{type(e).__name__}）")

    if "result" in st.session_state:  # 存在 session_state：按下載按鈕重新整理時結果不會消失
        show_result(st.session_state["result"])


main()