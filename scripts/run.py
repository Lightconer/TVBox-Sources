"""主流程：爬取 → 过滤去重 → 测速校验 → 择优 → 生成三格式输出 + 状态/徽章

用法：
  python scripts/run.py                          # 完整流程
  python scripts/run.py --limit 50               # 本地调试：只测速前 50 个
  python scripts/run.py --skip-check             # 只爬取生成，不做测速
  python scripts/run.py --config config/config.yaml
"""
import argparse
import json
import os
import sys
from datetime import datetime

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import setup_logging, LOGGER  # noqa: E402
from crawl import crawl_sources, apply_filters  # noqa: E402
from group_map import apply_group_map  # noqa: E402
from check import run_check, select_best, normalize_name  # noqa: E402
from output import write_m3u, write_txt, write_badge, write_tvbox_config  # noqa: E402


def final_audit(selected, cfg):
    """输出前最终质量审计：
    - 关键词双保险：频道名命中失效标注关键词的强制剔除（防分组改名后漏网）；
    - 不重复校验：统计重复 URL（同一地址出现多次，应为 0）；
    - 冗余覆盖：统计唯一频道数与多源冗余频道数（冗余是 TVBox 自动切换的正常特性）。
    返回 (过滤后的频道, 审计统计)
    """
    kw = [str(k).lower() for k in
          (cfg.get("crawl", {}).get("filters", {}) or {}).get("exclude_keywords", [])]
    keep, blocked = [], 0
    for ch in selected:
        n = (ch.name or "").lower()
        if any(k and k in n for k in kw):
            blocked += 1
            LOGGER.warning("审计拦截失效标注频道: %s", ch.name)
            continue
        keep.append(ch)

    seen_urls, dup_urls = set(), 0
    channel_keys, url_count = set(), 0
    for ch in keep:
        url_count += 1
        if ch.url in seen_urls:
            dup_urls += 1
        else:
            seen_urls.add(ch.url)
        channel_keys.add((ch.group, normalize_name(ch.name)))

    audit = {
        "keyword_blocked": blocked,
        "dup_urls": dup_urls,
        "channels": len(channel_keys),        # 唯一频道数
        "urls": url_count,                    # 总地址数（含冗余）
        "redundant_channels": url_count - len(channel_keys),  # 多源冗余频道数
    }
    return keep, audit


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main():
    parser = argparse.ArgumentParser(description="TVBox 直播源自动更新")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--limit", type=int, default=0, help="仅测速前 N 个频道（本地调试）")
    parser.add_argument("--skip-check", action="store_true", help="只爬取生成，不做测速")
    args = parser.parse_args()

    setup_logging()
    cfg = load_config(args.config)
    if args.limit:
        cfg["_limit"] = args.limit

    out_cfg = cfg.get("output", {})
    out_dir = out_cfg.get("dir", "output")
    os.makedirs(out_dir, exist_ok=True)

    # ---------- 1. 爬取 ----------
    LOGGER.info("==> 步骤1/3 爬取数据源")
    raw = crawl_sources(cfg)
    LOGGER.info("爬取到原始频道 %d 个", len(raw))
    if not raw:
        LOGGER.error("未爬取到任何频道，终止（保留上次有效输出，不产生空提交）")
        sys.exit(1)

    channels = apply_filters(raw, cfg)
    LOGGER.info("过滤去重后剩余 %d 个", len(channels))
    if not channels:
        LOGGER.error("过滤后无可用频道，终止")
        sys.exit(1)

    # 按频道名映射为统一中文分组（央视/卫视/各省/专题…）
    channels = apply_group_map(channels, cfg)
    group_counts = {}
    for c in channels:
        group_counts[c.group] = group_counts.get(c.group, 0) + 1
    LOGGER.info("中文分组映射后分组数 %d：%s",
                len(group_counts),
                "、".join(f"{k}({v})" for k, v in sorted(group_counts.items(), key=lambda x: -x[1])[:12]))

    now = datetime.now()
    meta = {
        "update_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "sources": [s.get("name") or s.get("url", "")
                    for s in cfg["crawl"].get("sources", []) if s.get("enabled", True)],
        "crawled": len(raw),
        "dedup": len(channels),
    }

    # ---------- 2. 测速校验 ----------
    LOGGER.info("==> 步骤2/3 测速校验")
    if args.skip_check:
        selected, passed = channels, []
        meta.update({"http_ok": len(channels), "passed": len(channels), "dead": 0, "note": "skip-check"})
    else:
        results = run_check(channels, cfg)
        http_ok = [r for r in results if r.ok]
        selected, sel_passed = select_best(results, cfg)
        dead = len(results) - len(sel_passed)
        avg_lat = 0
        if sel_passed:
            avg_lat = int(sum(r.latency_ms for r in sel_passed) / len(sel_passed))
        LOGGER.info("HTTP 可达 %d/%d，通过择优 %d，丢弃 %d，平均延迟 %dms",
                    len(http_ok), len(results), len(selected), dead, avg_lat)
        meta.update({
            "http_ok": len(http_ok),
            "passed": len(selected),
            "dead": dead,
            "groups": len({c.group for c in selected}),
            "avg_latency_ms": avg_lat,
        })

    # ---------- 3. 最终质量审计（关键词双保险 + 不重复校验）----------
    selected, audit = final_audit(selected, cfg)
    LOGGER.info("最终审计：拦截失效标注 %d，重复地址 %d，唯一频道 %d，多源冗余频道 %d",
                audit["keyword_blocked"], audit["dup_urls"],
                audit["channels"], audit["redundant_channels"])
    meta.update({
        "keyword_blocked": audit["keyword_blocked"],
        "dup_urls": audit["dup_urls"],
        "channels": audit["channels"],
        "redundant_channels": audit["redundant_channels"],
    })

    # ---------- 4. 生成输出 ----------
    LOGGER.info("==> 步骤3/3 生成输出（%d 个频道）", len(selected))
    header = out_cfg.get("header", {})
    for fmt in out_cfg.get("formats", ["m3u", "txt"]):
        path = os.path.join(out_dir, f"live.{fmt}")
        if fmt == "m3u":
            write_m3u(selected, path, header)
        elif fmt == "txt":
            write_txt(selected, path)
        LOGGER.info("  已生成 %s", path)

    # 状态 + 徽章
    with open(os.path.join(out_dir, "status.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    write_badge(
        os.path.join(out_dir, "badge.json"),
        f"{meta['update_time'][:16]} · {len(selected)} 频道",
        "brightgreen" if selected else "yellow",
    )
    # TVBox/影视仓配置接口（一键导入）
    write_tvbox_config(os.path.join(out_dir, "tvbox.json"), cfg)
    LOGGER.info("状态与徽章已写入 %s", out_dir)
    LOGGER.info("==> 全部完成 ✔")


if __name__ == "__main__":
    main()
