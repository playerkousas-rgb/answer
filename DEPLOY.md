# DEPLOY.md — GitHub + Vercel 部署指引

架構：**完全冇 backend。**

```
GitHub repo (public)                        Vercel (static)
├── llms.txt              ◄─── AI 直接讀 ─── raw.githubusercontent.com
├── llms-full.txt
├── index.json
├── por/<rule_id>.md      (332 個，每條規條一個穩定 URL)
├── md/POR.md
├── corpus/               ◄─── single source of truth（你放 MD 嘅地方）
└── web/                  ─────► Vercel root
    ├── index.html          人用前端
    ├── app.js              client-side BM25 + router + signals
    ├── api/index.json      搜尋索引（build 時生成）
    ├── api/alias_routes.json
    ├── api/manifest.json
    ├── por/*.md
    ├── md/*.md
    ├── llms.txt / llms-full.txt
    ├── sitemap.xml / robots.txt / vercel.json
    └── .nojekyll
```

**AI** → 讀 GitHub repo 嘅 MD（`raw.githubusercontent.com`，冇 auth、冇 rate limit）
**人** → Vercel 前端（client-side 搜尋，冇 server）
**LLM** → 用戶自己帶 API key，瀏覽器直連 provider。你個站**永遠唔持有 key**。

---

## Step 0 — 前置

```bash
pip install -r requirements.txt
```

需要：Python 3.10+、一個 GitHub account、一個 Vercel account。

---

## ⭐ 部署次序

呢個 repo 支援**兩個狀態**，兩個都 deploy 到：

| 狀態 | `corpus/` | `web/` | Vercel | 前端顯示 |
|---|---|---|---|---|
| **shell** | 空 | 已生成（空索引） | ✅ deploy 到 | 「未收錄任何文件 · 架構已部署」 |
| **full** | 有 MD | 已生成（含內容） | ✅ deploy 到 | 正常搜尋 UI |

所以你**唔使等內容先至 deploy**。次序係：

```
Step 1  build shell     →  web/ 生成
Step 2  push GitHub     →  repo 建好
Step 3  deploy Vercel   →  站上架（顯示「未有內容」）
Step 4  加內容          →  corpus/ 放 MD，重跑 Step 1，push，Vercel 自動 redeploy
```

`build_site.py` 會自動判斷：corpus 空 → shell mode；有內容 → full build。
**同一條 command，唔使加 flag。**

---

## Step 1 — Build（shell 或 full，自動判斷）

```bash
python3 pipeline/build_site.py --owner <你的GH> --repo <repo名>
```

corpus 空嘅時候會見到：
```
⚙️  SHELL build（corpus/ 係空嘅）
   ✅ 呢個狀態可以直接 deploy 去 Vercel（Root Directory = web）
```

> ⚠️ build 路徑**零 third-party 依賴** —— 唔使 `pip install` 乜，只需要 Python 3.8+。

---

## Step 1b — （之後）放你嘅 MD 入 `corpus/`

`corpus/` 係 **single source of truth**。其他所有嘢都係 generated。

```
corpus/
├── POR/POR.md                 ← 政策、組織及規條
├── GUIDE/gpopening.md         ← 開辦童軍旅指引
├── CIRC-POLICY/20-2026.md     ← 通告
└── ...
```

每份 MD **必須**有 frontmatter（呢啲欄位會變成 AI 睇到嘅出處）：

```yaml
---
doc_id: POR
title: 香港童軍總會「政策、組織及規條」
doc_type: policy                    # policy | guideline | circular
source_url: https://www.scout.org.hk/uploads/tc/por/por_c_20260917.pdf
official_landing: https://www.scout.org.hk/tc/scouting/policy-guidelines/por/index.html
source_sha256: 03381e0c…
source_bytes: 1558349
source_pages: 105
source_last_modified: 2026-09-18T03:52:40Z
version_label: 2010版（整合修訂至 2026-09）
effective_from: 2026-09-18
status: current                     # current | superseded | needs_manual_review
supersedes: por_c_20220908_1.pdf
language: zh-HK
sensitivity: public                 # 唔係 public 就唔會入 build
qa_passed: true                     # false → 唔會入 build
retrieved: 2026-10-02
---
```

