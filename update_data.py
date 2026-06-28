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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json"
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

def fetch_etf_list_and_nav():
    tickers = {}
    nav_dict = {}
    session = requests.Session()
    
    # 1. 透過第三方 API (FinMind) 獲取全市場 ETF，繞過政府對 GitHub IP 的封鎖
    try:
        url = "https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInfo"
        res = session.get(url, headers=HEADERS, timeout=10)
        if res.status_code == 200:
            for item in res.json().get('data', []):
                if item.get('industry_category') == 'ETF':
                    code = str(item.get('stock_id'))
                    name = str(item.get('stock_name'))
                    market = str(item.get('type')).lower()
                    if market == 'twse': tickers[f"{code}.TW"] = name
                    elif market == 'tpex': tickers[f"{code}.TWO"] = name
    except Exception as e:
        print(f"[警告] 名單獲取失敗: {e}")

    # 備用清單
    if not tickers:
        core_etfs = ["0050.TW", "0056.TW", "00878.TW", "00929.TW", "00919.TW", "00713.TW", "00915.TW", "00939.TW", "00940.TW", "006208.TW", "00679B.TWO", "00687B.TWO"]
        for t in core_etfs: tickers[t] = t.split('.')[0]

    # 2. 抓取官方淨值 (直接請求網頁版 API)
    try:
        res = session.get("https://www.twse.com.tw/rwd/zh/fund/MI_101?response=json", headers=HEADERS, timeout=5)
        for row in res.json().get('data', []):
            try: nav_dict[f"{row[0]}.TW"] = float(str(row[4]).replace(',', ''))
            except: pass
    except: pass

    try:
        res = session.get("https://www.tpex.org.tw/web/etf/g_info/fund_info_prb.php?l=zh-tw", headers=HEADERS, timeout=5)
        for row in res.json().get('aaData', []):
            try: nav_dict[f"{row[0]}.TWO"] = float(str(row[3]).replace(',', ''))
            except: pass
    except: pass

    return tickers, nav_dict

def fetch_yahoo_data(ticker, session):
    """完全棄用 yfinance，使用原生 requests 抓取 Yahoo 底層 API，保證不卡死且無 Crumb 限制"""
    data = {}
    
    # 1. Quote 資訊 (抓取股數、市值、最新報價、預期配息)
    try:
        q_url = f"https://query2.finance.yahoo.com/v7/finance/quote?symbols={ticker}"
        q_res = session.get(q_url, headers=HEADERS, timeout=5)
        if q_res.status_code == 200:
            quote = q_res.json().get('quoteResponse', {}).get('result', [{}])[0]
            data['price'] = quote.get('regularMarketPrice')
            data['shares'] = quote.get('sharesOutstanding')
            data['mcap'] = quote.get('marketCap')
            data['q_yield'] = quote.get('trailingAnnualDividendYield')
            data['q_div_rate'] = quote.get('trailingAnnualDividendRate')
            data['q_ex_div'] = quote.get('exDividendDate')
            data['q_next_div'] = quote.get('dividendRate')
    except: pass

    # 2. Chart 與 歷史配息事件 (計算技術指標、實質 TTM 配息)
    try:
        c_url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=1y&interval=1d&events=div"
        c_res = session.get(c_url, headers=HEADERS, timeout=5)
        if c_res.status_code == 200:
            result = c_res.json().get('chart', {}).get('result', [{}])[0]
            
            # K線圖數據
            timestamps = result.get('timestamp', [])
            indicators = result.get('indicators', {}).get('quote', [{}])[0]
            if timestamps and indicators.get('close'):
                data['hist'] = pd.DataFrame({
                    'Close': indicators['close'],
                    'Volume': indicators.get('volume', [])
                }, index=timestamps).dropna()
            
            # 歷史配息紀錄
            data['dividends'] = result.get('events', {}).get('dividends', {})
    except: pass

    return data

