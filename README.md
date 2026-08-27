# TVBox / 影视仓 直播源自动更新方案

基于 **GitHub Actions** 的直播源自动更新仓库：**定时爬取**公开直播源 → **测速校验**（HTTP 首包 + ffprobe 媒体探测）→ **自动择优** → **自动提交**，最终产出可直接填入 TVBox / 影视仓 / 各 IPTV 播放器的 `txt` / `m3u` 直播源文件。

> 仓库地址：`https://github.com/Lightconer/TVBox-Sources`

![更新状态](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/Lightconer/TVBox-Sources/main/output/badge.json)

---

## 一、工作流程（架构）

```
┌──────────────┐   每6小时/手动   ┌──────────────────────────────┐
│ GitHub Actions│ ──────────────▶ │  checkout → 装ffmpeg/python   │
└──────────────┘                  └──────────────┬───────────────┘
                                                 ▼
                              ┌───────────────────────────────────┐
                              │ ① 爬取：多源 m3u/txt/json → 解析   │
                              │ ② 过滤：分组/关键词/协议/国家 → 去重 │
                              │ ③ 测速：HTTP首包(TTFB) + ffprobe   │
                              │ ④ 择优：打分→组内排序→截断         │
                              │ ⑤ 输出：live.m3u / live.txt / json │
                              └──────────────────┬────────────────┘
                                                 ▼
                              ┌───────────────────────────────────┐
                              │ ⑥ 有变化才提交并推送（无空提交噪音） │
                              └───────────────────────────────────┘
                                                 ▼
                           TVBox/影视仓 填入 raw 链接即可自动更新
```

**打分规则**：延迟满分 60（≤500ms 满分，每慢 1s 扣 6 分，10s 归零）+ 媒体有效性 40 分（ffprobe 确认能解出视频流才给）。低于 `min_score` 的频道直接丢弃。

---

## 二、目录结构

```
tvbox-live-sync/
├── .github/workflows/update.yml   # GitHub Actions 工作流（定时+手动触发+自动提交）
├── config/config.yaml             # 数据源 / 过滤 / 测速 / 输出配置
├── scripts/
│   ├── run.py                     # 主流程入口（爬取→测速→生成）
│   ├── crawl.py                   # 爬取解析：m3u/txt/json、过滤、去重
│   ├── check.py                   # 测速校验：HTTP 首包 + ffprobe + 打分择优
│   ├── output.py                  # 输出：m3u / txt / json / badge
│   └── utils.py                   # 日志与 HTTP 请求公共工具
├── output/                        # 生成的直播源（提交到仓库供 raw 链接读取）
│   ├── live.txt                   # ★ TVBox/影视仓 txt 格式
│   ├── live.m3u                   # ★ 标准 m3u 格式
│   ├── live.json                  # 结构化数据（程序读取用）
│   ├── status.json                # 本次运行统计
│   └── badge.json                 # README 徽章数据
├── requirements.txt
└── README.md
```

---

## 三、快速开始（部署到 GitHub，约 3 分钟）

1. **新建仓库**（建议 public，private 也能用但需在 TVBox 端做认证），把本项目所有文件推上去：

   ```bash
   git init && git add . && git commit -m "init: tvbox live sync"
   git remote add origin https://github.com/<你>/<仓库>.git
   git push -u origin main
   ```

2. **修改配置**：编辑 `config/config.yaml`
   - 在 `crawl.sources` 里增删直播源（支持 m3u / txt / json）；
   - 按需调整 `filters`（分组、关键词、国家）、`check`（并发数、超时、每个分组保留数）；
   - 把 `output.header.url` 改成你的仓库地址。

3. **开启 Actions**：仓库页面 **Actions** 标签 → 首次会自动运行；也可以直接点 **Run workflow** 手动触发一次。

4. **验证**：Actions 跑完后，`output/` 下会更新出 `live.txt` / `live.m3u`，点进 commit 可看到「自动更新直播源」提交记录。

> 默认使用 `GITHUB_TOKEN` 自动提交到本仓库，无需任何密钥配置。若仓库开了分支保护导致推送失败，见「常见问题」。

---

## 四、在 TVBox / 影视仓 中使用

拿到生成文件的 raw 链接（任选其一），填入播放器的 **直播源地址 / 订阅地址**：

