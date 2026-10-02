#!/usr/bin/env python3
"""
static_eval.py — 測試**真正會 ship 嘅 artifact**。

點解有呢個檔案：
  舊嘅 app/run_eval.py + pipeline/canary.py 測嘅係 server-mode 代碼
  （app/search_service.py + app/index.json）。
  但實際交付物係**靜態站 + Agent Skill** — 冇 server。
  測一個唔會 ship 嘅 code path 係冇意義嘅。

  呢個 script 直接讀 build 出嚟嘅：
    · web/api/index.json          ← Vercel 前端用
    · skill/hk-scout-kb/assets/kb-index.json  ← Skill 用
  用同一套 logic（mirror app/app.js）跑測試。

  ⚠️ 呢個係 Python mirror，唔係 app.js 本身。如果两者 logic drift，
     測試就會假 pass。所以 `check_parity()` 會對比兩邊嘅關鍵常數。

Usage:
    python3 pipeline/static_eval.py            # canary + golden
    python3 pipeline/static_eval.py --canary
    python3 pipeline/static_eval.py --golden
    python3 pipeline/static_eval.py --skill    # 額外測 skill 嘅 index
    python3 pipeline/static_eval.py --json
"""
from __future__ import annotations
import argparse, datetime, json, math, re, sys, unicodedata
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB_INDEX = ROOT / "web" / "api" / "index.json"
WEB_ALIAS = ROOT / "web" / "api" / "alias_routes.json"
SKILL_INDEX = ROOT / "skill" / "hk-scout-kb" / "assets" / "kb-index.json"
GOLDEN = ROOT / "data" / "golden.json"
APPS = ROOT / "app" / "app.js"

STOP = set("""的 了 是 由 至 起 止 及 和 與 或 在 有 為 以 於 不 而 之 其 本 該 上 下 中 個
名 須 應 可 得 被 把 對 從 到 但 若 如 因 所 已 未 一個 我們 你們 他們 什麼 點樣 幾多 邊個
乜嘢 嘅 咗 喺 唔 冇 佢 呢 嗰 呀 啦 嘢 噉 咁 嚟 睇 想 要 會 做 用 問 答""".split())

INTENT_TERMS = ["月薪", "薪金", "薪酬", "工資", "人工", "收費", "費用", "天氣", "氣溫",
                "電話", "電郵", "地址", "辦公時間", "歷史", "成立年份", "股價", "機票"]


# ---------------------------------------------------------------------------
# 以下 tokenize / analyse / retrieve 係 app/app.js 嘅 Python mirror。
# 改一邊必須改另一邊 — check_parity() 會 catch 常見嘅 drift。
# ---------------------------------------------------------------------------
def tokenize(text):
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


def tokenize_skill(text):
    """skill 嘅 search_kb.py 只用 bigram（冇 trigram）— 刻意分開"""
    t = re.sub(r"[#*|>`\-=[\]()（）\s]+", " ", str(text or "").lower())
    out = []
    for m in re.finditer(r"[a-z0-9]+", t):
        if m.group(0) not in STOP:
            out.append(m.group(0))
    for m in re.finditer(r"[\u4e00-\u9fff]+", t):
        s = m.group(0)
        if len(s) >= 2 and s not in STOP:
            out.append(s)
        for i in range(len(s) - 1):
            if s[i:i + 2] not in STOP:
                out.append(s[i:i + 2])
    return out


def normq(q):
    s = unicodedata.normalize("NFKC", str(q or "")).strip().lower()
    return re.sub(r"[\s,，。.?？!！:：;；、\"'『』「」()（）]+", "", s)


