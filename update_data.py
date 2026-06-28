import yfinance as yf
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import pandas as pd
import json
import time
import math
import random
from datetime import datetime

FILE_MAP = {
    "高股息": "data_high_div.json", "市值型": "data_market_cap.json",
    "主題型": "data_theme.json", "債券型": "data_bond.json",
    "槓桿型": "data_leverage.json", "反向型": "data_inverse.json",
    "綜合/其他": "data_other.json"
}

def get_robust_session():
    """建立附帶自動重試機制的連線池，設定嚴格逾時，防止無限期掛起"""
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    return session

def sanitize_json(val):
    """遞迴過濾器：將 NaN、Inf 轉為 None，確保 JSON 寫入 null"""
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

def fetch_official_data():
    """獲取官方清單與淨值，若失敗則啟用暴力備援名單"""
    tickers = {}
    nav_dict = {}
    headers = {"User-Agent": "Mozilla/5.0"}
    
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", headers=headers, timeout=5)
        for item in res.json():
            if str(item.get('Code', '')).startswith('00'):
                tickers[f"{item['Code']}.TW"] = item.get('Name', '')
    except Exception as e:
        print(f"[日誌] TWSE 名單獲取失敗: {e}")

    try:
        res = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", headers=headers, timeout=5)
        for item in res.json():
            code = str(item.get('SecuritiesCompanyCode', ''))
            if code.startswith('00'):
                tickers[f"{code}.TWO"] = item.get('CompanyName', '')
    except Exception as e:
        print(f"[日誌] TPEX 名單獲取失敗: {e}")

    # 強制備援機制：如果官方 API 被鎖導致名單為空，直接生成 0050~00999 避免程式結束
    if not tickers:
        print("[警告] 官方 API 無法取得名單，啟動暴力備援清單...")
        for i in range(50, 1000):
            tickers[f"00{str(i).zfill(3)}.TW"] = "台股 ETF"
            tickers[f"00{str(i).zfill(3)}B.TWO"] = "債券 ETF"

    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/MI_101", headers=headers, timeout=5)
        for item in res.json():
            try: nav_dict[f"{item.get('Code')}.TW"] = float(str(item.get('Nav', '0')).replace(',', ''))
            except: pass
    except: pass

    try:
        res = requests.get("https://www.tpex.org.tw/web/etf/g_info/fund_info_prb.php?l=zh-tw", headers=headers, timeout=5)
        for row in res.get('aaData', []):
            try: nav_dict[f"{row[0]}.TWO"] = float(str(row[3]).replace(',', ''))
            except: pass
    except: pass

    return tickers, nav_dict

def main():
    tickers, nav_dict = fetch_official_data()
    robust_session = get_robust_session()
    db = {cat: [] for cat in FILE_MAP.keys()}

    print(f"啟動單線安全掃描，共 {len(tickers)} 檔...")

    for idx, (ticker, name) in enumerate(tickers.items()):
        if idx % 20 == 0 and idx > 0:
            print(f"進度: {idx} / {len(tickers)}")

        try:
            tk = yf.Ticker(ticker, session=robust_session)
            # 強制逾時設定，防止 yfinance 掛起
            tk.session.request = lambda *args, **kwargs: robust_session.request(*args, timeout=5, **kwargs)
            
            info = tk.info
            hist = tk.history(period="1y")

            if hist.empty or len(hist) < 20:
                continue

            current_price = float(hist['Close'].iloc[-1])
            vol_20d = int(hist['Volume'].tail(20).mean() / 1000)

            if len(hist) >= 200:
                cagr_1y = float((current_price - hist['Close'].iloc[0]) / hist['Close'].iloc[0])
                max_p = hist['Close'].cummax()
                mdd = float(((hist['Close'] - max_p) / max_p).min())
                daily_ret = hist['Close'].pct_change().dropna()
                sharpe = float((daily_ret.mean() / daily_ret.std()) * (252**0.5)) if daily_ret.std() != 0 else None
            else:
                cagr_1y, sharpe, mdd = None, None, None

            # 規模與淨值備援邏輯
            aum = info.get('totalAssets') or info.get('marketCap')
            if aum:
                aum = aum / 100000000
            else:
                shares = info.get('sharesOutstanding')
                if shares and current_price:
                    aum = (shares * current_price) / 100000000
            
            nav = nav_dict.get(ticker)
            premium = None
            if nav and nav > 0:
                premium = (current_price - nav) / nav

            # 錯誤日誌追蹤
            if aum is None: print(f"[除錯] {ticker} 缺失規模資料 (AUM)")
            if nav is None: print(f"[除錯] {ticker} 缺失官方淨值資料 (NAV)")

            # 配息處理邏輯：只抓實質數據
            yield_ttm = info.get('trailingAnnualDividendYield')
            dividend_rate = info.get('trailingAnnualDividendRate')
            
            next_div_date = None
            next_div_amount = None
            
            ex_div_ts = info.get('exDividendDate')
            if ex_div_ts:
                if ex_div_ts >= datetime.now().timestamp():
                    next_div_date = datetime.fromtimestamp(ex_div_ts).strftime('%Y-%m-%d')
                    next_div_amount = info.get('dividendRate') or info.get('lastDividendValue')

            # 覆蓋備援名稱
            final_name = name if name != "台股 ETF" and name != "債券 ETF" else info.get('shortName', name)
            category = categorize_etf(final_name)
            
            db[category].append({
                "id": ticker.split('.')[0],
                "name": final_name,
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
            })
            
        except Exception as e:
            pass # 逾時或無效標的直接跳過，不印出干擾日誌
            
        time.sleep(random.uniform(0.2, 0.8)) # 安全休眠

    # 輸出
    for cat, data in db.items():
        clean_data = sanitize_json(data)
        with open(FILE_MAP[cat], "w", encoding="utf-8") as f:
            json.dump(clean_data, f, ensure_ascii=False, indent=2)

    print("資料庫更新完成。")

if __name__ == "__main__":
    main()
