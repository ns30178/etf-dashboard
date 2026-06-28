import yfinance as yf
import requests
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

def fetch_etf_list():
    """透過 FinMind API 獲取 ETF 名單，繞過 TWSE/TPEX 對 GitHub IP 的封鎖"""
    tickers = {}
    try:
        url = "https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInfo"
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            for item in res.json().get('data', []):
                if item.get('industry_category') == 'ETF':
                    code = str(item.get('stock_id'))
                    if code.startswith('00'):
                        market = str(item.get('type')).lower()
                        suffix = '.TW' if market == 'twse' else '.TWO'
                        tickers[f"{code}{suffix}"] = str(item.get('stock_name'))
    except:
        pass
    
    # 極端備援名單
    if not tickers:
        core_etfs = ["0050.TW", "0056.TW", "00878.TW", "00929.TW", "00919.TW", "00713.TW", "00679B.TWO", "00687B.TWO"]
        for t in core_etfs: tickers[t] = t.split('.')[0]
    return tickers

def fetch_yahoo_news(ticker_id):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(f"https://tw.stock.yahoo.com/quote/{ticker_id}/news", headers=headers, timeout=5)
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
    db = {cat: [] for cat in FILE_MAP.keys()}
    news_db = {}
    
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    for idx, (ticker, name) in enumerate(tickers.items()):
        try:
            tk = yf.Ticker(ticker, session=session)
            
            # 1. 抓取歷史股價與技術指標
            hist = tk.history(period="1y")
            if hist.empty or len(hist) < 20: continue

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

            # 2. 透過 fast_info 強制取得發行股數，無懼 Crumb 封鎖
            fast = tk.fast_info
            shares = fast.shares if hasattr(fast, 'shares') else None
            aum = (shares * current_price) / 100000000 if (shares and not math.isnan(shares)) else None

            # 3. 透過 dividends 物件反推實質配息 (繞過 info 的封鎖)
            divs = tk.dividends
            dividend_rate = None
            yield_ttm = None
            next_div_date = None
            next_div_amount = None

            if not divs.empty:
                now = pd.Timestamp.now(tz=divs.index.tz)
                one_year_ago = now - pd.DateOffset(years=1)
                
                # 計算近 12 個月配息總額
                past_divs = divs[(divs.index >= one_year_ago) & (divs.index <= now)]
                if not past_divs.empty:
                    dividend_rate = float(past_divs.sum())
                    yield_ttm = dividend_rate / current_price

                # 判斷未來已公告配息
                future_divs = divs[divs.index > now]
                if not future_divs.empty:
                    next_div_date = future_divs.index[0].strftime('%Y-%m-%d')
                    next_div_amount = float(future_divs.iloc[0])

            # 4. 淨值與折溢價備援
            info = tk.info
            nav = info.get('navPrice')
            premium = ((current_price - nav) / nav) if (nav and nav > 0) else None

            final_name = info.get('shortName', name) if name.startswith("00") else name
            category = categorize_etf(final_name)
            ticker_id = ticker.split('.')[0]
            
            db[category].append({
                "id": ticker_id, "name": final_name, "price": current_price, "premium": premium,
                "aum": aum, "cagr_1y": cagr_1y, "sharpe": sharpe, "mdd": mdd, "vol_20d": vol_20d,
                "yield_ttm": yield_ttm, "dividend_rate": dividend_rate,
                "next_div_date": next_div_date, "next_div_amount": next_div_amount
            })
            
            if vol_20d > 100:
                news_db[ticker_id] = fetch_yahoo_news(ticker_id)
            
        except Exception:
            pass
            
        time.sleep(random.uniform(0.1, 0.3))

    # 輸出
    for cat, data in db.items():
        clean_data = sanitize_json(data)
        with open(FILE_MAP[cat], "w", encoding="utf-8") as f:
            json.dump(clean_data, f, ensure_ascii=False, indent=2)

    ipo_db = [
        {"id": "00946", "name": "群益科技高息成長", "issueDate": "2026-05-09", "price": 10.0, "fee": 0.30, "freq": "月配", "topHoldings": "聯發科, 瑞昱, 聯詠"},
        {"id": "00947", "name": "台新臺灣IC設計", "issueDate": "2026-06-12", "price": 15.0, "fee": 0.40, "freq": "季配", "topHoldings": "台積電, 聯發科, 瑞昱"}
    ]
    for ipo in ipo_db:
        news_db[ipo['id']] = fetch_yahoo_news(ipo['id'])
        
    with open("data_ipo.json", "w", encoding="utf-8") as f:
        json.dump(ipo_db, f, ensure_ascii=False, indent=2)

    with open("data_news.json", "w", encoding="utf-8") as f:
        json.dump(news_db, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
