"""爬取模块：拉取多个直播源 → 解析（m3u/txt/json）→ 过滤 → 去重

修复项（相对 TVBox-Sources）：
  - parse_m3u 用正则取引号外逗号，修复 EXTINF 名中含逗号被截断
  - parse_json 支持 urls 为数组/逗号串/嵌套 dict 的频道项
  - 数据源改为并发爬取（ThreadPoolExecutor），保留原始顺序
  - 增加 exclude_url_patterns，过滤占位符地址
  - 去重时统一 URL strip + 大小写规范化协议/主机

接入 vbskycn 上游新增：
  - no_mirror：源级开关，禁用 jsdelivr 自动镜像（其 12h 缓存会拿到上游旧版本）
  - cache_bust：源级开关，给下载 URL 加 ?t=<epoch> 绕过 CDN 旧副本
  - download_text 支持 no_cache 参数，逐请求禁用响应的缓存复用
"""
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import LOGGER, make_session, download_text  # noqa: E402


@dataclass
class Channel:
    """单个直播频道

    groups: 跨组归属列表（模板中同一频道可同时出现在「央视频道」和「体育专区」等）。
            group 仍是主分组（用于去重/打分），groups 用于最终输出时展开成多条。
    """
    name: str = "未知"
    url: str = ""
    group: str = "未分组"
    tvg_id: str = ""
    tvg_logo: str = ""
    tvg_country: str = ""
    source: str = ""
    groups: list = field(default_factory=list)

    @property
    def key(self):
        return _norm_url(self.url)


ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')
# 找到第一个「不在引号内」的逗号（EXTINF 名字可能含逗号）
_COMMA_RE = re.compile(r',(?=(?:[^"]*"[^"]*")*[^"]*$)')


def _norm_url(url):
    """URL 规范化用于去重：去首尾空白，协议与主机小写"""
    u = (url or "").strip()
    m = re.match(r"^(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*):(?P<rest>//.*)$", u)
    if m:
        return m.group("scheme").lower() + ":" + m.group("rest")
    return u


def parse_m3u(text, source):
    """解析 #EXTM3U 格式"""
    channels, cur = [], None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            attrs = dict(ATTR_RE.findall(line))
            m = _COMMA_RE.search(line)
            name = line[m.end():].strip() if m else "未知"
            cur = Channel(
                name=name or attrs.get("tvg-name") or "未知",
                url="",
                group=attrs.get("group-title", "未分组"),
                tvg_id=attrs.get("tvg-id", ""),
                tvg_logo=attrs.get("tvg-logo", ""),
                tvg_country=attrs.get("tvg-country", ""),
                source=source,
            )
        elif line.startswith("#"):
            continue
        else:
            if cur is not None and not cur.url:
                cur.url = line
                channels.append(cur)
                cur = None
            else:
                channels.append(Channel(name="未知", url=line, group="未分组", source=source))
    return channels


def parse_txt(text, source):
    """解析 TVBox txt 格式：#genre# 分组头 + 「频道名,url」"""
    channels, group = [], "未分组"
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        # 兼容两种分组头写法：#genre#组名（旧）与 组名,#genre#（TVBox 标准，
        # 也是本项目 write_txt 的输出格式）。修复标准格式被误当频道的问题。
        if "#genre#" in line:
            g = line.replace("#genre#", "").strip().strip(",").strip()
            group = g if g else "未分组"
            continue
        if line.startswith("#") or "," not in line:
            continue
        # 只按第一个逗号拆分，URL 中可能带逗号（如 query 参数）
        name, url = line.split(",", 1)
        name, url = name.strip().lstrip("#").strip(), url.strip()
        if not name or not url:
            continue
        channels.append(Channel(name=name, url=url, group=group, source=source))
    return channels


def parse_json(text, source):
    """解析 JSON 直播源（宽松处理嵌套结构，支持 urls 数组）"""
    try:
        data = json.loads(text)
    except Exception:
        return []
    channels = []

    def _add(name, url, group):
        if isinstance(url, str) and url.strip().startswith(("http://", "https://")):
            channels.append(Channel(
                name=str(name) or "未知", url=url.strip(),
                group=str(group) or "未分组", source=source,
            ))

    def walk(node):
        if isinstance(node, dict):
            name = node.get("name") or node.get("title") or node.get("channel") or ""
            url = node.get("url") or node.get("urls") or node.get("link") or ""
            group = node.get("group") or node.get("group-title") or "未分组"
            if isinstance(url, str):
                for u in re.split(r"[,;\s]+", url):
                    _add(name, u, group)
            elif isinstance(url, (list, tuple)):
                for u in url:
                    _add(name, u, group)
            elif isinstance(url, dict):
                for u in url.values():
                    _add(name, u, group)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str) and node.strip().startswith(("http://", "https://")):
            # 形如 {"CCTV1": "http://..."} 的键值结构由父级 dict 处理
            return

    walk(data)
    return channels


PARSERS = {"m3u": parse_m3u, "txt": parse_txt, "json": parse_json}


def detect_parser(url, text):
    """根据 URL 后缀与内容片段猜测格式"""
    low = url.lower()
    if ".m3u" in low or ".m3u8" in low or text.lstrip().startswith("#EXTM3U"):
        return "m3u"
    if ".json" in low or text.lstrip().startswith(("{", "[")):
        return "json"
    return "txt"


def mirror_url(url):
    """为 GitHub raw 链接生成 jsdelivr CDN 镜像"""
    m = re.match(r"https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/(.+)", url)
    if m:
        owner, repo, branch, path = m.groups()
        return f"https://cdn.jsdelivr.net/gh/{owner}/{repo}@{branch}/{path}"
    return None


