"""测速校验模块：HTTP 首包测速 + ffprobe 媒体校验 → 打分 → 分组择优

修复项（相对 TVBox-Sources）：
  - requests.Session 线程局部复用，避免每次新建 TCP/TLS 连接
  - ffprobe 的 rw_timeout 改为读取配置（原来硬编码 4s 与配置 10s 不一致）
  - 增加 -probesize/-analyzeduration，避免 HLS 探测过久被外层超时杀掉
  - compute_score 修正为「≤500ms 满分」（与 README 描述一致）
  - select_best 增加展示名清洗，去掉 (720p) 等画质尾缀
  - http_probe 增加 Content-Length=0 与空 stream 判定
"""
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


_NORM_SUFFIX_RE = re.compile(
    r"(高清|标清|超清|hd|sd|uhd|2160p|1440p|1080p|720p|540p|480p|360p)$", re.I)
# 4K/8K 是「频道身份」而非画质后缀（如 东方卫视4K / CCTV-4K）：
# 归一化时不能去掉，否则会与普通频道重名误合并；仅清洗纯分辨率写法 2160p/4K频道 等。
_DISPLAY_4K_RE = re.compile(r"[\s\-_]*(4K|8K)频道$", re.I)


def normalize_name(name):
    """频道名规范化：去括号标注/分隔符/尾缀，用于合并同一频道的不同写法"""
    n = (name or "").lower()
    n = re.sub(r"[\(\[【][^\)\]】]*[\)\]】]", "", n)
    n = re.sub(r"[\s\-_—:：,，.。'\"’‘]+", "", n)
    n = _NORM_SUFFIX_RE.sub("", n)
    return n.strip()


def display_name(name):
    """展示名清洗：去掉画质尾缀与方括号标注，保留频道本名

    注意：4K/8K 作为频道名一部分时保留（东方卫视4K 不应显示成 东方卫视），
    只有「4K频道/2160p」这类纯画质描述才清洗。
    """
    n = (name or "").strip()
    n = _DISPLAY_4K_RE.sub("", n)
    n = re.sub(r"[\(\[【][^\)\]】]*\d*\s*[pPkKiI][^\)\]】]*[\)\]】]\s*$", "", n)
    n = re.sub(r"[\(\[【][^\)\]】]*(?:高清|标清|超清)[^\)\]】]*[\)\]】]\s*$", "", n)
    n = _NORM_SUFFIX_RE.sub("", n)
    return n.strip() or (name or "未知")


def select_best(results, cfg):
    """择优：丢弃低分 → 同频道聚合多源 → 组内截断。

    返回 (selected_channels, passed_results)
    """
    check_cfg = cfg.get("check", {})
    min_score = check_cfg.get("min_score", 30)
    max_per_group = check_cfg.get("max_per_group", 60)
    max_urls_per_channel = max(1, int(check_cfg.get("max_urls_per_channel", 8)))

    passed = [r for r in results if r.ok and r.score >= min_score]
    passed.sort(key=lambda r: (-r.score, r.latency_ms))

    seen_urls, slots = set(), {}
    for r in passed:
        key_url = (r.channel.url or "").strip().lower()
        if key_url in seen_urls:
            continue
        seen_urls.add(key_url)
        key = (r.channel.group, normalize_name(r.channel.name))
        slot = slots.setdefault(key, {"name": r.channel.name, "items": []})
        if len(slot["items"]) < max_urls_per_channel:
            slot["items"].append(r)
            # 展示名优先选清洗后更完整（更长）的一个
            if len(display_name(r.channel.name)) > len(display_name(slot["name"])):
                slot["name"] = r.channel.name

    by_group = {}
    for (group, _nk), slot in slots.items():
        by_group.setdefault(group, []).append(slot)

    selected = []
    for gname, gslots in by_group.items():
        if max_per_group and max_per_group > 0:
            gslots = gslots[:max_per_group]
        for slot in gslots:
            name = display_name(slot["name"])
            for r in slot["items"]:
                ch = r.channel
                ch.name = name   # 统一展示名，便于 TVBox 合并多源
                selected.append(ch)
    return selected, passed
