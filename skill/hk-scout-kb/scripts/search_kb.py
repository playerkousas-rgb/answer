#!/usr/bin/env python3
"""
search_kb.py — 童軍公開文件本地搜尋。

**零依賴**：Python 3.8+ stdlib only。呢個係刻意嘅 ——
skill 會被裝入幾十種唔同嘅 agent 環境，你唔可以假設有 jieba / requests / pandas。

兩種 mode：
  local   （預設）搜尋 bundled assets/kb-index.json — 唔使網絡
  --fetch 去 raw.githubusercontent.com 攞逐字原文 — 要網絡

Usage:
  python3 search_kb.py "幼童軍團 年齡"
  python3 search_kb.py "樂行童軍 幾多歲 退" --json
  python3 search_kb.py --rule 2.3.7
  python3 search_kb.py --rule 2.3.7 --fetch
  python3 search_kb.py --list 2.3
"""
from __future__ import annotations
import argparse, json, math, os, re, sys, unicodedata
import urllib.request
import urllib.parse
from collections import Counter
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
INDEX = SKILL_DIR / "assets" / "kb-index.json"

# ---------------------------------------------------------------------------
# 環境變數可以蓋過 repo 位置（用户 fork 咗就改呢個）
# ---------------------------------------------------------------------------
RAW_BASE = os.environ.get(
    "SCOUT_KB_RAW_BASE",
    "https://raw.githubusercontent.com/{owner}/{repo}/{branch}",
)
UA = os.environ.get(
    "SCOUT_KB_UA",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
)

DISCLAIMER_LOCAL = ("本網站為非官方獨立工具，旨在方便成員瀏覽公開資訊，"
                    "與香港童軍總會無任何官方從屬關係。所有通告版權歸香港童軍總會所有。")

STOP = set("""的 了 是 由 至 起 止 及 和 與 或 在 有 為 以 於 不 而 之 其 本 該 上 下 中 個
名 須 應 可 得 被 把 對 從 到 但 若 如 因 所 已 未 一個 我們 你們 他們 什麼 點樣 幾多 邊個
乜嘢 嘅 咗 喺 唔 冇 佢 呢 嗰 呀 啦 嘢 噉 咁 嚟 睇 想 要 會 做 用 問 答""".split())


# ---------------------------------------------------------------------------
# 分詞：CJK bigram + trigram + 完整段。
# ⚠️ 冇用 jieba 係刻意嘅（零依賴）。實測：jieba 會把「小童軍團」切做「小/童軍團」，
#    令「童軍團」誤中「小童軍團」。bigram + 下面嘅 alias substring 匹配反而更可靠。
# ---------------------------------------------------------------------------
def tokenize(text: str) -> list[str]:
    t = unicodedata.normalize("NFKC", str(text or "")).lower()
    t = re.sub(r"[#*|>`\-=[\]()（）\s]+", " ", t)
    out = []
    for m in re.finditer(r"[a-z0-9]+", t):
        if m.group(0) not in STOP:
            out.append(m.group(0))
    for m in re.finditer(r"[\u4e00-\u9fff]+", t):
        s = m.group(0)
        if len(s) >= 2 and s not in STOP:
            out.append(s)
        for i in range(len(s) - 1):
            bg = s[i:i + 2]
            if bg not in STOP:
                out.append(bg)
        for i in range(len(s) - 2):
            tg = s[i:i + 3]
            if tg not in STOP:
                out.append(tg)
    return out


def normq(q: str) -> str:
    s = unicodedata.normalize("NFKC", str(q or "")).strip().lower()
    return re.sub(r"[\s,，。.?？!！:：;；、\"'『』「」()（）]+", "", s)


