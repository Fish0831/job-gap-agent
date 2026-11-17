# job-gap-agent：評估機制設計

> 版本 v1.0（2026-11-17）。建立在 main = `25d7051`、`docs/design.md` v0.5、`design-dec-jan.md` v1.1 之上。
> 這份是設計；程式只有骨架，不是完整實作。文中的函式、常數、檔名都對照過目前的程式碼；標「新增」的是要寫的。
> 「現況」欄的數字有兩種來源：**〔算〕** = 我用公開的 `samples/jobs.json`（20 份）直接算的，不花錢、你可以重現；**〔紀錄〕** = 交接紀錄或 `design.md` 裡你量過的。
> 門檻都是建議值。第一輪量完基準後要校準一次（待決定 12）。

## 0. 摘要

**一句話：** 評估分三層——不花錢的程式檢查（每次 commit）、對人工標準答案的準確率（改 prompt 或模型時）、同輸入重跑的穩定度（改 prompt 或模型時）——一輪完整評估約 US$0.32（20 份）到 US$0.52（60 份）。

| 層 | 回答什麼 | 什麼時候跑 | 費用 | 在哪跑 |
|---|---|---|---|---|
| L0 程式檢查 | 資料有沒有明顯異常（必備比例、命名切碎、引用的原文不存在） | 每次 commit、每加 10 份職缺 | US$0 | CI＋本機 |
| L1 對標準答案 | 每個 LLM 步驟準不準 | 改 prompt、換模型 | US$0（用快取和 L2 存下來的結果） | 本機（要私人資料） |
| L2 重跑穩定度 | 同樣輸入，結果會跳多少；改動有沒有超過雜訊 | 改 prompt、換模型；每月一次 | 約 US$0.10–0.32 | 本機 |

**現況基準：**

| 項目 | 現況 | 來源 |
|---|---|---|
| 樣本 | 20 份、381 個技能項目（平均 19／份，必備平均 14） | 〔算〕 |
| 必備比例 | 0.47–1.00；01、04、20 三份是 100% 必備（16/16、12/12、16/16） | 〔算〕 |
| 命名切碎 | 統一名稱後 211 個不同技能，153 個（73%）只出現在 1 份；出現在 3 份以上的只有 33 個 | 〔算〕 |
| 同義詞的影響 | 「AI coding 工具」被拆成 6 個名稱（每個需求分數 ≤ 6）；合併後需求 14、7 份必備，會排第 6。`Tool Calling`＋`Tool Use` 合併後也是 14。只合併這兩組，需求前 10 名就換 2 個 | 〔算〕 |
| `soft` 類別誤丟 | `Requirement Analysis`（10 號）、`Technical Writing`（10、19 號）被模型標成 `soft`，`drop_soft` 會丟掉；其他職缺的同名技能是 `other`、有保留 | 〔算〕 |
| 需求前 10 名對樣本的敏感度 | bootstrap 重抽後和現在的前 10 名平均重疊 7.4／10；`ai-product`（5 份）只有 5.0／10 | 〔算〕 |
| 履歷轉換 | 編造 0、抓到 23/30、狀態一致 16/23、前 10 名 8/10（一份履歷、跑一次） | 〔紀錄〕 |
| 比對穩定度 | 只有個案：CI/CD 優先度 6 ↔ 12 | 〔紀錄〕 |
| 拆解穩定度 | 只有個案：同一份職缺 32 → 22 個技能 | 〔紀錄〕 |
| 拆解、比對的準確率 | 沒量過 | — |

**三個最重要的結論：**

1. **先修拆解的命名，再量其他東西。** 命名切碎會同時污染市場報告的排名、樣本夠不夠的判斷、以及比對的穩定度（同一個技能換個名字就多判一次）。
2. **排名跳動有一半是規則造成的，不花錢就能改。** 把優先度從「先取多數、再乘權重」改成「每份職缺各算一次再加總」（3.3 節方案 A），一次狀態翻轉的影響從「整個優先度加倍」變成「最多差 2」。
3. **存原始結果，規則離線重算。** 穩定度實驗把每次的 `MatchResult` 存下來；之後要比較平手規則、`fit_score` 權重、同義詞表，都用同一批資料重算，不用再花錢。

---

## 1. 評估總表

符號：`key()`、`canonical()` 是 `normalize.py` 現有的函式。拆解結果一律先經過 `drop_soft` → `unify` → `dedupe` 再比較（和比對用同一套規則）。狀態的好壞順序：有 > 部分 > 沒有。

### 1.1 extract（拆解）

| 指標 | 公式 | 需要的標準答案 | 門檻建議 |
|---|---|---|---|
| `quote_grounding_rate` | `original_text`（去掉空白後）是職缺原文子字串的項目數 ÷ 項目數 | 不用（要本機的 `jobs/`、`cache/`） | ≥ 0.98；找不到的逐筆看 |
| `fabricated_count` | 多抓的項目中，人工確認「原文沒有」的數量 | G1 | 0 |
| `precision` | TP ÷ 模型抓到的技能數 | G1 | ≥ 0.80，目標 0.90 |
| `recall` | TP ÷ 標準答案的技能數 | G1 | ≥ 0.80，目標 0.90 |
| `required_accuracy` | TP 中 `required` 和標準答案相同的數量 ÷ TP | G1 | ≥ 0.90 |
| `over_required_rate` | 模型標必備、標準答案是加分的數量 ÷ TP 中標準答案是加分的數量 | G1 | ≤ 0.10 |
| `under_required_rate` | 反方向 | G1 | ≤ 0.10 |
| `required_lint_flags` | 程式規則掃出的可疑項目數（6.4 節） | 不用 | 每個都看過 |
| `soft_drop_conflicts` | 模型標成 `category == "soft"`、但名稱不在 `SOFT_SKILLS` 的項目數（現在這些會被 `drop_soft` 丟掉） | 不用 | 0（現況 3） |
| `n_skills_cv` | 每份職缺重跑 N 次，技能數的標準差 ÷ 平均；報所有職缺的中位數和最大值 | 不用 | 中位數 ≤ 0.10、最大 ≤ 0.20 |
| `skill_jaccard` | 兩次重跑的技能集合 交集 ÷ 聯集，所有配對取平均 | 不用 | ≥ 0.75 |
| `required_flip_rate` | N 次都出現的技能中，`required` 不是每次都一樣的比例 | 不用 | ≤ 0.10 |
| `reextract_top10_churn` | 全部重拆一次後，需求前 10 名換了幾個 | 不用 | ≤ 2 |
| `singleton_rate` | 只出現在 1 份職缺的技能數 ÷ 不同技能數 | 不用 | 看趨勢（現況 0.73） |
| `name_variants_top20` | 需求前 20 名的技能中，還有其他名稱沒合併的數量 | 人工看候選清單 | 0 |

