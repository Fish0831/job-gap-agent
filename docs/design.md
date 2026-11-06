# 求職 Agent 設計文件（v0.3，2026-11-06）

> 專案名稱 **job-gap-agent**。
> 範圍：第一版（v1，2026-11-06 完成）。之後的版本列在最後。
> v0.3 更新：市場缺口報告、structured outputs、拆解快取、模型比較結論、技能名稱統一（第 15 節是設計決策紀錄）。

## 1. 一句話

**貼上一份職缺，告訴我：哪些技能我已經有作品可以證明、哪些只有一部分、哪些完全沒有，以及下一步最值得補什麼。**

**跨職缺的市場缺口報告（主打功能）：** 一次分析多份目標職缺，統計「哪些必備技能最常出現、我卻沒有」，用資料決定下一步學什麼。主流工具（例如 LinkedIn Job Match）是一份一份看，這是和它們最大的差別。

## 2. 使用者與使用情境

使用者：我自己（AI Product Engineer／AI 應用工程師／ML Engineer 方向，研發替代役期間準備轉職）。之後會部署成網頁，讓別人也能用。

| 情境 | 我會怎麼用 |
|---|---|
| 看到一個有興趣的職缺 | 存成 `jobs/xx.txt`，一個指令，幾秒鐘內知道差距在哪 |
| 每個月檢討學習方向 | 看**市場缺口報告**：所有職缺裡哪個必備技能最常被標成「沒有」→ 下個月優先學 |
| 準備面試 | 看報告的「證據」欄，知道每個要求該拿哪個作品來講 |

## 3. 輸入

| 輸入 | 格式 | 說明 |
|---|---|---|
| 職缺 | 純文字檔（`jobs/*.txt`），中文或英文；`---` 上面是 `title:`、`company:`，下面是原文 | 從求職網站**手動複製貼上**，不寫爬蟲 |
| 我的技能檔 | `profile.yaml`（私人，不公開；repo 裡放 `profile.example.yaml` 範例） | 列出作品／經歷（每個有 id），以及每個技能的程度和對應的作品 |
| 同義詞表 | `skill_synonyms.yaml`（公開） | `統一名稱: [其他寫法]`，程式查表統一技能名稱 |

`profile.yaml` 的樣子：

```yaml
evidence:
  - id: token-cost-estimator
    type: project
    summary: LLM 成本估算工具：CLI、FastAPI、pytest，GitHub 公開
    url: https://github.com/Fish0831/token-cost-estimator
  - id: master-thesis
    type: research
    summary: 碩論：DRL（DQN、PPO、SAC）用於平台動態定價

skills:
  - name: FastAPI
    level: basic
    evidence: [token-cost-estimator]
  - name: Reinforcement Learning
    level: strong
    aliases: [強化學習, DRL]
    evidence: [master-thesis]
  - name: Docker
    level: basic
    evidence: []          # 會一點但沒有作品 → 算「部分」
```

**alias 只放「同一個技能的別名」**，不放相關但不同的東西（11/6 教訓：`LLM Model Selection` 放了「地端模型」，Haiku 就把 Open-source LLM 判成「有」）。

| level | 意思 |
|---|---|
| `basic` | 做過，但需要查資料或有人帶 |
| `working` | 能自己獨立做出來 |
| `strong` | 能解釋原理、自己除錯、教別人 |

## 4. 輸出

### 缺口報告（每份職缺，`reports/<職缺>_<時間>.md`）

- 摘要：必備／加分各有幾項「有／部分／沒有」
- 建議優先補強（前 5 項）：必備沒有 > 必備部分 > 加分沒有 > 加分部分，每項附一句下一步建議
- 完整比對表：類別、技能、狀態、熟練度、證據、判斷理由
- 最後一行：費用、名稱直接對上幾項、LLM 判斷幾項、比對模型

### 市場缺口報告（多份職缺彙總，`reports/market_<時間>.md`）

`python -m job_agent jobs --market`

| 段落 | 內容 |
|---|---|
| 最該先補的技能 | 前 10 名：必備幾份、加分幾份、我的狀態、優先度、出現在哪幾份 |
| 我最有競爭力的技能 | 我「有」、而且最多份職缺列為必備的技能＋證據 |
| 完整統計 | 出現在 2 份以上的技能 |
| 判斷不一致的技能 | 同一個技能在不同職缺被判成不同狀態 → 提醒檢查 alias 或同義詞表 |
| 樣本範圍 | 幾份、哪些職缺和公司、產生時間、費用 |