# ---------------------------------------------------------------------------
class KB:
    def __init__(self, path: Path = INDEX):
        if not path.exists():
            sys.exit(f"❌ 搵唔到 index：{path}\n"
                     f"   呢個 skill 嘅 assets/kb-index.json 缺失或者未 build。")
        d = json.loads(path.read_text(encoding="utf-8"))
        self.meta = d["meta"]
        self.docs = d["documents"]
        self.rules = d["rules"]
        self.postings = d["postings"]
        self.idf = d.get("idf", {})
        self.alias = d.get("alias_routes", {})
        self.stale = d.get("known_stale_sources", [])
        self.N = len(self.rules)
        self.by_id = {r["id"]: r for r in self.rules if r.get("id")}
        self._asorted = None

    # ---------------- alias ----------------
    def alias_sorted(self):
        if self._asorted is None:
            a = self.alias
            self._asorted = {
                "section": sorted(a.get("section_aliases", {}).items(), key=lambda kv: -len(kv[0])),
                "intent": sorted(a.get("intent_aliases", {}).items(), key=lambda kv: -len(kv[0])),
                "generic": sorted(a.get("generic_topics", {}).items(), key=lambda kv: -len(kv[0])),
                "direct": a.get("direct_rules", {}),
            }
        return self._asorted

    def analyse(self, query: str) -> dict:
        A = self.alias_sorted()
        q = normq(query)
        out = {"explicit": [], "sections": [], "expand": [], "boost": set(),
               "exact": set(), "generic": None, "clarify": None, "notes": []}

        for m in re.finditer(r"(\d+\.\d+(?:\.\d+){0,2})", str(query).lower()):
            rid = m.group(1).rstrip(".")
            if rid not in out["explicit"]:
                out["explicit"].append(rid)
        if out["explicit"]:
            out["boost"].update(out["explicit"])
            out["exact"].update(out["explicit"])
            out["notes"].append("query 含明確規條編號 → 直接定位")

        consumed = []
        for term, rule in A["section"]:
            t = normq(term)
            if not t or t not in q:
                continue
            # 最長優先：「童軍」唔可以喺已命中嘅「小童軍」入面重複命中
            if any(t in c for c in consumed):
                continue
            consumed.append(t)
            out["sections"].append({"term": term, "rule": rule})
            out["boost"].add(rule)
            out["exact"].add(rule)
        if out["sections"]:
            out["notes"].append("section: " + ", ".join(
                f'{s["term"]}→{s["rule"]}' for s in out["sections"]))

        for term, expands in A["intent"]:
            if normq(term) and normq(term) in q:
                for e in expands:
                    if e not in out["expand"]:
                        out["expand"].append(e)
        if out["expand"]:
            out["notes"].append("口語 → 原文用字: " + ", ".join(out["expand"]))

        for term, rules in A["generic"]:
            if normq(term) and normq(term) in q:
                out["generic"] = list(rules)
                out["boost"].update(rules)
                out["exact"].update(rules)
                out["clarify"] = (f"「{term}」涉及 {len(rules)} 條規條（{'、'.join(rules)}）。"
                                  f"用戶冇指明邊一個。")
                out["notes"].append(f"generic topic「{term}」→ {len(rules)} 條規條")
                break

        for term, rule in A["direct"].items():
            if normq(term) and normq(term) in q:
                out["boost"].add(rule)
                out["exact"].add(rule)

        # 問年齡但冇指明支部
        if (not out["sections"] and not out["generic"]
                and any(e == "年齡" for e in out["expand"])):
            out["generic"] = ["2.3.3", "2.3.4", "2.3.5", "2.3.6", "2.3.7"]
            out["boost"].update(out["generic"])
            out["exact"].update(out["generic"])
            out["clarify"] = ("用戶問年齡但冇指明支部（小童軍 2.3.3 / 幼童軍 2.3.4 / "
                              "童軍 2.3.5 / 深資童軍 2.3.6 / 樂行童軍 2.3.7）。")
            out["notes"].append("問年齡但冇指明支部 → 返回全部")
        return out

    # ---------------- search ----------------
    def search(self, query: str, top_k: int = 5):
        ana = self.analyse(query)

        # 明確規條編號 → 直接回
        if ana["explicit"]:
            hits = []
            for rid in ana["explicit"]:
                r = self.by_id.get(rid)
                if r:
                    hits.append({"rule": r, "score": 100.0, "why": "明確規條編號"})
                else:
                    # 可能係父規條 → 回所有子條
                    subs = [x for x in self.rules
                            if (x.get("id") or "").startswith(rid + ".")]
                    for r in subs[:top_k]:
                        hits.append({"rule": r, "score": 90.0,
                                     "why": f"{rid} 嘅子規條"})
            if hits:
                return {"query": query, "analysis": ana, "hits": hits[:top_k],
                        "total_candidates": len(hits), "mode": "explicit_rule_id"}

        qtoks = tokenize(query)
        for e in ana["expand"]:
            qtoks += tokenize(e)
        for s in ana["sections"]:
            qtoks += tokenize(s["term"])

        scores = Counter()
        matched_terms = Counter()
        for t in set(qtoks):
            p = self.postings.get(t)
            if not p:
                continue
            matched_terms[t] += 1
            for rid, s in p:
                scores[rid] += s

        # alias boost（**只影響排序，唔過濾**）
        if scores:
            base = max(scores.values()) or 1.0
        else:
            base = 1.0
        for rid in ana["boost"]:
            targets = [rid] + [x.get("id") for x in self.rules
                               if (x.get("id") or "").startswith(rid + ".")]
            for t in targets:
                if t is None:
                    continue
                cur = scores.get(t, 0.0)
                scores[t] = (cur * 2.6 + base * 1.1) if rid in ana["exact"] \
                    else (cur * 1.7 + base * 0.5)

        ranked = [rid for rid, _ in scores.most_common() if rid in self.by_id]
        hits = []
        for rid in ranked[:top_k]:
            r = self.by_id[rid]
            why = []
            if rid in ana["exact"]:
                why.append("alias 精確命中")
            elif rid in ana["boost"]:
                why.append("alias 相關")
            ov = [t for t in matched_terms if t in (r.get("kw") or [])]
            if ov:
                why.append("關鍵字: " + ", ".join(sorted(ov)[:6]))
            hits.append({"rule": r, "score": round(scores[rid], 2),
                         "why": "; ".join(why) or "BM25"})

        return {"query": query, "analysis": ana, "hits": hits,
                "total_candidates": len(ranked), "mode": "bm25",
                "signals": self._signals(query, ana, hits, matched_terms)}

    def _signals(self, query, ana, hits, matched_terms):
        sig = {}
        # ⚠️ 用**完整 CJK 段**計 coverage，唔好用 bigram。
        #    bigram 令「世界童軍總部」嘅「童軍」一定命中 → coverage 永遠 1.0 → OOD 失效。
        segs = sorted(set(re.findall(r"[\u4e00-\u9fff]{2,}", query)))
        in_vocab = [t for t in segs if t in self.idf]
        sig["lexical_coverage"] = {
            "segments": segs,
            "segments_covered": in_vocab,
            "segments_uncovered": [t for t in segs if t not in self.idf],
            "segment_ratio": round(len(in_vocab) / len(segs), 3) if segs else None,
            "matched_bigrams": sorted(t for t in matched_terms if len(t) >= 2)[:12],
            "meaning": "segment_ratio 先係有意義嘅指標。低 = 用戶用嘅完整詞喺 corpus "
                       "冇出現過 — 可能係領域外，亦可能只係同義改述。由你判斷，"
                       "呢個工具唔會替你決定答唔答。",
        }
        if ana["generic"]:
            cover = [h["rule"]["id"] for h in hits if h["rule"]["id"] in ana["generic"]]
            sig["section_conflict"] = {
                "topic_rules": ana["generic"], "candidates_cover": cover,
                "clarify": ana["clarify"],
                "meaning": "呢個問題涉及多個互斥嘅實體。用戶冇指明邊一個。"
                           "你可以並列全部，或者反問。唔好自己揀一個。",
            }
        # weak_match：分數量級（純詞彙方法唯一可靠嘅 OOD 信號）
        if hits and hits[0]["score"] < 30.0:
            sig["weak_match"] = {
                "top_score": hits[0]["score"],
                "meaning": "最高分 candidate 嘅相關度好低。呢個 query 好可能唔屬於 "
                           "已收錄文件嘅範圍，或者用詞同原文差太遠。"
                           "候选仍然照樣返回 —— 由你判斷要唔要用。",
            }
        amended = [h["rule"]["id"] for h in hits if h["rule"].get("amended_by")]
        if amended:
            sig["version_warning"] = {
                "rules": amended,
                "meaning": "呢啲規條有後續通告修訂，引用時要一併列出通告編號同生效日。",
            }
        if not hits:
            sig["no_candidates"] = {
                "meaning": "搵唔到任何候选。唔好用似是而非嘅規條代替 — 如實講明搵唔到。",
                "suggestion": "試 --list <章> 列出所有規條，或者讀 references/rules-index.md",
            }
        return sig

    # ---------------- fetch ----------------
    def raw_url(self, path: str) -> str:
        owner = self.meta.get("owner") or "{owner}"
        repo = self.meta.get("repo") or "{repo}"
        branch = self.meta.get("branch") or "main"
        return RAW_BASE.format(owner=owner, repo=repo, branch=branch) + "/" + path.lstrip("/")

    def fetch_rule(self, rid: str) -> str | None:
        url = self.raw_url(f"por/{rid}.md")
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                if r.status != 200:
                    return None
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:
            print(f"⚠️  fetch 失敗 {url}: {type(e).__name__}: {e}", file=sys.stderr)
            return None


