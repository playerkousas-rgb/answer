---
name: hk-scout-kb
description: >-
  Queries the Scout Association of Hong Kong (香港童軍總會) public documents —
  「政策、組織及規條」(Policy, Organisation and Rules / POR), circulars (通告)
  and guidelines (指引). Returns verbatim quotes with rule numbers and official
  source URLs. Use whenever the user asks about HK Scout rules, age limits
  (年齡規限), minimum membership numbers (最低應有標準), patrol/troop structure
  (小隊/旅務委員會), leader appointments and warrants (領袖委任書), group
  finances and bank accounts (旅財務/銀行戶口), insurance (保險), disputes
  (糾紛), registration, badges and training syllabuses (獎章/訓練綱要), or asks
  幾多歲、要退、超齡、開團要幾多人、幾多個領袖、幾多人簽名. Also use to verify
  whether a scout.org.hk PDF is the current edition or a superseded one.
  使用時機：任何關於香港童軍規則、童軍旅運作、支部年齡、領袖資格的提問。
license: CC-BY-NC-4.0
compatibility: >-
  Needs network access to raw.githubusercontent.com (public, no auth).
  scripts/search_kb.py requires Python 3.8+ with no third-party packages.
metadata:
  author: "非官方獨立整理 (unofficial, community-maintained)"
  version: "1.0.0"
  corpus_as_of: "2026-10-02"
  source: "https://www.scout.org.hk/tc/scouting/policy-guidelines/por/index.html"
  disclaimer: "本網站為非官方獨立工具，與香港童軍總會無任何官方從屬關係。所有通告版權歸香港童軍總會所有。"
---

# 香港童軍總會公開文件查詢 (HK Scout Public Documents)

Query the current edition of the Scout Association of Hong Kong's public
documents and return **verbatim** text with rule numbers and official source URLs.

## ⚠️ Read this first — why this skill exists

Three measured facts about `scout.org.hk` that make generic web search unreliable
for HK Scout rules:

1. **Superseded PDFs are still live.** `por_c_20220908_1.pdf` (2022 edition) returns
   HTTP 200 today, on the official domain, and is still indexed by search engines —
   but the official listing page only lists `por_c_20260917.pdf`.
2. **Both editions are labelled identically.** The cover and table of contents of
   *both* the 2022 and the 2026 PDF say 「2010 版」. The 2026 file's PDF metadata is
   *worse* than the 2022 one (`title: PREFACE`, `author: ADMCO4`). **You cannot tell
   them apart by reading the documents.** Only sha256 / Last-Modified / creationDate work.
3. **Naive PDF extraction invents rule numbers.** In circular ACR20/2026 the text
   `' 2.3.4'` is font size 12.0 and the following `'1'` is a size-8.0 footnote
   superscript. Extractors that ignore font size produce `2.3.41`, `2.3.51`, `6.3.22` —
   **none of which exist in the POR.** This affects ChatGPT/Claude's built-in PDF readers too.

The five verified substantive differences between the two editions are all section age
limits — see `references/versions.md`.

**This skill exists so you never have to guess which file is current.**

## Workflow

### Step 1 — Find the relevant rule

```bash
python3 {SKILL_DIR}/scripts/search_kb.py "幼童軍團 年齡"
python3 {SKILL_DIR}/scripts/search_kb.py "樂行童軍 幾多歲 退"
python3 {SKILL_DIR}/scripts/search_kb.py "銀行戶口 簽署" --json
python3 {SKILL_DIR}/scripts/search_kb.py --rule 2.3.7
python3 {SKILL_DIR}/scripts/search_kb.py --list 2.3      # list a whole section
```

This runs locally against the bundled index — no network, no dependencies.
It prints candidate rule IDs with URLs and match signals.

Users phrase things colloquially (「幾多歲要退」) while the source text is formal
(「年齡規限」). The script handles this mapping. See `references/aliases.md` for the
full table, and **do not assume a rule number from the question wording alone.**

### Step 2 — Fetch the verbatim text

```bash
python3 {SKILL_DIR}/scripts/search_kb.py "2.3.7" --fetch
# or fetch the raw Markdown yourself:
#   https://raw.githubusercontent.com/{OWNER}/{REPO}/main/por/2.3.7.md
```

