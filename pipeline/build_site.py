#!/usr/bin/env python3
"""
build_site.py — 由 corpus/ 入面嘅 MD 生成成個 GitHub repo 結構 + Vercel 前端。

**呢個 script 係唯一嘅 build 步驟。** 跑完之後：
  · repo 入面嘅所有檔案都係 generated（除咗 corpus/ 本身）
  · 直接 git push → GitHub 就係 AI 讀嘅地方
  · Vercel 指向 web/ 就係人用嘅地方

架構：**完全冇 backend。**
  AI  → 讀 GitHub repo 嘅 MD（raw.githubusercontent.com）
  人  → Vercel static site，client-side search
  LLM → 用戶自己帶 API key，瀏覽器直連 provider

Usage:
    python3 pipeline/build_site.py                    # build 全部
    python3 pipeline/build_site.py --check            # 檢查 consistency
    python3 pipeline/build_site.py --base-url https://kb.example.com
"""
from __future__ import annotations
import argparse, datetime, hashlib, json, math, os, re, shutil, sys
from collections import Counter
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "pipeline"))

CORPUS = ROOT / "corpus"
WEB = ROOT / "web"
RAW_BASE = "https://raw.githubusercontent.com/{owner}/{repo}/{branch}"

RULE_HEAD = re.compile(r"^(#{2,4})\s*(?:規條|章條)\s*(\d+(?:\.\d+){0,3})\s*(.*)$")
RULE_INLINE = re.compile(r"(?:規條|章條)\s*(\d+\.\d+(?:\.\d+){0,2})")
CHAPTER_HEAD = re.compile(r"^#\s*第\s*(\d+)\s*章\s*(.*)$")
SITE_NAME = "香港童軍總會公開文件資料庫（非官方）"

DISCLAIMER = ("本網站為非官方獨立工具，旨在方便成員瀏覽公開資訊，"
              "與香港童軍總會無任何官方從屬關係。所有通告版權歸香港童軍總會所有。")


# ==================================================================== frontmatter
def parse_md(path: Path):
    text = path.read_text(encoding="utf-8")
    fm = {}
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            for line in text[3:end].split("\n"):
                m = re.match(r"^([a-z_][a-z0-9_]*):\s*(.*)$", line.strip())
                if m:
                    fm[m.group(1)] = m.group(2).strip().strip('"').strip("'")
            text = text[end + 4:].lstrip("\n")
    return fm, text


# ==================================================================== chunking
def chunk_doc(fm: dict, text: str):
    lines = text.split("\n")
    blocks, cur_head, cur_lines = [], None, []
    chapter, ch_title = None, None
    for l in lines:
        chm = CHAPTER_HEAD.match(l)
        if chm:
            if cur_head is not None:
                blocks.append((chapter, ch_title, cur_head, "\n".join(cur_lines)))
            chapter, ch_title = chm.group(1), chm.group(2).strip()
            cur_head, cur_lines = f"# 第 {chapter} 章 {ch_title}".strip(), []
            continue
        m = RULE_HEAD.match(l)
        if m:
            if cur_head is not None:
                blocks.append((chapter, ch_title, cur_head, "\n".join(cur_lines)))
            cur_head, cur_lines = f"規條 {m.group(2)} {m.group(3)}".strip(), []
        else:
            cur_lines.append(l)
    if cur_head is not None:
        blocks.append((chapter, ch_title, cur_head, "\n".join(cur_lines)))

    chunks = []
    # head 已經係 "規條 2.3.7 <title>" 或者 "# 第 1 章 <title>"，唔帶 markdown '#'。
    # ⚠️ 之前呢度用咗 RULE_HEAD.match("#"*3 + " " + head) 嘅 hack，
    #    結果 (\d+(?:\.\d+){0,3}) 匹配咗 "###" 入面嘅嘢 → rule_id 全部變成 "###"，
    #    332 個 MD 檔全部寫去同一個檔名互相覆蓋。已改用下面嘅直接 regex。
    RID_FROM_HEAD = re.compile(r"^規條\s*(\d+(?:\.\d+){0,3})\s*(.*)$")
    for chapter, ch_title, head, body in blocks:
        body = body.strip()
        m = RID_FROM_HEAD.match(head)
        rid = m.group(1) if m else None
        title = (m.group(2).strip() if m and m.group(2) else head.replace("規條 ", "", 1).strip())
        if not rid and not head.startswith("# 第"):
            continue
        if len(body) < 12 and not rid:
            continue
        # 安全檢查：rule_id 必須似樣，唔可以係符號
        if rid and not re.fullmatch(r"\d+(?:\.\d+){0,3}", rid):
            continue
        chunks.append({
            "rule_id": rid, "heading": head, "title": title,
            "verbatim": body, "chapter": chapter, "chapter_title": ch_title,
        })
    return chunks