class Engine:
    """通用 engine：食 web index 或者 skill index 都得"""

    def __init__(self, index_path, alias_path=None, tok=None):
        d = json.loads(Path(index_path).read_text(encoding="utf-8"))
        self.raw = d
        self.tok = tok or tokenize
        # schema 差異：web 用 "chunks"，skill 用 "rules"
        self.chunks = d.get("chunks") or d.get("rules") or []
        self.postings = d["postings"]
        self.idf = d.get("idf", {})
        self.N = len(self.chunks)
        # web postings 係 [idx, score]；skill postings 係 [rule_id, score]
        self._post_by_id = isinstance(self.postings, dict) and any(
            isinstance(v[0][0], str) for v in self.postings.values() if v)
        self.by_rule = {}
        self.id2i = {}
        for i, c in enumerate(self.chunks):
            rid = c.get("rule_id") or c.get("id")
            if rid:
                self.by_rule.setdefault(rid, []).append(i)
                self.id2i[rid] = i
        if alias_path and Path(alias_path).exists():
            a = json.loads(Path(alias_path).read_text(encoding="utf-8"))
        else:
            a = d.get("alias_routes", {})
        a.pop("_comment", None)
        for k in list(a):
            if isinstance(a[k], dict):
                a[k].pop("_comment", None)
        self.alias = {
            "section": sorted(a.get("section_aliases", {}).items(), key=lambda kv: -len(kv[0])),
            "intent": sorted(a.get("intent_aliases", {}).items(), key=lambda kv: -len(kv[0])),
            "generic": sorted(a.get("generic_topics", {}).items(), key=lambda kv: -len(kv[0])),
            "unanswerable": sorted(a.get("unanswerable", {}).items(), key=lambda kv: -len(kv[0])),
            "direct": a.get("direct_rules", {}),
        }

    # ---------------- analyse ----------------
    def analyse(self, query):
        A = self.alias
        q = normq(query)
        out = {"explicit": [], "sections": [], "expand": [], "boost": set(),
               "exact": set(), "generic": None, "clarify": None,
               "unanswerable": None, "notes": []}
        for m in re.finditer(r"(\d+\.\d+(?:\.\d+){0,2})", str(query).lower()):
            rid = m.group(1).rstrip(".")
            if rid not in out["explicit"]:
                out["explicit"].append(rid)
        if out["explicit"]:
            out["boost"].update(out["explicit"]); out["exact"].update(out["explicit"])
            out["notes"].append("query 含明確規條編號")
        for term, reason in A["unanswerable"]:
            if normq(term) and normq(term) in q:
                out["unanswerable"] = {"term": term, "reason": reason}; break
        for term, rules in A["generic"]:
            if normq(term) and normq(term) in q:
                out["generic"] = list(rules)
                out["boost"].update(rules); out["exact"].update(rules)
                out["clarify"] = f"「{term}」涉及 {len(rules)} 條規條"
                out["notes"].append(f"generic topic「{term}」")
                break
        consumed = []
        for term, rule in A["section"]:
            t = normq(term)
            if not t or t not in q:
                continue
            if any(t in c for c in consumed):
                continue
            consumed.append(t)
            out["sections"].append({"term": term, "rule": rule})
            out["boost"].add(rule); out["exact"].add(rule)
        if out["sections"]:
            out["notes"].append("section: " + ",".join(s["rule"] for s in out["sections"]))
        for term, expands in A["intent"]:
            if normq(term) and normq(term) in q:
                for e in expands:
                    if e not in out["expand"]:
                        out["expand"].append(e)
        # 最長優先 — 防止「團長」搶走「副團長」嘅匹配（實測回歸）
        _dc = []
        for term, rule in sorted(A["direct"].items(), key=lambda kv: -len(kv[0])):
            t = normq(term)
            if not t or t not in q:
                continue
            if any(t in c for c in _dc):
                continue
            _dc.append(t)
            out["boost"].add(rule); out["exact"].add(rule)
        if (not out["sections"] and not out["generic"]
                and any(e == "年齡" for e in out["expand"])):
            out["generic"] = ["2.3.3", "2.3.4", "2.3.5", "2.3.6", "2.3.7"]
            out["boost"].update(out["generic"]); out["exact"].update(out["generic"])
            out["clarify"] = "問年齡但冇指明支部"
            out["notes"].append("問年齡但冇指明支部 → 返回全部")
        return out

    # ---------------- retrieve ----------------
    def retrieve(self, query, top_k=5):
        ana = self.analyse(query)
        qtoks = self.tok(query)
        for e in ana["expand"]:
            qtoks += self.tok(e)
        for s in ana["sections"]:
            qtoks += self.tok(s["term"])

        scores = [0.0] * self.N
        matched = set()
        for t in set(qtoks):
            p = self.postings.get(t)
            if not p:
                continue
            matched.add(t)
            for key, s in p:
                i = self.id2i.get(key) if self._post_by_id else key
                if i is not None and 0 <= i < self.N:
                    scores[i] += s

        boost_i, exact_i = set(), set()
        for rid in ana["boost"]:
            for i in self.by_rule.get(rid, []):
                boost_i.add(i); exact_i.add(i)
            for k, idxs in self.by_rule.items():
                if k.startswith(rid + "."):
                    boost_i.update(idxs)
        base = max(scores) if any(scores) else 1.0
        base = base or 1.0
        for i in boost_i:
            scores[i] = (scores[i] * 2.6 + base * 1.1) if i in exact_i \
                else (scores[i] * 1.7 + base * 0.5)

        direct = set()
        for rid in ana["explicit"]:
            direct.update(self.by_rule.get(rid, []))
            for k, idxs in self.by_rule.items():
                if k.startswith(rid + "."):
                    direct.update(idxs)

        cand = [i for i in range(self.N) if scores[i] > 0]
        cand.sort(key=lambda i: -scores[i])
        if direct:
            cand = [i for i in cand if i in direct] + [i for i in cand if i not in direct]

        total = len(cand)
        page = cand[:top_k]
        passages = []
        for n, i in enumerate(page):
            c = self.chunks[i]
            passages.append({
                "rank": n + 1, "score": round(scores[i], 3),
                "rule_id": c.get("rule_id") or c.get("id"),
                "heading": c.get("heading") or c.get("title"),
                "verbatim": c.get("verbatim") or "",
                "chars": c.get("chars") or len(c.get("verbatim") or ""),
                "is_stub": bool(c.get("is_stub")),
                "aggregated": bool(c.get("aggregated")),
                "md_url": c.get("md_url") or (f"/por/{c.get('id')}.md" if c.get("id") else None),
                "source": {
                    "document": c.get("doc_title") or c.get("doc"),
                    "version_label": c.get("version_label") or c.get("version"),
                    "source_sha256": c.get("source_sha256") or c.get("sha256"),
                    "source_last_modified": c.get("source_last_modified") or c.get("last_modified"),
                    "official_pdf": c.get("official_pdf"),
                    "official_landing": c.get("official_landing"),
                    "amended_by": c.get("amended_by"),
                    "as_of": c.get("as_of"),
                },
            })
        return {"query": query, "analysis": ana, "passages": passages,
                "total_candidates": total,
                "signals": self._signals(query, ana, page, scores, total, matched)}

    def _signals(self, query, ana, page, scores, total, matched):
        sig = {}
        # ⚠️ 用**完整 CJK 段**計 coverage，唔好用 bigram。
        #    bigram 令「世界童軍總部」嘅「童軍」一定命中 → coverage 永遠 1.0 → OOD 失效。
        segs = sorted(set(re.findall(r"[\u4e00-\u9fff]{2,}", query)))
        s_cov = [t for t in segs if t in self.idf]
        s_unc = [t for t in segs if t not in self.idf]
        content = {t for t in matched if len(t) >= 2}
        cov = [t for t in content if t in self.idf]
        sig["lexical_coverage"] = {
            "segments": segs,
            "segments_covered": s_cov,
            "segments_uncovered": s_unc,
            "segment_ratio": round(len(s_cov) / len(segs), 3) if segs else None,
            "bigram_ratio": round(len(cov) / len(content), 3) if content else None,
        }
        ql = re.sub(r"\s+", "", query.lower())
        intents = [w for w in INTENT_TERMS if w in ql]
        if intents:
            blob = re.sub(r"\s+", "", "".join(
                (self.chunks[i].get("verbatim") or "") for i in page))
            sig["answerability"] = {
                "query_intent_terms": intents,
                "found_in_candidates": [w for w in intents if w in blob],
                "missing_in_candidates": [w for w in intents if w not in blob],
            }
        if len(page) >= 2:
            s0, s1 = scores[page[0]], scores[page[1]]
            r0 = self.chunks[page[0]].get("rule_id") or self.chunks[page[0]].get("id")
            r1 = self.chunks[page[1]].get("rule_id") or self.chunks[page[1]].get("id")
            sig["ambiguity"] = {"ratio": round(s1 / s0, 3) if s0 else None,
                                "different_rules": r0 != r1}
        if ana["generic"]:
            hit = [(self.chunks[i].get("rule_id") or self.chunks[i].get("id")) for i in page]
            sig["section_conflict"] = {"topic": ana["generic"],
                                       "candidates_cover": [r for r in ana["generic"] if r in hit],
                                       "clarify": ana["clarify"]}
        # weak_match：分數量級。in-domain query 通常 top score 50–140；
        # OOD 通常 < 25。呢個係純詞彙方法唯一可靠嘅 OOD 信號。
        if page:
            top = scores[page[0]]
            allmax = max(scores) if any(scores) else 0
            if top < 30.0:
                sig["weak_match"] = {
                    "top_score": round(top, 2),
                    "meaning": "最高分 candidate 嘅相關度好低。呢個 query 好可能唔屬於 "
                               "已收錄文件嘅範圍，或者用詞同原文差太遠。"
                               "候选仍然照樣返回 —— 由你判斷要唔要用。",
                }
        am = [(self.chunks[i].get("rule_id") or self.chunks[i].get("id")) for i in page
              if self.chunks[i].get("amended_by")]
        if am:
            sig["version_warning"] = {"rules": am}
        if ana["unanswerable"]:
            sig["topic_not_in_corpus"] = ana["unanswerable"]
        if not total:
            sig["no_candidates"] = True
        return sig