TP 的定義：模型的技能名稱（或標準答案 `accept` 裡的名稱）經 `key(canonical())` 後和標準答案對上。同一個標準答案被對到第二次算「拆太細」（`split`），另外計數，不算多抓。

`over_required_rate` 是最要盯的：它會灌高市場需求分數，也直接改變 `fit_score` 的分母（GARMIN 那一類錯誤）。

### 1.2 match（`semantic_map`）

只算交給 LLM 的項目（`how != "name"`）；名稱直接對上的由程式保證。

| 指標 | 公式 | 需要的標準答案 | 門檻建議 |
|---|---|---|---|
| `relation_accuracy` | 關係在可接受範圍、而且對應到的 profile 技能正確的數量 ÷ 項目數 | G2 | ≥ 0.85 |
| `status_accuracy` | 狀態和標準狀態相同的數量 ÷ 項目數。標準狀態 = 把標準答案的對應丟進 `decide()` | G2 | ≥ 0.85 |
| `false_have_count` | 模型判「有」、標準不是「有」的數量 | G2 | 0 |
| `inflation_rate` | 模型的狀態比標準好的數量 ÷ 項目數 | G2 | ≤ 0.05 |
| `conservative_rate` | 模型的狀態比標準差的數量 ÷ 項目數 | G2 | ≤ 0.15 |
| `invalid_rate` | 「LLM 沒有回這個技能」＋「對應到不存在的技能」÷ 項目數 | 不用 | ≤ 0.01 |
| `status_flip_rate` | N 次重跑狀態不完全相同的（職缺, 技能）數 ÷ 全部（職缺, 技能）數 | 不用 | ≤ 0.10 |
| `cross_job_inconsistency` | 出現在 2 份以上、而且 `SkillStat.consistent` 是 False 的技能數 ÷ 出現在 2 份以上的技能數 | 不用 | ≤ 0.10 |
| `exact_rate` | `stats["exact"]` ÷（`exact` ＋ `semantic`） | 不用 | 只記錄；越高越省錢、越穩 |

門檻不對稱是刻意的：工具是找缺口的，誤判成「有」比誤判成「沒有」嚴重，所以 `false_have_count` 必須是 0，`conservative_rate` 可以寬一點。

### 1.3 resume（履歷 → profile 草稿）

沿用 `compare_profiles` 的 `both`／`only_draft`／`only_ref`。

| 指標 | 公式 | 需要的標準答案 | 門檻建議 | 11/10 |
|---|---|---|---|---|
| `fabrication_count` | 草稿的技能或作品在履歷原文找不到依據的數量 | 履歷原文（人工） | 0 | 0 |
| `recall` | `both` ÷ 手寫技能數 | `profile.yaml` | 只記錄 | 23/30 = 0.77 |
| `recall_adj` | `both` ÷（手寫技能數 − 履歷沒寫的技能數） | G3 | ≥ 0.85 | 待量 |
| `status_agreement` | `status_of` 相同的數量 ÷ `both` | `profile.yaml` | ≥ 0.75 | 16/23 = 0.70 |
| `inflation_count` | 草稿「有」、手寫「部分」的數量 | `profile.yaml` | 0 | 0 |
| `level_agreement` | level 相同的數量 ÷ `both`（手寫的 `strong` 當成 `working`，因為草稿不能輸出 `strong`） | `profile.yaml` | ≥ 0.60，只記錄 | 待量 |
| `level_over_rate` | 草稿 level 比手寫高的數量 ÷ `both` | `profile.yaml` | ≤ 0.15 | 待量 |
| `draft_jaccard` | N 次草稿技能集合的 交集 ÷ 聯集，所有配對取平均 | 不用 | ≥ 0.80 | 待量 |
| `draft_top10_overlap` | 用草稿和用手寫 profile 各跑市場報告，前 10 名的重疊數 | `profile.yaml` | ≥ 7 | 8 |

`recall_adj` 把 11/10 的兩種漏抓分開：「履歷沒寫」不算模型的錯（要改的是履歷），「履歷有寫但沒抓」才進分母。

level 的優先度可以放低：`decide()`、`priority()`、`fit_score` 都不用 level，它只影響畫面上顯示的字。所以 level 不準不會讓排名出錯，門檻只記錄、不擋改動。

### 1.4 市場報告整體

| 指標 | 公式 | 需要的標準答案 | 門檻建議 |
|---|---|---|---|
| `top10_overlap` | 兩次重跑的 `top_gaps` 前 10 名交集數，所有配對取平均和最小值 | 不用 | 平均 ≥ 8、最小 ≥ 7 |
| `rank_tau` | 兩次重跑對「任一次前 20 名的聯集」的優先度做 Kendall τ，所有配對取平均 | 不用 | ≥ 0.80 |
| `priority_max_jump` | 同一個技能 N 次的優先度 最大 − 最小；報前 10 名裡最大的 | 不用 | ≤ 4（現況個案 6） |
| `gap_precision@10` | 前 10 名中我確認「真的是缺口」的數量 ÷ 10（其餘標成：判斷錯、名稱重複、不算技能） | 人工 2 分鐘 | ≥ 0.8 |
| `demand_bootstrap_top10` | 見第 4 節 | 不用 | ≥ 8.5 |

### 1.5 fit_score 排名

| 指標 | 公式 | 需要的標準答案 | 門檻建議 |
|---|---|---|---|
| `spearman_fit` | `fit_score` 和我的 1–5 分評分的 Spearman ρ（`skill_fit`、`overall_fit` 各算一次） | G4 | ≥ 0.6 |
| `top5_overlap_human` | 程式前 5 名 ∩ 我的前 5 名 | G4 | ≥ 3 |
| `fit_sd_max` | 每份職缺的 `fit_score` 在 N 次重跑的標準差，取最大 | 不用 | ≤ 0.05 |
| `top5_overlap_runs` | 兩次重跑的前 5 名交集數，所有配對取平均 | 不用 | ≥ 4 |
| `role_fit_agreement` | N 次中「最接近的職務類別」和多數結果相同的比例 | 不用 | ≥ 0.9 |
| `denominator_bias` | `fit_score` 和必備數的 Spearman ρ | 不用 | 絕對值 ≤ 0.3 |
| `required_sensitivity` | 把所有項目都當必備重算一次，和原排名的 Kendall τ | 不用 | ≥ 0.7 |

