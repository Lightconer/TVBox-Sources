"""测速校验模块：HTTP 首包测速 + ffprobe 媒体校验 → 打分 → 分组择优

流程：
  1. HTTP 请求读取首包，记录连通性与首包延迟（TTFB）；
  2. 可选 ffprobe 探测媒体流，确认「真的能播放」（m3u8/rtmp 等真实直播流）；
  3. 按「延迟 + 媒体有效性」打分，丢弃不可用 / 低分频道；
  4. 每个分组按分数排序截断，保证体积可控。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import LOGGER  # noqa: E402

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

# ffprobe 是否可用（Actions 环境安装 ffmpeg 后为 True）
FFPROBE_AVAILABLE = shutil.which("ffprobe") is not None


class CheckResult:
    __slots__ = ("channel", "ok", "latency_ms", "media_ok", "resolution", "score")

    def __init__(self, channel, ok=False, latency_ms=0, media_ok=False,
                 resolution=None, score=0.0):
        self.channel = channel
        self.ok = ok                 # 是否通过（HTTP 可达 + 可选媒体校验）
        self.latency_ms = latency_ms
        self.media_ok = media_ok     # ffprobe 是否确认可播放
        self.resolution = resolution  # {"width":..,"height":..} 或 None
        self.score = score


# 明确非直播流的页面类型（命中即判定为错误页/非可用源，防止 200 假页面）
_NON_STREAM_CT = ("text/html", "application/json", "text/xml", "application/xml")


def http_probe(url, connect_timeout, read_timeout, user_agent):
    """HTTP 首包测速，返回 (ok, latency_ms)。

    校验三项，确保"确实可用"：
      1. 状态码 < 400（404/403 等错误页不算可达）；
      2. Content-Type 不是网页/JSON 等非流类型（200 假页面判失败）；
      3. 首包能读到内容。
    """
    if requests is None:
        return False, 0
    start = time.time()
    try:
        r = requests.get(
            url,
            timeout=(connect_timeout, read_timeout),
            stream=True,
            headers={"User-Agent": user_agent},
            allow_redirects=True,
        )
        ok = False
        try:
            if r.ok:
                ct = (r.headers.get("Content-Type") or "").lower().split(";")[0].strip()
                if ct in _NON_STREAM_CT:
                    ok = False  # 明确是网页/错误页，不是直播流
                else:
                    for _ in r.iter_content(chunk_size=1024):
                        ok = True
                        break
        except Exception:  # noqa: BLE001
            ok = False
        latency = int((time.time() - start) * 1000)
        r.close()
        return ok, latency
    except Exception:  # noqa: BLE001
        return False, int((time.time() - start) * 1000)


def ffprobe_probe(url, timeout):
    """ffprobe 探测媒体流。
    返回：dict 有视频流(含分辨率) | False 探测失败/无视频流 | None 未安装 ffprobe
    """
    if not FFPROBE_AVAILABLE:
        return None
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_streams",
        "-rw_timeout", "4000000",
        "-user_agent", "Mozilla/5.0",
        url,
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout, text=True)
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
    """打分：延迟满分 60（<=500ms 满分，每 1s 扣 6 分，10s 归零）+ 媒体有效性 40 分"""
    lat_score = max(0.0, 60.0 - (latency_ms / 1000.0) * 6.0)
    media_score = 40.0 if media_ok else 0.0
    return round(lat_score + media_score, 1)


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
        # ffprobe 明确失败：如要求媒体校验则判死，否则仅按 HTTP 通过
        if require_ffprobe and FFPROBE_AVAILABLE:
            return CheckResult(ch, ok=False, latency_ms=lat, media_ok=False, score=0.0)
        return CheckResult(
            ch, ok=True, latency_ms=lat, media_ok=False,
            score=compute_score(lat, False),
        )
    if media is None:
        # 未安装 ffprobe：降级为仅 HTTP 测速
        return CheckResult(
            ch, ok=True, latency_ms=lat, media_ok=False,
            score=compute_score(lat, False),
        )
    return CheckResult(
        ch, ok=True, latency_ms=lat, media_ok=True, resolution=media,
        score=compute_score(lat, True),
    )


def run_check(channels, cfg):
    """并发测速全部频道，返回结果列表（顺序不定）"""
    check_cfg = cfg.get("check", {})
    workers = max(1, check_cfg.get("workers", 30))
    limit = cfg.get("_limit", 0)
    ua = cfg.get("crawl", {}).get("request", {}).get("user_agent", "")

    if not FFPROBE_AVAILABLE and check_cfg.get("require_ffprobe", True):
        LOGGER.warning("未检测到 ffprobe(ffmpeg)，已降级为仅 HTTP 测速；"
                       "生产环境请确保 Actions 中安装了 ffmpeg")

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
                r = fut.result()
            except Exception:  # noqa: BLE001
                r = CheckResult(futs[fut], ok=False, score=0.0)
            results.append(r)
            if done % 50 == 0 or done == total:
                LOGGER.info("  测速进度 %d/%d", done, total)
    return results


_NORM_SUFFIX_RE = re.compile(r"(高清|标清|超清|hd|sd|uhd|4k|8k|2160p|1440p|1080p|720p|540p|480p|360p)$")


def normalize_name(name):
    """频道名规范化：去分辨率/标注括号与尾缀，用于把「同一频道」的不同写法合并。

    例：'CCTV-1 综合 (1080p)' / 'CCTV-1 综合' / 'CCTV1综合' → 'cctv1综合'
    """
    n = (name or "").lower()
    n = re.sub(r"[\(\[][^\)\]]*[\)\]]", "", n)          # 去 (1080p) [Geo-blocked]
    n = re.sub(r"[\s\-_—:：,，.。'\"’‘]+", "", n)         # 去空格/分隔符
    n = _NORM_SUFFIX_RE.sub("", n)                       # 去尾部清晰度词
    return n.strip()


def select_best(results, cfg):
    """择优：丢弃低分 → 同频道名聚合多源（播放失败自动切换）→ 组内截断。

    - 每个频道名保留最多 max_urls_per_channel 个可用地址（默认 5），按分数排序；
    - 每个分组保留最多 max_per_group 个频道（0=不限）。
    返回 (selected_channels, passed_results)
    """
    check_cfg = cfg.get("check", {})
    min_score = check_cfg.get("min_score", 40)
    max_per_group = check_cfg.get("max_per_group", 60)
    max_urls_per_channel = max(1, check_cfg.get("max_urls_per_channel", 5))

    passed = [r for r in results if r.ok and r.score >= min_score]
    passed.sort(key=lambda r: (-r.score, r.latency_ms))

    # 1) 按 (分组, 规范化频道名) 聚合，同一 URL 只保留一次（防跨源重复），取分数最高的多个地址
    seen_urls = set()
    slots = {}  # key -> {"name": 展示名, "items": [CheckResult,...]}
    for r in passed:
        if r.channel.url in seen_urls:
            continue
        seen_urls.add(r.channel.url)
        key = (r.channel.group, normalize_name(r.channel.name))
        slot = slots.setdefault(key, {"name": r.channel.name, "items": []})
        if len(slot["items"]) < max_urls_per_channel:
            slot["items"].append(r)
            # 展示名取更长/更完整的那个
            if len(r.channel.name) > len(slot["name"]):
                slot["name"] = r.channel.name

    # 2) 按分组聚合，组内频道数截断
    by_group = {}
    for (group, _nk), slot in slots.items():
        by_group.setdefault(group, []).append(slot)

    selected = []
    for gname, gslots in by_group.items():
        if max_per_group and max_per_group > 0:
            gslots = gslots[:max_per_group]
        for slot in gslots:
            for r in slot["items"]:
                ch = r.channel
                ch.name = slot["name"]   # 统一展示名，便于 TVBox 合并多源
                selected.append(ch)
    return selected, passed
