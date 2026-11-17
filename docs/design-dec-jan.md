# job-gap-agent：12 月–1 月設計（FastAPI＋Docker＋CI → 職缺問答 agent）

> 版本 v1.1（2026-11-17；v1.1：Cloud Run 區域改成美國、預算用整數存、token 加版本號）。建立在 main = `1b161d0`、`docs/design.md` v0.5、70 個測試之上。
> 範圍：2026-12-01 ～ 2027-01-31。12/20–12/25 不排工作。
> 這份只有設計，沒有實作程式碼；Pydantic 模型和 Dockerfile 是草稿，實作時會再調整。
> 文中的函式、常數、檔名都對照過目前的程式碼；標「新增」的是這兩個月要寫的。

## 0. 摘要

**一句話：** 12 月把邏輯從 Streamlit 搬到一個無狀態的 FastAPI 服務（Docker、Cloud Run、CI），1 月在同一個服務上加「職缺問答 agent」。

| # | 決定 | 選擇 | 主要理由 |
|---|---|---|---|
| ADR-1 | Streamlit 和 FastAPI 的關係 | Streamlit 變成只畫畫面的 client，透過 `Backend` 介面呼叫 API；四步遷移，每步有開關可回退 | 邏輯只有一份；agent 只需要做在 API |
| ADR-2 | FastAPI 部署在哪 | Google Cloud Run（美國區域，D2 實測後定） | 冷啟動短、支援長請求、免費額度夠；替代方案是 Render 免費版 |
| ADR-3 | 限流與費用保護 | API 層做「先預留、後結算」的每日預算＋固定視窗限流，計數器放 Upstash Redis | 平台會休眠，記憶體裡的計數器會歸零；現在的預算檢查有競爭條件 |
| ADR-4 | 分析結果存在哪 | 不存在伺服器：簽章後交給前端保管（`analysis_token`） | 符合「不存履歷」；平台休眠不掉資料 |
| ADR-5 | agent loop | 自己寫的 loop，4 個唯讀工具，最多 6 次模型呼叫，每對話有費用上限 | 能力小才安全；每個停止條件都能用假的 Claude 測試 |

**目標架構（1 月底）：**

```
瀏覽器 ──► Streamlit Community Cloud（app.py：只畫畫面，沒有 Anthropic key）
                │  HTTPS ＋ X-Frontend-Key ＋ X-Client-Id
                ▼
          FastAPI（Docker，Cloud Run，無狀態）
          api/           路由、錯誤格式、限流與預算（Depends）
            ├─ job_agent/service.py   make_profile／run_analysis（固定流程）
            ├─ job_agent/agent.py     agent loop ──► job_agent/tools.py（唯讀純函式）
            ├─ job_agent/llm.py ────► Claude API（唯一出口，原則不變）
            └─ job_agent/limits.py ─► Upstash Redis（只有計數器：預算、次數）
```

**不變的原則：** LLM 只做語意判斷；沒有作品證據不算「有」；只有 `llm.py` 呼叫 Claude；重要規則 prompt＋程式兩道防線；不存履歷、不存軌跡；邏輯和輸入輸出分開。

## 1. 現況：哪些東西要搬、哪些不用動

| 現在在哪 | 內容 | 12 月–1 月怎麼處理 |
|---|---|---|
| `resume.read_resume_bytes`、`profile_from_text`、`pipeline.analyze`、`market.aggregate`／`top_gaps`／`strengths`／`priority` | 核心邏輯，已經和畫面分開 | **不動**，由新的 `service.py` 串起來 |
| `app.py` 的 `run_analysis` | 串流程＋畫進度 | 流程搬到 `service.py`，`app.py` 只留畫面 |
| `app.py` 的 `_budget`／`budget_left`／`add_spent`／`est_cost` | 每日預算，存在 `st.cache_resource` | 搬到 `limits.py`，改成先預留後結算（ADR-3） |
| `st.session_state["result"]` | `Profile`＋`list[Run]`，只活在 Streamlit 連線裡 | API 回傳報告；1 月起另外回傳 `analysis_token`（ADR-4） |
| `market.Run`／`SkillStat` | dataclass，`evidence` 是 `set`、`status` 是 property | 不改；新增對外的 Pydantic 輸出模型（第 3 節） |
| `llm.call_structured` | 單輪、沒有 tools | 不改；1 月新增 `llm.call_tools`（ADR-5） |
| `requirements.txt` | 沒鎖版本，pytest、streamlit 混在一起 | 拆成三份（第 5 節） |
| `samples.SAMPLES_FILE = Path("samples/jobs.json")` | 相對於執行目錄 | **不要改成以檔案為基準**：五個 `AppTest` 測試靠 `monkeypatch.chdir` 換樣本檔。Docker 用 `WORKDIR /app` 解決 |

順手要修的小地方：

- `llm.py` 每次呼叫都 `client or Anthropic()` → 改成共用一個 client，並設定 timeout 和重試次數。
- `llm.MAX_COST_PER_CALL` 是花完才檢查 → 保留當警報；agent 另外做呼叫前的上限（ADR-5）。
- `resume.py` 的錯誤都是 `ValueError` → 加三個子類別（仍繼承 `ValueError`，現有的 `except ValueError` 不受影響），API 才能對應到不同錯誤碼。
- `budget_left()` 用 `date.today()`，雲端上是 UTC → 明確用 Asia/Taipei 的日期。
- `design.md` 第 8、12 節還寫「61 個測試」，實際是 70 → v0.6 一起改。

---

## 2. 架構決策（ADR）

### ADR-1：Streamlit 和 FastAPI 的關係

**狀態：** 提議　**日期：** 2026-11-17

**背景：** 邏輯已經在 `job_agent/`，但流程串接、預算、結果暫存還在 `app.py`。1 月的 agent 需要同一套邏輯；之後可能換前端。Streamlit Community Cloud 是目前唯一的線上入口，遷移期間不能壞。

**選項：**

| | A. Streamlit 當 client，呼叫 API | B. 兩者並存，各自 import `job_agent` | C. 直接換成瀏覽器前端 |
|---|---|---|---|
| 邏輯有幾份 | 一份（API） | 兩個入口，限流和預算要做兩次 | 一份 |
| key 放哪 | 只在 API | Streamlit 和 API 都有 | 只在 API |
| 工作量 | 中 | 小 | 大（要學前端） |
| 缺點 | 多一跳、兩層冷啟動 | agent 要做兩次，或 Streamlit 沒有 agent | 8 週做不完 |

**決定：** 選 A，但分四步走，中間用 B 當過渡。`app.py` 只透過一個 `Backend` 介面拿資料，介面有兩個實作：

- `LocalBackend`：在同一個 process 直接呼叫 `job_agent.service`（現在的行為）。
- `ApiBackend`：用 httpx 呼叫 FastAPI。

用環境變數 `API_BASE_URL` 切換：有設就用 `ApiBackend`，沒設就用 `LocalBackend`。

