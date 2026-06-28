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

def get_robust_session():
    """建立附帶自動重試與嚴格 Timeout 的安全連線池"""
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    # 安全注入 Timeout，防止掛機
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

def fetch_official_data():
    tickers = {}
    nav_dict = {}
    headers = {"User-Agent": "Mozilla/5.0"}
    
    # 嘗試獲取官方名單
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", headers=headers, timeout=5)
        for item in res.json():
            if str(item.get('Code', '')).startswith('00'):
                tickers[f"{item['Code']}.TW"] = item.get('Name', '')
    except: pass

    try:
        res = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", headers=headers, timeout=5)
        for item in res.json():
            code = str(item.get('SecuritiesCompanyCode', ''))
            if code.startswith('00'):
                tickers[f"{code}.TWO"] = item.get('CompanyName', '')
    except: pass

    # 若 GitHub IP 被鎖，啟動暴力備援名單 (限縮範圍加快速度)
    if not tickers:
        print("[警告] 官方 API 封鎖連線，啟用備援名單...")
        for i in range(50, 950):
            tickers[f"00{str(i).zfill(3)}.TW"] = f"00{str(i).zfill(3)}"
            tickers[f"00{str(i).zfill(3)}B.TWO"] = f"00{str(i).zfill(3)}B"

    # 嘗試獲取官方淨值
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/MI_101", headers=headers, timeout=5)
        for item in res.json():
            try: nav_dict[f"{item.get('Code')}.TW"] = float(str(item.get('Nav', '0')).replace(',', ''))
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

    print(f"啟動穿透型量化引擎，共計掃描 {len(tickers)} 檔...")

    for idx, (ticker, name) in enumerate(tickers.items()):
        if idx % 50 == 0 and idx > 0:
            print(f"進度: {idx} / {len(tickers)}")

        try:
            tk = yf.Ticker(ticker, session=robust_session)
            
            # 1. 抓取股價與技術指標
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

            # 2. 啟動雙層資料防護：先抓 info，若被擋則用 fast_info 暴力反推
            info = tk.info
            aum = info.get('totalAssets') or info.get('marketCap')
            
            if aum:
                aum = aum / 100000000
            else:
                try:
                    # 使用 fast_info 繞過 Yahoo Crumb 防火牆
                    shares = info.get('sharesOutstanding') or tk.fast_info.shares
                    if shares and current_price:
                        aum = (shares * current_price) / 100000000
                except:
                    aum = None

            # 3. 淨值與折溢價備援：官方淨值優先，Yahoo 淨值次之
            nav = nav_dict.get(ticker) or info.get('navPrice')
            premium = ((current_price - nav) / nav) if nav and nav > 0 else None

            # 4. 配息資料
            yield_ttm = info.get('trailingAnnualDividendYield')
            dividend_rate = info.get('trailingAnnualDividendRate')
            
            next_div_date = None
            next_div_amount = None
            ex_div_ts = info.get('exDividendDate')
            if ex_div_ts and ex_div_ts >= datetime.now().timestamp():
                next_div_date = datetime.fromtimestamp(ex_div_ts).strftime('%Y-%m-%d')
                next_div_amount = info.get('dividendRate') or info.get('lastDividendValue')

            # 寫入資料庫
            final_name = info.get('shortName', name) if name.startswith("00") else name
            category = categorize_etf(final_name)
            ticker_id = ticker.split('.')[0]
            
            db[category].append({
                "id": ticker_id, "name": final_name, "price": current_price, "premium": premium,
                "aum": aum, "cagr_1y": cagr_1y, "sharpe": sharpe, "mdd": mdd, "vol_20d": vol_20d,
                "yield_ttm": yield_ttm, "dividend_rate": dividend_rate,
                "next_div_date": next_div_date, "next_div_amount": next_div_amount
            })
            
            # 抓取新聞
            if vol_20d > 100:
                news_db[ticker_id] = fetch_yahoo_news(ticker_id, robust_session)
            
        except Exception:
            pass # 發生無效代號或其他錯誤直接跳過，不印出干擾日誌
            
        # 安全休眠防封鎖
        time.sleep(random.uniform(0.1, 0.4))

    # 輸出資料
    for cat, data in db.items():
        clean_data = sanitize_json(data)
        with open(FILE_MAP[cat], "w", encoding="utf-8") as f:
            json.dump(clean_data, f, ensure_ascii=False, indent=2)

    # 寫入靜態 IPO 資料
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

    print("✅ 資料庫全數更新完畢。")

if __name__ == "__main__":
    main()
