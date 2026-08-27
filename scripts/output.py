"""输出模块：把择优后的频道写成 TVBox/影视仓兼容的 m3u、txt 与配置接口"""
import json
import os
import re


def _open(path):
    """统一以 UTF-8 + LF 换行写出（跨平台稳定，Linux 上跑无影响）"""
    return open(path, "w", encoding="utf-8", newline="\n")


def write_m3u(channels, path, header=None):
    """标准 #EXTM3U 格式（TVBox/影视仓/IP-TV 播放器通用）"""
    lines = ["#EXTM3U"]
    if header:
        lines.append(f"#EXTENC:UTF-8")
        lines.append(f"#PLAYLIST:{header.get('name', '')}")
    current_group = None
    for ch in channels:
        if ch.group != current_group:
            lines.append(f"#EXTGRP:{ch.group}")
            current_group = ch.group
        attrs = []
        if ch.tvg_id:
            attrs.append(f'tvg-id="{ch.tvg_id}"')
        attrs.append(f'group-title="{ch.group}"')
        if ch.tvg_logo:
            attrs.append(f'tvg-logo="{ch.tvg_logo}"')
        lines.append(f"#EXTINF:-1 {' '.join(attrs)},{ch.name}")
        lines.append(ch.url)
    with _open(path) as f:
        f.write("\n".join(lines) + "\n")


def write_txt(channels, path):
    """TVBox/影视仓 txt 格式：#genre# 分组头 + 「频道名,url」"""
    lines = []
    current_group = None
    for ch in channels:
        if ch.group != current_group:
            lines.append(f"{ch.group},#genre#")
            current_group = ch.group
        lines.append(f"{ch.name},{ch.url}")
    with _open(path) as f:
        f.write("\n".join(lines) + "\n")


def write_badge(path, text, color="brightgreen"):
    """生成 shields.io 兼容的 badge 数据，用于 README 展示"""
    badge = {
        "schemaVersion": 1,
        "label": "直播源",
        "message": text,
        "color": color,
        "isError": False,
    }
    with _open(path) as f:
        json.dump(badge, f, ensure_ascii=False)


def derive_links(cfg):
    """根据 output.header.url 推导 raw 与 jsdelivr 直链基地址（用于生成配置接口）"""
    url = (cfg.get("output") or {}).get("header", {}).get("url", "")
    m = re.search(r"github\.com/([^/]+)/([^/]+)", url or "")
    if not m:
        return None, None
    owner, repo = m.group(1), m.group(2)
    raw_base = f"https://raw.githubusercontent.com/{owner}/{repo}/main/output"
    jsd_base = f"https://cdn.jsdelivr.net/gh/{owner}/{repo}@main/output"
    return raw_base, jsd_base


def write_tvbox_config(path, cfg):
    """生成 TVBox/影视仓「配置接口」json：填入配置地址即可一键导入直播源"""
    raw_base, jsd_base = derive_links(cfg)
    live_url = f"{jsd_base}/live.txt" if jsd_base else ""
    m3u_url = f"{jsd_base}/live.m3u" if jsd_base else ""
    data = {
        "lives": [
            {"name": "自动更新直播源 (TXT)", "url": live_url, "epg": ""},
            {"name": "自动更新直播源 (M3U)", "url": m3u_url, "epg": ""},
        ],
        "sites": [],
        "spider": "",
    }
    with _open(path) as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
