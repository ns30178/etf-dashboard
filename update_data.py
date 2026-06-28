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
    """根據中文名稱自動分類 ETF 類型"""
    if any(k in name for k in ['正2', '正達', '倍']): return "槓桿型"
    if any(k in name for k in ['反1', '反向']): return "反向型"
    if any(k in name for k in ['高息', '高股息', '優息', '股息', '息收']): return "高股息"
    if any(k in name for k in ['債', '國債', '投等', '金融債', '公司債']): return "債券型"
    if any(k in name for k in ['半導體', '電動車', '5G', 'AI', '科技', '生技', '不動產', '綠能', '網購', '主題', '電競']): return "主題型"
    if any(k in name for k in ['50', '100', '市值', '加權', '大盤', '摩台', 'MSCI', '中型', '富時']): return "市值型"
    if any(k in name for k in ['不動產', 'REITs']): return "綜合/其他"
    return "綜合/其他"

def get_all_taiwan_etfs():
    """從官方 API 提取代號與正確中文名稱"""
    tickers = {}
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    print("取得證交所(上市)代號與中文名稱...")
    try:
        resp = scraper.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", headers=headers, timeout=10)
        for item in resp.json():
            if str(item.get('Code', '')).startswith('00'):
                tickers[f"{item['Code']}.TW"] = item.get('Name', '未知名稱')
    except Exception: pass

    print("取得櫃買中心(上櫃)代號與中文名稱...")
    try:
        resp = scraper.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", headers=headers, timeout=10)
        for item in resp.json():
            code = str(item.get('SecuritiesCompanyCode', ''))
            if code.startswith('00'):
                tickers[f"{code}.TWO"] = item.get('CompanyName', '未知名稱')
    except Exception: pass
    return tickers

def get_official_nav():
    """直接從台灣證交所/櫃買中心取得最準確的官方每日淨值"""
    nav_dict = {}
    print("向官方 API 獲取每日淨值資料...")
    try: # 上市淨值
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/MI_101", timeout=10).json()
        for item in res: nav_dict[f"{item['Code']}.TW"] = float(item.get('Nav', 0))
    except: pass
    try: # 上櫃淨值
        res = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_etf_nav", timeout=10).json()
        for item in res: nav_dict[f"{item['SecuritiesCompanyCode']}.TWO"] = float(item.get('Nav', 0))
    except: pass
    return nav_dict

def get_yahoo_aum(tickers_list):
    """利用流通股數精算資產規模(AUM)，避開 Yahoo 市值缺失問題"""
    aum_data = {}
    print("精算 ETF 資產規模...")
    for i in range(0, len(tickers_list), 40):
        batch = tickers_list[i:i+40]
        url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={','.join(batch)}"
        try:
            res = scraper.get(url, timeout=10).json().get('quoteResponse', {}).get('result', [])
            for item in res:
                shares = item.get('sharesOutstanding')
                price = item.get('regularMarketPrice')
                if shares and price:
                    aum_data[item['symbol']] = round((shares * price) / 100000000, 2) # 轉為億元
        except Exception: pass
        time.sleep(1)
    return aum_data

def fetch_yahoo_news(ticker):
    """抓取單檔 ETF 的最新財經新聞"""
    url = f"https://tw.stock.yahoo.com/quote/{ticker}/news"
    try:
        soup = BeautifulSoup(scraper.get(url, timeout=5).text, 'html.parser')
        news = []
        for a in soup.find_all('a', href=True):
            title, href = a.text.strip(), a['href']
            if len(title) > 10 and 'http' in href and ('news' in href or 'article' in href):
                news.append({"title": title, "link": href, "date": datetime.today().strftime('%Y-%m-%d')})
            if len(news) >= 3: break
        return news
    except: return []

def fetch_yahoo_data_and_dividends(ticker):
    """取得股價歷史並手動加總過去 12 個月的配息，確保配息率 100% 準確"""
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}?range=1y&interval=1d&events=div"
    try:
        resp = scraper.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10).json()
        result = resp.get('chart', {}).get('result', [{}])[0]
        
        # 1. 解析股價與成交量
        close_prices = result.get('indicators', {}).get('quote', [{}])[0].get('close')
        volumes = result.get('indicators', {}).get('quote', [{}])[0].get('volume')
        if not close_prices: return None, 0
        if not volumes: volumes = [0] * len(close_prices)
        df = pd.DataFrame({'Close': close_prices, 'Volume': volumes}).dropna()
        
        # 2. 解析並加總近一年的歷史配息
        div_total = 0.0
        events = result.get('events', {}).get('dividends', {})
        for ts, div in events.items():
            div_total += float(div.get('amount', 0))
            
        return df, div_total
    except Exception:
        return None, 0

def main():
    print("啟動全市場 ETF 分頁分類引擎...")
    etf_dict = get_all_taiwan_etfs()
    all_tickers = list(etf_dict.keys())
    
    # 預先抓取官方淨值與 AUM
    official_nav = get_official_nav()
    aum_dict = get_yahoo_aum(all_tickers)
    
    market_db = []
    success_count = 0
    
    for idx, ticker in enumerate(all_tickers):
        if idx % 20 == 0: print(f"處理進度: {idx+1} / {len(all_tickers)} ...")
            
        df, div_total = fetch_yahoo_data_and_dividends(ticker)
        if df is None or df.empty or len(df) < 20: continue
            
        try:
            current_price = float(df['Close'].iloc[-1])
            vol_20d = int(df['Volume'].tail(20).mean() / 1000) if not df['Volume'].empty else 0
            
            # 績效計算
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
            
            # 精算官方折溢價
            nav = official_nav.get(ticker)
            premium = ((current_price - nav) / nav) if nav and nav > 0 else None
            
            # 計算 TTM 殖利率
            yield_ttm = (div_total / current_price) if div_total > 0 and current_price > 0 else None

            news_data = fetch_yahoo_news(ticker) if vol_20d > 100 else []

            market_db.append({
                "id": ticker.split('.')[0],
                "name": name, 
                "category": category,
                "price": current_price,
                "premium": premium,
                "aum": aum_dict.get(ticker),
                "cagr_1y": cagr_1y,
                "sharpe": sharpe,
                "mdd": mdd,
                "yield_ttm": yield_ttm, 
                "dividend_rate": div_total if div_total > 0 else None,
                "vol_20d": vol_20d,
                "news": news_data
            })
            success_count += 1
            time.sleep(random.uniform(0.5, 1.2))
        except Exception: continue

    ipo_db = [
        {"id": "00946", "name": "群益科技高息成長", "issueDate": "2026-05-09", "price": 10.0, "fee": 0.30, "freq": "月配", "topHoldings": "聯發科, 瑞昱, 聯詠", "news": []},
        {"id": "00947", "name": "台新臺灣IC設計", "issueDate": "2026-06-12", "price": 15.0, "fee": 0.40, "freq": "季配", "topHoldings": "台積電, 聯發科, 瑞昱", "news": []}
    ]

    with open("market_data.json", "w", encoding="utf-8") as f:
        json.dump({"ipo": ipo_db, "main": market_db}, f, ensure_ascii=False, indent=2)
        
    print(f"量化運算完成！共寫入 {success_count} 檔有效數據。")

if __name__ == "__main__":
    main()