全部由程式統計，不靠 LLM：

- **需求分數** = 必備 2 分、加分 1 分，逐份加總
- **優先度** = 需求分數 × 缺口權重（沒有 2、部分 1、有 0）
- **跨職缺的「我的狀態」**：取各份判斷中最常見的；平手取比較差的。理由：狀態本來應該是「我」的屬性，跨職缺不一致是 LLM 判斷的變動；取多數可以抵銷單次的偶然，平手時保守，寧可多列一個缺口，也不要誤以為自己會。不一致的會用 ⚠️ 標出來。

## 5. 判斷規則

| 狀態 | 條件 |
|---|---|
| ✅ 有 | 名稱對上（程式）或 LLM 判斷 same，**而且至少有一個作品當證據** |
| 🟡 部分 | 同一個技能但沒有證據；或 LLM 判斷 related（相關技能，例：要 LangGraph，我有手寫 agent loop） |
| ❌ 沒有 | LLM 判斷 none、沒有回答，或對應到 profile 裡不存在的技能 |

**證據不由 LLM 輸出**：LLM 只回答「對應到哪個 profile 技能、是 same／related／none」，證據和 level 一律由程式從 `profile.yaml` 複製。LLM 說的技能名稱不在 profile 裡 → 不採信，算「沒有」。

## 6. 系統怎麼運作（v1）

```
職缺文字 ──► ① 拆解（LLM，structured outputs，有快取）──► JobRequirements
                                                              │
profile.yaml ──► ② 讀取技能檔（Pydantic 檢查證據 id）───────────┤
                                                              ▼
            ③ 比對  A 整理（程式）：去掉軟實力 → 同義詞統一名稱 → 去重
                    B 名稱對上（程式）：name／alias，寫法差異也算 → 不呼叫 LLM
                    C 語意對應（LLM）：剩下的一次問完，same／related／none
                    D 決定狀態（程式）：檢查技能存在、複製證據和 level
                                                              │
                    ④ 缺口報告（程式）◄───────────────────────┤
                    ⑤ 市場缺口報告（程式，多份彙總）◄──────────┘
```

| 步驟 | 檔案 | 誰做 |
|---|---|---|
| ① 拆解 | `extract.py` | LLM（規則寫在 SYSTEM） |
| ② 技能檔 | `schemas.py` 的 `Profile` | 程式 |
| ③ A 整理 | `normalize.py`（`drop_soft`、`unify`、`key`）＋ `match.py` 的 `dedupe` | 程式 |
| ③ B 名稱對上 | `match.py` 的 `exact_match` | 程式 |
| ③ C 語意對應 | `match.py` 的 `semantic_map` | LLM |
| ③ D 決定狀態 | `match.py` 的 `decide` | 程式 |
| ④ 缺口報告 | `report.py` | 程式 |
| ⑤ 市場缺口報告 | `market.py` | 程式 |
| 呼叫 Claude | `llm.py`（唯一直接跟 Claude 打交道的檔案） | — |
| CLI | `__main__.py`、`compare.py` | — |

**誠實說明：v1 是固定流程（workflow），還不是會自己決定下一步的 agent。** 這是刻意的：先把每個步驟做穩、能測試。之後再讓 agent loop 自己決定要比對哪些職缺、拿哪個作品來講。

## 7. 資料格式（Pydantic）

| 模型 | 欄位 |
|---|---|
| `Requirement` | `skill`（統一英文名）、`original_text`（職缺原文）、`category`（language／framework／llm／ml／infra／data／soft／other）、`required` |
| `JobRequirements` | `title`、`company`、`min_years`、`requirements`、`responsibilities` |
| `Evidence` | `id`、`type`（project／work／presentation／research／award）、`summary`、`url` |
| `ProfileSkill` | `name`、`level`、`aliases`、`evidence` |
| `Profile` | `evidence`、`skills`；讀檔時檢查每個證據 id 都存在（`model_validator`） |
| `MatchResult` | `skill`、`required`、`status`、`evidence`、`level`、`reason`、`how`（name／same／related／none）、`matched_to` |
| `GapReport` | `job`、`matches`、`top_gaps`、`created_at` |
| `SkillMapping`／`MappingResult` | ③ C 段給 LLM 的回答格式（`match.py`） |
| `Run`／`SkillStat` | ⑤ 市場報告的彙總資料（`market.py`，dataclass） |

