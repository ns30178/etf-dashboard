import os
import requests
import pandas as pd
import json
import time
import math
from datetime import datetime, timedelta

FINMIND_TOKEN = os.environ.get("FINMIND_TOKEN", "")
FUGLE_KEY = os.environ.get("FUGLE_API_KEY", "")

FILE_MAP = {
    "高股息": "data_high_div.json", "市值型": "data_market_cap.json",
    "主題型": "data_theme.json", "債券型": "data_bond.json",
    "槓桿型": "data_leverage.json", "反向型": "data_inverse.json",
    "綜合/其他": "data_other.json"
}

def sanitize_json(val):
    if isinstance(val, dict):
        return {k: sanitize_json(v) for k, v in val.items()}
    elif isinstance(val, list):
        return [sanitize_json(v) for v in val]
    elif isinstance(val, float):
        if math.isnan(val) or math.isinf(val):
            return None
    return val

def categorize_etf(name):
    if any(k in name for k in ['正2', '正達', '倍']): return "槓桿型"
    if any(k in name for k in ['反1', '反向']): return "反向型"
    if any(k in name for k in ['高息', '高股息', '優息', '股息', '息收']): return "高股息"
    if any(k in name for k in ['債', '國債', '投等', '金融債', '公司債']): return "債券型"
    if any(k in name for k in ['半導體', '電動車', '5G', 'AI', '科技', '生技', '不動產', '綠能', '網購', '主題', '電競']): return "主題型"
    if any(k in name for k in ['50', '100', '市值', '加權', '大盤', '摩台', 'MSCI', '中型', '富時']): return "市值型"
    return "綜合/其他"

def fetch_etf_list():
    tickers = {}
    url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInfo&token={FINMIND_TOKEN}"
    try:
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            for item in res.json().get('data', []):
                if item.get('industry_category') == 'ETF':
                    tickers[str(item.get('stock_id'))] = str(item.get('stock_name'))
    except:
        pass
    
    if not tickers:
        core_etfs = ["0050", "0056", "00878", "00929", "00919", "00713", "00915", "00939", "00940", "006208"]
        for t in core_etfs: tickers[t] = "ETF"
    return tickers

def fetch_fugle_candles(symbol):
    url = f"https://api.fugle.tw/marketdata/v1.0/stock/historical/candles/{symbol}?timeframe=D"
    headers = {"X-API-KEY": FUGLE_KEY}
    try:
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json().get('data', [])
            if data:
                df = pd.DataFrame(data)
                df['date'] = pd.to_datetime(df['date'])
                df.set_index('date', inplace=True)
                df.sort_index(inplace=True)
                return df
    except:
        pass
    return pd.DataFrame()

def fetch_finmind_dividend(symbol):
    start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
    url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockDividend&data_id={symbol}&start_date={start_date}&token={FINMIND_TOKEN}"
    try:
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            return res.json().get('data', [])
    except:
        pass
    return []

def main():
    tickers = fetch_etf_list()
    db = {cat: [] for cat in FILE_MAP.keys()}
    search_index = []

    print(f"啟動 API 商業端點獲取程序，共計 {len(tickers)} 檔...")

    for idx, (ticker, name) in enumerate(tickers.items()):
        try:
            # 1. 股價與技術指標 (Fugle)
            hist = fetch_fugle_candles(ticker)
            if hist.empty or len(hist) < 20: continue

            current_price = float(hist['close'].iloc[-1])
            vol_20d = int(hist['volume'].tail(20).mean() / 1000)

            if len(hist) >= 200:
                cagr_1y = float((current_price - hist['close'].iloc[0]) / hist['close'].iloc[0])
                max_p = hist['close'].cummax()
                mdd = float(((hist['close'] - max_p) / max_p).min())
                daily_ret = hist['close'].pct_change().dropna()
                sharpe = float((daily_ret.mean() / daily_ret.std()) * (252**0.5)) if daily_ret.std() != 0 else None
            else:
                cagr_1y, sharpe, mdd = None, None, None

            # 2. 配息資訊 (FinMind)
            div_data = fetch_finmind_dividend(ticker)
            ttm_div = 0.0
            next_div_date = None
            next_div_amount = None
            
            now = datetime.now()
            for record in div_data:
                ex_date = datetime.strptime(record.get('DividendYieldDate', '1900-01-01'), '%Y-%m-%d')
                amt = float(record.get('CashEarningsDistribution', 0) or 0)
                if ex_date <= now:
                    ttm_div += amt
                elif ex_date > now:
                    next_div_date = ex_date.strftime('%Y-%m-%d')
                    next_div_amount = amt

            yield_ttm = (ttm_div / current_price) if (ttm_div and current_price) else None
            dividend_rate = ttm_div if ttm_div > 0 else None

            # 資料庫匯整
            category = categorize_etf(name)
            
            db[category].append({
                "id": ticker, "name": name, "price": current_price, "premium": None,
                "aum": None, "cagr_1y": cagr_1y, "sharpe": sharpe, "mdd": mdd, "vol_20d": vol_20d,
                "yield_ttm": yield_ttm, "dividend_rate": dividend_rate,
                "next_div_date": next_div_date, "next_div_amount": next_div_amount
            })
            
            # 建構全市場搜尋索引
            search_index.append({
                "id": ticker,
                "name": name,
                "category": category
            })
            
        except Exception:
            pass
            
        # 符合 Fugle 60 requests/minute 限制
        time.sleep(1.2)

    # 輸出過濾資料庫
    for cat, data in db.items():
        clean_data = sanitize_json(data)
        with open(FILE_MAP[cat], "w", encoding="utf-8") as f:
            json.dump(clean_data, f, ensure_ascii=False, indent=2)

    # 輸出全市場搜尋索引
    with open("search_index.json", "w", encoding="utf-8") as f:
        json.dump(search_index, f, ensure_ascii=False, indent=2)

    # 靜態 IPO 檔案寫入
    ipo_db = [
        {"id": "00946", "name": "群益科技高息成長", "issueDate": "2026-05-09", "price": 10.0, "fee": 0.30, "freq": "月配", "topHoldings": "聯發科, 瑞昱, 聯詠"},
        {"id": "00947", "name": "台新臺灣IC設計", "issueDate": "2026-06-12", "price": 15.0, "fee": 0.40, "freq": "季配", "topHoldings": "台積電, 聯發科, 瑞昱"}
    ]
    with open("data_ipo.json", "w", encoding="utf-8") as f:
        json.dump(ipo_db, f, ensure_ascii=False, indent=2)
    with open("data_news.json", "w", encoding="utf-8") as f:
        json.dump({}, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
