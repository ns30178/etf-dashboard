import os
import requests
import pandas as pd
import json
import time
import math
import warnings
from datetime import datetime, timedelta, timezone

warnings.filterwarnings("ignore", category=RuntimeWarning)

FINMIND_TOKEN = os.environ.get("FINMIND_TOKEN", "")
FUGLE_KEY = os.environ.get("FUGLE_API_KEY", "")
TELEGRAM_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TG_CHAT_ID", "")

FILE_MAP = {
    "高股息": "data_high_div.json", "市值型": "data_market_cap.json",
    "主題型": "data_theme.json", "債券型": "data_bond.json",
    "槓桿型": "data_leverage.json", "反向型": "data_inverse.json",
    "綜合/其他": "data_other.json"
}

FREQ_MAP_CACHE = {
    "00929": "月配", "00934": "月配", "00936": "月配", "00939": "月配", "00940": "月配", "00944": "月配", "00946": "月配",
    "0056": "季配", "00878": "季配", "00919": "季配", "00713": "季配", "00915": "季配", "00731": "季配", "00918": "季配",
    "0050": "半年配", "006208": "半年配"
}

def send_telegram_message(message):
    print("--- 準備發送 Telegram 推播 ---")
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: 
        print("⚠️ 警告：找不到 TG_BOT_TOKEN 或 TG_CHAT_ID，推播已略過。")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try: 
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code == 200: print("✅ Telegram 推播發送成功！")
        else: print(f"❌ Telegram 發送失敗。錯誤碼: {res.status_code}, 原因: {res.text}")
    except Exception as e: 
        print(f"❌ Telegram 連線發生異常: {e}")

def sanitize_json(val):
    if isinstance(val, dict): return {k: sanitize_json(v) for k, v in val.items()}
    elif isinstance(val, list): return [sanitize_json(v) for v in val]
    elif isinstance(val, float): return None if math.isnan(val) or math.isinf(val) else val
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
    try:
        url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInfo&token={FINMIND_TOKEN}"
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            for item in res.json().get('data', []):
                if item.get('industry_category') == 'ETF':
                    code = str(item.get('stock_id'))
                    tickers[code] = {"name": str(item.get('stock_name'))}
    except: pass
    return tickers

def fetch_fugle_candles(symbol):
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
    url = f"https://api.fugle.tw/marketdata/v1.0/stock/historical/candles/{symbol}?from={start_date}&to={end_date}&timeframe=D"
    
    for _ in range(2):
        try:
            res = requests.get(url, headers={"X-API-KEY": FUGLE_KEY}, timeout=5)
            if res.status_code == 200:
                data = res.json().get('data', [])
                if data:
                    df = pd.DataFrame(data)
                    df['date'] = pd.to_datetime(df['date'])
                    df.set_index('date', inplace=True)
                    df.sort_index(inplace=True)
                    return df
        except: pass
        time.sleep(1)
    return pd.DataFrame()

def fetch_finmind_price_fallback(symbol):
    start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
    url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPrice&data_id={symbol}&start_date={start_date}&token={FINMIND_TOKEN}"
    
    for _ in range(2):
        try:
            res = requests.get(url, timeout=5)
            if res.status_code == 200:
                data = res.json().get('data', [])
                if data:
                    df = pd.DataFrame(data)
                    df['date'] = pd.to_datetime(df['date'])
                    df.set_index('date', inplace=True)
                    df['close'] = df['close'] if 'close' in df else df.get('Close')
                    df['volume'] = df['Trading_Volume']
                    df.sort_index(inplace=True)
                    return df
        except: pass
        time.sleep(1)
    return pd.DataFrame()

def fetch_finmind_dividend(symbol):
    start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
    url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockDividend&data_id={symbol}&start_date={start_date}&token={FINMIND_TOKEN}"
    try:
        res = requests.get(url, timeout=4)
        if res.status_code == 200: return res.json().get('data', [])
    except: pass
    return []

def get_dividend_freq(symbol, div_data):
    if symbol in FREQ_MAP_CACHE: return FREQ_MAP_CACHE[symbol]
    if not div_data: return "未知"
    now = datetime.now()
    one_year_ago = now - timedelta(days=365)
    count = sum(1 for d in div_data if one_year_ago <= datetime.strptime(d.get('DividendYieldDate', '1900-01-01'), '%Y-%m-%d') <= now)
    if count >= 10: return "月配"
    if count >= 3: return "季配"
    if count == 2: return "半年配"
    if count == 1: return "年配"
    return "未知"