樣本小，比例一律連同個數一起報（例如 16/23），並在需要時附 Wilson 信賴區間。20 份職缺算出來的 ρ = 0.6，區間大約是 0.2–0.8，只能當方向參考。

---

## 2. 標準答案

全部放在 `gold/`（新增，加進 `.gitignore`）：裡面有職缺原文片段、你的技能和評分，不公開。公開的只有數字。

| 代號 | 檔案 | 內容 | 數量 | 時間 |
|---|---|---|---|---|
| G1 | `gold/extract/<job_id>.yaml` | 一份職缺的技能清單、必備或加分、出自哪一段 | 8 份（第一輪 4 份） | 每份約 15 分鐘；4 份 1 小時，8 份 2 小時 |
| G2 | `gold/match.csv` | 職缺技能 → 應該對到哪個 profile 技能、什麼關係 | 約 60 個技能 | 20 分鐘 |
| G3 | `gold/profile_notes.yaml` | 手寫 profile 的 30 個技能，各標「履歷有沒有寫」 | 30 個 | 5 分鐘 |
| G4 | `gold/fit.csv` | 每份職缺我自己打的適合度 | 20 份 | 15 分鐘 |
| G5 | `jobs/*.txt` 開頭的 `role:` | 已經有了；多加一行 `role_confidence:` | 20–60 份 | 5 分鐘 |

第一輪（G4＋G1 開發組）約 1 小時 15 分；全部標完約 2 小時 45 分。之後每加 10 份職缺，只要補 G4（5 分鐘）和 G5。

### 2.1 G1：拆解（8 份）

**選哪 8 份：** 三個類別都有、長短都有、已知有問題的都在。

| 用途 | 職缺 | 為什麼 |
|---|---|---|
| 開發組（第一輪標；可以看錯誤、用來調 prompt） | 15 GARMIN（ml，13/22）、01（ai-product，16/16）、20（ai-app，16/16）、05 TSMC（ml，7/15） | 15 是已知問題；01、20 是 100% 必備；05 是必備比例最低的 |
| 測試組（之後標；只看數字，不拿來調 prompt） | 12（ml，37 項，最長）、16 PRO360（ai-product）、17（ai-app，33 項）、09 NVIDIA（ai-app） | 分開才不會把 prompt 調成只對這幾份有效 |

開發組 69 個項目、測試組 98 個。69 個項目時，precision 0.85 的 95% 區間約 ±0.08；167 個時約 ±0.05。

**兩步標註：**

1. **盲標（每份約 10 分鐘）：** 只打開 `jobs/NN.txt`，不看模型的拆解。寫出技能、必備或加分、出自哪一段。
2. **對齊（每份約 5 分鐘）：** 跑 `python -m evals.extract_eval --align NN`，它會列出兩邊沒對上的項目。名稱不同但是同一個技能的，寫進 `accept`；模型多抓的，每個標一種類型。

先盲標是為了不被模型的答案帶著走；如果直接在模型輸出上打勾，recall 會被高估。

**格式：**

```yaml
# gold/extract/15.yaml
job_id: "15"
annotated: 2026-11-18
minutes: 14                # 標註花了幾分鐘（之後估時間用）
skills:
  - skill: PyTorch
    required: true
    section: qualification   # qualification｜duty｜overview｜preferred
  - skill: LLM
    required: false
    section: overview        # 「涵蓋技術／有機會接觸」→ 不算必備
    accept: [Large Language Model]   # 模型用這些名稱也算對
  - skill: Vector Database
    required: true
    section: duty
    accept: [Vector Search]
ignore:                      # 邊界項目：抓到不算錯，沒抓到也不算漏
  - English
fp:                          # 第二步才填：模型多抓的，每個標一種
  - skill: Model Quantization
    type: gold_missed        # gold_missed｜fabricated｜soft｜split｜dup
```

`fp` 的類型：`gold_missed` = 我漏標（補進 `skills`）；`fabricated` = 原文沒有；`soft` = 軟實力或太籠統；`split` = 把一項拆太細；`dup` = 和另一項重複。

**標註準則：**

1. 照 `extract.SYSTEM` 的規則標，因為要量的是「模型有沒有照規格做」：不同技能的列表拆開、同一類東西的舉例合併成一項。
2. `required` 看段落：條件要求／必備條件 → true；加分／者佳／preferred → false；工作內容裡核心職責用到的技術 → true；「涵蓋技術、有機會接觸、tech stack 概覽」→ false，除非條件要求也提到。
3. 軟實力、資料格式、語言能力不標；不確定算不算技能的放 `ignore`，不要硬標。
4. 名稱用簡短英文即可，不用和模型一樣，第二步會對齊。
5. 第二步只能做兩件事：加 `accept`、標 `fp`。除了 `gold_missed` 以外，不因為看到模型輸出而改答案。`gold_missed` 的數量本身要記錄（它代表我自己的漏標率）。

### 2.2 G2：比對（約 60 個技能）

網頁版的樣本沒有 `original_text`，`semantic_map` 只看得到技能名稱，所以標準答案以「技能」為單位就夠了，不用每份職缺各標一次。

**標哪些：** 腳本先列出「名稱對不上、要交給 LLM」的技能。出現在 2 份以上的全標（統一名稱後有 58 個，扣掉名稱直接對上的，估計 40–45 個），再加固定亂數種子抽的 20 個只出現 1 次的。

```csv
requirement_skill,expected_relation,expected_profile_skill,accept_relation,n_jobs,note
Vector Database,none,,,4,
LangChain,related,AI Agent,related|none,1,邊界：有手寫 agent loop
LLM Application Development,same,LLM API Integration,same|related,1,
```

`requirement_skill`、`n_jobs` 由腳本填好；你填中間三欄。`accept_relation` 給邊界項目用：列出的關係都算對。

**準則：** 用面試當判斷標準——面試官問這個技能，我能不能拿 profile 那個技能的作品來回答？能直接回答 = `same`；能回答一部分 = `related`；不能 = `none`。不確定在兩個之間時，`expected_relation` 填比較保守的，`accept_relation` 填兩個。

報兩個數字：嚴格（只算 `expected_relation`）和寬鬆（`accept_relation` 都算對）。兩者差很多代表邊界項目多，那是技能本身模糊，不是模型的問題。

### 2.3 G3、G4

```yaml
# gold/profile_notes.yaml：手寫 profile.yaml 的每個技能標一次
- name: LLM Cost Optimization
  in_resume: true      # 履歷有寫 → 草稿沒抓到算模型的錯
- name: Docker
  in_resume: false     # 履歷沒寫 → 不算模型的錯（要改的是履歷）
```