| 步驟 | 時間 | 做什麼 | 線上的 Streamlit | 回退方式 |
|---|---|---|---|---|
| 0 | D1 | 抽出 `service.py`；`app.py` 改呼叫 `LocalBackend` | 行為不變 | `git revert` |
| 1 | D1–D2 | FastAPI 包住 `service.py`，本機和 Cloud Run 都能跑 | 不變（還沒接 API） | 不用回退 |
| 2 | D3 | `ApiBackend` 完成，在本機或第二個 Streamlit app 驗證 | 不變（開關關著） | 不設 `API_BASE_URL` |
| 3 | D4 | 線上設定 `API_BASE_URL`；穩定後把 Anthropic key 從 Streamlit Secrets 移除 | 走 API | 刪掉 `API_BASE_URL`（key 移除前） |

**取捨：** 多一跳會增加延遲，而且 Streamlit Cloud 和 API 各有冷啟動。換到的是邏輯、限流、key 都只有一處，agent 只做一次。Streamlit 在這兩個月留著，因為 `st.chat_message`、`st.status` 可以直接做出對話和步驟顯示。

**後果：**
- 進度條會變粗：`analyze` 的 `on_progress` 過不了 HTTP。12 月先分兩個請求（履歷 → 草稿、草稿 → 報告），畫面顯示兩個階段。
- 步驟 3 之後線上沒有 `LocalBackend` 可退（Streamlit 沒有 key 了），API 掛掉就顯示錯誤訊息。
- 12/20–12/25 線上維持步驟 2（開關關著）。

### ADR-2：FastAPI 部署在哪裡

**狀態：** 提議（需要你決定，見「待決定」1）　**日期：** 2026-11-17

**背景：** 需求是能跑 Docker image、一次請求可以跑 20–40 秒（之後 agent 要串流）、流量很低、費用接近零、靠近它最常呼叫的服務（Streamlit Cloud、Claude API、Redis）。記憶體需求小（FastAPI＋pypdf＋python-docx）。

**選項（2026-11-17 查詢，數字會變，決定前再看一次官方頁面）：**

| | Google Cloud Run | Render（Free） | Fly.io |
|---|---|---|---|
| 免費額度 | 每月 180,000 vCPU-秒、360,000 GiB-秒、200 萬次請求（request-based billing） | 免費 web service：512 MB、0.1 CPU，每月 750 小時 | 新帳號沒有免費額度，只有試用（2 小時機器時間或 7 天） |
| 閒置行為 | 縮到 0 台，下次請求冷啟動（通常數秒，D2 實測） | 15 分鐘沒流量就休眠，喚醒約 1 分鐘 | 可設定自動停止；不停的最小機器每月約 US$2 |
| 請求時間上限 | 預設 5 分鐘，最長 60 分鐘 | HTTP 回應最長 100 分鐘 | 沒有查到硬上限 |
| 區域 | 可選；美國多個區域、asia-east1（台灣） | 可選；有美國區域和新加坡 | 可選；有美國區域和亞洲區域 |
| 帳單風險 | 沒有硬上限，只有預算警示 | 免費版不會被收費 | 用多少付多少 |
| 需要信用卡 | 要（billing account） | 不用 | 要 |
| 學習價值 | 最多：容器平台、IAM、Secret Manager | 最簡單 | 介於中間 |

**決定：** 推薦 Cloud Run。設定：1 vCPU、512 MiB、最少 0 台、**最多 1 台**、concurrency 8、timeout 120 秒、區域先用 us-west1。

**區域為什麼選美國（v1.1 修正）：** 呼叫 API 的不是瀏覽器，而是 Streamlit Community Cloud 的伺服器，它只在美國。API 放台灣的話，Streamlit → API、API → Claude、API → Redis 每一次來回都可能跨太平洋；一次分析有 30 幾次 LLM 呼叫，agent 一輪最多 6 次而且是一個接一個。原則是把服務放在它呼叫最頻繁的對象旁邊。就算之後換成瀏覽器直接呼叫 API，瀏覽器到 API 只有一次來回，API 到 Claude 有很多次，結論不變。Claude API 預設是全球路由，實際在哪裡推論不保證，所以 D2 把同一個 image 部署到 us-west1 和 asia-east1 各量一次（`/healthz`、一次 Haiku 呼叫、一次完整分析），用數字決定。Upstash 的區域選和 Cloud Run 相同或最近的。

**理由：** 冷啟動是最大的差別。Streamlit Cloud 12 小時沒人用就會睡，再疊上 Render 的 1 分鐘喚醒，第一次打開 demo 的體驗最差；Render 的 0.1 CPU 也會拖慢 5 個 worker 的平行比對。用量上，一次分析抓 30 秒 × 1 vCPU，免費額度約可跑 6,000 次／月，遠超過需求。

**後果與風險：**
- Cloud Run 沒有花費硬上限。最多 1 台的理論最壞情況（整個月滿載）約 US$60。對策：預算警示設 US$5；沒帶 `X-Frontend-Key` 的請求在最前面就拒絕；每月看一次帳單。
- 計數器不能放記憶體（縮到 0 台就歸零）→ ADR-3。
- 如果你不想綁信用卡或不接受帳單風險，改用 Render 免費版，其他設計都不用變（同一個 Docker image），代價是約 1 分鐘的喚醒時間。

### ADR-3：限流與費用保護怎麼搬到 API 層

**狀態：** 提議　**日期：** 2026-11-17

**背景：** 現在的三層是：每連線 3 次（`MAX_RUNS_PER_SESSION`）、全站每日預算（`DAILY_BUDGET_USD`）、Console 月上限。有三個問題：

1. 預算存在記憶體，重開就歸零；Cloud Run 縮到 0 台一天會發生很多次。
2. `budget_left()` 先檢查、`add_spent()` 跑完才記帳，同時進來的請求會一起通過檢查。
3. Streamlit 當 client 時，API 看到的來源 IP 是 Streamlit Cloud 的，不是使用者的。`st.context.ip_address` 官方文件註明可以被偽造、不該用在安全用途。

**決定：**

**(1) 誰可以呼叫：** 遷移期間 API 不對外開放。所有會花錢的端點都要帶 `X-Frontend-Key`（Streamlit 和 API 共用的密鑰，各自放在平台的 secrets）。沒帶或不對 → 401，不做任何運算。

**(2) 依誰限流：** Streamlit 每個連線產生一個隨機 `X-Client-Id`。API 只在 `X-Frontend-Key` 正確時才相信這個標頭。IP 目前只當輔助訊號（拿得到就多一道，拿不到就略過）。等瀏覽器直接呼叫 API 時，再改成用平台加上的 `X-Forwarded-For` 當主要依據。

**(3) 每日預算改成「先預留、後結算」：**

```
請求進來 → reserve(est_cost)：原子地把今天的金額加上預估值
              ├─ 超過 DAILY_BUDGET_USD → 減回去，回 503 budget_exhausted
              └─ 沒超過 → 執行
執行完   → settle(實際費用 − 預估值)：補差額（失敗就全額退回）
```

