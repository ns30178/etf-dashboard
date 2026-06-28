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

def main():
    # 這裡我們遍歷常見的 0050-00999 代號，yfinance 會自動過濾掉無效代號
    etf_codes = [f"{str(i).zfill(4)}.TW" for i in range(50, 999)]
    db = {cat: [] for cat in FILE_MAP.keys()}
    
    print(f"🚀 啟動 yfinance 量化引擎，正在掃描 ETF...")

    for code in etf_codes:
        try:
            ticker = yf.Ticker(code)
            info = ticker.info
            
            # 確保有基礎價格，排除無效代號
            price = info.get('currentPrice') or info.get('regularMarketPrice')
            if not price: continue
            
            name = info.get('shortName', '未知名稱')
            
            # 計算規模 (AUM)
            aum = info.get('totalAssets') or info.get('marketCap')
            aum = round(aum / 100000000, 2) if aum else None
            
            # 計算淨值與折溢價
            nav = info.get('navPrice')
            premium = ((price - nav) / nav) if (nav and nav > 0) else None
            
            # 配息資訊
            dividend_rate = info.get('trailingAnnualDividendRate')
            yield_ttm = info.get('trailingAnnualDividendYield')
            
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
            
            print(f"✅ {code} 已解析")
            time.sleep(0.3)
            
        except Exception as e:
            continue

    for cat, data in db.items():
        with open(FILE_MAP[cat], "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    print("🎉 資料更新完成")

if __name__ == "__main__":
    main()