```csv
job_id,skill_fit,overall_fit,note
17,5,5,0–3 年、歡迎有作品
07,3,1,技能有一半但要 5 年
```

`skill_fit`（1–5）只看技能；`overall_fit`（1–5）加上年資、產業、意願。**一定要在看程式排名之前打分。** 兩欄分開是為了回答「年資要不要進公式」：如果 `fit_score` 和 `skill_fit` 很一致、和 `overall_fit` 差很多，代表公式本身沒問題，缺的是技能以外的資訊。

### 2.4 之後讓 LLM 分類 role：怎麼量準確率

| 項目 | 做法 |
|---|---|
| 標準答案 | `jobs/*.txt` 的 `role:`。多加一行 `role_confidence: clear` 或 `boundary`（06、07、12 這類）。這一行不送給模型，不影響快取指紋 |
| 指標 | `accuracy`、每類的 precision／recall、`macro_f1`、3×3 混淆矩陣；另外報「只算 clear 的 accuracy」 |
| 基準線 | 全猜最多的那一類（現在 ml 8/20 = 0.40）；職稱和關鍵字規則。LLM 要明顯贏過關鍵字規則才值得用 |
| 避免洩題 | `ROLE_HINTS` 是邊標邊定的規則，prompt 也會參考這些職缺。把職缺固定分成開發組（寫 prompt、挑範例）和測試組（只看數字），範例不能來自測試組。60 份時各 30 份，每類各半 |
| 自己的一致性 | 規則改過（13 號從 ai-app 改成 ai-product）。隔一週以上、不看舊答案重標 10 份，算自己前後一致的比例。這是 LLM 準確率的上限 |
| 模型自己的一致性 | 測試組跑 3 次，三次都相同的比例 ≥ 0.9 |
| 門檻 | 測試組 `accuracy` ≥ 0.85、clear 的 ≥ 0.95。達不到就維持樣本人標，LLM 只用在使用者貼的職缺，並允許輸出「不確定」 |
| 區間 | 30 份對 27 份的 95% 區間約 0.74–0.97；60 份對 54 份約 0.80–0.95。報數字時附上 |
| 費用 | 60 份 × 3 次約 US$0.07（Haiku，輸入是整份職缺原文） |

---

## 3. 穩定度實驗

5.5 系列不能帶 `temperature`（`test_extract.py` 有檢查），所以沒辦法把模型固定住，只能量跳動有多大，再用規則或投票壓低。

### 3.1 看哪些數字

| 實驗 | 固定什麼、重跑什麼 | 看的數字 |
|---|---|---|
| S1 比對 | 拆解固定（`samples/jobs.json`），`pipeline.analyze` 重跑 N 次 | `status_flip_rate`、`cross_job_inconsistency`、`top10_overlap`、`rank_tau`、`priority_max_jump`、`fit_sd_max`、`top5_overlap_runs`、`role_fit_agreement` |
| S2 拆解 | 職缺原文固定，`extract_from_text` 重跑 N 次，每次再比對一次 | `n_skills_cv`、`skill_jaccard`、`required_flip_rate`、`reextract_top10_churn` |
| S3 履歷 | 履歷固定，`profile_from_text` 重跑 N 次 | `draft_jaccard`、每次的技能數和作品數、都出現的技能中狀態或 level 不一致的比例 |

S1 的結果是「比對造成的跳動」；S2 是「拆解＋比對」。兩者的差就是拆解的貢獻。

S1 的數字同時是**雜訊下限**：之後改 prompt 或換模型，指標的變化要超過 S1 自己 N 次之間的範圍，才算真的有差（6.5 節）。

**兩個要注意的地方：**

- S2 要直接呼叫 `extract_from_text`，不要用 `extract_requirements(use_cache=False)` 或 `--no-cache`：後者會覆蓋 `cache/`，下次匯出樣本就換了一份拆解。
- S1 用網頁版的條件跑（`load_samples()`，沒有 `original_text`），因為那是使用者實際看到的。CLI 有原文、判斷可能比較準，可以另外跑一次比較（第二輪）。

### 3.2 N 要多少、花多少錢

如果某個項目有 q 的機率出現少數結果，重跑 N 次能看到至少一次翻轉的機率是 `1 − (1−q)^N − q^N`：

| q | N=3 | N=5 | N=10 |
|---|---|---|---|
| 0.2 | 0.48 | 0.67 | 0.89 |
| 0.1 | 0.27 | 0.41 | 0.65 |

建議：**平常 N=5；建立基準時 S1 跑一次 N=10。** N=3 會漏掉一半以上不穩定的項目。

單價用現有的實測：拆解約 US$0.0025／份（20 份約 US$0.05）、比對約 US$0.001／份（網頁實測 US$0.012 = 履歷 US$0.0024 ＋ 10 份比對）、履歷 US$0.0024／次。

| 實驗 | 呼叫次數 | 20 份樣本 | 60 份樣本 |
|---|---|---|---|
| S1 比對，N=5 | 5 × 份數 | US$0.10 | US$0.30 |
| S2 拆解，N=3，固定 20 份 | 60 次拆解＋60 次比對 | US$0.21 | US$0.21 |
| S3 履歷，N=5 | 5 | US$0.012 | US$0.012 |
| L1 對標準答案 | 0（用快取和 S1 的結果） | US$0 | US$0 |
| **一輪合計** | | **約 US$0.32** | **約 US$0.52** |
| 基準時 S1 改 N=10 | | 多 US$0.10 | 多 US$0.30 |

新收的職缺技能項目比較多，比對的實際費用可能高 2 成左右。腳本開跑前先印估計費用，超過 `--budget`（預設 US$0.50）就停（沿用「先檢查再花錢」）。

S2 在 60 份時仍然只跑固定的 20 份（G1 的 8 份＋每類再抽 4 份），不然光拆解就要 US$0.45。

### 3.3 平手規則怎麼改

**跳動是三層疊起來的：**

1. LLM 對同一組（職缺技能, profile）每次判斷不同。
2. 同一個技能在每份職缺各判一次，再用 `overall_status` 取多數；平手取比較差的，所以剛好平手時，一次翻轉就換狀態。
3. `priority = demand × GAP_WEIGHT[status]`，權重是 0／1／2 的階梯；狀態一換，優先度直接加倍或減半（CI/CD：6 × 1 ↔ 6 × 2）。

第 2、3 層是程式規則，不花錢就能改。

**方案 A（建議現在做）：每份職缺各算一次再加總**

```python
def priority(stat: SkillStat) -> int:
    return (sum(2 * GAP_WEIGHT[stat.statuses[j]] for j in stat.required_in)
            + sum(GAP_WEIGHT[stat.statuses[j]] for j in stat.preferred_in))
```