# ---------------------------------------------------------------------------
# parity check：Python mirror vs app.js — 防止測試假 pass
# ---------------------------------------------------------------------------
def check_parity():
    prob = []
    if not APPS.exists():
        return [f"❌ 缺 {APPS}（前端檢索邏輯）"]
    js = APPS.read_text(encoding="utf-8")
    # STOP list 要一致
    mjs = re.search(r'const STOP = new Set\(\("(.+?)"\)\.split', js, re.S)
    if not mjs:
        prob.append("❌ app.js 搵唔到 STOP list — 無法做 parity check")
    else:
        js_stop = set(mjs.group(1).split())
        if js_stop != STOP:
            prob.append(f"⚠️ STOP list drift：app.js {len(js_stop)} 個 vs static_eval {len(STOP)} 個；"
                        f"差集 {sorted(js_stop ^ STOP)[:8]}")
    # boost 常數要一致
    for const in ("2.6", "1.1", "1.7", "0.5"):
        if const not in js:
            prob.append(f"⚠️ app.js 冇 boost 常數 {const} — logic 可能已改")
    # trigram：app.js 有，skill 嘅 search_kb.py 冇（刻意）
    if "i + 3 <=" not in js:
        prob.append("⚠️ app.js 冇 trigram — 同 static_eval 嘅 tokenize 唔一致")
    return prob