預估值沿用 `est_cost(n_jobs)`（30 份職缺時是 US$0.065）。日期一律用 Asia/Taipei。

**(4) 計數器存哪：**

| 選項 | 重開會歸零嗎 | 成本 | 評語 |
|---|---|---|---|
| 記憶體（現狀） | 會 | 0 | 只留給測試和本機用 |
| SQLite＋持久化磁碟 | 不會 | Cloud Run 和 Render 免費版都沒有持久化磁碟 | 不適用 |
| **Upstash Redis（推薦）** | 不會 | 免費版 256 MB、每月 50 萬個指令、不用信用卡 | 金額用整數存（百萬分之一美元），`INCRBY`、`EXPIRE` 都是原子操作，沒有浮點誤差；一次分析約用 6–8 個指令 |
| Firestore | 不會 | 有免費額度 | 綁定 GCP；換平台要重寫 |

程式上定義一個 `Store` 介面，兩個實作：`MemoryStore`（測試、本機）和 `RedisStore`。限流用固定視窗（`INCR`＋`EXPIRE`），自己寫約 20 行，不引入額外套件。

**(5) 建議的上限（都做成環境變數）：**

| 範圍 | key | 上限 |
|---|---|---|
| `POST /v1/profile` | client id | 3 次／天（等於現在的 `MAX_RUNS_PER_SESSION`） |
| `POST /v1/analysis` | client id | 6 次／天（換職務類別會重跑） |
| `POST /v1/chat` | 對話（`sid`） | 12 輪、US$0.05 |
| `POST /v1/chat` | client id | 40 輪／天 |
| 全站 | 台北日期 | `DAILY_BUDGET_USD`（預設 1.0） |
| Console | workspace | 每月花費上限（不變，最後一道） |

**後果：**
- Redis 連不上時，會花錢的端點回 503（fail-closed）。寧可 demo 暫時不能用，也不要在沒有預算保護時繼續花錢。
- 只存計數器，不存任何履歷內容。IP 若有使用，先用密鑰做雜湊再當 key，TTL 48 小時。
- `X-Client-Id` 重新整理就會換，所以每連線上限仍然只擋手滑；真正的上限還是每日預算和 Console。這一點和現在一樣，文件要誠實寫出來。

### ADR-4：分析結果（agent 的狀態）存在哪裡

**狀態：** 提議　**日期：** 2026-11-17

**背景：** agent 的 `get_report`、`what_if` 每一輪都需要這位使用者的 `Profile` 和比對結果（`list[MatchResult]`）。現在它們只在 `st.session_state`。原則是不存履歷、不存軌跡。

**選項：**

| | A. 伺服器記憶體 session＋TTL | B. 存進 Redis | C. 簽章後交給前端保管（推薦） |
|---|---|---|---|
| 伺服器存使用者資料 | 有（記憶體） | 有（第三方服務） | 沒有 |
| 平台休眠／重開 | 資料消失，要重新上傳 | 不受影響 | 不受影響 |
| 請求大小 | 小 | 小 | 每次多帶 10–40 KB（估計，J1 用真資料量） |
| 複雜度 | 最低 | 中 | 中（一個簽章函式庫） |

**決定：** 選 C。`POST /v1/analysis` 除了報告，另外回傳一個 `analysis_token`：把 `AnalysisState`（`sid`、`role`、`profile`、每份職缺的 `MatchResult`、樣本版本）序列化、壓縮、用伺服器密鑰簽章，並帶時間戳（2 小時過期）。前端不解析它，只在呼叫 `/v1/chat` 時原樣帶回。建議用 `itsdangerous` 的 `URLSafeTimedSerializer`，不自己寫密碼學。

**為什麼要簽章（而不是直接收 JSON）：**
- `sid` 不能偽造 → 每對話的費用和輪數上限才擋得住（計數器用 `sid` 當 key，放在 ADR-3 的 Store）。
- 要先做過一次分析（有限流）才拿得到 token → `/v1/chat` 不能被單獨濫用。
- 伺服器不用重新檢查結構是否一致（證據 id、狀態規則）。

**簽章不等於可信內容：** token 裡的文字（作品摘要、判斷理由）來自履歷和 LLM，進到 agent 時仍然當成不可信資料（ADR-5）。簽章也不是加密；內容是使用者自己的資料，只回到同一個人手上。

**後果：**
- 重新部署且 `samples/jobs.json` 有變 → 舊 token 的職缺編號可能對不上 → token 帶樣本檔的雜湊，不一致回 409，請使用者重新分析。
- 對話紀錄也由前端保管，每次帶最近 8 則純文字（不含工具呼叫）。使用者可以偽造自己的對話紀錄，但只影響自己的對話，而且工具唯讀、有費用上限，所以接受這個風險（待決定 7）。
- 12 月不需要 token；J1 才實作。
- 這是最難回頭的決定：`/v1/chat`、工具、之後的確認流程都建立在它上面。所以 token 內容帶版本號 `v`，而且其他程式只透過 `state.load(token)`／`state.dump(state)` 兩個函式讀寫。之後要改成伺服器端保存（token 變成 session id）時，只換這兩個函式。

### ADR-5：agent loop 的設計

**狀態：** 提議　**日期：** 2026-11-17

**背景：** 目標是由模型決定要查什麼、查幾次、何時停。已決定：先做職缺問答；`what_if` 不用 LLM；一般 tool use，不強制 tool_choice。工具的輸入輸出草稿在第 4 節。

**決定：**

**(1) 工具（全部唯讀、純函式、只看得到這次分析的資料）：**

| 工具 | 回答什麼 | 底層用到的現有函式 |
|---|---|---|
| `list_jobs` | 分析過哪些職缺、各自的缺口數、適合度排名 | `samples.load_samples`、每份的 `MatchResult` |
| `get_report` | 某一份職缺的完整比對 | `report.build_report`、`report.gaps` |
| `get_market_stats` | 跨職缺的缺口和強項 | `market.aggregate`、`top_gaps`、`strengths`、`priority` |
| `what_if` | 假設學會某些技能，各職缺和排名怎麼變 | `normalize.key`、`canonical`，改狀態後重跑 `aggregate` |

「哪幾份最適合我」的排序由程式算（`fit_score`），模型只負責決定要不要查、查完怎麼解釋。

**(2) loop 與上限：**

| 項目 | 值 | 說明 |
|---|---|---|
| 每個問題最多幾次模型呼叫 | 6 | 最多 5 輪工具＋1 次收尾 |
| 每個問題最多幾次工具呼叫 | 10 | 模型可以一次要求多個工具，程式依序執行 |
| 每次呼叫 `max_tokens` | 1024 | 答案本來就該短 |
| 每個工具結果 | 最多 4,000 字 | 超過就截斷並標 `truncated: true` |
| 送給模型的總內容 | 最多約 12,000 字 | 超過就丟掉最舊的對話紀錄 |
| 每對話 | 12 輪、US$0.05（Haiku） | 計數器用 `sid`，放 Store |
| 問題長度 | 500 字 | |