> ⚠️ `status: superseded`、`qa_passed: false`、`sensitivity` 唔係 `public` 嘅檔案
> **會被 build 跳過**。呢個係保護機制：寧可知識庫舊三日，都唔好俾佢錯。

正文格式：規條用 `### 規條 2.3.7 樂行童軍團`，章節用 `# 第 1 章 總會的目的`。
`pipeline/build_site.py` 會自動 chunk + 聚合父規條。

---

放完之後重跑 Step 1 同一條 command，就會變成 full build：

```
✅ build 完成
   documents      : 1
   rules (MD 檔)  : 332
   chunks         : 332
   aggregation    : {'from_heading': 72, 'aggregated': 61, 'pages_stripped': 158}
   web/api/index.json : 0.81 MB
```

`--owner`/`--repo` 會用嚟生成 MD 入面嘅 `raw.githubusercontent.com` URL。
**未填就會生成 `YOUR_GH/scout-kb` 佔位符** — deploy 前必須填。

Skill 都要一齊 build：
```bash
python3 pipeline/build_skill.py --owner <你的GH> --repo <repo名> --zip
```

---

## Step 3 — 本機驗證（deploy 前一定要做）

```bash
cd web && python3 -m http.server 8010 --bind 0.0.0.0
```

開 `http://localhost:8010`，check：

| 檢查 | 期望 |
|---|---|
| `/` | UI 載入，左下角顯示 `332 條規條 · build <id>` |
| `/app.js` | 200，`text/javascript` |
| `/api/index.json` | 200，~0.8 MB |
| `/por/2.3.7.md` | 200，`text/markdown`，有 frontmatter |
| `/llms.txt` | 200，列明已知過時來源 |
| 搜尋「樂行童軍幾多歲要退」 | top-1 = 規條 2.3.7，verbatim 含「十八歲至二十五歲」 |
| 搜尋「童軍團幾多歲要退」 | top-1 = 規條 **2.3.5**（唔係 2.3.3 小童軍） |
| 搜尋「旅務委員會」 | top-3 含規條 2.10 |
| 搜尋「童軍旅團長月薪幾多」 | 有候选 + `signals.topic_not_in_corpus`（**唔會拒答**） |

自動化驗證：
```bash
python3 pipeline/static_eval.py --skill   # canary + golden，測 web 同 skill

```

---

## Step 2 — Push 去 GitHub

```bash
cd /path/to/scout-kb
git init -b main
git add -A
git commit -m "init: AI-first 童軍公開文件資料庫"
git remote add origin git@github.com:YOUR_GITHUB_USERNAME/scout-kb.git
git push -u origin main
```

**要 commit 嘅**（因為 GitHub 就係 AI 讀嘅地方）：
`por/` `md/` `llms.txt` `llms-full.txt` `index.json` `web/` `corpus/` `data/` `skill/`

shell 狀態嘅時候 `por/` `md/` `web/por/` `web/md/` 只有 `.gitkeep` —— 呢個係正常嘅。

`.gitignore` 已經排除咗中間產物（`raw_downloads/`、`registry/state.json` 等）。

Push 完之後，AI 就可以讀：
```
https://raw.githubusercontent.com/YOUR_GH/scout-kb/main/llms.txt
https://raw.githubusercontent.com/YOUR_GH/scout-kb/main/por/2.3.7.md
https://raw.githubusercontent.com/YOUR_GH/scout-kb/main/index.json
```

> ⚠️ **repo 必須 public**，否則 `raw.githubusercontent.com` 需要 auth。

---

