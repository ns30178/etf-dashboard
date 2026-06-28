import yfinance as yf
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import pandas as pd
import json
import time
import math
import random
from bs4 import BeautifulSoup
from datetime import datetime

FILE_MAP = {
    "高股息": "data_high_div.json", "市值型": "data_market_cap.json",
    "主題型": "data_theme.json", "債券型": "data_bond.json",
    "槓桿型": "data_leverage.json", "反向型": "data_inverse.json",
    "綜合/其他": "data_other.json"
}

def get_robust_session():
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
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

def fetch_official_data():
    tickers = {}
    nav_dict = {}
    headers = {"User-Agent": "Mozilla/5.0"}
    
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", headers=headers, timeout=10)
        for item in res.json():
            if str(item.get('Code', '')).startswith('00'):
                tickers[f"{item['Code']}.TW"] = item.get('Name', '')
    except Exception:
        pass

    try:
        res = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", headers=headers, timeout=10)
        for item in res.json():
            code = str(item.get('SecuritiesCompanyCode', ''))
            if code.startswith('00'):
                tickers[f"{code}.TWO"] = item.get('CompanyName', '')
    except Exception:
        pass

    if not tickers:
        for i in range(50, 1000):
            tickers[f"00{str(i).zfill(3)}.TW"] = "台股 ETF"
            tickers[f"00{str(i).zfill(3)}B.TWO"] = "債券 ETF"

    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/MI_101", headers=headers, timeout=10)
        for item in res.json():
            try: nav_dict[f"{item.get('Code')}.TW"] = float(str(item.get('Nav', '0')).replace(',', ''))
            except: pass
    except: pass

    try:
        res = requests.get("https://www.tpex.org.tw/web/etf/g_info/fund_info_prb.php?l=zh-tw", headers=headers, timeout=10)
        for row in res.get('aaData', []):
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
    except:
        return []

def main():
    tickers, nav_dict = fetch_official_data()
    robust_session = get_robust_session()
    db = {cat: [] for cat in FILE_MAP.keys()}
    news_db = {}

    for idx, (ticker, name) in enumerate(tickers.items()):
        try:
            tk = yf.Ticker(ticker, session=robust_session)
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

            yield_ttm = info.get('trailingAnnualDividendYield')
            dividend_rate = info.get('trailingAnnualDividendRate')
            
            next_div_date = None
            next_div_amount = None
            
            ex_div_ts = info.get('exDividendDate')
            if ex_div_ts:
                if ex_div_ts >= datetime.now().timestamp():
                    next_div_date = datetime.fromtimestamp(ex_div_ts).strftime('%Y-%m-%d')
                    next_div_amount = info.get('dividendRate') or info.get('lastDividendValue')

            final_name = name if name != "台股 ETF" and name != "債券 ETF" else info.get('shortName', name)
            category = categorize_etf(final_name)
            ticker_id = ticker.split('.')[0]
            
            db[category].append({
                "id": ticker_id,
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
            
            # 日均量大於 100 張才抓取新聞
            if vol_20d > 100:
                news_db[ticker_id] = fetch_yahoo_news(ticker_id, robust_session)
            
        except Exception:
            pass
            
        time.sleep(random.uniform(0.2, 0.8))

    for cat, data in db.items():
        clean_data = sanitize_json(data)
        with open(FILE_MAP[cat], "w", encoding="utf-8") as f:
            json.dump(clean_data, f, ensure_ascii=False, indent=2)

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

if __name__ == "__main__":
    main()
