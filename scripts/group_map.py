"""中文分组映射模块：按频道名（含原分组名）将频道归入统一中文分组

规则来自 config.yaml 的 output.group_map.rules，顺序匹配、先命中先生效。
例如：
  CCTV-1 综合      -> 央视
  湖南卫视          -> 卫视
  河北都市          -> 河北
  凤凰中文          -> 港澳台
  欧洲足球          -> 体育
"""
import re


def _compile_rules(raw_rules):
    """把配置规则编译为 [(group, regexp), ...]，非法规则跳过"""
    rules = []
    for r in raw_rules or []:
        group = r.get("group", "").strip()
        pattern = r.get("pattern", "")
        if not group or not pattern:
            continue
        try:
            rules.append((group, re.compile(pattern)))
        except re.error:
            continue
    return rules


def apply_group_map(channels, cfg):
    """按配置把每个频道的分组重写为中文分组；未启用则原样返回"""
    gm = (cfg.get("output") or {}).get("group_map") or {}
    if not gm.get("enabled", True):
        return channels
    rules = _compile_rules(gm.get("rules", []))
    if not rules:
        return channels

    for ch in channels:
        # 同时匹配频道名与源自带分组，命中率更高
        haystack = f"{ch.name} {ch.group}"
        for group, pat in rules:
            if pat.search(haystack):
                ch.group = group
                break
    return channels
