#!/usr/bin/env python3
"""
router.py — 查詢理解層。**呢個 module 先係「使用」問題嘅核心。**

點解唔可以單靠 BM25：
  人問「童軍團幾多歲要退」，原文寫「年齡規限：十一歲至十五歲」。
  BM25 係字面匹配 —— 「幾多歲」「要退」喺原文一個字都冇。
  更糟：jieba 把「小童軍團」切做「小/童軍團」，
  所以問「童軍團」反而會誤中「小童軍團」。

router 做四件事：
  1. **unanswerable 早退**：已知 corpus 冇嘅主題 → 直接拒答，唔好回似是而非嘅規條
  2. **section 解析**：最長子串匹配（「小童軍」唔會搶走「童軍」）
  3. **intent 解析**：口語 → 原文用字（「幾多歲要退」→「年齡規限」）
  4. **generic topic**：跨多條規條嘅問題 → 回所有相關規條 + ambiguous，
     令模型反問「你問邊個支部？」而唔係亂揀一個答

全部用 **substring match on raw query**，唔經分詞 —— 呢個係避開 jieba 切詞問題嘅關鍵。
"""
from __future__ import annotations
import json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALIAS_PATH = ROOT / "data" / "alias_routes.json"

_cache = None


def load_aliases(path=None):
    global _cache
    if _cache is not None and path is None:
        return _cache
    p = Path(path) if path else ALIAS_PATH
    d = json.loads(p.read_text(encoding="utf-8"))
    for k in list(d):
        if isinstance(d[k], dict):
            d[k].pop("_comment", None)
    # 最長優先排序（substring 匹配要由長到短試）
    d["_section_sorted"] = sorted(d.get("section_aliases", {}).items(),
                                  key=lambda kv: -len(kv[0]))
    d["_intent_sorted"] = sorted(d.get("intent_aliases", {}).items(),
                                 key=lambda kv: -len(kv[0]))
    d["_generic_sorted"] = sorted(d.get("generic_topics", {}).items(),
                                  key=lambda kv: -len(kv[0]))
    d["_unanswerable_sorted"] = sorted(d.get("unanswerable", {}).items(),
                                       key=lambda kv: -len(kv[0]))
    if path is None:
        _cache = d
    return d


def _norm(q: str) -> str:
    """輕量正規化：全形→半形、去空白、 lowercase。唔好做太多，怕撞唔到 substring。"""
    q = q.strip().lower()
    q = "".join(chr(ord(c) - 0xFEE0) if 0xFF01 <= ord(c) <= 0xFF5E else c for c in q)
    q = re.sub(r"[\s,，。.?？!！:：;；、\"'『』「」()（）]+", "", q)
    return q


