import requests
import json
from utils import save_json

def parse_tvbox_coder():
    """
    解析TVBox接口源 (https://tvbox.wpcoder.cn)
    备用接口：https://tvbox.wpcoder.cn/tvbox.json
    """
    sources = []
    urls = [
        "https://tvbox.wpcoder.cn/tvbox.json",
        "https://tvbox.wpcoder.cn/meow.json"
    ]
    
    for url in urls:
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                # 遍历所有直播组 (央视/卫视/地方台等)
                for category in data.get('lives', []):
                    for channel in category.get('channels', []):
                        if channel.get('urls'):
                            sources.append({
                                'name': channel['name'],
                                'url': channel['urls'][0],  # 取第一个可用URL
                                'group': category['name']
                            })
                break  # 成功获取则停止尝试
        except:
            continue
    
    save_json(sources, 'data/result2.json')
    print(f"获取TVBox接口源: {len(sources)} 个频道")
    return sources

if __name__ == "__main__":
    parse_tvbox_coder()
