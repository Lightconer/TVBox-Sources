# TVBox-Sources

自己用的直播源订阅。GitHub Actions 每 4 小时自动爬取、测速、分组，生成 `live.txt` / `live.m3u` / `tvbox.json`。

## 在哪用 / 复制哪个地址

所有生成的文件都在 [output 目录](https://github.com/Lightconer/TVBox-Sources/tree/main/output)。下面的地址复制粘贴就行，播放器端不用改别的。

**电视 / 盒子：TVBox、影视仓等空壳类**

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

顺序固定为：央视 → 卫视 → 河北 → 地方 → 体育 → 少儿 → 电影 → 戏曲 → 4K → 港澳国际 → 其他。同一个频道可以同时在多个组里，比如 CCTV-5 既在「央视频道」也在「体育专区」，CCTV-6 既在「央视频道」也在「电影剧场」，河北卫视既在「河北频道」也在「卫视频道」。

组内按频道号排（CCTV-1、CCTV-2 …… CCTV-10），河北组再按「省级台 → 各地市台」排。同一频道的几条线路按画质从高到低排，同画质再按速度。没有 1080p 及以上的线路时就往下用低清晰度，不会因为找不到高清源就把频道丢掉。

列表最上面有个「更新时间」组，里面写着本次抓取的时间和频道数，打开直播就能确认数据新不新；不想要就把 `output.notice.enabled` 改成 `false`。

分组规则写在 `config/config.yaml`，想恢复成「一个频道只在一个组」把 `output.group_map.multi_group` 改成 `false` 就行；想调组的先后顺序改 `output.group_order`。

### 频道别名

同一个台在不同源里有好几种写法：`CCTV1`、`CCTV-1`、`CCTV 1 50 FPS`、`中央一台`、`央视一套`、`中央电视台综合频道`。`config/alias.yaml` 把它们归到同一个名字上，最终只出现一条 `CCTV-1`，多条线路挂在它下面。

```yaml
cctv_normalize: true          # 央视台号自动归一，覆盖 CCTV1/中央一台/央视一套 这类
aliases:                      # 规律吃不掉的写法手工补
  CCTV-1: ["中央电视台综合频道", "央视综合频道"]
```

归一规则只认「前缀 + 台号 + 尾巴」这种结构，所以 `CCTV-4K`、`CCTV-4 美洲`、`CCTV-1 香港` 不会被误并。

## EPG 节目单

`output.epg` 填的是 fanmingming 仓库里的 `e.xml`（123 个频道、3.6 万条节目）。填了之后：

- `live.m3u` 头部会带 `url-tvg` / `x-tvg-url`
- `tvbox.json` 里带上 epg 地址
- 频道的 `tvg-id` 会换成 EPG 用的写法（`CCTV-1` → `CCTV1`），播放器才能把节目单挂上

每轮还会探一遍 `output.epg_candidates` 里的地址，结果写在 `status.json` 的 `epg` 字段，换源前先看那里哪个是 `ok: true`。

## 国内节点（可选）

Actions 跑在海外，测速出来的「快」和家里的实际体验不是一回事。`update-cn.yml` 是给自托管 runner 用的：在 NAS / 软路由 / 小主机上装个 runner、打个 `iptv-cn` 标签，就能用国内线路测速。

```bash
# 在 runner 机器上
./config.sh --url https://github.com/Lightconer/TVBox-Sources --token <TOKEN> --labels iptv-cn
```

装好后到 Actions 里手动跑一次「直播源更新（国内节点）」验证；确认没问题再去掉工作流里 `schedule` 的注释，让它定时跑。没装 runner 之前别开定时，否则任务会一直排队。

## 数据源

源列表在 `config/config.yaml` 的 `crawl.sources`，`enabled: false` 可以临时关掉某个。抓取走 raw 并带时间戳，避免拿到几小时前的旧内容。

单个源还能配 `filters` 做二次筛选，比如 iptv-org 的全球总索引有一万多条，只挑中国大陆和港澳台的：

```yaml
- name: "iptv-org 全球索引(港澳台+大陆)"
  url: "https://raw.githubusercontent.com/iptv-org/iptv/gh-pages/index.m3u"
  filters:
    include_id: ["\\.(cn|hk|tw|mo)@"]   # 按 tvg-id 里的国家码筛
  country_group: { cn: "中国大陆", hk: "香港", tw: "台湾", mo: "澳门" }
```

`include_name` / `include_id` / `include_country` 之间是「或」，`exclude_name` 是排除，`limit` 可以硬性截断条数。

zbefine、茶客源、BigBigGrandG 这三个源最后更新停在 2023 年，已经标成 `[停更]` 并 `enabled: false`，地址留着方便以后复查。

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
