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
    if any(k in name for k in ['不動產', 'REITs']): return "REITs"
    return "綜合/其他"

def get_all_taiwan_etfs():
    """從官方 API 提取代號與正確中文名稱"""
    tickers = {}
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    print("取得證交所(上市)代號與中文名稱...")
    try:
        resp = scraper.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", headers=headers, timeout=10)
        if resp.status_code == 200:
            for item in resp.json():
                if str(item.get('Code', '')).startswith('00'):
                    tickers[f"{item['Code']}.TW"] = item.get('Name', '未知名稱')
    except Exception:
        print("上市 API 阻擋，名稱將不完整")

    print("取得櫃買中心(上櫃)代號與中文名稱...")
    try:
        resp = scraper.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", headers=headers, timeout=10)
        if resp.status_code == 200:
            for item in resp.json():
                code = str(item.get('SecuritiesCompanyCode', ''))
                if code.startswith('00'):
                    tickers[f"{code}.TWO"] = item.get('CompanyName', '未知名稱')
    except Exception:
        print("上櫃 API 阻擋，名稱將不完整")
        
    return tickers

def get_yahoo_fundamentals(tickers_list):
    """批次取得規模、淨值、殖利率與配息數據"""
    print("抓取基本面、折溢價與配息資料...")
    fundamentals = {}
    for i in range(0, len(tickers_list), 40):
        batch = tickers_list[i:i+40]
        url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={','.join(batch)}"
        try:
            resp = scraper.get(url, timeout=10)
            res = resp.json().get('quoteResponse', {}).get('result', [])
            for item in res:
                sym = item.get('symbol')
                
                # 擴大抓取欄位以涵蓋規模與淨值
                y_ttm = item.get('trailingAnnualDividendYield') or item.get('dividendYield')
                d_rate = item.get('trailingAnnualDividendRate') or item.get('dividendRate')
                aum_raw = item.get('marketCap')
                nav = item.get('navPrice')

                fundamentals[sym] = {
                    'yield': y_ttm,
                    'dividend_rate': d_rate,
                    'aum': round(aum_raw / 100000000, 2) if aum_raw else None, # 轉換為億
                    'nav': nav
                }
        except Exception:
            pass
        time.sleep(1)
    return fundamentals

def fetch_yahoo_news(ticker):
    """抓取單檔 ETF 的最新財經新聞"""
    url = f"https://tw.stock.yahoo.com/quote/{ticker}/news"
    try:
        resp = scraper.get(url, timeout=5)
        soup = BeautifulSoup(resp.text, 'html.parser')
        news_items = []
        for a in soup.find_all('a', href=True):
            title = a.text.strip()
            href = a['href']
            if len(title) > 10 and 'http' in href and ('news' in href or 'article' in href):
                news_items.append({"title": title, "link": href, "date": datetime.today().strftime('%Y-%m-%d')})
            if len(news_items) >= 3:
                break
        return news_items
    except Exception:
        return []

def fetch_yahoo_data(ticker):
    """直連 Yahoo 獲取股價與交易量歷史"""
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}?range=1y&interval=1d"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        resp = scraper.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return pd.DataFrame()
        
        data = resp.json()
        if not data.get('chart', {}).get('result'):
            return pd.DataFrame()
            
        result = data['chart']['result'][0]
        if 'timestamp' not in result or 'quote' not in result['indicators']:
            return pd.DataFrame()
            
        close_prices = result['indicators']['quote'][0].get('close')
        volumes = result['indicators']['quote'][0].get('volume')
        
        if not close_prices: return pd.DataFrame()
        if not volumes: volumes = [0] * len(close_prices)
        
        df = pd.DataFrame({'Close': close_prices, 'Volume': volumes}).dropna()
        return df
    except Exception:
        return pd.DataFrame()

def main():
    print("啟動全市場 ETF 掃描引擎...")
    etf_dict = get_all_taiwan_etfs()
    all_tickers = list(etf_dict.keys())
    print(f"總計 {len(all_tickers)} 檔潛在代號準備掃描。")
    
    fundamentals = get_yahoo_fundamentals(all_tickers)
    
    market_db = []
    success_count = 0
    
    for idx, ticker in enumerate(all_tickers):
        if idx % 20 == 0:
            print(f"處理進度: {idx+1} / {len(all_tickers)} ...")
            
        df = fetch_yahoo_data(ticker)
        
        if df.empty or len(df) < 20:
            continue
            
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

            news_data = fetch_yahoo_news(ticker) if vol_20d > 100 else []
            name = etf_dict.get(ticker, "未知名稱")
            category = categorize_etf(name)
            fund_data = fundamentals.get(ticker, {})

            # 計算折溢價
            nav = fund_data.get('nav')
            premium = None
            if nav and current_price and nav > 0:
                premium = (current_price - nav) / nav

            market_db.append({
                "id": ticker.split('.')[0],
                "name": name, 
                "category": category,
                "price": current_price,
                "premium": premium,
                "aum": fund_data.get('aum'),
                "cagr_1y": cagr_1y,
                "sharpe": sharpe,
                "mdd": mdd,
                "yield_ttm": fund_data.get('yield'), 
                "dividend_rate": fund_data.get('dividend_rate'),
                "vol_20d": vol_20d,
                "news": news_data
            })
            success_count += 1
            time.sleep(random.uniform(0.5, 1.2))
            
        except Exception:
            continue

    ipo_db = [
        {"id": "00946", "name": "群益科技高息成長", "category": "高股息", "issueDate": "2026-05-09", "price": 10.0, "fee": 0.30, "freq": "月配", "topHoldings": "聯發科, 瑞昱, 聯詠", "news": []},
        {"id": "00947", "name": "台新臺灣IC設計", "category": "主題型", "issueDate": "2026-06-12", "price": 15.0, "fee": 0.40, "freq": "季配", "topHoldings": "台積電, 聯發科, 瑞昱", "news": []}
    ]

    final_data = {"ipo": ipo_db, "main": market_db}
    
    with open("market_data.json", "w", encoding="utf-8") as f:
        json.dump(final_data, f, ensure_ascii=False, indent=2)
        
    print(f"量化運算完成！共寫入 {success_count} 檔有效數據。")

if __name__ == "__main__":
    main()
