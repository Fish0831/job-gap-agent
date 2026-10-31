# 求職 Agent 設計文件（v0.2，2026-10-31）

> 專案名稱 **job-gap-agent**。
> 範圍：第一版（v1，預計 11/8 完成）。之後的版本列在最後。

## 1. 一句話

**貼上一份職缺，告訴我：哪些技能我已經有作品可以證明、哪些只有一部分、哪些完全沒有，以及下一步最值得補什麼。**

## 2. 使用者與使用情境

使用者：我自己（AI Product Engineer／AI 應用工程師／ML Engineer 方向，研發替代役期間準備轉職）。

| 情境 | 我會怎麼用 |
|---|---|
| 看到一個有興趣的職缺 | 貼進去，3 分鐘內知道差距在哪 |
| 每個月檢討學習方向 | 看累積的報告裡，哪個技能最常被標成「沒有」→ 下個月優先學 |
| 準備面試 | 看報告的「證據」欄，知道每個要求該拿哪個作品來講 |

## 3. 輸入

| 輸入 | 格式 | 說明 |
|---|---|---|
| 職缺 | 純文字檔（`jobs/*.txt`），中文或英文 | 從求職網站**手動複製貼上**，不寫爬蟲 |
| 我的技能檔 | `profile.yaml`（私人，不公開；repo 裡放 `profile.example.yaml` 範例） | 列出作品／經歷（每個有 id），以及每個技能的程度和對應的作品 |

`profile.yaml` 的樣子：

```yaml
evidence:
  - id: token-cost-estimator
    type: project
    summary: LLM 成本估算工具：CLI、FastAPI、pytest，GitHub 公開
    url: https://github.com/Fish0831/token-cost-estimator
  - id: agent-experiment
    type: project
    summary: 手寫 agent loop、6 個工具、多代理人 vs 單一代理人比較實驗（費用、正確率）
  - id: master-thesis
    type: research
    summary: 碩論：DRL（DQN、PPO、SAC）用於平台動態定價

skills:
  - name: FastAPI
    level: basic
    evidence: [token-cost-estimator]
  - name: AI Agent
    level: working
    aliases: [LLM Agent, 代理人, tool use]
    evidence: [agent-experiment]
  - name: Reinforcement Learning
    level: strong
    aliases: [強化學習, DRL]
    evidence: [master-thesis]
  - name: Docker
    level: basic
    evidence: []          # 會一點但沒有作品 → 算「部分有」
```

**level（v0.2 新增）**：有作品不等於熟練，所以每個技能另外標程度。

| level | 意思 |
|---|---|
| `basic` | 做過，但需要查資料或有人帶 |
| `working` | 能自己獨立做出來 |
| `strong` | 能解釋原理、自己除錯、教別人 |

## 4. 輸出：缺口報告（Markdown）

```
# 缺口報告：AI 應用工程師 @ 某公司（2026-11-05）

符合度：必備 5/8 有證據、2/8 部分、1/8 沒有；加分 1/4

| 技能 | 必備/加分 | 狀態 | 證據 | 說明 |
|---|---|---|---|---|
| Python | 必備 | ✅ 有（working） | token-cost-estimator | |
| RAG | 必備 | ❌ 沒有 | — | 職缺原文：「熟悉 RAG 架構與向量資料庫」 |
| FastAPI | 必備 | ✅ 有（basic） | token-cost-estimator | 有作品但還不熟 |
| Docker | 必備 | 🟡 部分 | — | 技能檔有列，但沒有作品 |
| LangGraph | 加分 | 🟡 部分 | agent-experiment | 沒用過 LangGraph，但手寫過 agent loop |

## 建議優先補強（最多 3 項）
1. RAG：……（為什麼優先、建議怎麼補）
```

## 5. 判斷規則

| 狀態 | 條件 |
|---|---|
| ✅ 有 | 技能檔有這個技能，**而且至少有一個作品當證據** |
| 🟡 部分 | 技能檔有列但沒有證據；或沒有完全一樣的技能，但有相關的作品（例：要 LangGraph，我有手寫 agent loop） |
| ❌ 沒有 | 以上都不是 |

「有」的技能會在報告裡顯示 level；`basic` 的必備技能也會列入「建議優先補強」的候選。

**規則：證據只能引用 `profile.yaml` 裡存在的 id。** LLM 回傳的證據 id 會用程式檢查，不存在就算錯誤，避免它編造我沒有的經歷。

## 6. 系統怎麼運作（v1）

```
職缺文字 ──► ① 拆解需求（LLM，結構化輸出）──► JobRequirements
                                                    │
profile.yaml ──► ② 讀取技能檔 ─────────────────────►├──► ③ 比對（LLM + 程式檢查證據 id）──► MatchResult 清單
                                                                                        │
                                                                ④ 產生報告（程式 + LLM 寫建議）──► report.md
```

**中英混合的處理：** 拆解時要求 LLM 把每個技能轉成**統一的英文名稱**（例：「向量資料庫」→ `Vector Database`、「K8s」→ `Kubernetes`），同時保留職缺原文。比對時再用技能檔的 `aliases` 補足。

**誠實說明：v1 比較像固定流程（workflow），還不是會自己決定下一步的 agent。** 這是刻意的：先把每個工具做穩、能測試，第 3 週（RAG）再讓 agent loop（沿用 `run_agent`）自己決定要搜尋哪些職缺、比對哪些作品，回答「我最適合哪幾個職缺？」這種需要多個步驟的問題。

