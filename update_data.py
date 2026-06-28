import yfinance as yf
import requests
import pandas as pd
import json
import time
import math
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

FILE_MAP = {
    "高股息": "data_high_div.json", "市值型": "data_market_cap.json",
    "主題型": "data_theme.json", "債券型": "data_bond.json",
    "槓桿型": "data_leverage.json", "反向型": "data_inverse.json",
    "綜合/其他": "data_other.json"
}

def sanitize_json(val):
    """遞迴過濾器：將 NaN、Inf 轉為 None，確保 JSON 格式安全"""
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
    """獲取台股全市場 ETF 名單與 OpenAPI 官方淨值"""
    tickers = {}
    nav_dict = {}
    headers = {"User-Agent": "Mozilla/5.0"}
    
    # 1. 取得名單
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", headers=headers, timeout=10)
        for item in res.json():
            if str(item.get('Code', '')).startswith('00'):
                tickers[f"{item['Code']}.TW"] = item.get('Name', '')
    except: pass

    try:
        res = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", headers=headers, timeout=10)
        for item in res.json():
            code = str(item.get('SecuritiesCompanyCode', ''))
            if code.startswith('00'):
                tickers[f"{code}.TWO"] = item.get('CompanyName', '')
    except: pass

    # 2. 取得官方淨值 (TWSE & TPEX)
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/MI_101", headers=headers, timeout=10)
        for item in res.json():
            try: nav_dict[f"{item.get('Code')}.TW"] = float(str(item.get('Nav', '0')).replace(',', ''))
            except: pass
    except: pass

    try:
