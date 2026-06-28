import pandas as pd
import json
import cloudscraper
import time
import random
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
    return "綜合/其他"

def get_all_taiwan_etfs():
    """從官方 API 提取代號與正確中文名稱"""
    tickers = {}
    headers = {'User-Agent': 'Mozilla/5.0'}
    print("取得官方 ETF 代號清單...")
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
    """獲取官方每日淨值(NAV)，並清洗千分位逗號字串"""
    nav_dict = {}
    headers = {"User-Agent": "Mozilla/5.0"}
    print("獲取官方每日淨值(NAV)...")
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
    """批次獲取規模與配息資料 (包含雙重防護計算)"""
    fund_data = {}
    headers = {"User-Agent": "Mozilla/5.0"}
    print("批次獲取規模與配息資料...")
    for i in range(0, len(tickers_list), 40):
        batch = tickers_list[i:i+40]
        url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={','.join(batch)}"
        try:
            res = scraper.get(url, headers=headers, timeout=10).json().get('quoteResponse', {}).get('result', [])
            for item in res:
                sym = item.get('symbol')
                
                # 防護機制：如果沒有 marketCap，就用在外流通股數乘上市價
                mcap = item.get('marketCap')
                shares = item.get('sharesOutstanding')
                price = item.get('regularMarketPrice')
                
                aum = None
                if mcap and mcap > 0: aum = round(mcap / 100000000, 2)
                elif shares and price and shares > 0: aum = round((shares * price) / 100000000, 2)
                
                fund_data[sym] = {
                    'aum': aum,
                    'yield': item.get('trailingAnnualDividendYield') or item.get('dividendYield'),
                    'dividend_rate': item.get('trailingAnnualDividendRate') or item.get('dividendRate')
                }
        except: pass
        time.sleep(1)
    return fund_data

def fetch_yahoo_news(ticker):
    """抓取單檔 ETF 的最新財經新聞"""
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
    """直接加總歷史除權息事件，精算配息額度"""
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
    print("啟動全市場 ETF 掃描引擎 (終極數據修復版)...")
    etf_dict = get_all_taiwan_etfs()
    all_tickers = list(etf_dict.keys())
    
    official_nav = get_official_nav()
    fundamentals = get_yahoo_fundamentals(all_tickers)
    
    market_db = []
    success_count = 0
    
    for idx, ticker in enumerate(all_tickers):
        if idx % 20 == 0: print(f"處理進度: {idx+1} / {len(all_tickers)} ...")
            
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
            
            # 淨值與折溢價計算 (防護除以 0 的錯誤)
            nav = official_nav.get(ticker)
            premium = ((current_price - nav) / nav) if nav and nav > 0 else None
            
            # 整合配息資訊 (雙重驗證：用歷史紀錄算出來的，跟 Yahoo 給的，哪個有值就用哪個)
            fund_info = fundamentals.get(ticker, {})
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
            success_count += 1
            time.sleep(random.uniform(0.5, 1.2))
        except Exception: continue

    ipo_db = [
        {"id": "00946", "name": "群益科技高息成長", "category": "高股息", "issueDate": "2026-05-09", "price": 10.0, "fee": 0.30, "freq": "月配", "topHoldings": "聯發科, 瑞昱, 聯詠", "news": []},
        {"id": "00947", "name": "台新臺灣IC設計", "category": "主題型", "issueDate": "2026-06-12", "price": 15.0, "fee": 0.40, "freq": "季配", "topHoldings": "台積電, 聯發科, 瑞昱", "news": []}
    ]

    with open("market_data.json", "w", encoding="utf-8") as f:
        json.dump({"ipo": ipo_db, "main": market_db}, f, ensure_ascii=False, indent=2)
        
    print(f"量化運算完成！共寫入 {success_count} 檔有效數據。")

if __name__ == "__main__":
    main()
