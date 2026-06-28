import pandas as pd
import json
import cloudscraper
import time
import random
import requests

# 建立繞過防火牆的偽裝 Session
scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})

def get_all_taiwan_etfs():
    tickers = []
    # 使用普通 requests，避免被政府網站誤判為攻擊腳本
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    
    print("開始取得證交所(上市) ETF清單...")
    try:
        resp = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            for item in data:
                if str(item.get('Code', '')).startswith('00'):
                    tickers.append(f"{item['Code']}.TW")
        else:
            print(f"上市 API 阻擋，狀態碼: {resp.status_code}")
    except Exception as e:
        print("上市清單取得失敗 (證交所封鎖海外 IP)")

    print("開始取得櫃買中心(上櫃) ETF清單...")
    try:
        resp = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            for item in data:
                code = str(item.get('SecuritiesCompanyCode', ''))
                if code.startswith('00'):
                    tickers.append(f"{code}.TWO")
        else:
             print(f"上櫃 API 阻擋，狀態碼: {resp.status_code}")
    except Exception as e:
        print("上櫃清單取得失敗")
        
    # 若政府 API 雙雙封鎖，啟用全市場代號生成器備案
    if not tickers:
        print("政府開放資料皆無法連線，啟用全市場代號生成器(備案)...")
        tickers = [f"00{str(i).zfill(3)}.TW" for i in range(50, 999)]
        
    return list(set(tickers))

def fetch_yahoo_data(ticker):
    """完全棄用 yfinance，手動打造 Yahoo API 直連解析器"""
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}?range=1y&interval=1d"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json"
    }
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
             
        # 若無成交量資料，補 0 避免運算報錯
        if not volumes:
            volumes = [0] * len(close_prices)
        
        df = pd.DataFrame({'Close': close_prices, 'Volume': volumes})
        df = df.dropna()
        return df
    except Exception as e:
        return pd.DataFrame() # 發生任何錯誤都安靜回傳空資料表，絕不死機

def main():
    print("啟動全市場 ETF 掃描引擎 (終極防禦版)...")
    all_tickers = get_all_taiwan_etfs()
    print(f"總計 {len(all_tickers)} 檔潛在 ETF 代號準備運算。")
    
    market_db = []
    success_count = 0
    
    for idx, ticker in enumerate(all_tickers):
        # 每處理 20 檔回報一次進度
        if idx % 20 == 0:
            print(f"目前處理進度: 第 {idx+1} / {len(all_tickers)} 檔...")
            
        df = fetch_yahoo_data(ticker)
        
        if df.empty or len(df) < 20:
            continue
            
        try:
            current_price = float(df['Close'].iloc[-1])
            vol_20d = int(df['Volume'].tail(20).mean() / 1000) if not df['Volume'].empty else None
            
            # 量化指標計算
            if len(df) >= 200:
                cagr_1y = float((current_price - df['Close'].iloc[0]) / df['Close'].iloc[0])
                max_p = df['Close'].expanding().max()
                mdd = float(((df['Close'] - max_p) / max_p).min())
                daily_ret = df['Close'].pct_change().dropna()
                sharpe = float((daily_ret.mean() / daily_ret.std()) * (252**0.5)) if daily_ret.std() != 0 else 0
            else:
                cagr_1y, sharpe = None, None
                mdd = float(((df['Close'] - df['Close'].expanding().max()) / df['Close'].expanding().max()).min()) if len(df) > 0 else None

            market_db.append({
                "id": ticker.split('.')[0],
                "name": "台股 ETF", 
                "type": "市場標的",
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
                "news": [] 
            })
            success_count += 1
            
            # 關鍵防護：每次抓取後，隨機休息 0.5 ~ 1.5 秒，模仿人類操作避免被抓
            time.sleep(random.uniform(0.5, 1.5))
            
        except Exception as e:
            continue

    ipo_db = [
        {"id": "0095X", "name": "某投信全球AI趨勢", "issueDate": "2026-08-01", "price": 10.0, "fee": 0.60, "freq": "不配息", "topHoldings": "NVIDIA, MSFT", "news": []},
        {"id": "0096X", "name": "某投信台灣價值", "issueDate": "2026-08-15", "price": 15.0, "fee": 0.35, "freq": "月配", "topHoldings": "長榮, 聯發科", "news": []}
    ]

    final_data = {"ipo": ipo_db, "main": market_db}
    
    with open("market_data.json", "w", encoding="utf-8") as f:
        json.dump(final_data, f, ensure_ascii=False, indent=2)
        
    print(f"量化運算完成！共成功解析並寫入 {success_count} 檔 ETF 數據。")

if __name__ == "__main__":
    main()
