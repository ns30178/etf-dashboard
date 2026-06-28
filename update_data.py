import yfinance as yf
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import pandas as pd
import json
import time
import math
import random
import functools
from bs4 import BeautifulSoup
from datetime import datetime

FILE_MAP = {
    "高股息": "data_high_div.json", "市值型": "data_market_cap.json",
    "主題型": "data_theme.json", "債券型": "data_bond.json",
    "槓桿型": "data_leverage.json", "反向型": "data_inverse.json",
    "綜合/其他": "data_other.json"
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def get_robust_session():
    """建立附帶自動重試與嚴格 Timeout 的安全連線池，防止 yfinance 掛機"""
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    session.headers.update(HEADERS)
    session.request = functools.partial(session.request, timeout=5)
    return session

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

def fetch_etf_list_and_nav():
    tickers = {}
    nav_dict = {}
    
    # 1. 透過第三方 FinMind 獲取台股 ETF 名單 (破解政府 IP 封鎖)
    try:
        url = "https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInfo"
        res = requests.get(url, headers=HEADERS, timeout=10)
        if res.status_code == 200:
            for item in res.json().get('data', []):
                if item.get('industry_category') == 'ETF':
                    code = str(item.get('stock_id'))
                    name = str(item.get('stock_name'))
                    market = str(item.get('type')).lower()
                    if market == 'twse': tickers[f"{code}.TW"] = name
                    elif market == 'tpex': tickers[f"{code}.TWO"] = name
    except: pass

    # 備用核心清單
    if not tickers:
        core_etfs = ["0050.TW", "0056.TW", "00878.TW", "00929.TW", "00919.TW", "00713.TW", "00915.TW", "00939.TW", "00940.TW", "006208.TW", "00679B.TWO", "00687B.TWO", "00937B.TWO"]
        for t in core_etfs: tickers[t] = t.split('.')[0]

    # 2. 獲取官方淨值 (若 GitHub 在美國被擋，則會為空，由後方 Yahoo 淨值備援)
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/MI_101", headers=HEADERS, timeout=5)
        for item in res.json():
            try: nav_dict[f"{item.get('Code')}.TW"] = float(str(item.get('Nav', '0')).replace(',', ''))
            except: pass
    except: pass

    try:
        res = requests.get("https://www.tpex.org.tw/web/etf/g_info/fund_info_prb.php?l=zh-tw", headers=HEADERS, timeout=5)
        for row in res.json().get('aaData', []):
            try: nav_dict[f"{row[0]}.TWO"] = float(str(row[3]).replace(',', ''))
            except: pass
    except: pass

    return tickers, nav_dict

def fetch_yahoo_news(ticker_id, session):
    try:
        res = session.get(f"https://tw.stock.yahoo.com/quote/{ticker_id}/news", timeout=5)
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
    tickers, nav_dict = fetch_etf_list_and_nav()
    robust_session = get_robust_session()
    db = {cat: [] for cat in FILE_MAP.keys()}
    news_db = {}

    print(f"🚀 啟動完整量化引擎，共計掃描 {len(tickers)} 檔...")

    for idx, (ticker, name) in enumerate(tickers.items()):
        if idx % 20 == 0 and idx > 0:
            print(f"⏳ 進度: {idx} / {len(tickers)}")

        try:
            tk = yf.Ticker(ticker, session=robust_session)
            
            # 1. 股價與技術指標
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

            # 2. 基本面、配息與規模 (AUM)
            info = tk.info
            
            # 規模計算：首選官方總資產 -> 其次市值 -> 最終備援(股數*市價)
            aum = info.get('totalAssets') or info.get('marketCap')
            if aum:
                aum = aum / 100000000
            else:
                try:
                    shares = info.get('sharesOutstanding') or tk.fast_info.shares
                    aum = (shares * current_price) / 100000000 if shares and current_price else None
                except:
                    aum = None

            # 淨值與折溢價：首選官方抓取 -> 其次 Yahoo 報價
            nav = nav_dict.get(ticker) or info.get('navPrice')
            premium = ((current_price - nav) / nav) if nav and nav > 0 else None

            # 配息資料擷取
            yield_ttm = info.get('trailingAnnualDividendYield')
            dividend_rate = info.get('trailingAnnualDividendRate')
            
            next_div_date = None
            next_div_amount = None
            ex_div_ts = info.get('exDividendDate')
            if ex_div_ts and ex_div_ts >= datetime.now().timestamp():
                next_div_date = datetime.fromtimestamp(ex_div_ts).strftime('%Y-%m-%d')
                next_div_amount = info.get('dividendRate') or info.get('lastDividendValue')

            # 資料庫匯整
            final_name = info.get('shortName', name) if name.startswith("00") else name
            category = categorize_etf(final_name)
            ticker_id = ticker.split('.')[0]
            
            db[category].append({
                "id": ticker_id, "name": final_name, "price": current_price, "premium": premium,
                "aum": aum, "cagr_1y": cagr_1y, "sharpe": sharpe, "mdd": mdd, "vol_20d": vol_20d,
                "yield_ttm": yield_ttm, "dividend_rate": dividend_rate,
                "next_div_date": next_div_date, "next_div_amount": next_div_amount
            })
            
            # 新聞抓取
            if vol_20d > 100:
                news_db[ticker_id] = fetch_yahoo_news(ticker_id, robust_session)
                
        except Exception:
            pass # 發生無效代號直接跳過
            
        time.sleep(random.uniform(0.1, 0.4))

    # 匯出資料庫
    for cat, data in db.items():
        clean_data = sanitize_json(data)
        with open(FILE_MAP[cat], "w", encoding="utf-8") as f:
            json.dump(clean_data, f, ensure_ascii=False, indent=2)

    # 寫入靜態 IPO 與其新聞
    ipo_db = [
        {"id": "00946", "name": "群益科技高息成長", "issueDate": "2026-05-09", "price": 10.0, "fee": 0.30, "freq": "月配", "topHoldings": "聯發科, 瑞昱, 聯詠"},
        {"id": "00947", "name": "台新臺灣IC設計", "issueDate": "2026-06-12", "price": 15.0, "fee": 0.40, "freq": "季配", "topHoldings": "台積電, 聯發科, 瑞昱"}
    ]
    for ipo in ipo_db:
        news_db[ipo['id']] = fetch_yahoo_news(ipo['id'], robust_session)
        
    with open("data_ipo.json", "w", encoding="utf-8") as f:
        json.dump(ipo_db, f, ensure_ascii=False, indent=2)

    with open("data_news.json", "w", encoding="utf-8") as f:
        json.dump(news_db, f, ensure_ascii=False, indent=2)

    print("🎉 資料庫更新完成。")

if __name__ == "__main__":
    main()
