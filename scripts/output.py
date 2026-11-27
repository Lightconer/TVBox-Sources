"""写出 TVBox / 影视仓兼容的 m3u、txt 和配置接口。"""
import json
import os
import re


def _open(path):
    """统一以 UTF-8 + LF 换行写出"""
    return open(path, "w", encoding="utf-8", newline="\n")


def _esc(v):
    return str(v or "").replace('"', "&quot;")


def write_m3u(channels, path, header=None):
    """标准 #EXTM3U 格式"""
    lines = ["#EXTM3U"]
    if header:
        lines.append("#EXTENC:UTF-8")
        lines.append(f"#PLAYLIST:{header.get('name', '')}")
    current_group = None
    for ch in channels:
        if ch.group != current_group:
            lines.append(f"#EXTGRP:{ch.group}")
            current_group = ch.group
        attrs = []
        if ch.tvg_id:
            attrs.append(f'tvg-id="{_esc(ch.tvg_id)}"')
        attrs.append(f'group-title="{_esc(ch.group)}"')
        if ch.tvg_logo:
            attrs.append(f'tvg-logo="{_esc(ch.tvg_logo)}"')
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
    """生成 shields.io 兼容的 badge 数据"""
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
    """根据 output.header.url 推导 raw 与 jsdelivr 直链基地址"""
    out = cfg.get("output") or {}
    header = out.get("header") or {}
    url = header.get("url", "") or ""
    repo = (out.get("repo") or "").strip().strip("/")
    if not repo:
        m = re.search(r"github\.com/([^/]+)/([^/#?]+)", url)
        if m:
            repo = f"{m.group(1)}/{m.group(2)}"
    repo = re.sub(r"\.git$", "", repo)
    if not repo or "/" not in repo:
        return None, None
    branch = (out.get("branch") or "main").strip() or "main"
    return (
        f"https://raw.githubusercontent.com/{repo}/{branch}/output",
        f"https://cdn.jsdelivr.net/gh/{repo}@{branch}/output",
    )


def write_tvbox_config(path, cfg):
    """生成 TVBox/影视仓「配置接口」json：填入配置地址即可一键导入"""
    raw_base, jsd_base = derive_links(cfg)
    live_url = f"{jsd_base}/live.txt" if jsd_base else ""
    m3u_url = f"{jsd_base}/live.m3u" if jsd_base else ""
    epg = ((cfg.get("output") or {}).get("epg") or "").strip()
    data = {
        "lives": [
            {"name": "自动更新直播源 (TXT)", "url": live_url, "epg": epg},
            {"name": "自动更新直播源 (M3U)", "url": m3u_url, "epg": epg},
        ],
        "sites": [],
        "spider": "",
    }
    with _open(path) as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