# ---------------------------------------------------------------------------
def print_human(res: dict, kb: KB, fetch: bool):
    ana = res["analysis"]
    print(f"查詢：{res['query']}")
    if ana["notes"]:
        print(f"理解：{' · '.join(ana['notes'])}")
    print(f"候选：{res['total_candidates']} 個　mode={res['mode']}")

    if not res["hits"]:
        print("\n❌ 搵唔到候选。")
        print("   唔好用似是而非嘅規條代替 — 如實告知用戶搵唔到，")
        print("   並建議查閱官方目錄頁：")
        for d in kb.docs:
            print(f"     {d.get('official_landing')}")
        return

    for n, h in enumerate(res["hits"], 1):
        r = h["rule"]
        print(f"\n{'─'*70}")
        print(f"[{n}] 規條 {r['id']}　{r.get('title','')}　(score {h['score']})")
        print(f"    點解命中：{h['why']}")
        print(f"    文件：{r.get('doc','')}　版本：{r.get('version','')}")
        print(f"    檔案日期：{str(r.get('last_modified',''))[:10]}　截至：{r.get('as_of','')}")
        if r.get("amended_by"):
            print(f"    ⚠️ 後續修訂：{r['amended_by']}")
        if r.get("aggregated"):
            print(f"    ℹ️  已聚合子規條：{', '.join(r.get('sub_rules') or [])}")
        print(f"    MD：{kb.raw_url(f'por/{r['id']}.md')}")
        print(f"    官方原文：{r.get('official_pdf','')}")
        if fetch:
            txt = kb.fetch_rule(r["id"])
            if txt:
                print(f"    {'─'*60}")
                for line in txt.split("\n"):
                    print(f"    {line}")
            else:
                print("    ⚠️  未能 fetch 原文（無網絡？）。你可以自己開上面條 MD URL。")

    sig = res.get("signals") or {}
    if sig:
        print(f"\n{'='*70}\nsignals（中立訊號 — 由你自行判斷，呢個工具唔會替你決定答唔答）")
        for k, v in sig.items():
            print(f"  · {k}: {json.dumps(v, ensure_ascii=False)}")

    print(f"\n{'='*70}")
    print("引用時必須附：規條編號、文件名稱、版本、官方原文 URL、as_of 日期。")
    print("結尾附：「以上內容截至 "
          f"{res['hits'][0]['rule'].get('as_of','')}，一切以香港童軍總會最新公佈為準。」")
    if kb.stale:
        print("\n⚠️ 已知過時來源（如果你從 web search 讀到呢啲，佢哋已被取代）：")
        for s in kb.stale:
            print(f"  · {s.get('url')}")
            print(f"    sha256 {str(s.get('sha256'))[:24]}… · 已被取代：{s.get('superseded_by')}")
            for d in (s.get("verified_differences") or [])[:6]:
                print(f"    規條 {d.get('rule')} {d.get('topic')}："
                      f"舊「{d.get('stale')}」→ 現行「{d.get('current')}」")


