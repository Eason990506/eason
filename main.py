import os
import time
import requests
import ccxt
import pandas as pd
from datetime import datetime, timezone

# ==================== 配置 ====================
SYMBOLS = ["BTC/USDT", "ETH/USDT"]
SERVERCHAN_KEY = os.getenv("SERVERCHAN_KEY", "SCT440554TBR9un4ZZPUOu6r28LJvNDpY8")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "50"))
COOLDOWN_MINUTES = int(os.getenv("COOLDOWN_MINUTES", "8"))

ADX_THRESHOLD = 25
VOL_MULT = 1.8
BODY_MIN = 0.70

# ==================== 初始化（改用 OKX） ====================
exchange = ccxt.okx({
    'enableRateLimit': True,
    'options': {'defaultType': 'spot'}
})

last_signal_time = {symbol: 0 for symbol in SYMBOLS}

def send_wechat(title: str, content: str):
    if not SERVERCHAN_KEY:
        print("未配置 SERVERCHAN_KEY，跳过推送")
        return
    url = f"https://sctapi.ftqq.com/{SERVERCHAN_KEY}.send"
    data = {"title": title, "desp": content}
    try:
        resp = requests.post(url, data=data, timeout=10)
        print("微信推送结果:", resp.text)
    except Exception as e:
        print("推送失败:", e)

def fetch_ohlcv(symbol: str, timeframe: str, limit: int = 100):
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit
