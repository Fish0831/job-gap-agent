# 求職 Agent 設計文件（v0.5，2026-11-16）

> 專案名稱 **job-gap-agent**。網頁 demo：https://job-gap-agent.streamlit.app
> 範圍：v1（2026-11-06 完成）＋履歷 → profile 草稿（11/10）＋網頁 demo（11/11–11/12 上線）。之後的版本列在最後。
> v0.3 更新：市場缺口報告、structured outputs、拆解快取、模型比較結論、技能名稱統一。
> v0.4 更新：履歷 → profile 草稿（⓪）與轉換評估、11 月中網頁 demo 改成「上傳履歷 → 缺口報告」、職缺 API 移到更遠的計畫、專案往 agent 發展的方向（第 15 節是設計決策紀錄）。
> v0.5 更新：網頁 demo 上線（Streamlit：上傳履歷 → 草稿 → 和樣本職缺平行比對 → 市場缺口報告）、公開樣本職缺（只有技能清單）、送出前遮掉聯絡資料、三層費用保護、報告頁改成卡片＋圖表、畫面不顯示費用、上傳上限 10 MB、定位（主打 AI 工程師相關職缺）、下一步：擴充樣本＋職務類別。

## 1. 一句話

**貼上一份職缺，告訴我：哪些技能我已經有作品可以證明、哪些只有一部分、哪些完全沒有，以及下一步最值得補什麼。**

**跨職缺的市場缺口報告（主打功能）：** 一次分析多份目標職缺，統計「哪些必備技能最常出現、我卻沒有」，用資料決定下一步學什麼。主流工具（例如 LinkedIn Job Match）是一份一份看，這是和它們最大的差別。

**最終目標（11/10）：** 使用者在網頁上傳自己的履歷 → 按一下 → 看到缺口報告；接著在同一頁問 agent（「哪 3 份最適合我？」「學會 RAG 會怎樣？」「SQL 去哪裡學？」）。專案要從固定流程（workflow）發展成真正由模型決定下一步的 agent，見「之後的版本」。

**定位（11/11）：** 主打「分析 **AI 工程師相關職缺**」。和其他工具的差別：① 專門做 AI 職缺；② 有作品證明才算「有」；③ 跨職缺的市場統計。（台灣就業通預計 2026 年底推出「技能配適度分析」，算是類似的功能，所以重點放在 AI 職缺和作品證據。）

**目前狀態（11/12）：** 第一個里程碑「上傳履歷 → 缺口報告」已經上線（第 6 節的網頁版）。

## 2. 使用者與使用情境

使用者：我自己（AI Product Engineer／AI 應用工程師／ML Engineer 方向，研發替代役期間準備轉職）。之後會部署成網頁，讓別人也能用。

| 情境 | 我會怎麼用 |
|---|---|
| 看到一個有興趣的職缺 | 存成 `jobs/xx.txt`，一個指令，幾秒鐘內知道差距在哪 |
| 每個月檢討學習方向 | 看**市場缺口報告**：所有職缺裡哪個必備技能最常被標成「沒有」→ 下個月優先學 |
| 準備面試 | 看報告的「證據」欄，知道每個要求該拿哪個作品來講 |
| 別人使用（網頁版，11/12 上線） | 打開網址 → 上傳履歷 → 自動產生 profile 草稿 → 和樣本職缺比對 → 市場缺口報告；不用自己寫 `profile.yaml`、不用 API key |

## 3. 輸入

| 輸入 | 格式 | 說明 |
|---|---|---|
| 職缺 | 純文字檔（`jobs/*.txt`），中文或英文；`---` 上面是 `title:`、`company:`，下面是原文 | 從求職網站**手動複製貼上**，不寫爬蟲 |
| 我的技能檔 | `profile.yaml`（私人，不公開；repo 裡放 `profile.example.yaml` 範例） | 列出作品／經歷（每個有 id），以及每個技能的程度和對應的作品 |
| 同義詞表 | `skill_synonyms.yaml`（公開） | `統一名稱: [其他寫法]`，程式查表統一技能名稱 |
| 履歷（11/10） | `.pdf`、`.docx`、`.txt`、`.md`（私人，放 `resumes/`，.gitignore） | ⓪ 轉成 profile 草稿（格式和 `profile.yaml` 一樣），檢查後可以直接用 `--profile` |
| 樣本職缺（11/11） | `samples/jobs.json`（公開） | 網頁版比對用的 10 份職缺。由 `python -m job_agent.samples jobs` 從 `jobs/` 的拆解結果匯出，**只留職稱、公司、年資、技能名稱／類別／必備或加分**；職缺原文（`original_text`）和工作內容摘要清空 |

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

