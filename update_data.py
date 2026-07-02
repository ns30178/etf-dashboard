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

# 現有已知名單 (加速處理，降低 API 請求次數)
FREQ_MAP = {
    "00929": "月配", "00934": "月配", "00936": "月配", "00939": "月配", "00940": "月配", "00943": "月配", "00944": "月配", "00946": "月配", "00963": "月配", "00964": "月配", "00772B": "月配", "00773B": "月配", "00933B": "月配", "00937B": "月配", "00945B": "月配", "00948B": "月配", "00953B": "月配", "00958B": "月配", "00959B": "月配", "00968B": "月配",
    "00907": "雙月配",
    "0056": "季配", "00878": "季配", "00919": "季配", "00713": "季配", "00915": "季配", "00731": "季配", "00918": "季配", "00702": "季配", "00932": "季配", "00961": "季配", "00962": "季配", "00956": "季配", "00972": "季配", "00400A": "季配", "00984A": "季配", "00896": "季配", "00922": "季配", "00923": "季配", "00927": "季配", "00888": "季配", "00891": "季配", "00894": "季配", "00904": "季配", "00912": "季配", "00947": "季配", "00960": "季配", "00728": "季配", "00882": "季配", "00892": "季配", "00900": "季配", "00905": "季配", "00885": "季配", "00901": "季配", "00752": "季配", "00876": "季配", "00928": "季配", "00935": "季配", "00951": "季配", "00952": "季配", "00712": "季配", "00714": "季配", "00717": "季配", "00733": "季配", "00751B": "季配", "00754B": "季配", "00755B": "季配", "00756B": "季配", "00758B": "季配", "00761B": "季配", "00764B": "季配", "00768B": "季配", "00782B": "季配", "00786B": "季配", "00788B": "季配", "00789B": "季配", "00795B": "季配", "00836B": "季配", "00840B": "季配", "00841B": "季配", "00842B": "季配", "00844B": "季配", "00846B": "季配", "00847B": "季配", "00848B": "季配", "00849B": "季配", "00853B": "季配", "00856B": "季配", "00857B": "季配", "00859B": "季配", "00862B": "季配", "00679B": "季配", "00687B": "季配", "00720B": "季配", "00725B": "季配", "00740B": "季配", "00746B": "季配", "00722B": "季配", "00723B": "季配", "00724B": "季配", "00726B": "季配", "00727B": "季配", "00778B": "季配", "00779B": "季配", "00966B": "季配",
    "0050": "半年配", "006208": "半年配", "00730": "半年配", "00690": "半年配", "00913": "半年配", "0051": "半年配", "0053": "半年配", "0055": "半年配", "006203": "半年配", "006204": "半年配", "00692": "半年配", "00850": "半年配", "00881": "半年配", "00898": "半年配", "00903": "半年配", "00981A": "半年配", "00988A": "半年配", "00994A": "半年配", "00995A": "半年配", "00875": "半年配", "00646": "半年配", "006205": "半年配", "00830": "半年配", "00851": "半年配",
    "00861": "年配", "00877": "年配"
}

def send_telegram_message(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try: requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=10)
    except: pass

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

def get_dynamic_frequency(symbol, name):
    if "月配" in name: return "月配"
    if "季配" in name: return "季配"
    if "半年配" in name: return "半年配"
    if "年配" in name: return "年配"
    try:
        start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
        url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockDividend&data_id={symbol}&start_date={start_date}&token={FINMIND_TOKEN}"
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            data = res.json().get('data', [])
            if data and len(data) > 0:
                dates = set([d['date'] for d in data])
                count = len(dates)
                if count >= 10: return "月配"
                elif count >= 3: return "季配"
                elif count == 2: return "半年配"
                elif count == 1: return "年配"
    except Exception:
        pass
    return "-"

def fetch_etf_list():
    tickers = {}
    try:
        url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInfo&token={FINMIND_TOKEN}"
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            for item in res.json().get('data', []):
                if item.get('industry_category') == 'ETF':
                    tickers[str(item.get('stock_id'))] = {"name": str(item.get('stock_name'))}
    except: pass
    return tickers

