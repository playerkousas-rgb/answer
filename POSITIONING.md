# 定位 (Positioning)

> 呢個檔案係成個 app 嘅定位說明。**先讀呢份，再讀其他。**
> 所有設計決定都可以追溯返呢度。

---

## 一句講晒

**把香港童軍總會已公開嘅文件，整理成 AI 可以直接讀取嘅格式。**

唔係一個問答系統。唔係一個官方渠道。唔係一份新嘅規則文件。
係一個**格式轉換層** —— 同一批公開檔案，換成 AI 讀得準嘅樣子。

---

## 為咩要做

### 現況

一個童軍領袖想知「樂行童軍幾多歲要退」。佢有三條路：

| 路徑 | 結果 |
|---|---|
| 自己去總會網站搵 | 要搵到目錄頁 → 開 105 頁 PDF → Ctrl+F。多數人唔會做。 |
| 問 AI | AI 好可能答「二十六歲」—— **錯**。現行係二十五歲。 |
| 問其他領袖 | 對方都係問 AI，或者記憶停留在舊版。 |

第二條路徑係問題核心：**而家多數人只問 AI，唔搵原文。** 而 AI 答錯。

### AI 點解會答錯

唔係模型蠢。係三個**已實測**嘅技術事實：

#### 1. 舊版 PDF 冇下架

```
官方目錄頁 /tc/scouting/policy-guidelines/por/index.html
  列出嘅 PDF：POR_C_20260917.pdf          ← 只有一份

por_c_20220908_1.pdf   HTTP 200   1,507,231 bytes   Last-Modified 2022-09-15
por_c_20260917.pdf     HTTP 200   1,558,349 bytes   Last-Modified 2026-09-18
```

**兩份都開到。** 舊版唔喺目錄頁，但 URL 仍然 live、仍然係官方域名、
仍然被搜尋引擎索引緊 4 年。

所以「叫 AI 去 scout.org.hk 查」嘅結果，取決於 AI 行邊條路：
行目錄頁 → 現行版 ✅；行 web search → 好可能係舊版 ❌。
**你控制唔到 AI 行邊條。**

#### 2. 新舊兩版嘅標籤一模一樣

```
舊版 2022-09    首 5 頁「版」字樣：['2010 年版', '2010 版']
現行 2026-09    首 5 頁「版」字樣：['2010 年版', '2010 版']
```

即係話：**就算 AI 兩份都搵到、都讀晒封面，佢都分唔到邊份現行。**

PDF metadata 更加幫唔到手 —— 現行版仲差過舊版：

| | 舊版 2022 | 現行 2026 |
|---|---|---|
| `title` | 香港童軍總會「政策、組織及規條」 | **`PREFACE`** |
| `author` | 香港童軍總會 | **`ADMCO4`** |
| `producer` | Adobe PDF Library 17.11.238 | Microsoft® Word 2019 |

AI 如果靠 metadata 判斷，會揀錯。

**唯一可靠嘅判別方式係 sha256 / Last-Modified / 官方目錄頁。**

#### 3. Naive PDF 抽取會產生幻覺規條

實測通告 ACR20/2026（POR 修訂通告，1 頁）：

| 抽取方式 | 抽出嘅規條編號 |
|---|---|
| 通用 PDF reader（AI 內置嗰個） | `2.3`, **`2.3.41`**, **`2.3.51`**, `6.3`, **`6.3.22`** |
| span-level 過濾（本站用嘅） | `2.3`, `2.3.4`, `2.3.5`, `6.3`, `6.3.2` |

核對現行 POR：

```
「規條 2.3.41」存在? ❌ 冇 — 幻覺規條
「規條 2.3.51」存在? ❌ 冇 — 幻覺規條
「規條 6.3.22」存在? ❌ 冇 — 幻覺規條
```

原因：原文 `' 2.3.4'` 係 font size **12.0**，後面黏住嘅 `'1'` 係 size **8.0** 嘅
**腳註上標**。任何唔睇 font size 嘅抽取器都會合成出 `2.3.41`。

