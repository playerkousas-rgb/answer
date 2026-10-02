#!/usr/bin/env python3
"""
verify_version.py — 核對一份 scout.org.hk 檔案係咪官方現行版本。

**呢個 script 係成個 skill 最重要嘅一件嘢。**

原因（全部已實測）：
  1. 舊版 PDF 冇下架 —— por_c_20220908_1.pdf 今日仍然 HTTP 200
  2. 官方目錄頁只列現行版（今日只列 POR_C_20260917.pdf）
  3. 新舊兩版封面／目錄**都印住「2010 版」** —— 讀文件本身分唔到
  4. 現行版嘅 PDF metadata 仲差過舊版（title=PREFACE, author=ADMCO4）
  → 唯一可靠判別：官方目錄頁 + sha256 + Last-Modified

Usage:
  python3 verify_version.py "https://www.scout.org.hk/uploads/tc/por/por_c_20220908_1.pdf"
  python3 verify_version.py por_c_20220908_1.pdf
  python3 verify_version.py --sha256 3787fac7...
  python3 verify_version.py --offline      # 唔上網，只用 bundled 資料
"""
from __future__ import annotations
import argparse, hashlib, json, os, re, sys, urllib.parse, urllib.request
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
INDEX = SKILL_DIR / "assets" / "kb-index.json"
UA = os.environ.get(
    "SCOUT_KB_UA",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
)

DISCLAIMER = ("本網站為非官方獨立工具，旨在方便成員瀏覽公開資訊，"
              "與香港童軍總會無任何官方從屬關係。所有通告版權歸香港童軍總會所有。")


