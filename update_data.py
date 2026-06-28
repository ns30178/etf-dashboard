import pandas as pd
import json
import cloudscraper
import time
from datetime import datetime

scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})

FILE_MAP = {
    "高股息": "data_high_div.json", "市值型": "data_market_cap.json",
    "主題型": "data_theme.json", "債券型": "data_bond.json",
    "槓桿型": "data_leverage.json", "反向型": "data_inverse.json",
    "綜合/其他": "data_other.json"
}

def safe_get(url, is_json=True):
    for _ in range(3):
        try:
            res = scraper.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            if res.status_code == 200: return res.json() if is_json else res.text
            elif res.status_code == 429: time.sleep(5)
        except: time.sleep(2)
    return {} if is_json else ""

def categorize_etf(name):
    if any(k in name for k in ['正2', '正達', '倍']): return "槓桿型"
    if any(k in name for k in ['反1', '反向']): return "反向型"
    if any(k in name for k in ['高息', '高股息', '優息', '股息', '息收']): return "高股息"
    if any(k in name for k in ['債', '國債', '投等', '金融債', '公司債']): return "債券型"
    if any(k in name for k in ['半導體', '電動車', '5G', 'AI', '科技', '生技', '不動產', '綠能', '網購', '主題', '電競']): return "主題型"
    if any(k in name for k in ['50', '100', '市值', '加權', '大盤', '摩台', 'MSCI', '中型', '富時']): return "市值型"
    return "綜合/其他"

def get_all_taiwan_etfs():
    tickers = {}
    try:
        res = safe_get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL")
        for item in res:
            if str(item.get('Code', '')).startswith('00'): tickers[f"{item['Code']}.TW"] = item.get('Name', '未知名稱')
    except: pass
    return tickers

def get_official_nav():
    nav_dict = {}
    try:
        res = safe_get("https://www.twse.com.tw/rwd/zh/fund/MI_101?response=json")
        for row in res.get('data', []):
            try: nav_dict[f"{row[0]}.TW"] = float(str(row[4]).replace(',', ''))
            except: pass
    except: pass
    return nav_dict

def get_yahoo_batch_info(tickers):
    batch_data = {}
    for i in range(0, len(tickers), 40):
        batch = tickers[i:i+40]
        url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={','.join(batch)}"
        res = safe_get(url)
        for item in res.get('quoteResponse', {}).get('result', []):
            sym = item.get('symbol')
            mcap = item.get('marketCap')
            price = item.get('regularMarketPrice')
            shares = item.get('sharesOutstanding')
            aum = (mcap / 100000000) if mcap else ((shares * price) / 100000000 if (shares and price) else None)
            batch_data[sym] = {
                'price': price,
                'aum': round(aum, 2) if aum else None,
                'dividend_rate': item.get('trailingAnnualDividendRate'),
                'yield_ttm': item.get('trailingAnnualDividendYield')
            }
        time.sleep(1)
    return batch_data

def fetch_historical_chart(ticker):
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}?range=1y&interval=1d"
    resp = safe_get(url)
    if not resp: return None
    try:
        result = resp.get('chart', {}).get('result', [{}])[0]
        close = result.get('indicators', {}).get('quote', [{}])[0].get('close')
        vol = result.get('indicators', {}).get('quote', [{}])[0].get('volume')
        return pd.DataFrame({'Close': close, 'Volume': vol}).dropna()
    except: return None

def main():
    etf_dict = get_all_taiwan_etfs()
    all_tickers = list(etf_dict.keys())
    official_nav = get_official_nav()
    batch_info = get_yahoo_batch_info(all_tickers)
    
    db = {cat: [] for cat in FILE_MAP.keys()}
    news_db = {}
    
    for ticker in all_tickers:
        info = batch_info.get(ticker, {})
        df = fetch_historical_chart(ticker)
        if df is None or df.empty: continue
        
        current_price = info.get('price') or float(df['Close'].iloc[-1])
        vol_20d = int(df['Volume'].tail(20).mean() / 1000)
        nav = official_nav.get(ticker)
        premium = ((current_price - nav) / nav) if nav and nav >
