import json
import os
from utils import load_json

def categorize_channel(name):
    """根据频道名称分类"""
    name = name.lower()
    if 'cctv' in name or '央视' in name:
        return '央视'
    elif '卫视' in name:
        return '卫视'
    elif '河北' in name:
        return '河北地方台'
    elif '北京' in name:
        return '北京地方台'
    elif '天津' in name:
        return '天津地方台'
    elif ('香港' in name or '澳门' in name or '台湾' in name 
          or '凤凰' in name or '翡翠' in name):
        return '港澳台'
    elif ('都市' in name or '体育' in name or '新闻' in name 
          or '教育' in name or '公共' in name):
        return '其他地方台'
    return '其他'

def merge_sources():
    """合并所有来源并分组"""
    # 加载所有数据
    sources = []
    sources += load_json('data/result1.json')
    sources += load_json('data/result2.json')
    sources += load_json('data/result3.json')
    
    # URL去重
    unique_sources = []
    url_set = set()
    for src in sources:
        if src.get('url') and src['url'] not in url_set:
            url_set.add(src['url'])
            unique_sources.append(src)
    
    # 重新分组
    categorized = {group: [] for group in [
        '央视', '卫视', '河北地方台', '北京地方台', 
        '天津地方台', '港澳台', '其他地方台'
    ]}
    
    for src in unique_sources:
        group = categorize_channel(src['name'])
        if group in categorized:
            categorized[group].append(src)
    
    # 生成M3U文件
    m3u_content = "#EXTM3U\n"
    for group, channels in categorized.items():
        m3u_content += f"#genre:{group}\n"
        for ch in channels:
            m3u_content += (
                f'#EXTINF:-1 tvg-id="{ch["name"]}" tvg-name="{ch["name"]}" '
                f'tvg-logo="{ch.get("logo", "")}" group-title="{group}",{ch["name"]}\n'
                f'{ch["url"]}\n'
            )
    
    # 保存结果
    os.makedirs('output', exist_ok=True)
    with open('output/tvbox.m3u', 'w', encoding='utf-8') as f:
        f.write(m3u_content)
    
    print(f"合并后频道数: {len(unique_sources)}")
    print(f"分组统计: {'; '.join([f'{k}:{len(v)}' for k, v in categorized.items()])}")

if __name__ == "__main__":
    merge_sources()
