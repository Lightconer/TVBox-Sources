# TVBox 直播源自动更新

本项目通过GitHub Actions每日自动收集并更新TVBox直播源。

## 直播源分组

- 央视
- 卫视
- 河北地方台
- 北京地方台
- 天津地方台
- 港澳台
- 其他地方台

## 使用方式

1. 在TVBox应用中添加以下直播源地址：
2. https://raw.githubusercontent.com/Lightconer/TVBox-Sources/main/output/tvbox.m3u
3.  或直接下载 `output/tvbox.m3u` 文件使用

## 更新频率

每日北京时间6:00自动更新。

## 手动运行

点击仓库的 Actions 标签页，选择 "Daily TVBox Source Update"，然后点击 "Run workflow"。

## 项目结构
📁 TVBox-Sources
├── .github/workflows/daily_update.yml # 自动更新配置
├── scripts/ # 处理脚本
│ ├── step1_official_sources.py # 官方源获取
│ ├── step2_tvbox_coder.py # TVBox接口解析
│ ├── step3_hebei_search.py # 河北源搜索
│ ├── step4_merge.py # 合并与分组
│ └── utils.py # 工具函数
└── output/tvbox.m3u # 生成的直播源
