import os
import requests
import pandas as pd
import json
import time
import math
import warnings
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

warnings.filterwarnings("ignore", category=RuntimeWarning)

# 對接你截圖中正確的 Secrets 名稱
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
        else: print(f"❌ Telegram 發送失敗。錯誤碼: {res.status_code}")
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
    
    # 加入重試機制，避免網路瞬斷導致資料空白
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

def fetch_yahoo_news(symbol):
    try:
        res = requests.get(f"https://tw.stock.yahoo.com/quote/{symbol}/news", headers={"User-Agent": "Mozilla/5.0"}, timeout=4)
        soup = BeautifulSoup(res.text, 'html.parser')
        news = []
        for a in soup.find_all('a', href=True):
            title, href = a.text.strip(), a['href']
            if len(title) > 10 and 'http' in href and ('news' in href or 'article' in href):
                news.append({"title": title, "link": href, "date": datetime.today().strftime('%Y-%m-%d')})
            if len(news) >= 3: break
        return news
    except: return []

def main():
    tickers = fetch_etf_list()
    if not tickers: 
        send_telegram_message("❌ ETF 資料庫更新失敗：FinMind 名單獲取異常。")
        return

    db = {cat: [] for cat in FILE_MAP.keys()}
    news_db = {}
    search_index = []

    print(f"啟動 API 混合量化引擎，共 {len(tickers)} 檔...")

    for idx, (ticker_id, info) in enumerate(tickers.items()):
        name = info['name']
        category = categorize_etf(name)
        
        current_price = cagr_1y = sharpe = mdd = vol_20d = yield_ttm = dividend_rate = None

        try:
            hist = fetch_fugle_candles(ticker_id)
            if hist.empty:
                hist = fetch_finmind_price_fallback(ticker_id)

            if not hist.empty and len(hist) > 0:
                current_price = float(hist['close'].iloc[-1])
                if len(hist) >= 20: vol_20d = int(hist['volume'].tail(20).mean() / 1000)
                if len(hist) >= 200:
                    cagr_1y = float((current_price - hist['close'].iloc[0]) / hist['close'].iloc[0])
                    max_p = hist['close'].cummax()
                    mdd = float(((hist['close'] - max_p) / max_p).min())
                    daily_ret = hist['close'].pct_change().dropna()
                    std_val = daily_ret.std()
                    if pd.notna(std_val) and std_val > 0:
                        sharpe = float((daily_ret.mean() / std_val) * (252**0.5))

            div_data = fetch_finmind_dividend(ticker_id)
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

            if vol_20d and vol_20d > 100: news_db[ticker_id] = fetch_yahoo_news(ticker_id)

        except Exception as e: print(f"{ticker_id} 處理異常: {e}")
        
        db[category].append({
            "id": ticker_id, "name": name,
            "price": current_price, "cagr_1y": cagr_1y, 
            "sharpe": sharpe, "mdd": mdd, "vol_20d": vol_20d,
            "yield_ttm": yield_ttm, "dividend_rate": dividend_rate
        })
        search_index.append({"id": ticker_id, "name": name, "category": category})
        
        time.sleep(1.0)

    for cat, data in db.items():
        with open(FILE_MAP[cat], "w", encoding="utf-8") as f: json.dump(sanitize_json(data), f, ensure_ascii=False, indent=2)
    with open("search_index.json", "w", encoding="utf-8") as f: json.dump(search_index, f, ensure_ascii=False, indent=2)

    ipo_db = [
        {"id": "00946", "name": "群益科技高息成長", "issueDate": "2026-05-09", "price": 10.0, "fee": 0.30, "freq": "月配", "topHoldings": "聯發科, 瑞昱, 聯詠"},
        {"id": "00947", "name": "台新臺灣IC設計", "issueDate": "2026-06-12", "price": 15.0, "fee": 0.40, "freq": "季配", "topHoldings": "台積電, 聯發科, 瑞昱"}
    ]
    for ipo in ipo_db: news_db[ipo['id']] = fetch_yahoo_news(ipo['id'])
    with open("data_ipo.json", "w", encoding="utf-8") as f: json.dump(ipo_db, f, ensure_ascii=False, indent=2)
    with open("data_news.json", "w", encoding="utf-8") as f: json.dump(news_db, f, ensure_ascii=False, indent=2)

    tw_tz = timezone(timedelta(hours=8))
    tw_time = datetime.now(tw_tz).strftime('%Y-%m-%d %H:%M:%S')
    success_msg = f"更新✅ 台股全市場 ETF 數據庫與新聞動態更新成功！\n執行時間：{tw_time}"
    print(success_msg)
    send_telegram_message(success_msg)

if __name__ == "__main__":
    main()