| 文件 | 直链（raw） | CDN 加速（jsdelivr，推荐国内使用） |
|---|---|---|
| TXT | `https://raw.githubusercontent.com/Lightconer/TVBox-Sources/main/output/live.txt` | `https://cdn.jsdelivr.net/gh/Lightconer/TVBox-Sources@main/output/live.txt` |
| M3U | `https://raw.githubusercontent.com/Lightconer/TVBox-Sources/main/output/live.m3u` | `https://cdn.jsdelivr.net/gh/Lightconer/TVBox-Sources@main/output/live.m3u` |

- **影视仓**：设置 → 直播 → 添加直播源 → 粘贴 TXT 直链；
- **TVBox**：配置接口里把直播地址指向该 TXT/M3U 链接。

---

## 五、本地运行（可选）

不依赖 GitHub 也能在本地跑通全流程，方便调试配置：

```bash
# 安装依赖（已装可跳过）
pip install -r requirements.txt

# 完整流程（会自动检测 ffmpeg；本地未安装时自动降级为仅 HTTP 测速）
python scripts/run.py

# 本地调试：只测速前 50 个，快速看效果
python scripts/run.py --limit 50

# 只爬取生成、跳过测速
python scripts/run.py --skip-check
```

> 建议本地装一下 ffmpeg（`winget install ffmpeg` / `brew install ffmpeg` / `apt install ffmpeg`），测速更严格——会真正探测流是否可播放。

---

## 六、配置项速查（config/config.yaml）

| 配置 | 说明 | 默认 |
|---|---|---|
| `crawl.sources` | 数据源列表，`enabled: false` 可停用 | - |
| `crawl.request.timeout / retries / max_size` | 请求超时、重试、单源最大下载 MB | 15 / 2 / 20 |
| `crawl.filters.include_groups` | 只保留的分组（小写） | 空=全部 |
| `crawl.filters.exclude_groups` | 排除的分组（支持正则） | 空 |
| `crawl.filters.include_keywords / exclude_keywords` | 频道名关键词过滤 | 空 / 测试、广告等 |
| `crawl.filters.country` | 按 tvg-country 过滤（如 `CN`） | 空=不过滤 |
| `crawl.filters.allowed_schemes` | 允许的协议 | http, https |
| `check.workers` | 并发测速线程数 | 30 |
| `check.connect_timeout / read_timeout / ffprobe_timeout` | 连接 / 首包 / 探测超时（秒） | 5 / 8 / 10 |
| `check.require_ffprobe` | 是否强制 ffprobe 媒体校验 | true |
| `check.min_score` | 最低通过分，低于丢弃 | 40 |
| `check.max_per_group` | 每分组最多保留频道数（0=不限） | 50 |
| `output.formats` | 输出的格式 | m3u, txt, json |

---

## 七、定时节奏与提交策略

- **cron**：`0 0,6,12,18 * * *`（UTC）→ 北京时间 **02:00 / 08:00 / 14:00 / 20:00** 各跑一次。GitHub 对免费仓库定时任务可能有分钟级延迟，属正常。
- **空提交保护**：工作流先暂存 `live.*` 三个文件，`git diff --cached` 为空则跳过提交——频道没变就不会产生无意义 commit；只有内容变化才连 `status.json` / `badge.json` 一起提交。
- **失败保护**：若某次爬取 0 频道或全部失效，脚本 `sys.exit(1)` 终止，**不会用空文件覆盖**上次有效输出。

---

## 八、常见问题

1. **Actions 推不上去？**
   默认 `GITHUB_TOKEN` 只能推本仓库、且不能通过需要 status check 的分支保护。若失败：仓库 Settings → Secrets → 新建 `GH_TOKEN`（fine-grained PAT，勾选仓库 `Contents: Read and write`），并把工作流里 `git push` 改为
   `git push "https://x-access-token:${{ secrets.GH_TOKEN }}@github.com/${GITHUB_REPOSITORY}.git" HEAD:${GITHUB_REF##*/}`

2. **生成的源打不开 / 频道少？**
   直播源稳定性由上游决定，公开源常有失效地址。可：加大 `workers`、放宽 `min_score`、增加更多数据源、调大 `max_per_group`。

3. **要不要 commit 输出文件？**
   要。TVBox 通过 raw 链接读 `output/` 文件，文件必须提交进仓库才能被访问；`cache/`、`__pycache__` 等已 gitignore。

4. **国内访问 raw.githubusercontent.com 慢？**
   用 jsdelivr CDN 链接（见上表），或把输出同步到 Gitee/自有服务器后改链接。

---

## 九、免责声明

本项目仅用于技术学习与个人收藏，直播源来自公开网络，版权归原权利人所有；请遵守所在地法律法规，勿用于商业传播。
