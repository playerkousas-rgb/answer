# corpus/ — single source of truth

**呢個目錄係唯一需要你手動維護嘅地方。**
其他所有嘢（`por/` `md/` `web/` `skill/` `llms.txt` `index.json`）都係 generated。

## 結構

```
corpus/
├── POR/POR.md                 ← 政策、組織及規條（已有一份做示例）
├── GUIDE/<name>.md            ← 指引類
├── CIRC-POLICY/<no>.md        ← 政策通告
├── CIRC-GUIDELINE/<no>.md     ← 活動指引通告
└── ...
```

目錄名隨意 —— `doc_id` 由 frontmatter 決定，唔係由路徑。

## 每份 MD 必須有 frontmatter

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
sensitivity: public                 # 唔係 public → build 會跳過
qa_passed: true                     # false → build 會跳過
retrieved: 2026-10-02
---
```

> ⚠️ `status: superseded`、`qa_passed: false`、`sensitivity` 唔係 `public` 嘅檔案
> **會被 build 跳過**。呢個係保護機制：寧可知識庫舊三日，都唔好俾佢錯。

## 正文格式

```markdown
# 第 2 章 童軍旅

### 規條 2.3.7 樂行童軍團

(a) 一樂行童軍團為樂行童軍所組成……
(d) 年齡規限：十八歲至二十五歲。
```

- 規條用 `### 規條 <編號> <標題>`
- 章節用 `# 第 <n> 章 <標題>`
- **逐字引用原文，唔好改寫、唔好精简**

Build 會自動：
- chunk 成規條級
- 聚合 heading-only 嘅父規條（例「規條 2.11 童軍旅財務」正文空，內容全喺子條）
- 抽出寫晒喺 heading 行嘅內容（例「### 規條 1.1.3 總會的銘言為「準備」。」）
- 清除漏入正文嘅 PDF 頁碼
- 產生每條規條一個 MD + client-side 搜尋索引 + skill 索引

## 放完之後

```bash
python3 pipeline/build_site.py  --owner <你的GH> --repo <repo名>
python3 pipeline/build_skill.py --owner <你的GH> --repo <repo名> --zip
python3 pipeline/static_eval.py --skill --strict     # 失敗就 exit 1
git add -A && git commit -m "add: xxx" && git push
```