def fetch_fugle_candles(symbol):
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
    url = f"https://api.fugle.tw/marketdata/v1.0/stock/historical/candles/{symbol}?from={start_date}&to={end_date}&timeframe=D"
    for _ in range(2):
        try:
            res = requests.get(url, headers={"X-API-KEY": FUGLE_KEY}, timeout=5)
            if res.status_code == 200 and res.json().get('data'):
                df = pd.DataFrame(res.json().get('data'))
                df['date'] = pd.to_datetime(df['date'])
                return df.set_index('date').sort_index()
        except: pass
        time.sleep(1)
    return pd.DataFrame()

def fetch_finmind_price_fallback(symbol):
    start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
    url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPrice&data_id={symbol}&start_date={start_date}&token={FINMIND_TOKEN}"
    for _ in range(2):
        try:
            res = requests.get(url, timeout=5)
            if res.status_code == 200 and res.json().get('data'):
                df = pd.DataFrame(res.json().get('data'))
                df['date'] = pd.to_datetime(df['date'])
                df.set_index('date', inplace=True)
                df['close'] = df['close'] if 'close' in df else df.get('Close')
                df['volume'] = df['Trading_Volume']
                return df.sort_index()
        except: pass
        time.sleep(1)
    return pd.DataFrame()

# 🚀 強化版：MoneyDJ 新基金募集資料爬蟲模組
def fetch_ipo_data():
    ipo_list = []
    try:
        url = "https://www.moneydj.com/fundj/fundmarket.djhtm?a=broncho-1"
        # 加上更擬真、詳細的 Header，避免被輕易判定為機器人封鎖
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7"
        }
        res = requests.get(url, headers=headers, timeout=15)
        res.encoding = 'utf-8'
        
        # 只有在成功取得網頁時才進行解析
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            tables = soup.find_all("table")
            
            for table in tables:
                text_content = table.get_text()
                # 精準定位：一定要同時包含這些表頭字眼才解析，避開雜訊表格
                if "基金名稱" in text_content and "基金型態" in text_content and "募集期間" in text_content:
                    rows = table.find_all("tr")
                    for row in rows:
                        # 加上 recursive=False 確保不會抓到「表格中的表格」導致錯亂
                        cols = row.find_all(["td", "th"], recursive=False)
                        if len(cols) >= 5:
                            name = cols[0].get_text(strip=True)
                            fund_type = cols[1].get_text(strip=True)
                            manager = cols[2].get_text(strip=True)
                            period = cols[4].get_text(strip=True)
                            
                            # 剔除表頭與無效行
                            if "基金名稱" in name or "核准募集" in name or not name:
                                continue
                                
                            name = name.split("\n")[0].strip()
                            if len(name) < 2:
                                continue
                                
                            # 確保不重複加入
                            if not any(item['name'] == name for item in ipo_list):
                                ipo_list.append({
                                    "id": "IPO",
                                    "name": name,
                                    "type": fund_type,
                                    "manager": manager,
                                    "period": period
                                })
                    # 抓到目標表格並解析完畢後，直接跳出迴圈
                    if len(ipo_list) > 0:
                        break
    except Exception as e:
        print(f"MoneyDJ IPO 募集解析異常: {e}")

    # 🛡️【防呆提示機制】：如果被 MoneyDJ 防火牆封鎖，或者網頁剛好真的清空了，給予前端一個提示，避免變成白畫面
    if not ipo_list:
        ipo_list.append({
            "id": "⚠️ 系統提示",
            "name": "目前無資料，或伺服器遭 MoneyDJ 阻擋",
            "type": "請點擊展開",
            "manager": "-",
            "period": "點擊下方按鈕直接前往查看"
        })
        
    return ipo_list

