#!/usr/bin/env python3
"""
build_skill.py — 由 corpus/ 生成 Agent Skill 包（agentskills.io 開放標準）。

同 build_site.py 一樣由 corpus/ 出發，所以兩者永遠一致。
corpus/ 係 single source of truth；skill 只係另一種 packaging。

產生：
  skill/hk-scout-kb/
  ├── SKILL.md                      ← 手寫（呢個 script 只驗證，唔覆蓋）
  ├── scripts/search_kb.py          ← 手寫
  ├── scripts/verify_version.py     ← 手寫
  ├── references/rules-index.md     ← generated（332 條規條）
  ├── references/versions.md        ← generated
  ├── references/aliases.md         ← generated（由 data/alias_routes.json）
  ├── references/documents.md       ← generated
  ├── references/citation.md        ← 手寫
  ├── assets/kb-index.json          ← generated（compact 搜尋索引）
  ├── AGENTS.md                     ← generated（Codex / Copilot 用）
  └── .github/copilot-instructions.md ← generated

用法：
  python3 pipeline/build_skill.py --owner YOUR_GH --repo scout-kb
  python3 pipeline/build_skill.py --zip          # 另外產生 .zip（Claude.ai 上傳用）
"""
from __future__ import annotations
import argparse, datetime, hashlib, json, math, os, re, shutil, sys, zipfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

from build_site import (  # noqa: E402
    parse_md, chunk_doc, aggregate, DISCLAIMER, RAW_BASE,
)

SKILL_ROOT = ROOT / "skill" / "hk-scout-kb"
SKILL_NAME = "hk-scout-kb"

DISCOVERY = """# AGENTS.md

呢個 repo 提供一個 **Agent Skill**（agentskills.io 開放標準）：`hk-scout-kb`。

如果你係一個 AI agent 而用戶問到香港童軍總會嘅規則、年齡規限、旅務運作、
領袖資格、財務程序等，請使用 `skill/hk-scout-kb/SKILL.md` 入面嘅工作流程。

## 快速開始

```bash
python3 skill/hk-scout-kb/scripts/search_kb.py "幼童軍團 年齡"
python3 skill/hk-scout-kb/scripts/search_kb.py --rule 2.3.7 --fetch
python3 skill/hk-scout-kb/scripts/verify_version.py <任何 scout.org.hk PDF URL>
```

`search_kb.py` 零依賴（Python 3.8+ stdlib only），本地搜尋 bundled index。
`--fetch` 先至需要網絡（去 raw.githubusercontent.com 攞逐字原文）。

## 最重要嘅一件事

scout.org.hk 嘅**舊版 PDF 冇下架**，URL 仍然 live，而且新舊兩版封面**都印住「2010 版」**。
你冇辦法靠讀文件本身分辨邊份現行。用 `verify_version.py`，或者只信呢個 repo 入面嘅 MD。

## 引用格式

每次回答必須附：規條編號、文件名稱、版本、官方原文 URL、as_of 日期。
結尾附：「以上內容截至 {as_of}，一切以香港童軍總會最新公佈為準。」

""" + DISCLAIMER + "\n"


def tokenize(text: str):
    t = re.sub(r"[#*|>`\-=[\]()（）\s]+", " ", str(text or "").lower())
    out = []
    STOP = set("的 了 是 由 至 起 止 及 和 與 或 在 有 為 以 於 不 而 之 其 本 該 上 下 中 個 名 須 應 可 得 被 把 對 從 到 但 若 如 因 所 已 未 一個 我們 你們 他們 什麼 點樣 幾多 邊個 乜嘢 嘅 咗 喺 唔 冇 佢 呢 嗰 呀 啦 嘢 噉 咁 嚟 睇 想 要 會 做 用 問 答".split())
    for m in re.finditer(r"[a-z0-9]+", t):
        if m.group(0) not in STOP:
            out.append(m.group(0))
    for m in re.finditer(r"[\u4e00-\u9fff]+", t):
        seg = m.group(0)
        if len(seg) >= 2 and seg not in STOP:
            out.append(seg)
        for i in range(len(seg) - 1):
            if seg[i:i + 2] not in STOP:
                out.append(seg[i:i + 2])
        # ⚠️ trigram 必須有 — build_site.py / app/app.js 都有。
        #    缺咗佢會令 web 同 skill 嘅 postings 唔同 → 同一條問題兩個交付物回唔同答案。
        for i in range(len(seg) - 2):
            if seg[i:i + 3] not in STOP:
                out.append(seg[i:i + 3])
    return out