每次呼叫模型前檢查：剩餘的對話預算 ≥ 這次呼叫的最壞情況費用，不夠就不呼叫。用 `llm.PRICES` 計算：

| 模型 | 一般一輪（4 次呼叫） | 最壞一輪（6 次呼叫、內容滿載） |
|---|---|---|
| `claude-haiku-5-5` | 約 US$0.002 | 約 US$0.010 |
| `claude-sonnet-5-5` | 約 US$0.036 | 約 US$0.205 |

先用 Haiku（`model_for("agent")`，讀 `AGENT_MODEL`）。J4 用評估集比過再決定要不要換，和 11/6 選比對模型的做法一樣。

**(3) 什麼時候停：**

| 情況 | 處理 |
|---|---|
| `stop_reason == "end_turn"` | 正常結束，做引用檢查後回答 |
| 模型呼叫次數或工具呼叫次數用完 | 再呼叫一次，這次設 `tool_choice: none`，要求用手上的資訊回答並說明不完整 |
| 對話預算不夠 | 不再呼叫模型，回固定訊息 |
| 同一個工具＋同樣參數重複第 2 次 | 不執行，回 `is_error` 提示「已經查過」；第 3 次直接收尾 |
| `stop_reason` 是 `max_tokens`／`refusal` | 回固定訊息；不重試 |
| 呼叫 Claude 失敗或逾時 | 這一輪結束，回 502／504；已花的費用照樣記帳 |

`tool_choice: none` 只用在收尾，是「禁止再用工具」，不是強制用某個工具，和原本的決定不衝突（Sonnet 5.5 等模型也支援 `auto` 和 `none`）。

**(4) 工具出錯怎麼辦：**

| 錯誤 | 回給模型什麼 |
|---|---|
| 參數驗證失敗（Pydantic） | `tool_result` 設 `is_error: true`，內容是哪個欄位、允許什麼值 |
| 查不到（例如 `job_id` 不存在） | `is_error: true`，附上可用的 `job_id` 清單 |
| `what_if` 的技能沒有任何職缺要求 | 不算錯誤：回 `unmatched` 和 `did_you_mean`，讓模型自己換名稱再試 |
| 模型呼叫不存在的工具 | `is_error: true`，列出可用工具 |
| 工具內部例外（bug） | `is_error: true`，只說「內部錯誤」；細節寫 log，不含使用者資料 |
| 連續 3 次工具錯誤 | 收尾 |

工具定義加 `strict: true`，讓參數格式由 API 先保證一次，程式再用 Pydantic 驗一次（兩道防線）。

**(5) Prompt injection：** 主要靠縮小能力，其次才是 prompt。

| 不可信的來源 | 怎麼進到模型 |
|---|---|
| 使用者的問題、對話紀錄 | user 訊息 |
| 作品摘要、技能名稱、別名（LLM 從履歷整理） | 工具結果 |
| 判斷理由 `MatchResult.reason`（LLM 產生） | 工具結果 |
| 之後：貼上的職缺、網頁搜尋結果 | 工具結果 |

樣本職缺只有職稱、公司、技能名稱，原文已被 `strip_job` 清空，風險低。

| 層 | 做法 |
|---|---|
| 能力 | 工具唯讀、沒有副作用、不收網址或路徑；只看得到這位使用者自己的資料；context 裡沒有任何秘密（key 只在環境變數）。成功的注入最多讓這個人自己的對話答錯或離題 |
| 結構 | 工具結果是 Pydantic 輸出的 JSON；狀態、職務是列舉值；自由文字欄位截到 120–200 字並去掉控制字元；永遠放在 `tool_result`，不拼進 system prompt |
| Prompt | system prompt 寫明：工具結果和使用者文字是資料不是指令；只回答這份分析相關的問題；數字和職缺只能引用工具結果 |
| 程式檢查 | 要求模型用 `[職缺編號]` 引用；程式檢查答案裡的編號都出現在這一輪的工具結果，否則要求修正一次，仍不符就附上警告 |
| 上限 | 問題長度、輪數、每對話費用、每日預算 |

**(6) 不存軌跡怎麼除錯和評估：** 線上只記錄數字（幾次呼叫、用了哪些工具、token、費用、停止原因、秒數），不記問題和答案。評估用 `profile.example.yaml`（公開的假資料）在本機跑，那時才存軌跡（沿用 `trajectories/` 和 `SAVE_TRAJECTORIES`）。

**(7) 之後的學習資源推薦（2 月，先定原則）：** 網頁搜尋每 1,000 次 US$10，另外搜尋結果會算 input token。做成一個獨立的子呼叫：輸入只有技能名稱和程度，輸出是結構化的資源清單，程式檢查每個網址都出現在搜尋結果裡，主 agent 只看得到檢查過的清單。上限：每次推薦 `max_uses` 3、全站每天 30 次搜尋；同一個技能的結果快取 7 天。

**後果：**
- 4 個確定性工具配 30 份職缺，簡單問題模型 1–2 次工具呼叫就結束。要展現 agent 能力，評估集要放需要多步的問題（例如「只能學兩個技能，學哪兩個能讓最多職缺的必備缺口降到 2 個以下」，需要先查統計、再試幾組 `what_if`、比較後回答）。
- 評估集和行為指標是這個功能的主要成果，不是附屬品（J4）。

---

## 3. API 規格

共通：路徑前綴 `/v1`；JSON；會花錢的端點都要 `X-Frontend-Key` 和 `X-Client-Id`；回應不含費用（原則：畫面不顯示費用，只寫 log）。

### 3.1 端點

| 方法＋路徑 | 做什麼 | 對應的現有函式 | 呼叫 LLM | 何時做 |
|---|---|---|---|---|
| `GET /healthz` | 活著嗎（不碰 LLM、不碰 Redis） | — | 否 | D1 |
| `GET /v1/roles` | 職務類別、說明、各有幾份樣本 | `roles.ROLES`、`ROLE_HINTS`、`samples.load_roles`、`MIN_ROLE_SAMPLES`（現在在 `app.py`，搬到 `service.py`） | 否 | D1 |
| `GET /v1/jobs?role=` | 樣本職缺清單 | `samples.load_samples(role=)`、`load_roles` | 否 | D1 |
| `POST /v1/profile` | 上傳履歷（multipart）→ 技能草稿 | `resume.read_resume_bytes` → `resume.profile_from_text`（內含 `check_text`、`mask_contacts`、`clean`） | 是 | D1 |
| `POST /v1/analysis` | 草稿＋職務 → 市場缺口報告 | `samples.load_samples` → `pipeline.analyze` → `market.aggregate`／`top_gaps`／`strengths`／`priority` | 是 | D1 |
| `POST /v1/chat` | 問 agent | `agent.run_turn`（新增）→ `tools.py`（新增） | 是 | J3 |
| `POST /v1/gap-report` | 貼一份職缺 → 單份缺口報告 | `extract.extract_from_text` → `match.match` → `report.build_report` | 是 | 之後（待決定 5） |