# ==================================================================== aggregation
def aggregate(chunks):
    """
    父規條 heading-only（例「規條 2.11 童軍旅財務」正文係空，內容全喺 2.11.1/.2/.3）
    → build 時就聚合，令每個 MD 檔自足。AI 讀 /por/2.11.md 就攞到晒。
    """
    by_rule = {}
    for c in chunks:
        if c["rule_id"]:
            by_rule.setdefault(c["rule_id"], []).append(c)
    stats = Counter()
    for c in chunks:
        rid, verb = c["rule_id"], c["verbatim"].strip()
        # case B: 內容寫晒喺 heading 行（例「規條 1.1.3 總會的銘言為「準備」。」）
        if len(verb) < 60:
            m = re.match(r"^規條\s*\d+(?:\.\d+)*\s*(.+)$", c["heading"])
            if m and len(m.group(1).strip()) > 8:
                rest = m.group(1).strip()
                verb = (rest + "\n" + verb).strip() if verb else rest
                c["verbatim"] = verb
                stats["from_heading"] += 1
        # case A: 聚合子規條
        if rid and len(verb) < 60:
            subs = sorted((k for k in by_rule if k != rid and k.startswith(rid + ".")),
                          key=lambda x: [int(n) for n in x.split(".") if n.isdigit()])
            if subs:
                parts = [c["heading"]]
                if verb:
                    parts.append(verb)
                for k in subs:
                    for sc in by_rule[k]:
                        parts.append(f"### {sc['heading']}\n\n{sc['verbatim']}".strip())
                c["verbatim"] = "\n\n".join(parts)[:12000]
                c["sub_rules"] = subs
                stats["aggregated"] += 1
        # 清除漏入正文嘅 PDF 頁碼
        before = c["verbatim"]
        after = "\n".join(l for l in before.split("\n")
                          if not re.fullmatch(r"\s*\d{1,3}(\s*-\s*\d{1,3})?\s*", l))
        after = re.sub(r"\n{3,}", "\n\n", after).strip()
        if after != before:
            c["verbatim"] = after
            stats["pages_stripped"] += 1
        if len(c["verbatim"].strip()) < 10:
            c["is_stub"] = True
            stats["stub"] += 1
    return stats


# ==================================================================== BM25 (client-side)
STOP = set("的 了 是 由 至 起 止 及 和 與 或 在 有 為 以 於 不 而 之 其 本 該 上 下 中 個 名 須 應 可 得 被 把 對 從 到 但 若 如 因 所 已 未 一個 我們 你們 他們 什麼 點樣 幾多 邊個 乜嘢 嘅 咗 喺 唔 冇 佢 呢 嗰 呀 啦 嘢 噉 咁 嚟 睇 想 要 會 做 用 問 答".split())


def tokenize(text):
    """
    CJK bigram + trigram + 完整段；英文 word。

    ⚠️ **呢個 tokenizer 必須同 app/app.js 同 skill/scripts/search_kb.py 完全一致。**
       三者唔一致 → 同一條問題喺 Vercel 前端同 Agent Skill 會回唔同答案。
       pipeline/static_eval.py 嘅 check_parity() 會 catch drift。

    ⚠️ 刻意**唔用 jieba**：
       (a) 零依賴 — build 唔使裝 third-party package，Vercel / Actions 都簡單
       (b) 實測 jieba 把「小童軍團」切做「小/童軍團」，令「童軍團」誤中「小童軍團」。
           bigram + alias substring（最長優先）反而更可靠。
    """
    t = re.sub(r"[#*|>`\-=[\]()（）\s]+", " ", str(text or "").lower())
    out = []
    for m in re.finditer(r"[a-z0-9]+", t):
        if m.group(0) not in STOP:
            out.append(m.group(0))
    for m in re.finditer(r"[\u4e00-\u9fff]+", t):
        seg = m.group(0)
        if len(seg) >= 2 and seg not in STOP:
            out.append(seg)
        for i in range(len(seg) - 1):
            bg = seg[i:i + 2]
            if bg not in STOP:
                out.append(bg)
        for i in range(len(seg) - 2):
            tg = seg[i:i + 3]
            if tg not in STOP:
                out.append(tg)
    return out


def build_bm25(chunks):
    docs = [tokenize((c["heading"] + " " + c["heading"] + " " + c["verbatim"])) for c in chunks]
    N = len(docs)
    df = Counter()
    for d in docs:
        for t in set(d):
            df[t] += 1
    avgdl = sum(len(d) for d in docs) / max(N, 1)
    idf = {t: round(math.log(1 + (N - n + 0.5) / (n + 0.5)), 3) for t, n in df.items()}
    k1, b = 1.5, 0.75
    postings = {}
    for i, d in enumerate(docs):
        tf = Counter(d)
        dl = len(d)
        for t, f in tf.items():
            postings.setdefault(t, []).append(
                [i, round(idf[t] * f * (k1 + 1) / (f + k1 * (1 - b + b * dl / avgdl)), 3)])
    for t in postings:
        postings[t].sort(key=lambda x: -x[1])
        postings[t] = postings[t][:60]
    return postings, idf, docs, {"k1": k1, "b": b, "N": N,
                                 "avgdl": round(avgdl, 2), "vocab": len(idf)}


