import os
import requests
import pandas as pd
import json
import time
import math
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

# 環境變數與金鑰（維持最高資安等級：從 Secrets 獲取）
FINMIND_TOKEN = os.environ.get("FINMIND_TOKEN", "")
FUGLE_KEY = os.environ.get("FUGLE_API_KEY", "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# 系統斷路器：避免 Yahoo 封鎖導致程式卡死
YAHOO_TIMEOUTS = 0

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
    try: requests.post(url, json=payload, timeout=5)
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

def calculate_issue_time(start_date_str):
    if not start_date_str: return "-"
    try:
        start = datetime.strptime(start_date_str[:10], "%Y-%m-%d")
        now = datetime.now()
        diff = now - start
        years = diff.days // 365
        months = (diff.days % 365) // 30
        if years == 0 and months == 0: return "未滿1個月"
        return f"{years}年{months}月"
    except: return "-"

def fetch_etf_list_and_nav():
    tickers, nav_dict = {}, {}
    try:
        url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInfo&token={FINMIND_TOKEN}"
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            for item in res.json().get('data', []):
                if item.get('industry_category') == 'ETF':
                    code = str(item.get('stock_id'))
                    tickers[code] = {
                        "name": str(item.get('stock_name')), 
                        "market": item.get('type', '').lower(),
                        "start_date": str(item.get('start_date', ''))
                    }
    except: pass
    
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/MI_101", headers=HEADERS, timeout=5)
        for item in res.json(): nav_dict[item.get('Code')] = float(str(item.get('Nav', '0')).replace(',', ''))
    except: pass
    return tickers, nav_dict

def fetch_fugle_candles(symbol):
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
    url = f"https://api.fugle.tw/marketdata/v1.0/stock/historical/candles/{symbol}?from={start_date}&to={end_date}&timeframe=D"
    try:
        res = requests.get(url, headers={"X-API-KEY": FUGLE_KEY}, timeout=4)
        if res.status_code == 200:
            data = res.json().get('data', [])
            if data:
                df = pd.DataFrame(data)
                df['date'] = pd.to_datetime(df['date'])
                df.set_index('date', inplace=True)
                df.sort_index(inplace=True)
                return df
    except: pass
    return pd.DataFrame()

def fetch_finmind_price_fallback(symbol):
    """【終極備援】當富果失敗時，切換至 FinMind 抓取歷史市價"""
    start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
    url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPrice&data_id={symbol}&start_date={start_date}&token={FINMIND_TOKEN}"
    try:
        res = requests.get(url, timeout=4)
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
    return pd.DataFrame()

def fetch_finmind_dividend(symbol):
    start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
    url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockDividend&data_id={symbol}&start_date={start_date}&token={FINMIND_TOKEN}"
    try:
        res = requests.get(url, timeout=4)
        if res.status_code == 200: return res.json().get('data', [])
    except: pass
    return []

def get_yahoo_crumb():
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        session.get('https://tw.yahoo.com/', timeout=5)
        crumb = session.get('https://query1.finance.yahoo.com/v1/test/getcrumb', timeout=5).text
        if "<html>" in crumb: return session, ""
        return session, crumb
    except: return session, ""

def fetch_yahoo_quote(symbol, market, session, crumb):
    """補回的 Yahoo API：用於取得預估配息與規模"""
    global YAHOO_TIMEOUTS
    if YAHOO_TIMEOUTS >= 3: return {}
    ticker = f"{symbol}.TW" if market == 'twse' else f"{symbol}.TWO"
    url = f"https://query2.finance.yahoo.com/v7/finance/quote?symbols={ticker}"
    if crumb: url += f"&crumb={crumb}"
    try:
        res = session.get(url, timeout=3)
        if res.status_code == 200: return res.json().get('quoteResponse', {}).get('result', [{}])[0]
    except requests.exceptions.Timeout: YAHOO_TIMEOUTS += 1
    except: pass
    return {}

def fetch_yahoo_html_backup(ticker_id):
    """HTML 備援，內建斷路器"""
    global YAHOO_TIMEOUTS
    data = {"nav": None, "aum": None, "top_holdings": []}
    if YAHOO_TIMEOUTS >= 3: return data
        
    try:
        url = f"https://tw.stock.yahoo.com/quote/{ticker_id}/profile"
        res = requests.get(url, headers=HEADERS, timeout=3)
        soup = BeautifulSoup(res.text, 'html.parser')
        nav_node = soup.find(string=lambda t: t and t.strip() == "淨值")
        if nav_node:
            try: data['nav'] = float(nav_node.find_next('span').text.replace(',', ''))
            except: pass
        aum_node = soup.find(string=lambda t: t and t.strip() == "基金規模")
        if aum_node:
            try:
                aum_str = aum_node.find_next('span').text
                if '億' in aum_str: data['aum'] = float(aum_str.replace('億', '').replace(',', '').strip())
            except: pass
    except requests.exceptions.Timeout: YAHOO_TIMEOUTS += 1
    except: pass

    if YAHOO_TIMEOUTS >= 3: return data

    try:
        url_h = f"https://tw.stock.yahoo.com/quote/{ticker_id}/holding"
        res_h = requests.get(url_h, headers=HEADERS, timeout=3)
        soup_h = BeautifulSoup(res_h.text, 'html.parser')
        links = soup_h.find_all('a', href=True)
        for link in links:
            if '/quote/' in link['href'] and link.text.strip() and len(link.text.strip()) > 1:
                name = link.text.strip()
                parent = link.find_parent('li') or link.find_parent('div')
                if parent:
                    pct_span = parent.find(string=lambda t: t and '%' in t)
                    if pct_span:
                        data['top_holdings'].append(f"{name} ({pct_span.strip()})")
                        if len(data['top_holdings']) >= 3: break
    except requests.exceptions.Timeout: YAHOO_TIMEOUTS += 1
    except: pass
    
    return data

def fetch_yahoo_news(symbol):
    global YAHOO_TIMEOUTS
    if YAHOO_TIMEOUTS >= 3: return []
    try:
        res = requests.get(f"https://tw.stock.yahoo.com/quote/{symbol}/news", headers=HEADERS, timeout=3)
        soup = BeautifulSoup(res.text, 'html.parser')
        news = []
        for a in soup.find_all('a', href=True):
            title, href = a.text.strip(), a['href']
            if len(title) > 10 and 'http' in href and ('news' in href or 'article' in href):
                news.append({"title": title, "link": href, "date": datetime.today().strftime('%Y-%m-%d')})
            if len(news) >= 3: break
        return news
    except requests.exceptions.Timeout: YAHOO_TIMEOUTS += 1
    except: return []

def main():
    tickers, nav_dict = fetch_etf_list_and_nav()
    if not tickers: 
        send_telegram_message("❌ ETF 資料庫更新失敗：FinMind 名單獲取異常。")
        return

    db = {cat: [] for cat in FILE_MAP.keys()}
    news_db = {}
    search_index = []
    yh_session, crumb = get_yahoo_crumb()

    print(f"啟動 API 混合量化引擎，以 FinMind 清單為基準，共 {len(tickers)} 檔...")

    for idx, (ticker_id, info) in enumerate(tickers.items()):
        name = info['name']
        category = categorize_etf(name)
        issue_time = calculate_issue_time(info['start_date'])
        
        current_price = premium = aum = cagr_1y = sharpe = mdd = vol_20d = None
        yield_ttm = dividend_rate = next_div_date = next_div_amount = None
        top_holdings = []

        try:
            # 1. 第一防線：Fugle 行情
            hist = fetch_fugle_candles(ticker_id)
            
            # 2. 第二防線：若 Fugle 沒給數據，瞬間切換為 FinMind 行情
            if hist.empty or len(hist) < 20:
                hist = fetch_finmind_price_fallback(ticker_id)

            if not hist.empty and len(hist) > 0:
                current_price = float(hist['close'].iloc[-1])
                if len(hist) >= 20:
                    vol_20d = int(hist['volume'].tail(20).mean() / 1000)
                if len(hist) >= 200:
                    cagr_1y = float((current_price - hist['close'].iloc[0]) / hist['close'].iloc[0])
                    max_p = hist['close'].cummax()
                    mdd = float(((hist['close'] - max_p) / max_p).min())
                    daily_ret = hist['close'].pct_change().dropna()
                    sharpe = float((daily_ret.mean() / daily_ret.std()) * (252**0.5)) if daily_ret.std() != 0 else None

            # 配息資料 (FinMind)
            div_data = fetch_finmind_dividend(ticker_id)
            ttm_div = 0.0
            now = datetime.now()
            for record in div_data:
                ex_date_str = record.get('DividendYieldDate', '1900-01-01')
                amt = float(record.get('CashEarningsDistribution', 0) or 0)
                try:
                    if datetime.strptime(ex_date_str, '%Y-%m-%d') <= now: ttm_div += amt
                except: pass

            # 【已修復】Yahoo API 與 HTML 雙重備援
            quote = fetch_yahoo_quote(ticker_id, info['market'], yh_session, crumb)
            q_aum = quote.get('marketCap')
            
            html_data = fetch_yahoo_html_backup(ticker_id)
            nav = nav_dict.get(ticker_id) or html_data.get('nav') or quote.get('navPrice')
            
            if q_aum: aum = q_aum / 100000000
            elif quote.get('sharesOutstanding') and current_price: aum = (quote.get('sharesOutstanding') * current_price) / 100000000
            else: aum = html_data.get('aum')
            
            top_holdings = html_data.get('top_holdings', [])

            if nav and current_price and nav > 0: premium = ((current_price - nav) / nav)
            
            # 【已修復】抓取預估配息
            q_ex = quote.get('exDividendDate')
            if q_ex and q_ex > now.timestamp():
                next_div_date = datetime.fromtimestamp(q_ex).strftime('%Y-%m-%d')
                next_div_amount = quote.get('dividendRate') or quote.get('trailingAnnualDividendRate')

            if ttm_div > 0: dividend_rate = ttm_div
            if ttm_div > 0 and current_price: yield_ttm = ttm_div / current_price

            if vol_20d and vol_20d > 100: news_db[ticker_id] = fetch_yahoo_news(ticker_id)

        except Exception: pass
        
        # 寫入 JSON (容錯回寫)
        db[category].append({
            "id": ticker_id, "name": name, "issue_time": issue_time,
            "price": current_price, "premium": premium, "aum": aum, 
            "cagr_1y": cagr_1y, "sharpe": sharpe, "mdd": mdd, "vol_20d": vol_20d,
            "yield_ttm": yield_ttm, "dividend_rate": dividend_rate,
            "next_div_date": next_div_date, "next_div_amount": next_div_amount,
            "top_holdings": top_holdings
        })
        search_index.append({"id": ticker_id, "name": name, "category": category})
        
        time.sleep(1.0) # 遵守速率限制避免再次被鎖

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

    # 台灣時區推播 (Utc+8)
    tw_tz = timezone(timedelta(hours=8))
    tw_time = datetime.now(tw_tz).strftime('%Y-%m-%d %H:%M:%S')
    success_msg = f"更新✅ 台股全市場 ETF 數據庫與新聞動態更新成功！\n執行時間：{tw_time}"
    print(success_msg)
    send_telegram_message(success_msg)

if __name__ == "__main__":
    main()
