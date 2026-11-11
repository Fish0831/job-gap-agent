"""網頁版的流程：一份 profile × 多份樣本職缺 → 平行比對 → 市場報告需要的 Run 清單。

為什麼要平行：10 份職缺一份接一份比對，每份要等 Claude 幾秒 → 使用者要等 30–60 秒。
比對之間彼此不相關，所以同時送出（ThreadPoolExecutor），總時間 ≈ 最慢的那一份。

邏輯和畫面分開：這裡不碰 Streamlit，進度用 on_progress 回呼告訴畫面 → 可以用假的 Claude 測試。
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from anthropic import APIError

from job_agent.llm import LLMError
from job_agent.market import Run
from job_agent.match import match
from job_agent.normalize import load_synonyms
from job_agent.schemas import JobRequirements, Profile

MAX_WORKERS = 5  # 同時最多幾個請求（太多可能碰到 API 的速率限制）


def analyze(
    profile: Profile,
    samples: list[tuple[str, JobRequirements]],
    client=None,
    max_workers: int = MAX_WORKERS,
    on_progress: Callable[[int, int], None] | None = None,
) -> tuple[list[Run], float, list[str]]:
    """回傳（比對成功的 Run，照樣本順序；總費用；失敗的職缺編號）。一份失敗不影響其他份。"""
    synonyms = load_synonyms()  # 只讀一次，大家共用
    runs: dict[int, Run] = {}
    failed: list[int] = []
    total = 0.0

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(match, job, profile, client, None, synonyms): i
            for i, (_, job) in enumerate(samples)
        }
        for done, future in enumerate(as_completed(futures), 1):  # 誰先做完就先處理誰
            i = futures[future]
            job_id, job = samples[i]
            try:
                results, stats = future.result()
                runs[i] = Run(job_id=job_id, job=job, results=results)
                total += stats["cost_usd"]
            except (LLMError, APIError, ValueError):
                failed.append(i)
            if on_progress:
                on_progress(done, len(samples))

    # 平行執行時誰先做完不一定 → 回傳前照樣本順序排好，結果才固定
    return [runs[i] for i in sorted(runs)], total, [samples[i][0] for i in sorted(failed)]