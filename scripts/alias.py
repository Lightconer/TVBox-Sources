"""频道别名：把 CCTV1 / CCTV-1 / 央视一套 这类写法归一成同一个频道名。

思路参考 Guovin/iptv-api 的频道别名表：先用规则吃掉有规律的（央视台号），
再用一张显式的别名表兜住没规律的写法（「中央电视台综合频道」这类全称）。

配置文件：config/alias.yaml
"""
import re

# 央视台号区间（超出范围的当作别的频道，不做归一）
_ENABLED = True
_CCTV_ENABLED = True
_CCTV_MIN, _CCTV_MAX = 1, 17
_TABLE = {}          # 归一化后的键 -> 规范名
_CANONICAL = set()   # 规范名集合，用于日志/统计

# 「CCTV / 中央 / 央视 / 中央电视台」开头
_PREFIX = re.compile(r"^(?:cctv|中央|央视|中央电视台)", re.IGNORECASE)
# 台号后面常见的尾巴：CCTV-1 综合 / CCTV1高清 / CCTV-13 新闻 / 央视一套 …
_TAIL = re.compile(
    r"(?:套|频道|电视台|电视|台|综合|高清|超高清|标清|uhd|fhd|hd|sd|直播|在线"
    r"|新闻|财经|综艺|体育|电影|电视剧|军事|纪录|记录|科教|戏曲|法治|社会与法"
    r"|少儿|音乐|农业|农村|国际|中文国际)+$",
    re.IGNORECASE)
_CN_NUM = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
    "十": 10, "十一": 11, "十二": 12, "十三": 13, "十四": 14, "十五": 15, "十六": 16,
    "十七": 17,
}


def _key(name):
    """宽松键：忽略空格、横线、下划线、点号，全部小写

    这样 CCTV-1 / CCTV1 / cctv 1 会落到同一个键上。
    """
    return re.sub(r"[\s\-_·.]+", "", str(name or "")).lower()


def load(data=None, path="config/alias.yaml"):
    """载入别名配置；data 为空时读 path，文件不存在就只保留内置的央视规则"""
    global _ENABLED, _CCTV_ENABLED, _CCTV_MIN, _CCTV_MAX, _TABLE, _CANONICAL
    if data is None:
        try:
            import yaml
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except FileNotFoundError:
            data = {}
        except Exception:  # noqa: BLE001
            data = {}

    _ENABLED = bool(data.get("enabled", True))
    _CCTV_ENABLED = bool(data.get("cctv_normalize", True))
    rng = data.get("cctv_range") or [_CCTV_MIN, _CCTV_MAX]
    try:
        _CCTV_MIN, _CCTV_MAX = int(rng[0]), int(rng[1])
    except Exception:  # noqa: BLE001
        _CCTV_MIN, _CCTV_MAX = 1, 17

    table, canonical = {}, set()
    for name, aliases in (data.get("aliases") or {}).items():
        canon = str(name).strip()
        if not canon:
            continue
        canonical.add(canon)
        table[_key(canon)] = canon
        for a in aliases or []:
            k = _key(a)
            if k:
                table[k] = canon
    _TABLE, _CANONICAL = table, canonical
    return {"enabled": _ENABLED, "aliases": len(table), "channels": len(canonical)}


def _cctv_number(name):
    """从央视写法里取出台号，取不到返回 None

    只认「前缀 + 台号（+ 套/频道/高清 之类的尾巴）」这种结构，
    所以 CCTV-4K、CCTV-4 美洲、CCTV-1 香港 都不会被误合并。
    """
    s = str(name or "").strip()
    m = _PREFIX.match(s)
    if not m:
        return None
    rest = s[m.end():]
    # 编码/帧率标注先去掉（要在去空格之前：「CCTV 1 50 FPS」去掉 "50 FPS" 后才不会粘成 150）
    rest = re.sub(r"(?:\d+\s*fps|hevc|avs[0-9]?|hdr|hq)", "", rest, flags=re.IGNORECASE)
    rest = re.sub(r"[\s\-_·]+", "", rest)      # 去掉分隔符
    rest = re.sub(r"^(?:电视|电视台|台|第)+", "", rest)   # 中央电视台 / 中央一台
    rest = _TAIL.sub("", rest)                  # 去掉 套/频道/高清…
    if not rest:
        return None
    if rest in ("5+", "5＋", "5plus"):
        return "5+"
    if rest.isdigit():
        n = int(rest)
        return n if _CCTV_MIN <= n <= _CCTV_MAX else None
    if rest in _CN_NUM:
        n = _CN_NUM[rest]
        return n if _CCTV_MIN <= n <= _CCTV_MAX else None
    return None


def resolve(name):
    """返回规范名；没有命中任何规则就原样返回"""
    if not _ENABLED or not name:
        return name
    hit = _TABLE.get(_key(name))
    if hit:
        return hit
    if _CCTV_ENABLED:
        n = _cctv_number(name)
        if n == "5+":
            return "CCTV-5+"
        if isinstance(n, int):
            return f"CCTV-{n}"
    return name


def stats():
    return {"enabled": _ENABLED, "aliases": len(_TABLE), "channels": len(_CANONICAL)}
