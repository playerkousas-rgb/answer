/* ============================================================================
   app.js — client-side 檢索 + 查詢理解 + signals + 用戶自帶 LLM
   ============================================================================
   成個 app 喺瀏覽器入面行，**冇任何 server**：
     · BM25 檢索      → api/index.json（build 時生成嘅 postings）
     · 查詢理解       → api/alias_routes.json（substring + 最長優先）
     · signals        → 全部 client-side 計算
     · LLM            → 瀏覽器直連 provider（key 只喺 localStorage）

   設計原則：**唔拒答、唔分級、唔落命令。**
   返回齊全候选 + 中立 signals，由 AI 自己判斷。

   ⚠️ 呢個檔案嘅 source of truth 係 app/app.js。
      pipeline/build_site.py 會 copy 佢去 web/app.js。
      唔好直接改 web/ 入面嗰份 —— build 時會被覆蓋。
   ========================================================================== */

const STOP = new Set(("的 了 是 由 至 起 止 及 和 與 或 在 有 為 以 於 不 而 之 其 本 該 上 下 中 個 名 須 應 可 得 被 把 對 從 到 但 若 如 因 所 已 未 一個 我們 你們 他們 什麼 點樣 幾多 邊個 乜嘢 嘅 咗 喺 唔 冇 佢 呢 嗰 呀 啦 嘢 噉 咁 嚟 睇 想 要 會 做 用 問 答").split(" "));

/* client-side 嘅簡化分詞。
   build 時嘅 postings 係用 jieba + 自訂詞典生成嘅，兩者唔會 100% 一致。
   策略：中文出「完整段 + bigram + trigram」，英文出 word。
   bigram/trigram 保證能夠命中 jieba 切出嘅詞（例：「年齡規限」→ 年齡/規限/年齡規限）。 */