def build(owner="", repo="", branch="main", base_url="", make_zip=False):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    corpus = ROOT / "corpus"

    # ---------- 讀 corpus ----------
    docs, all_chunks = [], []
    skipped = []
    for path in sorted(corpus.rglob("*.md")):
        rel = path.relative_to(corpus)
        if any(p.startswith("_") for p in rel.parts):
            continue
        # ⚠️ 同 build_site.py 一樣：說明檔唔係 corpus 文件
        if path.name.lower() in ("readme.md", "index.md", "changelog.md"):
            skipped.append(f"{rel}（說明檔）"); continue
        fm, text = parse_md(path)
        if not fm.get("doc_id"):
            skipped.append(f"{rel}（frontmatter 缺 doc_id）"); continue
        if fm.get("status") == "superseded" or fm.get("qa_passed") == "false":
            skipped.append(f"{rel}（status={fm.get('status')} qa_passed={fm.get('qa_passed')}）")
            continue
        if fm.get("sensitivity") not in (None, "", "public"):
            continue
        did = fm.get("doc_id") or path.stem
        ch = chunk_doc(fm, text)
        all_chunks += [{**c, "doc_id": did, "_fm": fm} for c in ch]
        docs.append({
            "doc_id": did, "title": fm.get("title", did),
            "doc_type": fm.get("doc_type", "policy"),
            "version_label": fm.get("version_label", ""),
            "source_url": fm.get("source_url", ""),
            "official_landing": fm.get("official_landing", ""),
            "source_sha256": fm.get("source_sha256", ""),
            "source_last_modified": fm.get("source_last_modified", ""),
            "source_bytes": fm.get("source_bytes", ""),
            "source_pages": fm.get("source_pages", ""),
            "effective_from": fm.get("effective_from", ""),
            "supersedes": fm.get("supersedes", ""),
            "status": fm.get("status", "current"),
            "as_of": now[:10],
            "md_path": f"md/{did}.md",
            "chunks": len(ch),
        })
    if not docs:
        # ⚠️ **唔好 raise** —— 呢個會令 DEPLOY.md Step 1 嗰串 command 中断。
        #    但亦都唔好靜靜地生一個「空 skill」：實測 corpus 空嘅時候呢度會生
        #    一個 28 KB 嘅 skill（0 條規條），而且照樣 report「✅ 規格驗證通過」，
        #    用戶會以為自己有個 skill。
        #    正確做法：**skip + exit 0 + 清楚講明**。
        print("=" * 68)
        print("⚙️  SKILL build SKIPPED — corpus/ 係空嘅")
        print("-" * 68)
        print("   冇內容就 build 唔到 skill —— 生出來嘅會係一個 0 條規條嘅空殼。")
        print("   skill/hk-scout-kb/ 入面手寫嘅部份（SKILL.md、scripts/）已經喺 repo，")
        print("   只係缺 generated 嘅 assets/kb-index.json 同 references/。")
        print()
        print("   → 放 MD 入 corpus/ 之後重跑呢條 command 就會生成。")
        print("   → 呢個唔係錯誤，所以 exit 0（唔會 block CI）。")
        print("=" * 68)
        return None

    stats = aggregate(all_chunks)

    # ---------- compact search index ----------
    rules = []
    for c in all_chunks:
        if not c["rule_id"]:
            continue
        d = next((x for x in docs if x["doc_id"] == c["doc_id"]), {})
        kws = sorted(set(tokenize(c["heading"] + " " + c["heading"] + " " + c["verbatim"][:900])))
        rules.append({
            "id": c["rule_id"],
            "title": c["title"] or c["heading"].replace("規條 ", "", 1).strip(),
            "doc": d.get("title", ""),
            "doc_id": c["doc_id"],
            "version": d.get("version_label", ""),
            "sha256": d.get("source_sha256", ""),
            "last_modified": d.get("source_last_modified", ""),
            "official_pdf": d.get("source_url", ""),
            "official_landing": d.get("official_landing", ""),
            "as_of": d.get("as_of", now[:10]),
            "chapter": c.get("chapter"),
            "chars": len(c["verbatim"]),
            "aggregated": bool(c.get("sub_rules")),
            "sub_rules": c.get("sub_rules"),
            "is_stub": bool(c.get("is_stub")),
            "amended_by": c.get("amended_by"),
            "kw": kws[:80],
            # ⭐ 連 verbatim 一齊 bundle。
            #    原因：agent 用 --rule <id> 就係想攞原文。如果要 --fetch 先攞到，
            #    就等於每次都要上網。加埋 verbatim 只係多 ~180 KB，
            #    但令成個 skill **完全離線可用**（除咗 verify_version 要實時核實）。
            "verbatim": c["verbatim"],
        })

    rid2i = {r["id"]: i for i, r in enumerate(rules)}
    docs_tokens = [tokenize((r["title"] + " " + r["title"] + " " +
                             " ".join(r["kw"]) + " " + r["id"])) for r in rules]
    N = len(docs_tokens)
    df = Counter()
    for dt in docs_tokens:
        for t in set(dt):
            df[t] += 1
    avgdl = sum(len(d) for d in docs_tokens) / max(N, 1)
    idf = {t: round(math.log(1 + (N - n + 0.5) / (n + 0.5)), 3) for t, n in df.items()}
    k1, b = 1.5, 0.75
    postings = {}
    for i, dt in enumerate(docs_tokens):
        tf = Counter(dt)
        dl = len(dt)
        for t, f in tf.items():
            postings.setdefault(t, []).append(
                [rules[i]["id"], round(idf[t] * f * (k1 + 1) / (f + k1 * (1 - b + b * dl / avgdl)), 3)])
    for t in postings:
        postings[t].sort(key=lambda x: -x[1])
        postings[t] = postings[t][:40]

    alias_p = ROOT / "data" / "alias_routes.json"
    alias = json.loads(alias_p.read_text(encoding="utf-8")) if alias_p.exists() else {}
    alias.pop("_comment", None)
    for k in list(alias):
        if isinstance(alias[k], dict):
            alias[k].pop("_comment", None)

    stale_p = ROOT / "data" / "known_stale.json"
    stale = json.loads(stale_p.read_text(encoding="utf-8")) if stale_p.exists() else []

    SKILL_ROOT.joinpath("assets").mkdir(parents=True, exist_ok=True)
    SKILL_ROOT.joinpath("references").mkdir(parents=True, exist_ok=True)

    idx = {
        "meta": {
            "skill": SKILL_NAME,
            "generated": now,
            "owner": owner, "repo": repo, "branch": branch,
            "base_url": base_url,
            "raw_base": RAW_BASE.format(owner=owner or "{owner}",
                                        repo=repo or "{repo}", branch=branch),
            "rules": len(rules),
            "bm25": {"k1": k1, "b": b, "N": N, "avgdl": round(avgdl, 2),
                     "vocab": len(idf)},
            "build_stats": dict(stats),
            "disclaimer": DISCLAIMER,
        },
        "documents": docs,
        "rules": rules,
        "postings": postings,
        "idf": idf,
        "alias_routes": alias,
        "known_stale_sources": stale,
    }
    p_idx = SKILL_ROOT / "assets" / "kb-index.json"
    p_idx.write_text(json.dumps(idx, ensure_ascii=False, separators=(",", ":")),
                     encoding="utf-8")

    # ---------- references/rules-index.md ----------
    L = ["# 規條索引 (Rules Index)", "",
         f"生成時間：{now}　·　共 {len(rules)} 條", "",
         "每條規條嘅 Markdown 都可以直接 fetch：",
         f"`{idx['meta']['raw_base']}/por/<rule_id>.md`", "",
         "或者用 script：`python3 scripts/search_kb.py --rule <rule_id> --fetch`", ""]
    by_chapter = {}
    for r in rules:
        by_chapter.setdefault(r.get("chapter") or "?", []).append(r)
    for ch in sorted(by_chapter, key=lambda x: (len(x), x)):
        rows = by_chapter[ch]
        title = rows[0].get("title", "")
        L.append(f"## 第 {ch} 章")
        L.append("")
        L.append("| 規條 | 標題 | 字數 | 備註 |")
        L.append("|---|---|---|---|")
        for r in sorted(rows, key=lambda x: [int(n) for n in x["id"].split(".") if n.isdigit()]):
            note = []
            if r.get("aggregated"): note.append(f"聚合 {len(r['sub_rules'])} 條子規條")
            if r.get("is_stub"): note.append("stub（無獨立內容）")
            if r.get("amended_by"): note.append(f"⚠️ 修訂 {r['amended_by']}")
            L.append(f"| `{r['id']}` | {r['title'][:60]} | {r['chars']} | {'；'.join(note)} |")
        L.append("")
    L += ["---", "", DISCLAIMER, ""]
    (SKILL_ROOT / "references" / "rules-index.md").write_text("\n".join(L), encoding="utf-8")

    # ---------- references/versions.md ----------
    L = ["# 版本資料 (Versions)", "",
         "## ⚠️ 點解版本核對係呢個 skill 最重要嘅功能", "",
         "三個已實測嘅事實：", "",
         "### 1. 舊版 PDF 冇下架", "",
         "總會嘅舊版「政策、組織及規條」PDF **仍然 live**，仍然喺官方域名，"
         "搜尋引擎仍然索引緊。但官方目錄頁只列現行版。", "",
         "### 2. 新舊兩版嘅標籤一模一樣", "",
         "兩份 PDF 嘅封面同目錄**都印住「2010 版」**。"
         "現行版嘅 PDF metadata 仲差過舊版：", "",
         "| | 舊版 2022 | 現行 2026 |", "|---|---|---|",
         "| `title` | 香港童軍總會「政策、組織及規條」 | `PREFACE` |",
         "| `author` | 香港童軍總會 | `ADMCO4` |",
         "| `producer` | Adobe PDF Library 17.11.238 | Microsoft® Word 2019 |", "",
         "**即係話：讀文件本身分唔到邊份現行。** 只有 sha256 / Last-Modified / "
         "creationDate 可靠。", "",
         "### 3. Naive PDF 抽取會產生幻覺規條", "",
         "通告 ACR20/2026 入面，`' 2.3.4'` 係 font size 12.0，"
         "後面黏住嘅 `'1'` 係 size 8.0 嘅腳註上標。"
         "唔睇 font size 嘅抽取器會合成出 `2.3.41`、`2.3.51`、`6.3.22` — "
         "**呢三條規條喺 POR 入面完全唔存在。**", "",
         "呢個問題影響所有通用 PDF reader，包括 ChatGPT / Claude 內置嗰個。", "",
         "---", "",
         "## 現行版本", ""]
    for d in docs:
        L += [f"### {d['title']}", "",
              f"- doc_id: `{d['doc_id']}`",
              f"- 版本標籤: {d.get('version_label')}",
              f"- sha256: `{d.get('source_sha256')}`",
              f"- Last-Modified: {d.get('source_last_modified')}",
              f"- 頁數 / bytes: {d.get('source_pages')} / {d.get('source_bytes')}",
              f"- 生效日: {d.get('effective_from')}",
              f"- 官方原文: {d.get('source_url')}",
              f"- 官方目錄頁: {d.get('official_landing')}",
              f"- 取代咗: `{d.get('supersedes') or '—'}`", ""]
    if stale:
        L += ["## 已被取代嘅版本（仍然 live，但唔好引用）", ""]
        for s in stale:
            L += [f"### `{s.get('url')}`", "",
                  f"- sha256: `{s.get('sha256')}`",
                  f"- Last-Modified: {s.get('last_modified')}",
                  f"- 頁數: {s.get('pages')}",
                  f"- 狀態: {s.get('status')}　·　URL 仲 live: {s.get('still_live')}",
                  f"- 已被取代: {s.get('superseded_by')}",
                  f"- 備註: {s.get('note')}", "",
                  "**已核實嘅實質差異：**", "",
                  "| 規條 | 項目 | 舊版 | 現行 |", "|---|---|---|---|"]
            for d in s.get("verified_differences", []):
                L.append(f"| {d.get('rule')} | {d.get('topic')} | "
                         f"{d.get('stale')} | {d.get('current')} |")
            L += ["", "> 本站只陳列事實（官方目錄頁而家列緊邊份），",
                  "> 唔判斷總會任何文件嘅對錯。", ""]
    L += ["---", "", DISCLAIMER, ""]
    (SKILL_ROOT / "references" / "versions.md").write_text("\n".join(L), encoding="utf-8")

    # ---------- references/aliases.md ----------
    L = ["# 口語 → 原文用字 對照表 (Aliases)", "",
         "## 點解需要呢個表", "",
         "用戶用口語問（「幾多歲要退」「開團要幾多人」），",
         "原文用書面語（「年齡規限」「最低應有標準」）。",
         "字面匹配完全對唔上。`search_kb.py` 用呢個表做映射。", "",
         "## 匹配方式", "",
         "**Substring match on raw query + 最長優先**，唔經分詞。", "",
         "原因（實測）：jieba 會把「小童軍團」切做「小/童軍團」，",
         "於是問「童軍團」會誤中「小童軍團」。",
         "substring + 最長優先先至可靠。", "",
         "## 支部名 → 規條", "",
         "| 用戶可能講 | 規條 |", "|---|---|"]
    for k, v in sorted(alias.get("section_aliases", {}).items(), key=lambda kv: kv[1]):
        L.append(f"| {k} | `{v}` |")
    L += ["", "## 意圖詞 → 原文用字", "",
          "| 用戶可能講 | 擴充成 |", "|---|---|"]
    for k, v in sorted(alias.get("intent_aliases", {}).items()):
        L.append(f"| {k} | {'、'.join(v) if isinstance(v, list) else v} |")
    L += ["", "## 直接指定規條", "", "| 用戶可能講 | 規條 |", "|---|---|"]
    for k, v in sorted(alias.get("direct_rules", {}).items(), key=lambda kv: kv[1]):
        L.append(f"| {k} | `{v}` |")
    L += ["", "## 跨多條規條嘅主題（要並列或反問，唔好自己揀一個）", "",
          "| 用戶可能講 | 涉及規條 |", "|---|---|"]
    for k, v in alias.get("generic_topics", {}).items():
        L.append(f"| {k} | {'、'.join(v)} |")
    if alias.get("unanswerable"):
        L += ["", "## 已知唔喺 corpus 範圍嘅主題", "",
              "呢啲主題仍然會返回候选（**唔會拒答**），",
              "但會附一個 signal 話你知 corpus 冇呢類資料。由你自行判斷。", "",
              "| 用戶可能講 | 備註 |", "|---|---|"]
        for k, v in alias.get("unanswerable", {}).items():
            L.append(f"| {k} | {v} |")
    L += ["", "---", "",
          "## 維護方式", "",
          "**唔好靠幻想加詞。** 正確做法：", "",
          "1. 記低所有「應該答到但答唔到」嘅真實查詢",
          "2. 睇佢係「用詞對唔上」（加 alias）定「真係冇呢類資料」（加 unanswerable）",
          "3. 改 `data/alias_routes.json`，重跑 `python3 pipeline/build_skill.py`", "",
          DISCLAIMER, ""]
    (SKILL_ROOT / "references" / "aliases.md").write_text("\n".join(L), encoding="utf-8")

    # ---------- references/documents.md ----------
    L = ["# 文件清單 (Documents)", "", f"生成時間：{now}", ""]
    for d in docs:
        L += [f"## {d['title']}", "",
              "| 欄位 | 值 |", "|---|---|",
              f"| doc_id | `{d['doc_id']}` |",
              f"| 類型 | {d.get('doc_type')} |",
              f"| 版本標籤 | {d.get('version_label')} |",
              f"| sha256 | `{d.get('source_sha256')}` |",
              f"| Last-Modified | {d.get('source_last_modified')} |",
              f"| 頁數 | {d.get('source_pages')} |",
              f"| bytes | {d.get('source_bytes')} |",
              f"| 生效日 | {d.get('effective_from')} |",
              f"| 規條數 | {d.get('chunks')} |",
              f"| 狀態 | {d.get('status')} |",
              f"| 官方原文 | {d.get('source_url')} |",
              f"| 官方目錄頁 | {d.get('official_landing')} |",
              f"| 全文 MD | `{idx['meta']['raw_base']}/{d.get('md_path')}` |", "",
              "> 引用呢份文件時，必須附 sha256 或者 Last-Modified。",
              "> 因為文件本身嘅版本標籤（「2010 版」）分唔到新舊。", ""]
    L += ["---", "", DISCLAIMER, ""]
    (SKILL_ROOT / "references" / "documents.md").write_text("\n".join(L), encoding="utf-8")

    # ---------- references/citation.md ----------
    (SKILL_ROOT / "references" / "citation.md").write_text(f"""# 引用格式 (Citation)

## 硬性要求

每次回答必須附：

1. **規條編號** — 例 `規條 2.3.7(d)`
2. **文件名稱** — 例 香港童軍總會「政策、組織及規條」
3. **版本標籤** — 例 2010版（整合修訂至 2026-09）
4. **官方原文 URL** — 例 https://www.scout.org.hk/uploads/tc/por/por_c_20260917.pdf
5. **as_of 日期** — 資料截至日期
6. **結尾句** — 「以上內容截至 {{as_of}}，一切以香港童軍總會最新公佈為準。」

如果有 `amended_by`，額外附通告編號同生效日。

## 模板：單一規條

```
根據香港童軍總會「政策、組織及規條」（2010版，整合修訂至 2026-09）：

**規條 2.3.7 樂行童軍團**

> (d) 年齡規限：十八歲至二十五歲。
> (e) 下列各項為一樂行童軍團的最低應有標準：
> (i) 人 數：一已成立的樂行童軍團，最少要有樂行童軍六名。
> ……

📄 官方原文：https://www.scout.org.hk/uploads/tc/por/por_c_20260917.pdf
🔗 官方目錄頁：https://www.scout.org.hk/tc/scouting/policy-guidelines/por/index.html
`sha256 03381e0c…` · 檔案日期 2026-09-18

以上內容截至 2026-10-02，一切以香港童軍總會最新公佈為準。

（本網站為非官方獨立工具，與香港童軍總會無任何官方從屬關係。
所有通告版權歸香港童軍總會所有。）
```

## 模板：跨多條規條（用戶冇指明支部）

```
你問嘅「年齡規限」喺五個支部各有唔同嘅規定。以下全部列出：

| 規條 | 支部 | 年齡規限 |
|---|---|---|
| 2.3.3 | 小童軍團 | 四歲至七歲 |
| 2.3.4 | 幼童軍團 | 六歲至十一歲 |
| 2.3.5 | 童軍團 | 十一歲至十五歲 |
| 2.3.6 | 深資童軍團 | 十五歲至二十歲 |
| 2.3.7 | 樂行童軍團 | 十八歲至二十五歲 |

（逐字引用自……）

如果你問嘅係某一個支部，請話我知。
```

**唔好自己揀一個答。** 亦唔好只答最「常見」嗰個。

## 模板：搵唔到

```
我喺已收錄嘅總會公開文件入面搵唔到關於「XXX」嘅條文。

我搜尋咗：{{你實際用咗嘅關鍵字}}
候選規條：{{如果有，列出並講明點解唔相關}}

建議直接查閱官方原文：
· 政策、組織及規條：https://www.scout.org.hk/tc/scouting/policy-guidelines/por/index.html
· 通告：https://www.scout.org.hk/tc/circulars-forms/circulars/

或者聯絡相關署／區總監。
```

**絕對唔好：**
- 用似是而非嘅規條代替
- 靠模型自身知識補完
- 講「大概就是……」「一般來說……」

## 模板：用戶貼咗一個 PDF URL

先跑 `verify_version.py`，然後：

```
⚠️ 你提供嘅檔案已被官方目錄頁取代。

你俾嘅：{{url}}
官方目錄頁而家列緊：{{current_url}}

已核實嘅實質差異：
{{差異表}}

以下答案根據現行版本：
……
```

## 禁止生成建議嘅主題

以下主題**只可以列原文**，唔可以生成建議或判斷，並要加一句
「此類事宜請直接聯絡相關署或區總監。」

- 財務／審計（旅財務、銀行戶口、賬冊保存）
- 意外及保險（索償程序）
- 保護童軍成員免受傷害
- 紀律及糾紛處理
- 成員個人資料

原因：呢啲範疇錯誤嘅後果最嚴重，而且往往涉及個案判斷。

---

{DISCLAIMER}
""", encoding="utf-8")

    # ---------- AGENTS.md / copilot-instructions ----------
    (ROOT / "AGENTS.md").write_text(DISCOVERY, encoding="utf-8")
    (SKILL_ROOT / "AGENTS.md").write_text(DISCOVERY, encoding="utf-8")
    gh = SKILL_ROOT / ".github"
    gh.mkdir(exist_ok=True)
    (gh / "copilot-instructions.md").write_text(DISCOVERY, encoding="utf-8")

    # ---------- 驗證 SKILL.md ----------
    problems = validate_skill(SKILL_ROOT)

    # ---------- zip ----------
    zip_path = None
    if make_zip:
        zip_path = ROOT / "skill" / f"{SKILL_NAME}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(SKILL_ROOT.rglob("*")):
                if f.is_file():
                    z.write(f, f.relative_to(SKILL_ROOT.parent))
    return {
        "rules": len(rules), "docs": len(docs), "stats": dict(stats),
        "index_bytes": p_idx.stat().st_size,
        "vocab": len(idf), "now": now,
        "zip": zip_path, "problems": problems,
        "files": sorted(str(p.relative_to(SKILL_ROOT)) for p in SKILL_ROOT.rglob("*") if p.is_file()),
    }


