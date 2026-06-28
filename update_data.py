import yfinance as yf
import pandas as pd
import json
import cloudscraper
import time

# 建立繞過防火牆的偽裝 Session
scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})

def get_all_taiwan_etfs():
    """自動取得全市場 ETF，並加入強勢備案機制"""
    tickers = []
    
    print("開始取得證交所(上市) ETF清單...")
    try:
        # 強制使用 scraper 偽裝請求，突破 TWSE 擋外國 IP 問題
        resp = scraper.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            for item in data:
                if str(item.get('Code', '')).startswith('00'):
                    tickers.append(f"{item['Code']}.TW")
        else:
            raise Exception(f"HTTP 狀態碼異常: {resp.status_code}")
    except Exception as e:
        print(f"上市清單取得失敗 ({e})，啟用強勢備案機制...")
        # 備案：若證交所死機或嚴格封鎖，強制生成所有可能的代號
        tickers.extend([f"00{str(i).zfill(3)}.TW" for i in range(50, 990)])

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
    print("啟動全市場 ETF 掃描引擎 (掛載 Cloudscraper 防禦)...")
    all_tickers = get_all_taiwan_etfs()
    print(f"總計 {len(all_tickers)} 檔潛在 ETF 代號準備運算。")
    
    market_db = []
    batch_size = 40 # 將批次縮小至 40，降低被 Yahoo 截斷的機率
    
    for i in range(0, len(all_tickers), batch_size):
        batch = all_tickers[i:i+batch_size]
        print(f"下載進度: 第 {i+1} ~ {i+len(batch)} 檔 ...")
        
        try:
            # 關鍵修正：將 scraper 強制注入 yfinance，繞過 Yahoo JSONDecodeError
            data = yf.download(batch, period="1y", session=scraper, progress=False)
            
            for ticker in batch:
                try:
                    # 處理 yfinance 多檔與單檔的回傳結構差異
                    if len(batch) == 1:
                        close_prices = data['Close']
                        volumes = data['Volume'] if 'Volume' in data.columns else pd.Series(dtype=float)
                    else:
                        if ticker not in data['Close'].columns:
                            continue
                        close_prices = data['Close'][ticker]
                        volumes = data['Volume'][ticker] if 'Volume' in data.columns else pd.Series(dtype=float)
                        
                    close_prices = close_prices.dropna()
                    if len(close_prices) < 20:
                        continue
                        
                    current_price = float(close_prices.iloc[-1])
                    vol_20d = int(volumes.dropna().tail(20).mean() / 1000) if not volumes.empty else None
                    
                    # 量化指標計算
                    if len(close_prices) >= 200:
                        cagr_1y = float((current_price - close_prices.iloc[0]) / close_prices.iloc[0])
                        max_p = close_prices.expanding().max()
                        mdd = float(((close_prices - max_p) / max_p).min())
                        daily_ret = close_prices.pct_change().dropna()
                        sharpe = float((daily_ret.mean() / daily_ret.std()) * (252**0.5)) if daily_ret.std() != 0 else 0
                    else:
                        cagr_1y, sharpe = None, None
                        mdd = float(((close_prices - close_prices.expanding().max()) / close_prices.expanding().max()).min())

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
                except Exception as e:
                    continue
            
            # 關鍵修正：加入強制延遲，避免連續發送請求被 Yahoo 封鎖
            time.sleep(2) 
            
        except Exception as e:
            print(f"此批次下載失敗: {e}")
            continue

    ipo_db = [
        {"id": "0095X", "name": "某投信全球AI趨勢", "issueDate": "2026-08-01", "price": 10.0, "fee": 0.60, "freq": "不配息", "topHoldings": "NVIDIA, MSFT", "news": []},
        {"id": "0096X", "name": "某投信台灣價值", "issueDate": "2026-08-15", "price": 15.0, "fee": 0.35, "freq": "月配", "topHoldings": "長榮, 聯發科", "news": []}
    ]

    final_data = {"ipo": ipo_db, "main": market_db}
    
    with open("market_data.json", "w", encoding="utf-8") as f:
        json.dump(final_data, f, ensure_ascii=False, indent=2)
        
    print(f"量化運算完成！共成功解析並寫入 {len(market_db)} 檔 ETF 數據。")

if __name__ == "__main__":
    main()
