import yfinance as yf
import pandas as pd
import json
import cloudscraper
import time
import random # 引入隨機模組

# 建立繞過防火牆的偽裝 Session
scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})

def get_all_taiwan_etfs():
    """自動取得全市場 ETF，並加入強勢備案機制"""
    tickers = []
    
    print("開始取得證交所(上市) ETF清單...")
    try:
        resp = scraper.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            for item in data:
                if str(item.get('Code', '')).startswith('00'):
                    tickers.append(f"{item['Code']}.TW")
    except Exception as e:
        print(f"上市清單取得失敗 ({e})，啟用備案機制...")
        tickers.extend([f"00{str(i).zfill(3)}.TW" for i in range(50, 999)])

    print("開始取得櫃買中心(上櫃) ETF清單...")
    try:
        resp = scraper.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            for item in data:
                code = str(item.get('SecuritiesCompanyCode', ''))
                if code.startswith('00'):
                    tickers.append(f"{code}.TWO")
    except Exception as e:
        print(f"上櫃清單取得失敗 ({e})")
        
    return list(set(tickers))

def main():
    print("啟動全市場 ETF 掃描引擎 (狙擊手循序模式)...")
    all_tickers = get_all_taiwan_etfs()
    print(f"總計 {len(all_tickers)} 檔潛在 ETF 代號準備運算。")
    
    market_db = []
    success_count = 0
    
    # 放棄批次下載，改為單檔循序處理以避開 Yahoo 封鎖
    for idx, ticker in enumerate(all_tickers):
        # 每 20 檔印出一次進度，避免 Log 太長
        if idx % 20 == 0:
            print(f"目前處理進度: 第 {idx+1} / {len(all_tickers)} 檔...")
            
        try:
            # 關鍵修正：threads=False 關閉多執行緒，確保 session 偽裝生效
            df = yf.download(ticker, period="1y", session=scraper, threads=False, progress=False)
            
            if df.empty or len(df) < 20:
                continue
                
            prices = df['Close'].dropna()
            volumes = df['Volume'].dropna() if 'Volume' in df.columns else pd.Series(dtype=float)
            
            if len(prices) == 0:
                continue
                
            current_price = float(prices.iloc[-1])
            vol_20d = int(volumes.tail(20).mean() / 1000) if not volumes.empty else None
            
            # 量化指標計算
            if len(prices) >= 200:
                cagr_1y = float((current_price - prices.iloc[0]) / prices.iloc[0])
                max_p = prices.expanding().max()
                mdd = float(((prices - max_p) / max_p).min())
                daily_ret = prices.pct_change().dropna()
                sharpe = float((daily_ret.mean() / daily_ret.std()) * (252**0.5)) if daily_ret.std() != 0 else 0
            else:
                cagr_1y, sharpe = None, None
                mdd = float(((prices - prices.expanding().max()) / prices.expanding().max()).min()) if len(prices) > 0 else None

            market_db.append({
                "id": ticker.split('.')[0],
                "name": "台股 ETF", 
                "type": "台股標的",
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
            
            # 關鍵防護：每次抓取後，隨機休息 0.3 ~ 1.2 秒，模仿人類操作
            time.sleep(random.uniform(0.3, 1.2))
            
        except Exception as e:
            # 單檔發生錯誤不中斷，直接跳下一檔
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