def fetch_yahoo_news(ticker_id, session):
    try:
        res = session.get(f"https://tw.stock.yahoo.com/quote/{ticker_id}/news", headers=HEADERS, timeout=5)
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
    session = requests.Session()
    db = {cat: [] for cat in FILE_MAP.keys()}
    news_db = {}

    print(f"🚀 啟動無依賴原生量化引擎，共計 {len(tickers)} 檔...")

    for idx, (ticker, name) in enumerate(tickers.items()):
        if idx % 30 == 0 and idx > 0:
            print(f"⏳ 進度: {idx} / {len(tickers)}")

        try:
            raw_data = fetch_yahoo_data(ticker, session)
            
            # 解析歷史資料
            hist = raw_data.get('hist')
            if hist is None or hist.empty or len(hist) < 20: continue

            current_price = raw_data.get('price') or float(hist['Close'].iloc[-1])
            vol_20d = int(hist['Volume'].tail(20).mean() / 1000)

            # 技術指標計算
            if len(hist) >= 200:
                cagr_1y = float((current_price - hist['Close'].iloc[0]) / hist['Close'].iloc[0])
                max_p = hist['Close'].cummax()
                mdd = float(((hist['Close'] - max_p) / max_p).min())
                daily_ret = hist['Close'].pct_change().dropna()
                sharpe = float((daily_ret.mean() / daily_ret.std()) * (252**0.5)) if daily_ret.std() != 0 else None
            else:
                cagr_1y, sharpe, mdd = None, None, None

            # 規模與折溢價
            aum = raw_data.get('mcap')
            if aum:
                aum = aum / 100000000
            elif raw_data.get('shares') and current_price:
                aum = (raw_data['shares'] * current_price) / 100000000
                
            nav = nav_dict.get(ticker)
            premium = ((current_price - nav) / nav) if nav and nav > 0 else None

            # 實質配息與未來配息計算
            now_ts = datetime.now().timestamp()
            ttm_div = 0.0
            next_div_date = None
            next_div_amount = None

            if raw_data.get('dividends'):
                for ts_str, div_info in raw_data['dividends'].items():
                    ts = int(ts_str)
                    amt = float(div_info['amount'])
                    # 過去一年內除息的總和
                    if now_ts - 31536000 <= ts <= now_ts:
                        ttm_div += amt
                    # 未來已公告除息
                    elif ts > now_ts:
                        next_div_date = datetime.fromtimestamp(ts).strftime('%Y-%m-%d')
                        next_div_amount = amt

            # 如果歷史配息沒抓到，使用 Quote 的備援資料
            dividend_rate = ttm_div if ttm_div > 0 else raw_data.get('q_div_rate')
            yield_ttm = (dividend_rate / current_price) if (dividend_rate and current_price) else raw_data.get('q_yield')

            if not next_div_date:
                q_ex = raw_data.get('q_ex_div')
                if q_ex and q_ex > now_ts:
                    next_div_date = datetime.fromtimestamp(q_ex).strftime('%Y-%m-%d')
                    next_div_amount = raw_data.get('q_next_div')

            # 寫入資料
            final_name = name
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
                news_db[ticker_id] = fetch_yahoo_news(ticker_id, session)

        except Exception as e:
            pass
            
        time.sleep(random.uniform(0.1, 0.3))

    # 輸出過濾 JSON
    for cat, data in db.items():
        clean_data = sanitize_json(data)
        with open(FILE_MAP[cat], "w", encoding="utf-8") as f:
            json.dump(clean_data, f, ensure_ascii=False, indent=2)

    # IPO 靜態檔案
    ipo_db = [
        {"id": "00946", "name": "群益科技高息成長", "issueDate": "2026-05-09", "price": 10.0, "fee": 0.30, "freq": "月配", "topHoldings": "聯發科, 瑞昱, 聯詠"},
        {"id": "00947", "name": "台新臺灣IC設計", "issueDate": "2026-06-12", "price": 15.0, "fee": 0.40, "freq": "季配", "topHoldings": "台積電, 聯發科, 瑞昱"}
    ]
    for ipo in ipo_db:
        news_db[ipo['id']] = fetch_yahoo_news(ipo['id'], session)
        
    with open("data_ipo.json", "w", encoding="utf-8") as f:
        json.dump(ipo_db, f, ensure_ascii=False, indent=2)

    with open("data_news.json", "w", encoding="utf-8") as f:
        json.dump(news_db, f, ensure_ascii=False, indent=2)

    print("🎉 資料庫更新完成。")

if __name__ == "__main__":
    main()
