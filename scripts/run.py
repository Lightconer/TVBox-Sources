"""主流程：爬取 → 分组 → 测速 → 择优 → 生成输出。

用法：
  python scripts/run.py                 # 完整流程
  python scripts/run.py --limit 50      # 只测前 50 个频道
  python scripts/run.py --skip-check    # 跳过测速
  python scripts/run.py --verify-only   # 校验现有输出
"""
import argparse
import json
import os
import sys
from datetime import datetime

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import setup_logging, LOGGER  # noqa: E402
from crawl import Channel, crawl_sources, apply_filters  # noqa: E402
from group_map import apply_group_map, expand_groups  # noqa: E402
from check import run_check, select_best, normalize_name, natural_sort_key  # noqa: E402
from output import (write_m3u, write_txt, write_badge, write_tvbox_config)  # noqa: E402


def now_beijing():
    """返回带时区的北京时间（Python 3.9+）"""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Shanghai"))
    except Exception:  # pragma: no cover - 极老版本兜底
        from datetime import timedelta, timezone
        return datetime.now(timezone(timedelta(hours=8)))


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def check_upstream(cfg):
    """查询上游最新 commit，返回可写入 status.json 的字典

    保证「及时更新」的可观测性：记录上游 commit sha / 推送时间 / 距离现在的滞后分钟数。
    GitHub API 未认证额度 60 次/小时，本项目每 4h 跑一次，完全够用；失败不影响主流程。
    """
    up = cfg.get("upstream") or {}
    if not up.get("check", True):
        return {"enabled": False}
    api = up.get("api") or (
        f"https://api.github.com/repos/{up.get('repo', '')}/commits"
        f"?path={up.get('file', '')}&per_page=1"
    )
    result = {
        "enabled": True,
        "repo": up.get("repo", ""),
        "file": up.get("file", ""),
        "ok": False,
    }
    try:
        from utils import make_session
        s = make_session(cfg.get("crawl", {}).get("request", {}))
        s.headers["Accept"] = "application/vnd.github+json"
        # 默认严格校验 TLS；仅在显式设置 IPTV_INSECURE_SSL=1 时关闭（代理/特殊网络环境）
        verify_ssl = os.environ.get("IPTV_INSECURE_SSL") != "1"
        try:
            resp = s.get(api, timeout=10, verify=verify_ssl)
            if resp.status_code != 200:
                result["error"] = f"HTTP {resp.status_code}"
                LOGGER.warning("上游状态查询失败: %s", result["error"])
                return result
            data = resp.json()
            if not isinstance(data, list) or not data:
                result["error"] = "empty commit list"
                return result
            commit = data[0]
            sha = commit.get("sha", "")
            dt = commit.get("commit", {}).get("committer", {}).get("date", "")
            result.update({
                "ok": True,
                "sha": sha[:12],
                "commit_time": dt,
                "message": commit.get("commit", {}).get("message", "").split("\n")[0][:80],
            })
            if dt:
                from datetime import timezone
                try:
                    t = datetime.fromisoformat(dt.replace("Z", "+00:00"))
                    lag = int((datetime.now(timezone.utc) - t).total_seconds() / 60)
                    result["lag_minutes"] = max(0, lag)
                    warn_at = int(up.get("stale_warn_minutes", 360))
                    if lag > warn_at:
                        result["stale"] = True
                        LOGGER.warning("上游 %s 已 %d 分钟未更新（阈值 %d 分钟）",
                                       up.get("repo", ""), lag, warn_at)
                except Exception:  # noqa: BLE001
                    pass
        finally:
            s.close()
    except Exception as e:  # noqa: BLE001
        result["error"] = str(e)[:120]
        LOGGER.warning("上游状态查询异常: %s", e)
    return result


def final_audit(selected, cfg):
    """输出前最终质量审计 + 去重：
    - 关键词双保险：命中失效标注关键词的强制剔除
    - 按 (分组, URL) 去重：跨组频道在不同组保留同一 URL，同组内重复才丢弃
    - 统计唯一频道数与冗余数
    """
    kw = [str(k).lower() for k in
          (cfg.get("crawl", {}).get("filters", {}) or {}).get("exclude_keywords", [])]
    keep, blocked, dup_urls = [], 0, 0
    seen = set()
    for ch in selected:
        n = (ch.name or "").lower()
        if any(k and k in n for k in kw):
            blocked += 1
            LOGGER.warning("审计拦截失效标注频道: %s", ch.name)
            continue
        # 跨组复用：同一 URL 在不同分组各自保留；同组重复才丢弃
        key = (ch.group, (ch.url or "").strip().lower())
        if key in seen:
            dup_urls += 1
            continue
        seen.add(key)
        keep.append(ch)

    channel_keys = {(ch.group, normalize_name(ch.name)) for ch in keep}
    audit = {
        "keyword_blocked": blocked,
        "dup_urls": dup_urls,
        "channels": len(channel_keys),
        "urls": len(keep),
        "redundant_channels": len(keep) - len(channel_keys),
    }
    return keep, audit


