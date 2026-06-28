import os
import requests
import pandas as pd
import json
import time
import math
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

# 讀取 GitHub Secrets 金鑰
FINMIND_TOKEN = os.environ.get("FINMIND_TOKEN", "")
FUGLE_KEY = os.environ.get("FUGLE_API_KEY", "")

FILE_MAP = {
    "高股息": "data_high_div.json", "市值型": "data_market_cap.json",
    "主題型": "data_theme.json", "債券型": "data_bond.json",
    "槓桿型": "data_leverage.json", "反向型": "data_inverse.json",
    "綜合/其他": "data_other.json"
}

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

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
    tickers, nav_dict = {}, {}
    # 1. 透過 FinMind 獲取 ETF 名單
    try:
        url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInfo&token={FINMIND_TOKEN}"
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            for item in res.json().get('data', []):
                if item.get('industry_category') == 'ETF':
                    code = str(item.get('stock_id'))
                    tickers[code] = {"name": str(item.get('stock_name')), "market": item.get('type').lower()}
    except: pass
    
    # 2. 獲取官方淨值 (若 GitHub IP 被擋則依靠後續 Yahoo 備援)
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/MI_101", headers=HEADERS, timeout=5)
        for item in res.json(): nav_dict[item.get('Code')] = float(str(item.get('Nav', '0')).replace(',', ''))
    except: pass
    try:
        res = requests.get("https://www.tpex.org.tw/web/etf/g_info/fund_info_prb.php?l=zh-tw", headers=HEADERS, timeout=5)
        for row in res.json().get('aaData', []): nav_dict[row[0]] = float(str(row[3]).replace(',', ''))
    except: pass

    return tickers, nav_dict

def fetch_fugle_candles(symbol):
    """Fugle 富果 API 抓取精確日 K 線 (計算報酬率、波動用)"""
    url = f"https://api.fugle.tw/marketdata/v1.0/stock/historical/candles/{symbol}?timeframe=D"
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
    return pd.DataFrame()

def fetch_finmind_dividend(symbol):
    """FinMind API 獲取真實除息紀錄"""
    start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
    url = f"https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockDividend&data_id={symbol}&start_date={start_date}&token={FINMIND_TOKEN}"
    try:
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            return res.json().get('data', [])
    except: pass
    return []

def fetch_yahoo_quote(symbol, market):
    """Yahoo API 作為規模與淨值備援 (不使用 Crumb)"""
    ticker = f"{symbol}.TW" if market == 'twse' else f"{symbol}.TWO"
    url = f"https://query2.finance.yahoo.com/v7/finance/quote?symbols={ticker}"
    try:
        res = requests.get(url, headers=HEADERS, timeout=5)
        if res.status_code == 200: return res.json().get('quoteResponse', {}).get('result', [{}])[0]
    except: pass
    return {}

def fetch_yahoo_news(symbol):
    try:
        res = requests.get(f"https://tw.stock.yahoo.com/quote/{symbol}/news", headers=HEADERS, timeout=5)
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
    if not tickers: return print("❌ 名單獲取失敗。")

    db = {cat: [] for cat in FILE_MAP.keys()}
    news_db = {}
    search_index = []

    print(f"🚀 啟動 API 商業端點引擎，共計 {len(tickers)} 檔...")

    for idx, (ticker_id, info) in enumerate(tickers.items()):
        if idx % 30 == 0 and idx > 0: print(f"⏳ 進度: {idx} / {len(tickers)}")

        try:
            # 1. 行情數據 (Fugle API)
            hist = fetch_fugle_candles(ticker_id)
            if hist.empty or len(hist) < 20: continue

            current_price = float(hist['close'].iloc[-1])
            vol_20d = int(hist['volume'].tail(20).mean() / 1000)

            if len(hist) >= 200:
                cagr_1y = float((current_price - hist['close'].iloc[0]) / hist['close'].iloc[0])
                max_p = hist['close'].cummax()
                mdd = float(((hist['close'] - max_p) / max_p).min())
                daily_ret = hist['close'].pct_change().dropna()
                sharpe = float((daily_ret.mean() / daily_ret.std()) * (252**0.5)) if daily_ret.std() != 0 else None
            else:
                cagr_1y, sharpe, mdd = None, None, None

            # 2. 配息數據 (FinMind API)
            div_data = fetch_finmind_dividend(ticker_id)
            ttm_div = 0.0
            next_div_date, next_div_amount = None, None
            now = datetime.now()
            
            for record in div_data:
                ex_date_str = record.get('DividendYieldDate', '1900-01-01')
                amt = float(record.get('CashEarningsDistribution', 0) or 0)
                try:
                    ex_date = datetime.strptime(ex_date_str, '%Y-%m-%d')
                    if ex_date <= now: ttm_div += amt
                    elif ex_date > now:
                        next_div_date = ex_date_str
                        next_div_amount = amt
                except: pass

            dividend_rate = ttm_div if ttm_div > 0 else None
            yield_ttm = (ttm_div / current_price) if (ttm_div and current_price) else None

            # 3. 規模與折溢價 (TWSE/TPEX 優先，Yahoo 備援)
            quote = fetch_yahoo_quote(ticker_id, info['market'])
            aum_raw = quote.get('marketCap')
            if aum_raw: aum = aum_raw / 100000000
            else:
                shares = quote.get('sharesOutstanding')
                aum = (shares * current_price) / 100000000 if shares and current_price else None

            nav = nav_dict.get(ticker_id) or quote.get('navPrice') or quote.get('regularMarketPrice')
            premium = ((current_price - nav) / nav) if nav and nav > 0 else None

            # 寫入分類與資料庫
            category = categorize_etf(info['name'])
            db[category].append({
                "id": ticker_id, "name": info['name'], "price": current_price, "premium": premium,
                "aum": aum, "cagr_1y": cagr_1y, "sharpe": sharpe, "mdd": mdd, "vol_20d": vol_20d,
                "yield_ttm": yield_ttm, "dividend_rate": dividend_rate,
                "next_div_date": next_div_date, "next_div_amount": next_div_amount
            })
            
            # 建構極輕量全市場搜尋索引
            search_index.append({"id": ticker_id, "name": info['name'], "category": category})
            
            if vol_20d > 100: news_db[ticker_id] = fetch_yahoo_news(ticker_id)
            
        except Exception as e: pass
            
        # 安全休眠，遵守 Fugle 60 requests/minute 限制
        time.sleep(1.0)

    # 輸出所有 JSON
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

    print("🎉 資料庫與搜尋引擎更新完成。")

if __name__ == "__main__":
    main()