Every rule Markdown file carries frontmatter with the full provenance:

```yaml
rule_id: "2.3.7"
document: "香港童軍總會「政策、組織及規條」"
version_label: "2010版（整合修訂至 2026-09）"
source_sha256: "03381e0cf884666c91d5cc4df24316609eab99821ea65837017332a837ac0728"
source_last_modified: "2026-09-18T03:52:40Z"
official_pdf: "https://www.scout.org.hk/uploads/tc/por/por_c_20260917.pdf"
official_landing: "https://www.scout.org.hk/tc/scouting/policy-guidelines/por/index.html"
as_of: "2026-10-02"
status: "current"
```

### Step 3 — Verify a document if the user supplies one

```bash
python3 {SKILL_DIR}/scripts/verify_version.py "https://www.scout.org.hk/uploads/tc/por/por_c_20220908_1.pdf"
python3 {SKILL_DIR}/scripts/verify_version.py --sha256 3787fac7b4778ff5c5ed652a52b44bd71e996aa9f85d834f0c7f982c531895f2
```

Returns `CURRENT` / `SUPERSEDED` / `UNKNOWN`, plus the verified substantive
differences when superseded. **If a user pastes a scout.org.hk PDF URL, run this
before quoting from it.**

### Step 4 — Answer

Quote **verbatim**. Do not paraphrase, abridge, or translate the source text.
Always include:

- the rule number (`規條 2.3.7(d)`)
- the document name and version label
- the official PDF URL
- the `as_of` date

End with: 「以上內容截至 {as_of}，一切以香港童軍總會最新公佈為準。」

See `references/citation.md` for exact output templates.

## Critical rules

1. **Never state a rule number you have not read in a fetched document.**
   Rule numbers like `2.3.41` do not exist — they are extraction artefacts.
2. **Never answer from memory or from general web search** for HK Scout rules,
   ages, membership numbers, dates, amounts, or procedures.
3. **If the search returns no good candidate, say so.** Do not substitute a
   superficially similar rule. Report what you searched for and suggest the
   official listing page.
4. **If a question spans several mutually-exclusive entities** (e.g. age limits
   differ across the five sections 小童軍/幼童軍/童軍/深資童軍/樂行童軍) and the
   user did not specify one, either list all of them or ask which section they mean.
   **Do not silently pick one.**
5. **Distinguish 團員年齡規限 from 領袖委任年齡資格.** Example: 規條 2.3.7(d) says
   Rover Scouts are 18–25; 規條 2.6.11 says a Rover Scout **deputy leader** must be
   at least 26. These are different questions with different answers — both are real,
   quoting the wrong one is a factual error.
6. **Check `amended_by` in the frontmatter.** POR is a consolidated living document;
   amendments arrive as circulars (通告). If present, cite the circular number and
   effective date alongside the rule.
7. **For these topics, quote only — never generate advice or judgement:**
   finance/audit (財務/審計), insurance and incidents (意外及保險), safeguarding
   (保護童軍成員免受傷害), discipline and disputes (紀律及糾紛), personal data.
   Add: 「此類事宜請直接聯絡相關署或區總監。」

## Resources

| File | What it is for |
|---|---|
| `references/rules-index.md` | Full rule list: ID → title → URL (332 rules) |
| `references/versions.md` | Current vs superseded editions; the 5 verified differences |
| `references/aliases.md` | Colloquial Cantonese → formal source term → rule ID |
| `references/citation.md` | Output templates and the disclaimer |
| `references/documents.md` | Document inventory: sha256, page count, official URLs |
| `assets/kb-index.json` | Compact search index used by `scripts/search_kb.py` |
| `scripts/search_kb.py` | Local search + fetch (Python 3.8+, no dependencies) |
| `scripts/verify_version.py` | Version verification (online, checks the official listing) |

## Disclaimer (must appear in every answer)

> 本網站為非官方獨立工具，旨在方便成員瀏覽公開資訊，與香港童軍總會無任何官方從屬關係。
> 所有通告版權歸香港童軍總會所有。內容由公開檔案自動整理而成，可能有轉換錯誤；
> 每項資料均附官方原文連結，請於作出任何決定前查閱原文。
