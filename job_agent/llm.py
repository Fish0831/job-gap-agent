"""呼叫 Claude：用 structured outputs 強制照 Pydantic 格式回答，並記錄 token、費用、時間。

結構化輸出 4 步驟：
1. Pydantic 定義格式（schemas.py）
2. transform_schema() 轉成 JSON Schema → 放進 output_config.format
3. API 保證回傳的 JSON 符合這個格式
4. model_validate_json() 用同一個 Pydantic 格式再檢查一次

（11/5 從「強制使用工具 tool_choice」改成 structured outputs：
  Sonnet 5.5、Opus 5.5 不接受強制使用工具，structured outputs 則是所有模型都支援。）
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path

from anthropic import Anthropic, transform_schema
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

DEFAULT_MODEL = "claude-haiku-5-5"
# 美元／每百萬 token（2026-11 官方價格；Haiku 是 prompt ≤ 100k token 的價格）
PRICES = {
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00},
    "claude-opus-5-5": {"input": 4.00, "output": 20.00},
}
MAX_COST_PER_CALL = 0.20  # 單次呼叫超過這個金額就停下來（Opus 會思考，所以比原本的 0.05 高）
TRAJECTORY_DIR = Path("trajectories")


class LLMError(Exception):
    """LLM 沒有照預期回答（被截斷、拒答、沒有回傳內容、超過費用上限、模型沒有價格）。"""


def model_for(step: str) -> str:
    """每個步驟用哪個模型：讀 .env 的 EXTRACT_MODEL／MATCH_MODEL，沒設就用預設。"""
    model = os.getenv(f"{step.upper()}_MODEL", DEFAULT_MODEL)
    if model not in PRICES:
        raise LLMError(f"unknown model {model!r}: add its price to PRICES in llm.py first")
    return model


def cost_of(usage, model: str) -> float:
    price = PRICES[model]
    return (usage.input_tokens * price["input"] + usage.output_tokens * price["output"]) / 1_000_000


def call_structured(
    *,
    system: str,
    user: str,
    schema: type[BaseModel],
    name: str,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 8000,
    client: Anthropic | None = None,
) -> tuple[BaseModel, dict]:
    """要求 Claude 用指定的 Pydantic 格式回答，回傳（通過檢查的物件, 用量紀錄）。"""
    if model not in PRICES:
        raise LLMError(f"unknown model {model!r}: add its price to PRICES in llm.py first")
    client = client or Anthropic()

    start = time.perf_counter()
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"format": {"type": "json_schema", "schema": transform_schema(schema)}},
    )
    seconds = time.perf_counter() - start

    stats = {
        "model": model,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cost_usd": round(cost_of(response.usage, model), 6),
        "seconds": round(seconds, 2),
        "stop_reason": response.stop_reason,
    }

    # 模型可能先思考（thinking 區塊），答案在 text 區塊
    text = next((block.text for block in response.content if block.type == "text"), None)
    _save_trajectory(name, model, system, user, text, stats)

    if stats["cost_usd"] > MAX_COST_PER_CALL:
        raise LLMError(f"cost ${stats['cost_usd']} exceeds limit ${MAX_COST_PER_CALL}")
    if response.stop_reason == "max_tokens":
        raise LLMError("response was cut off (max_tokens); try a larger max_tokens")
    if response.stop_reason == "refusal":
        raise LLMError("model refused to answer")
    if text is None:
        raise LLMError("model did not return a text block")

    result = schema.model_validate_json(text)  # 格式不對 → pydantic.ValidationError
    return result, stats


def _save_trajectory(name: str, model: str, system: str, user: str, text, stats: dict) -> None:
    TRAJECTORY_DIR.mkdir(exist_ok=True)
    path = TRAJECTORY_DIR / f"{name}_{model}_{datetime.now():%Y%m%d_%H%M%S}.json"
    try:
        output = json.loads(text) if text else None
    except json.JSONDecodeError:
        output = text  # 壞掉的 JSON 也存下來，方便除錯
    record = {"system": system, "user": user, "output": output, "stats": stats}
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")