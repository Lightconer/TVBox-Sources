import requests
from utils import save_json

def fetch_official_sources():
    """
    从iptv-org项目获取中国官方直播源
    数据源：https://iptv-org.github.io/iptv/countries/cn.m3u
    """
    url = "https://iptv-org.github.io/iptv/countries/cn.m3u"
    response = requests.get(url)
    sources = []
    
    if response.status_code == 200:
        lines = response.text.split('\n')
        channel = {}
        for line in lines:
            if line.startswith('#EXTINF:'):
                # 解析频道信息 (e.g. "tvg-id="CCTV1" tvg-name="CCTV1"")
                info = line.split('"')
                channel = {
                    'name': info[3],
                    'url': '',
                    'logo': next((s.split('=')[1] for s in info if 'tvg-logo' in s), ''),
                    'group': next((s.split('=')[1] for s in info if 'group-title' in s), '')
                }
            elif line.startswith('http'):
                channel['url'] = line
                sources.append(channel)
                channel = {}
    
    save_json(sources, 'data/result1.json')
    print(f"获取官方源: {len(sources)} 个频道")
    return sources

if __name__ == "__main__":
    fetch_official_sources()