然後 AI 會信心十足咁答：「根據規條 2.3.41…」—— 而呢條規條根本唔存在。
**呢個問題影響所有通用 PDF reader，包括 ChatGPT / Claude 內置嗰個。**

### 後果

兩版之間有 **5 項已核實嘅實質差異**，全部係各支部年齡規限：

| 規條 | 支部 | 舊版（仍 live） | 現行 |
|---|---|---|---|
| 2.3.3 | 小童軍團 | 足五歲起至足八歲止 | 四歲至七歲 |
| 2.3.4 | 幼童軍團 | 足七歲半至足十二歲止 | 六歲至十一歲 |
| 2.3.5 | 童軍團 | 足十一歲起至足十六歲止 | 十一歲至十五歲 |
| 2.3.6 | 深資童軍團 | 足十五歲起至足二十一歲止 | 十五歲至二十歲 |
| 2.3.7 | 樂行童軍團 | 足十八歲起至足二十六歲止 | 十八歲至二十五歲 |

呢啲唔係瑣碎差異 —— 係**收唔收人、幾時要退**嘅實際決定。

---

## 為邊個做

### 主要消費者：AI

呢個 app 嘅**主要讀者唔係人，係 AI agent**。

所以所有輸出都係：
- 純 Markdown / JSON —— 冇 auth、冇 rate limit、冇 backend
- 每條規條一個**穩定 URL** —— `por/<rule_id>.md`
- 每個 passage **自帶完整出處** —— rule_id、版本、sha256、檔案日期、官方 PDF 連結
- `llms.txt` / `index.json` / `llms-full.txt` —— AI 發現同載入用

### 次要消費者：人

人用嘅係 Vercel 前端，功能只有三個：
1. **關鍵字搜尋** —— client-side，冇 server
2. **接入自己嘅 AI** —— 揀 provider、貼自己嘅 API key，瀏覽器直連
3. **版本核對** —— 貼一個 scout.org.hk PDF URL，即刻知係現行定舊版

### 第三類消費者：用戶自己嘅 AI agent

