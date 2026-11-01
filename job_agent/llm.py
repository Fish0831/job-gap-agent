"""呼叫 Claude：用 tool use 強制照 Pydantic 格式回答，並記錄 token、費用、時間。"""

import json
import time
from datetime import datetime
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

MODEL = "claude-haiku-5-5"
PRICE_PER_MTOK = {"input": 0.10, "output": 0.50}  # 美元／每百萬 token（同 token-cost-estimator 的 models.json）
MAX_COST_PER_CALL = 0.05  # 單次呼叫超過這個金額就停下來
TRAJECTORY_DIR = Path("trajectories")


class LLMError(Exception):
    """LLM 沒有照預期回答（被截斷、沒用工具、超過費用上限）。"""


def cost_of(usage) -> float:
    return (
        usage.input_tokens * PRICE_PER_MTOK["input"]
        + usage.output_tokens * PRICE_PER_MTOK["output"]
    ) / 1_000_000


def call_structured(
    *,
    system: str,
    user: str,
    schema: type[BaseModel],
    tool_name: str,
    tool_description: str,
    max_tokens: int = 4096,
    client: Anthropic | None = None,
) -> tuple[BaseModel, dict]:
    """要求 Claude 用指定的 Pydantic 格式回答，回傳（通過檢查的物件, 用量紀錄）。"""
    client = client or Anthropic()
    tool = {
        "name": tool_name,
        "description": tool_description,
        "input_schema": schema.model_json_schema(),
    }

    start = time.perf_counter()
    response = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        tools=[tool],
        tool_choice={"type": "tool", "name": tool_name},
    )
    seconds = time.perf_counter() - start

    stats = {
        "model": MODEL,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cost_usd": round(cost_of(response.usage), 6),
        "seconds": round(seconds, 2),
        "stop_reason": response.stop_reason,
    }

    tool_input = next(
        (block.input for block in response.content if block.type == "tool_use"), None
    )
    _save_trajectory(tool_name, system, user, tool_input, stats)

    if stats["cost_usd"] > MAX_COST_PER_CALL:
        raise LLMError(f"cost ${stats['cost_usd']} exceeds limit ${MAX_COST_PER_CALL}")
    if response.stop_reason == "max_tokens":
        raise LLMError("response was cut off (max_tokens); try a larger max_tokens")
    if tool_input is None:
        raise LLMError("model did not return a tool_use block")

    result = schema.model_validate(tool_input)  # 格式不對 → pydantic.ValidationError
    return result, stats


def _save_trajectory(name: str, system: str, user: str, output, stats: dict) -> None:
    TRAJECTORY_DIR.mkdir(exist_ok=True)
    path = TRAJECTORY_DIR / f"{name}_{datetime.now():%Y%m%d_%H%M%S}.json"
    record = {"system": system, "user": user, "output": output, "stats": stats}
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
