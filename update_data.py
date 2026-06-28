import pandas as pd
import json
import cloudscraper
import time
import random
from bs4 import BeautifulSoup
from datetime import datetime

# 建立繞過防火牆的偽裝 Session
scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})

def get_all_taiwan_etfs():
    """自動取得全市場 ETF，若遭阻擋則強制啟動備案"""
    tickers = set()
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    
    print("開始取得證交所(上市) ETF清單...")
    try:
        resp = scraper.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", headers=headers, timeout=10)
        if resp.status_code == 200:
            for item in resp.json():
                if str(item.get('Code', '')).startswith('00'):
                    tickers.add(f"{item['Code']}.TW")
        else:
            raise Exception("TWSE API Blocked")
    except Exception as e:
        print("上市 API 遭阻擋，啟動上市代號強制生成備案 (0050~00999)...")
        # 只要被擋，強制把所有可能的上市 ETF 代號塞進去，寧可錯殺不可放過
        for i in range(50, 1000):
            tickers.add(f"00{str(i).zfill(3)}.TW")

    print("開始取得櫃買中心(上櫃) ETF清單...")
    try:
        resp = scraper.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", headers=headers, timeout=10)
        if resp.status_code == 200:
            for item in resp.json():
                code = str(item.get('SecuritiesCompanyCode', ''))
                if code.startswith('00'):
                    tickers.add(f"{code}.TWO")
    except Exception as e:
        print("上櫃清單取得失敗")
        
    return list(tickers)

def get_etf_names(tickers):
    """批次向 Yahoo API 查詢真實的 ETF 中文名稱"""
    print("開始取得 ETF 真實中文名稱...")
    names = {}
    for i in range(0, len(tickers), 50):
        batch = tickers[i:i+50]
        symbols_str = ",".join(batch)
        url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={symbols_str}"
        try:
            resp = scraper.get(url, timeout=10)
            res = resp.json().get('quoteResponse', {}).get('result', [])
            for item in res:
                sym = item.get('symbol')
                # 優先使用長檔名，若無則用短檔名
                name = item.get('longName') or item.get('shortName') or "台股 ETF"
                names[sym] = name
        except Exception:
            pass
        time.sleep(1)
    return names

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
            # 過濾出真實的新聞連結
            if len(title) > 10 and 'http' in href and ('news' in href or 'article' in href):
                news_items.append({"title": title, "link": href, "date": datetime.today().strftime('%Y-%m-%d')})
            if len(news_items) >= 3:
                break
        return news_items
    except Exception:
        return []

def fetch_yahoo_data(ticker):
    """直連 Yahoo 獲取股價與交易量"""
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
        
        if not close_prices:
             return pd.DataFrame()
             
        if not volumes:
            volumes = [0] * len(close_prices)
        
        df = pd.DataFrame({'Close': close_prices, 'Volume': volumes}).dropna()
        return df
    except Exception:
        return pd.DataFrame()

def main():
    print("啟動全市場 ETF 掃描引擎 (完整修復版)...")
    all_tickers = get_all_taiwan_etfs()
    print(f"總計 {len(all_tickers)} 檔潛在代號準備掃描。")
    
    # 取得真實中文名稱
    etf_names = get_etf_names(all_tickers)
    
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
            
            # 計算量化指標
            if len(df) >= 200:
                cagr_1y = float((current_price - df['Close'].iloc[0]) / df['Close'].iloc[0])
                max_p = df['Close'].expanding().max()
                mdd = float(((df['Close'] - max_p) / max_p).min())
                daily_ret = df['Close'].pct_change().dropna()
                sharpe = float((daily_ret.mean() / daily_ret.std()) * (252**0.5)) if daily_ret.std() != 0 else 0
            else:
                cagr_1y, sharpe = None, None
                mdd = float(((df['Close'] - df['Close'].expanding().max()) / df['Close'].expanding().max()).min())

            # 只有日均量大於 100 張的熱門標的才去抓新聞，避免被 Yahoo 封鎖
            news_data = fetch_yahoo_news(ticker) if vol_20d > 100 else []

            market_db.append({
                "id": ticker.split('.')[0],
                "name": etf_names.get(ticker, "台股 ETF"), 
                "type": "台股 ETF",
                "price": current_price,
                "cagr_1y": cagr_1y,
                "sharpe": sharpe,
                "mdd": mdd,
                "yield_ttm": None, 
                "premium": None,  
                "fee": None,
                "aum": None,
                "vol_20d": vol_20d,
                "dividend": None,
                "news": news_data
            })
            success_count += 1
            time.sleep(random.uniform(0.5, 1.2))
            
        except Exception:
            continue

    # =====================================================================
    # 【注意】IPO 區塊的 ETF 尚未上市，無法用 API 抓取！必須手動維護！
    # 只要你有新的 IPO 想追蹤，請按照下方的格式新增到這個 ipo_db 陣列中。
    # =====================================================================
    ipo_db = [
        {"id": "00946", "name": "群益科技高息成長", "issueDate": "2026-05-09", "price": 10.0, "fee": 0.30, "freq": "月配", "topHoldings": "聯發科, 瑞昱, 聯詠", "news": [{"title": "00946 掛牌首日買氣旺", "link": "#", "date": "2026-05-09"}]},
        {"id": "00947", "name": "台新臺灣IC設計", "issueDate": "2026-06-12", "price": 15.0, "fee": 0.40, "freq": "季配", "topHoldings": "台積電, 聯發科, 瑞昱", "news": []}
    ]

    final_data = {"ipo": ipo_db, "main": market_db}
    
    with open("market_data.json", "w", encoding="utf-8") as f:
        json.dump(final_data, f, ensure_ascii=False, indent=2)
        
    print(f"量化運算完成！共成功解析並寫入 {success_count} 檔真實 ETF 數據。")

if __name__ == "__main__":
    main()
