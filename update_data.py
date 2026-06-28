import pandas as pd
import json
import cloudscraper
import time
import random
import requests
from bs4 import BeautifulSoup
from datetime import datetime

scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})

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
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        for item in scraper.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", headers=headers, timeout=10).json():
            if str(item.get('Code', '')).startswith('00'):
                tickers[f"{item['Code']}.TW"] = item.get('Name', '未知名稱')
    except: pass
    try:
        for item in scraper.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", headers=headers, timeout=10).json():
            code = str(item.get('SecuritiesCompanyCode', ''))
            if code.startswith('00'):
                tickers[f"{code}.TWO"] = item.get('CompanyName', '未知名稱')
    except: pass
    return tickers

def get_official_nav():
    nav_dict = {}
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        for item in scraper.get("https://openapi.twse.com.tw/v1/exchangeReport/MI_101", headers=headers, timeout=10).json():
            try: nav_dict[f"{item['Code']}.TW"] = float(str(item.get('Nav', '0')).replace(',', ''))
            except: pass
    except: pass
    try:
        for item in scraper.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_etf_nav", headers=headers, timeout=10).json():
            try: nav_dict[f"{item['SecuritiesCompanyCode']}.TWO"] = float(str(item.get('Nav', '0')).replace(',', ''))
            except: pass
    except: pass
    return nav_dict

def get_yahoo_fundamentals(tickers_list):
    fund_data = {}
    headers = {"User-Agent": "Mozilla/5.0"}
    for i in range(0, len(tickers_list), 40):
        batch = tickers_list[i:i+40]
        url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={','.join(batch)}"
        try:
            res = scraper.get(url, headers=headers, timeout=10).json().get('quoteResponse', {}).get('result', [])
            for item in res:
                sym = item.get('symbol')
                
                mcap = item.get('marketCap')
                assets = item.get('totalAssets')
                shares = item.get('sharesOutstanding')
                price = item.get('regularMarketPrice')
                
                aum = None
                if assets and assets > 0: aum = round(assets / 100000000, 2)
                elif mcap and mcap > 0: aum = round(mcap / 100000000, 2)
                elif shares and price and shares > 0: aum = round((shares * price) / 100000000, 2)
                
                fund_data[sym] = {
                    'aum': aum,
                    'nav': item.get('navPrice'),
                    'yield': item.get('trailingAnnualDividendYield') or item.get('dividendYield'),
                    'dividend_rate': item.get('trailingAnnualDividendRate') or item.get('dividendRate')
                }
        except: pass
        time.sleep(1)
    return fund_data

def fetch_yahoo_news(ticker):
    try:
        soup = BeautifulSoup(scraper.get(f"https://tw.stock.yahoo.com/quote/{ticker}/news", timeout=5).text, 'html.parser')
        news = []
        for a in soup.find_all('a', href=True):
            title, href = a.text.strip(), a['href']
            if len(title) > 10 and 'http' in href and ('news' in href or 'article' in href):
                news.append({"title": title, "link": href, "date": datetime.today().strftime('%Y-%m-%d')})
            if len(news) >= 3: break
        return news
    except: return []

def fetch_yahoo_data_and_dividends(ticker):
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}?range=1y&interval=1d&events=div"
    try:
        resp = scraper.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10).json()
        result = resp.get('chart', {}).get('result', [{}])[0]
        
        close_prices = result.get('indicators', {}).get('quote', [{}])[0].get('close')
        volumes = result.get('indicators', {}).get('quote', [{}])[0].get('volume')
        if not close_prices: return None, 0
        if not volumes: volumes = [0] * len(close_prices)
        df = pd.DataFrame({'Close': close_prices, 'Volume': volumes}).dropna()
        
        div_total = 0.0
        events = result.get('events')
        if events and 'dividends' in events:
            for ts, div in events['dividends'].items():
                div_total += float(div.get('amount', 0))
                
        return df, div_total
    except: return None, 0

def main():
    etf_dict = get_all_taiwan_etfs()
    all_tickers = list(etf_dict.keys())
    
    official_nav = get_official_nav()
    fundamentals = get_yahoo_fundamentals(all_tickers)
    
    market_db = []
    
    for idx, ticker in enumerate(all_tickers):
        df, div_total_chart = fetch_yahoo_data_and_dividends(ticker)
        if df is None or df.empty or len(df) < 20: continue
            
        try:
            current_price = float(df['Close'].iloc[-1])
            vol_20d = int(df['Volume'].tail(20).mean() / 1000) if not df['Volume'].empty else 0
            
            if len(df) >= 200:
                cagr_1y = float((current_price - df['Close'].iloc[0]) / df['Close'].iloc[0])
                max_p = df['Close'].expanding().max()
                mdd = float(((df['Close'] - max_p) / max_p).min())
                daily_ret = df['Close'].pct_change().dropna()
                sharpe = float((daily_ret.mean() / daily_ret.std()) * (252**0.5)) if daily_ret.std() != 0 else 0
            else:
                cagr_1y, sharpe = None, None
                mdd = float(((df['Close'] - df['Close'].expanding().max()) / df['Close'].expanding().max()).min())

            name = etf_dict.get(ticker, "未知名稱")
            category = categorize_etf(name)
            fund_info = fundamentals.get(ticker, {})
            
            nav = official_nav.get(ticker) or fund_info.get('nav')
            premium = ((current_price - nav) / nav) if nav and nav > 0 else None
            
            final_div_rate = div_total_chart if div_total_chart > 0 else fund_info.get('dividend_rate')
            final_yield = None
            if final_div_rate and current_price > 0:
                final_yield = final_div_rate / current_price
            elif fund_info.get('yield'):
                final_yield = fund_info.get('yield')

            news_data = fetch_yahoo_news(ticker) if vol_20d > 100 else []

            market_db.append({
                "id": ticker.split('.')[0],
                "name": name, 
                "category": category,
                "price": current_price,
                "premium": premium,
                "aum": fund_info.get('aum'),
                "cagr_1y": cagr_1y,
                "sharpe": sharpe,
                "mdd": mdd,
                "yield_ttm": final_yield, 
                "dividend_rate": final_div_rate,
                "vol_20d": vol_20d,
                "news": news_data
            })
            time.sleep(random.uniform(0.5, 1.2))
        except Exception: continue

    ipo_db = [
        {"id": "00946", "name": "群益科技高息成長", "issueDate": "2026-05-09", "price": 10.0, "fee": 0.30, "freq": "月配", "topHoldings": "聯發科, 瑞昱, 聯詠"},
        {"id": "00947", "name": "台新臺灣IC設計", "issueDate": "2026-06-12", "price": 15.0, "fee": 0.40, "freq": "季配", "topHoldings": "台積電, 聯發科, 瑞昱"}
    ]
    
    for ipo in ipo_db:
        ipo['news'] = fetch_yahoo_news(ipo['id'])

    with open("market_data.json", "w", encoding="utf-8") as f:
        json.dump({"ipo": ipo_db, "main": market_db}, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
