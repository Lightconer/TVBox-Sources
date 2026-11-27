"""公共工具：日志、HTTP 请求（重试、大小上限、连接复用）。"""
import logging
import time

import requests

LOGGER = logging.getLogger("tvbox-sources")

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def setup_logging(level=logging.INFO):
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def make_session(cfg=None):
    """创建复用的 Session（连接池），并带上统一请求头"""
    cfg = cfg or {}
    s = requests.Session()
    s.headers.update({
        "User-Agent": cfg.get("user_agent", DEFAULT_UA),
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    return s


def http_get(session, url, timeout=15, retries=2, stream=False, **kw):
    """带重试的 GET。最终失败返回 None；出错时确保响应被关闭"""
    last_err = None
    for attempt in range(max(1, retries + 1)):
        resp = None
        try:
            resp = session.get(url, timeout=timeout, stream=stream, **kw)
            if resp.status_code >= 400:
                raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
            return resp
        except Exception as e:  # noqa: BLE001
            last_err = e
            if resp is not None:
                resp.close()
            if attempt < retries:
                wait = min(2 ** attempt, 4)
                LOGGER.debug("请求失败(%s) 第%d次: %s，%ss 后重试", url, attempt + 1, e, wait)
                time.sleep(wait)
    LOGGER.debug("请求最终失败 %s: %s", url, last_err)
    return None


def _guess_encoding(resp, fallback="utf-8"):
    """优先按 Content-Type charset 解码，其次 BOM，最后 fallback"""
    ct = (resp.headers.get("Content-Type") or "").lower()
    if "charset=" in ct:
        enc = ct.split("charset=", 1)[1].split(";")[0].strip().strip('"')
        if enc:
            return enc
    return fallback


def download_text(session, url, max_size_mb=20, timeout=15, retries=2, encoding=None,
                  no_cache=False):
    """下载文本内容（大小上限 + 重试 + 编码探测），失败返回 None

    no_cache=True 时附带 Pragma/Cache-Control 头，配合 URL 时间戳确保拿到上游最新版本。
    """
    headers = {"Cache-Control": "no-cache", "Pragma": "no-cache"} if no_cache else None
    resp = http_get(session, url, timeout=timeout, retries=retries, stream=True,
                    headers=headers)
    if resp is None:
        return None
    max_bytes = max(1, int(max_size_mb)) * 1024 * 1024
    chunks, size, first = [], 0, True
    try:
        enc = encoding or _guess_encoding(resp)
        for chunk in resp.iter_content(chunk_size=64 * 1024):
            if first and enc.lower().replace("_", "-") in ("utf-8", "utf8"):
                # 去掉 UTF-8 BOM
                chunk = chunk.lstrip(b"\xef\xbb\xbf")
                first = False
            chunks.append(chunk)
            size += len(chunk)
            if size > max_bytes:
                LOGGER.warning("来源超过大小上限(%sMB)，已截断: %s", max_size_mb, url)
                break
    except Exception as e:  # noqa: BLE001
        LOGGER.warning("读取内容失败 %s: %s", url, e)
        return None
    finally:
        resp.close()
    if not chunks:
        return None
    raw = b"".join(chunks)
    if encoding is None and enc.lower().replace("_", "-") in ("utf-8", "utf8"):
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            LOGGER.debug("UTF-8 解码失败，回退 gb18030: %s", url)
            return raw.decode("gb18030", errors="ignore")
    return raw.decode(enc or "utf-8", errors="ignore")