def candidate_urls(src, prefer_mirror=False):
    """返回该源的候选下载地址列表：raw 优先、镜像兜底（可反转）

    vbskycn 上游源设置 no_mirror=true，避免 jsdelivr 的 s-maxage=43200（12h）
    缓存导致拿到数小时前的旧版本；上游对时效性要求高，必须以 raw 为准。
    显式配置的 mirror 仍保留（可指向不带缓存的备用直链）。
    """
    raw = src.get("url", "")
    if isinstance(raw, str):
        raw = [u for u in raw.split(",") if u.strip()]
    no_mirror = bool(src.get("no_mirror", False))
    candidates = []
    for u in raw:
        m = None if no_mirror else mirror_url(u)
        if prefer_mirror and m:
            candidates.append(m)
        candidates.append(u)
        if not prefer_mirror and m:
            candidates.append(m)
    explicit = src.get("mirror")
    if explicit and explicit not in candidates:
        candidates.append(explicit)
    # 去重但保持顺序
    seen, out = set(), []
    for u in candidates:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _cache_busted(url, enabled):
    """给 URL 追加时间戳参数，强制 CDN 回源拿最新版本（vbskycn 上游用）"""
    if not enabled:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}t={int(time.time())}"


def _fetch_source(src, req_cfg, prefer_mirror):
    """抓取并解析单个源，返回 (source_name, channels)"""
    name = src.get("name") or "未命名源"
    urls = candidate_urls(src, prefer_mirror)
    if not urls:
        LOGGER.warning("跳过无 URL 来源: %s", name)
        return name, []

    session = make_session(req_cfg)
    cache_bust = bool(src.get("cache_bust", False))
    try:
        text, used = None, None
        for url in urls:
            fetch_url = _cache_busted(url, cache_bust)
            LOGGER.info("爬取来源: %s (%s)%s", name, fetch_url,
                        " [cache-bust]" if cache_bust else "")
            text = download_text(
                session, fetch_url,
                max_size_mb=req_cfg.get("max_size", 20),
                timeout=req_cfg.get("timeout", 12),
                retries=req_cfg.get("retries", 1),
                no_cache=cache_bust,
            )
            if text is not None:
                used = url
                break
            LOGGER.info("  该地址不可用，尝试下一个候选地址…")
        if text is None:
            LOGGER.warning("来源 %s 的所有地址均失败，跳过", name)
            return name, []

        parser = detect_parser(used, text)
        channels = PARSERS[parser](text, name)
        LOGGER.info("  [%s] 解析到 %d 个频道", parser, len(channels))
        return name, channels
    finally:
        session.close()


def crawl_sources(cfg):
    """遍历配置中的数据源并抓取解析，返回原始频道列表（顺序与配置一致）"""
    crawl_cfg = cfg.get("crawl", {})
    req_cfg = crawl_cfg.get("request", {})
    sources = [s for s in crawl_cfg.get("sources", []) if s.get("enabled", True)]
    prefer_mirror = os.environ.get("LIVE_SOURCE_PREFER_MIRROR") == "1"

    if not sources:
        return []

    workers = max(1, min(len(sources), int(crawl_cfg.get("workers", 5))))
    results = [None] * len(sources)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_fetch_source, s, req_cfg, prefer_mirror) for s in sources]
        for i, fut in enumerate(futs):
            try:
                results[i] = fut.result()
            except Exception as e:  # noqa: BLE001
                LOGGER.warning("来源 %s 抓取异常: %s", sources[i].get("name"), e)
                results[i] = (sources[i].get("name", "未命名源"), [])

    all_channels = []
    for _name, channels in results:
        all_channels.extend(channels)
    return all_channels


def apply_filters(channels, cfg):
    """过滤 + 按 URL 去重，返回可用频道列表"""
    crawl_cfg = cfg.get("crawl", {})
    filters = crawl_cfg.get("filters", {})
    req_cfg = crawl_cfg.get("request", {})
    allowed_schemes = [s.lower() for s in filters.get("allowed_schemes", ["http", "https"])]
    include_groups = [g.lower() for g in filters.get("include_groups", [])]
    exclude_groups = [re.compile(p, re.IGNORECASE) for p in filters.get("exclude_groups", [])]
    include_kw = [k.lower() for k in filters.get("include_keywords", [])]
    exclude_kw = [k.lower() for k in filters.get("exclude_keywords", [])]
    exclude_ch = [c.lower() for c in filters.get("exclude_channels", [])]
    country = (filters.get("country") or "").strip().upper()
    # 合并 filters 与 request 中的 URL 排除规则
    bad_url = [re.compile(p, re.IGNORECASE) for p in (
        list(filters.get("exclude_url_patterns", []))
        + list(req_cfg.get("exclude_url_patterns", []))
    )]

    def ok(ch):
        if not ch.url:
            return False
        u = ch.url.strip()
        if any(p.search(u) for p in bad_url):
            return False
        scheme = u.split(":", 1)[0].lower()
        if scheme not in allowed_schemes:
            return False
        g = (ch.group or "").strip().lower()
        if include_groups and g not in include_groups:
            return False
        if any(p.search(g) for p in exclude_groups):
            return False
        n = (ch.name or "").lower()
        if include_kw and not any(k in n for k in include_kw):
            return False
        if any(k in n for k in exclude_kw):
            return False
        if any(k and k in n for k in exclude_ch):
            return False
        if country and (ch.tvg_country or "").strip().upper() not in ("", country):
            return False
        return True

    kept = [c for c in channels if ok(c)]

    seen, dedup = set(), []
    for c in kept:
        k = c.key
        if k in seen:
            continue
        seen.add(k)
        dedup.append(c)
    return dedup