def validate_skill(d: Path):
    """agentskills.io 規格驗證"""
    prob = []
    sk = d / "SKILL.md"
    if not sk.exists():
        return ["❌ 缺 SKILL.md"]
    t = sk.read_text(encoding="utf-8")
    if not t.startswith("---"):
        prob.append("❌ SKILL.md 唔係以 --- 開頭（冇 frontmatter）")
        return prob
    end = t.find("\n---", 3)
    if end < 0:
        prob.append("❌ frontmatter 未閉合")
        return prob
    fm = t[3:end]
    body = t[end + 4:]

    m = re.search(r"(?m)^name:\s*(.+)$", fm)
    if not m:
        prob.append("❌ frontmatter 缺 required 欄位 name")
    else:
        name = m.group(1).strip().strip('"').strip("'")
        if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name):
            prob.append(f"❌ name「{name}」唔合規格：只可以細階字母、數字、單 hyphen")
        if len(name) > 64:
            prob.append(f"❌ name 超過 64 字元（{len(name)}）")
        if name != d.name:
            prob.append(f"❌ name「{name}」同資料夾名「{d.name}」唔同 — skill 唔會 load")
        for w in ("anthropic", "claude"):
            if w in name:
                prob.append(f"❌ name 唔可以用 reserved word「{w}」")

    dm = re.search(r"(?m)^description:\s*(.*)$", fm)
    desc = ""
    if dm:
        # 支援 `description: >-` block scalar
        if dm.group(1).strip() in (">-", ">", "|", "|-"):
            lines, i = [], fm.split("\n").index(dm.group(0)) + 1
            fl = fm.split("\n")
            while i < len(fl) and (fl[i].startswith("  ") or not fl[i].strip()):
                lines.append(fl[i].strip()); i += 1
            desc = " ".join(x for x in lines if x)
        else:
            desc = dm.group(1).strip().strip('"').strip("'")
    if not desc:
        prob.append("❌ frontmatter 缺 required 欄位 description")
    elif len(desc) > 1024:
        prob.append(f"⚠️ description {len(desc)} 字元，超過 1024 上限")
    elif len(desc) < 120:
        prob.append(f"⚠️ description 只有 {len(desc)} 字元 — 太短會令 agent 唔識 trigger。"
                    "應該包含『做乜』+『幾時用』+ 用戶實際會講嘅字眼。")

    nlines = len(body.split("\n"))
    if nlines > 500:
        prob.append(f"⚠️ SKILL.md body {nlines} 行，超過建議嘅 500 行")
    toks = len(body) // 3
    if toks > 5000:
        prob.append(f"⚠️ SKILL.md body 約 {toks} tokens，超過建議嘅 5000")

    # referenced files 要存在（一層深）
    for ref in re.findall(r"`?(references/[\w\-.]+|assets/[\w\-.]+|scripts/[\w\-.]+)`?", body):
        if not (d / ref).exists():
            prob.append(f"❌ SKILL.md 引用咗 {ref} 但檔案唔存在")
    return prob


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--owner", default=os.environ.get("GITHUB_OWNER", ""))
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPO", ""))
    ap.add_argument("--branch", default="main")
    ap.add_argument("--base-url", default=os.environ.get("SITE_BASE_URL", ""))
    ap.add_argument("--zip", action="store_true")
    a = ap.parse_args()

    r = build(a.owner, a.repo, a.branch, a.base_url, a.zip)
    if r is None:
        return          # 空 corpus → 已印咗說明，exit 0
    print("=" * 68)
    print(f"✅ skill build 完成  {r['now']}")
    print(f"   rules           : {r['rules']}")
    print(f"   documents       : {r['docs']}")
    print(f"   BM25 vocab      : {r['vocab']}")
    print(f"   aggregation     : {r['stats']}")
    print(f"   kb-index.json   : {r['index_bytes']/1024:.0f} KB")
    print("-" * 68)
    for f in r["files"]:
        print(f"   {f}")
    print("-" * 68)
    if r["problems"]:
        print("⚠️ 規格驗證：")
        for p in r["problems"]:
            print("   " + p)
    else:
        print("✅ 規格驗證通過（agentskills.io：name / description / body 長度 / 引用檔案）")
    if r["zip"]:
        print(f"📦 zip: {r['zip']}  ({r['zip'].stat().st_size/1024:.0f} KB)")
    if not (a.owner and a.repo):
        print("\n⚠️  未指定 --owner/--repo → search_kb.py --fetch 會用佔位符。")
        print("    發佈前請重跑：python3 pipeline/build_skill.py --owner YOUR_GH --repo scout-kb")


if __name__ == "__main__":
    main()
