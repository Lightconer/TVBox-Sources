# TVBox-Sources · 直播源自动更新（优化版）

> 在本仓库原自动化流水线基础上，接入多个公开直播源，
> 并接入一个国内可直连、每 4 小时自动更新的高质量上游源（带台标 / EPG），
> 分组按用户模板重组为 9 组并支持**跨组复用**，
> 同时修复了原流水线中发现的多处正确性与健壮性问题。

**目标**：定时爬取 → 测速校验 → 中文分组择优 → 自动生成 `live.txt` / `live.m3u` / `tvbox.json`，
输出文件保持原有结构，TVBox / 影视仓 **无需更改任何配置**即可继续使用原来的订阅地址。

---

## 一、和原项目相比改了什么

### ★ 上游主源（及时更新）

上游主源由部署在服务器上的扫描程序**每 4 小时**自动推送一次最新可用源
（实测 UTC 0/4/8/12/16/20 的 `:10` 左右，即北京 08/12/16/20/00/04）。其主 M3U 含 668 条地址、
329 个唯一频道、11 个分组，并带 `tvg-logo` 台标与 EPG 节目单，质量很高。已作为**首位数据源**接入（去重时优先保留）。

**「及时更新」三道机制**：

| 机制 | 实现 | 作用 |
|---|---|---|
| ① 定时错峰 | Actions 在 UTC `1/5/9/13/17/21` 的 `:50` 运行（北京 09/13/17/21/01/05 的 `:50`） | 上游推送后约 40 分钟再跑，既能拿到新版本，又避开缓存窗口 |
| ② 缓存绕过 | 上游源配置 `no_mirror: true` + `cache_bust: true` | 禁用 jsdelivr（缓存 12h 会拿到旧版本）；下载 URL 加时间戳并带 `Cache-Control: no-cache`，强制回源 |
| ③ 状态可观测 | `run.py` 用 GitHub API 查上游最新 commit，写入 `status.json.upstream` | 每次运行都能看到用的上游版本、提交时间和滞后分钟数；超 6h 未更新会告警 |

**准实时触发（可选）**：除了定时，工作流支持 `repository_dispatch`，上游一更新即可立刻触发。
在上游仓库添加 Webhook，或在任意地方调用：

```bash
curl -X POST \
  -H "Authorization: Bearer <你的 GitHub Token>" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/<你的用户名>/<你的仓库>/dispatches \
  -d '{"event_type":"upstream-push"}'
```

这样可把更新延迟从「最多 4 小时」缩短到「分钟级」。Token 需 `repo` 权限（细粒度 token 需 Contents: write）。
`status.json.upstream` 字段示例：

```json
"upstream": {
  "enabled": true, "repo": "<上游仓库>", "file": "tv/iptv4.m3u",
  "ok": true, "sha": "0d0e7b94ee36",
  "commit_time": "2026-11-27T08:14:50Z", "lag_minutes": 25, "stale": false
}
```

### 其他公开数据源
在基础源之外，还接入了 9 个公开可用的补充源（覆盖综合、IPv6、港澳台等），
统一在 `config/config.yaml` 的 `crawl.sources` 中维护，可按需 `enabled: true/false` 开关。

无效源（已停更、失效或需本地环境）默认 `enabled: false`；
`127.0.0.1` / `你的服务器IP` 等占位地址通过 `exclude_url_patterns` 自动过滤。

### 代码修复（详见各文件顶部注释）

| # | 原问题 | 修复 |
|---|---|---|
| 1 | `datetime.now()` 在 UTC runner 上写出 UTC 时间 | 改用 `ZoneInfo("Asia/Shanghai")` 北京时间 |
| 2 | `check.enabled: true` 配置项无人读取 | 主流程真正生效，另有 `--skip-check` 覆盖 |
| 3 | ffprobe 内部 `rw_timeout` 硬编码 4s 与配置 10s 冲突 | 由配置驱动，并加 `-probesize` / `-analyzeduration` |
| 4 | 每频道 `requests.get` 新建 Session，无连接复用 | 线程局部 Session 复用连接池 |
| 5 | 5 个数据源串行下载 | `ThreadPoolExecutor` 并发（可配 `crawl.workers`） |
| 6 | `#EXTINF` 名字含逗号时被截断 | 正则匹配引号外的第一个逗号 |
| 7 | `parse_json` 不识别 `urls` 数组 | 支持字符串/数组/嵌套 dict |
| 8 | 「更新时间」伪频道复用首个 URL，计入重复与徽章数 | 改为文件首行注释，统计与徽章用真实频道数 |
| 9 | `final_audit` 只统计重复 URL 不删除 | 真正按 URL 去重 |
| 10 | `diag.py` 用 `r.content[:300000]` 无界读直播流 | 改为 `iter_content` 有界读取，默认开启 TLS 校验 |
| 11 | 打分规则文档称「≤500ms 满分」但代码是 0ms 才满分 | 修正公式，与文档一致 |
| 12 | 展示名保留 `(720p)` 等尾缀，同频道显示不一致 | `display_name()` 统一清洗 |
| 13 | 重试/异常时响应未关闭，连接池泄漏 | `finally` 中 close |
| 14 | 仅 UTF-8 解码，GBK 源乱码 | 按 Content-Type charset + BOM + gb18030 回退 |
| 15 | m3u 属性值不转义 | `_esc()` 转义引号 |
| 16 | 数据源走 jsdelivr 缓存 12h，上游更新后仍拿旧版本 | 上游源 `no_mirror: true` + `cache_bust: true`（时间戳 + no-cache 头） |
| 17 | 无法得知用的是上游哪个版本、是否滞后 | GitHub API 查最新 commit 写入 `status.json.upstream`，超 6h 告警 |
| 18 | 一频道只能归一组，无法按模板跨组复用 | `multi_group` + `theme_groups` + 跨组展开，按 (组,URL) 去重 |
| 19 | 展示名清洗把「东方卫视4K」误删成「东方卫视」 | 4K/8K 作为频道身份保留，仅清洗纯画质标注 |

