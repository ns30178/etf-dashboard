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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
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
    """直接從證交所/櫃買中心主網站抓取『真實存在』的 ETF 名單與淨值，捨棄 1800 檔暴力盲掃"""
    tickers = {}
    nav_dict = {}
    print("🔍 正在獲取台股全市場 ETF 精確名單與官方淨值...")

    # TWSE (上市)
    try:
        res = requests.get("https://www.twse.com.tw/rwd/zh/fund/MI_101?response=json", headers=HEADERS, timeout=10)
        for row in res.json().get('data', []):
            code, name = str(row[0]), str(row[1])
            if code.startswith('00'):
                tickers[f"{code}.TW"] = name
                try: nav_dict[f"{code}.TW"] = float(str(row[4]).replace(',', ''))
                except: pass
    except Exception as e:
        print(f"[警告] TWSE 獲取失敗: {e}")

    # TPEX (上櫃)
    try:
        res = requests.get("https://www.tpex.org.tw/web/etf/g_info/fund_info_prb.php?l=zh-tw", headers=HEADERS, timeout=10)
        for row in res.json().get('aaData', []):
            code, name = str(row[0]), str(row[1])
            if code.startswith('00'):
                tickers[f"{code}.TWO"] = name
                try: nav_dict[f"{code}.TWO"] = float(str(row[3]).replace(',', ''))
                except: pass
    except Exception as e:
        print(f"[警告] TPEX 獲取失敗: {e}")

    return tickers, nav_dict

def fetch_yahoo_quote(ticker):
    """使用 Yahoo v7 輕量 API，無需 Crumb 即可取得規模與配息"""
    url = f"https://query2.finance.yahoo.com/v7/finance/quote?symbols={ticker}"
    try:
        res = requests.get(url, headers=HEADERS, timeout=5)
        if res.status_code == 200:
            return res.json().get('quoteResponse', {}).get('result', [{}])[0]
    except: pass
    return {}

def fetch_yahoo_chart(ticker):
    """抓取歷史股價計算技術指標"""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=1y&interval=1d"
    try:
        res = requests.get(url, headers=HEADERS, timeout=5)
        if res.status_code == 200:
            result = res.json().get('chart', {}).get('result', [{}])[0]
            timestamps = result.get('timestamp', [])
            indicators = result.get('indicators', {}).get('quote', [{}])[0]
            if timestamps and indicators.get('close'):
                return pd.DataFrame({'Close': indicators['close'], 'Volume': indicators.get('volume', [])}, index=timestamps).dropna()
    except: pass
    return pd.DataFrame()

def fetch_yahoo_news(ticker_id):
    try:
        res = requests.get(f"https://tw.stock.yahoo.com/quote/{ticker_id}/news", headers=HEADERS, timeout=5)
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
    if not tickers:
        print("❌ 無法取得任何 ETF 名單，為避免無限空轉浪費額度，程式安全終止。")
        return

    db = {cat: [] for cat in FILE_MAP.keys()}
    news_db = {}
    print(f"🚀 啟動極速量化引擎，共計掃描 {len(tickers)} 檔...")

    for idx, (ticker, name) in enumerate(tickers.items()):
        if idx % 50 == 0 and idx > 0:
            print(f"⏳ 進度: {idx} / {len(tickers)}")

        try:
            # 1. 抓取股價與技術指標
            hist = fetch_yahoo_chart(ticker)
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

            # 2. 抓取基礎資訊 (規模、配息)
            quote = fetch_yahoo_quote(ticker)
            
            aum_raw = quote.get('marketCap')
            if aum_raw:
                aum = aum_raw / 100000000
            else:
                shares = quote.get('sharesOutstanding')
                aum = (shares * current_price) / 100000000 if shares and current_price else None

            nav = nav_dict.get(ticker) or quote.get('regularMarketPrice') # 極端備援
            premium = ((current_price - nav) / nav) if nav and nav > 0 else None

            yield_ttm = quote.get('trailingAnnualDividendYield')
            dividend_rate = quote.get('trailingAnnualDividendRate')
            
            next_div_date = None
            next_div_amount = quote.get('dividendRate')
            ex_div_ts = quote.get('exDividendDate')
            
            if ex_div_ts and ex_div_ts >= datetime.now().timestamp():
                next_div_date = datetime.fromtimestamp(ex_div_ts).strftime('%Y-%m-%d')
            else:
                next_div_amount = None # 若無未來發放日，則清空預測金額

            # 寫入資料庫
            final_name = quote.get('shortName', name) if name.startswith("00") else name
            category = categorize_etf(final_name)
            ticker_id = ticker.split('.')[0]
            
            db[category].append({
                "id": ticker_id, "name": final_name, "price": current_price, "premium": premium,
                "aum": aum, "cagr_1y": cagr_1y, "sharpe": sharpe, "mdd": mdd, "vol_20d": vol_20d,
                "yield_ttm": yield_ttm, "dividend_rate": dividend_rate,
                "next_div_date": next_div_date, "next_div_amount": next_div_amount
            })
            
            # 抓取新聞 (日均量 > 100)
            if vol_20d > 100:
                news_db[ticker_id] = fetch_yahoo_news(ticker_id)
            
        except Exception:
            pass
            
        time.sleep(random.uniform(0.1, 0.3))

    # 輸出過濾後的資料
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
        news_db[ipo['id']] = fetch_yahoo_news(ipo['id'])
        
    with open("data_ipo.json", "w", encoding="utf-8") as f:
        json.dump(ipo_db, f, ensure_ascii=False, indent=2)

    with open("data_news.json", "w", encoding="utf-8") as f:
        json.dump(news_db, f, ensure_ascii=False, indent=2)

    print("🎉 資料庫更新完成。")

if __name__ == "__main__":
    main()