def main():
    tickers = fetch_etf_list()
    if not tickers: return

    db = {cat: [] for cat in FILE_MAP.keys()}
    search_index = []
    
    # 建立「歷史快取防禦機制」：先載入上次的資料庫，避免限流時檔案歸零
    old_data_map = {}
    for cat, filename in FILE_MAP.items():
        if os.path.exists(filename):
            try:
                with open(filename, "r", encoding="utf-8") as f:
                    items = json.load(f)
                    for item in items:
                        if isinstance(item, dict) and "id" in item:
                            old_data_map[item["id"]] = item
            except: pass
            
    current_year = datetime.now().year
    last_year = current_year - 1

    print(f"啟動量化引擎，共 {len(tickers)} 檔...")

    for idx, (ticker_id, info) in enumerate(tickers.items()):
        name = info['name']
        category = categorize_etf(name)
        
        current_price = cagr_1y = ytd = sharpe = mdd = vol_20d = yield_ttm = dividend_rate = None
        
        freq = FREQ_MAP.get(ticker_id)
        if not freq:
            freq = get_dynamic_frequency(ticker_id, name)

        try:
            hist = fetch_fugle_candles(ticker_id)
            if hist.empty: hist = fetch_finmind_price_fallback(ticker_id)

            if not hist.empty and len(hist) > 0:
                current_price = float(hist['close'].iloc[-1])
                
                last_year_df = hist[hist.index.year == last_year]
                if not last_year_df.empty:
                    last_close = float(last_year_df['close'].iloc[-1])
                    ytd = (current_price - last_close) / last_close
                else:
                    this_year_df = hist[hist.index.year == current_year]
                    if not this_year_df.empty:
                        first_close = float(this_year_df['close'].iloc[0])
                        ytd = (current_price - first_close) / first_close

                if len(hist) >= 20: vol_20d = int(hist['volume'].tail(20).mean() / 1000)
                
                if len(hist) >= 200:
                    first_price = float(hist['close'].iloc[0])
                    cagr_1y = (current_price - first_price) / first_price
                    max_p = hist['close'].cummax()
                    mdd = float(((hist['close'] - max_p) / max_p).min())
                    daily_ret = hist['close'].pct_change().dropna()
                    std_val = daily_ret.std()
                    if pd.notna(std_val) and std_val > 0:
                        sharpe = float((daily_ret.mean() / std_val) * (252**0.5))
        except Exception: pass
        
        # 觸發防禦：若 API 被限流或沒給市價，拿上一次成功的數據補位
        if current_price is None or math.isnan(current_price):
            if ticker_id in old_data_map:
                db[category].append(old_data_map[ticker_id])
                search_index.append({"id": ticker_id, "name": name, "category": category})
            continue
        
        db[category].append({
            "id": ticker_id, "name": name, "freq": freq,
            "price": current_price, "ytd": ytd, "cagr_1y": cagr_1y, 
            "sharpe": sharpe, "mdd": mdd, "vol_20d": vol_20d,
            "yield_ttm": yield_ttm, "dividend_rate": dividend_rate
        })
        search_index.append({"id": ticker_id, "name": name, "category": category})
        time.sleep(0.5) 

    for cat, data in db.items():
        with open(FILE_MAP[cat], "w", encoding="utf-8") as f:
            json.dump(sanitize_json(data), f, ensure_ascii=False, indent=2)
            
    with open("search_index.json", "w", encoding="utf-8") as f:
        json.dump(search_index, f, ensure_ascii=False, indent=2)

    # 執行 MoneyDJ 動態募集資料庫寫入
    ipo_db = fetch_ipo_data()
    with open("data_ipo.json", "w", encoding="utf-8") as f:
        json.dump(ipo_db, f, ensure_ascii=False, indent=2)

    tw_tz = timezone(timedelta(hours=8))
    tw_time = datetime.now(tw_tz).strftime('%Y-%m-%d %H:%M:%S')
    with open("meta.json", "w", encoding="utf-8") as f:
        json.dump({"last_update": tw_time}, f, ensure_ascii=False)

    success_msg = f"更新✅ 台股全市場 ETF 數據庫已更新完畢！\n執行時間：{tw_time}"
    print(success_msg)
    send_telegram_message(success_msg)

if __name__ == "__main__": main()
