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
    """建立附帶自動重試機制的連線池，專剋 429 與 502 錯誤"""
    session = requests.Session()
    # status_forcelist 鎖定 429(過多請求), 500, 502, 503, 504
    # backoff_factor=1 代表重試間隔為 0s, 2s, 4s, 8s... 指數增加
    retry = Retry(total=5, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    return session

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
    if any(k in name for k in ['正2', '正達', '倍']): return "槓桿型"
    if any(k in name for k in ['反1', '反向']): return "反向型"
    if any(k in name for k in ['高息', '高股息', '優息', '股息', '息收']): return "高股息"
    if any(k in name for k in ['債', '國債', '投等', '金融債', '公司債']): return "債券型"
    if any(k in name for k in ['半導體', '電動車', '5G', 'AI', '科技', '生技', '不動產', '綠能', '網購', '主題', '電競']): return "主題型"
    if any(k in name for k in ['50', '100', '市值', '加權', '大盤', '摩台', 'MSCI', '中型', '富時']): return "市值型"
    return "綜合/其他"

def fetch_official_data():
    """獲取台股全市場 ETF 名單與 OpenAPI 官方淨值"""
    tickers = {}
    nav_dict = {}
    headers = {"User-Agent": "Mozilla/5.0"}
    
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", headers=headers, timeout=10)
        for item in res.json():
            if str(item.get('Code', '')).startswith('00'):
                tickers[f"{item['Code']}.TW"] = item.get('Name', '')
    except: pass

    try:
        res = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", headers=headers, timeout=10)
        for item in res.json():
            code = str(item.get('SecuritiesCompanyCode', ''))
            if code.startswith('00'):
                tickers[f"{code}.TWO"] = item.get('CompanyName', '')
    except: pass

    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/MI_101", headers=headers, timeout=10)
        for item in res.json():
            try: nav_dict[f"{item.get('Code')}.TW"] = float(str(item.get('Nav', '0')).replace(',', ''))
            except: pass
    except: pass

    try:
        res = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_etf_nav", headers=headers, timeout=10)
        for item in res.json():
            try: nav_dict[f"{item.get('SecuritiesCompanyCode')}.TWO"] = float(str(item.get('Nav', '0')).replace(',', ''))
            except: pass
    except: pass

    return tickers, nav_dict

def main():
    tickers, nav_dict = fetch_official_data()
    robust_session = get_robust_session()
    db = {cat: [] for cat in FILE_MAP.keys()}

    print(f"🚀 啟動防封鎖單線爬蟲，共獲取 {len(tickers)} 檔清單...")
    print("⚠️ 為徹底避開 Yahoo 防火牆限制，預計執行時間需約 5~10 分鐘，請耐心等候。")

    for idx, (ticker, name) in enumerate(tickers.items()):
        if idx % 10 == 0 and idx > 0:
            print(f"⏳ 爬取進度: {idx} / {len(tickers)} 檔...")

        success = False
        # 單檔標的最多嘗試解析 3 次
        for attempt in range(3):
            try:
                # 將防禦型 session 注入 yfinance
                tk = yf.Ticker(ticker, session=robust_session)
                info = tk.info
                hist = tk.history(period="1y")

                if hist.empty or len(hist) < 20:
                    success = True
                    break

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

                # 規模備援計算
                aum = info.get('totalAssets') or info.get('marketCap')
                if aum:
                    aum = aum / 100000000
                else:
                    shares = info.get('sharesOutstanding')
                    if shares and current_price:
                        aum = (shares * current_price) / 100000000

                # 折溢價計算
                nav = nav_dict.get(ticker)
                premium = ((current_price - nav) / nav) if nav and nav > 0 else None

                # 實質配息與未來已公告配息
                yield_ttm = info.get('trailingAnnualDividendYield')
                dividend_rate = info.get('trailingAnnualDividendRate')
                
                next_div_date = None
                next_div_amount = None
                
                ex_div_ts = info.get('exDividendDate')
                if ex_div_ts is not None and ex_div_ts >= datetime.now().timestamp():
                    next_div_date = datetime.fromtimestamp(ex_div_ts).strftime('%Y-%m-%d')
                    next_div_amount = info.get('dividendRate') or info.get('lastDividendValue')

                category = categorize_etf(name)
                db[category].append({
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
                })
                
                success = True
                break  # 解析成功，跳出重試迴圈

            except Exception as e:
                # 如果遇到 JSON Decode 或其他怪異錯誤，休息 2 秒後重試
                time.sleep(2)

        if not success:
            print(f"[錯誤] {ticker} ({name}) 嘗試 3 次仍被阻擋或查無資料，已跳過。")

        # 標的與標的之間加入隨機的安全休眠，完全模擬真實人類行為
        time.sleep(random.uniform(0.5, 1.5))

    # 清洗資料並寫入 JSON
    for cat, data in db.items():
        clean_data = sanitize_json(data)
        with open(FILE_MAP[cat], "w", encoding="utf-8") as f:
            json.dump(clean_data, f, ensure_ascii=False, indent=2)

    print("🎉 資料庫更新與過濾完成。")

if __name__ == "__main__":
    main()