- 所有職缺判斷一致時，結果和現在完全相同（`demand × 權重`）。
- 一次翻轉（部分 ↔ 沒有）只改變那一份的貢獻：必備差 2、加分差 1。現在最多差到整個 `demand`。
- 例子（假設 CI/CD 是必備 2 份＋加分 2 份，需求 6）：現在是 12 ↔ 6；方案 A 是 9 ↔ 8。
- 顯示用的狀態照舊（取多數、平手取保守、標 ⚠️），只是不再決定排名。

| 好處 | 代價 |
|---|---|
| 不花錢；只改 `market.priority()` 一個函式 | `test_priority_is_demand_times_gap_weight` 裡不一致的那個案例（RAG：12 → 10）要改 |
| 說明更直接：「每份職缺的缺口加起來」 | 沒有減少 LLM 的翻轉；`strengths()` 和 `fit_score` 仍然用單一狀態，還是會跳 |
| 可以用 S1 存下來的結果離線比較新舊規則 | 「狀態是我的屬性」這件事沒有被修好，只是不再被放大 |

**方案 B（12 月視情況）：每個技能只判一次，投票 K 次**

把比對的單位從「每份職缺」改成「每個不同的技能」：先把所有職缺對不上的技能收集起來去重，分批（每批 ≤ 40 個）問 LLM，每批問 K=3 次取多數（沒有多數取保守），結果套用到所有職缺。

- `cross_job_inconsistency` 變成 0，平手規則不再需要。
- 投票讓單次翻轉變少：單次出現少數結果的機率 0.2 → 三次取多數後約 0.10；0.1 → 約 0.03。
- `fit_score` 和 `strengths()` 也一起變穩。

| 好處 | 代價 |
|---|---|
| 從源頭修：同一個技能只有一個狀態 | 改動大：`match()`、`pipeline.analyze()`、約 10 個測試；適合和 12 月的 `service.py` 一起做 |
| 結果可以依（profile, 技能）快取 | 判斷量估計 1.5–2 倍（20 份時約 300 次 → 約 570 次），每次分析估計從 US$0.02 變成 US$0.04 以內 |
| 網頁版本來就沒有 `original_text`，沒有損失 | CLI 會失去每份職缺的上下文；一個判斷錯會套用到所有職缺（但一致、看得到、可以用 alias 修） |

**建議：** 這週做 A，用 S1 的資料量前後差異。A 之後如果 `status_flip_rate` 還 > 0.10，或 `top5_overlap_runs` < 4，12 月做 B。不建議「每份職缺各投票 3 次」：費用 3 倍，而且跨職缺不一致還在。

---

## 4. 樣本是否足夠

**先講限制：** 現在命名切碎（73% 的技能只出現一次），「新技能出現數」量到的大多是新名稱，不是新需求。所以先整理同義詞表，再看下面的數字；每次整理完重算一次。

用**需求分數**（`demand` = 必備 2 分、加分 1 分）排名，不用缺口排名：需求分數不看 profile、不經過比對的 LLM，只反映樣本本身，而且只讀 `samples/jobs.json`、不花錢。

### 4.1 每加 10 份檢查一次

| 指標 | 公式 | 現況（10 → 20 份，或 20 份） |
|---|---|---|
| `demand_top10_churn` | 加這一批之前和之後，需求前 10 名換了幾個 | 2 |
| `seen_before_rate` | 這一批的技能項目中，名稱在舊樣本出現過的比例（全部／只算必備） | 0.48／0.56 |
| `new_recurring_skills` | 這一批帶進來的新技能中，在這一批出現 2 份以上的數量 | 9（新技能共 97 個） |
| `demand_bootstrap_top10` | 把職缺有放回地重抽 1000 次，每次的前 10 名和全樣本前 10 名的重疊數，取平均和第 10 百分位 | 平均 7.4、第 10 百分位 6 |
| 同上，每個職務類別 | 只用那一類的職缺 | ai-app（7 份）7.0、ml（8 份）7.2、ai-product（5 份）5.0 |
| `boundary_gap` | 第 10 名和第 11 名的需求分數差 | 第 8–10 名都是 12 分，第 11 名 10 分 |

為什麼比你原本的兩個指標多：

- 「新技能出現數」永遠不會趨近 0（長尾＋命名），所以改看 `seen_before_rate` 和 `new_recurring_skills`：新出現、而且不只一份職缺要的，才是樣本漏掉的需求。
- 「前 10 名變動數」會受這一批剛好收到哪一類影響（11–20 號的 ai-app、ai-product 比例比前 10 份高）。bootstrap 不看收集順序，比較能代表「再收一批類似的，會差多少」。
- 第 8–10 名同分時，換名次不代表市場變了。同分的當成同一名看。

單一技能的比例也附區間：某技能在 20 份中 10 份必備，95% 區間約 30%–70%；60 份中 30 份，約 38%–62%。每類 20 份時，仍然分不出「50%」和「35%」的技能誰比較重要。

### 4.2 停止收集的標準

連續兩批（20 份）都滿足，就停：

| 條件 | 全部樣本 | 每個職務類別 |
|---|---|---|
| `demand_top10_churn` | ≤ 1 | ≤ 2 |
| `demand_bootstrap_top10` 平均 | ≥ 8.5 | ≥ 7.5 |
| `seen_before_rate`（必備） | ≥ 0.75 | — |

另外設硬上限 60 份。到 60 份還沒滿足也停：再手動收的時間不划算，改成在畫面上誠實標示（每類的樣本數；樣本少的類別只顯示前 5 名）。同一家公司最多 2 份的限制維持，代表性比數量重要。

---

## 5. fit_score 的合理性

`fit_score =（有 + 0.5 × 部分）÷ 必備數`，只看必備；`role_fit` = 每個職務類別的平均。

### 5.1 檢查步驟

1. **先盲評（G4）**，再看程式的排名。
2. **比較：** `spearman_fit`、`top5_overlap_human`。
3. **看最大分歧：** 列出「程式名次和我的名次差最多」的 3 份職缺，逐份看原因。數字只告訴你合不合，原因才告訴你該改什麼。常見原因：必備標太多（分母被灌大）、相關技能被算 0.5、年資、加分技能被忽略。
4. **不用標準答案的結構檢查：**
   - `denominator_bias`：必備數多的職缺是不是系統性偏低。01、04、20 是 100% 必備，分母可能偏大。
   - `required_sensitivity`：把所有項目都當必備重算，排名變很多的話，代表排名主要由「必備／加分」的標記決定，而那正是拆解最不穩的輸出，要先修拆解。
   - 必備數少的職缺分數很粗：05 號只有 7 個必備，一個技能有或沒有就差 0.14。必備數 < 5 的職缺在畫面上標「必備項目少，分數僅供參考」。

