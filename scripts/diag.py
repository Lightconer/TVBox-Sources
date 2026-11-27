# -*- coding: utf-8 -*-
"""诊断脚本：用本地(国内)网络对 live.txt 做分片级严格校验，找出"播放失败"频道。

修复项（相对 TVBox-Sources）：
  - 不再用 r.content[:300000] 无界读取直播流（会挂死/吃满内存），改为 iter_content 有界读取
  - 不再全局关闭 TLS 校验（S.verify=False），默认校验；可用 --insecure 显式开启
  - 除法零保护；非 playlist 的直流不被误判为失败
  - 支持本地文件或远程 URL；输出可保存为 JSON 报告
"""
import argparse
import concurrent.futures as cf
import json
import os
import re
import sys
import time
import urllib3
import warnings
from collections import OrderedDict
from urllib.parse import urljoin

import requests

warnings.filterwarnings("ignore")
urllib3.disable_warnings()

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
PAGE_CT = ("text/html", "application/json", "text/xml", "application/xml")

PLAYLIST_RE = re.compile(r"#EXT-X-STREAM-INF:[^\n]*\n\s*(\S+)")
SEGMENT_RE = re.compile(r"#EXTINF:[^\n]*\n\s*(\S+)")


def make_session(insecure=False, workers=24):
    s = requests.Session()
    s.headers["User-Agent"] = UA
    s.verify = not insecure
    # 提高连接池上限，避免并发时大量新建连接
    from requests.adapters import HTTPAdapter
    s.mount("http://", HTTPAdapter(pool_connections=workers, pool_maxsize=workers))
    s.mount("https://", HTTPAdapter(pool_connections=workers, pool_maxsize=workers))
    return s


def _bounded_text(resp, limit=300000):
    """有界读取响应体前 limit 字节，避免直播流无界下载"""
    buf = bytearray()
    try:
        for chunk in resp.iter_content(chunk_size=8192):
            buf.extend(chunk)
            if len(buf) >= limit:
                break
    except Exception:  # noqa: BLE001
        pass
    return bytes(buf[:limit]).decode("utf-8", "ignore")


def probe_stream(session, url, timeout=(4, 6), depth=0, max_depth=2):
    """探测一个直播 URL 是否真的能播（返回 True/False）"""
    r = None
    try:
        r = session.get(url, timeout=timeout, stream=True, allow_redirects=True)
        if not r.ok:
            return False
        ct = (r.headers.get("Content-Type") or "").lower().split(";")[0].strip()
        if ct in PAGE_CT:
            return False

        is_playlist = (url.lower().split("?")[0].endswith((".m3u8", ".m3u"))
                       or "mpegurl" in ct)
        if is_playlist:
            body = _bounded_text(r)
            r.close()
            r = None
            if "#EXTM3U" not in body:
                return False
            if "#EXT-X-STREAM-INF" in body:
                m = PLAYLIST_RE.search(body)
                if not m:
                    return False
                return probe_stream(session, urljoin(url, m.group(1).strip()),
                                    timeout, depth + 1, max_depth)
            segs = SEGMENT_RE.findall(body)
            if not segs:
                return False
            ok = 0
            for seg in segs[:2]:
                seg_url = urljoin(url, seg.strip())
                try:
                    rr = session.get(seg_url, timeout=timeout, stream=True, allow_redirects=True)
                    if rr.ok and "text/html" not in (rr.headers.get("Content-Type") or "").lower():
                        for _ in rr.iter_content(chunk_size=512):
                            ok += 1
                            break
                    rr.close()
                except Exception:  # noqa: BLE001
                    pass
            return ok >= 1

        # 非 playlist 直流：能读到首包即视为可用
        for _ in r.iter_content(chunk_size=512):
            return True
        return False
    except Exception:  # noqa: BLE001
        return False
    finally:
        if r is not None:
            r.close()