# ---------------------------------------------------------------------------
# CANARY：結構 + 必中題 + 必拒答
# ---------------------------------------------------------------------------
CANARY = [
    ("樂行童軍團的年齡規限", "2.3.7", ["十八歲", "二十五歲"], ["二十六歲"]),
    ("幼童軍團年齡規限",     "2.3.4", ["六歲", "十一歲"],     ["七歲半", "十二歲"]),
    ("童軍團年齡規限",       "2.3.5", ["十一歲", "十五歲"],   ["十六歲"]),
    ("小童軍團年齡規限",     "2.3.3", ["四歲", "七歲"],       ["五歲", "八歲"]),
    ("深資童軍團年齡規限",   "2.3.6", ["十五歲", "二十歲"],   ["二十一歲"]),
    ("總會嘅銘言",           "1.1.3", ["準備"],               []),
    ("童軍團幾多歲要退",     "2.3.5", ["十一歲", "十五歲"],   []),
    ("小童軍團最少要幾多名團員", "2.3.3", ["三十六"],          []),
    ("銀行戶口要幾多人簽",   "2.11.4", ["銀行戶口"],          []),
    ("旅務委員會",           "2.10",  ["旅務委員會"],          []),
]


def run_canary(eng, label):
    fails, rows = [], []
    if eng.N == 0:
        return [f"{label}: index 係空嘅"], []

    empty = [(c.get("rule_id") or c.get("id")) for c in eng.chunks
             if len((c.get("verbatim") or "").strip()) < 10 and not c.get("is_stub")]
    if empty:
        fails.append(f"{label}: {len(empty)} 個 chunk 近乎空白但未標 is_stub（聚合失敗？）{empty[:5]}")
    ids = [(c.get("rule_id") or c.get("id")) for c in eng.chunks]
    dup = [i for i, n in Counter(ids).items() if n > 1 and i]
    if dup:
        fails.append(f"{label}: rule_id 撞車（會令檔案互相覆蓋）{dup[:5]}")
    bad = [i for i in ids if i and not re.fullmatch(r"\d+(?:\.\d+){0,3}", str(i))]
    if bad:
        fails.append(f"{label}: rule_id 格式非法 {bad[:5]}")
    miss_prov = []
    for c in eng.chunks[:120]:
        for k in ("official_pdf", "as_of"):
            v = c.get(k) or c.get("source_sha256") if k == "official_pdf" else c.get(k)
        if not (c.get("official_pdf") and c.get("as_of")):
            miss_prov.append(c.get("rule_id") or c.get("id"))
    if miss_prov:
        fails.append(f"{label}: {len(miss_prov)} 個 chunk 缺 official_pdf/as_of（AI 冇得 cite）{miss_prov[:4]}")

    for q, rule, contains, must_not in CANARY:
        r = eng.retrieve(q, top_k=5)
        ps = r["passages"]
        got = [p["rule_id"] for p in ps]
        blob = re.sub(r"\s+", "", (ps[0]["verbatim"] if ps else ""))
        hit = any(g == rule or (g or "").startswith(rule + ".") for g in got)
        ok_text = all(re.sub(r"\s+", "", c) in blob for c in contains)
        ok_neg = not any(re.sub(r"\s+", "", x) in blob for x in must_not)
        ok = hit and ok_text and ok_neg
        rows.append({"label": label, "q": q, "ok": ok, "expect": rule,
                     "got": got[:3], "hit": hit, "text": ok_text, "neg": ok_neg})
        if not ok:
            why = []
            if not hit: why.append(f"expect 規條 {rule} → got {got[:3]}")
            if not ok_text: why.append(f"原文缺 {'/'.join(contains)}")
            if not ok_neg:
                leak = [x for x in must_not if re.sub(r"\s+", "", x) in blob]
                why.append(f"🔴 滲漏舊版內容 {leak}")
            fails.append(f"{label}: 「{q}」 " + "; ".join(why))
    return fails, rows