## Step 3 — Deploy 去 Vercel

### 方法 A：Dashboard（最簡單）

1. [vercel.com/new](https://vercel.com/new) → Import 你個 repo
2. **Root Directory** 設做 `web`　← 呢步最關鍵
3. Framework Preset: **Other**
4. Build Command: **留空**
5. Output Directory: **留空**（因為 `web/` 本身已經係 output）
6. Deploy

> ✅ **shell 狀態都 deploy 到。** `web/` 已經存在（Step 1 生成咗），
> Vercel 會正常 build 同上架，前端顯示「未收錄任何文件」。
> 你之後 push 內容，佢會自動 redeploy 變成 full 站。

### 方法 B：CLI

```bash
npm i -g vercel
cd web
vercel --prod
```

### 方法 C：Build-on-push（長遠用呢個）

如果你想 push `corpus/` 之後 Vercel 自動 rebuild：

- Root Directory: **留空**（用 repo 根）
- Build Command: `python3 pipeline/build_site.py --base-url $VERCEL_URL --owner $GITHUB_OWNER --repo $GITHUB_REPO`
  （零 third-party 依賴，唔使 pip install）
- Output Directory: `web`
- Environment Variables: `GITHUB_OWNER`、`GITHUB_REPO`

⚠️ 注意：Vercel build environment 要有 Python 3。如果冇，用
[`vercel/python` runtime](https://vercel.com/docs/functions/runtimes/python)
或者改用 GitHub Actions build + 只 deploy `web/`：

```yaml
# .github/workflows/deploy.yml
name: deploy
on: { push: { branches: [main], paths: ["corpus/**", "data/**", "app/**", "pipeline/**"] } }
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12", cache: pip }
      - run: python3 pipeline/build_site.py --base-url https://scout-kb.vercel.app --owner ${{ github.repository_owner }} --repo ${{ github.event.repository.name }}
      - run: python3 pipeline/static_eval.py --skill --strict   # 失敗就 block deploy
      - uses: actions/upload-artifact@v4
        with: { name: web, path: web/ }
```

然後 Vercel 設定成 deploy 呢個 artifact（或者用 `actions/deploy-pages` 直接出 GitHub Pages）。

---

## Step 3b — Deploy 後檢查

```bash
BASE=https://scout-kb.vercel.app
curl -sI $BASE/                     # 200, text/html
curl -sI $BASE/app.js               # 200, text/javascript
curl -sI $BASE/api/index.json       # 200, application/json
curl -sI $BASE/por/2.3.7.md         # 200, text/markdown
curl -s  $BASE/llms.txt | head -20
curl -s  $BASE/api/manifest.json | python3 -m json.tool | head -30
```

再 check `raw.githubusercontent.com` 嗰邊：
```bash
curl -s https://raw.githubusercontent.com/YOUR_GH/scout-kb/main/llms.txt | head -20
curl -s https://raw.githubusercontent.com/YOUR_GH/scout-kb/main/por/2.3.7.md | head -20
```

---

## CORS：用戶自帶 API key 嘅實際情況

前端用 **direct mode**（瀏覽器直連 provider），key 只喺 localStorage。

| Provider | 瀏覽器直連 | 備註 |
|---|---|---|
| **Gemini** | ✅ 一般得 | `?key=` query param |
| **OpenRouter** | ✅ 得 | 明確支援 |
| **Anthropic** | ✅ 得 | 需要 `anthropic-dangerous-direct-browser-access: true`（`app.js` 已自動加） |
| **OpenAI** | ⚠️ 視乎政策 | 有時會被 CORS block |
| **Ollama / 本地** | ⚠️ 要自己設 | `OLLAMA_ORIGINS=*` |

**如果 direct mode 被 CORS block：**
1. 用戶可以即刻改用 **copy mode**（永遠可用，零設定）
2. 或者你加一個 serverless proxy（見下面）

### 可選：Vercel serverless proxy

建立 `web/api/llm.js`：

```javascript
export const config = { runtime: "edge" };

export default async function handler(req) {
  if (req.method !== "POST") return new Response("POST only", { status: 405 });
  const { provider, model, api_key, body } = await req.json();
  const ENDPOINTS = {
    openai:     "https://api.openai.com/v1/chat/completions",
    openrouter: "https://openrouter.ai/api/v1/chat/completions",
    anthropic:  "https://api.anthropic.com/v1/messages",
  };
  const url = ENDPOINTS[provider];
  if (!url) return new Response(JSON.stringify({ error: "unknown provider" }), { status: 400 });

  const headers = { "Content-Type": "application/json" };
  if (provider === "anthropic") {
    headers["x-api-key"] = api_key;
    headers["anthropic-version"] = "2023-06-01";
  } else {
    headers["Authorization"] = `Bearer ${api_key}`;
  }

  // ⚠️ key 只喺呢次 request 用，唔會 log、唔會儲存
  const r = await fetch(url, { method: "POST", headers, body: JSON.stringify(body) });
  const txt = await r.text();
  return new Response(txt, {
    status: r.status,
    headers: { "Content-Type": "application/json",
               "Cache-Control": "no-store" },
  });
}
```

> ⚠️ **安全提醒**：即使加咗 proxy，你個站都**唔應該長期持有用户嘅 key**。
> 每次 request 由瀏覽器帶上、用完即棄。唔好寫入任何 storage 或 log。
> 如果唔想做呢個責任，**就淨係提供 copy mode** — 完全冇風險。

---

## 之後點樣更新內容

```bash
# 1. 放新 MD 入 corpus/
cp 新文件.md corpus/GUIDE/xxx.md      # 記得加 frontmatter

# 2. rebuild
python3 pipeline/build_site.py --base-url https://scout-kb.vercel.app \
                               --owner YOUR_GH --repo scout-kb

# 3. 驗證
python3 pipeline/static_eval.py --skill --strict


# 4. push
git add -A && git commit -m "add: xxx" && git push
```

`web/api/index.json` 會自動重建，Vercel 會自動 redeploy。

---

## 常見問題

**Q: AI 點知道嚟呢度搵？**
A: 三個途徑 —
1. `llms.txt`（部分 AI crawler 會讀）
2. Google 索引 `por/*.md`（`sitemap.xml` 已提交）
3. **你主动話俾 AI 知** — 喺 ChatGPT/Claude 入面貼呢句：
   > 「請先 fetch `https://raw.githubusercontent.com/YOUR_GH/scout-kb/main/llms.txt`
   > 了解呢個資料庫嘅結構，然後先回答。」

   呢個係而家最可靠嘅做法。唔好指望 AI 自己搵到。

**Q: 使唔使 MCP server？**
A: 呢個架構唔使。AI 直接讀 MD 就得。MCP 嘅價值係「俾 AI 有一個 tool 可以 call」，
但如果 AI 已經可以 fetch URL，MD + `llms.txt` 就夠。
真要 MCP 嘅話，可以之後用 `web/api/index.json` 起一個（見 `DESIGN.md`）。

**Q: `index.json` 0.8 MB 會唔會太慢？**
A: gzip 之後約 200 KB，首次載入可接受。如果之後 corpus 大咗（>5 MB），
改成 lazy-load：只載 postings，verbatim 逐條 fetch `por/<id>.md`。
`app.js` 嘅 `retrieve()` 已經回 `canonical_url`，改動唔大。

**Q: 可唔可以用 GitHub Pages 代替 Vercel？**
A: 可以。`web/` 已經有 `.nojekyll`。GitHub Pages 仲有一個好處：
同 repo 同一個域名，AI fetch `por/*.md` 同人行 UI 係同一個 host。
設定：repo Settings → Pages → Source: `main` branch, `/web` directory。
