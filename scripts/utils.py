"""公共工具：日志、HTTP 请求（带重试与大小上限）"""
import logging
import time

import requests

LOGGER = logging.getLogger("tvbox-live")

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
    cfg = cfg or {}
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": cfg.get("user_agent", DEFAULT_UA),
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
    )
    return s


def http_get(session, url, timeout=15, retries=2, stream=False, **kw):
    """带重试的 GET 请求，最终失败返回 None"""
    last_err = None
    for attempt in range(max(1, retries + 1)):
        try:
            resp = session.get(url, timeout=timeout, stream=stream, **kw)
            if resp.status_code >= 400:
                raise requests.HTTPError(f"HTTP {resp.status_code}")
            return resp
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < retries:
                wait = min(2 ** attempt, 4)
                LOGGER.debug("请求失败(%s) 第%d次: %s，%ss 后重试", url, attempt + 1, e, wait)
                time.sleep(wait)
    LOGGER.warning("请求最终失败 %s: %s", url, last_err)
    return None


def download_text(session, url, max_size_mb=20, timeout=15, retries=2):
    """下载文本内容（带大小上限与重试），失败返回 None"""
    resp = http_get(session, url, timeout=timeout, retries=retries, stream=True)
    if resp is None:
        return None
    max_bytes = max(1, int(max_size_mb)) * 1024 * 1024
    chunks, size = [], 0
    try:
        for chunk in resp.iter_content(chunk_size=64 * 1024):
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
    return b"".join(chunks).decode("utf-8", errors="ignore")
