# -*- coding: utf-8 -*-
"""诊断脚本：用本地(国内)网络对远程 live.txt 做分片级严格校验，找出"播放失败"频道。

对每个 URL：
  1. HTTP 首包（status<400 + content-type 非页面）
  2. 若是 .m3u8/.m3u：解析 playlist（含 master→variant 递归），探测前 2 个分片能否下载
统计每个频道可用源数 → 可用源=0 的频道就是会"播放失败请更换频道"的频道。
"""
import concurrent.futures as cf
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
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
PAGE_CT = ("text/html", "application/json", "text/xml", "application/xml")
S = requests.Session()
S.headers["User-Agent"] = UA
S.verify = False

PLAYLIST_RE = re.compile(r"#EXT-X-STREAM-INF:[^\n]*\n\s*(\S+)")
SEGMENT_RE = re.compile(r"#EXTINF:[^\n]*\n\s*(\S+)")


def probe_stream(url, timeout=(4, 6), depth=0):
    """探测一个直播 URL 是否真的能播（返回 True/False）。"""
    try:
        r = S.get(url, timeout=timeout, stream=True, allow_redirects=True)
        if not r.ok:
            return False
        ct = (r.headers.get("Content-Type") or "").lower().split(";")[0].strip()
        if ct in PAGE_CT:
            return False
        if url.lower().endswith((".m3u8", ".m3u")) or "mpegurl" in ct or depth < 2:
            # 尝试解析 playlist
            try:
                body = r.content[:300000].decode("utf-8", "ignore")
            except Exception:
                body = ""
            r.close()
            if "#EXTM3U" in body:
                if "#EXT-X-STREAM-INF" in body:  # master playlist → 取第一个变体
                    m = PLAYLIST_RE.search(body)
                    if m:
                        return probe_stream(urljoin(url, m.group(1).strip()), timeout, depth + 1)
                    return False
                segs = SEGMENT_RE.findall(body)
                if not segs:  # 无分片，可能是 VOD/空流
                    return False
                # 探测前 2 个分片
                ok = 0
                for seg in segs[:2]:
                    seg_url = urljoin(url, seg.strip())
                    try:
                        rr = S.get(seg_url, timeout=timeout, stream=True, allow_redirects=True)
                        if rr.ok:
                            sct = (rr.headers.get("Content-Type") or "").lower()
                            if "text/html" not in sct:
                                for _ in rr.iter_content(chunk_size=512):
                                    break
                                ok += 1
                        rr.close()
                    except Exception:
                        pass
                return ok >= 1
            # 非 m3u8 内容（http-flv / ts 直链）：能读到首包即可
            for _ in r.iter_content(chunk_size=512):
                return True
            return False
        # 非 playlist 协议
        for _ in r.iter_content(chunk_size=512):
            r.close()
            return True
        r.close()
        return False
    except Exception:
        return False


def main():
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        print(f"读取本地文件: {sys.argv[1]}")
        txt = open(sys.argv[1], encoding="utf-8").read()
    else:
        live_url = sys.argv[1] if len(sys.argv) > 1 else \
            "https://cdn.jsdelivr.net/gh/Lightconer/TVBox-Sources@main/output/live.txt"
        print(f"拉取: {live_url}")
        try:
            txt = S.get(live_url, timeout=25).text
        except Exception as e:
            print(f"网络拉取失败({e})，改用本地 output/live.txt")
            txt = open("output/live.txt", encoding="utf-8").read()
    lines = txt.splitlines()

    cur = None
    chan = OrderedDict()
    for l in lines:
        if l.endswith("#genre#"):
            cur = l.split(",")[0]
            chan.setdefault(cur, OrderedDict())
        elif "," in l and cur:
            n, u = l.split(",", 1)
            chan[cur].setdefault(n, []).append(u)

    all_items = [(g, n, u) for g, ch in chan.items() for n, us in ch.items() for u in us]
    print(f"总频道 {sum(len(ch) for ch in chan.values())} 个，总 URL {len(all_items)} 条，开始严格探测（分片级）...\n")

    t0 = time.time()
    ok_flags = {}
    with cf.ThreadPoolExecutor(max_workers=24) as ex:
        futs = {ex.submit(probe_stream, u): (g, n, u) for g, n, u in all_items}
        done = 0
        for f in cf.as_completed(futs):
            g, n, u = futs[f]
            try:
                ok_flags[(g, n, u)] = f.result()
            except Exception:
                ok_flags[(g, n, u)] = False
            done += 1
            if done % 100 == 0:
                print(f"  进度 {done}/{len(all_items)} ...")
    print(f"探测完成，耗时 {time.time()-t0:.0f}s\n")

    # 聚合：每频道可用源数
    per_chan = OrderedDict()
    for (g, n, u), ok in ok_flags.items():
        per_chan.setdefault((g, n), {"ok": 0, "total": 0, "urls": []})
        per_chan[(g, n)]["total"] += 1
        per_chan[(g, n)]["urls"].append((u, ok))
        if ok:
            per_chan[(g, n)]["ok"] += 1

    dead_chan = [(g, n, v) for (g, n), v in per_chan.items() if v["ok"] == 0]
    fragile = [(g, n, v) for (g, n), v in per_chan.items() if v["ok"] == 1]

    print(f"=== 可用源=0 的频道（必现播放失败）: {len(dead_chan)} 个 ===")
    for g, n, v in dead_chan:
        print(f"  [{g}] {n}  (共{v['total']}源, 全挂)")
    print(f"\n=== 仅 1 个可用源的脆弱频道: {len(fragile)} 个 ===")
    for g, n, v in fragile[:30]:
        print(f"  [{g}] {n}  (共{v['total']}源, 仅1可用)")

    ok_total = sum(1 for v in ok_flags.values() if v)
    print(f"\n=== 汇总 ===")
    print(f"总 URL: {len(ok_flags)}, 严格可用: {ok_total} ({ok_total*100//len(ok_flags)}%)")
    print(f"频道总数: {len(per_chan)}, 全挂频道: {len(dead_chan)}, 脆弱频道: {len(fragile)}")


if __name__ == "__main__":
    main()
