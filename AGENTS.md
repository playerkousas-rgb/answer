# AGENTS.md

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

本網站為非官方獨立工具，旨在方便成員瀏覽公開資訊，與香港童軍總會無任何官方從屬關係。所有通告版權歸香港童軍總會所有。