`/v1/profile` 和 `/v1/analysis` 分開的理由：正好對應 `profile_from_text` 和 `analyze` 兩個函式；每個請求較短；前端可以顯示兩個階段；之後讓使用者改 level 或接「profile 訪談 agent」時不用改 API。

### 3.2 模型草稿（新增 `job_agent/dto.py`）

`Profile`、`MatchResult`、`Status`、`Level` 直接用 `schemas.py` 現有的。

```python
RoleId = Literal["ai-app", "ml", "ai-product"]        # roles.ROLES 的 key；新增類別時兩邊要一起改（加測試檢查）

class RoleOut(BaseModel):
    role: RoleId
    label: str               # roles.ROLES[role]
    hint: str                # roles.ROLE_HINTS[role]
    n_samples: int
    selectable: bool         # n_samples >= MIN_ROLE_SAMPLES

class JobSummary(BaseModel):
    job_id: str
    role: RoleId | None      # 舊格式的樣本沒有 role
    title: str
    company: str | None
    min_years: int | None
    n_required: int
    n_preferred: int

class ProfileResponse(BaseModel):       # POST /v1/profile
    profile: Profile
    notes: list[str]         # clean() 的清理紀錄，含「遮掉聯絡資料 N 處」

class ProfileIn(Profile):               # 從前端收進來的 profile：多加大小限制
    # 作品 ≤ 60、技能 ≤ 150、每個字串 ≤ 300 字、aliases ≤ 10 個（用 validator 檢查）
    ...

class AnalysisRequest(BaseModel):       # POST /v1/analysis
    profile: ProfileIn
    role: RoleId | None = None          # None = 全部職務

class SkillStatOut(BaseModel):          # market.SkillStat 的對外版本
    skill: str
    required_in: list[str]
    preferred_in: list[str]
    statuses: dict[str, Status]         # 每份職缺的判斷
    status: Status                      # SkillStat.status（取多數、平手取保守）
    consistent: bool
    demand: int
    priority: int                       # market.priority(stat)
    evidence: list[str]                 # set → 排序後的 list
    level: Level | None

class MarketReport(BaseModel):
    role: RoleId | None
    role_label: str
    n_jobs: int
    required_counts: dict[Status, int]  # 必備技能的 有／部分／沒有（app.show_report 的 counts）
    top_gaps: list[SkillStatOut]        # market.top_gaps(stats)
    strengths: list[SkillStatOut]       # market.strengths(stats)
    stats: list[SkillStatOut]           # market.aggregate(runs) 全部
    jobs: list[JobSummary]

class AnalysisResponse(BaseModel):
    report: MarketReport
    failed: list[str]                   # pipeline.analyze 的第三個回傳值
    analysis_token: str | None = None   # J1 起才有
    expires_at: datetime | None = None

class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=2000)

class ChatRequest(BaseModel):           # POST /v1/chat
    analysis_token: str
    question: str = Field(min_length=1, max_length=500)
    history: list[ChatMessage] = Field(default_factory=list, max_length=8)

class ToolStep(BaseModel):
    tool: str
    input: dict
    ok: bool
    summary: str                        # 程式產生的一行摘要，給畫面顯示

class ChatResponse(BaseModel):
    answer: str
    steps: list[ToolStep]
    cited_job_ids: list[str]
    stopped_by: Literal["end_turn", "max_rounds", "budget", "refusal", "error"]
    turns_left: int

class ErrorBody(BaseModel):
    code: str                           # 給程式判斷，例如 "resume_too_short"
    message: str                        # 給使用者看的中文訊息

class ErrorResponse(BaseModel):
    error: ErrorBody
```

### 3.3 錯誤碼

所有錯誤都是 `{"error": {"code": ..., "message": ...}}`。

| HTTP | code | 什麼時候 | 來源 |
|---|---|---|---|
| 400 | `invalid_token` | `analysis_token` 簽章不對或格式壞掉 | `state.py`（新增） |
| 401 | `invalid_client` | 沒帶或帶錯 `X-Frontend-Key` | `api/deps.py` |
| 409 | `stale_analysis` | token 的樣本版本和目前的 `samples/jobs.json` 不同 | `state.py` |
| 410 | `token_expired` | token 超過 2 小時 | `state.py` |
| 413 | `file_too_large` | 上傳超過 10 MB | API（邊讀邊數，不相信 Content-Length） |
| 415 | `unsupported_file` | 副檔名不支援、文字檔不是 UTF-8 | `read_resume_bytes` |
| 422 | `resume_too_short` | 少於 `MIN_CHARS`（多半是掃描檔） | `check_text` |
| 422 | `resume_too_long` | 超過 `MAX_CHARS` | `check_text` |
| 422 | `validation_error` | 欄位格式、長度、`role` 不對；證據 id 不存在 | Pydantic（含 `Profile.check_evidence_ids`） |
| 429 | `rate_limited` | 每連線或每對話的次數用完；附 `Retry-After` | `limits.py` |
| 502 | `llm_error` | `LLMError`、`anthropic.APIError`；或 `analyze` 全部失敗 | `llm.py`、`pipeline.py` |
| 503 | `budget_exhausted` | 全站每日預算用完；`Retry-After` 到台北時間隔天 0 點 | `limits.py` |
| 503 | `limiter_unavailable` | Redis 連不上 | `limits.py` |
| 504 | `llm_timeout` | 呼叫 Claude 逾時 | `llm.py` |

`analyze` 只有部分職缺失敗時仍回 200，`failed` 列出編號（和現在網頁的行為一樣）。

### 3.4 新增與修改的檔案

| 檔案 | 狀態 | 內容 |
|---|---|---|
| `job_agent/service.py` | 新增 | `list_roles`、`list_jobs`、`make_profile`、`run_analysis`、`build_market_report`：`app.py` 和 API 共用 |
| `job_agent/dto.py` | 新增 | 3.2 的模型 |
| `job_agent/limits.py` | 新增 | `Store` 介面、`MemoryStore`、`RedisStore`、`reserve`／`settle`、固定視窗限流；不依賴 FastAPI |
| `job_agent/state.py` | 新增（J1） | `AnalysisState`、簽章與驗證 |
| `job_agent/tools.py` | 新增（J1） | 4 個工具與註冊表 |
| `job_agent/agent.py` | 新增（J2） | `run_turn` |
| `job_agent/llm.py` | 修改 | 共用 client；J1 新增 `call_tools` |
| `job_agent/resume.py` | 修改 | 三個 `ValueError` 子類別 |
| `api/main.py`、`api/deps.py`、`api/errors.py` | 新增 | 路由、依賴（密鑰、限流、預算）、例外 → 錯誤格式 |
| `ui/backend.py` | 新增 | `Backend` 介面、`LocalBackend`、`ApiBackend` |
| `app.py` | 修改 | 只透過 `Backend` 拿資料；預算相關函式移走 |

---

## 4. Agent 工具規格（J1 實作，新增 `job_agent/tools.py`）

每個工具是 `fn(state: AnalysisState, args: XxxIn) -> XxxOut` 的純函式。工具的 JSON Schema 由輸入模型產生，和程式驗證用同一個模型。