---

## 二、零配置变更接入（推荐）

如果你已经在 TVBox / 影视仓里填的是：

```
https://cdn.jsdelivr.net/gh/Lightconer/TVBox-Sources@main/output/tvbox.json
# 或
https://cdn.jsdelivr.net/gh/Lightconer/TVBox-Sources@main/output/live.txt
https://cdn.jsdelivr.net/gh/Lightconer/TVBox-Sources@main/output/live.m3u
```

那么：**把本项目的 `scripts/`、`config/`、`requirements.txt`、`.github/workflows/update.yml` 覆盖到你现有的那个仓库**（即订阅地址指向的仓库），
输出文件名、目录结构、`tvbox.json` 结构全部保持不变，播放器端**什么都不用改**。

`config/config.yaml` 中默认：

```yaml
output:
  header:
    url: "https://github.com/Lightconer/TVBox-Sources"   # 生成的 tvbox.json 订阅地址由此推导
```

保持该值与你的订阅地址所在仓库一致即可。若部署到自己的新仓库，只需把它改成你自己的仓库地址，
然后**一次性**把播放器配置地址改成新仓库的 `tvbox.json`（之后就不用再动了）。

> 提交策略：只有 `live.m3u` / `live.txt` / `tvbox.json` 有实质变化时才 commit，避免空提交噪音；
> 若爬取为 0 频道或择优后全空则终止，不覆盖上次有效输出。

---

## 三、目录结构

```
TVBox-Sources/
├── .github/workflows/update.yml   # 定时+手动+变更触发，自动提交，CDN 刷新
├── config/config.yaml             # 数据源 / 过滤 / 测速 / 分组 / 输出
├── scripts/
│   ├── run.py                     # 主流程（4 步）+ --verify-only 校验
│   ├── crawl.py                   # 并发爬取：m3u/m3u8/txt/json 解析、过滤去重
│   ├── group_map.py               # 中文分组映射（央视/卫视/各省/专题）
│   ├── check.py                   # HTTP 首包 + ffprobe 测速、打分、多源择优
│   ├── output.py                  # m3u / txt / tvbox.json / badge 输出
│   ├── utils.py                   # 日志、Session、重试下载、编码探测
│   └── diag.py                    # 分片级严格诊断（国内视角）
├── output/                        # 生成物（需提交进仓库供 raw 链接读取）
│   ├── live.txt
│   ├── live.m3u
│   ├── tvbox.json
│   ├── status.json
│   └── badge.json
├── requirements.txt
└── README.md
```

---

## 四、本地运行

```bash
pip install -r requirements.txt

# 完整流程（自动检测 ffmpeg；未安装则降级为仅 HTTP 测速）
python scripts/run.py --config config/config.yaml

# 国内网络：让 jsdelivr 镜像优先，避免 raw 超时
export LIVE_SOURCE_PREFER_MIRROR=1        # bash
$env:LIVE_SOURCE_PREFER_MIRROR = "1"      # PowerShell

# 快速调试：只测速前 50 个 / 跳过测速
python scripts/run.py --limit 50
python scripts/run.py --skip-check

# 仅校验现有输出文件结构（CI 中也会跑）
python scripts/run.py --verify-only

# 国内视角严格诊断：找出会显示「播放失败」的频道
python scripts/diag.py output/live.txt --workers 24 --json-out diag-report.json
```

> 建议安装 ffmpeg（`winget install ffmpeg` / `brew install ffmpeg` / `apt install ffmpeg`），
> 测速会真正探测视频流是否可播放，而不是只看 HTTP 200。

---

## 五、分组规则（按模板重组）

