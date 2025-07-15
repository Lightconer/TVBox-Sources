import requests
from bs4 import BeautifulSoup
from utils import save_json

def search_hebei_sources():
    """
    通过搜索引擎获取河北地方台直播源
    数据源：
    1. https://www.foodieguide.com/iptvsearch/?s=河北
    2. http://tonkiang.us/?s=河北
    """
    sources = []
    # 引擎1: foodieguide
    try:
        url1 = "https://www.foodieguide.com/iptvsearch/?s=河北"
        res1 = requests.get(url1, timeout=15)
        soup1 = BeautifulSoup(res1.text, 'html.parser')
        for row in soup1.select('table tr'):
            tds = row.find_all('td')
            if len(tds) > 1:
                sources.append({
                    'name': tds[0].text.strip(),
                    'url': tds[1].text.strip(),
                    'group': '河北地方台'
                })
    except Exception as e:
        print(f"搜索引擎1失败: {str(e)}")
    
    # 引擎2: tonkiang
    try:
        url2 = "http://tonkiang.us/?s=河北"
        res2 = requests.get(url2, timeout=15)
        soup2 = BeautifulSoup(res2.text, 'html.parser')
        for div in soup2.select('div.resultdiv'):
            name = div.find('b').text if div.find('b') else '未知频道'
            url = div.find('a')['href'] if div.find('a') else ''
            if url:
                sources.append({
                    'name': name,
                    'url': url,
                    'group': '河北地方台'
                })
    except Exception as e:
        print(f"搜索引擎2失败: {str(e)}")
    
    save_json(sources, 'data/result3.json')
    print(f"获取河北地方源: {len(sources)} 个频道")
    return sources

if __name__ == "__main__":
    search_hebei_sources()
