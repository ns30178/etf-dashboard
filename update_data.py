import pandas as pd
import json
import cloudscraper
import time
import random
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

def safe_get(url, is_json=True):
    """帶有自動重試的請求模組，避免被短暫封鎖"""
    for _ in range(3):
        try:
            res = scraper.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            if res.status_code == 200:
                return res.json() if is_json else res.text
            elif res.status_code == 429:
                time.sleep(3) # 遇到阻擋先睡 3 秒
        except:
            time.sleep(2)
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
    print("👉 取得官方 ETF 代號名冊...")
    try:
        res = safe_get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL")
        for item in res:
            if str(item.get('Code', '')).startswith('00'):
                tickers[f"{item['Code']}.TW"] = item.get('Name', '未知名稱')
    except: pass
    try:
        res = safe_get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes")
        for item in res:
            code = str(item.get('SecuritiesCompanyCode', ''))
            if code.startswith('00'):
                tickers[f"{code}.TWO"] = item.get('CompanyName', '未知名稱')
    except: pass

    # 若政府 API 雙雙死機，強制寫入常規代號備案
    if not tickers:
        for i in range(50, 999):
            tickers[f"00{str(i).zfill(3)}.TW"] = "台股 ETF"
    return tickers

def get_official_nav():
    nav_dict = {}
    print("👉 同步官方每日淨值(NAV)...")
    
    # 1. 證交所主網 (修復：NAV 真實位置在陣列的 index 4)
    try:
        res = safe_get("https://www.twse.com.tw/rwd/zh/fund/MI_101?response=json")
        for row in res.get('data', []):
            try: nav_dict[f"{row[0]}.TW"] = float(str(row[4]).replace(',', ''))
            except: pass
    except: pass
    
    # 2. 櫃買中心主網 (修復：NAV 真實位置在陣列的 index 3)
    try:
        res = safe_get("https://www.tpex.org.tw/web/etf/g_info/fund_info_prb.php?l=zh-tw")
        for row in res.get('aaData', []):
            try: nav_dict[f"{row[0]}.TWO"] = float(str(row[3]).replace(',', ''))
            except: pass
    except: pass
    
    # 3. OpenAPI 備援
    if not nav_dict:
        try:
            res = safe_get("https://openapi.twse.com.tw/v1/exchangeReport/MI_101")
            for item in res:
                try: nav_dict[f"{item['Code']}.TW"] = float(str(item.get('Nav', '0')).replace(',', ''))
                except: pass
        except: pass
        try:
            res = safe_get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_etf_nav")
            for item in res:
                try: nav_dict[f"{item['SecuritiesCompanyCode']}.TWO"] = float(str(item.get('Nav', '0')).replace(',', ''))
                except: pass
        except: pass
    return nav_dict

def get_yahoo_batch_info(tickers):
    """【終極解法】用批次 API 一次取得 40 檔資料，徹底解決 AUM 的 429 錯誤"""
    print("👉 批次精算市場規模(AUM)與配息率...")
    batch_data = {}
    for i in range(0, len(tickers), 40):
        batch = tickers[i:i+40]
        url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={','.join(batch)}"
        res = safe_get(url)
        for item in res.get('quoteResponse', {}).get('result', []):
            sym = item.get('symbol')
            price = item.get('regularMarketPrice')
            shares = item.get('sharesOutstanding')
            mcap = item.get('marketCap')

            # 雙重邏輯強制精算億元規模
            aum = None
            if mcap and mcap > 0: aum = mcap / 100000000
            elif shares and price and shares > 0: aum = (shares * price) / 100000000

            batch_data[sym] = {
                'price': price,
                'aum': round(aum, 2) if aum else None,
                'yield': item.get('trailingAnnualDividendYield') or item.get('dividendYield'),
                'dividend_rate': item.get('trailingAnnualDividendRate') or item.get('dividendRate')
            }
        time.sleep(1) # 批次請求間依然稍作休息
    return batch_data

def fetch_yahoo_news(ticker):
    try:
        html = safe_get(f"https://tw.stock.yahoo.com/quote/{ticker}/news", is_json=False)
        soup = BeautifulSoup(html, 'html.parser')
        news = []
        for a in soup.find_all('a', href=True):
            title, href = a.text.strip(), a['href']
            if len(title) > 10 and 'http' in href and ('news' in href or 'article' in href):
                news.append({"title": title, "link": href, "date": datetime.today().strftime('%Y-%m-%d')})
            if len(news) >= 3: break
        return news
    except: return []

def fetch_historical_chart(ticker):
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}?range=1y&interval=1d&events=div"
    resp = safe_get(url)
    if not resp: return None, 0
    try:
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
    print(f"🚀 總計找到 {len(all_tickers)} 檔 ETF，啟動雙軌備援量化引擎...")
    
    official_nav = get_official_nav()
    batch_info = get_yahoo_batch_info(all_tickers)
    
    db = {cat: [] for cat in FILE_MAP.keys()}
    news_db = {}
    success_count = 0
    
    for idx, ticker in enumerate(all_tickers):
        if idx % 20 == 0: print(f"⚡ 運算進度: {idx+1} / {len(all_tickers)} 檔...")
            
        info = batch_info.get(ticker, {})
        current_price = info.get('price')
        
        df, div_total_chart = fetch_historical_chart(ticker)
        
        # 雙軌備援：就算圖表下載失敗，但只要有批次價格，依然產出基本面資料！
        if df is not None and not df.empty and len(df) >= 20:
            current_price = current_price or float(df['Close'].iloc[-1])
            vol_20d = int(df['Volume'].tail(20).mean() / 1000)
            
            if len(df) >= 200:
                cagr_1y = float((current_price - df['Close'].iloc[0]) / df['Close'].iloc[0])
                max_p = df['Close'].expanding().max()
                mdd = float(((df['Close'] - max_p) / max_p).min())
                daily_ret = df['Close'].pct_change().dropna()
                sharpe = float((daily_ret.mean() / daily_ret.std()) * (252**0.5)) if daily_ret.std() != 0 else 0
            else:
                cagr_1y, sharpe = None, None
                mdd = float(((df['Close'] - df['Close'].expanding().max()) / df['Close'].expanding().max()).min())
        else:
            if not current_price: continue # 如果連基礎報價都抓不到，才忍痛捨棄
            vol_20d = 0
            cagr_1y, sharpe, mdd = None, None, None

        name = etf_dict.get(ticker, "未知名稱")
        category = categorize_etf(name)
        
        # 結合精準官方淨值計算折溢價
        nav = official_nav.get(ticker)
        premium = ((current_price - nav) / nav) if nav and nav > 0 and current_price else None
        
        final_div_rate = div_total_chart if div_total_chart > 0 else info.get('dividend_rate')
        final_yield = (final_div_rate / current_price) if final_div_rate and current_price else info.get('yield')

        ticker_id = ticker.split('.')[0]
        
        if vol_20d > 100:
            news_db[ticker_id] = fetch_yahoo_news(ticker)

        db[category].append({
            "id": ticker_id, "name": name, "price": current_price,
            "premium": premium, "aum": info.get('aum'), "cagr_1y": cagr_1y,
            "sharpe": sharpe, "mdd": mdd, "yield_ttm": final_yield, 
            "dividend_rate": final_div_rate, "vol_20d": vol_20d
        })
        success_count += 1
        time.sleep(random.uniform(0.3, 0.7))

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
        
    print(f"🎉 任務全數完成！共成功導出 {success_count} 檔有效 ETF。")

if __name__ == "__main__":
    main()