按用户模板重组为 **9 个分组** + 1 个兜底组，**支持同一频道跨组出现**：

| 分组 | 匹配规则 | 说明 |
|---|---|---|
| 央视频道 | CCTV / CGTN / 央视 / 中央 | 央视全系列 |
| 卫视频道 | 卫视（排除河北卫视） | 省级卫视 |
| 地方频道 | 河北卫视 / 城市台 / 市县频道 | 地方频道 |
| 港澳国际 | 凤凰 / TVB / 翡翠 / 星空 / 港澳台 | 港澳台及国际 |
| 其他频道 | `.*` 兜底 | 未命中以上规则的频道 |
| 体育专区 *专题* | CCTV-5/5+/16、五星体育、广东体育… | 从央视频道等**跨组**复用 |
| 少儿动画 *专题* | CCTV-14、金鹰卡通、卡酷少儿… | 跨组复用 |
| 电影剧场 *专题* | CCTV-6、CHC、第一剧场… | 跨组复用 |
| 戏曲音乐 *专题* | CCTV-11/15、梨园频道… | 跨组复用 |
| 4K超高清 *专题* | CCTV-4K/8K、东方卫视4K、苏州4K… | 跨组复用 |

**跨组原理**（`group_map.multi_group: true`）：
- `rules` 定**主组**（唯一），`theme_groups` 命中就**追加**到 `groups`
- 输出前 `expand_groups()` 把频道展开成「每组一条」，例：CCTV-5 同时出现在
  `央视频道` 和 `体育专区`；东方卫视4K 同时出现在 `卫视频道` 和 `4K超高清`
- 只命中兜底组却命中专题组的（如「五星体育」），专题组自动升为主组，避免冗余
- 去重改为按 `(分组, URL)`，跨组副本不会被误删
- `4K/8K` 作为频道名一部分时保留（东方卫视4K 不再被清洗成 东方卫视）

> 想改回「一频道一组」：设 `group_map.multi_group: false` 即可，无需改代码。

---

## 六、配置速查

| 配置项 | 说明 | 默认 |
|---|---|---|
| `upstream.check / cache_bust` | 查询上游 commit / 加时间戳绕过 CDN | true / true |
| `upstream.stale_warn_minutes` | 上游超多久未更新则告警 | 360（6h） |
| `crawl.sources[].no_mirror` | 源级禁用 jsdelivr 镜像（时效性高的源必开） | false（上游主源为 true） |
| `crawl.sources[].cache_bust` | 源级加时间戳强制回源 | false（上游主源为 true） |
| `crawl.workers` | 并发爬取源数量 | 6 |
| `crawl.request.timeout / retries / max_size` | 单源请求超时 / 重试 / 最大 MB | 12 / 1 / 20 |
| `crawl.request.exclude_url_patterns` | URL 黑名单正则（过滤占位符） | 127.0.0.1 等 |
| `crawl.filters.exclude_keywords` | 频道名关键词黑名单（含失效标注） | 测试/失效/offline… |
| `crawl.filters.exclude_channels` | 精确黑名单（子串匹配） | iptv-org 海外子台等 |
| `check.enabled` | 是否测速（**本次修复为真正生效**） | true |
| `check.workers` | 并发测速线程数 | 30 |
| `check.connect_timeout / read_timeout / ffprobe_timeout` | 连接 / 首包 / 探测超时 | 5 / 8 / 10 |
| `check.min_score` | 最低通过分（≤500ms 延迟满分 60 + 媒体 40） | 30 |
| `check.max_per_group` | 每分组最多频道数（0=不限） | 60 |
| `check.max_urls_per_channel` | 每频道最多保留地址数（冗余自动切换） | 8 |
| `output.epg` | EPG 订阅地址（留空保持兼容） | "" |
| `output.group_map.rules` | 主分组规则（唯一归属，顺序匹配） | 央视→港澳国际→卫视→地方→其他 |
| `output.group_map.theme_groups` | 专题组规则（命中即追加，跨组复用） | 体育/少儿/电影/戏曲/4K |
| `output.group_map.multi_group` | 是否开启跨组复用 | true |
| `output.group_map.max_per_group` | 专题组每组上限（0=不限） | 0 |

---

## 七、注意事项

- **测速视角**：GitHub Actions 在海外执行，反映的是海外到源的连通性；国内实际体验以本地 `diag.py` 为准。
- **IPv6**：没有 IPv6 宽带就把 `fanmingming IPv6专用` 和 BigBigGrandG / Kimentanm / YanG 源设为 `enabled: false`。
- **失效源**：`config.yaml` 中已标注 `[已失效]` 的源默认关闭；如要重新启用，先确认其可用性。
- **版权**：直播源来自公开网络，仅供技术学习与个人收藏，请遵守所在地法律法规，勿用于商业传播。