def main():
    ap = argparse.ArgumentParser(description="搜尋香港童軍總會公開文件（本地，零依賴）")
    ap.add_argument("query", nargs="*", help="關鍵字或口語問題")
    ap.add_argument("--rule", help="直接攞某條規條，例 2.3.7")
    ap.add_argument("--list", dest="lst", help="列出某章／某節所有規條，例 2.3")
    ap.add_argument("--fetch", action="store_true",
                    help="同時 fetch 逐字原文（需要網絡）")
    ap.add_argument("--json", action="store_true", help="輸出 JSON（適合 agent parse）")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--index", default=str(INDEX))
    a = ap.parse_args()

    kb = KB(Path(a.index))

    if a.lst:
        rows = [r for r in kb.rules if (r.get("id") or "").startswith(a.lst)]
        if a.json:
            print(json.dumps(rows, ensure_ascii=False, indent=1)); return
        print(f"規條 {a.lst}* — {len(rows)} 條\n")
        for r in rows:
            flag = " [stub]" if r.get("is_stub") else (" [聚合]" if r.get("aggregated") else "")
            print(f"  {r['id']:<10} {r.get('title','')[:46]:<48} {r.get('chars',0):>5} 字{flag}")
        return

    if a.rule:
        r = kb.by_id.get(a.rule)
        if not r:
            subs = [x for x in kb.rules if (x.get("id") or "").startswith(a.rule + ".")]
            if subs:
                print(f"規條 {a.rule} 本身冇獨立內容，但有 {len(subs)} 條子規條：")
                for x in subs:
                    print(f"  {x['id']:<10} {x.get('title','')[:50]}")
                print(f"\n讀父規條（已聚合子條）：{kb.raw_url(f'por/{a.rule}.md')}")
                if a.fetch:
                    t = kb.fetch_rule(a.rule)
                    if t: print("\n" + t)
                return
            sys.exit(f"❌ 搵唔到規條 {a.rule}。用 --list {a.rule.split('.')[0]} 睇下有乜。")
        if a.json:
            out = dict(r); out["md_url"] = kb.raw_url(f"por/{r['id']}.md")
            if a.fetch: out["verbatim"] = kb.fetch_rule(r["id"])
            print(json.dumps(out, ensure_ascii=False, indent=1)); return
        print(f"規條 {r['id']}　{r.get('title','')}")
        print(f"文件：{r.get('doc','')}　版本：{r.get('version','')}")
        print(f"檔案日期：{str(r.get('last_modified',''))[:10]}　as_of：{r.get('as_of','')}")
        print(f"sha256：{r.get('sha256','')}")
        print(f"MD：{kb.raw_url(f'por/{r['id']}.md')}")
        print(f"官方原文：{r.get('official_pdf','')}")
        if r.get("amended_by"): print(f"⚠️ 後續修訂：{r['amended_by']}")
        if r.get("aggregated"): print(f"ℹ️  已聚合子規條：{', '.join(r.get('sub_rules') or [])}")
        # verbatim 已經 bundle 喺 index → 唔使上網
        if r.get("verbatim"):
            print("\n" + "─"*70 + "\n逐字原文（bundled，離線可用）：\n")
            print(r["verbatim"])
            print("\n" + "─"*70)
            print(f"以上內容截至 {r.get('as_of','')}，一切以香港童軍總會最新公佈為準。")
            print(DISCLAIMER_LOCAL)
        else:
            print("\n⚠️ index 入面冇 verbatim — 請用 --fetch 或者開上面條 MD URL。")
        if a.fetch:
            t = kb.fetch_rule(r["id"])
            if t: print("\n=== 由 GitHub 攞嘅版本（核對用）===\n" + t)
        return

    q = " ".join(a.query).strip()
    if not q:
        ap.print_help(); sys.exit(1)

    res = kb.search(q, top_k=a.top_k)
    if a.json:
        out = dict(res)
        # ⚠️ analysis 入面 boost / exact 係 Python set → JSON 唔 serializable。
        #    呢個 bug 令 --json 完全冇輸出（agent 會以為搵唔到嘢）。
        ana = dict(out["analysis"])
        ana["boost"] = sorted(ana.pop("boost", set()))
        ana["exact"] = sorted(ana.pop("exact", set()))
        out["analysis"] = ana
        for h in out["hits"]:
            h["rule"] = dict(h["rule"])
            h["rule"]["md_url"] = kb.raw_url(f"por/{h['rule']['id']}.md")
            if a.fetch and not h["rule"].get("verbatim"):
                h["rule"]["verbatim"] = kb.fetch_rule(h["rule"]["id"])
        out["meta"] = kb.meta
        out["disclaimer"] = kb.meta.get("disclaimer")
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        print_human(res, kb, a.fetch)


if __name__ == "__main__":
    main()