def load_live(path_or_url, session):
    if path_or_url and os.path.exists(path_or_url):
        print(f"读取本地文件: {path_or_url}")
        with open(path_or_url, encoding="utf-8") as f:
            return f.read()
    url = path_or_url or "https://cdn.jsdelivr.net/gh/Lightconer/TVBox-Sources@main/output/live.txt"
    print(f"拉取: {url}")
    try:
        return session.get(url, timeout=25).text
    except Exception as e:  # noqa: BLE001
        print(f"网络拉取失败({e})，改用本地 output/live.txt")
        with open("output/live.txt", encoding="utf-8") as f:
            return f.read()


def parse_live(txt):
    cur, chan = None, OrderedDict()
    for line in txt.splitlines():
        if line.startswith("#") and not line.rstrip().endswith("#genre#"):
            continue
        if line.rstrip().endswith("#genre#"):
            cur = line.split(",")[0]
            chan.setdefault(cur, OrderedDict())
        elif "," in line and cur:
            n, u = line.split(",", 1)
            chan[cur].setdefault(n.strip(), []).append(u.strip())
    return chan


def main():
    ap = argparse.ArgumentParser(description="直播源分片级严格校验")
    ap.add_argument("source", nargs="?", default="", help="本地 live.txt 路径或远程 URL")
    ap.add_argument("--workers", type=int, default=24)
    ap.add_argument("--insecure", action="store_true", help="关闭 TLS 校验（不推荐）")
    ap.add_argument("--json-out", default="", help="保存详细报告为 JSON")
    args = ap.parse_args()

    session = make_session(insecure=args.insecure, workers=args.workers)
    txt = load_live(args.source, session)
    chan = parse_live(txt)
    all_items = [(g, n, u) for g, ch in chan.items() for n, us in ch.items() for u in us]
    total = len(all_items)
    print(f"总频道 {sum(len(ch) for ch in chan.values())} 个，总 URL {total} 条，开始严格探测（分片级）...\n")
    if total == 0:
        print("没有可探测的 URL，退出")
        sys.exit(1)

    t0 = time.time()
    ok_flags = {}
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(probe_stream, session, u): (g, n, u) for g, n, u in all_items}
        done = 0
        for f in cf.as_completed(futs):
            g, n, u = futs[f]
            try:
                ok_flags[(g, n, u)] = f.result()
            except Exception:  # noqa: BLE001
                ok_flags[(g, n, u)] = False
            done += 1
            if done % 100 == 0:
                print(f"  进度 {done}/{total} ...")
    print(f"探测完成，耗时 {time.time()-t0:.0f}s\n")

    per_chan = OrderedDict()
    for (g, n, u), ok in ok_flags.items():
        slot = per_chan.setdefault((g, n), {"ok": 0, "total": 0, "urls": []})
        slot["total"] += 1
        slot["urls"].append({"url": u, "ok": ok})
        if ok:
            slot["ok"] += 1

    dead_chan = [(g, n, v) for (g, n), v in per_chan.items() if v["ok"] == 0]
    fragile = [(g, n, v) for (g, n), v in per_chan.items() if v["ok"] == 1]

    print(f"=== 可用源=0 的频道（必现播放失败）: {len(dead_chan)} 个 ===")
    for g, n, v in dead_chan:
        print(f"  [{g}] {n}  (共{v['total']}源, 全挂)")
    print(f"\n=== 仅 1 个可用源的脆弱频道: {len(fragile)} 个 ===")
    for g, n, v in fragile[:30]:
        print(f"  [{g}] {n}  (共{v['total']}源, 仅1可用)")

    ok_total = sum(1 for v in ok_flags.values() if v)
    pct = ok_total * 100 // total if total else 0
    print(f"\n=== 汇总 ===")
    print(f"总 URL: {total}, 严格可用: {ok_total} ({pct}%)")
    print(f"频道总数: {len(per_chan)}, 全挂频道: {len(dead_chan)}, 脆弱频道: {len(fragile)}")

    if args.json_out:
        report = {
            "total_urls": total, "ok_urls": ok_total, "pct": pct,
            "channels": len(per_chan), "dead_channels": len(dead_chan),
            "fragile_channels": len(fragile),
            "dead": [{"group": g, "name": n, **v} for g, n, v in dead_chan],
        }
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"报告已保存: {args.json_out}")


if __name__ == "__main__":
    main()