# ==================================================================== rule MD
def rule_md(c, doc, base_url, owner_repo_branch):
    fm = {
        "rule_id": c["rule_id"],
        "document": doc["title"],
        "doc_id": doc["doc_id"],
        "version_label": doc.get("version_label", ""),
        "source_sha256": doc.get("source_sha256", ""),
        "source_last_modified": doc.get("source_last_modified", ""),
        "official_pdf": doc.get("source_url") or doc.get("official_pdf", ""),
        "official_landing": doc.get("official_landing", ""),
        "as_of": doc.get("as_of", ""),
        "status": "current",
    }
    if c.get("sub_rules"):
        fm["aggregated_from_sub_rules"] = c["sub_rules"]
    if c.get("amended_by"):
        fm["amended_by"] = c["amended_by"]
    L = ["---"]
    for k, v in fm.items():
        L.append(f'{k}: {json.dumps(v, ensure_ascii=False)}')
    L += ["---", "", f"# {c['heading']}", "", c["verbatim"].strip(), "", "---", ""]
    if c.get("sub_rules"):
        L.append(f"> 本頁已聚合子規條：{', '.join(c['sub_rules'])}")
        L.append("")
    if c.get("amended_by"):
        L.append(f"> ⚠️ 本規條有後續修訂通告：{c['amended_by']}")
        L.append("")
    L += [f"以上內容截至 {doc.get('as_of','')}，一切以香港童軍總會最新公佈為準。", "",
          f"官方原文：{doc.get('source_url') or doc.get('official_pdf','')}", ""]
    if owner_repo_branch:
        raw = RAW_BASE.format(**owner_repo_branch)
        L.append(f"Raw MD（AI 可直接 fetch）：`{raw}/por/{c['rule_id']}.md`")
        L.append("")
    L.append(DISCLAIMER)
    L.append("")
    return "\n".join(L)


