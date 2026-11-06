"""技能名稱統一（程式＋同義詞表，不靠 LLM）。

兩層：
1. key()：把寫法差異抹掉 → Scikit-Learn、scikit-learn、scikit learn 都變成 "scikitlearn"
2. 同義詞表（skill_synonyms.yaml）：OpenAI API、Gemini API → LLM API Integration

比對（match.py）和之後的市場缺口報告都用這裡，確保「同一個技能」只有一種名字。
"""

import re
import unicodedata
from pathlib import Path

import yaml

from job_agent.schemas import Requirement

SYNONYMS_FILE = Path(__file__).parent.parent / "skill_synonyms.yaml"

# 只去掉空白、-、_、.（不去掉 + 和 #，不然 C++、C# 會變成 C）
_IGNORED = re.compile(r"[\s\-_.]+")


def key(name: str) -> str:
    """比較用的鍵：全形轉半形、小寫、去掉空白和 -_. → 寫法不同但其實一樣的名稱會得到同一個鍵。"""
    return _IGNORED.sub("", unicodedata.normalize("NFKC", name).lower())


def load_synonyms(path: str | Path = SYNONYMS_FILE) -> dict[str, str]:
    """讀同義詞表 → {key(任何寫法): 統一名稱}。檔案不存在就回傳空表（不統一，只做 key 比對）。"""
    path = Path(path)
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    table = {}
    for canonical, variants in data.items():
        for name in [canonical, *(variants or [])]:
            k = key(str(name))
            if k in table and table[k] != canonical:
                raise ValueError(f"{path}: '{name}' is listed under both '{table[k]}' and '{canonical}'")
            table[k] = canonical
    return table


def canonical(name: str, synonyms: dict[str, str]) -> str:
    """查表：有在同義詞表裡就換成統一名稱，沒有就保持原樣。"""
    return synonyms.get(key(name), name.strip())


def unify(requirements: list[Requirement], synonyms: dict[str, str]) -> list[Requirement]:
    """把每個需求的 skill 換成統一名稱（original_text 保留原文，之後還查得到職缺怎麼寫）。"""
    return [r.model_copy(update={"skill": canonical(r.skill, synonyms)}) for r in requirements]