### profile 草稿（履歷轉換，`reports/profile_draft_<時間>.yaml`）

`python -m job_agent.resume resumes/my_resume.pdf [--compare profile.yaml]`

- 印出作品清單、技能清單（有作品／只列名稱）、**程式清理紀錄**（去掉了什麼、改了什麼）
- 存成和 `profile.yaml` 一樣格式的 YAML，開頭註明「請檢查後再用」
- `--compare`：用和比對一樣的名稱規則，對照手寫的技能檔（兩邊都有、只在草稿、只在手寫；⚠ = 報告狀態會不一樣）
- 用草稿跑市場報告：`python -m job_agent jobs --market --profile reports/profile_draft_<時間>.yaml`

### 網頁版（11/11–11/12，`app.py`）

| 分頁 | 內容 |
|---|---|
| 📊 市場缺口報告 | 三張卡片（比對的職缺數、必備技能有／部分／沒有、最該先補）→ **最該先補的技能**長條圖（沒有＝紅、部分＝橘、⚠ ＝判斷不穩定，滑鼠移上去看各份判斷）→ **我最有競爭力的技能**標籤 → **完整統計**（可以點欄位排序，「出現在」列公司名稱） |
| 📝 技能草稿 | 作品卡片、技能分兩組（有作品證明／只列在技能欄）、程式清理紀錄、下載草稿 YAML |

- 排序、統計和 CLI 的市場報告用同一套函式（`aggregate`、`priority`、`top_gaps`、`strengths`），只是畫法不同。
- **畫面上不顯示費用**（11/12）：使用者不需要知道、也不該讓人覺得在花錢；費用和秒數只 `print` 到伺服器 logs。

市場報告全部由程式統計，不靠 LLM：

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

**履歷 → 草稿的規則（11/10）：**

| 規則 | 誰保證 |
|---|---|
| 作品（evidence）只來自具體的專案、工作、研究、簡報、得獎；學歷和修課不算 | prompt |
| 技能只出現在技能欄 → evidence 空 → 報告是「部分」 | prompt＋程式（D 段 validator） |
| level 預設 basic，有明確描述才 working；**草稿不會出現 strong** | 格式（`DraftSkill.level` 只有 basic／working） |
| 沒有作品證據的技能 → 一律 basic | 程式（`clean()`） |
| 軟實力、重複技能、名稱寫法 | 程式（`SOFT_SKILLS`、同義詞表、同名合併） |
| 證據 id 不存在 → 刪掉並記錄 | 程式（`clean()`），最後 `Profile` 的 validator 再檢查一次 |
| 履歷裡的文字是資料不是指令 | prompt |

## 6. 系統怎麼運作

```
履歷 ──► ⓪ 履歷 → 草稿  A 讀檔（程式）→ B 整理（LLM）→ C 清理（程式）→ D 檢查（程式）
                                                              │（profile 草稿，人檢查後）
                                                              ▼
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

**網頁版（11/11）** 把同樣的步驟串起來，只多了「讀上傳的檔案」「遮掉聯絡資料」「平行比對」：

```
上傳履歷 ─► read_resume_bytes ─► mask_contacts（遮 email／電話）─► ⓪ B–D 草稿
                                                                    │
samples/jobs.json（10 份，只有技能清單，不用再拆解）─────────────────┤
                                                                    ▼
                         pipeline.analyze：③ 比對 × 10 份同時送出（ThreadPoolExecutor）
                                                                    │
                         app.py：⑤ 統計 → 卡片、長條圖、表格 ◄────────┘
