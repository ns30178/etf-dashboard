import yfinance as yf
import requests
import pandas as pd
import json
import time
import math
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

FILE_MAP = {
    "高股息": "data_high_div.json", "市值型": "data_market_cap.json",
    "主題型": "data_theme.json", "債券型": "data_bond.json",
    "槓桿型": "data_leverage.json", "反向型": "data_inverse.json",
    "綜合/其他": "data_other.json"
}

def sanitize_json(val):
    """遞迴過濾器：將 NaN、Inf 轉為 None，確保 JSON 格式安全"""
    if isinstance(val, dict):
        return {k: sanitize_json(v) for k, v in val.items()}
    elif isinstance(val, list):
        return [sanitize_json(v) for v in val]
    elif isinstance(val, float):
        if math.isnan(val) or math.isinf(val):
            return None
    return val

def categorize_etf(name):
    if any(k in name for k in ['正2', '正達', '倍']):
        return "槓桿型"
    if any(k in name for k in ['反1', '反向']):
        return "反向型"
    if any(k in name for k in ['高息', '高股息', '優息', '股息', '息收']):
        return "高股息"
    if any(k in name for k in ['債', '國債', '投等', '金融債', '公司債']):
        return "債券型"
    if any(k in name for k in ['半導體', '電動車', '5G', 'AI', '科技', '生技', '不動產', '綠能', '網購', '主題', '電競']):
        return "主題型"
    if any(k in name for k in ['50', '100', '市值', '加權', '大盤', '摩台', 'MSCI', '中型', '富時']):
        return "市值型"
    return "綜合/其他"

def fetch_official_data():
    """獲取台股全市場 ETF 名單與 OpenAPI 官方淨值"""
    tickers = {}
    nav_dict = {}
    headers = {"User-Agent": "Mozilla/5.0"}
    
    # 1. 取得名單 (TWSE)
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", headers=headers, timeout=10)
        if res.status_code == 200:
            for item in res.json():
                if str(item.get('Code', '')).startswith('00'):
                    tickers[f"{item['Code']}.TW"] = item.get('Name', '')
    except Exception as e:
        print(f"取得 TWSE 名單失敗: {e}")

    # 1. 取得名單 (TPEX)
    try:
        res = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", headers=headers, timeout=10)
        if res.status_code == 200:
            for item in res.json():
                code = str(item.get('SecuritiesCompanyCode', ''))
                if code.startswith('00'):
                    tickers[f"{code}.TWO"] = item.get('CompanyName', '')
    except Exception as e:
        print(f"取得 TPEX 名單失敗: {e}")

    # 2. 取得官方淨值 (TWSE)
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/MI_101", headers=headers, timeout=10)
        if res.status_code == 200:
            for item in res.json():
                try:
                    nav_dict[f"{item.get('Code')}.TW"] = float(str(item.get('Nav', '0')).replace(',', ''))
                except Exception:
                    pass
    except Exception as e:
        print(f"取得 TWSE 淨值失敗: {e}")

    # 2. 取得官方淨值 (TPEX)
    try:
        res = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_etf_nav", headers=headers, timeout=10)
        if res.status_code == 200:
            for item in res.json():
                try:
                    nav_dict[f"{item.get('SecuritiesCompanyCode')}.TWO"] = float(str(item.get('Nav', '0')).replace(',', ''))
                except Exception:
                    pass
    except Exception as e:
        print(f"取得 TPEX 淨值失敗: {e}")

    return tickers, nav_dict

def process_etf(ticker, name, nav_dict):
    """單一標的處理邏輯（供非同步呼叫）"""
    try:
        tk = yf.Ticker(ticker)
        info = tk.info
        hist = tk.history(period="1y")

        if hist.empty or len(hist) < 20:
            return None

        # 價格與技術指標
        current_price = float(hist['Close'].iloc[-1])
        vol_20d = int(hist['Volume'].tail(20).mean() / 1000)

        if len(hist) >= 200:
            cagr_1y = float((current_price - hist['Close'].iloc[0]) / hist['Close'].iloc[0])
            max_p = hist['Close'].cummax()
            mdd = float(((hist['Close'] - max_p) / max_p).min())
            daily_ret = hist['Close'].pct_change().dropna()
            sharpe = float((daily_ret.mean() / daily_ret.std()) * (252**0.5)) if daily_ret.std() != 0 else None
        else:
            cagr_1y = None
            sharpe = None
            mdd = None

        # 規模 (AUM) 備援計算
        aum = info.get('totalAssets') or info.get('marketCap')
        if aum:
            aum = aum / 100000000
        else:
            shares = info.get('sharesOutstanding')
            if shares and current_price:
                aum = (shares * current_price) / 100000000
        
        # 折溢價計算
        nav = nav_dict.get(ticker)
        premium = None
        if nav and nav > 0:
            premium = (current_price - nav) / nav

        # 除錯日誌
        if aum is None:
            print(f"[日誌] {ticker} 缺失規模(AUM)原始資料")
        if nav is None:
            print(f"[日誌] {ticker} 缺失淨值(NAV)原始資料: OpenAPI 無回傳此代號")

        # 實質配息與未來已公告配息
        yield_ttm = info.get('trailingAnnualDividendYield')
        dividend_rate = info.get('trailingAnnualDividendRate')
        
        next_div_date = None
        next_div_amount = None
        
        ex_div_ts = info.get('exDividendDate')
        if ex_div_ts is not None:
            # 判斷是否為未來日期 (大於等於今日)
            if ex_div_ts >= datetime.now().timestamp():
                next_div_date = datetime.fromtimestamp(ex_div_ts).strftime('%Y-%m-%d')
                # 以 dividendRate 或 lastDividendValue 作為已公告發放額
                next_div_amount = info.get('dividendRate') or info.get('lastDividendValue')

        category = categorize_etf(name)
        
        return category, {
            "id": ticker.split('.')[0],
            "name": name,
            "price": current_price,
            "premium": premium,
            "aum": aum,
            "cagr_1y": cagr_1y,
            "sharpe": sharpe,
            "mdd": mdd,
            "vol_20d": vol_20d,
            "yield_ttm": yield_ttm,
            "dividend_rate": dividend_rate,
            "next_div_date": next_div_date,
            "next_div_amount": next_div_amount
        }
    except Exception as e:
        print(f"[錯誤] 解析 {ticker} 時發生異常跳過: {e}")
        return None

def main():
    tickers, nav_dict = fetch_official_data()
    print(f"啟動併發爬蟲，共獲取 {len(tickers)} 檔清單...")

    db = {cat: [] for cat in FILE_MAP.keys()}

    # 啟用併發請求壓縮爬取時間
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(process_etf, t, name, nav_dict): t for t, name in tickers.items()}
        
        for future in as_completed(futures):
            result = future.result()
            if result:
                cat, data = result
                db[cat].append(data)

    # 清洗資料並寫入 JSON
    for cat, data in db.items():
        clean_data = sanitize_json(data)
        with open(FILE_MAP[cat], "w", encoding="utf-8") as f:
            json.dump(clean_data, f, ensure_ascii=False, indent=2)

    print("資料庫更新與過濾完成。")

if __name__ == "__main__":
    main()