# ==================================================================== main build
def build(base_url="", owner="", repo="", branch="main"):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    orb = {"owner": owner, "repo": repo, "branch": branch} if owner and repo else None

    if WEB.exists():
        shutil.rmtree(WEB)
    (WEB / "por").mkdir(parents=True)
    (WEB / "circulars").mkdir(parents=True)
    (WEB / "api").mkdir(parents=True)
    (ROOT / "por").mkdir(exist_ok=True)          # repo 根都有 MD（AI 主要讀呢度）
    (ROOT / "circulars").mkdir(exist_ok=True)

    docs, all_chunks, doc_chunks = [], [], {}
    skipped = []
    for path in sorted(CORPUS.rglob("*.md")):
        rel = path.relative_to(CORPUS)
        if any(p.startswith("_") for p in rel.parts):
            continue
        # ⚠️ corpus/README.md 呢類說明檔唔係 corpus 文件。
        #    實測：冇呢個檢查會多出一條「規條」，令 guard 報 333 vs 332。
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
        chunks = chunk_doc(fm, text)
        doc_chunks[did] = chunks
        all_chunks += [{**c, "doc_id": did} for c in chunks]
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
            "status": fm.get("status", "current"),
            "language": fm.get("language", "zh-HK"),
            "as_of": now[:10],
            "md_path": f"md/{did}.md",
            "raw_md": (RAW_BASE.format(**orb) + f"/md/{did}.md") if orb else None,
            "chunks": len(chunks),
        })

    if not docs:
        # ⚠️ **唔好 raise** —— 用戶嘅部署次序係「先建架構 → deploy Vercel → 之後先加內容」。
        #    空 corpus 係一個合法狀態，要生成一個可 deploy 嘅 shell。
        #    （之前呢度直接 fail，令 Vercel 冇 web/ 可以 deploy。）
        return None

    stats = aggregate(all_chunks)

    # ---- 寫每條規條一個 MD（repo 根 + web/por）----
    rules = []
    for c in all_chunks:
        if not c["rule_id"]:
            continue
        body = rule_md(c, next(d for d in docs if d["doc_id"] == c["doc_id"]), base_url, orb)
        for target in (ROOT / "por" / f'{c["rule_id"]}.md', WEB / "por" / f'{c["rule_id"]}.md'):
            target.write_text(body, encoding="utf-8")
        rules.append({
            "id": c["rule_id"],
            "title": c["title"] or c["heading"].replace("規條 ", "", 1).strip(),
            "heading": c["heading"],
            "chapter": c.get("chapter"),
            "doc_id": c["doc_id"],
            "chars": len(c["verbatim"]),
            "aggregated": bool(c.get("sub_rules")),
            "sub_rules": c.get("sub_rules"),
            "is_stub": bool(c.get("is_stub")),
            "md": f"/por/{c['rule_id']}.md",
            "raw_md": (RAW_BASE.format(**orb) + f"/por/{c['rule_id']}.md") if orb else None,
            "amended_by": c.get("amended_by"),
        })

    # ---- 全文 MD ----
    for path in sorted(CORPUS.rglob("*.md")):
        if any(p.startswith("_") for p in path.relative_to(CORPUS).parts):
            continue
        if path.name.lower() in ("readme.md", "index.md", "changelog.md"):
            continue
        fm, text = parse_md(path)
        did = fm.get("doc_id") or path.stem
        if did not in [d["doc_id"] for d in docs]:
            continue
        full = path.read_text(encoding="utf-8")
        (ROOT / "md").mkdir(exist_ok=True)
        (ROOT / "md" / f"{did}.md").write_text(full, encoding="utf-8")
        (WEB / "md").mkdir(exist_ok=True)
        (WEB / "md" / f"{did}.md").write_text(full, encoding="utf-8")

    # ---- client-side BM25 index ----
    postings, idf, tokenized, params = build_bm25(all_chunks)
    chunks_out = []
    for i, c in enumerate(all_chunks):
        d = next(x for x in docs if x["doc_id"] == c["doc_id"])
        chunks_out.append({
            "id": i,
            "rule_id": c["rule_id"],
            "heading": c["heading"],
            "doc_id": c["doc_id"],
            "doc_title": d["title"],
            "version_label": d.get("version_label", ""),
            "source_sha256": d.get("source_sha256", ""),
            "source_last_modified": d.get("source_last_modified", ""),
            "official_pdf": d.get("source_url", ""),
            "official_landing": d.get("official_landing", ""),
            "as_of": d.get("as_of", ""),
            "md_url": f"/por/{c['rule_id']}.md" if c["rule_id"] else f"/md/{c['doc_id']}.md",
            "raw_md": (RAW_BASE.format(**orb) + f"/por/{c['rule_id']}.md")
                      if (orb and c["rule_id"]) else None,
            "amended_by": c.get("amended_by"),
            "aggregated": bool(c.get("sub_rules")),
            "is_stub": bool(c.get("is_stub")),
            "chars": len(c["verbatim"]),
            "verbatim": c["verbatim"],
        })
    (WEB / "api").mkdir(exist_ok=True)
    (WEB / "api" / "index.json").write_text(json.dumps({
        "build": {"generated": now, "params": params,
                  "build_id": hashlib.sha256(now.encode()).hexdigest()[:16],
                  "tokenizer": "cjk-bigram+trigram (stdlib, 同 app.js / search_kb.py 一致)"},
        "chunks": chunks_out, "postings": postings, "idf": idf,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # ---- alias routes（client-side 查詢理解）----
    alias_p = ROOT / "data" / "alias_routes.json"
    alias = json.loads(alias_p.read_text(encoding="utf-8")) if alias_p.exists() else {}
    alias.pop("_comment", None)
    for k in list(alias):
        if isinstance(alias[k], dict):
            alias[k].pop("_comment", None)
    (WEB / "api" / "alias_routes.json").write_text(
        json.dumps(alias, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # ---- manifest ----
    manifest = {
        "name": SITE_NAME,
        "description": ("把香港童軍總會已公開嘅文件整理成 AI 可直接讀取嘅 Markdown。"
                        "每條規條一個穩定 URL，逐字原文，完整出處"
                        "（版本、sha256、檔案日期、官方 PDF 連結）。"),
        "audience": "AI agents and LLMs (primary); humans via the Vercel frontend",
        "generated": now,
        "base_url": base_url,
        "repository": {"owner": owner, "repo": repo, "branch": branch,
                       "raw_base": RAW_BASE.format(**orb) if orb else None},
        "licence": DISCLAIMER,
        "disclaimer": DISCLAIMER,
        "how_to_consume": {
            "for_ai": [
                f"讀 {RAW_BASE.format(**orb) + '/llms.txt' if orb else '/llms.txt'} 攞概覽同已知過時來源",
                f"每條規條：{RAW_BASE.format(**orb) + '/por/<rule_id>.md' if orb else '/por/<rule_id>.md'}",
                f"成份文件：{RAW_BASE.format(**orb) + '/md/POR.md' if orb else '/md/POR.md'}",
                "規條索引：/index.json（machine-readable，含所有 rule id + title + URL）",
                "所有檔案都係純 Markdown / JSON，冇 auth、冇 rate limit",
            ],
            "for_humans": [base_url or "(Vercel URL)", "client-side 搜尋，冇 server"],
            "for_llm_chat": "用戶自己帶 API key，瀏覽器直連 provider。本站唔持有 key。",
        },
        "documents": docs,
        "rule_count": len(rules),
        "chunk_count": len(chunks_out),
        "build_stats": dict(stats),
        "endpoints": {
            "index_json": "/index.json",
            "llms_txt": "/llms.txt",
            "llms_full_txt": "/llms-full.txt",
            "rules": "/por/<rule_id>.md",
            "full_doc": "/md/<doc_id>.md",
            "search_index": "/api/index.json",
            "alias_routes": "/api/alias_routes.json",
            "sitemap": "/sitemap.xml",
            "robots": "/robots.txt",
        },
        "known_stale_sources": json.loads(
            (ROOT / "data" / "known_stale.json").read_text(encoding="utf-8")
        ) if (ROOT / "data" / "known_stale.json").exists() else [],
    }
    (ROOT / "index.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    (WEB / "api" / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---- llms.txt ----
    L = ["# 香港童軍總會公開文件資料庫（非官方）", "",
         f"> {manifest['description']}", "", DISCLAIMER, "",
         "## 呢個 repo 係俾 AI 讀嘅", "",
         "所有檔案都係純 Markdown / JSON，冇 auth、冇 rate limit。你可以直接 fetch：", ""]
    if orb:
        rb = RAW_BASE.format(**orb)
        L += [f"- 概覽：`{rb}/llms.txt`（呢個檔案）",
              f"- 全 corpus 單一檔案：`{rb}/llms-full.txt`",
              f"- machine-readable 索引：`{rb}/index.json`",
              f"- 每條規條：`{rb}/por/<rule_id>.md`　例 `{rb}/por/2.3.7.md`",
              f"- 成份文件：`{rb}/md/POR.md`", ""]
    else:
        L += ["- 全 corpus 單一檔案：`/llms-full.txt`",
              "- machine-readable 索引：`/index.json`",
              "- 每條規條：`/por/<rule_id>.md`　例 `/por/2.3.7.md`",
              "- 成份文件：`/md/POR.md`", ""]
    if manifest["known_stale_sources"]:
        L += ["## ⚠️ 已知過時來源", "",
              "以下檔案嘅 URL 仍然 live，搜尋引擎仍然索引緊，但已被官方目錄頁取代。",
              "如果你喺 web search 讀到呢啲，請改用現行版本：", ""]
        for s in manifest["known_stale_sources"]:
            L += [f"- `{s['url']}`",
                  f"  - sha256 `{s.get('sha256')}` · last-modified {s.get('last_modified')} · {s.get('pages')} 頁",
                  f"  - 已被取代：{s.get('superseded_by')}"]
            for d in s.get("verified_differences", []):
                L.append(f"  - 規條 {d['rule']} {d['topic']}：舊「{d['stale']}」→ 現行「{d['current']}」")
            L.append("")
    L += ["## 文件", ""]
    for d in docs:
        L.append(f"- **{d['title']}** — {d.get('version_label') or ''}"
                 f" · 檔案日期 {str(d.get('source_last_modified'))[:10]}"
                 f" · sha256 `{str(d.get('source_sha256'))[:16]}…`"
                 f" · {d['chunks']} 條規條")
        if d.get("source_url"):
            L.append(f"  - 官方原文：{d['source_url']}")
        if d.get("official_landing"):
            L.append(f"  - 官方目錄頁：{d['official_landing']}")
        L.append("")
    L += [f"共 {len(rules)} 條規條。完整索引：`/index.json`", "",
          "## 引用格式", "",
          "引用時請附：規條編號、文件名稱、版本／檔案日期、官方原文 URL。",
          "所有 verbatim 內容係逐字引用，未經改寫。", "",
          f"資料截至 {now[:10]}，一切以香港童軍總會最新公佈為準。", ""]
    txt = "\n".join(L)
    (ROOT / "llms.txt").write_text(txt, encoding="utf-8")
    (WEB / "llms.txt").write_text(txt, encoding="utf-8")

    # ---- llms-full.txt ----
    full = ["# 香港童軍總會公開文件資料庫（非官方）— 全 corpus", "",
            DISCLAIMER, "", f"生成時間 {now}", "", "---", ""]
    for d in docs:
        p = ROOT / "md" / f"{d['doc_id']}.md"
        if p.exists():
            full.append(p.read_text(encoding="utf-8"))
            full.append("\n---\n")
    ftxt = "\n".join(full)
    (ROOT / "llms-full.txt").write_text(ftxt, encoding="utf-8")
    (WEB / "llms-full.txt").write_text(ftxt, encoding="utf-8")

    # ---- sitemap / robots ----
    urls = ["/", "/llms.txt", "/index.json"]
    urls += [f"/por/{r['id']}.md" for r in rules]
    urls += [f"/md/{d['doc_id']}.md" for d in docs]
    sm = ['<?xml version="1.0" encoding="UTF-8"?>',
          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        sm.append(f"  <url><loc>{(base_url or '')}{u}</loc></url>")
    sm.append("</urlset>")
    (WEB / "sitemap.xml").write_text("\n".join(sm), encoding="utf-8")
    (WEB / "robots.txt").write_text(
        "User-agent: *\nAllow: /\n\n"
        f"Sitemap: {base_url or ''}/sitemap.xml\n\n"
        "# AI crawlers: 呢個站嘅內容係刻意公開俾你讀嘅。\n"
        "# 建議先讀 /llms.txt 同 /index.json。\n", encoding="utf-8")

    # ---- 拷貝 UI（index.html + app.js）----
    ui = ROOT / "app" / "ui.html"
    if ui.exists():
        (WEB / "index.html").write_text(ui.read_text(encoding="utf-8"), encoding="utf-8")
    js = ROOT / "app" / "app.js"
    if js.exists():
        (WEB / "app.js").write_text(js.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        raise SystemExit("❌ 缺少 app/app.js — client-side 檢索需要佢")

    # ---- 輸出驗證 ----
    # 教訓：之前 rule_id 全部變成 "###"，332 個 MD 寫去同一個檔名互相覆蓋，
    #       但 build 照樣 report「rules: 332」✅。**唔好信自己嘅 counter，要數實際檔案。**
    written = len(list((WEB / "por").glob("*.md")))
    if written != len(rules):
        # 講明係邊條撞車 / 非法 —— 淨係話「不一致」冇用，實測證明要指名道姓
        cnt = Counter(r["id"] for r in rules)
        dupes = {k: v for k, v in cnt.items() if v > 1}
        illegal = [r["id"] for r in rules
                   if not re.fullmatch(r"\d+(?:\.\d+){0,3}", str(r["id"]))]
        msg = [f"❌ BUILD 不一致：report 咗 {len(rules)} 條規條，"
               f"但 web/por/ 實際只有 {written} 個 MD 檔。"]
        if dupes:
            msg.append(f"   撞車嘅 rule_id（多條規條寫去同一個檔名，互相覆蓋）：")
            for k, v in sorted(dupes.items(), key=lambda kv: -kv[1])[:10]:
                heads = [r["heading"][:52] for r in rules if r["id"] == k][:3]
                msg.append(f"     · {k!r} × {v}  ← {heads}")
        if illegal:
            msg.append(f"   格式非法嘅 rule_id：{illegal[:10]}")
        if not dupes and not illegal:
            msg.append("   原因未明 — 請檢查 corpus/ 入面係咪有唔應該被當成 corpus 嘅 MD"
                       "（例：README.md、無 frontmatter 嘅檔）。")
        raise SystemExit("\n".join(msg))
    bad_ids = [r["id"] for r in rules if not re.fullmatch(r"\d+(?:\.\d+){0,3}", r["id"])]
    if bad_ids:
        raise SystemExit(f"❌ rule_id 格式唔啱：{bad_ids[:8]}")
    for f in (WEB / "api" / "index.json", WEB / "app.js", WEB / "index.html",
              WEB / "llms.txt", ROOT / "llms.txt", ROOT / "index.json"):
        if not f.exists() or f.stat().st_size < 100:
            raise SystemExit(f"❌ 缺少或過細嘅輸出檔：{f}")

    return {"docs": docs, "rules": rules, "chunks": len(chunks_out), "skipped": skipped,
            "stats": dict(stats), "params": params, "now": now,
            "llms_full_bytes": len(ftxt.encode()),
            "index_bytes": (WEB / "api" / "index.json").stat().st_size}



# ===========================================================================
# SHELL MODE — corpus 空嘅時候都要 deploy 到 Vercel
# ===========================================================================
# 用戶嘅部署次序係：先建架構 → deploy Vercel → 之後先加內容。
# 所以 web/ 必須喺**冇任何內容**嘅情況下都存在、都可 deploy、都唔會壞。
#
# ⚠️ 實測：原本 loadKB() fetch api/index.json，404 → throw → UI 直接壞晒，
#    用戶見到一片空白。shell mode 生成一個 chunks=[] 嘅 index.json，
#    UI 就顯示「未收錄任何文件」而唔係報錯。
def write_shell(base_url="", owner="", repo="", branch="main", now=""):
    """生成 web/ 骨架 + repo 根嘅空 placeholder。完全唔需要 corpus 內容。"""
    import datetime as _dt
    now = now or _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    rb = RAW_BASE.format(owner=owner or "YOUR_OWNER", repo=repo or "YOUR_REPO",
                         branch=branch)
    WEB.mkdir(parents=True, exist_ok=True)
    for sub in ("api", "por", "md"):
        (WEB / sub).mkdir(parents=True, exist_ok=True)
    # 空目錄 git 追唔到 → 加 .gitkeep
    for sub in ("por", "md"):
        (WEB / sub / ".gitkeep").write_text("", encoding="utf-8")
        (ROOT / sub).mkdir(parents=True, exist_ok=True)
        (ROOT / sub / ".gitkeep").write_text("", encoding="utf-8")

    ui = ROOT / "app" / "ui.html"
    if ui.exists():
        (WEB / "index.html").write_text(ui.read_text(encoding="utf-8"), encoding="utf-8")
    js = ROOT / "app" / "app.js"
    if js.exists():
        (WEB / "app.js").write_text(js.read_text(encoding="utf-8"), encoding="utf-8")

    empty_index = {
        "build": {
            "generated": now, "build_id": "shell",
            "tokenizer": "cjk-bigram+trigram (stdlib, 同 app.js / search_kb.py 一致)",
            "params": {"k1": 1.5, "b": 0.75, "N": 0, "avgdl": 0, "vocab": 0},
            "aggregation": {},
            "empty": True,
            "note": "corpus/ 係空嘅 — 呢個係 skeleton build。"
                    "放 MD 入 corpus/ 再跑 build_site.py 就會有內容。",
        },
        "documents": [], "chunks": [], "postings": {}, "idf": {}, "routes": [],
    }
    _dump(WEB / "api" / "index.json", empty_index)
    _dump(ROOT / "index.json", empty_index)

    alias_p = ROOT / "data" / "alias_routes.json"
    alias = json.loads(alias_p.read_text(encoding="utf-8")) if alias_p.exists() else {}
    _dump(WEB / "api" / "alias_routes.json", alias)

    manifest = {
        "name": SITE_NAME,
        "generated": now,
        "mode": "shell",
        "rule_count": 0,
        "document_count": 0,
        "disclaimer": DISCLAIMER,
        "corpus": {"documents": [], "build_id": "shell", "empty": True},
        "repository": {"owner": owner, "repo": repo, "branch": branch,
                       "raw_base": rb},
        "site": {"base_url": base_url},
        "ai_entrypoints": [
            rb + "/llms.txt", rb + "/index.json", rb + "/por/<rule_id>.md",
            rb + "/md/<doc_id>.md", rb + "/llms-full.txt",
        ],
        "note": "呢個係 skeleton — 未收錄任何文件。AI 讀到嘅會係空索引。"
                "內容加入之後呢個 manifest 會自動更新。",
    }
    _dump(WEB / "api" / "manifest.json", manifest)

    # llms.txt / llms-full.txt：空 corpus 都要存在，但要講明未有內容
    (WEB / "llms.txt").write_text(f"""# {SITE_NAME}

> ⚠️ **呢個資料庫而家未收錄任何文件。**
> 佢係一個已部署好嘅 skeleton — 架構完成，內容待加入。

{DISCLAIMER}

## 結構（內容加入之後會生效）

- 概覽：`{rb}/llms.txt`（呢個檔案）
- 全 corpus 單一檔案：`{rb}/llms-full.txt`
- machine-readable 索引：`{rb}/index.json`
- 每條規條：`{rb}/por/<rule_id>.md`
- 成份文件：`{rb}/md/<doc_id>.md`

每個 MD 檔都會有 frontmatter，包含完整出處：
`rule_id` · `document` · `version_label` · `source_sha256` ·
`source_last_modified` · `official_pdf` · `official_landing` · `as_of` · `status`

## 文件

（未有）
""", encoding="utf-8")
    (ROOT / "llms.txt").write_text((WEB / "llms.txt").read_text(encoding="utf-8"),
                                    encoding="utf-8")
    for f in (WEB / "llms-full.txt", ROOT / "llms-full.txt"):
        f.write_text(f"# {SITE_NAME} — 全 corpus\n\n"
                     f"> ⚠️ 未收錄任何文件。\n\n{DISCLAIMER}\n",
                     encoding="utf-8")

    # sitemap：冇內容就只列首頁
    (WEB / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f'  <url><loc>{(base_url or "https://example.vercel.app").rstrip("/")}/</loc>'
        '<changefreq>weekly</changefreq></url>\n</urlset>\n', encoding="utf-8")
    (WEB / "robots.txt").write_text(
        "User-agent: *\nAllow: /\n\n"
        "User-agent: GPTBot\nAllow: /\n\n"
        "User-agent: ClaudeBot\nAllow: /\n\n"
        "User-agent: Google-Extended\nAllow: /\n\n"
        f"Sitemap: {(base_url or '').rstrip('/')}/sitemap.xml\n", encoding="utf-8")
    (WEB / ".nojekyll").write_text("", encoding="utf-8")
    return {"mode": "shell", "rule_count": 0, "now": now}


def _dump(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8")



def write_vercel_files(base_url, owner, repo, branch):
    """Vercel deploy 需要嘅設定檔"""
    (WEB / "vercel.json").write_text(json.dumps({
        "$schema": "https://openapi.vercel.sh/vercel.json",
        "cleanUrls": True,
        "trailingSlash": False,
        "headers": [
            {"source": "/(.*)", "headers": [
                {"key": "X-Content-Type-Options", "value": "nosniff"},
                {"key": "Access-Control-Allow-Origin", "value": "*"},
                {"key": "Referrer-Policy", "value": "strict-origin-when-cross-origin"}]},
            {"source": "/por/(.*)\\.md", "headers": [
                {"key": "Content-Type", "value": "text/markdown; charset=utf-8"},
                {"key": "Cache-Control", "value": "public, max-age=3600"}]},
            {"source": "/api/(.*)\\.json", "headers": [
                {"key": "Content-Type", "value": "application/json; charset=utf-8"},
                {"key": "Cache-Control", "value": "public, max-age=300"}]},
            {"source": "/llms.txt", "headers": [
                {"key": "Content-Type", "value": "text/plain; charset=utf-8"}]},
            {"source": "/llms-full.txt", "headers": [
                {"key": "Content-Type", "value": "text/plain; charset=utf-8"},
                {"key": "Cache-Control", "value": "public, max-age=3600"}]},
        ],
        "rewrites": [{"source": "/por/:rule", "destination": "/por/:rule"}],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    (WEB / ".nojekyll").write_text("", encoding="utf-8")


def write_repo_files(base_url, owner, repo, branch):
    """
    ⚠️ 呢個 function 而家係**刻意空置**嘅。

    之前佢會生成兩個檔案，兩個都係錯嘅：

    1. `REPO_README.md` — 同手寫嘅 `README.md` 內容重複，而且我 rm 咗佢之後
       每次 build 又生出返嚟。**手寫檔案唔應該被 build 覆蓋。**

    2. `.gitignore` — 我手寫咗一份更完整嘅（列出乜嘢係交付物、乜嘢係中間產物），
       build 又用返舊版蓋走佢。同一個問題。

    教訓：**generated 同 hand-written 檔案唔可以共用路徑。**
    build 只應該寫入佢自己擁有嘅路徑：por/ md/ web/ llms*.txt index.json
    """
    return None



def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=os.environ.get("SITE_BASE_URL", ""))
    ap.add_argument("--owner", default=os.environ.get("GITHUB_OWNER", ""))
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPO", ""))
    ap.add_argument("--branch", default="main")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()

    r = build(a.base_url, a.owner, a.repo, a.branch)
    write_vercel_files(a.base_url, a.owner, a.repo, a.branch)
    write_repo_files(a.base_url, a.owner, a.repo, a.branch)

    if r is None:
        # ---------- SHELL MODE ----------
        sh = write_shell(a.base_url, a.owner, a.repo, a.branch)
        print("=" * 66)
        print(f"⚙️  SHELL build（corpus/ 係空嘅）  {sh['now']}")
        print("-" * 66)
        print("   生成咗一個**可 deploy 但未有內容**嘅靜態站：")
        print("     web/index.html          前端（會顯示「未收錄任何文件」）")
        print("     web/app.js              client-side 檢索邏輯")
        print("     web/api/index.json      chunks=[] 嘅空索引")
        print("     web/api/manifest.json   mode=\"shell\"")
        print("     web/api/alias_routes.json  查詢理解表（已有）")
        print("     web/llms.txt            講明未有內容 + 結構說明")
        print("     web/vercel.json · robots.txt · sitemap.xml · .nojekyll")
        print("     index.json · llms.txt · llms-full.txt   （repo 根 placeholder）")
        print("-" * 66)
        print("   ✅ 呢個狀態可以直接 deploy 去 Vercel（Root Directory = web）")
        print("   → 之後放 MD 入 corpus/ 再跑同一條 command，就會變成 full build。")
        print("=" * 66)
        return

    print("=" * 66)
    print(f"✅ build 完成  {r['now']}")
    print(f"   documents      : {len(r['docs'])}")
    if r.get("skipped"):
        print(f"   跳過           : {len(r['skipped'])} 個檔案")
        for x in r["skipped"][:6]:
            print(f"                  · {x}")
    print(f"   rules (MD 檔)  : {len(r['rules'])}")
    print(f"   chunks         : {r['chunks']}")
    print(f"   BM25 vocab     : {r['params']['vocab']}  avgdl={r['params']['avgdl']}")
    print(f"   aggregation    : {r['stats']}")
    print(f"   web/api/index.json : {r['index_bytes']/1e6:.2f} MB")
    print(f"   llms-full.txt      : {r['llms_full_bytes']/1e6:.2f} MB")
    print("-" * 66)
    print("   repo 根（AI 讀）  : por/ md/ llms.txt llms-full.txt index.json")
    print("   web/（Vercel）    : index.html por/ md/ api/ llms.txt sitemap.xml vercel.json")
    print("=" * 66)
    if not (a.owner and a.repo):
        print("⚠️  未指定 --owner/--repo → MD 入面嘅 raw GitHub URL 未生成。")
        print("    deploy 前請重跑：python3 pipeline/build_site.py \\")
        print("      --base-url https://YOUR.vercel.app --owner YOUR_GH --repo YOUR_REPO")


if __name__ == "__main__":
    main()