def verify_outputs(out_dir):
    """校验输出文件结构：格式行、分组头、URL 数量、JSON 可解析"""
    import glob
    problems = []
    m3u = os.path.join(out_dir, "live.m3u")
    txt = os.path.join(out_dir, "live.txt")
    if os.path.exists(m3u):
        head = open(m3u, encoding="utf-8").readline().strip()
        if head != "#EXTM3U":
            problems.append(f"live.m3u 首行不是 #EXTM3U: {head!r}")
    if os.path.exists(txt):
        n_genre = sum(1 for l in open(txt, encoding="utf-8") if l.rstrip().endswith("#genre#"))
        if n_genre == 0:
            problems.append("live.txt 缺少 #genre# 分组头")
    for name in ("status.json", "badge.json", "tvbox.json"):
        p = os.path.join(out_dir, name)
        if not os.path.exists(p):
            problems.append(f"缺少 {name}")
            continue
        try:
            with open(p, encoding="utf-8") as f:
                json.load(f)
        except Exception as e:  # noqa: BLE001
            problems.append(f"{name} 不是合法 JSON: {e}")
    if problems:
        for p in problems:
            LOGGER.error("校验失败: %s", p)
        return 1
    LOGGER.info("输出文件结构校验通过（%s）", out_dir)
    return 0