### 5.2 要不要調權重、加入加分技能

用 S1 存下來的結果離線比較，不花錢：

| 版本 | 公式 | 想驗證什麼 |
|---|---|---|
| V0（現在） | （有 + 0.5 × 部分）÷ 必備數 | 基準 |
| V1 加入加分 | （必備得分 + 0.5 × 加分得分）÷（必備數 + 0.5 × 加分數） | 對「必備／加分」標錯比較不敏感 |
| V2 拆開「部分」 | 同一個技能但沒作品 = 0.5；相關技能（`how == "related"`）= 0.25 | 「會但沒作品」和「只會隔壁的」是不是該不同 |
| V3 依市場加權 | 每個必備技能的權重 = 它在樣本中的需求比例 | 核心技能是不是該比冷門技能重要 |

**建議：先不改，維持 V0。** 理由：

- 只有 20 份職缺、一個評分者，為了多對幾名去調權重很容易調過頭。
- 換版本的條件要先講好：ρ 提高 ≥ 0.15、`top5_overlap_human` 多 1 份以上、而且公式一句話講得清楚。三個都成立才換，一次只換一個。
- 加分技能不併進分數，改成在旁邊多顯示一欄（「加分 3/5」）。例外：如果 G1 量出來 `over_required_rate` > 0.10，V1 會比較穩，那時再考慮。
- 年資不進公式，只顯示。`skill_fit` 和 `overall_fit` 的差距如果很大，再決定要不要加「年資不符」的標記。
- 到 60 份時，隨機抽 20 份新的補評分，確認結論沒變。

### 5.3 role_fit

每類只有 5–8 份，平均值的誤差不小。規則：最高和第二高的類別平均差 ≥ 0.10，而且 `role_fit_agreement` ≥ 0.9，才說「最接近 X」；否則說「X 和 Y 接近」。每類的份數和平均一起顯示。

---

## 6. 評估程式的結構

### 6.1 資料夾

```
job-gap-agent/
├── evals/                    ← 新增；和 job_agent/ 平行，不進 Docker image
│   ├── metrics.py            ← 純函式，不呼叫 LLM、不讀檔
│   ├── gold.py               ← 標準答案的 Pydantic 格式＋讀檔檢查
│   ├── samples_eval.py       ← L0：只讀 samples/jobs.json
│   ├── extract_eval.py       ← L0＋L1：拆解
│   ├── match_eval.py         ← L1：比對
│   ├── stability.py          ← L2：S1／S2／S3；--rescore 離線重算
│   ├── resume_eval.py        ← L1：包 compare_profiles
│   ├── fit_eval.py           ← fit_score 和我的評分
│   ├── role_eval.py          ← 2 月：LLM 分類 role
│   ├── __main__.py           ← python -m evals --quick｜--full
│   ├── examples/             ← 公開的假標準答案（配 profile.example.yaml，測試用）
│   ├── reviewed.yaml         ← 看過、確認沒問題的例外（例如某份真的 100% 必備）
│   └── history.jsonl         ← 公開：每次評估一行，只有數字
├── gold/                     ← 新增，.gitignore：G1–G4
├── reports/eval/<時間>_<名稱>/   ← 已在 .gitignore：原始結果和逐項明細
└── tests/test_evals_*.py     ← 用假的 Claude
```

放在頂層 `evals/` 而不是 `job_agent/` 裡：12 月的 Dockerfile 只複製明確列出的資料夾，評估程式自然不會進 image。

第一輪只需要 `metrics.py`、`samples_eval.py`、`extract_eval.py`、`stability.py`（只做 S1）。其他等用到再寫。

### 6.2 腳本

| 腳本 | 做什麼 | 輸入 | 輸出 | 費用 |
|---|---|---|---|---|
| `samples_eval.py` | 必備比例、`soft_drop_conflicts`、`singleton_rate`、同義詞候選清單、需求前 10、bootstrap、每 10 份的變化 | `samples/jobs.json`、`skill_synonyms.yaml` | Markdown＋數字 | US$0 |
| `extract_eval.py` | `quote_grounding_rate`、`required_lint`；`--align NN` 對齊；有 G1 就算 precision／recall／required | `jobs/`、`cache/`、`gold/extract/` | 明細＋數字 | US$0 |
| `stability.py match --n 5` | S1：重跑、存 `runs.json`、算穩定度 | `samples/jobs.json`、`profile.yaml` | `runs.json`＋數字 | US$0.10 |
| `stability.py --rescore <資料夾>` | 用存下來的結果比較規則（新舊 `priority`、`fit_score` 各版本） | `runs.json` | 對照表 | US$0 |
| `stability.py extract --n 3` | S2 | `jobs/`（固定 20 份）、`profile.yaml` | 同上 | US$0.21 |
| `stability.py resume --n 5` | S3 | 履歷、`profile.yaml` | 同上 | US$0.012 |
| `match_eval.py` | 對 G2 的 `relation_accuracy`、`status_accuracy`、`false_have_count`、混淆矩陣 | `runs.json`、`gold/match.csv`、`profile.yaml` | 明細＋數字 | US$0 |
| `resume_eval.py` | 把 `compare_profiles` 的結果變成 1.3 節的數字 | 草稿、`profile.yaml`、G3 | 數字 | US$0 |
| `fit_eval.py` | 第 5 節全部 | `runs.json`、`gold/fit.csv` | 對照表 | US$0 |
| `python -m evals --full` | 照順序全部跑；先印估計費用；最後在 `history.jsonl` 加一行 | 以上全部 | `summary.md` | 約 US$0.32 |

**結果存哪裡：**

| 位置 | 內容 | 公開 |
|---|---|---|
| `reports/eval/<時間>_<名稱>/runs.json` | 每次重跑的原始 `MatchResult`、拆解結果 | 否 |
| `reports/eval/<時間>_<名稱>/details.md` | 逐項明細（哪個技能翻了、錯在哪） | 否 |
| `evals/history.jsonl` | 日期、commit、模型、三個 `SYSTEM` 的指紋、份數、N、費用、所有指標 | 是 |
| `docs/evaluation.md` 或 README | 最新一輪的摘要表（面試用） | 是 |

`history.jsonl` 記 `SYSTEM` 的指紋，是為了之後能回答「哪一次改 prompt 讓哪個數字變了」。

### 6.3 哪些進 CI

