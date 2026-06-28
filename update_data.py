import pandas as pd
import json
import cloudscraper
import time
import random
import requests
import yfinance as yf
from bs4 import BeautifulSoup
from datetime import datetime

scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})

FILE_MAP = {
    "高股息": "data_high_div.json",
    "市值型": "data_market_cap.json",
    "主題型": "data_theme.json",
    "債券型": "data_bond.json",
    "槓桿型": "data_leverage.json",
    "反向型": "data_inverse.json",
    "綜合/其他": "data_other.json"
}

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
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    print("取得官方最新 ETF 名冊...")
    
    # 1. 嘗試 OpenAPI
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", headers=headers, timeout=10)
        if res.status_code == 200:
            for item in res.json():
                if str(item.get('Code', '')).startswith('00'):
                    tickers[f"{item['Code']}.TW"] = item.get('Name', '未知名稱')
    except: pass
    
    try:
        res = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", headers=headers, timeout=10)
        if res.status_code == 200:
            for item in res.json():
                code = str(item.get('SecuritiesCompanyCode', ''))
                if code.startswith('00'):
                    tickers[f"{code}.TWO"] = item.get('CompanyName', '未知名稱')
    except: pass

    # 2. 嘗試從淨值網頁反向萃取代號
    if not tickers:
        try:
            res = scraper.get("https://www.twse.com.tw/rwd/zh/fund/MI_101?response=json", headers=headers, timeout=10).json()
            for row in res.get('data', []):
                code = str(row[0])
                if code.startswith('00'):
                    tickers[f"{code}.TW"] = str(row[1])
        except: pass
        try:
            res = scraper.get("https://www.tpex.org.tw/web/etf/g_info/fund_info_prb.php?l=zh-tw", headers=headers, timeout=10).json()
            for row in res.get('aaData', []):
                code = str(row[0])
                if code.startswith('00'):
                    tickers[f"{code}.TWO"] = str(row[1])
        except: pass

    # 3. 備案暴力生成模式
    if not tickers:
        for i in range(50, 1000):
            tickers[f"00{str(i).zfill(3)}.TW"] = "台股 ETF"
            tickers[f"00{str(i).zfill(3)}B.TWO"] = "債券 ETF"

    return tickers

def get_official_nav():
    nav_dict = {}
    headers = {"User-Agent": "Mozilla/5.0"}
    print("同步證交所與櫃買中心官方淨值...")
    try:
        res = scraper.get("https://www.twse.com.tw/rwd/zh/fund/MI_101?response=json", headers=headers, timeout=10).json()
        for row in res.get('data', []):
            try: nav_dict[f"{row[0]}.TW"] = float(str(row[3]).replace(',', ''))
            except: pass
    except: pass
    try:
        res = scraper.get("https://www.tpex.org.tw/web/etf/g_info/fund_info_prb.php?l=zh-tw", headers=headers, timeout=10).json()
        for row in res.get('aaData', []):
            try: nav_dict[f"{row[0]}.TWO"] = float(str(row[3]).replace(',', ''))
            except: pass
    except: pass
    
    if not nav_dict:
        try:
            for item in scraper.get("https://openapi.twse.com.tw/v1/exchangeReport/MI_101", headers=headers, timeout=10).json():
                try: nav_dict[f"{item['Code']}.TW"] = float(str(item.get('Nav', '0')).replace(',', ''))
                except: pass
            for item in scraper.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_etf_nav", headers=headers, timeout=10).json():
                try: nav_dict[f"{item['SecuritiesCompanyCode']}.TWO"] = float(str(item.get('Nav', '0')).replace(',', ''))
                except: pass
        except: pass

    return nav_dict

