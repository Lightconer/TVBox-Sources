"""输出模块：把择优后的频道写成 TVBox/影视仓兼容的 m3u、txt 与 json"""
import json
import os
from dataclasses import asdict


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


def write_json(channels, path, meta):
    """结构化 json（便于程序读取/做聚合源），元信息在前"""
    data = {
        "meta": meta,
        "total": len(channels),
        "channels": [asdict(c) for c in channels],
    }
    with _open(path) as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


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
