import pandas as pd
import json
import time
import random
import yfinance as yf
from datetime import datetime

# 檔案映射表
FILE_MAP = {
    "高股息": "data_high_div.json", "市值型": "data_market_cap.json",
    "主題型": "data_theme.json", "債券型": "data_bond.json",
    "槓桿型": "data_leverage.json", "反向型": "data_inverse.json",
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

def get_etf_list():
    """模擬全市場 ETF 列表，這裡直接透過 yfinance 驗證"""
    # 這裡你可以維護一個基礎代號清單，或者是我們由 0050-00999 進行遍歷篩選
    # 為了穩定，我們先假設你有一份 ETF 代號基礎清單
    return [f"{str(i).zfill(4)}.TW" for i in range(50, 999)]

def main():
    etf_codes = get_etf_list()
    db = {cat: [] for cat in FILE_MAP.keys()}
    
    print(f"🚀 啟動 yfinance 量化引擎，預計掃描 {len(etf_codes)} 檔...")

    for code in etf_codes:
        try:
            ticker = yf.Ticker(code)
            info = ticker.info
            
            # 1. 基礎檢核 (確保不是空代號)
            if 'regularMarketPrice' not in info and 'currentPrice' not in info:
                continue
            
            price = info.get('currentPrice') or info.get('regularMarketPrice')
            name = info.get('shortName', '未知名稱')
            
            # 2. 核心邏輯：強制計算規模 (AUM) 與 淨值 (NAV)
            # AUM: 優先用 totalAssets，沒有則用 marketCap，再沒有用 sharesOutstanding * price
            aum = info.get('totalAssets') or info.get('marketCap')
            if aum: 
                aum = round(aum / 100000000, 2)
            elif info.get('sharesOutstanding'):
                aum = round((info.get('sharesOutstanding') * price) / 100000000, 2)
            
            # NAV: 取得淨值
            nav = info.get('navPrice')
            premium = ((price - nav) / nav) if (nav and nav > 0) else None
            
            # 3. 配息資訊
            dividend_rate = info.get('trailingAnnualDividendRate')
            yield_ttm = info.get('trailingAnnualDividendYield')
            
            # 寫入資料庫
            cat = categorize_etf(name)
            db[cat].append({
                "id": code.replace('.TW', ''),
                "name": name,
                "price": price,
                "premium": premium,
                "aum": aum,
                "yield_ttm": yield_ttm,
                "dividend_rate": dividend_rate
            })
            
            print(f"✅ {code} 已處理")
            time.sleep(0.5) # 避開 Rate Limit
            
        except Exception as e:
            continue

    # 輸出檔案
    for cat, data in db.items():
        with open(FILE_MAP[cat], "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    print("🎉 資料已全部更新")

if __name__ == "__main__":
    main()