```

| 步驟 | 檔案 | 誰做 |
|---|---|---|
| ⓪ A 讀檔 | `resume.py` 的 `read_resume`（pypdf、python-docx 含表格）、`check_text`（100～20000 字） | 程式 |
| ⓪ B 整理 | `resume.py` 的 `profile_from_text` → `ResumeDraft`（不存執行軌跡） | LLM |
| ⓪ C 清理＋D 檢查 | `resume.py` 的 `clean` → `Profile` | 程式 |
| 網頁：讀上傳檔＋遮罩 | `resume.py` 的 `read_resume_bytes`、`mask_contacts` | 程式 |
| ① 拆解 | `extract.py` | LLM（規則寫在 SYSTEM） |
| ② 技能檔 | `schemas.py` 的 `Profile` | 程式 |
| ③ A 整理 | `normalize.py`（`drop_soft`、`unify`、`key`）＋ `match.py` 的 `dedupe` | 程式 |
| ③ B 名稱對上 | `match.py` 的 `exact_match` | 程式 |
| ③ C 語意對應 | `match.py` 的 `semantic_map` | LLM |
| ③ D 決定狀態 | `match.py` 的 `decide` | 程式 |
| ④ 缺口報告 | `report.py` | 程式 |
| ⑤ 市場缺口報告 | `market.py` | 程式 |
| 網頁：平行比對 | `pipeline.py` 的 `analyze`（一份失敗不影響其他份；結果照樣本順序排回） | 程式 |
| 網頁：樣本職缺 | `samples.py` 的 `strip_job`、`export_samples`、`load_samples` | 程式 |
| 網頁：畫面 | `app.py`（只負責畫面，邏輯全部呼叫 `job_agent`） | — |
| 呼叫 Claude | `llm.py`（唯一直接跟 Claude 打交道的檔案） | — |
| CLI | `__main__.py`、`compare.py`、`resume.py` | — |

**誠實說明：目前（含 ⓪ 和網頁版）是固定流程（workflow），還不是會自己決定下一步的 agent。** 每一步做什麼、下一步是什麼，都由程式決定。這是刻意的：先把每個步驟做穩、能測試，之後這些步驟會變成 agent 的工具（見「之後的版本」）。**目標是讓專案真正變成 agent，而不是停在 workflow（11/10 決定）。**

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
| `DraftSkill`／`ResumeDraft` | ⓪ 給 LLM 的回答格式（`resume.py`）：和 `ProfileSkill`／`Profile` 很像，但 level 只有 basic／working、沒有 validator（寫錯的 id 由 `clean()` 刪掉，不會讓整次呼叫失敗） |

## 8. 技術選擇

| 項目 | 選擇 | 理由 |
|---|---|---|
| 語言 | Python 3.10+ | 跟估算器一樣 |
| LLM | Claude API，拆解、比對、履歷預設都用 `claude-haiku-5-5`；可用 `EXTRACT_MODEL`／`MATCH_MODEL`／`RESUME_MODEL` 換 | 11/6 模型比較的結論（第 15 節） |
| 結構化輸出 | Pydantic → `transform_schema()` → `output_config.format` → `model_validate_json()` | 所有模型都支援；11/5 從強制 tool_choice 改過來 |
| 設定檔 | YAML（`pyyaml`） | 技能檔和同義詞表要自己手寫，YAML 好讀好改 |
| 讀履歷 | `pypdf`（PDF）、`python-docx`（Word，段落＋表格） | 純 Python、好安裝；掃描檔（圖片）讀不到字 → 明確報錯 |
| 測試 | pytest，假的 Claude（`tests/conftest.py` 的 `FakeClient`）；網頁用 Streamlit 的 `AppTest` | 不連網、不花錢；61 個測試（假履歷文字，不放真履歷） |
| 網頁 | Streamlit（`streamlit>=1.67`）＋ Altair 長條圖 | 只用 Python 就能做出上傳、進度條、分頁、圖表；適合 demo。12 月 FastAPI 之後可以換前端 |
| 部署 | Streamlit Community Cloud（連 GitHub `main`，push 就自動重新部署） | 免費、設定簡單；key 放平台的 Secrets，不進 repo |
| 安全 | 每次呼叫費用上限 $0.20、執行軌跡（`trajectories/`）、沒有價格的模型不能用 | 換模型時費用上限也不會失效 |
| 個資 | `call_structured(save_trajectory=False)`、環境變數 `SAVE_TRAJECTORIES=0` 全部關掉；網頁版送出前 `mask_contacts` | 履歷內容不寫進軌跡；網頁版一次全關、聯絡資料不送出 |

## 9. v1 不做

- 爬蟲（求職網站通常禁止自動化收集）
- API、資料庫、Docker（12 月 FastAPI＋Docker＋CI；網頁 demo 已在 11/12 上線）
- 「我最適合哪幾個職缺」的排名（之後由職缺問答 agent 回答）
- 履歷改寫、投遞追蹤
- 串職缺 API（更遠的計畫，見第 14 節）
- 多使用者、登入

## 10. 怎樣算成功

| 指標 | 目標 | 目前 |
|---|---|---|
| 證據正確性 | 100% 的證據 id 都存在 | 程式保證（LLM 不輸出證據） |
| 費用 | 每份職缺 < $0.01（Haiku） | 第一次約 $0.0025／份；拆解有快取後約 $0.001／份 |
| 穩定性 | 模型之間的差異只出現在模糊項目 | 11/6：3 份職缺共 9 項差異，都是邊界技能 |
| 拆解準確率 | 抓對的技能 ≥ 80%、多抓的 ≤ 20% | 尚未標註（回顧週之後） |
| 有不有用 | 每份報告自己打 1–5 分，平均 ≥ 4 | 尚未評分 |
| 履歷轉換：不編造 | 草稿裡「有」的證據 100% 在履歷找得到 | 11/10：14／14，編造 0 個 |
| 履歷轉換：不灌水 | 草稿判「有」、手寫判「部分」的技能 = 0 | 11/10：0 個（不一致的 7 個全是草稿較保守） |
| 履歷轉換：抓得到 | 手寫 profile 的技能，草稿抓到 ≥ 80% | 11/10：23／30（77%），狀態一致 16／23（70%） |
| 履歷轉換：報告一致 | 草稿和手寫 profile 的市場報告前 10 名大致相同 | 11/10：8／10 相同，前 6 名分數一樣 |
| 網頁版費用 | 每人 < $0.02 | 11/11 網頁實測：**US$0.0120**（履歷＋比對 10 份）；比對失敗 0 份 |
| 網頁版等待時間 | < 60 秒 | 平行比對（同時 5 份）；部署後實測待補（logs 有 `analysis done: cost …, …s`） |

**11/10 履歷轉換評估怎麼做**：拿自己的履歷轉草稿，用三個角度看：① 和履歷原文比（有沒有編造）；② 和手寫的 `profile.yaml` 比（標準答案）；③ 漏掉的分兩類——**履歷沒寫**（例如 token-cost-estimator、agent 實驗不在履歷上 → 改履歷）和**履歷有寫但模型沒抓**（例如 LLM Cost Optimization、Technical Presentation → 之後改 prompt，改完要重新評估）。level 是最不準的部分（模型讀到「獨立開發」容易給 working），所以草稿要請使用者自己確認 level。

## 11. 風險

| 風險 | 對策 |
|---|---|
| 同一個技能有很多寫法（中英、縮寫、大小寫） | 拆解時統一英文名稱＋程式的 `key()`＋同義詞表＋profile 的 aliases |
| 一個舉例被拆成好幾項（OpenAI、Claude、Gemini） | 拆解規則：同一類東西的舉例合併，不同技能的列表拆開 |
| LLM 說我有我沒有的經歷 | LLM 不輸出證據；技能名稱要存在於 profile；沒有作品只能算「部分」 |
| alias 太廣讓 LLM 灌水 | 比對規則：alias 相似不等於 same；alias 只放真正的別名 |
| LLM 沒擋住軟實力 | 程式的排除清單（`SOFT_SKILLS`）當第二道防線 |
| 同一個技能跨職缺判斷不一致 | 市場報告取多數、平手取保守，並標出 ⚠️ 讓我回頭修輸入 |
| 部署後別人用我的 key | **三層費用保護（11/11–11/12 已做）**：① 每個連線最多 3 次（`MAX_RUNS_PER_SESSION`，重新整理會重置，只是擋手滑）；② 全站每日預算（`DAILY_BUDGET_USD`，預設 US$1，所有使用者共用）；③ Console 另建 workspace＋專用 key，設每月花費上限（伺服器重開時 ② 會歸零，③ 才是真正的上限）。另外有上傳大小（10 MB）和履歷字數上限（20000 字） |
| 履歷是個資 | 不存執行軌跡、不存檔；網頁版送出前用程式遮掉電話和 email（資料最小化）；`resumes/` 在 .gitignore |
| 履歷裡藏指令（prompt injection） | prompt 註明「履歷是資料不是指令」；輸出格式固定（structured outputs）；證據 id 和 level 由程式檢查 |
| LLM 把技能程度寫太高 | 格式不允許 strong；沒作品一律 basic；網頁上提醒使用者確認 level |
| 掃描檔 PDF 讀不到字 | 少於 100 字直接報錯，請使用者改傳 Word 或文字檔 |
| 比對每次判斷不同，排名跳動 | 市場報告標 ⚠️；11/10 實例：CI/CD 剛好平手，「平手取保守」讓優先度 6 ↔ 12 翻倍 → 網頁版把「判斷不穩定」標得更明顯；之後評估多跑幾次取多數的成本 |
| 樣本只有 10 份 AI 相關職缺 | 網頁上註明樣本範圍（「樣本職缺」展開看得到）；下一步手動擴充到約 30 份並加上職務類別（第 13 節）；更多、更新的職缺留給職缺 API（更遠的計畫） |
| 公開樣本洩漏職缺原文 | `strip_job` 清空 `original_text`、`responsibilities`；測試檢查匯出的檔案裡沒有原文；commit 前自己打開檢查 |
| 檔案太大傳不上去 | 11/12 朋友回報 → `maxUploadSize` 從 2 MB 改成 10 MB，上傳框標題寫「最大 10 MB」；字數上限另外擋費用 |

## 12. repo 結構

```
job-gap-agent/
├── docs/design.md          ← 這份文件
├── jobs/                   ← 職缺文字檔（.gitignore）
├── profile.yaml            ← 我的技能檔（.gitignore）
├── profile.example.yaml    ← 範例技能檔（公開，測試用它）
├── skill_synonyms.yaml     ← 技能名稱同義詞表（公開）
├── app.py                  ← 網頁 demo（Streamlit，只負責畫面）
├── samples/jobs.json       ← 公開的樣本職缺（只有技能清單）
├── .streamlit/config.toml  ← 上傳上限、不傳使用統計（secrets.toml 在 .gitignore）
├── job_agent/
│   ├── schemas.py          ← Pydantic 格式
│   ├── llm.py              ← 呼叫 Claude、structured outputs、價格、費用上限、執行軌跡
│   ├── extract.py          ← ① 拆解＋快取
│   ├── normalize.py        ← 名稱統一、軟實力排除
│   ├── match.py            ← ③ 比對 A–D
│   ├── report.py           ← ④ 缺口報告
│   ├── market.py           ← ⑤ 市場缺口報告
│   ├── compare.py          ← 模型比較工具
│   ├── resume.py           ← ⓪ 履歷 → profile 草稿（11/10）、讀上傳檔、遮掉聯絡資料
│   ├── pipeline.py         ← 網頁版：平行比對多份職缺
│   ├── samples.py          ← 網頁版：匯出／讀取樣本職缺
│   └── __main__.py         ← python -m job_agent jobs [--market]
├── resumes/                ← 履歷（.gitignore）
├── cache/、reports/、trajectories/  ← 執行產生（.gitignore）
└── tests/                  ← 61 個測試
```

## 13. 時程

| 日期 | 內容 |
|---|---|
| 10/29–10/31 | 設計文件、收集 10 份職缺、`profile.yaml`、開 repo、`schemas.py` |
| 11/1 | `llm.py`、① 拆解 |
| 11/2–11/3 | ③ 比對、④ 報告 |
| 11/5 | structured outputs、換模型、拆解快取、一個指令的 CLI、模型比較工具 |
| 11/6 | 名稱統一、舉例合併、alias 規則、重新比較模型、⑤ 市場缺口報告 → **v1** |
| 11/7–11/9 | 回顧週；11/9 決定：網頁 demo 改成「上傳履歷 → 缺口報告」（履歷功能從 12 月提前）、職缺 API 移到更遠的計畫 |
| 11/10 | ⓪ 履歷 → profile 草稿（`resume.py`）、軌跡開關、轉換評估 |
| 11/11 | 網頁 demo 本機版：`app.py`、平行比對、公開樣本、遮掉聯絡資料、三層費用保護 |
| 11/12 | 部署到 Streamlit Community Cloud、報告頁美化（卡片、長條圖、可排序表格）、上傳上限 10 MB |
| 11/16– | 擴充樣本到約 30 份＋職務類別（網頁上選目標職務，市場報告只統計那一類） |
| 11/17–11/30 | 貼單份職缺出報告、修 bug；11/30 月底回顧 |
| 12 月 | FastAPI＋Docker＋CI |
| 1 月 | agent 功能：職缺問答 agent → 學習資源推薦 |

## 14. 資料來源

| 來源 | 方式 | 狀態 |
|---|---|---|
| 104、LinkedIn、Cake 等求職網站 | **手動複製貼上**。這些網站通常禁止自動化收集 | v1 使用 |
| 台灣就業通（勞動部） | 政府開放資料 API | 更遠的計畫 |
| 用 Greenhouse／Lever 等招募系統的公司 | 官方公開的 Job Board API | 更遠的計畫 |

設計原則：只用合法、公開的來源；新增來源時，每種來源各寫一個轉換器，輸出成同樣的 `jobs/*.txt` 格式，後面的步驟完全不用改。

**職缺 API 什麼時候做（11/9）**：想要「更多、更新的職缺」或「依使用者的目標職務挑選職缺」時才做。要做的事：每個 API 一個轉換器、新職缺拆解＋快取、淘汰過期職缺。很適合交給 agent：依使用者的目標職務決定要搜哪些職缺。

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
| 11/6 | 快取指紋加入資料格式（`JobRequirements` 的 JSON Schema） | 改了 schemas.py 的欄位，舊快取也要失效 |
| 11/9 | **11 月中網頁 demo = 上傳履歷 → 缺口報告**（履歷功能從 12 月提前），用 10 份樣本職缺，不串職缺 API | 別人用網頁時不可能自己寫 `profile.yaml`；沒有履歷功能，網頁版就只有我自己能用 |
| 11/9 | 職缺 API 移到更遠的計畫 | 10 份樣本足夠 demo；API 要處理轉換器、快取、過期職缺，等需要更多職缺時再做 |
| 11/10 | **LLM 回傳 `ResumeDraft`，不直接回傳 `Profile`**；`level` 只開放 basic／working | 格式可以比最終格式更嚴；寫錯一個證據 id 時只刪掉那個，不會讓整次呼叫失敗、白花錢 |
| 11/10 | 履歷步驟不存執行軌跡；`SAVE_TRAJECTORIES=0` 可以全部關掉 | 履歷是個資；網頁版不留任何使用者資料 |
| 11/10 | 用草稿跑市場報告重用現有的 `--profile`，`resume.py` 不另外做 `--market` | 草稿和手寫 profile 格式一樣，後面的流程完全不用改 |
| 11/10 | 用手寫 `profile.yaml` 當標準答案評估履歷轉換（`--compare`） | 錯誤全在保守那一邊（0 個灌水）→ 草稿可以用在網頁版；level 最不準 → 請使用者確認 |
| 11/10 | **順序：12 月先做 FastAPI＋Docker＋CI，1 月再加 agent 功能；目標是讓專案真正變成 agent，不停在 workflow** | 先把骨幹工程化、部署穩定，再把現有步驟包成 agent 的工具 |
| 11/11 | **`app.py` 只負責畫面**，流程放 `pipeline.py` | 邏輯可以用假的 Claude 測試；12 月換成 FastAPI 時直接重用 |
| 11/11 | **平行比對**（ThreadPoolExecutor，同時 5 份），回傳前照樣本順序排回 | 比對之間互不相關；逐一跑要 30–60 秒。排回順序讓結果固定；軌跡檔名加微秒避免同時寫同一個檔 |
| 11/11 | **公開樣本只有技能清單**（`samples/jobs.json`），不部署 `cache/` | 快取裡有職缺原文片段，不該公開；比對只需要技能名稱 |
| 11/11 | **送出前用程式遮掉 email／電話**（`mask_contacts`） | 資料最小化：比對用不到聯絡資料。測試同時檢查「該遮的有遮」和「日期、數字、連結沒有被誤遮」 |
| 11/11 | 費用保護：每連線次數＋全站每日預算＋Console 上限 | Streamlit 不一定拿得到使用者 IP，沒辦法依 IP 限流 → 用三層組合 |
| 11/11 | 定位：主打 AI 工程師相關職缺 | 和通用的職缺比對工具做出區隔；樣本本來就是 AI 職缺 |
| 11/12 | 部署到 Streamlit Community Cloud；Console 另建 workspace＋專用 key | 和自己開發用的 key 分開，花費上限只管 demo |
| 11/12 | **畫面不顯示費用**，只記到 logs | 使用者不需要；README 保留「約 US$0.01／次」給想了解成本控制的人 |
| 11/12 | 報告頁用卡片＋長條圖＋可排序表格，不再顯示 Markdown | 一眼看到重點；⚠ 判斷不穩定在圖上就看得到 |
| 11/12 | 為什麼 12 月還要 FastAPI | 不是為了開放給外部呼叫，而是讓網頁、1 月的 agent、之後的前端呼叫同一套邏輯；換前端不動後端；之後比較好做限流和監控。漂亮的前端（例如 React）跑在瀏覽器、不能放 key，所以一定要有後端 |

## 之後的版本

- **11 月中：網頁 demo** ✅（11/12 上線：https://job-gap-agent.streamlit.app）
- **11 月下旬：**
  - **擴充樣本＋職務類別**：手動收集到約 30 份 AI 職缺；每份職缺標一個職務類別（例如 AI 應用／Agent 工程師、ML 工程師、AI 產品工程師）；網頁上先選目標職務，市場報告只統計那一類
  - **貼單份職缺**：網頁上貼一份職缺 → 單份缺口報告（`extract_from_text` 已經有；要加字數上限、每連線次數上限，職缺文字視為不可信的輸入；每份約多 $0.003）
- **12 月：** FastAPI＋Docker＋CI
- **1 月：** 加入 agent 功能（見下方）

### 可以加入的 Agent 功能

目前是 workflow（程式決定下一步）。**目標是讓專案真正變成 agent（11/10 決定）**：使用者問的是開放式問題，由模型自己決定要呼叫哪些工具、呼叫幾次、什麼時候停。架構是「workflow 當骨幹（每個步驟變成工具），agent 決定怎麼用這些工具」。

| # | 功能 | 使用者可以問什麼 | 為什麼算 agent | 給模型的工具 |
|---|---|---|---|---|
| 1 | **職缺問答 agent**（先做） | 「哪 3 份最適合我？」「如果我學會 RAG，哪幾份的缺口會變少？」「光寶和 NVIDIA 差在哪？」 | 問題是開放的，要查哪些職缺、算什麼，由模型自己決定 | 用現有程式包成工具：`list_jobs`、`get_report`、`get_market_stats`、`what_if`（假設加上某技能，程式重算） |
| 2 | **學習資源推薦** | 報告產生後：「SQL 要怎麼補？」 | 搜什麼、搜到的夠不夠、要不要換關鍵字再搜，由模型決定 | 網路搜尋、讀網頁 |

**1. 職缺問答 agent 為什麼先做**：工具全部是現有函式（`match`、`build_report`、`aggregate`），不用上網、便宜；可以學到工具設計、agent loop、停止條件；網頁上看得到 agent 一步一步呼叫工具，demo 效果好。`what_if` 不用呼叫 LLM：把比對結果裡的技能改成「有」，再用程式重算統計，便宜、結果固定、能測試。agent loop 用一般的 tool use（讓模型自己選工具，不強制 tool_choice）。

**2. 學習資源推薦的設計重點**：針對「最該先補的技能」前幾名；依程度挑選（沒有 → 入門、部分 → 進階或實作專案）；只推薦搜得到來源的資源（不能憑記憶編書名），附連結和查詢日期；標示中／英、免費／付費。搜尋可以用 Claude API 的網頁搜尋工具（另外計費，做的時候查官方最新價格），搜尋次數和費用設上限。

**其他候選（討論過，還沒決定）**：
- **profile 訪談 agent**：11/10 評估發現，很多技能「有作品但履歷沒寫」（草稿判部分、其實應該是有）。agent 可以針對「只列名稱」的技能追問使用者（「你有用 FastAPI 做過專案嗎？」），再把回答補成證據——由模型決定要問哪些、問到什麼程度。
- 拆解自我檢查、學習計畫 agent、職缺 API 搜尋 agent（依目標職務決定搜哪些職缺）、包成 MCP server。

**加 agent 時的原則**：
- agent 決定「要呼叫什麼」，證據、狀態、排序還是由工具裡的程式保證
- 設上限：最多幾步、每次對話的費用上限；沿用 `llm.py` 的費用上限和執行軌跡（`trajectories/`）

- **之後：** 評估（拆解準確率標註、履歷轉換改 prompt 後重新評估）；資料來源轉換器（職缺 API）；模型路由（簡單的用 Haiku、難的用 Sonnet）