## 8. 技術選擇

| 項目 | 選擇 | 理由 |
|---|---|---|
| 語言 | Python 3.10+ | 跟估算器一樣 |
| LLM | Claude API，拆解和比對預設都用 `claude-haiku-5-5`；可用 `EXTRACT_MODEL`／`MATCH_MODEL` 換 | 11/6 模型比較的結論（第 15 節） |
| 結構化輸出 | Pydantic → `transform_schema()` → `output_config.format` → `model_validate_json()` | 所有模型都支援；11/5 從強制 tool_choice 改過來 |
| 設定檔 | YAML（`pyyaml`） | 技能檔和同義詞表要自己手寫，YAML 好讀好改 |
| 測試 | pytest，假的 Claude（`tests/conftest.py` 的 `FakeClient`） | 不連網、不花錢；39 個測試 |
| 安全 | 每次呼叫費用上限 $0.20、執行軌跡（`trajectories/`）、沒有價格的模型不能用 | 換模型時費用上限也不會失效 |

## 9. v1 不做

- 爬蟲（求職網站通常禁止自動化收集）
- 網頁介面、API、資料庫、部署（11 月中網頁 demo；12 月 FastAPI＋Docker＋CI）
- 「我最適合哪幾個職缺」的排名
- 履歷改寫、投遞追蹤
- 多使用者、登入

## 10. 怎樣算成功

| 指標 | 目標 | 目前 |
|---|---|---|
| 證據正確性 | 100% 的證據 id 都存在 | 程式保證（LLM 不輸出證據） |
| 費用 | 每份職缺 < $0.01（Haiku） | 第一次約 $0.0025／份；拆解有快取後約 $0.001／份 |
| 穩定性 | 模型之間的差異只出現在模糊項目 | 11/6：3 份職缺共 9 項差異，都是邊界技能 |
| 拆解準確率 | 抓對的技能 ≥ 80%、多抓的 ≤ 20% | 尚未標註（回顧週之後） |
| 有不有用 | 每份報告自己打 1–5 分，平均 ≥ 4 | 尚未評分 |

## 11. 風險

| 風險 | 對策 |
|---|---|
| 同一個技能有很多寫法（中英、縮寫、大小寫） | 拆解時統一英文名稱＋程式的 `key()`＋同義詞表＋profile 的 aliases |
| 一個舉例被拆成好幾項（OpenAI、Claude、Gemini） | 拆解規則：同一類東西的舉例合併，不同技能的列表拆開 |
| LLM 說我有我沒有的經歷 | LLM 不輸出證據；技能名稱要存在於 profile；沒有作品只能算「部分」 |
| alias 太廣讓 LLM 灌水 | 比對規則：alias 相似不等於 same；alias 只放真正的別名 |
| LLM 沒擋住軟實力 | 程式的排除清單（`SOFT_SKILLS`）當第二道防線 |
| 同一個技能跨職缺判斷不一致 | 市場報告取多數、平手取保守，並標出 ⚠️ 讓我回頭修輸入 |
| 部署後別人用我的 key | key 只在伺服器環境變數、Console 花費上限、每 IP 限次數、字數上限、不存資料 |

## 12. repo 結構

```
job-gap-agent/
├── docs/design.md          ← 這份文件
├── jobs/                   ← 職缺文字檔（.gitignore）
├── profile.yaml            ← 我的技能檔（.gitignore）
├── profile.example.yaml    ← 範例技能檔（公開，測試用它）
├── skill_synonyms.yaml     ← 技能名稱同義詞表（公開）
├── job_agent/
│   ├── schemas.py          ← Pydantic 格式
│   ├── llm.py              ← 呼叫 Claude、structured outputs、價格、費用上限、執行軌跡
│   ├── extract.py          ← ① 拆解＋快取
│   ├── normalize.py        ← 名稱統一、軟實力排除
│   ├── match.py            ← ③ 比對 A–D
│   ├── report.py           ← ④ 缺口報告
│   ├── market.py           ← ⑤ 市場缺口報告
│   ├── compare.py          ← 模型比較工具
│   └── __main__.py         ← python -m job_agent jobs [--market]
├── cache/、reports/、trajectories/  ← 執行產生（.gitignore）
└── tests/                  ← 39 個測試
```