function tokenize(text) {
  const t = String(text || "").toLowerCase().replace(/[#*|>`\-=[\]()（）\s]+/g, " ");
  const out = [];
  for (const m of t.matchAll(/[a-z0-9]+/g)) {
    const w = m[0];
    if (!STOP.has(w)) out.push(w);
  }
  for (const m of t.matchAll(/[\u4e00-\u9fff]+/g)) {
    const s = m[0];
    if (s.length >= 2 && !STOP.has(s)) out.push(s);
    for (let i = 0; i + 2 <= s.length; i++) {
      const bg = s.slice(i, i + 2);
      if (!STOP.has(bg)) out.push(bg);
    }
    for (let i = 0; i + 3 <= s.length; i++) {
      const tg = s.slice(i, i + 3);
      if (!STOP.has(tg)) out.push(tg);
    }
  }
  return out;
}

/* --------------------------------------------------------------- state */
const KB = {
  idx: null, alias: null, manifest: null,
  byRule: {}, postings: {}, idf: {}, chunks: [],
  params: { k1: 1.5, b: 0.75, N: 0, avgdl: 1 },
  ready: false, error: null,
};

async function loadKB() {
  try {
    const [idx, alias, man] = await Promise.all([
      fetch("api/index.json").then(r => { if (!r.ok) throw new Error("api/index.json → HTTP " + r.status); return r.json(); }),
      fetch("api/alias_routes.json").then(r => r.ok ? r.json() : {}),
      fetch("api/manifest.json").then(r => r.ok ? r.json() : null),
    ]);
    KB.idx = idx; KB.alias = alias; KB.manifest = man;
    KB.chunks = idx.chunks; KB.postings = idx.postings; KB.idf = idx.idf;
    KB.params = Object.assign(KB.params, (idx.build || {}).params || {});
    KB.params.N = KB.chunks.length;
    KB.byRule = {};
    KB.chunks.forEach((c, i) => { if (c.rule_id) (KB.byRule[c.rule_id] = KB.byRule[c.rule_id] || []).push(i); });
    KB.ready = true;
    return true;
  } catch (e) { KB.error = String(e && e.message || e); return false; }
}

/* --------------------------------------------------------------- router */
function normQ(q) {
  let s = String(q || "").trim().toLowerCase();
  s = [...s].map(c => { const n = c.codePointAt(0); return (n >= 0xFF01 && n <= 0xFF5E) ? String.fromCodePoint(n - 0xFEE0) : c; }).join("");
  return s.replace(/[\s,，。.?？!！:：;；、"'『』「」()（）]+/g, "");
}
function sortedEntries(o) { return Object.entries(o || {}).sort((a, b) => b[0].length - a[0].length); }

let ALIAS_SORTED = null;
function aliasSorted() {
  if (ALIAS_SORTED) return ALIAS_SORTED;
  const a = KB.alias || {};
  ALIAS_SORTED = {
    section: sortedEntries(a.section_aliases),
    intent: sortedEntries(a.intent_aliases),
    generic: sortedEntries(a.generic_topics),
    unanswerable: sortedEntries(a.unanswerable),
    direct: a.direct_rules || {},
  };
  return ALIAS_SORTED;
}

function analyse(query) {
  const A = aliasSorted();
  const q = normQ(query);
  const out = {
    query, unanswerable: null, generic: null, sections: [], intents: [],
    direct_rules: {}, explicit_rule_ids: [], boost_rules: [],
    expand_tokens: [], clarify: null, notes: [],
  };

  for (const m of String(query).toLowerCase().matchAll(/(\d+\.\d+(?:\.\d+){0,2})/g)) {
    const rid = m[1].replace(/\.+$/, "");
    if (!out.explicit_rule_ids.includes(rid)) out.explicit_rule_ids.push(rid);
  }
  if (out.explicit_rule_ids.length) {
    out.boost_rules = out.explicit_rule_ids.slice();
    out.notes.push("query 含明確規條編號 → 直接定位");
  }

  // unanswerable — **唔係用嚟拒答**，只係一個 signal
  for (const [term, reason] of A.unanswerable) {
    if (normQ(term) && q.includes(normQ(term))) { out.unanswerable = { term, reason }; break; }
  }

  for (const [term, rules] of A.generic) {
    if (normQ(term) && q.includes(normQ(term))) {
      out.generic = rules.slice();
      out.boost_rules = [...new Set([...out.boost_rules, ...rules])];
      out.clarify = `「${term}」涉及 ${rules.length} 條規條（${rules.join("、")}）。用戶冇指明邊一個。`;
      out.notes.push(`generic topic「${term}」→ ${rules.length} 條規條`);
      break;
    }
  }

  // section（最長優先 — 實測：jieba 把「小童軍團」切做「小/童軍團」，
  // 所以問「童軍團」會誤中「小童軍團」。substring + 最長優先先至可靠）
  const consumed = [];
  for (const [term, rule] of A.section) {
    const t = normQ(term);
    if (!t || !q.includes(t)) continue;
    if (consumed.some(c => c.includes(t))) continue;
    consumed.push(t);
    out.sections.push({ term, rule });
  }
  for (const s of out.sections) if (!out.boost_rules.includes(s.rule)) out.boost_rules.push(s.rule);
  if (out.sections.length) out.notes.push("section: " + out.sections.map(s => `${s.term}→${s.rule}`).join(", "));

  for (const [term, expands] of A.intent) {
    if (normQ(term) && q.includes(normQ(term))) {
      out.intents.push({ term, expands });
      for (const e of expands) if (!out.expand_tokens.includes(e)) out.expand_tokens.push(e);
    }
  }
  if (out.expand_tokens.length) out.notes.push("口語 → 原文用字: " + out.expand_tokens.join(", "));

  for (const [term, rule] of Object.entries(A.direct)) {
    if (normQ(term) && q.includes(normQ(term))) {
      out.direct_rules[term] = rule;
      if (!out.boost_rules.includes(rule)) out.boost_rules.push(rule);
    }
  }

  if (!out.sections.length && !out.generic && out.expand_tokens.includes("年齡")) {
    out.generic = ["2.3.3", "2.3.4", "2.3.5", "2.3.6", "2.3.7"];
    out.boost_rules = [...new Set([...out.boost_rules, ...out.generic])];
    out.clarify = "用戶問年齡但冇指明支部（小童軍 2.3.3 / 幼童軍 2.3.4 / 童軍 2.3.5 / 深資童軍 2.3.6 / 樂行童軍 2.3.7）。";
    out.notes.push("問年齡但冇指明支部 → 返回全部");
  }
  return out;
}

/* --------------------------------------------------------------- BM25 */
function bm25(qtoks) {
  const scores = new Float64Array(KB.params.N || KB.chunks.length);
  const seen = new Set();
  for (const t of qtoks) {
    if (seen.has(t)) continue; seen.add(t);
    const p = KB.postings[t];
    if (!p) continue;
    for (const pair of p) scores[pair[0]] += pair[1];
  }
  return scores;
}

/* --------------------------------------------------------------- retrieve */
function retrieve(query, opts) {
  opts = opts || {};
  const top_k = Math.min(opts.top_k || 8, 60);
  const offset = opts.offset || 0;
  const min_score = opts.min_score || 0;
  const ana = analyse(query);

  let qtoks = tokenize(query);
  for (const e of ana.expand_tokens) qtoks = qtoks.concat(tokenize(e));
  for (const s of ana.sections) qtoks = qtoks.concat(tokenize(s.term));

  const scores = bm25(qtoks);

  // boost：router 解析出嘅規條（**只影響排序，唔過濾**）
  const boost = new Set(), exact = new Set();
  for (const rid of ana.boost_rules) {
    for (const i of (KB.byRule[rid] || [])) { boost.add(i); exact.add(i); }
    for (const k of Object.keys(KB.byRule)) {
      if (k.startsWith(rid + ".")) for (const i of KB.byRule[k]) boost.add(i);
    }
  }
  let base = 0;
  for (let i = 0; i < scores.length; i++) if (scores[i] > base) base = scores[i];
  base = base || 1;
  for (const i of boost) {
    scores[i] = exact.has(i) ? scores[i] * 2.6 + base * 1.1 : scores[i] * 1.7 + base * 0.5;
  }

  const direct = new Set();
  for (const rid of ana.explicit_rule_ids) {
    for (const i of (KB.byRule[rid] || [])) direct.add(i);
    for (const k of Object.keys(KB.byRule)) if (k.startsWith(rid + ".")) for (const i of KB.byRule[k]) direct.add(i);
  }

  let cand = [];
  for (let i = 0; i < scores.length; i++) if (scores[i] > min_score) cand.push(i);
  cand.sort((a, b) => scores[b] - scores[a]);
  if (direct.size) cand = cand.filter(i => direct.has(i)).concat(cand.filter(i => !direct.has(i)));

  const total = cand.length;
  const page = cand.slice(offset, offset + top_k);
  const passages = page.map((i, n) => passage(i, n + 1, scores[i]));

  return {
    query,
    retrieved_at: new Date().toISOString(),
    corpus: corpusMeta(),
    query_understanding: {
      tokens: [...new Set(qtoks)].sort(),
      alias_expansions: ana.expand_tokens,
      resolved_sections: ana.sections,
      resolved_intents: ana.intents,
      explicit_rule_ids: ana.explicit_rule_ids,
      generic_topic_rules: ana.generic,
      notes: ana.notes,
    },
    pagination: { total_candidates: total, returned: passages.length, offset, top_k, has_more: offset + top_k < total },
    passages,
    signals: buildSignals(query, ana, qtoks, page, scores, total),
    known_stale_sources: (KB.manifest && KB.manifest.known_stale_sources) || [],
    provenance_note: "所有 verbatim 欄位係逐字引用，未經改寫。source 欄位提供完整出處，可直接用於 citation。signals 欄位係中立嘅機器可讀訊號，供你自行判斷；本資料庫唔會對答案做取捨。",
  };
}

function passage(i, rank, score) {
  const c = KB.chunks[i];
  return {
    rank, score: Math.round(score * 10000) / 10000,
    chunk_id: c.doc_id + "::" + (c.rule_id || c.heading),
    doc_id: c.doc_id, doc_title: c.doc_title,
    rule_id: c.rule_id, heading: c.heading,
    verbatim: c.verbatim || "", chars: c.chars || 0,
    aggregated_from_sub_rules: c.aggregated ? "yes" : null,
    is_stub: !!c.is_stub,
    canonical_url: c.md_url,
    raw_md: c.raw_md || null,
    source: {
      document: c.doc_title, version_label: c.version_label,
      source_sha256: c.source_sha256, source_last_modified: c.source_last_modified,
      official_pdf: c.official_pdf, official_landing: c.official_landing,
      amended_by: c.amended_by || null, as_of: c.as_of,
    },
  };
}

function corpusMeta() {
  const m = KB.manifest || {};
  return {
    build_id: (KB.idx.build || {}).build_id, generated: (KB.idx.build || {}).generated,
    chunks: KB.chunks.length, tokenizer: (KB.idx.build || {}).tokenizer,
    documents: m.documents || [], repository: m.repository || null,
  };
}

/* --------------------------------------------------------------- signals */
const INTENT_TERMS = ["月薪", "薪金", "薪酬", "工資", "人工", "收費", "費用", "天氣", "氣溫",
  "電話", "電郵", "地址", "辦公時間", "歷史", "成立年份", "股價", "機票"];

function buildSignals(query, ana, qtoks, page, scores, total) {
  const sig = {};

  // ⚠️ 用**完整 CJK 段**計 coverage，唔好用 bigram。
  //    bigram 令「世界童軍總部」嘅「童軍」一定命中 → coverage 永遠 1.0 → OOD 偵測失效。
  const segs = [...new Set((String(query).match(/[\u4e00-\u9fff]{2,}/g) || []))];
  const sCov = segs.filter(t => t in KB.idf);
  const sUnc = segs.filter(t => !(t in KB.idf));
  const content = [...new Set(qtoks)].filter(t => t.length >= 2);
  sig.lexical_coverage = {
    segments: segs,
    segments_covered: sCov,
    segments_uncovered: sUnc,
    segment_ratio: segs.length ? Math.round(sCov.length / segs.length * 1000) / 1000 : null,
    bigram_ratio: content.length
      ? Math.round(content.filter(t => t in KB.idf).length / content.length * 1000) / 1000 : null,
    meaning: "segment_ratio 先係有意義嘅指標（用完整詞計）。bigram_ratio 只係參考。"
           + "低 segment_ratio 表示用戶用嘅詞喺 corpus 冇出現過 —— 可能係領域外，"
           + "亦可能只係同義改述。由你判斷。",
  };

  const ql = query.replace(/\s+/g, "").toLowerCase();
  const intents = INTENT_TERMS.filter(w => ql.includes(w));
  if (intents.length) {
    const blob = page.map(i => (KB.chunks[i].verbatim || "")).join("").replace(/\s+/g, "");
    sig.answerability = {
      query_intent_terms: intents,
      found_in_candidates: intents.filter(w => blob.includes(w)),
      missing_in_candidates: intents.filter(w => !blob.includes(w)),
      meaning: "如果 missing 非空，即係候选原文冇提及用戶問嘅嘢。你可能需要講明搵唔到，而唔係用候选內容推斷。",
    };
  }

  if (page.length >= 2) {
    const s0 = scores[page[0]], s1 = scores[page[1]];
    sig.ambiguity = {
      top_score: Math.round(s0 * 1000) / 1000, second_score: Math.round(s1 * 1000) / 1000,
      ratio: s0 ? Math.round(s1 / s0 * 1000) / 1000 : null,
      different_rules: KB.chunks[page[0]].rule_id !== KB.chunks[page[1]].rule_id,
      meaning: "ratio 接近 1 而且 different_rules=true → 兩條規條同樣相關，你可能需要反問用戶或者並列兩者。",
    };
  }

  if (ana.generic) {
    const hit = page.map(i => KB.chunks[i].rule_id);
    sig.section_conflict = {
      detected: true, topic: ana.generic,
      candidates_cover: ana.generic.filter(r => hit.includes(r)),
      meaning: "呢個問題涉及多個互斥嘅實體（例：五個支部各有唔同嘅年齡規限）。用戶冇指明邊一個。你可以並列全部，或者反問。",
      clarify_hint: ana.clarify,
    };
  }

  // weak_match：分數量級。純詞彙方法捉唔到「世界童軍總部喺邊度」呢類 OOD
  // （「世界」「總部」都係真實 vocab），但佢哋嘅 BM25 分數會明顯偏低。
  if (page.length) {
    const top = scores[page[0]];
    if (top < 30.0) sig.weak_match = {
      top_score: Math.round(top * 100) / 100,
      meaning: "最高分 candidate 嘅相關度好低。呢個 query 好可能唔屬於已收錄文件嘅範圍，"
             + "或者用詞同原文差太遠。候选仍然照樣返回 —— 由你判斷要唔要用。",
    };
  }

  const amended = page.map(i => KB.chunks[i]).filter(c => c.amended_by)
    .map(c => ({ rule_id: c.rule_id, amended_by: c.amended_by }));
  if (amended.length) sig.version_warning = {
    rules_with_amendments: amended,
    meaning: "呢啲規條有後續通告修訂。引用時應該一併列出通告編號同生效日。",
  };

  if (ana.unanswerable) sig.topic_not_in_corpus = {
    matched_term: ana.unanswerable.term, note: ana.unanswerable.reason,
    meaning: "呢個主題已知唔喺已收錄嘅總會公開文件範圍內。候选仍然照樣返回 —— 由你決定點用。",
  };

  if (!total) sig.no_candidates = {
    meaning: "BM25 對呢個 query 搵唔到任何候选（所有分數為 0）。",
    suggestion: "可以讀 index.json 列出所有規條，或者讀 md/POR.md 全文。",
  };
  return sig;
}

/* --------------------------------------------------------------- 人用：關鍵字搜尋 */
function keywordSearch(q, opts) {
  opts = opts || {};
  const r = retrieve(q, { top_k: Math.min(opts.limit || 25, 100) });
  let hits = r.passages;
  if (opts.rule_prefix) hits = hits.filter(p => (p.rule_id || "").startsWith(opts.rule_prefix));
  if (opts.doc) hits = hits.filter(p => p.doc_id === opts.doc);
  const ws = q.split(/[\s,，、]+/).filter(Boolean);
  return {
    query: q, total: r.pagination.total_candidates, returned: hits.length,
    hits: hits.map(p => {
      const v = p.verbatim || "";
      let i = 0;
      for (const w of ws) { const j = v.indexOf(w); if (j >= 0) { i = j; break; } }
      return {
        rule_id: p.rule_id, heading: p.heading, score: p.score, doc_id: p.doc_id,
        snippet: v.slice(Math.max(0, i - 60), i + 240).replace(/\n/g, " ").trim(),
        md_url: p.canonical_url, raw_md: p.raw_md, official_pdf: p.source.official_pdf,
        version_label: p.source.version_label, as_of: p.source.as_of,
      };
    }),
  };
}

function listRules(prefix) {
  const ids = Object.keys(KB.byRule).sort((a, b) => {
    const pa = a.split(".").map(Number), pb = b.split(".").map(Number);
    for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
      const d = (pa[i] || 0) - (pb[i] || 0); if (d) return d;
    }
    return 0;
  });
  return ids.filter(r => !prefix || r.startsWith(prefix)).map(r => {
    const c = KB.chunks[KB.byRule[r][0]];
    return { rule_id: r, heading: c.heading, chars: c.chars, aggregated: !!c.aggregated, url: c.md_url };
  });
}

window.ScoutKB = { loadKB, retrieve, keywordSearch, listRules, analyse, KB, tokenize };
