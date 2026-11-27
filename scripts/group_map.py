"""按频道名归入中文分组，支持一个频道同时属于多个专题组。"""
import re
from copy import copy


def _compile_rules(raw_rules):
    """把配置规则编译为 [(group, regexp), ...]，非法规则跳过"""
    from utils import LOGGER
    rules = []
    for r in raw_rules or []:
        group = str(r.get("group", "")).strip()
        pattern = r.get("pattern", "")
        if not group or not pattern:
            continue
        try:
            rules.append((group, re.compile(pattern, re.IGNORECASE)))
        except re.error as e:
            LOGGER.warning("非法分组规则已跳过: %s -> %s (%s)", group, pattern, e)
    return rules


def apply_group_map(channels, cfg):
    """按配置重写频道分组；未启用则原样返回。

    - 主分组 rules：先命中先生效，写入 ch.group（唯一）
    - 专题组 theme_groups：全部命中都追加到 ch.groups（可多个），实现跨组
    - 未命中任何规则的频道 groups=[其原分组]
    """
    gm = (cfg.get("output") or {}).get("group_map") or {}
    if not gm.get("enabled", True):
        return channels
    primary = _compile_rules(gm.get("rules", []))
    themes = _compile_rules(gm.get("theme_groups", []))
    if not primary and not themes:
        return channels

    multi = bool(gm.get("multi_group", True))

    # 识别兜底主组（最后一条 pattern=".*" 的规则）：
    # 若频道只命中兜底组却命中了专题组，则把专题组提升为主组，
    # 避免出现「五星体育 → 其他频道 + 体育专区」这种冗余。
    rules_raw = gm.get("rules", []) or []
    fallback_group = None
    if rules_raw and str(rules_raw[-1].get("pattern", "")).strip() in (".*", ".+"):
        fallback_group = str(rules_raw[-1].get("group", "")).strip() or None

    for ch in channels:
        # 先只看频道名匹配主分组，命中就定下来；
        # 频道名信息量不足时（如「1套」「综合」），再带上源自带分组合并匹配。
        # 这样「河北卫视」不会因为源分组叫「卫视频道」而被卫视规则抢走。
        name_only = ch.name or ""
        haystack = f"{name_only} {ch.group}"

        # 1) 主分组：唯一
        main_group = None
        for group, pat in primary:
            if group == fallback_group:
                continue
            if pat.search(name_only):
                main_group = group
                break
        if not main_group:
            for group, pat in primary:
                if pat.search(haystack):
                    main_group = group
                    break

        # 2) 专题组命中列表：只匹配频道名，不用源自带分组
        #    （某些源把 group-title 标成「4K频道/体育」等，会导致东方卫视被误分到 4K 组）
        hit_themes = [group for group, pat in themes if pat.search(name_only)]

        # 兜底组让位：只命中兜底且有专题命中 → 首个专题组升为主组
        if main_group == fallback_group and hit_themes:
            main_group = hit_themes[0]

        if main_group:
            ch.group = main_group

        # 3) 专题组跨组追加
        if multi:
            groups = [ch.group]
            for group in hit_themes:
                if group not in groups:
                    groups.append(group)
            # 去重保序
            seen = set()
            ch.groups = [g for g in groups if not (g in seen or seen.add(g))]
        else:
            ch.groups = [ch.group]
    return channels


def expand_groups(channels, cfg):
    """把跨组频道展开成「每个归属组一条」的副本，用于输出。

    - 每条副本 group 设为对应归属组，保留同名/同 URL（TVBox 中同一地址可出现在多组）
    - 按 output.group_map.max_per_group 对每个组限流（0=不限）
    - 返回展开后的新列表（不修改输入对象的 group）
    """
    gm = (cfg.get("output") or {}).get("group_map") or {}
    if not gm.get("multi_group", True):
        return channels
    max_per_group = int(gm.get("max_per_group", 0) or 0)

    expanded, per_group = [], {}
    for ch in channels:
        groups = getattr(ch, "groups", None) or [ch.group]
        for g in groups:
            if max_per_group > 0 and per_group.get(g, 0) >= max_per_group:
                continue
            c = copy(ch)
            c.group = g
            c.groups = [g]
            expanded.append(c)
            per_group[g] = per_group.get(g, 0) + 1
    return expanded