| 進 CI（假的 Claude，不花錢） | 測什麼 |
|---|---|
| `tests/test_evals_metrics.py` | `metrics.py` 每個函式，用手算得出答案的小例子（含同分、空集合） |
| `tests/test_evals_gold.py` | `evals/examples/` 的格式能通過；`role` 寫錯、`accept_relation` 寫錯會被擋 |
| `tests/test_evals_stability.py` | 假 Claude 每次回不同答案（寫法同 `test_compare.py` 的 `ByModel`）→ `status_flip_rate`、`top10_overlap` 算對；確認不會寫進 `cache/` |
| `tests/test_evals_extract.py` | `align()` 的對上、多抓、漏抓、`split`、`ignore`；`grounded()` 對空白和全形的處理 |
| `tests/test_samples_quality.py` | 對公開的 `samples/jobs.json`：每份必備 ≥ 3；100% 必備的職缺要在 `reviewed.yaml`；`soft_drop_conflicts` = 0。現在會失敗（01、04、20 還沒看過；待決定 3 還沒修），第一輪做完再加進 CI |
| CI 多一個不擋 merge 的步驟 | `python -m evals.samples_eval` 把樣本檢查表印到 job summary |

| 只在本機手動跑 | 原因 |
|---|---|
| `stability.py`（S1–S3）、`role_eval.py` | 會花錢 |
| `extract_eval.py`、`match_eval.py`、`resume_eval.py`、`fit_eval.py` | 要用私人的 `jobs/`、`gold/`、`profile.yaml`、履歷 |

12 月的「測試中不准建立真的 `Anthropic()`」fixture 也會涵蓋 `tests/test_evals_*`。

### 6.4 程式骨架（不是完整實作）

```python
# evals/metrics.py：純函式
def precision_recall(n_tp: int, n_pred: int, n_gold: int) -> dict: ...
def jaccard(a: set, b: set) -> float: ...
def overlap_at_k(a: list, b: list, k: int = 10) -> int: ...
def mean_pairwise(items: list, fn) -> float: ...            # N 次重跑的所有配對取平均
def flip_rate(labels: dict[tuple, list[str]]) -> float: ... # N 次不完全相同的比例
def kendall_tau(x: list[float], y: list[float]) -> float: ...   # τ-b，處理同分；自己寫，不加套件
def spearman(x, y) -> float: ...     # statistics.correlation(x, y, method="ranked")，Python 3.12+
def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]: ...
```

```python
# evals/stability.py：S1（寫法照 compare.py：算的函式回傳 dict，to_markdown 回字串，main 負責讀寫檔）
def rerun_match(profile, samples, n, client=None) -> list[list[Run]]:
    """拆解固定，比對重跑 n 次。"""
    return [analyze(profile, samples, client=client)[0] for _ in range(n)]

def match_stability(all_runs: list[list[Run]], priority_fn=priority) -> dict:
    statuses: dict[tuple, list[str]] = {}          # (job_id, key(skill)) → 每次的狀態
    for runs in all_runs:
        for run in runs:
            for m in run.results:
                if m.how != "name":                # 名稱對上的不會變，不算
                    statuses.setdefault((run.job_id, key(m.skill)), []).append(m.status)
    tops = [ranked_gaps(aggregate(runs), priority_fn) for runs in all_runs]   # 用指定的規則重排
    return {"status_flip_rate": flip_rate(statuses),
            "top10_overlap": mean_pairwise(tops, overlap_at_k),
            ...}

def priority_sum(stat: SkillStat) -> int: ...      # 方案 A；--rescore 時和現在的 priority 並排比較
```

```python
# evals/extract_eval.py
def grounded(r: Requirement, body: str) -> bool:
    """original_text 去掉空白、全形轉半形後，是不是職缺原文的子字串。"""

PREFERRED = r"加分|者佳|尤佳|優先|plus|preferred|nice to have|bonus"
OVERVIEW = r"涵蓋技術|有機會接觸|你將接觸|可以學到|tech stack"
def required_lint(job: JobRequirements, body: str) -> list[dict]:
    """找出可疑的 required：標必備但所在的行或上方標題符合 PREFERRED／OVERVIEW；
    或標加分但在「條件要求／必備」標題下。只是列出來給人看，不是指標。"""

def align(pred: list[Requirement], gold: GoldJob, synonyms) -> dict:
    """和 compare_profiles 同一個做法：用 key(canonical()) 對齊，gold 的 accept 也算。
    回傳 tp（模型, 標準）、fp、fn、split；ignore 裡的不算。"""
```

`required_lint` 取代現在「抽查必備數／總數」的做法：不是只看比例異常的職缺，而是每一份都掃，直接指出哪幾個項目可疑。

同義詞候選清單也是輔助工具：用「一個名稱的字是另一個的子集」或字串相似度 ≥ 0.85 找候選（現在會列出 46 對，裡面有誤報，例如 `LLM`／`vLLM`），由你決定哪些寫進 `skill_synonyms.yaml`。純字串比不出 `MCP`／`Model Context Protocol` 這種，可以另外用一次 Haiku 呼叫請它把名稱分組（約 US$0.002），一樣由你核准後才寫進表。

### 6.5 改東西時怎麼把關

| 改什麼 | 跑什麼 | 費用 |
|---|---|---|
| 程式規則（`priority`、`fit_score`、同義詞表、`drop_soft`） | `stability.py --rescore`＋`samples_eval.py` | US$0 |
| `match.SYSTEM` 或比對模型（Haiku） | S1（N=5）＋`match_eval.py` | US$0.10 |
| `extract.SYSTEM` | 全部重拆（指紋變了本來就要）＋`extract_eval.py`＋S2 | 約 US$0.26–0.36 |
| `resume.SYSTEM` | S3＋`resume_eval.py` | US$0.012 |
| 換成 Sonnet（約 20 倍） | 只在 G1 的 8 份上跑 N=2 | 約 US$0.32 |

接受改動的條件，三個都要成立：

1. 安全指標沒有變差：`fabricated_count`、`false_have_count`、`inflation_count` 維持 0。
2. 目標指標的改善超過雜訊：新數字落在改動前 N 次重跑的最小–最大範圍之外。
3. 費用在預算內。

結果寫進 `history.jsonl`，決定寫進 `design.md` 第 15 節。

---

## 7. 第一輪：這週 3 小時

假設：腳本另外寫（下一次對話給你檔案），這 3 小時是你標註、執行、看結果的時間。花費 US$0.10。

