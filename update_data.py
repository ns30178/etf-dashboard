import requests
import pandas as pd
import json
import time
import math
import random
from datetime import datetime
from bs4 import BeautifulSoup

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

def fetch_official_data():
    tickers = {}
    nav_dict = {}
    
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", headers=HEADERS, timeout=5)
        for item in res.json():
            if str(item.get('Code', '')).startswith('00'):
                tickers[f"{item['Code']}.TW"] = item.get('Name', '')
    except:
        pass

    try:
        res = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", headers=HEADERS, timeout=5)
        for item in res.json():
            code = str(item.get('SecuritiesCompanyCode', ''))
            if code.startswith('00'):
                tickers[f"{code}.TWO"] = item.get('CompanyName', '')
    except:
        pass

    if not tickers:
        print("[警告] 官方 API 無法取得名單，啟動暴力備援清單...")
        for i in range(50, 1000):
            tickers[f"00{str(i).zfill(3)}.TW"] = "台股 ETF"
            tickers[f"00{str(i).zfill(3)}B.TWO"] = "債券 ETF"

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

def fetch_yahoo_modules(ticker):
    url = f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{ticker}?modules=summaryDetail,defaultKeyStatistics,calendarEvents"
    try:
        res = requests.get(url, headers=HEADERS, timeout=5)
        if res.status_code == 200:
            return res.json().get('quoteSummary', {}).get('result', [{}])[0]
    except:
        pass
    return {}

def fetch_yahoo_chart(ticker):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=1y&interval=1d"
    try:
        res = requests.get(url, headers=HEADERS, timeout=5)
        if res.status_code == 200:
            result = res.json().get('chart', {}).get('result', [{}])[0]
            timestamps = result.get('timestamp', [])
            indicators = result.get('indicators', {}).get('quote', [{}])[0]
            close_prices = indicators.get('close', [])
            volumes = indicators.get('volume', [])
            if close_prices and timestamps:
                df = pd.DataFrame({'Close': close_prices, 'Volume': volumes}, index=timestamps).dropna()
                return df
    except:
        pass
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
    except:
        return []

def main():
    tickers, nav_dict = fetch_official_data()
    db = {cat: [] for cat in FILE_MAP.keys()}
    news_db = {}

    print(f"啟動自研防卡死引擎，共計掃描 {len(tickers)} 檔...")

    for idx, (ticker, name) in enumerate(tickers.items()):
        if idx % 20 == 0 and idx > 0:
            print(f"進度: {idx} / {len(tickers)}")

        try:
            hist = fetch_yahoo_chart(ticker)
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

            modules = fetch_yahoo_modules(ticker)
            summary_detail = modules.get('summaryDetail', {})
            key_stats = modules.get('defaultKeyStatistics', {})
            calendar = modules.get('calendarEvents', {})

            aum_raw = summary_detail.get('totalAssets', {}).get('raw') or summary_detail.get('marketCap', {}).get('raw')
            if aum_raw:
                aum = aum_raw / 100000000
            else:
                shares = key_stats.get('sharesOutstanding', {}).get('raw')
                if shares and current_price:
                    aum = (shares * current_price) / 100000000
                else:
                    aum = None
            
            nav = nav_dict.get(ticker)
            premium = ((current_price - nav) / nav) if nav and nav > 0 else None

            if aum is None: print(f"[除錯] {ticker} 規模(AUM)原始資料缺失")
            if nav is None: print(f"[除錯] {ticker} 官方淨值(NAV)原始資料缺失")

            yield_ttm = summary_detail.get('trailingAnnualDividendYield', {}).get('raw') or key_stats.get('trailingAnnualDividendYield', {}).get('raw')
            dividend_rate = summary_detail.get('trailingAnnualDividendRate', {}).get('raw') or key_stats.get('trailingAnnualDividendRate', {}).get('raw')
            
            next_div_date = None
            next_div_amount = None
            
            ex_div_ts = calendar.get('exDividendDate', {}).get('raw')
            if ex_div_ts:
                if ex_div_ts >= datetime.now().timestamp():
                    next_div_date = datetime.fromtimestamp(ex_div_ts).strftime('%Y-%m-%d')
                    next_div_amount = key_stats.get('dividendRate', {}).get('raw')

            final_name = name if name != "台股 ETF" and name != "債券 ETF" else summary_detail.get('shortName', name)
            category = categorize_etf(final_name)
            ticker_id = ticker.with_suffix('').name if hasattr(ticker, 'with_suffix') else ticker.split('.')[0]
            
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

    print("資料庫更新完成。")

if __name__ == "__main__":
    main()