def main():
    parser = argparse.ArgumentParser(description="直播源自动更新")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--limit", type=int, default=0, help="仅测速前 N 个频道（本地调试）")
    parser.add_argument("--skip-check", action="store_true", help="跳过测速（覆盖配置）")
    parser.add_argument("--verify-only", action="store_true", help="仅校验现有输出")
    args = parser.parse_args()

    setup_logging()
    cfg = load_config(args.config)

    out_cfg = cfg.get("output", {})
    out_dir = out_cfg.get("dir", "output")
    os.makedirs(out_dir, exist_ok=True)

    if args.verify_only:
        sys.exit(verify_outputs(out_dir))

    if args.limit:
        cfg["_limit"] = args.limit

    now = now_beijing()
    date_str = now.strftime("%Y-%m-%d %H:%M:%S")
    check_cfg = cfg.get("check", {})
    do_check = bool(check_cfg.get("enabled", True)) and not args.skip_check

    # ---------- 1. 爬取 ----------
    LOGGER.info("==> 步骤1/4 爬取数据源")
    raw = crawl_sources(cfg)
    LOGGER.info("爬取到原始频道 %d 个", len(raw))
    if not raw:
        LOGGER.error("未爬取到任何频道，终止（保留上次有效输出）")
        sys.exit(1)

    channels = apply_filters(raw, cfg)
    LOGGER.info("过滤去重后剩余 %d 个", len(channels))
    if not channels:
        LOGGER.error("过滤后无可用频道，终止")
        sys.exit(1)

    channels = apply_group_map(channels, cfg)
    group_counts = {}
    for c in channels:
        group_counts[c.group] = group_counts.get(c.group, 0) + 1
    LOGGER.info("中文分组映射后分组数 %d：%s", len(group_counts),
                "、".join(f"{k}({v})" for k, v in
                          sorted(group_counts.items(), key=lambda x: -x[1])[:12]))

    meta = {
        "update_time": date_str,
        "sources": [s.get("name") or s.get("url", "")
                    for s in cfg["crawl"].get("sources", []) if s.get("enabled", True)],
        "crawled": len(raw),
        "dedup": len(channels),
        "upstream": check_upstream(cfg),
    }
    if meta["upstream"].get("ok"):
        LOGGER.info("上游 %s 最新提交 %s（%s，滞后 %s 分钟）",
                    meta["upstream"].get("repo"), meta["upstream"].get("sha"),
                    meta["upstream"].get("commit_time"), meta["upstream"].get("lag_minutes"))

    # ---------- 2. 测速校验 ----------
    LOGGER.info("==> 步骤2/4 测速校验%s", "" if do_check else "（已跳过）")
    if do_check:
        results = run_check(channels, cfg)
        http_ok = [r for r in results if r.ok]
        selected, sel_passed = select_best(results, cfg)
        dead = len(results) - len(sel_passed)
        avg_lat = int(sum(r.latency_ms for r in sel_passed) / len(sel_passed)) if sel_passed else 0
        LOGGER.info("HTTP 可达 %d/%d，通过择优 %d，丢弃 %d，平均延迟 %dms",
                    len(http_ok), len(results), len(selected), dead, avg_lat)
        meta.update({
            "http_ok": len(http_ok), "passed": len(selected), "dead": dead,
            "groups": len({c.group for c in selected}), "avg_latency_ms": avg_lat,
        })
    else:
        selected, passed = channels, []
        meta.update({"http_ok": len(channels), "passed": len(channels), "dead": 0,
                     "note": "check disabled"})

    # ---------- 3. 跨组展开 + 最终审计 ----------
    # 先展开跨组（同一频道复制到其归属的每个专题组），再审计；
    # final_audit 按 (组, URL) 去重，保证跨组副本不会被误删。
    gm_cfg = (cfg.get("output") or {}).get("group_map") or {}
    before_expand = len(selected)
    selected = expand_groups(selected, cfg)
    if len(selected) != before_expand:
        LOGGER.info("跨组展开：%d 条 → %d 条（增加 %d 条跨组副本）",
                    before_expand, len(selected), len(selected) - before_expand)

    selected, audit = final_audit(selected, cfg)
    LOGGER.info("最终审计：拦截失效标注 %d，重复地址 %d，唯一频道 %d，多源冗余频道 %d",
                audit["keyword_blocked"], audit["dup_urls"],
                audit["channels"], audit["redundant_channels"])
    meta.update({
        "keyword_blocked": audit["keyword_blocked"],
        "dup_urls": audit["dup_urls"],
        "channels": audit["channels"],
        "redundant_channels": audit["redundant_channels"],
        "cross_group_copies": max(0, len(selected) - before_expand),
        "multi_group": bool(gm_cfg.get("multi_group", True)),
    })

    if not selected:
        LOGGER.error("择优后无可用频道，终止（保留上次有效输出）")
        sys.exit(1)

    # ---------- 4. 排序 + 输出 ----------
    gm = (cfg.get("output") or {}).get("group_map", {})
    # 展示顺序：优先用 output.group_order；没列到的分组按 rules + theme_groups 顺序补齐
    base_order = [r.get("group") for r in gm.get("rules", []) if r.get("group")]
    base_order += [r.get("group") for r in gm.get("theme_groups", []) if r.get("group")]
    configured = [str(g) for g in (out_cfg.get("group_order") or []) if g]
    order = configured + [g for g in base_order if g not in configured]
    seen_order = set()
    order = [g for g in order if not (g in seen_order or seen_order.add(g))]
    idx = {g: i for i, g in enumerate(order)}
    # 分组顺序在前；同组内按频道名数字升序（CCTV-1 → CCTV-2 → CCTV-10），
    # 同一频道的多条线路保持 select_best 已排好的「画质优先、同档看速度」顺序。
    # output.group_sort 可给指定分组固定排序（如河北：河北卫视 → 省级 → 各市台）。
    group_sort = {k: [str(p) for p in v] for k, v in
                  ((out_cfg.get("group_sort") or {}).items())}

    def _sort_key(c):
        prefixes = group_sort.get(c.group) or []
        pos = len(prefixes)
        for i, p in enumerate(prefixes):
            if p and (c.name or "").startswith(p):
                pos = i
                break
        return (idx.get(c.group, len(order)), pos, natural_sort_key(c.name))

    selected.sort(key=_sort_key)
    LOGGER.info("分组与频道排序完成，共 %d 个分组", len({c.group for c in selected}))

    LOGGER.info("==> 步骤3/4 生成输出（%d 个频道，%d 个地址）",
                audit["channels"], len(selected))
    header = dict(out_cfg.get("header", {}) or {})
    header["name"] = f"{header.get('name', '自动更新直播源')} (更新于 {date_str})"

    # 列表顶部的「更新时间」条目：让使用者在播放器里一眼看到数据是几点抓的
    notice_cfg = dict(out_cfg.get("notice") or {})
    notice = None
    if notice_cfg.get("enabled", True):
        tpl = str(notice_cfg.get("text") or "{time} 更新 · {channels} 个频道")
        notice = {
            "enabled": True,
            "group": notice_cfg.get("group") or "更新时间",
            "name": tpl.format(time=date_str[:16], channels=audit["channels"],
                               lines=len(selected)),
            "url": notice_cfg.get("url") or "http://127.0.0.1/",
        }

    for fmt in out_cfg.get("formats", ["m3u", "txt"]):
        path = os.path.join(out_dir, f"live.{fmt}")
        if fmt == "m3u":
            write_m3u(selected, path, header, notice)
        elif fmt == "txt":
            # 时间信息以注释形式写在首行，TVBox/影视仓会忽略 # 开头的非 genre 行
            write_txt(selected, path, notice)
            with open(path, "r+", encoding="utf-8", newline="\n") as f:
                body = f.read()
                f.seek(0)
                f.write(f"#更新时间：{date_str}\n" + body)
        LOGGER.info("  已生成 %s", path)

    write_badge(os.path.join(out_dir, "badge.json"),
                f"{date_str[:16]} · {audit['channels']} 频道",
                "brightgreen" if selected else "yellow")
    with open(os.path.join(out_dir, "status.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    write_tvbox_config(os.path.join(out_dir, "tvbox.json"), cfg)

    LOGGER.info("==> 步骤4/4 校验输出")
    rc = verify_outputs(out_dir)
    if rc:
        sys.exit(rc)
    LOGGER.info("状态与徽章已写入 %s", out_dir)
    LOGGER.info("==> 全部完成")


if __name__ == "__main__":
    main()
