import yfinance as yf
import pandas as pd
import json
import cloudscraper
from bs4 import BeautifulSoup
import time
from datetime import datetime

# 初始化繞過 Cloudflare 的爬蟲工具
scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})

def fetch_yahoo_news(ticker):
    """擷取特定標的的新聞"""
    try:
        url = f"https://tw.stock.yahoo.com/quote/{ticker}.TW/news"
        resp = scraper.get(url, timeout=10)
        soup = BeautifulSoup(resp.text, 'html.parser')
        news_items = []
        for a in soup.find_all('a', href=True, limit=10):
            title = a.text.strip()
            if len(title) > 10 and 'http' in a['href']:
                news_items.append({"title": title, "link": a['href'], "date": datetime.today().strftime('%Y-%m-%d')})
            if len(news_items) >= 3:
                break
        return news_items
    except Exception:
        return []

def main():
    print("啟動 ETF 數據更新程序...")
    # 測試清單 (後續可擴充至 0050~00999)
    tickers = ["0050", "0056", "00878", "00919", "00929", "006208", "00713", "00940"]
    
    market_db = []
    
    for symbol in tickers:
        try:
            ticker_str = f"{symbol}.TW"
            df = yf.download(ticker_str, period="1y", progress=False)
            
            if df.empty:
                continue
                
            prices = df['Close']
            current_price = float(prices.iloc[-1])
            vol_20d = int(df['Volume'].tail(20).mean() / 1000) if 'Volume' in df.columns else None
            
            if len(df) >= 200:
                cagr_1y = float((current_price - prices.iloc[0]) / prices.iloc[0])
                max_p = prices.expanding().max()
                mdd = float(((prices - max_p) / max_p).min())
                daily_ret = prices.pct_change().dropna()
                sharpe = float((daily_ret.mean() / daily_ret.std()) * (252**0.5))
            else:
                cagr_1y, mdd, sharpe = None, None, None
                if len(df) > 0:
                    mdd = float(((prices - prices.expanding().max()) / prices.expanding().max()).min())

            # 抓取新聞
            news = fetch_yahoo_news(symbol)

            market_db.append({
                "id": symbol,
                "name": f"ETF_{symbol}", # 實務上需靜態名稱對照表
                "type": "台股ETF",
                "price": current_price,
                "cagr_1y": cagr_1y,
                "sharpe": sharpe,
                "mdd": mdd,
                "yield_ttm": None,
                "premium": 0.0, 
                "fee": 0.5,
                "aum": 100,
                "vol_20d": vol_20d,
                "dividend": None,
                "news": news
            })
            time.sleep(2) # 延遲重試機制的基礎防護
            
        except Exception as e:
            print(f"錯誤 {symbol}: {e}")
            continue

    # 模擬 IPO 靜態資料
    ipo_db = [{
        "id": "0095X", "name": "即將上市模擬", "issueDate": "2026-08-01", 
        "price": 10.0, "fee": 0.35, "freq": "月配", "topHoldings": "台積電, 聯發科", "news": []
    }]

    final_data = {"ipo": ipo_db, "main": market_db}
    
    with open("market_data.json", "w", encoding="utf-8") as f:
        json.dump(final_data, f, ensure_ascii=False, indent=2)
        
    print("數據更新完成並寫入 market_data.json")

if __name__ == "__main__":
    main()