| 時間 | 做什麼 | 能回答什麼 |
|---|---|---|
| 0:00–0:15 | **G4 盲評**：20 份職缺各打 `skill_fit`、`overall_fit`。一定要最先做 | — |
| 0:15–0:45 | L0：`samples_eval.py`＋`extract_eval.py`（`quote_grounding_rate`、`required_lint`）；人工看 01、04、20 和被標出來的項目 | 還有沒有 GARMIN 那一類的錯？模型引用的原文都存在嗎？ |
| 0:45–1:15 | S1：比對重跑 N=5（US$0.10）；`--rescore` 比較現在的規則和方案 A；`fit_eval.py` | 比對到底多不穩？方案 A 讓前 10 名穩多少？`fit_score` 排名合不合我的直覺、會不會跳？ |
| 1:15–2:15 | G1 開發組 4 份（15、01、20、05）：盲標 → 對齊 → 數字 | 拆解的 precision／recall 第一次有數字；必備標錯的比例和方向 |
| 2:15–2:40 | 看同義詞候選清單 → 補 `skill_synonyms.yaml` → 重跑 `samples_eval.py` | 命名切碎讓前 10 名失真多少？整理後 `singleton_rate` 降多少？ |
| 2:40–3:00 | 把數字填進摘要表、`history.jsonl`；對照門檻決定下一步 | 下一步先修拆解規則、比對規則，還是繼續收職缺 |

第一輪不做：G2（比對的準確率）、S2、S3、G1 測試組、role 分類。這些放第二輪（約 2.5 小時、US$0.22）。

時間不夠時的刪減順序：先刪 2:15 的同義詞整理，再把 G1 減到 2 份（15、01）。G4 盲評和 S1 不能刪。

---

## 8. 面試講法

標〔 〕的數字第一輪量完再填；其他是現在已經有的。

1. 這個專案有 3 個 LLM 步驟，我把評估分三層：不花錢的程式檢查放進 CI、對人工標準答案的準確率、同樣輸入重跑的穩定度；一輪完整評估不到 1 美元。
2. 標準答案是我自己標的，量小但有設計：8 份職缺逐項標，先不看模型輸出盲標、再對齊；一半用來調 prompt，另一半只看數字。全部標完不到 3 小時。
3. 拆解我量 precision〔 〕、recall〔 〕和「必備」標記的準確率〔 〕；另外用程式檢查模型引用的原文是不是真的出現在職缺裡。
4. 比對我最在意的不是準確率，而是「誤判成有」要是 0：這個工具是找缺口的，寧可保守。
5. 排名會跳不只是模型的問題。原本的規則是先對各份職缺的判斷取多數、再乘權重，剛好平手時一次翻轉會讓優先度加倍。我改成每份職缺各算一次再加總，並用同一批存下來的結果離線比較，前 10 名重疊從〔 〕變成〔 〕，沒有多花錢。
6. 我也發現拆解的命名把需求切碎了：20 份職缺有 211 個技能名稱，73% 只出現一次；光合併兩組同義詞，需求前 10 名就換了 2 個。所以順序是先修命名，再談樣本夠不夠。
7. 樣本量我用 bootstrap 看前 10 名穩不穩，而不是憑感覺：20 份時平均重疊 7.4／10，所以繼續收到〔 〕份，畫面上也標出每一類的樣本數。
8. 之後每次改 prompt、換模型或改規則都跑同一套評估，變化要超過重跑本身的雜訊才算進步，結果記在 repo 裡。

---

## 9. 待決定

| # | 要決定的事 | 我的建議 | 什麼時候要定 |
|---|---|---|---|
| 1 | main 上沒有 `rank_jobs`／`role_fit` 的程式，`fit_score` 只在 `design-dec-jan.md` 的公式裡 | 如果在你本機，先 push。這份的第 5 節照文件的公式寫（只看必備；同分時必備缺口少的在前，再依職缺編號）。實作不同的話告訴我 | 第一輪前 |
| 2 | 平手規則 | 現在做方案 A（改 `priority()`）；12 月依 S1 的數字決定要不要做方案 B | A：這週；B：12 月 D1 |
| 3 | `drop_soft` 會因為模型把 `category` 標成 `soft` 而丟掉 `Requirement Analysis`、`Technical Writing` | 改成只看 `SOFT_SKILLS` 名單。`category` 是模型每次可能不同的標籤（61 個多次出現的技能有 17 個類別不一致），不適合拿來決定去留。這也關係到你原本的疑問「Requirement Analysis 是不是真的缺口」 | 這週 |
| 4 | 命名切碎怎麼修 | 先擴充 `skill_synonyms.yaml`（程式查表，符合現有原則），每加 10 份整理一次，約 15 分鐘。不建議現在把技能名單放進 `extract.SYSTEM`：名單一改，所有職缺都要重拆 | 這週 |
| 5 | 「必備」要不要改成由程式決定 | 等 G1 的數字。`over_required_rate` > 0.10 才做：`Requirement` 加 `section` 欄位（qualification／duty／overview／preferred），模型只回答出自哪一段，`required` 由程式規則決定。代價是全部重拆一次 | G1 開發組量完 |
| 6 | 標準答案放哪 | `gold/` 加進 `.gitignore`；公開的只有 `evals/history.jsonl` 的數字和 `evals/examples/` 的假資料 | 這週 |
| 7 | G1 標幾份 | 8 份：這週 4 份（開發組），下週 4 份（測試組）。少於 4 份時區間太寬，數字沒有參考價值 | 這週 |
| 8 | 重跑幾次 | 平常 N=5；建立基準時 S1 跑一次 N=10 | 第一輪 |
| 9 | S1 用哪種條件跑 | 網頁版的條件（沒有 `original_text`）。CLI 條件第二輪另外比一次，順便知道原文對判斷有沒有幫助 | 第一輪 |
| 10 | `fit_score` 要不要調 | 先不調；加分技能另外顯示一欄，不併進分數；年資只顯示。有 G4 之後照 5.2 節的條件決定 | 第一輪後 |
| 11 | `role_fit` 什麼時候敢說「最接近」 | 最高和第二高差 ≥ 0.10、而且重跑一致 ≥ 0.9；否則顯示「接近」 | 寫 `role_fit` 畫面時 |
| 12 | 門檻 | 第一輪量完基準後校準一次，之後固定。門檻比基準嚴的項目列成待改善，不要為了通過而放寬 | 第一輪後 |
| 13 | 停止收集職缺的標準 | 4.2 節的三個條件連續兩批成立，或到 60 份 | 30 份時 |
| 14 | Sonnet 比較要不要放進每一輪 | 不放。要比的時候只在 G1 的 8 份上跑（約 US$0.32） | 需要時 |
| 15 | LLM 分類 role 的輸入用什麼 | 2 月再定。使用者貼的職缺有原文，用原文；門檻照 2.4 節 | 2 月 F1 |