def http(url, method="GET", timeout=45):
    req = urllib.request.Request(url, method=method, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, dict(r.headers), r.read()


def head_ok(url, timeout=30):
    """⚠️ 呢個站對 HEAD 請求 + 非瀏覽器 UA 會回 403。用 GET + browser UA。"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Range": "bytes=0-0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            h = dict(r.headers)
            r.read(1)
            return r.status, h
    except Exception as e:
        return None, {"error": f"{type(e).__name__}: {e}"}


def canon(url):
    """listing href 用大階檔名，server 301 redirect 去細階 → 必須 canonicalise"""
    p = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path.lower(), p.query, p.fragment))


def sha256_of_url(url, timeout=180):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            h = hashlib.sha256()
            n = 0
            while True:
                b = r.read(1 << 16)
                if not b:
                    break
                h.update(b); n += len(b)
            return h.hexdigest(), n
    except Exception as e:
        return None, str(e)


def fetch_listing(url, timeout=45):
    """去官方目錄頁攞現行清單 —— 呢個係唯一權威來源"""
    try:
        _, _, body = http(url, timeout=timeout)
        html = body.decode("utf-8", errors="replace")
        hrefs = re.findall(r'href="([^"]+\.pdf)"', html, re.I)
        out = []
        for h in hrefs:
            full = urllib.parse.urljoin(url, h)
            out.append(canon(full))
        return sorted(set(out)), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("identifier", nargs="?",
                    help="PDF URL 或檔名，例 por_c_20220908_1.pdf")
    ap.add_argument("--sha256", help="用 sha256 核對")
    ap.add_argument("--offline", action="store_true", help="唔上網，只用 bundled 資料")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--hash-url", action="store_true",
                    help="下載檔案計 sha256（慢，但最確定）")
    a = ap.parse_args()

    idx = json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.exists() else {}
    docs = idx.get("documents", [])
    stale = idx.get("known_stale_sources", [])

    ident = a.sha256 or a.identifier
    if not ident:
        ap.print_help(); sys.exit(1)

    result = {
        "identifier": ident,
        "verdict": "UNKNOWN",
        "checked_at": None,
        "official_index_lists": [],
        "matched": None,
        "current_version": None,
        "verified_differences": [],
        "note": None,
        "warning": None,
        "disclaimer": DISCLAIMER,
    }

    import datetime
    result["checked_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

    q = ident.lower().strip()
    fname = q.rsplit("/", 1)[-1]

    # ---------- 1) offline：對 bundled 資料 ----------
    for d in docs:
        du = (d.get("source_url") or "").lower()
        ds = (d.get("source_sha256") or "").lower()
        if q == ds or fname and fname in du or du.endswith(fname):
            result["verdict"] = "CURRENT"
            result["matched"] = d
            result["current_version"] = d
            result["note"] = (f"呢個檔案喺 corpus 入面登記做現行版。"
                              f"sha256={d.get('source_sha256')} · "
                              f"Last-Modified={d.get('source_last_modified')}")
            break
    if result["verdict"] == "UNKNOWN":
        for s in stale:
            su = (s.get("url") or "").lower()
            ss = (s.get("sha256") or "").lower()
            if q == ss or (fname and fname in su) or su.endswith(fname):
                result["verdict"] = "SUPERSEDED"
                result["matched"] = s
                result["verified_differences"] = s.get("verified_differences", [])
                result["current_version"] = {"source_url": s.get("superseded_by")}
                result["warning"] = (
                    "⚠️ 呢個檔案已被官方目錄頁取代，但 URL 仍然 live、搜尋引擎仍然索引緊。"
                    "唔好引用佢。兩份文件嘅封面都印住「2010 版」，"
                    "所以單靠讀文件係分唔到嘅 —— 只有 sha256 / Last-Modified 可靠。")
                break

    # ---------- 2) online：去官方目錄頁核實 ----------
    if not a.offline:
        landings = sorted({d.get("official_landing") for d in docs
                           if d.get("official_landing")})
        listed = []
        for L in landings:
            got, err = fetch_listing(L)
            if got:
                listed += got
            elif err:
                result.setdefault("errors", []).append(f"{L}: {err}")
        result["official_index_lists"] = listed

        if listed:
            target = q if q.startswith("http") else None
            if target:
                target = canon(target)
                hit = target in listed
            else:
                hit = any(u.lower().endswith(fname) for u in listed)
            if hit:
                result["verdict"] = "CURRENT"
                result["note"] = "官方目錄頁而家列緊呢個檔案。"
            else:
                if result["verdict"] != "SUPERSEDED":
                    result["verdict"] = "NOT_IN_INDEX"
                result["warning"] = (
                    "⚠️ 官方目錄頁**冇**列呢個檔案。呢個係最主要嘅判別依據 —— "
                    "舊版 PDF 冇下架，URL 仍然可以開啟，但已唔喺現行清單入面。")

        # HEAD/GET 核實檔案本身仲 live 嗎
        if q.startswith("http"):
            st, hdr = head_ok(canon(q))
            result["live_check"] = {
                "url": canon(q), "http_status": st,
                "last_modified": hdr.get("Last-Modified") or hdr.get("last-modified"),
                "etag": hdr.get("ETag") or hdr.get("etag"),
                "content_length": hdr.get("Content-Length") or hdr.get("content-length"),
                "error": hdr.get("error"),
            }
        elif fname:
            # 用 bundled URL 試
            for s in stale + docs:
                u = s.get("url") or s.get("source_url")
                if u and fname in u.lower():
                    st, hdr = head_ok(canon(u))
                    result["live_check"] = {
                        "url": canon(u), "http_status": st,
                        "last_modified": hdr.get("Last-Modified") or hdr.get("last-modified"),
                        "content_length": hdr.get("Content-Length") or hdr.get("content-length"),
                    }
                    break

        if a.hash_url:
            url = q if q.startswith("http") else (result.get("live_check") or {}).get("url")
            if url:
                h, n = sha256_of_url(url)
                result["computed_sha256"] = h
                result["computed_bytes"] = n

    out = json.dumps(result, ensure_ascii=False, indent=1)
    if a.json:
        print(out); return

    # ---------- human-readable ----------
    v = result["verdict"]
    mark = {"CURRENT": "✅", "SUPERSEDED": "❌", "NOT_IN_INDEX": "⚠️"}.get(v, "❓")
    print(f"{mark} {v}")
    print(f"   查詢：{ident}")
    print(f"   核對時間：{result['checked_at']}")
    if result["official_index_lists"]:
        print(f"\n官方目錄頁而家列出：")
        for u in result["official_index_lists"]:
            print(f"   · {u}")
    if result.get("live_check"):
        lc = result["live_check"]
        print(f"\n檔案本身：HTTP {lc.get('http_status')}")
        if lc.get("last_modified"): print(f"   Last-Modified: {lc['last_modified']}")
        if lc.get("content_length"): print(f"   Content-Length: {lc['content_length']}")
        if lc.get("error"): print(f"   ⚠️ {lc['error']}")
    m = result.get("matched")
    if m:
        print(f"\n登記資料：")
        for k in ("title", "doc_id", "version_label", "source_sha256", "sha256",
                  "source_last_modified", "last_modified", "pages", "source_url", "url",
                  "official_pdf", "official_landing", "superseded_by", "status"):
            if m.get(k): print(f"   {k}: {m[k]}")
    if result.get("verified_differences"):
        print(f"\n兩份文件之間已核實嘅實質差異（{len(result['verified_differences'])} 項）：")
        print(f"   {'規條':<8} {'項目':<22} {'舊版':<26} 現行")
        for d in result["verified_differences"]:
            print(f"   {d.get('rule',''):<8} {d.get('topic','')[:20]:<22} "
                  f"{d.get('stale','')[:24]:<26} {d.get('current','')}")
    if result.get("note"): print(f"\n{result['note']}")
    if result.get("warning"): print(f"\n{result['warning']}")
    if result.get("errors"): print(f"\n⚠️ {result['errors']}")
    print(f"\n{DISCLAIMER}")
    print("本站只陳列事實（官方目錄頁而家列緊邊份），唔判斷總會任何文件嘅對錯。")
    if v != "CURRENT":
        sys.exit(2)


if __name__ == "__main__":
    main()