## 7. 資料格式（Pydantic）

| 模型 | 欄位 |
|---|---|
| `Requirement` | `skill`（統一英文名）、`original_text`（職缺原文）、`category`（language／framework／llm／ml／infra／data／soft／other）、`required`（必備或加分） |
| `JobRequirements` | `title`、`company`、`min_years`（可為空）、`requirements: list[Requirement]`、`responsibilities: list[str]` |
| `Evidence` | `id`、`type`（project／work／presentation／research／award）、`summary`、`url`（可為空） |
| `ProfileSkill` | `name`、`level`（basic／working／strong）、`aliases`、`evidence: list[str]` |
| `Profile` | `evidence`、`skills`；讀檔時檢查每個證據 id 都存在（`model_validator`） |
| `MatchResult` | `skill`、`required`、`status`（have／partial／missing）、`evidence: list[str]`、`level`、`reason` |
| `GapReport` | `job`、`matches: list[MatchResult]`、`top_gaps`、`created_at` |

## 8. 技術選擇

| 項目 | 選擇 | 理由 |
|---|---|---|
| 語言 | Python 3.10+ | 跟估算器一樣 |
| LLM | Claude API，預設 `claude-haiku-5-5` | 便宜；之後拿 Sonnet 比較品質和費用 |
| 結構化輸出 | Pydantic 定義格式，用 tool use 讓模型照格式回傳 | 延續主題 3 學的 Pydantic |
| 設定檔 | YAML（`pyyaml`） | 技能檔要自己手寫，YAML 比 JSON 好讀好改 |
| 測試 | pytest | 先測不需要呼叫 LLM 的部分（格式、證據 id 檢查、報告產生） |
| 安全 | 沿用費用上限與執行軌跡 | 每次分析都記錄 token、費用、時間 |

## 9. v1 不做

- 爬蟲（求職網站使用條款不允許）
- 網頁介面、API、資料庫、部署（12 月）
- 多份職缺排名、「我最適合哪幾個」（第 3 週 RAG）
- 履歷改寫、投遞追蹤（之後的版本）
- 多使用者、登入

## 10. 怎樣算成功

| 指標 | 目標 | 怎麼量 |
|---|---|---|
| 拆解準確率 | 抓對的技能 ≥ 80%、多抓的 ≤ 20% | 自己標註 10–15 份職缺的正確答案（第 4 週） |
| 證據正確性 | 100% 的證據 id 都存在 | 程式檢查 |
| 費用 | 每份職缺 < $0.01（Haiku） | 執行軌跡 |
| 有不有用 | 每份報告自己打 1–5 分，平均 ≥ 4 | 收集 30 份時順便評 |

## 11. 風險

| 風險 | 對策 |
|---|---|
| 同一個技能有很多寫法（中英、縮寫） | 統一英文名稱 + aliases；評估時特別看這類錯誤 |
| LLM 說我有我沒有的經歷 | 證據只能引用 profile 的 id，程式檢查 |
| 職缺文字很亂（福利、公司介紹混在一起） | 拆解時只抓技能與職責；先用 10 份測試 |
| 技能檔寫得太樂觀，報告就不準 | 規則：沒有作品就只能算「部分」 |

## 12. repo 結構（預定）

```
job-gap-agent/
├── docs/design.md          ← 這份文件
├── jobs/                   ← 職缺文字檔（.gitignore，不公開）
├── profile.yaml            ← 我的技能檔（.gitignore，不公開）
├── profile.example.yaml    ← 範例技能檔（公開，測試用它）
├── job_agent/
│   ├── schemas.py          ← Pydantic 格式（10/31 完成）
│   ├── llm.py              ← 呼叫 Claude、費用上限、執行軌跡（從估算器的 agent 改）
│   ├── extract.py          ← ① 拆解需求
│   ├── match.py            ← ③ 比對
│   ├── report.py           ← ④ 產生報告
│   └── __main__.py         ← python -m job_agent analyze jobs/xxx.txt
├── reports/                ← 產生的報告（.gitignore）
└── tests/
    └── test_schemas.py     ← 10/31：5 個測試
```

職缺原文來自求職網站，屬於別人的內容，所以 `jobs/` 不放上 GitHub；repo 裡改放 2–3 份自己改寫過的範例。

## 13. 時程（對應 11 月計畫）

| 日期 | 內容 |
|---|---|
| 10/29 | 這份設計文件 |
| 10/30 | 收集 10 份職缺（中英各半，三種職務都要有） |
| 10/31 | 寫 `profile.yaml`、開 repo、`schemas.py` |
| 11/1 | `llm.py`；① 拆解需求：一份職缺 → 通過 Pydantic 檢查的 `JobRequirements` |
| 11/2–11/8 | ② ③ ④、CLI、pytest、職缺補到 30 份 → **v1** |

## 之後的版本

- **v2（第 3 週）：** RAG + agent loop，回答「我最適合哪幾個職缺？」、「這個要求我該拿哪個作品來講？」
- **v3（第 4 週）：** 評估（拆解準確率、檢索準確率）
- **v4（12 月）：** FastAPI + 資料庫（投遞追蹤）+ 網頁介面 → Docker → CI → 部署 + 監控
- **選做：** 履歷改寫、模型路由（簡單的用 Haiku、難的用 Sonnet）、包成 MCP server