class Router:
    def __init__(self, alias_path=None):
        self.a = load_aliases(alias_path)

    def analyse(self, query: str) -> dict:
        """
        回一個 query 理解結果：
          {
            "unanswerable": {"term":…, "reason":…} | None,
            "generic": [rule_id, …] | None,
            "sections": [{"term":"小童軍","rule":"2.3.3"}],
            "intents":  [{"term":"幾多歲","expands":["年齡"]}],
            "direct_rules": {"目的": "1.1"},
            "explicit_rule_ids": ["2.3.7"],
            "boost_rules": ["2.3.3"],       # 综合以上得出嘅要 boost 嘅規條
            "expand_tokens": ["年齡"],       # 用嚟擴充 BM25 query
            "clarify": "你問邊個支部？" | None,
          }
        """
        q = _norm(query)
        raw = query.lower()
        out = {"query": query, "unanswerable": None, "generic": None,
               "sections": [], "intents": [], "direct_rules": {},
               "explicit_rule_ids": [], "boost_rules": [], "expand_tokens": [],
               "clarify": None, "notes": []}

        # ---- 1) 明確規條編號（最高優先）----
        for m in re.finditer(r"(?:規條|規則|rule)?\s*(\d+\.\d+(?:\.\d+){0,2})", raw):
            rid = m.group(1).rstrip(".")
            if rid not in out["explicit_rule_ids"]:
                out["explicit_rule_ids"].append(rid)
        if out["explicit_rule_ids"]:
            out["boost_rules"] = list(out["explicit_rule_ids"])
            out["notes"].append("query 含明確規條編號 → 直接定位")

        # ---- 2) unanswerable 早退 ----
        for term, reason in self.a["_unanswerable_sorted"]:
            if _norm(term) in q:
                out["unanswerable"] = {"term": term, "reason": reason}
                return out

        # ---- 3) generic topic（跨多條規條）----
        for term, rules in self.a["_generic_sorted"]:
            if _norm(term) in q:
                out["generic"] = list(rules)
                out["boost_rules"] = list(dict.fromkeys(out["boost_rules"] + list(rules)))
                out["clarify"] = (f"「{term}」涉及 {len(rules)} 條規條（{'、'.join(rules)}）。"
                                  f"如果你想問某一個支部，請講明；"
                                  f"否則以下列出全部相關規條。")
                out["notes"].append(f"generic topic 「{term}」 → {len(rules)} 條規條")
                break

        # ---- 4) section 解析（最長優先）----
        consumed = []
        for term, rule in self.a["_section_sorted"]:
            t = _norm(term)
            if t and t in q:
                # 避免「童軍」喺「小童軍」入面重複命中
                if any(t in c for c in consumed):
                    continue
                consumed.append(t)
                out["sections"].append({"term": term, "rule": rule})
        for s in out["sections"]:
            if s["rule"] not in out["boost_rules"]:
                out["boost_rules"].append(s["rule"])
        if out["sections"]:
            out["notes"].append("section: " + ", ".join(f'{s["term"]}→{s["rule"]}'
                                                        for s in out["sections"]))

        # ---- 5) intent 解析 → 擴充 BM25 query ----
        for term, expands in self.a["_intent_sorted"]:
            if _norm(term) in q:
                out["intents"].append({"term": term, "expands": expands})
                for e in expands:
                    if e not in out["expand_tokens"]:
                        out["expand_tokens"].append(e)
        if out["expand_tokens"]:
            out["notes"].append("口語 → 原文用字: " + ", ".join(out["expand_tokens"]))

        # ---- 6) direct rules ----
        # 最長優先 — 防止「團長」搶走「副團長」嘅匹配
        _dconsumed = []
        for term, rule in sorted((self.a.get("direct_rules") or {}).items(),
                                 key=lambda kv: -len(kv[0])):
            t = _norm(term)
            if not t or t not in q:
                continue
            if any(t in c for c in _dconsumed):
                continue
            _dconsumed.append(t)
            out["direct_rules"][term] = rule
            if rule not in out["boost_rules"]:
                out["boost_rules"].append(rule)

        # ---- 7) 如果完全冇 section 而 intent 係「年齡」→ 應該 clarify ----
        if (not out["sections"] and not out["generic"]
                and any(e == "年齡" for e in out["expand_tokens"])):
            out["clarify"] = ("你想問邊個支部嘅年齡規限？"
                              "（小童軍 2.3.3 / 幼童軍 2.3.4 / 童軍 2.3.5 / "
                              "深資童軍 2.3.6 / 樂行童軍 2.3.7）")
            out["generic"] = ["2.3.3", "2.3.4", "2.3.5", "2.3.6", "2.3.7"]
            out["boost_rules"] = list(dict.fromkeys(out["boost_rules"] + out["generic"]))
            out["notes"].append("問年齡但冇指明支部 → 回全部 + clarify")

        return out


if __name__ == "__main__":
    import sys
    r = Router()
    for q in sys.argv[1:] or ["童軍團幾多歲要退", "小童軍團最少要幾多名團員",
                              "各支部年齡", "區總監可唔可以解散一個團",
                              "童軍旅團長月薪幾多"]:
        print("=" * 70); print("Q:", q)
        a = r.analyse(q)
        for k in ("unanswerable", "generic", "sections", "intents",
                  "boost_rules", "expand_tokens", "explicit_rule_ids", "clarify", "notes"):
            if a[k]:
                print(f"  {k}: {json.dumps(a[k], ensure_ascii=False)}")