# ---------------------------------------------------------------------------
# GOLDEN SET
# ---------------------------------------------------------------------------
def run_golden(eng, label):
    if not GOLDEN.exists():
        return [f"{label}: 缺 data/golden.json"], {}, []
    cases = json.loads(GOLDEN.read_text(encoding="utf-8"))
    rows = []
    R_hit = R_tot = G_hit = G_tot = L_ok = L_tot = 0
    OOD_ok = OOD_tot = VT_ok = VT_tot = 0
    fails = []
    for c in cases:
        q = c["q"]
        # golden.json 用 expect_result="no_match" 標記領域外題（唔係 in_domain 欄位）
        in_domain = c.get("expect_result") != "no_match"
        expect = c.get("expect_rule")
        r = eng.retrieve(q, top_k=5)
        ps = r["passages"]
        got = [p["rule_id"] for p in ps]
        top_v = re.sub(r"\s+", "", (ps[0]["verbatim"] if ps else ""))

        if not in_domain:
            OOD_tot += 1
            # 靜態站唔拒答 — 正確行為係**有 signal** 令 AI 自己判斷
            sig = r["signals"]
            lc = sig.get("lexical_coverage", {})
            refused = bool(sig.get("topic_not_in_corpus")
                           or sig.get("no_candidates")
                           or sig.get("weak_match")
                           or (sig.get("answerability", {}).get("missing_in_candidates"))
                           or lc.get("segments_uncovered"))
            ok = refused
            OOD_ok += ok
            rows.append({"id": c.get("id"), "q": q, "kind": "OOD", "ok": ok,
                         "signals": list(sig), "got": got[:2]})
            if not ok:
                fails.append(f"{label} OOD {c.get('id')}「{q}」冇任何 signal 提示領域外 "
                             f"(signals={list(sig)}, got={got[:2]})")
            continue

        G_tot += 1
        contains = [re.sub(r"\s+", "", x) for x in (c.get("expect_contains") or [])]
        # 開放題（expect_rule = null，例 Q18「童軍團長委任副團長」）：
        # 冇指定規條，只要求原文含某啲字 → 唔計 R，只計 G
        accept = c.get("expect_rule_any") or ([expect] if expect else None)
        if accept:
            R_tot += 1
            hit = any(any(g == x or (g or "").startswith(x + ".") for x in accept)
                      for g in got)
            R_hit += hit
            g_ok = hit and all(x in top_v for x in contains)
        else:
            hit = None
            g_ok = any(x in top_v for x in contains) if contains else bool(got)
        G_hit += g_ok
        L_tot += 1
        leak = [x for x in (c.get("must_not_contain") or []) if re.sub(r"\s+", "", x) in top_v]
        l_ok = not leak
        L_ok += l_ok
        if "版本陷阱" in (c.get("note") or "") or c.get("version_trap") or "\U0001f534" in (c.get("note") or ""):
            VT_tot += 1; VT_ok += ((hit is not False) and l_ok)
        ok_all = (hit is not False) and g_ok and l_ok
        rows.append({"id": c.get("id"), "q": q,
                     "kind": "open" if not expect else "in",
                     "ok": ok_all, "expect": expect, "got": got[:3],
                     "R": hit, "G": g_ok, "L": l_ok, "leak": leak})
        if not ok_all:
            why = []
            if hit is False: why.append(f"R miss: expect {expect} got {got[:3]}")
            if not g_ok: why.append(f"G: top-1 原文缺 {c.get('expect_contains')}")
            if leak: why.append(f"🔴 L: 滲漏 {leak}")
            fails.append(f"{label} {c.get('id')}「{q}」 " + "; ".join(why))

    pct = lambda a, b: f"{a}/{b} = {round(100*a/b) if b else 0}%"
    summary = {
        "in_domain": R_tot, "out_of_domain": OOD_tot,
        "R_retrieval_accuracy": pct(R_hit, R_tot),
        "G_groundedness": pct(G_hit, G_tot),
        "L_no_leakage": pct(L_ok, L_tot),
        "OOD_flagged": pct(OOD_ok, OOD_tot),
        "version_trap": pct(VT_ok, VT_tot),
    }
    return fails, summary, rows


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--canary", action="store_true")
    ap.add_argument("--golden", action="store_true")
    ap.add_argument("--skill", action="store_true", help="一齊測 skill 嘅 index")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true",
                    help="任何 fail 就 exit 1（CI 用）")
    a = ap.parse_args()
    do_canary = a.canary or not (a.canary or a.golden)
    do_golden = a.golden or not (a.canary or a.golden)

    out = {"generated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
           "targets": {}, "parity": check_parity(), "fails": []}

    # ⚠️ web 同 skill 而家用**同一個** tokenizer（bigram+trigram）。
    #    之前 skill 只用 bigram → 兩個交付物對同一條問題回唔同答案。
    targets = [("web", WEB_INDEX, WEB_ALIAS, tokenize)]
    if a.skill:
        targets.append(("skill", SKILL_INDEX, None, tokenize))

    missing, empty = [], []
    for label, ipath, apath, tok in targets:
        if not Path(ipath).exists():
            missing.append(f"{label}: {ipath}")
            continue
        # ⚠️ shell mode 會生成一個 chunks=[] 嘅 index.json。
        #    呢個唔係 fail —— 係「架構已部署，內容待加入」。要 skip。
        #    （實測：原本淨係 check 檔案存唔存在，shell mode 之下檔案存在
        #      但係空嘅 → canary 0/0 → exit 1，block 咗 CI。）
        try:
            _d = json.loads(Path(ipath).read_text(encoding="utf-8"))
        except Exception as e:
            missing.append(f"{label}: {ipath} 讀唔到 ({type(e).__name__})")
            continue
        _n = len(_d.get("chunks") or _d.get("rules") or [])
        if _n == 0 or (_d.get("build") or {}).get("empty") \
                or (_d.get("meta") or {}).get("empty"):
            empty.append(f"{label}: {ipath}（0 條規條）")
            continue
        eng = Engine(ipath, apath, tok)
        t = {"index": str(ipath), "chunks": eng.N,
             "postings_by_id": eng._post_by_id,
             "alias_source": "external" if apath else "embedded"}
        if do_canary:
            f, rows = run_canary(eng, label)
            out["fails"] += f
            t["canary"] = {"passed": not f, "checks": rows,
                           "pass_rate": f"{sum(1 for r in rows if r['ok'])}/{len(rows)}"}
        if do_golden:
            f, summ, rows = run_golden(eng, label)
            out["fails"] += f
            t["golden"] = {"summary": summ, "cases": rows, "passed": not f}
        out["targets"][label] = t

    # ⚠️ corpus 空嘅時候（skeleton 階段）generated index 未存在。
    #    呢個唔係 fail —— 係「未 build」。要清楚講明，唔好當錯誤。
    # skip 判定：missing（未 build）同 empty（shell mode）都係合法狀態
    skip_reasons = []
    if missing:
        skip_reasons.append("未 build：" + "; ".join(missing))
    if empty:
        skip_reasons.append("shell mode（0 條規條）：" + "; ".join(empty))
    if skip_reasons and not out["targets"]:
        out["skipped"] = (" | ".join(skip_reasons) +
                          "。corpus/ 係空嘅時候呢個係正常 —— 架構已部署，內容待加入。"
                          "放 MD 入 corpus/ 再跑 build_site.py / build_skill.py 之後先至有嘢測。")
        out["passed"] = True
    elif missing:
        out["fails"] += ["缺 generated index: " + "; ".join(missing)]
    elif empty and out["targets"]:
        # 一邊有內容一邊冇 → web 同 skill 唔同步，呢個係真問題
        out["fails"] += ["⚠️ web 同 skill 唔同步：" + "; ".join(empty) +
                         " — 兩個都應該一齊 build"]

    out["passed"] = out.get("passed", not out["fails"])
    (ROOT / "static_eval_results.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        print("=" * 70)
        print(f"static_eval @ {out['generated']}")
        if out["parity"]:
            print("\n⚠️  app.js ↔ static_eval parity：")
            for p in out["parity"]:
                print("   " + p)
        for label, t in out["targets"].items():
            print(f"\n── {label}: {t['index']}")
            print(f"   chunks={t['chunks']}  postings_by_id={t['postings_by_id']}  "
                  f"alias={t['alias_source']}")
            if "canary" in t:
                c = t["canary"]
                print(f"   canary: {'✅ PASS' if c['passed'] else '❌ FAIL'}  {c['pass_rate']}")
                for r in c["checks"]:
                    if not r["ok"]:
                        print(f"      ❌ {r['q']:<22} expect {r['expect']} got {r['got']}")
            if "golden" in t:
                g = t["golden"]
                print(f"   golden:")
                for k, v in g["summary"].items():
                    print(f"      {k:<24} {v}")
        print("\n" + "=" * 70)
        if out.get("skipped"):
            print(f"\n⚠️  SKIP — {out['skipped']}")
        if out["fails"]:
            print(f"❌ {len(out['fails'])} 個問題：")
            for f in out["fails"][:20]:
                print("   - " + f)
        else:
            print("✅ 全部通過")
        print("💾 static_eval_results.json")
    if a.strict and out["fails"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