```python
class AnalysisState(BaseModel):          # analysis_token 的內容
    v: int = 1                           # 格式版本；欄位有變就加 1，舊版 token 回 409 請重新分析
    sid: str                             # 隨機產生，預算和輪數的 key
    role: RoleId | None
    samples_version: str                 # samples/jobs.json 的雜湊前 8 碼
    profile: Profile
    runs: list[RunOut]                   # RunOut = job_id ＋ list[MatchResult]；job 本身由伺服器用 job_id 從樣本查

# ---- list_jobs ----
class ListJobsIn(BaseModel):
    sort_by: Literal["fit", "missing_required", "job_id"] = "fit"
    limit: int = Field(10, ge=1, le=30)

class JobFit(BaseModel):
    job_id: str
    title: str
    company: str | None
    role: RoleId | None
    min_years: int | None
    required: dict[Status, int]          # 必備技能的 有／部分／沒有
    preferred: dict[Status, int]
    fit_score: float                     # 程式算，0–1；公式見下

class ListJobsOut(BaseModel):
    scope: str                           # 例如「AI 應用／Agent 工程師（7 份）」；提醒模型只有這些職缺
    jobs: list[JobFit]

# ---- get_report ----
class GetReportIn(BaseModel):
    job_id: str

class MatchOut(BaseModel):
    skill: str
    required: bool
    status: Status
    level: Level | None
    evidence: list[str]
    matched_to: str | None
    reason: str                          # 截到 120 字

class GetReportOut(BaseModel):
    job: JobFit
    top_gaps: list[str]                  # report.build_report(job, results).top_gaps
    matches: list[MatchOut]

# ---- get_market_stats ----
class MarketStatsIn(BaseModel):
    kind: Literal["gaps", "strengths", "all"] = "gaps"
    top_n: int = Field(10, ge=1, le=20)

class MarketStatsOut(BaseModel):
    scope: str
    n_jobs: int
    skills: list[SkillStatOut]

# ---- what_if ----
class WhatIfIn(BaseModel):
    add_skills: list[str] = Field(min_length=1, max_length=5)   # 每個 ≤ 60 字

class JobDelta(BaseModel):
    job_id: str
    title: str
    company: str | None
    missing_required_before: int
    missing_required_after: int
    fit_before: float
    fit_after: float

class WhatIfOut(BaseModel):
    applied: list[str]                   # 實際對上的職缺技能名稱
    unmatched: list[str]                 # 沒有任何職缺要求的
    did_you_mean: dict[str, list[str]]   # 相近的技能名稱（difflib）
    jobs: list[JobDelta]                 # 只列有變化的，改善最多的排前面
    top_gaps_before: list[str]
    top_gaps_after: list[str]
    note: str                            # 固定文字：「這是假設，不代表已經有作品證據」
```

**`fit_score`（新的程式規則，要寫進 design.md）：** 只看必備技能，`(有 + 0.5 × 部分) ÷ 必備數`。同分時必備缺口少的在前，再依職缺編號。年資（`min_years`）不進公式，只回傳給模型參考。

**`what_if` 怎麼算：** 把 `add_skills` 用 `normalize.canonical`＋`key` 統一名稱 → 複製比對結果，名稱對上的 `MatchResult` 改成 `status="have"` → 重跑 `market.aggregate` → 和原本的結果比較。只做名稱和同義詞表的比對，不呼叫 LLM，所以「學會 LangGraph」不會自動算到「AI Agent Framework」；這個限制要寫在工具說明裡。

**`llm.call_tools`（新增）：** 和 `call_structured` 並列，收 `system`、`messages`、`tools`、`model`、`max_tokens`、`tool_choice`，回傳（內容區塊、用量）。費用計算、`PRICES` 檢查、軌跡開關沿用現有的寫法。`agent.py` 不直接 import `anthropic`。

---

## 5. Docker 與 CI

### 5.1 相依套件拆三份

| 檔案 | 給誰用 | 內容 |
|---|---|---|
| `requirements.txt` | Streamlit Community Cloud（它固定讀這個檔名）、本機 | 現有的，加 `httpx`；移除 `pytest` |
| `requirements-api.txt` | Docker image | `anthropic`、`pydantic`、`pyyaml`、`python-dotenv`、`pypdf`、`python-docx`、`fastapi`、`uvicorn`、`python-multipart`、`itsdangerous`、Redis client；**鎖定版本**（`==`） |
| `requirements-dev.txt` | CI、本機開發 | 引用上面兩份，加 `pytest`、`ruff`、`mypy` |

API 的 image 不裝 streamlit。版本更新交給 Dependabot 每週開 PR，CI 會跑測試。

### 5.2 Dockerfile 草稿

```dockerfile
# syntax=docker/dockerfile:1
# ---- 第一階段：安裝套件（這一層只在 requirements-api.txt 改變時重建）----
FROM python:3.14-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements-api.txt .
RUN pip install -r requirements-api.txt

# ---- 第二階段：執行用的 image（沒有 pip 快取、沒有測試、沒有 streamlit）----
FROM python:3.14-slim
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SAVE_TRAJECTORIES=0
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
# 由少變到常變排列；只複製明確列出的東西，不用 COPY . .
COPY skill_synonyms.yaml ./
COPY samples/ samples/
COPY job_agent/ job_agent/
COPY api/ api/
USER app
EXPOSE 8080
CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
```

分層的理由：

| 層 | 為什麼這樣排 |
|---|---|
| 兩階段 | 執行用的 image 不帶安裝過程的暫存檔，比較小 |
| 先 `COPY requirements-api.txt` 再安裝 | 只改程式碼時，安裝套件那層用快取，build 只要幾秒 |
| 逐項 `COPY` | 你的工作目錄裡有 `jobs/`、`resumes/`、`profile.yaml`、`.env`、`cache/`、`trajectories/`。`COPY . .` 會把它們包進 image |
| `WORKDIR /app` | `samples.SAMPLES_FILE` 是相對路徑；`normalize.SYNONYMS_FILE` 以檔案為基準，兩個都對得上 |
| 非 root 使用者 | API 不寫任何檔案（`SAVE_TRAJECTORIES=0`、`extract_from_text` 不寫快取），不需要寫入權限 |
| `${PORT:-8080}` | Cloud Run 會用環境變數指定埠號 |

另外要有 `.dockerignore`（`.git`、`.venv`、`.env`、`jobs/`、`resumes/`、`profile.yaml`、`cache/`、`reports/`、`trajectories/`、`.streamlit/secrets.toml`、`tests/`），當第二道防線。**要部署的 image 只在 CI build**：CI 是從 git checkout 出來的，本來就沒有私人檔案。

### 5.3 CI（GitHub Actions）

