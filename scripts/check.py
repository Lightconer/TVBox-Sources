"""测速校验：HTTP 首包 + ffprobe 探测，打分后按画质和速度择优。"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import LOGGER, DEFAULT_UA  # noqa: E402
from alias import resolve as resolve_alias  # noqa: E402

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

FFPROBE_AVAILABLE = shutil.which("ffprobe") is not None

# 线程局部 Session：测速阶段并发复用连接
_tls = threading.local()


def _get_session(ua):
    s = getattr(_tls, "session", None)
    if s is None:
        s = requests.Session()
        s.headers.update({"User-Agent": ua or DEFAULT_UA})
        _tls.session = s
    elif ua and s.headers.get("User-Agent") != ua:
        s.headers["User-Agent"] = ua
    return s


class CheckResult:
    __slots__ = ("channel", "ok", "latency_ms", "media_ok", "resolution", "score")

    def __init__(self, channel, ok=False, latency_ms=0, media_ok=False,
                 resolution=None, score=0.0):
        self.channel = channel
        self.ok = ok
        self.latency_ms = latency_ms
        self.media_ok = media_ok
        self.resolution = resolution
        self.score = score


_NON_STREAM_CT = ("text/html", "application/json", "text/xml", "application/xml")


def http_probe(url, connect_timeout, read_timeout, user_agent):
    """HTTP 首包测速，返回 (ok, latency_ms)。校验状态码 / Content-Type / 首包"""
    if requests is None:
        return False, 0
    start = time.time()
    try:
        r = _get_session(user_agent).get(
            url,
            timeout=(connect_timeout, read_timeout),
            stream=True,
            allow_redirects=True,
        )
        ok = False
        try:
            if r.ok:
                ct = (r.headers.get("Content-Type") or "").lower().split(";")[0].strip()
                if ct not in _NON_STREAM_CT:
                    for _ in r.iter_content(chunk_size=1024):
                        ok = True
                        break
        except Exception:  # noqa: BLE001
            ok = False
        finally:
            r.close()
        return ok, int((time.time() - start) * 1000)
    except Exception:  # noqa: BLE001
        return False, int((time.time() - start) * 1000)


def ffprobe_probe(url, timeout):
    """ffprobe 探测媒体流。返回 dict(有视频流) | False(失败) | None(未安装)"""
    if not FFPROBE_AVAILABLE:
        return None
    rw_timeout_us = int(max(1, timeout) * 1_000_000)
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_entries", "stream=codec_type,width,height",
        "-rw_timeout", str(rw_timeout_us),
        "-probesize", "5000000",
        "-analyzeduration", "5000000",
        "-user_agent", "Mozilla/5.0",
        url,
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout + 3, text=True)
        if out.returncode != 0:
            return False
        data = json.loads(out.stdout or "{}")
        video = next(
            (s for s in data.get("streams", []) if s.get("codec_type") == "video"), None
        )
        if video is None:
            return False
        return {"width": video.get("width") or 0, "height": video.get("height") or 0}
    except subprocess.TimeoutExpired:
        return False
    except Exception:  # noqa: BLE001
        return False


def compute_score(latency_ms, media_ok):
    """打分：延迟 ≤500ms 满分 60，每慢 1s 扣 6 分，10s 归零；媒体有效性 40 分"""
    lat_score = max(0.0, 60.0 - max(0.0, latency_ms - 500) / 1000.0 * 6.0)
    return round(lat_score + (40.0 if media_ok else 0.0), 1)


# 画质档位：数值越大画质越高。用于在「同一频道的多条线路」里先按画质、再按速度排序。
_QUALITY_TIERS = [
    (5, re.compile(r"(2160p|1440p|4k|8k|uhd|2160)", re.I)),   # 超高清 4K/8K
    (4, re.compile(r"(1080p|1080i|fhd|fullhd|1080)", re.I)),   # 全高清
    (3, re.compile(r"(720p|720i|hd\b|高清)", re.I)),           # 高清
    (2, re.compile(r"(576p|480p|540p|sd\b|标清)", re.I)),      # 标清
]


def quality_tier(ch, resolution=None):
    """判定线路画质档位 5=4K/8K 4=1080p 3=720p 2=SD 1=未知。

    优先用 ffprobe 实测的 width/height；无实测时按频道名/URL 中的标注推断。
    这样即使本地未装 ffprobe，也能按名字里的 4K/1080P 标注排序。
    """
    res = resolution if resolution is not None else getattr(ch, "resolution", None)
    h = w = 0
    if isinstance(res, dict):
        h = int(res.get("height") or 0)
        w = int(res.get("width") or 0)
    if h >= 2160 or w >= 3840:
        return 5
    if h >= 1080 or w >= 1920:
        return 4
    if h >= 720 or w >= 1280:
        return 3
    if h >= 480 or w >= 640:
        return 2
    # 无实测分辨率：从名字与 URL 推断（URL 里常带 1080/4k 路径）
    text = f"{getattr(ch, 'name', '')} {getattr(ch, 'url', '')}"
    for tier, pat in _QUALITY_TIERS:
        if pat.search(text):
            return tier
    return 1


def check_channel(ch, cfg, ua):
    """单频道测速，返回 CheckResult"""
    check_cfg = cfg.get("check", {})
    ct = check_cfg.get("connect_timeout", 5)
    rt = check_cfg.get("read_timeout", 8)
    ft = check_cfg.get("ffprobe_timeout", 10)
    require_ffprobe = check_cfg.get("require_ffprobe", True)

    ok, lat = http_probe(ch.url, ct, rt, ua)
    if not ok:
        return CheckResult(ch, ok=False, latency_ms=lat, score=0.0)

    media = ffprobe_probe(ch.url, ft)
    if media is False:
        if require_ffprobe and FFPROBE_AVAILABLE:
            return CheckResult(ch, ok=False, latency_ms=lat, media_ok=False, score=0.0)
        return CheckResult(ch, ok=True, latency_ms=lat, media_ok=False,
                           score=compute_score(lat, False))
    if media is None:
        return CheckResult(ch, ok=True, latency_ms=lat, media_ok=False,
                           score=compute_score(lat, False))
    return CheckResult(ch, ok=True, latency_ms=lat, media_ok=True,
                       resolution=media, score=compute_score(lat, True))


def run_check(channels, cfg):
    """并发测速全部频道，返回结果列表（顺序不定）"""
    check_cfg = cfg.get("check", {})
    workers = max(1, int(check_cfg.get("workers", 30)))
    limit = cfg.get("_limit", 0)
    ua = cfg.get("crawl", {}).get("request", {}).get("user_agent", "")

    if not FFPROBE_AVAILABLE and check_cfg.get("require_ffprobe", True):
        LOGGER.warning("未检测到 ffprobe，已降级为仅 HTTP 测速；生产环境请确保安装 ffmpeg")

    targets = channels[:limit] if limit else channels
    results = []
    total = len(targets)
    if total == 0:
        return results

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(check_channel, ch, cfg, ua): ch for ch in targets}
        done = 0
        for fut in as_completed(futs):
            done += 1
            try:
                results.append(fut.result())
            except Exception:  # noqa: BLE001
                results.append(CheckResult(futs[fut], ok=False, score=0.0))
            if done % 50 == 0 or done == total:
                LOGGER.info("  测速进度 %d/%d", done, total)
    return results


# 画质/清晰度后缀：这些是冗余标注，展示名里一律去掉
# 例：CCTV-1 (720p) / CCTV-1高清 / 湖南卫视 HD / 浙江卫视[1080P] → 只留频道本名
# 注意：4K/8K 单独出现时是「频道身份」（CCTV-4K / 东方卫视4K），不作为普通画质后缀删除；
#       仅当带分辨率写法（2160p/4K频道）或出现在括号标注里时才清掉。
_RES_WORD = r"(?:2160p|1440p|1080p|1080i|720p|720i|540p|480p|360p|uhd|fhd|fullhd|hd|sd|高清|超清|标清|蓝光|超高清)"
# 方括号/圆括号包装的画质标注（整体删除，含括号内其它描述）
_BRACKET_QUALITY_RE = re.compile(r"\s*[\(\[【（][^\)\]】）]*" + _RES_WORD + r"[^\)\]】）]*[\)\]】）]\s*$", re.I)
# 以分隔符连接的画质尾缀：-HD / _1080P / ·高清
_SEP_QUALITY_RE = re.compile(r"[\s\-_—·•|/]+" + _RES_WORD + r"\s*$", re.I)
# 直接粘连的画质后缀（数字/字母后）：CCTV1720p / CCTV1高清
_GLUED_QUALITY_RE = re.compile(r"(?<=\d)" + _RES_WORD + r"\s*$", re.I)
_NORM_SUFFIX_RE = re.compile(r"^(?:高清|标清|超清|hd|sd|uhd|2160p|1440p|1080p|720p|540p|480p|360p)$", re.I)
# 频道身份标识：CCTV-4K / CCTV-8K / 东方卫视4K 等，4K/8K 是频道名一部分不能删
_CHANNEL_4K_RE = re.compile(r"^(.*?[\u4e00-\u9fa5A-Za-z]|[ctvCTV\-]+)(\d*(?:4K|8K))$", re.I)


def _strip_quality(name):
    """去掉画质类后缀，但保留 4K/8K 作为频道身份的情况"""
    n = (name or "").strip()
    n = _BRACKET_QUALITY_RE.sub("", n)
    # 括号里的 (4K)/(8K) 是画质标注，清掉（CCTV-1 (4K) → CCTV-1）
    n = re.sub(r"\s*[\(\[【（]\s*(?:4K|8K)\s*[\)\]】）]\s*$", "", n, flags=re.I)
    # 台号+空格+4K/8K 是画质标注（CCTV-1 4K → CCTV-1）；
    # CCTV-4K / 东方卫视4K 是紧跟的频道身份，不在此列
    n = re.sub(r"\s+(?:4K|8K)\s*$", "", n, flags=re.I)
    n = _SEP_QUALITY_RE.sub("", n)
    n = _GLUED_QUALITY_RE.sub("", n)
    # "超高清频道" 这类纯画质描述才清；"4K频道" 的 4K 是频道身份，保留
    n = re.sub(r"[\s\-_]*超高清频道\s*$", "", n, flags=re.I)
    return n.strip()


def normalize_name(name):
    """频道名规范化：去括号标注/分隔符/画质尾缀，用于把同一频道的不同写法聚合。

    与 display_name 的区别：归一化会把 4K/8K 也去掉，以便
    「CCTV-1 / CCTV-1 (4K) / CCTV-1 1080P」聚合成同一频道的多条线路；
    但 CCTV-4K 这种「4K 紧跟台号」的频道身份会保留，避免与 CCTV-4 混淆。
    """
    n = (name or "").lower()
    n = re.sub(r"[\(\[【][^\)\]】]*[\)\]】]", "", n)
    # 先把「紧跟台号/中文台名的 4K/8K 频道身份」保护成占位符，避免被画质正则清掉
    # CCTV-4K / 东方卫视4K 是独立频道；CCTV-1 4K（有空格）是画质标注，不保护
    n = re.sub(r"(?<=[\d\u4e00-\u9fa5\-])(4k|8k)\b", r"QQ\1QQ", n, flags=re.I)
    n = re.sub(r"[\s\-_—:：,，.。'\"’‘]+", "", n)
    # 分隔后的 4K/8K/分辨率标注：CCTV-1 4K / CCTV-1 1080P → CCTV1
    n = re.sub(r"(?:4k|8k|2160p|1440p|1080p|1080i|720p|540p|480p|360p|uhd|fhd|hd|sd|高清|超清|标清)$",
               "", n, flags=re.I)
    return n.replace("QQ", "").strip()


def display_name(name):
    """展示名清洗：去掉画质/清晰度尾缀与方括号标注，只保留频道本名

    - CCTV-1 (720p) → CCTV-1    CCTV-1高清 → CCTV-1    湖南卫视[1080P] → 湖南卫视
    - CCTV-4K / 东方卫视4K 保留：4K/8K 是频道身份，不是画质标注
    """
    n = _strip_quality(name)
    return n or (name or "未知")


def natural_sort_key(name):
    """频道自然排序键：CCTV-1 / CCTV-2 / CCTV-10 按数字升序，而非字典序 1/10/2。

    返回 (是否有数字, 数字, 名称)：无数字的频道排在数字频道之后。
    """
    s = display_name(name)
    m = re.search(r"(\d+)", s)
    if m:
        return (0, int(m.group(1)), s)
    return (1, 0, s)


def select_best(results, cfg):
    """择优：丢弃低分 → 同频道聚合多源 → 组内截断。

    返回 (selected_channels, passed_results)
    """
    check_cfg = cfg.get("check", {})
    min_score = check_cfg.get("min_score", 30)
    max_per_group = check_cfg.get("max_per_group", 60)
    max_urls_per_channel = max(1, int(check_cfg.get("max_urls_per_channel", 8)))

    quality_first = bool(check_cfg.get("quality_first", True))
    pref_min_tier = int(check_cfg.get("preferred_min_tier", 4))  # 4=1080p 及以上

    passed = [r for r in results if r.ok and r.score >= min_score]
    # 全局排序：画质档位优先（高端先），同档按分数/速度；无高画质时低画质自然靠后仍被保留
    passed.sort(key=lambda r: (
        -(quality_tier(r.channel, r.resolution) if quality_first else 0),
        -r.score, r.latency_ms,
    ))

    seen_urls, slots = set(), {}
    for r in passed:
        key_url = (r.channel.url or "").strip().lower()
        if key_url in seen_urls:
            continue
        seen_urls.add(key_url)
        # 先过别名表：CCTV1 / CCTV-1 / 央视一套 会归到同一个 key，聚合到同一个频道
        canon = resolve_alias(r.channel.name)
        key = (r.channel.group, normalize_name(canon))
        slot = slots.setdefault(key, {"name": canon, "items": []})
        if len(slot["items"]) < max_urls_per_channel:
            slot["items"].append(r)
            # 展示名选「最干净」的：优先无画质标注、其次更完整的名字，
            # 避免 CCTV-1 4K 这类带标注写法胜出导致最终展示名残留后缀
            dn_r, dn_old = display_name(r.channel.name), display_name(slot["name"])
            score_r = (len(dn_r), dn_r == r.channel.name.strip())
            score_old = (len(dn_old), dn_old == slot["name"].strip())
            if dn_r and (score_r > score_old):
                slot["name"] = r.channel.name

    # 同一频道的多条线路：先画质档位、再速度排序（passed 已全局排序，这里二次确保）
    for slot in slots.values():
        slot["items"].sort(key=lambda r: (
            -(quality_tier(r.channel, r.resolution) if quality_first else 0),
            -r.score, r.latency_ms,
        ))
        # 降级策略：若无 1080p+ 线路，保留低画质线路（不过滤），仅打日志提示
        if quality_first and slot["items"] and \
                max(quality_tier(r.channel, r.resolution) for r in slot["items"]) < pref_min_tier:
            LOGGER.debug("频道「%s」无 %dp 及以上线路，降级使用现有画质",
                         display_name(slot["name"]), pref_min_tier * 540)

    by_group = {}
    for (group, _nk), slot in slots.items():
        by_group.setdefault(group, []).append(slot)

    selected = []
    for gname, gslots in by_group.items():
        if max_per_group and max_per_group > 0:
            gslots = gslots[:max_per_group]
        # 组内频道按 CCTV-1/2/3... 数字升序（自然排序），无数字的排在后面
        gslots.sort(key=lambda s: natural_sort_key(display_name(s["name"])))
        for slot in gslots:
            # 展示名再过一次别名：源里叫「CCTV1」「央视一套」的统一显示成 CCTV-1
            name = resolve_alias(display_name(slot["name"]))
            for r in slot["items"]:
                ch = r.channel
                ch.name = name   # 统一展示名，便于 TVBox 合并多源
                selected.append(ch)
    return selected, passed
