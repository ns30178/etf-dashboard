import os
import requests
import pandas as pd
import json
import time
import math
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

# 環境變數
FINMIND_TOKEN = os.environ.get("FINMIND_TOKEN", "")
FUGLE_KEY = os.environ.get("FUGLE_API_KEY", "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

FILE_MAP = {
    "高股息": "data_high_div.json", "市值型": "data_market_cap.json",
    "主題型": "data_theme.json", "債券型": "data_bond.json",
    "槓桿型": "data_leverage.json", "反向型": "data_inverse.json",
    "綜合/其他": "data_other.json"
}

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

def send_telegram_message(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try: requests.post(url, json=payload, timeout=10)
    except Exception as e: print(f"Telegram 發送失敗: {e}")

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
        res = requests.get(url, timeout=15)
        if res.status_code == 200:
            for item in res.json().get('data', []):
                if item.get('industry_category') == 'ETF':
                    tickers[str(item.get('stock_id'))] = str(item.get('stock_name'))
    except Exception as e: print(f"ETF 清單獲取錯誤: {e}")
    return tickers

def fetch_fugle_candles(symbol):
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
    url = f"https://api.fugle.tw/marketdata/v1.0/stock/historical/candles/{symbol}?from={start_date}&to={end_date}&timeframe=D"
    try:
        res = requests.get(url, headers={"X-API-KEY": FUGLE_KEY}, timeout=10)
        if res.status_code == 200:
            data = res.json().get('data', [])
            if data: return pd.DataFrame(data)
    except: pass
    return pd.DataFrame()

def fetch_yahoo_html_data(ticker_id):
    """最底層備援：強制爬蟲 Yahoo"""
    data = {"nav": None, "aum": None}
    try:
        url = f"https://tw.stock.yahoo.com/quote/{ticker_id}"
        res = requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        # 尋找頁面中所有包含數值的標籤
        text = soup.get_text()
        # 簡單提取邏輯 (實際狀況依網頁結構)
        if "淨值" in text:
            # 這裡需要根據 Yahoo 頁面實際結構微調，此為範例
            pass 
    except: pass
    return data

def main():
    tickers = fetch_etf_list()
    if not tickers: 
        send_telegram_message("❌ ETF 資料庫更新失敗：名單獲取異常。")
        return

    db = {cat: [] for cat in FILE_MAP.keys()}
    search_index = []
    processed_count = 0

    print(f"🚀 開始執行，共 {len(tickers)} 檔...")

    for ticker_id, name in tickers.items():
        try:
            hist = fetch_fugle_candles(ticker_id)
            if hist.empty or len(hist) < 20: 
                print(f"⚠️ {ticker_id} 無歷史行情，跳過。")
                continue

            # 簡易計算
            price = float(hist['close'].iloc[-1])
            category = categorize_etf(name)
            
            db[category].append({
                "id": ticker_id, "name": name, "price": price, 
                "premium": None, "aum": None, # 因 API 限制，這些欄位改由前端補全或定期批次更新
                "cagr_1y": None, "sharpe": None, "mdd": None, "vol_20d": None
            })
            search_index.append({"id": ticker_id, "name": name, "category": category})
            processed_count += 1
            
        except Exception as e: print(f"❌ {ticker_id} 處理錯誤: {e}")
        time.sleep(1.2) # 遵守 API 速率限制

    for cat, data in db.items():
        with open(FILE_MAP[cat], "w", encoding="utf-8") as f: json.dump(sanitize_json(data), f, ensure_ascii=False, indent=2)
    with open("search_index.json", "w", encoding="utf-8") as f: json.dump(search_index, f, ensure_ascii=False, indent=2)

    msg = f"✅ 更新完成！共處理 {processed_count} 檔 ETF。"
    print(msg)
    send_telegram_message(msg)

if __name__ == "__main__":
    main()