| Job | 何時跑 | 步驟 | 會擋 merge 嗎 |
|---|---|---|---|
| `test` | 每次 push、PR | Python 3.14 → 安裝 `requirements-dev.txt`（有快取）→ `ruff check` → `pytest` | 會 |
| `typecheck` | 同上 | `mypy job_agent api` | 先不擋（只看報告）；新檔案穩定後改成會擋 |
| `docker` | 同上 | build image → 啟動容器 → 打 `/healthz` → 檢查 image 裡沒有 `jobs/`、`.env`、`profile.yaml` | 會 |
| `deploy` | 只有 main | 推 image（標 git SHA）→ 部署到 Cloud Run → 打線上的 `/healthz` | — |

**測試：** 現有 70 個加上新增的，全部用假的 Claude（`conftest.FakeClient`），CI 不設 `ANTHROPIC_API_KEY`，不花錢。加一個自動套用的 fixture：測試中如果有人建立真的 `Anthropic()` 就直接失敗，避免之後不小心寫出會連網的測試。會花錢的 agent 評估（J4）只在本機手動跑，不進 CI。

**lint／型別檢查：** 加 `ruff`（幾乎零成本，D1 就可以加）。`mypy` 先不擋 merge：現有程式有不少 `client=None` 這類沒標型別的參數，一開始就強制會花掉一整週。

**secrets：**

| 秘密 | 放哪 | 不放哪 |
|---|---|---|
| `ANTHROPIC_API_KEY` | Cloud Run 的 Secret Manager → 環境變數 | image、repo、GitHub Actions、（步驟 3 之後）Streamlit |
| `FRONTEND_KEY` | Cloud Run 和 Streamlit Secrets 各一份 | repo |
| `TOKEN_SECRET`（簽章用） | Cloud Run | repo |
| Redis 連線字串 | Cloud Run | repo |
| 部署權限 | GitHub → GCP 用 Workload Identity Federation（不存長期金鑰） | GitHub Secrets 裡的 JSON 金鑰 |

- Dockerfile 不用 `ARG`／`ENV` 傳秘密；`.env` 只在本機，repo 放 `.env.example`。
- 步驟 3 換 key：在 Console 的 demo workspace 開一把新 key 給 API，確認正常後刪掉 Streamlit 那把。
- repo 是公開的 → 打開 GitHub 的 secret scanning 和 push protection。
- Workload Identity Federation 設定比較繁瑣。卡住超過 2 小時就先手動部署（`gcloud run deploy`），自動部署延到 1 月。

---

## 6. 週計畫

前提：11/30 前完成拆解規則修正、樣本擴充到約 30 份、design.md v0.6。每週 6–8 小時；每週列「必做」和「可以砍」。

| 週 | 日期 | 里程碑（必做） | 做完能 demo 什麼 | 新增測試 | 可以砍 | 風險 |
|---|---|---|---|---|---|---|
| D1 | 12/1（二）–12/6（日） | `service.py`、`dto.py`；FastAPI 本機跑起來（`healthz`、`roles`、`jobs`、`profile`、`analysis`）；`app.py` 改走 `LocalBackend`；CI 的 `test` job | 瀏覽器開 `/docs`，上傳履歷拿到草稿和報告的 JSON；GitHub 上看到綠勾 | 約 12 個：service 的輸出和 `aggregate` 一致；API 的 200／413／415／422／502；部分失敗；錯誤格式。原本 70 個只改 1 個（`test_app_shows_result_page` 塞進 `session_state` 的假結果要換成新格式），其他不能壞 | `ruff` | 中 |
| D2 | 12/7（一）–12/13（日） | Dockerfile、`.dockerignore`、`requirements-api.txt`；本機 `docker run`；GCP 專案、預算警示、Secret Manager；部署到 Cloud Run；`X-Frontend-Key` | 用自己的電腦對 `https://…run.app` 上傳履歷拿到報告；量冷啟動和一次分析的秒數（us-west1 和 asia-east1 各量一次） | 約 4 個：沒帶密鑰 401、帶錯 401、`healthz` 不用密鑰；CI 的 image 檢查腳本 | CI 的 `docker` job（先手動 build） | **最高（12 月）** |
| D3 | 12/14（一）–12/19（六） | `limits.py`（`Store`、預留結算、限流）；Upstash；`ApiBackend` 和 `API_BASE_URL` 開關，本機驗證。**線上開關維持關閉** | 第 4 次上傳回 429；把預算設成 0 回 503；本機的 Streamlit 走線上 API 跑完整流程 | 約 14 個：20 個執行緒同時預留，只有預算內的通過；失敗退款；跨日歸零（台北時間）；`Retry-After`；Redis 斷線回 503；`ApiBackend` 把錯誤碼轉成中文訊息 | IP 輔助訊號 | 中；也是 D2 的緩衝 |
| — | 12/20–12/25 | 不排工作。線上是舊路徑，API 有密鑰保護 | | | | |
| D4 | 12/26（六）–1/3（日） | 線上打開 `API_BASE_URL`，觀察兩天後換 key、移除 Streamlit 的 key；CI 補 `docker`、`deploy`；README 架構圖；design.md v0.7；tag `v2.0`；1/3 回顧 | 公開網址走 API；push 到 main 自動部署；README 有架構圖和實測秒數 | 「不准建立真的 client」的 fixture；線上 `/healthz` 的部署後檢查 | 自動部署、`mypy` | 中（自動部署設定） |
| J1 | 1/4（一）–1/10（日） | `tools.py`（4 個工具、`fit_score`、`what_if`）；`state.py`（token）；`llm.call_tools`；`/v1/analysis` 回傳 token | 指令列直接呼叫工具，例如對範例 profile 跑 `what_if RAG`，看到每份職缺的變化 | 約 20 個：每個工具的正常情況；`what_if` 的結果等於「改完狀態再 `aggregate`」；`unmatched` 和 `did_you_mean`；參數不合法；結果截斷；token 來回、竄改、過期、樣本版本不同 | 指令列介面 | 低 |
| J2 | 1/11（一）–1/17（日） | `agent.py` 的 `run_turn`（單輪）；所有上限和停止條件；工具錯誤處理；指令列版；用 Haiku 實際跑 10 題 | 終端機問「哪 3 份最適合我」「學會 RAG 會怎樣」，看到 agent 一步步呼叫工具再回答 | 約 15 個（照劇本回答的假 Claude）：兩輪正常流程；一次多個工具；參數錯 → 修正；不存在的工具；次數用完 → 收尾；預算不夠；重複呼叫；`refusal`；`max_tokens` | 重複呼叫偵測 | **最高（1 月）** |
| J3 | 1/18（一）–1/24（日） | `POST /v1/chat`（一次回傳答案和步驟）；每對話預算；對話紀錄；引用檢查；Streamlit 對話頁；部署 | 公開網址：報告下面可以問 agent，畫面顯示它查了什麼 | 約 12 個：履歷藏指令的 profile；要求忽略規則的問題；偽造的對話紀錄；引用不存在的職缺 → 修正；第 13 輪 429；對話預算用完 | 多輪對話（先每題獨立） | 中高 |
| J4 | 1/25（一）–1/31（日） | 評估集 15–20 題和評估腳本；Haiku 和 Sonnet 比較；README、demo GIF；design.md v0.8；面試講法定稿；1/31 回顧 | 一張表：選對工具的比例、引用正確率、平均幾次呼叫、每題費用、秒數 | 評估腳本本身的測試（用假 Claude）；評估集格式檢查 | 串流（SSE）、Sonnet 比較 | 中 |