`skill/hk-scout-kb/` 係一個 [agentskills.io](https://agentskills.io) 開放標準 skill，
40+ agent 產品通用。用戶 `cp -r` 就裝到，唔使經呢個網站。

---

## 三層架構

```
corpus/*.md   ← 唯一需要手動維護嘅地方（single source of truth）
     │
     ├── build_site.py  ──► por/*.md · md/*.md · llms.txt · index.json
     │                      web/{index.html, app.js, api/*.json}
     │                           │
     │                           ├──► GitHub repo   ← AI 讀 raw.githubusercontent.com
     │                           └──► Vercel        ← 人用前端
     │
     └── build_skill.py ──► skill/hk-scout-kb/     ← 用戶自己嘅 agent 裝
```

| | GitHub repo | Vercel | Agent Skill |
|---|---|---|---|
| 俾邊個 | AI | 人 | 用戶自己嘅 agent |
| 讀乜 | `por/*.md` `llms.txt` `index.json` | `web/index.html` | `scripts/search_kb.py` |
| 需要 server | ❌ | ❌（純靜態） | ❌ |
| 需要 API key | ❌ | 用戶自帶，只喺 localStorage | ❌ |

**成個系統冇 backend。** 呢個係刻意嘅：
- 冇 uptime 責任
- 冇 key 保管責任
- 冇 rate limit 要處理
- 冇 serverless 冷啟動
- deploy = push

---

## 設計原則

### 1. AI 係主要消費者 → **唔拒答、唔分級、唔落命令**

早期版本做過呢啲，全部係錯嘅方向：

| 做過 | 點解錯 |
|---|---|
| 領域外問題直接拒答 | 剝削 AI 判斷能力。佢可能有充分理由要睇候選。 |
| `role: answer \| related` 分級 | 我憑乜決定邊個係「答案」？AI 睇到原文自己會判斷。 |
| `instruction_to_model` 欄位 | 對 AI 落命令。佢唔係我嘅下屬。 |
| 為人類排版嘅輸出 | 主要消費者唔係人。 |

而家嘅做法：**返回齊全候選 + 中立 signals**。

```json
"signals": {
  "lexical_coverage": { "segment_ratio": 0.0, "segments_uncovered": ["世界童軍總部"] },
  "weak_match":       { "top_score": 21.6, "meaning": "相關度好低…由你判斷" },
  "section_conflict": { "topic_rules": [...], "clarify": "用戶冇指明邊個支部" },
  "version_warning":  { "rules": ["2.3.4"], "meaning": "有後續通告修訂" },
  "topic_not_in_corpus": { "reason": "…" }
}
```

Signals 係**描述事實**，唔係指令。AI 自己決定答唔答、點答。

### 2. 只陳列事實，唔做價值判斷

❌ 「總會放咗舊版落去，令人混淆」
❌ 「官方網站會俾錯資料」
✅ 「官方目錄頁而家列緊 `por_c_20260917.pdf`。`por_c_20220908_1.pdf` 唔喺清單入面，但 URL 仍然回傳 HTTP 200。」

**總會冇做錯任何嘢。** 佢個網站係設計俾人用瀏覽器睇嘅，設計得好。
本站只係補一個 AI 讀得到嘅格式。

呢條線要守住 —— 無論喺 UI、`llms.txt`、skill、定係同用戶解釋嘅時候。

### 3. `corpus/` 係 single source of truth

其他所有嘢都係 generated，可全量重建。

**手寫檔案唔應該被 build 覆蓋。**（教訓：之前 build 會生成 `REPO_README.md`
同 `.gitignore`，把我手寫嘅版本蓋走；我 `rm` 咗佢，下次 build 又生出返嚟。）

### 4. 穩定 URL

`por/<rule_id>.md` 永遠指向嗰條規條。唔好用日期、唔好用 hash、唔好用序號做 URL。

### 5. web 同 skill 必須行為一致

同一個 tokenizer、同一套 alias、同一套 signals。

（教訓：曾經 `web` 用 jieba、`skill` 用 bigram → 同一條問題兩個交付物回唔同答案，
實測 R 65% vs 83%。`static_eval.py` 而家同時測兩邊就係為咗呢個。）

### 6. 寧可知識庫舊三日，都唔好俾佢錯

Build 有硬性保護：
- `status: superseded` / `qa_passed: false` / `sensitivity` 唔係 `public` → 跳過
- `rule_id` 撞車或格式非法 → **build 直接 fail 並指名道姓**
- generated 檔案數目同 report 唔符 → fail

（教訓：曾經 332 個 MD 全部寫去同一個檔名 `###.md` 互相覆蓋，剩返一個 1,114 bytes
嘅廢檔，而 build 照樣 report「rules: 332 ✅」。**唔好信自己嘅 counter，要數實際檔案。**）

---

## 刻意唔做嘅嘢

| 唔做 | 原因 |
|---|---|
| Backend / server function | 冇必要。GitHub raw + Vercel static 夠晒。 |
| 保管用戶 API key | 責任太大。key 只喺用戶瀏覽器嘅 localStorage。 |
| 生成 prompt 俾用戶貼 | 用戶直接連去 Git 睇 MD 就得。（copy mode 只係 fallback） |
| 向量嵌入 / semantic search | BM25 + alias router 實測已經 100%。加 embedding 只會加複雜度同成本。 |
| 拒答守衛 | 見原則 1。 |
| 來源自動探測 / 爬蟲 | 用戶自己知道資料喺邊、會自己提供。 |
| 審計總會嘅文件 | 唔係本站嘅目的。 |
| 多語言 | 原文係繁中，就係繁中。 |

---

## 用戶三條路徑

### A. 完全唔想裝嘢嘅人

用 Vercel 前端。關鍵字搜尋，或者貼 prompt 去自己慣用嘅 AI。

### B. 有自己 AI agent 嘅人

裝 skill：
```bash
cp -r <repo>/skill/hk-scout-kb ~/.claude/skills/    # Claude Code
cp -r <repo>/skill/hk-scout-kb ~/.agents/skills/    # OpenAI Codex
cp -r <repo>/skill/hk-scout-kb .github/skills/      # VS Code Copilot
```

### C. AI 自己

```
https://raw.githubusercontent.com/<OWNER>/<REPO>/main/llms.txt
```

或者由人叫佢：
```
請先讀 https://raw.githubusercontent.com/<OWNER>/<REPO>/main/llms.txt
了解呢個資料庫嘅結構，之後所有童軍規則問題都根據呢度嘅原文回答，
並附規條編號同官方原文連結。
```

---

## ⚠️ 一個要誠實面對嘅限制

**AI 唔會自己搵到呢個站。**

- `llms.txt`：Google 嘅 John Mueller 明確講過冇任何 Google Search 系統讀佢。
  Ahrefs 研究 38,000 域名，97% 於 2026-05 收到 **0 次** AI crawler 請求。
- Google 索引 `por/*.md`：有效，但要時間。
- 早期：**要靠人主動告知**（上面路徑 C 嗰句）。

所以呢個站嘅早期增長係**社交問題，唔係技術問題**。架構做得再好，
冇人知道就冇人用。呢一點要如實講，唔好假設「起好就會有人嚟」。

---

## 目前狀態

**架構完成，內容待加入。**

- `corpus/` 係空嘅
- `web/` 已生成（shell mode）—— **可以直接 deploy 去 Vercel**
- 前端會顯示「未收錄任何文件 · 架構已部署，內容待加入」
- `api/manifest.json` 有 `"mode": "shell"`
- `llms.txt` 講明未有內容，但列明結構

### 已建成並通過測試嘅

| | |
|---|---|
| `pipeline/build_site.py` | corpus → web/ + por/ + llms.txt + index.json（含 shell mode） |
| `pipeline/build_skill.py` | corpus → Agent Skill（含 agentskills.io 規格驗證） |
| `pipeline/static_eval.py` | 測試閘門，**同時測 web 同 skill** |
| `pipeline/router.py` | 查詢理解（alias / 規條編號 / section / generic topic） |
| `app/app.js` | client-side BM25 + router + signals，零 server |
| `skill/hk-scout-kb/` | Agent Skill，`scripts/` **零 third-party 依賴** |
| `.github/workflows/build.yml` | push corpus/ → 自動 rebuild + 測試 + commit |

### 用一份 sample POR 測出嘅數字（**唔代表你嘅內容**）

| 指標 | web | skill |
|---|---|---|
| Canary | 10/10 | 10/10 |
| R 檢索命中 | 100% | 100% |
| G top-1 含答案 | 100% | 100% |
| L 冇舊版滲漏 | 100% | 100% |
| OOD 有 signal | 3/3 | 3/3 |
| 版本陷阱 | 5/5 | 5/5 |

> ⚠️ 測試題只有 23 條，而且係開發者自己寫嘅 —— 分佈唔代表真實用戶。
> 呢啲數字係**回歸保護**（防止改壞），唔係準確度保證。

---

## 免責聲明

> 本網站為非官方獨立工具，旨在方便成員瀏覽公開資訊，與香港童軍總會無任何官方從屬關係。
> 所有通告版權歸香港童軍總會所有。

另外三句，要出現喺每個輸出：
1. 內容由公開檔案自動整理而成，可能有轉換錯誤。
2. 每項資料均附官方原文連結，請於作出任何決定前查閱原文。
3. 一切以香港童軍總會最新公佈為準。

### 內容責任

財務／審計、意外及保險、保護童軍成員免受傷害、紀律及糾紛、成員個人資料 ——
呢啲範疇**只可以列原文**，唔可以生成建議或判斷，並要加：
「此類事宜請直接聯絡相關署或區總監。」

原因：呢啲範疇錯誤嘅後果最嚴重，而且往往涉及個案判斷。

---

## 相關文件

| 檔案 | 乜嘢 |
|---|---|
| `README.md` | repo 入口（AI 同人都会讀） |
| `DEPLOY.md` | 部署指引 + 驗證清單 + CORS 對照表 |
| `AGENTS.md` | 俾 AI agent 嘅入口 |
| `corpus/README.md` | 點樣放 MD（frontmatter 欄位說明） |
| `skill/README.md` | Agent Skill 安裝／設計／安全 |
| `docs/_archive/FEASIBILITY.md` | 前期實測報告（歷史記錄） |
