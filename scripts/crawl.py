"""爬取模块：拉取多个直播源 → 解析（m3u/txt/json）→ 过滤 → 去重"""
import json
import os
import re
import sys
from dataclasses import dataclass, asdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import LOGGER, make_session, download_text  # noqa: E402


@dataclass
class Channel:
    """单个直播频道"""
    name: str = "未知"
    url: str = ""
    group: str = "未分组"
    tvg_id: str = ""
    tvg_logo: str = ""
    tvg_country: str = ""
    source: str = ""

    @property
    def key(self):
        return self.url.strip()


ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')


def parse_m3u(text, source):
    """解析 #EXTM3U 格式（iptv-org 等公开源的标准格式）"""
    channels, cur = [], None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            attrs = dict(ATTR_RE.findall(line))
            comma = line.find(",")
            name = line[comma + 1:].strip() if comma >= 0 else "未知"
            cur = Channel(
                name=name or "未知",
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
            # URL 行：归属于上一条 #EXTINF；裸 URL 也保留
            if cur is not None and not cur.url:
                cur.url = line
                channels.append(cur)
                cur = None
            else:
                channels.append(Channel(name="未知", url=line, group="未分组", source=source))
    return channels


def parse_txt(text, source):
    """解析 TVBox 常见 txt 格式：#genre# 分组头 + 「频道名,url」"""
    channels, group = [], "未分组"
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#genre#"):
            g = line.split(",", 1)[-1].strip()
            group = g if g else "未分组"
            continue
        if line.startswith("#") or "," not in line:
            continue
        name, url = line.split(",", 1)
        name, url = name.strip().lstrip("#").strip(), url.strip()
        if not name or not url:
            continue
        channels.append(Channel(name=name, url=url, group=group, source=source))
    return channels


def parse_json(text, source):
    """解析 JSON 直播源（宽松处理嵌套结构）"""
    try:
        data = json.loads(text)
    except Exception:
        return []
    channels = []

    def walk(node):
        if isinstance(node, dict):
            name = node.get("name") or node.get("title") or node.get("channel") or ""
            url = node.get("url") or node.get("urls") or ""
            group = node.get("group") or node.get("group-title") or "未分组"
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                channels.append(
                    Channel(name=str(name) or "未知", url=url, group=str(group), source=source)
                )
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return channels


PARSERS = {"m3u": parse_m3u, "txt": parse_txt, "json": parse_json}


def detect_parser(url, text):
    """根据 URL 后缀与内容片段猜测格式"""
    low = url.lower()
    if ".m3u" in low or text.lstrip().startswith("#EXTM3U"):
        return "m3u"
    if ".json" in low or text.lstrip().startswith(("{", "[")):
        return "json"
    return "txt"


def mirror_url(url):
    """为 GitHub raw 链接生成 jsdelivr CDN 镜像（国内可直连），非 raw 链接返回 None"""
    m = re.match(r"https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/(.+)", url)
    if m:
        owner, repo, branch, path = m.groups()
        return f"https://cdn.jsdelivr.net/gh/{owner}/{repo}@{branch}/{path}"
    return None


def candidate_urls(src, prefer_mirror=False):
    """返回该源的候选下载地址列表。
    默认主地址(raw)优先、jsdelivr 镜像兜底；prefer_mirror=True 时镜像优先（国内本地跑更快）。
    src['url'] 可传字符串或逗号分隔列表；'mirror' 可显式指定镜像（可选）。
    """
    raw = src.get("url", "")
    if isinstance(raw, str):
        raw = [u for u in raw.split(",") if u.strip()]
    candidates = []
    for u in raw:
        m = mirror_url(u)
        if prefer_mirror and m:
            candidates.append(m)
        candidates.append(u)
        if not prefer_mirror and m:
            candidates.append(m)
    explicit = src.get("mirror")
    if explicit and explicit not in candidates:
        candidates.append(explicit)
    return candidates


def crawl_sources(cfg):
    """遍历配置中的数据源并抓取解析，返回原始频道列表。
    每个源按候选地址依次尝试（raw 失败自动降级 jsdelivr）。
    """
    crawl_cfg = cfg.get("crawl", {})
    req_cfg = crawl_cfg.get("request", {})
    session = make_session(req_cfg)
    sources = [s for s in crawl_cfg.get("sources", []) if s.get("enabled", True)]

    all_channels = []
    # 国内本地跑可设 LIVE_SOURCE_PREFER_MIRROR=1 让 jsdelivr 镜像优先，避免 raw 超时等待
    prefer_mirror = os.environ.get("LIVE_SOURCE_PREFER_MIRROR") == "1"
    for src in sources:
        name = src.get("name") or "未命名源"
        urls = candidate_urls(src, prefer_mirror)
        if not urls:
            LOGGER.warning("跳过无 URL 来源: %s", name)
            continue

        text = None
        used = None
        for url in urls:
            LOGGER.info("爬取来源: %s (%s)", name, url)
            text = download_text(
                session,
                url,
                max_size_mb=req_cfg.get("max_size", 20),
                timeout=req_cfg.get("timeout", 15),
                retries=req_cfg.get("retries", 2),
            )
            if text is not None:
                used = url
                break
            LOGGER.info("  该地址不可用，尝试下一个候选地址…")
        if text is None:
            LOGGER.warning("来源 %s 的所有地址均失败，跳过", name)
            continue

        parser = detect_parser(used, text)
        channels = PARSERS[parser](text, name)
        LOGGER.info("  [%s] 解析到 %d 个频道", parser, len(channels))
        all_channels.extend(channels)
    return all_channels


def apply_filters(channels, cfg):
    """过滤 + 按 URL 去重，返回可用频道列表"""
    filters = cfg.get("crawl", {}).get("filters", {})
    allowed_schemes = [s.lower() for s in filters.get("allowed_schemes", ["http", "https"])]
    include_groups = [g.lower() for g in filters.get("include_groups", [])]
    exclude_groups = [re.compile(p) for p in filters.get("exclude_groups", [])]
    include_kw = [k.lower() for k in filters.get("include_keywords", [])]
    exclude_kw = [k.lower() for k in filters.get("exclude_keywords", [])]
    exclude_ch = [c.lower() for c in filters.get("exclude_channels", [])]
    country = (filters.get("country") or "").strip().upper()

    def ok(ch):
        if not ch.url:
            return False
        scheme = ch.url.split(":", 1)[0].lower()
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
        if country and ch.tvg_country.strip().upper() not in ("", country):
            return False
        return True

    kept = [c for c in channels if ok(c)]

    # 按 URL 去重（保留第一个），保证同源重复条目不占体积
    seen, dedup = set(), []
    for c in kept:
        k = c.key
        if k in seen:
            continue
        seen.add(k)
        dedup.append(c)
    return dedup


def save_raw(channels, path):
    """调试用：把过滤后的频道存为 json，方便离线查看"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump([asdict(c) for c in channels], f, ensure_ascii=False, indent=2)