**風險最高的兩週：**

- **D2（Docker＋第一次雲端部署）：** 新東西最多（Docker、GCP 帳號、IAM、secrets）。如果開發機的網路有 HTTPS 檢查，`docker pull` 可能失敗；備案是只在 CI build。排在 12 月第二週，是為了讓 D3 當緩衝，而且正式切換（D4）在假期之後，有人看著。
- **J2（agent loop）：** 模型實際怎麼選工具要跑了才知道；Haiku 可能選得不好。對策：J1 先把工具做穩；J2 第一天就用真的模型跑 3 題，不要等 loop 寫完；loop 的每個分支都用假 Claude 測。

**如果進度落後，依序砍：** 串流 → Sonnet 比較 → 自動部署 → 多輪對話 → `mypy`。不能砍：預留結算的預算、`X-Frontend-Key`、評估集。

評估集抓的費用：20 題用 Haiku 約 US$0.04，用 Sonnet 約 US$0.72。

---

## 7. 面試講法（5–8 句）

1. 我先把固定流程的每一步做到可以測試，再把這些步驟包成工具交給 agent：agent 決定查什麼、查幾次、什麼時候停，但證據、狀態、排序永遠由程式計算，所以結果可重現。
2. 加 FastAPI 不是為了開放 API，而是讓 Streamlit、agent、之後的前端共用同一套邏輯，API key 只存在後端。
3. API 是無狀態的：分析結果簽章後交給前端保管，伺服器不存履歷也不存對話；代價是每次請求多帶幾十 KB，換到的是隱私，以及免費平台休眠也不會掉資料。
4. 費用保護有三層：用 Redis 原子操作做「先預留、後結算」的每日預算、每連線和每對話的上限、Console 的月上限；Redis 連不上時我選擇停止服務，而不是沒有保護地繼續花錢。
5. agent loop 是自己寫的，沒有用框架：最多 6 次模型呼叫、每對話有費用上限、工具出錯會把錯誤回給模型讓它修正、超過上限就禁用工具強制收尾，每個分支都有測試。
6. 防 prompt injection 主要靠縮小能力而不是靠 prompt：工具都是唯讀的純函式，只看得到使用者自己的資料，context 裡沒有秘密；再加上結構化的工具結果和程式做的引用檢查。
7. 我用離線評估集量 agent 的行為（選對工具的比例、引用正確率、每題費用），先用最便宜的模型，量過才決定要不要換。
8. 遷移分四步，每一步都能部署、用一個環境變數就能回退。

---

## 8. 待決定

| # | 要決定的事 | 我的建議 | 什麼時候要定 |
|---|---|---|---|
| 1 | 部署平台：Cloud Run 還是 Render 免費版 | Cloud Run。前提是你願意綁信用卡，並接受沒有花費硬上限（最壞約 US$60／月，設 US$5 警示）。不願意就選 Render，設計不變 | 12/6 前 |
| 2 | 計數器放 Upstash Redis 還是 Firestore | Upstash：不綁平台、不用信用卡、Redis 是常見技能 | 12/13 前 |
| 3 | Docker 在哪台電腦跑 | 個人電腦裝 Docker Desktop（WSL2）。如果網路環境會擋 image 下載，就只在 CI build | 12/6 前 |
| 4 | agent 用哪個模型 | 先 Haiku；J4 用評估集和 Sonnet 比（Sonnet 每輪貴約 20 倍） | J4 |
| 5 | 「貼單份職缺」什麼時候做 | 11/30 前沒做完就不做 Streamlit 版，2 月直接做成 `/v1/gap-report`，避免做兩次。它會帶進不可信的職缺文字，也要等 agent 的防護到位 | 11/30 回顧 |
| 6 | 每對話上限：12 輪、US$0.05、每題 6 次模型呼叫 | 先用這組，J2 實跑 10 題後依實際費用調整 | J2 |
| 7 | 對話紀錄要不要也簽章 | 先不簽：只影響使用者自己的對話，工具唯讀、有費用上限。J3 的注入測試有問題再加 | J3 |
| 8 | `fit_score` 公式：`(有 + 0.5 × 部分) ÷ 必備數` | 先用這個；年資不進公式。你可以用自己的 20 份職缺看排名合不合直覺 | J1 |
| 9 | 要不要加第 5、6 個工具（`compare_jobs`、`find_skill`） | 先不加。J2 實跑後，如果模型常為了比較兩份職缺而出錯，再加 `compare_jobs` | J2 結束 |
| 10 | agent 的回答要不要串流 | J3 先一次回傳答案和步驟；有時間 J4 再做 SSE | J3 |
| 11 | 讓使用者在分析前修改 level | API 已經支援（`/v1/analysis` 收 profile）；畫面 2 月再做 | 2 月 |
| 12 | 學習資源推薦什麼時候做 | 2 月。原則已寫在 ADR-5 (7)；要先確認 Console 有開網頁搜尋，以及 Haiku 的工具設定 | 1/31 回顧 |
| 13 | `mypy` 要不要擋 merge | 12 月不擋；1 月對新檔案（`tools.py`、`agent.py`、`api/`）改成會擋 | D4 |
| 14 | 步驟 3 之後 Streamlit 要不要留 key 當備援 | 不留。key 只在一個地方比較好解釋，也少一個外洩點 | D4 |
| 15 | Cloud Run 放哪個區域 | 先用 us-west1；D2 和 asia-east1 各量一次再定。換區域只要重新部署並改 `API_BASE_URL` | D2 |

---

## 9. 參考來源（2026-11-17 查詢）

平台的價格和限制會變，決定前請再看一次官方頁面。Fly.io 和 Render 的部分數字來自第三方整理。

- [Cloud Run pricing](https://cloud.google.com/run/pricing)
- [Cloud Run：Configure request timeout](https://cloud.google.com/run/docs/configuring/request-timeout)
- [Render：Platforms with a real free tier for developers in 2026](https://render.com/articles/platforms-with-a-real-free-tier-for-developers-in-2026)
- [Render：SSE vs WebSockets for streaming responses](https://render.com/articles/sse-vs-websockets-llm-streaming)
- [Fly.io pricing 2026（temps.sh 整理）](https://temps.sh/blog/fly-io-pricing-2026)
- [Upstash Redis pricing](https://upstash.com/pricing/redis)
- [Streamlit：st.context](https://docs.streamlit.io/develop/api-reference/utilities/st.context)
- [Streamlit Community Cloud：Manage your app（休眠規則）](https://docs.streamlit.io/streamlit-community-cloud/manage-your-app)
- [Claude Platform Docs：Web search tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool)
- [Claude Platform Docs：Define tools（tool_choice）](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools)
- [Claude Platform Docs：Strict tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use)
- [Claude Platform Docs：Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)