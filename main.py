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

# ==================== 初始化 ====================
exchange = ccxt.okx({
    'enableRateLimit': True,
    'options': {'defaultType': 'spot'}
})

last_signal_time = {symbol: 0 for symbol in SYMBOLS}

# 胜率统计
stats = {
    "long_3_win": 0, "long_3_total": 0,
    "long_5_win": 0, "long_5_total": 0,
    "short_3_win": 0, "short_3_total": 0,
    "short_5_win": 0, "short_5_total": 0,
}

# 待结算信号列表  [{symbol, direction, price, time, checked_3, checked_5}]
pending_signals = []

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

def get_winrate_text():
    def rate(w, t):
        return f"{(w / t * 100):.1f}%" if t > 0 else "暂无数据"
    
    text = f"""
**当前胜率统计**
3分钟多单: {rate(stats['long_3_win'], stats['long_3_total'])} ({stats['long_3_total']}次)
3分钟空单: {rate(stats['short_3_win'], stats['short_3_total'])} ({stats['short_3_total']}次)
5分钟多单: {rate(stats['long_5_win'], stats['long_5_total'])} ({stats['long_5_total']}次)
5分钟空单: {rate(stats['short_5_win'], stats['short_5_total'])} ({stats['short_5_total']}次)
"""
    return text

def fetch_ohlcv(symbol: str, timeframe: str, limit: int = 100):
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df

def ta_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def ta_atr(df, period=14):
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def calculate_simple_adx(df, period=14):
    up = df['high'].diff()
    down = -df['low'].diff()
    plus_dm = up.where((up > down) & (up > 0), 0)
    minus_dm = down.where((down > up) & (down > 0), 0)
    tr = ta_atr(df, period)
    plus_di = 100 * (plus_dm.rolling(period).mean() / tr)
    minus_di = 100 * (minus_dm.rolling(period).mean() / tr)
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)) * 100
    return dx.rolling(period).mean()

def calculate_indicators(df: pd.DataFrame):
    df = df.copy()
    df['ema9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['rsi'] = ta_rsi(df['close'], 14)
    df['vol_ma'] = df['volume'].rolling(20).mean()
    df['atr'] = ta_atr(df, 14)
    df['atr_ma'] = df['atr'].rolling(50).mean()
    df['adx'] = calculate_simple_adx(df, 14)
    return df

def check_signal(symbol: str):
    global last_signal_time

    df_1m = fetch_ohlcv(symbol, '1m', 120)
    df_5m = fetch_ohlcv(symbol, '5m', 50)
    df_15m = fetch_ohlcv(symbol, '15m', 50)

    df_1m = calculate_indicators(df_1m)
    df_5m = calculate_indicators(df_5m)
    df_15m = calculate_indicators(df_15m)

    row = df_1m.iloc[-2]
    row5 = df_5m.iloc[-2]
    row15 = df_15m.iloc[-2]

    trend_1m_bull = row['ema9'] > row['ema21'] and row['close'] > row['ema9']
    trend_1m_bear = row['ema9'] < row['ema21'] and row['close'] < row['ema9']
    trend_5m_bull = row5['ema9'] > row5['ema21']
    trend_15m_bull = row15['ema9'] > row15['ema21']

    body = abs(row['close'] - row['open'])
    range_ = row['high'] - row['low'] + 1e-10
    body_ratio = body / range_
    bull_pa = row['close'] > row['open'] and body_ratio >= BODY_MIN and row['close'] >= df_1m.iloc[-3]['high']
    bear_pa = row['close'] < row['open'] and body_ratio >= BODY_MIN and row['close'] <= df_1m.iloc[-3]['low']

    vol_ok = row['volume'] > row['vol_ma'] * VOL_MULT
    atr_ok = row['atr'] > row['atr_ma'] * 0.7
    rsi_bull = 53 < row['rsi'] < 72
    rsi_bear = 28 < row['rsi'] < 47
    adx_ok = row['adx'] > ADX_THRESHOLD

    long_signal = (trend_1m_bull and trend_5m_bull and trend_15m_bull and
                   adx_ok and rsi_bull and vol_ok and atr_ok and bull_pa)
    short_signal = (trend_1m_bear and not trend_5m_bull and not trend_15m_bull and
                    adx_ok and rsi_bear and vol_ok and atr_ok and bear_pa)

    now = time.time()
    if now - last_signal_time[symbol] < COOLDOWN_MINUTES * 60:
        return None

    if long_signal:
        last_signal_time[symbol] = now
        return "UP"
    if short_signal:
        last_signal_time[symbol] = now
        return "DOWN"
    return None

def update_pending_signals():
    """检查待结算信号的3分钟和5分钟结果"""
    global pending_signals, stats
    now = time.time()
    still_pending = []

    for sig in pending_signals:
        try:
            current_price = exchange.fetch_ticker(sig["symbol"])['last']
            elapsed = now - sig["time"]

            # 3分钟结算
            if not sig["checked_3"] and elapsed >= 180:
                win = (current_price > sig["price"]) if sig["direction"] == "UP" else (current_price < sig["price"])
                if sig["direction"] == "UP":
                    stats["long_3_total"] += 1
                    if win:
                        stats["long_3_win"] += 1
                else:
                    stats["short_3_total"] += 1
                    if win:
                        stats["short_3_win"] += 1
                sig["checked_3"] = True
                print(f"{sig['symbol']} 3分钟结果: {'胜' if win else '负'}")

            # 5分钟结算
            if not sig["checked_5"] and elapsed >= 300:
                win = (current_price > sig["price"]) if sig["direction"] == "UP" else (current_price < sig["price"])
                if sig["direction"] == "UP":
                    stats["long_5_total"] += 1
                    if win:
                        stats["long_5_win"] += 1
                else:
                    stats["short_5_total"] += 1
                    if win:
                        stats["short_5_win"] += 1
                sig["checked_5"] = True
                print(f"{sig['symbol']} 5分钟结果: {'胜' if win else '负'}")

            if not (sig["checked_3"] and sig["checked_5"]):
                still_pending.append(sig)
        except Exception as e:
            print("结算错误:", e)
            still_pending.append(sig)

    pending_signals = still_pending

def main():
    print(f"机器人启动 | 交易所: OKX | 监控: {', '.join(SYMBOLS)}")
    send_wechat("事件合约机器人启动", f"已启动，开始监控 {', '.join(SYMBOLS)}\n\n{get_winrate_text()}")

    while True:
        # 先更新待结算信号
        update_pending_signals()

        for symbol in SYMBOLS:
            try:
                signal = check_signal(symbol)
                if signal:
                    price = exchange.fetch_ticker(symbol)['last']
                    # 记录待结算信号
                    pending_signals.append({
                        "symbol": symbol,
                        "direction": signal,
                        "price": price,
                        "time": time.time(),
                        "checked_3": False,
                        "checked_5": False
                    })

                    title = f"事件合约信号：{symbol} {signal}"
                    content = f"""
**交易对**: {symbol}
**方向**: {signal}
**当前价格**: {price}
**时间**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC
**建议**: 3-5分钟事件合约

{get_winrate_text()}
"""
                    print(title)
                    send_wechat(title, content)
                else:
                    print(f"{datetime.now().strftime('%H:%M:%S')} {symbol} 无信号")
            except Exception as e:
                print(f"{symbol} 错误:", e)

            time.sleep(2)

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
