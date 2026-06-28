import yfinance as yf
import pandas as pd
import json
import requests
from datetime import datetime

def get_all_taiwan_etfs():
    """自動從證交所與櫃買中心開放資料取得全市場 ETF 代號"""
    tickers = []
    
    # 1. 抓取上市 ETF (證交所 Open API)
    try:
        twse_url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
        twse_resp = requests.get(twse_url, timeout=10).json()
        for item in twse_resp:
            if str(item.get('Code', '')).startswith('00'):
                tickers.append(f"{item['Code']}.TW")
    except Exception as e:
        print(f"上市 ETF 取得失敗: {e}")

    # 2. 抓取上櫃 ETF (櫃買中心 Open API)
    try:
        tpex_url = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"
        tpex_resp = requests.get(tpex_url, timeout=10).json()
        for item in tpex_resp:
            code = str(item.get('SecuritiesCompanyCode', ''))
            if code.startswith('00'):
                tickers.append(f"{code}.TWO")
    except Exception as e:
        print(f"上櫃 ETF 取得失敗: {e}")

    # 若政府 API 雙雙異常，啟用備用常規清單產生器
    if not tickers:
        print("警告: 無法連線政府開放資料，啟用備用代號產生器。")
        tickers = [f"00{str(i).zfill(3)}.TW" for i in range(50, 999)]
        
    return list(set(tickers))

def main():
    print("啟動全市場 ETF 掃描引擎...")
    all_tickers = get_all_taiwan_etfs()
    print(f"成功取得 {len(all_tickers)} 檔潛在 ETF 代號，準備進行量化運算。")
    
    market_db = []
    
    # 使用批次下載 (Batch Download) 大幅提升速度並降低被封鎖機率
    batch_size = 50
    for i in range(0, len(all_tickers), batch_size):
        batch = all_tickers[i:i+batch_size]
        print(f"處理進度: {i+1} ~ {i+len(batch)} 檔...")
        
        try:
            # yfinance 批次抓取 1 年歷史資料
            data = yf.download(batch, period="1y", group_by="ticker", progress=False)
            
            for ticker in batch:
                try:
                    # 處理單一檔或多檔的回傳格式差異
                    df = data if len(batch) == 1 else data[ticker]
                    
                    if df.empty or len(df) < 20:
                        continue
                        
                    prices = df['Close'].dropna()
                    if len(prices) == 0:
                        continue
                        
                    current_price = float(prices.iloc[-1])
                    vol_20d = int(df['Volume'].dropna().tail(20).mean() / 1000) if 'Volume' in df.columns else None
                    
                    # 量化指標計算
                    if len(prices) >= 200: # 約略一年的交易日
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
                        "name": f"台股 ETF", # 靜態名稱，後續可擴充對照表
                        "type": "台股標的",
                        "price": current_price,
                        "cagr_1y": cagr_1y,
                        "sharpe": sharpe,
                        "mdd": mdd,
                        "yield_ttm": None, # 需另接除權息 API
                        "premium": None,   # 需另接即時折溢價 API
                        "fee": None,
                        "aum": None,
                        "vol_20d": vol_20d,
                        "dividend": None,
                        "news": [] # 全市場同時抓新聞會被鎖 IP，暫留空陣列
                    })
                except Exception as e:
                    continue
        except Exception as e:
            print(f"批次下載失敗: {e}")
            continue

    # 模擬即將上市 (IPO) 區塊資料
    ipo_db = [
        {"id": "0095X", "name": "某投信全球AI趨勢", "issueDate": "2026-08-01", "price": 10.0, "fee": 0.60, "freq": "不配息", "topHoldings": "NVIDIA, MSFT", "news": []},
        {"id": "0096X", "name": "某投信台灣價值", "issueDate": "2026-08-15", "price": 15.0, "fee": 0.35, "freq": "月配", "topHoldings": "長榮, 聯發科", "news": []}
    ]

    final_data = {"ipo": ipo_db, "main": market_db}
    
    with open("market_data.json", "w", encoding="utf-8") as f:
        json.dump(final_data, f, ensure_ascii=False, indent=2)
        
    print(f"量化運算完成！共成功解析 {len(market_db)} 檔 ETF，已寫入 market_data.json。")

if __name__ == "__main__":
    main()
