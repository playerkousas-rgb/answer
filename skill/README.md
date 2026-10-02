# `hk-scout-kb` — Agent Skill

> 俾**用戶自己嘅 AI agent** 用嘅 skill。
> 格式：[agentskills.io](https://agentskills.io) 開放標準（Anthropic 2025-12-18 發佈）。
> 一個 skill，40+ 個 agent 產品通用。

```
hk-scout-kb/
├── SKILL.md                          ← frontmatter + 工作流程（~180 行）
├── AGENTS.md                         ← Codex / Copilot 用嘅入口
├── .github/copilot-instructions.md   ← VS Code Copilot 用
├── scripts/
│   ├── search_kb.py                  ← 本地搜尋（**零依賴**，Python 3.8+ stdlib）
│   └── verify_version.py             ← 版本核對（去官方目錄頁實時核實）
├── references/                       ← Level 3：agent 需要時先讀
│   ├── rules-index.md                ← 332 條規條完整索引
│   ├── versions.md                   ← 新舊版本 + 5 項已核實差異 + 3 個技術陷阱
│   ├── aliases.md                    ← 口語 → 原文用字 對照表
│   ├── citation.md                   ← 引用模板（4 種情境）
│   └── documents.md                  ← 文件清單（sha256 / 頁數 / 官方 URL）
└── assets/
    └── kb-index.json                 ← compact 搜尋索引（1.1 MB，含 BM25 postings）
```

---

## 用戶點樣裝

### Claude Code
```bash
# 個人（所有 project 都用到）
git clone https://github.com/YOUR_GH/scout-kb.git /tmp/scout-kb
cp -r /tmp/scout-kb/skill/hk-scout-kb ~/.claude/skills/

# 或者 project 層級
mkdir -p .claude/skills
cp -r /tmp/scout-kb/skill/hk-scout-kb .claude/skills/
```
之後直接問童軍規則就會自動 trigger，或者用 `/hk-scout-kb`。

### OpenAI Codex
```bash
mkdir -p ~/.agents/skills
cp -r /tmp/scout-kb/skill/hk-scout-kb ~/.agents/skills/
```
CLI 入面用 `$hk-scout-kb` 明確調用。

### VS Code / GitHub Copilot
```bash
mkdir -p .github/skills
cp -r /tmp/scout-kb/skill/hk-scout-kb .github/skills/
```

### Claude.ai（網頁版）
上傳 `skill/hk-scout-kb.zip`：Settings → Capabilities → Skills → Upload。

### Cursor / Windsurf / Goose / OpenCode / 其他
睇 [agentskills.io](https://agentskills.io) 嘅 client showcase，或者用
[Vercel skills CLI](https://github.com/vercel/skills)（支援 79 個 agent）：
```bash
npx skills add https://github.com/YOUR_GH/scout-kb/tree/main/skill/hk-scout-kb
```

### 完全唔想裝 skill 嘅用戶
直接叫佢哋嘅 AI fetch：
```
請先讀 https://raw.githubusercontent.com/YOUR_GH/scout-kb/main/llms.txt
了解呢個資料庫嘅結構，之後所有童軍規則問題都根據呢度嘅原文回答，
並附規條編號同官方原文連結。
```

---

## Progressive disclosure：點解呢個格式啱

| Level | 幾時載入 | Token 成本 | 內容 |
|---|---|---|---|
| 1 | **永遠**（agent startup） | ~100 | `name` + `description` |
| 2 | skill 被 trigger 時 | <5,000 | `SKILL.md` body |
| 3 | agent 需要時 | **0**（script 執行，只有 output 入 context） | `scripts/` `references/` `assets/` |

用戶裝咗 100 個 skill 都唔會塞爆 context — 你個 skill 平時只佔 ~100 tokens。
只有當有人問「樂行童軍幾多歲要退」嘅時候先至載入。

**呢個係 Skill 比 MCP 更啱你呢個 use case 嘅原因**：MCP 要維持一個 server 連線
同 tool schema 常駐 context；Skill 係純檔案，agent 需要時先讀。

---

## `description` 係最關鍵嘅一行

Agent 靠 `description` 決定要唔要用你個 skill。呢個係唯一喺 Level 1 見到嘅嘢。

而家嘅 description 刻意包含：
- **英文 + 中文**（用戶可能用任一種語言問）
- **正式術語**：Policy Organisation and Rules, POR, 年齡規限, 最低應有標準, 領袖委任書
- **口語 trigger**：幾多歲、要退、超齡、開團要幾多人、幾多個領袖、幾多人簽名
- **第二個用途**：verify whether a scout.org.hk PDF is current or superseded

改 description 就要重跑 `python3 pipeline/build_skill.py`（會驗證 ≤1024 字元）。

---

## 兩個 script 嘅設計

### `search_kb.py` — 零依賴
```bash
python3 scripts/search_kb.py "幼童軍團 年齡"          # 本地搜尋
python3 scripts/search_kb.py --rule 2.3.7 --fetch    # 攞逐字原文
python3 scripts/search_kb.py --list 2.3              # 列出成節
python3 scripts/search_kb.py "旅財政" --json          # agent parse 用
```

**刻意唔用 jieba / requests / 任何 third-party package。**
原因：skill 會被裝入幾十種唔同 agent 環境，你唔可以假設佢哋有乜。
Python 3.8+ stdlib only = 到處都行。

分詞用 CJK bigram + trigram + 完整段。實測：**呢個比 jieba 更可靠**，
因為 jieba 會把「小童軍團」切做「小/童軍團」，令「童軍團」誤中「小童軍團」。
Skill 改用 **substring match + 最長優先**（見 `references/aliases.md`），完全避開呢個問題。

### `verify_version.py` — 呢個係成個 skill 最重要嘅一件嘢
```bash
python3 scripts/verify_version.py "https://www.scout.org.hk/uploads/tc/por/por_c_20220908_1.pdf"
python3 scripts/verify_version.py --sha256 3787fac7...
python3 scripts/verify_version.py <url> --offline      # 唔上網
python3 scripts/verify_version.py <url> --json
```

**實時**去官方目錄頁核實，唔係靠 bundled 資料。回傳：
- `CURRENT` / `SUPERSEDED` / `NOT_IN_INDEX` / `UNKNOWN`
- 官方目錄頁而家列出嘅檔案
- 檔案本身仲 live 嗎（HTTP status + Last-Modified）
- 如果 SUPERSEDED：**已核實嘅實質差異**（5 項年齡規限）
- exit code 非 0（方便 agent / CI 判斷）

實測結果：

| 輸入 | 結果 |
|---|---|
| `por_c_20220908_1.pdf` | ❌ SUPERSEDED + 5 項差異 + 「URL 仍然 live」警告 |
| `por_c_20260917.pdf` | ✅ CURRENT |
| `POR_C_20260917.pdf`（大階） | ✅ CURRENT（自動 canonicalise — 大階會 301 去細階） |
| sha256 `3787fac7…` | ❌ SUPERSEDED（AI 手上只有 hash 都得） |
| 未知檔案 | ⚠️ NOT_IN_INDEX，但照樣報告「HTTP 200，檔案 live」 |

最後嗰個 case 正正就係問題核心：**舊版 PDF 開到，但唔喺官方目錄頁。**

---

## 你自己點樣 build / 更新

```bash
# 由 corpus/ 生成（同 build_site.py 同一個 source of truth）
python3 pipeline/build_skill.py --owner YOUR_GH --repo scout-kb --zip
```

輸出：
```
✅ skill build 完成
   rules           : 332
   BM25 vocab      : 7632
   kb-index.json   : 1131 KB
✅ 規格驗證通過（agentskills.io：name / description / body 長度 / 引用檔案）
📦 zip: skill/hk-scout-kb.zip  (283 KB)
```

`build_skill.py` 會自動驗證 agentskills.io 規格：
- `name` kebab-case、≤64 字元、**必須同資料夾名一樣**、唔可以用 reserved word
- `description` ≤1024 字元，太短會 warn（會令 agent 唔識 trigger）
- body ≤500 行 / ~5000 tokens
- SKILL.md 引用嘅每個 `references/` `scripts/` `assets/` 檔案都要存在

**手寫 vs generated：**

| 檔案 | 邊個負責 |
|---|---|
| `SKILL.md` | 手寫（build 只驗證，唔覆蓋） |
| `scripts/*.py` | 手寫 |
| `references/citation.md` | generated（模板固定） |
| `references/rules-index.md` | **generated**（由 corpus） |
| `references/versions.md` | **generated** |
| `references/aliases.md` | **generated**（由 `data/alias_routes.json`） |
| `references/documents.md` | **generated** |
| `assets/kb-index.json` | **generated** |
| `AGENTS.md` · `.github/copilot-instructions.md` | **generated** |

---

## 安全

Skill 係**可執行代碼**。用戶裝之前應該 review `scripts/`。

你個 skill 嘅安全性質：
- ✅ 零 third-party 依賴（冇 supply chain 風險）
- ✅ 只 read，唔 write（除咗 stdout）
- ✅ 只連 `raw.githubusercontent.com`（public，冇 auth）同 `scout.org.hk`（read-only GET）
- ✅ 唔收集任何資料、冇 telemetry
- ✅ 唔需要任何 API key
- ✅ `SKILL.md` 冇用 `allowed-tools`（experimental 欄位，跨 agent 支援不一致）

呢啲要寫明俾用戶知 —— 社群研究發現 **36% 嘅公開 skill 有安全瑕疵**，
所以你主動講清楚「零依賴、唯讀、冇 telemetry」係一個實際嘅信任優勢。

---

## 免責聲明（已內建喺 SKILL.md 同每個 script 輸出）

> 本網站為非官方獨立工具，旨在方便成員瀏覽公開資訊，與香港童軍總會無任何官方從屬關係。
> 所有通告版權歸香港童軍總會所有。內容由公開檔案自動整理而成，可能有轉換錯誤；
> 每項資料均附官方原文連結，請於作出任何決定前查閱原文。