def main():
    tickers = fetch_etf_list()
    if not tickers: 
        send_telegram_message("❌ ETF 資料庫更新失敗：FinMind 名單獲取異常。")
        return

    db = {cat: [] for cat in FILE_MAP.keys()}
    search_index = []
    current_year = datetime.now().year
    last_year = current_year - 1

    print(f"啟動極速量化引擎，共 {len(tickers)} 檔...")

    for idx, (ticker_id, info) in enumerate(tickers.items()):
        name = info['name']
        category = categorize_etf(name)
        
        current_price = cagr_1y = ytd = sharpe = mdd = vol_20d = yield_ttm = dividend_rate = None
        freq = "未知"

        try:
            hist = fetch_fugle_candles(ticker_id)
            if hist.empty:
                hist = fetch_finmind_price_fallback(ticker_id)

            if not hist.empty and len(hist) > 0:
                current_price = float(hist['close'].iloc[-1])
                
                # 計算 YTD
                last_year_df = hist[hist.index.year == last_year]
                if not last_year_df.empty:
                    last_year_close = float(last_year_df['close'].iloc[-1])
                    ytd = (current_price - last_year_close) / last_year_close
                else:
                    this_year_df = hist[hist.index.year == current_year]
                    if not this_year_df.empty:
                        first_close = float(this_year_df['close'].iloc[0])
                        ytd = (current_price - first_close) / first_close

                if len(hist) >= 20:
                    vol_20d = int(hist['volume'].tail(20).mean() / 1000)
                
                if len(hist) >= 200:
                    cagr_1y = float((current_price - hist['close'].iloc[0]) / hist['close'].iloc[0])
                    max_p = hist['close'].cummax()
                    mdd = float(((hist['close'] - max_p) / max_p).min())
                    daily_ret = hist['close'].pct_change().dropna()
                    
                    std_val = daily_ret.std()
                    if pd.notna(std_val) and std_val > 0:
                        sharpe = float((daily_ret.mean() / std_val) * (252**0.5))

            div_data = fetch_finmind_dividend(ticker_id)
            freq = get_dividend_freq(ticker_id, div_data)
            ttm_div = 0.0
            now = datetime.now()
            for record in div_data:
                ex_date_str = record.get('DividendYieldDate', '1900-01-01')
                amt = float(record.get('CashEarningsDistribution', 0) or 0)
                try:
                    if datetime.strptime(ex_date_str, '%Y-%m-%d') <= now: ttm_div += amt
                except: pass

            if ttm_div > 0: dividend_rate = ttm_div
            if ttm_div > 0 and current_price: yield_ttm = ttm_div / current_price

        except Exception as e: pass
        
        db[category].append({
            "id": ticker_id, "name": name, "freq": freq,
            "price": current_price, "cagr_1y": cagr_1y, "ytd": ytd,
            "sharpe": sharpe, "mdd": mdd, "vol_20d": vol_20d,
            "yield_ttm": yield_ttm, "dividend_rate": dividend_rate
        })
        search_index.append({"id": ticker_id, "name": name, "category": category})
        
        time.sleep(0.5) 

    for cat, data in db.items():
        with open(FILE_MAP[cat], "w", encoding="utf-8") as f: json.dump(sanitize_json(data), f, ensure_ascii=False, indent=2)
    with open("search_index.json", "w", encoding="utf-8") as f: json.dump(search_index, f, ensure_ascii=False, indent=2)

    ipo_db = [
        {"id": "00946", "name": "群益科技高息成長", "issueDate": "2026-05-09", "price": 10.0, "fee": 0.30, "freq": "月配", "topHoldings": "聯發科, 瑞昱, 聯詠"},
        {"id": "00947", "name": "台新臺灣IC設計", "issueDate": "2026-06-12", "price": 15.0, "fee": 0.40, "freq": "季配", "topHoldings": "台積電, 聯發科, 瑞昱"}
    ]
    with open("data_ipo.json", "w", encoding="utf-8") as f: json.dump(ipo_db, f, ensure_ascii=False, indent=2)

    tw_tz = timezone(timedelta(hours=8))
    tw_time = datetime.now(tw_tz).strftime('%Y-%m-%d %H:%M:%S')
    
    # 輸出時間戳記給前端
    with open("meta.json", "w", encoding="utf-8") as f: json.dump({"last_update": tw_time}, f, ensure_ascii=False)

    success_msg = f"更新✅ 台股全市場 ETF 數據庫已極速更新完畢！\n執行時間：{tw_time}"
    print(success_msg)
    send_telegram_message(success_msg)

if __name__ == "__main__":
    main()