def get_robust_fund_data(ticker, current_price):
    aum, nav, y_ttm, d_rate = None, None, None, None
    try:
        url = f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{ticker}?modules=summaryDetail"
        res = scraper.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5).json()
        summary = res.get('quoteSummary', {}).get('result', [{}])[0].get('summaryDetail', {})
        nav = summary.get('navPrice', {}).get('raw')
        assets = summary.get('totalAssets', {}).get('raw')
        mcap = summary.get('marketCap', {}).get('raw')
        if assets: aum = round(assets / 100000000, 2)
        elif mcap: aum = round(mcap / 100000000, 2)
        y_ttm = summary.get('trailingAnnualDividendYield', {}).get('raw')
        d_rate = summary.get('trailingAnnualDividendRate', {}).get('raw')
    except: pass

    if aum is None or nav is None:
        try:
            tk = yf.Ticker(ticker)
            info = tk.info
            if nav is None: nav = info.get('navPrice')
            if aum is None:
                assets2 = info.get('totalAssets')
                mcap2 = info.get('marketCap')
                shares2 = info.get('sharesOutstanding')
                if assets2: aum = round(assets2 / 100000000, 2)
                elif mcap2: aum = round(mcap2 / 100000000, 2)
                elif shares2 and current_price: aum = round((shares2 * current_price) / 100000000, 2)
        except: pass
    return {'aum': aum, 'nav': nav, 'yield': y_ttm, 'dividend_rate': d_rate}

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
        div_total = sum([float(div.get('amount', 0)) for div in result.get('events', {}).get('dividends', {}).values()])
        return df, div_total
    except: return None, 0

def main():
    etf_dict = get_all_taiwan_etfs()
    all_tickers = list(etf_dict.keys())
    print(f"總計找到 {len(all_tickers)} 檔 ETF，啟動全方位量化與新聞掃描...")
    
    official_nav = get_official_nav()
    
    db = {cat: [] for cat in FILE_MAP.keys()}
    news_db = {}
    success_count = 0
    
    for idx, ticker in enumerate(all_tickers):
        if idx % 20 == 0: print(f"運算進度: {idx+1} / {len(all_tickers)} 檔...")
            
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
            
            fund_info = get_robust_fund_data(ticker, current_price)
            nav = official_nav.get(ticker) or fund_info.get('nav')
            premium = ((current_price - nav) / nav) if nav and nav > 0 else None
            
            final_div_rate = div_total_chart if div_total_chart > 0 else fund_info.get('dividend_rate')
            final_yield = (final_div_rate / current_price) if final_div_rate and current_price > 0 else fund_info.get('yield')

            ticker_id = ticker.split('.')[0]
            
            if vol_20d > 100:
                news_db[ticker_id] = fetch_yahoo_news(ticker)
                time.sleep(random.uniform(0.3, 0.7))

            db[category].append({
                "id": ticker_id, "name": name, "price": current_price,
                "premium": premium, "aum": fund_info.get('aum'), "cagr_1y": cagr_1y,
                "sharpe": sharpe, "mdd": mdd, "yield_ttm": final_yield, 
                "dividend_rate": final_div_rate, "vol_20d": vol_20d
            })
            success_count += 1
        except: continue

    for cat, data in db.items():
        with open(FILE_MAP[cat], "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    ipo_db = [
        {"id": "00946", "name": "群益科技高息成長", "issueDate": "2026-05-09", "price": 10.0, "fee": 0.30, "freq": "月配", "topHoldings": "聯發科, 瑞昱, 聯詠"},
        {"id": "00947", "name": "台新臺灣IC設計", "issueDate": "2026-06-12", "price": 15.0, "fee": 0.40, "freq": "季配", "topHoldings": "台積電, 聯發科, 瑞昱"}
    ]
    for ipo in ipo_db:
        news_db[ipo['id']] = fetch_yahoo_news(ipo['id'])
        
    with open("data_ipo.json", "w", encoding="utf-8") as f:
        json.dump(ipo_db, f, ensure_ascii=False, indent=2)

    with open("data_news.json", "w", encoding="utf-8") as f:
        json.dump(news_db, f, ensure_ascii=False, indent=2)
        
    print(f"任務全數完成！共成功導出 {success_count} 檔有效 ETF 量化基本面與動態新聞。")

if __name__ == "__main__":
    main()