## 13. 時程

| 日期 | 內容 |
|---|---|
| 10/29–10/31 | 設計文件、收集 10 份職缺、`profile.yaml`、開 repo、`schemas.py` |
| 11/1 | `llm.py`、① 拆解 |
| 11/2–11/3 | ③ 比對、④ 報告 |
| 11/5 | structured outputs、換模型、拆解快取、一個指令的 CLI、模型比較工具 |
| 11/6 | 名稱統一、舉例合併、alias 規則、重新比較模型、⑤ 市場缺口報告 → **v1** |
| 11/7–11/9 | 回顧週 |
| 11 月中 | 網頁 demo |
| 12 月 | FastAPI＋Docker＋CI；貼履歷 → 產生 profile 草稿 |

## 14. 資料來源

| 來源 | 方式 | 狀態 |
|---|---|---|
| 104、LinkedIn、Cake 等求職網站 | **手動複製貼上**。這些網站通常禁止自動化收集 | v1 使用 |
| 台灣就業通（勞動部） | 政府開放資料 API | 選做 |
| 用 Greenhouse／Lever 等招募系統的公司 | 官方公開的 Job Board API | 選做 |

設計原則：只用合法、公開的來源；新增來源時，每種來源各寫一個轉換器，輸出成同樣的 `jobs/*.txt` 格式，後面的步驟完全不用改。

## 15. 設計決策紀錄

| 日期 | 決策 | 為什麼 |
|---|---|---|
| 11/5 | 強制 tool_choice → **structured outputs**（`output_config.format`） | Sonnet／Opus 5.5 不接受強制使用工具；structured outputs 所有模型都支援，格式由 API 保證，再用 Pydantic 檢查一次 |
| 11/5 | **拆解快取，指紋 = 模型 + 拆解規則 + 職缺內容** | 同一份職缺不重複花錢、結果固定；三者任何一個改變就自動重拆，不會用到過期的結果 |
| 11/5 | **只有 `llm.py` 直接跟 Claude 打交道** | 換 API 寫法（tool_choice → structured outputs）只改一個檔案 |
| 11/5 | 模型比較（光寶 02）：模型間差異 ≈ 同模型重跑差異 | → 結論是先修輸入，而不是換更貴的模型 |
| 11/6 | **技能名稱統一用程式＋同義詞表，不靠 LLM** | 查表不花 token、結果固定、能測試；LLM 只做語意判斷 |
| 11/6 | 拆解規則：同一類東西的舉例合併、不同技能的列表拆開 | Gemini API 被拆成 3 項，造成判斷不穩 |
| 11/6 | 比對規則：alias 相似不等於 same | Haiku 因為 alias 太廣灌水判「有」 |
| 11/6 | 修輸入後重新比較（02、06、09）→ **比對用 Haiku** | 原本的差異消失；剩下 9 項都是模糊技能；Sonnet 費用約 13 倍且偏寬鬆；Haiku 和 Opus 在 09 一致；工具是找缺口的，偏保守比較好 |
| 11/6 | 軟實力用程式排除清單擋第二次 | prompt 寫了要跳過，Haiku 仍拆出 Analytical Problem Solving |
| 11/6 | **市場報告全部程式計算**；跨職缺狀態取多數、平手取保守 | 排序要可解釋、可重現；不一致的標出來，回頭修輸入 |

## 之後的版本

- **11 月中：** 網頁 demo（重用 `extract_from_text`、`match`、`to_markdown`）
- **12 月：** FastAPI＋Docker＋CI；貼履歷 → 產生 profile 草稿
- **之後：** agent loop 回答「我最適合哪幾個職缺？」、「這個要求我該拿哪個作品來講？」；評估（拆解準確率）；資料來源轉換器；模型路由（簡單的用 Haiku、難的用 Sonnet）；包成 MCP server