# TVBox-Sources

自己用的直播源订阅。GitHub Actions 每 4 小时自动爬取、测速、分组，生成 `live.txt` / `live.m3u` / `tvbox.json`。

## 在哪用 / 复制哪个地址

所有生成的文件都在 [output 目录](https://github.com/Lightconer/TVBox-Sources/tree/main/output)。下面的地址复制粘贴就行，播放器端不用改别的。

**电视 / 盒子：TVBox、影视仓、天剑等空壳类**

在「设置 → 配置地址」里填：

```
https://cdn.jsdelivr.net/gh/Lightconer/TVBox-Sources@main/output/tvbox.json
```

**手机 / 电脑：PotPlayer、VLC、TiviMate、Perfect Player 等**

播放列表地址填这个，带台标和分组：

```
https://cdn.jsdelivr.net/gh/Lightconer/TVBox-Sources@main/output/live.m3u
```

只认 TXT 的播放器（DIYP、百川等）用这个：

```
https://cdn.jsdelivr.net/gh/Lightconer/TVBox-Sources@main/output/live.txt
```

CDN 偶尔慢半拍，想拿刚生成的版本就把 `cdn.jsdelivr.net/gh/` 换成 `raw.githubusercontent.com/`、去掉 `@main`：

```
https://raw.githubusercontent.com/Lightconer/TVBox-Sources/main/output/live.m3u
```

### output 里都有什么

| 文件 | 用途 |
|---|---|
| `tvbox.json` | TVBox / 影视仓配置接口，里面指向下面两个源文件 |
| `live.txt` | `频道名,地址` 格式，空壳播放器最常用 |
| `live.m3u` | 带台标、分组、EPG 字段，通用性最好 |
| `status.json` | 本次更新的统计（爬取数、频道数、上游版本等） |
| `badge.json` | README 徽章数据，显示更新时间和频道数 |

## 本地运行

依赖只有 requests 和 PyYAML：

```bash
pip install -r requirements.txt
python scripts/run.py
```

装了 ffprobe 会真正探测视频流能不能播，没装就只测 HTTP 首包，不影响出结果。

调试常用：

```bash
python scripts/run.py --limit 50        # 只测前 50 个频道
python scripts/run.py --skip-check      # 跳过测速
python scripts/run.py --verify-only     # 校验已有输出
python scripts/diag.py output/live.txt  # 用本地网络逐个诊断
```

Actions 在海外跑，测速结果跟国内实际观感会有出入，播放有问题时以 `diag.py` 的结果为准。

## 分组

央视 / 卫视 / 地方 / 港澳国际 / 其他，另有体育、少儿、电影、戏曲、4K 几个专题组。同一个频道可以同时在多个组里，比如 CCTV-5 既在「央视频道」也在「体育专区」，CCTV-6 既在「央视频道」也在「电影剧场」。

组内按频道号排（CCTV-1、CCTV-2 …… CCTV-10），同一频道的几条线路按画质从高到低排，同画质再按速度。没有 1080p 及以上的线路时就往下用低清晰度，不会因为找不到高清源就把频道丢掉。

分组规则写在 `config/config.yaml`，想恢复成「一个频道只在一个组」把 `output.group_map.multi_group` 改成 `false` 就行。

## 数据源

源列表在 `config/config.yaml` 的 `crawl.sources`，`enabled: false` 可以临时关掉某个。抓取走 raw 并带时间戳，避免拿到几小时前的旧内容。

想让上游一更新就立刻触发，可以配 `repository_dispatch`：

```bash
curl -X POST \
  -H "Authorization: Bearer <Token>" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/Lightconer/TVBox-Sources/dispatches \
  -d '{"event_type":"upstream-push"}'
```

不配也没关系，定时已经够用。

## 说明

直播源来自公开网络，仅供个人学习和收藏，别用于商业传播。
