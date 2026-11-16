"""職務類別：每份職缺標一個類別 → 網頁上先選目標職務，市場報告只統計那一類。

類別由人寫在職缺檔開頭（`role: ai-app`），不讓 LLM 猜：
收集職缺時本來就會讀過一遍，人標又快又準，而且結果固定、不花錢。
要新增或改名類別，只改這個檔案的 ROLES／ROLE_HINTS。
"""

from pathlib import Path

from job_agent.extract import read_job

ROLES = {
    "ai-app": "AI 應用／Agent 工程師",
    "ml": "ML 工程師",
    "ai-product": "AI 產品工程師",
}

# 標類別時的判斷依據（網頁上也會顯示給使用者看）
ROLE_HINTS = {
    "ai-app": "用 LLM 做應用：RAG、Agent、Tool Calling、串接 LLM API",
    "ml": "訓練、評估、部署模型：PyTorch／TensorFlow、特徵工程、MLOps",
    "ai-product": "從需求到上線做出產品功能：需求分析、規格、全端開發，常用 AI coding 工具",
}


def check_role(role: str, where: str = "") -> str:
    """role 不在 ROLES 裡就報錯（錯字、忘了寫都會擋下來）。"""
    if role not in ROLES:
        raise ValueError(f"{where}role 必須是 {'、'.join(ROLES)} 其中一個（現在是 {role!r}）")
    return role


def role_of(path: str | Path) -> str:
    """讀職缺檔開頭的 role:。"""
    meta, _ = read_job(path)
    return check_role(meta.get("role", ""), f"{Path(path).name}: ")


def filter_role(files: list[Path], role: str) -> list[Path]:
    """只留下某個職務類別的職缺檔。"""
    check_role(role)
    return [f for f in files if role_of(f) == role]