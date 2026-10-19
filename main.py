import time
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta
from collections import deque

# ==================== 配置区 ====================
SERVERCHAN_SENDKEY = "SCT440554TBR9un4ZZPUOu6r28LJvNDpY8"

SYMBOLS = ["BTC-USDT", "ETH-USDT"]
CHECK_INTERVAL = 20
MIN_BUY_SELL_RATIO = 2.8
MIN_SELL_BUY_RATIO = 2.8

# 15分钟优先阈值
NEAR_LOWER_15 = 0.30
NEAR_MID_15 = 0.20
NEAR_UPPER_15 = 0.35          # 距离上轨小于这个值视为贴上轨，不做多

NEAR_LOWER_5 = 0.20
NEAR_LOWER_3 = 0.18

SIGNAL_COOLDOWN = 180
PRICE_DIFF_LIMIT = 0.15
BBW_RANGE_THRESHOLD = 2.2

HK_TZ = timezone(timedelta(hours=8))

states = {
    "BTC-USDT": {
        "pending": deque(),
        "stats_3m": {"win": 0, "total": 0},
        "stats_5m": {"win": 0, "total": 0},
        "last_long_price": 0, "last_short_price": 0,
        "last_long_time": 0, "last_short_time": 0,
    },
    "ETH-USDT": {
        "pending": deque(),
        "stats_3m": {"win": 0, "total": 0},
        "stats_5m": {"win": 0, "total": 0},
        "last_long_price": 0, "last_short_price": 0,
        "last_long_time": 0, "last_short_time": 0,
    }
}

def now_hk():
    return datetime.now(HK_TZ)

def send_wechat(title: str, content: str):
    url = f"https://sctapi.ftqq.com/{SERVERCHAN_SENDKEY}.send"
    data = {"title": title, "desp": content}
    try:
        resp = requests.post(url, data=data, timeout=10)
        print("微信提醒发送成功" if resp.json().get("code") == 0 else f"发送失败: {resp.text}")
    except Exception as e:
        print(f"微信发送异常: {e}")

def get_okx_orderbook(symbol):
    try:
        url = f"https://www.okx.com/api/v5/market/books?instId={symbol}&sz=10"
        data = requests.get(url, timeout=5).json()
        if data.get("code") != "0" or not data.get("data"):
            return 0, 0
        bids = sum(float(p) * float(q) for p, q, _, _ in data["data"][0]["bids"])
        asks = sum(float(p) * float(q) for p, q, _, _ in data["data"][0]["asks"])
        return bids, asks
    except Exception as e:
        print(f"{symbol} 订单簿错误: {e}")
        return 0, 0

def get_okx_klines(symbol, interval="15m", limit=100):
    try:
        url = f"https://www.okx.com/api/v5/market/candles?instId={symbol}&bar={interval}&limit={limit}"
        data = requests.get(url, timeout=5).json()
        if data.get("code") != "0" or not data.get("data"):
            raise ValueError("K线数据异常")
        rows = data["data"][::-1]
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "vol", "volCcy", "volCcyQuote", "confirm"])
        df["close"] = df["close"].astype(float)
        return df
    except Exception as e:
        print(f"{symbol} {interval} K线失败: {e}")
        return None

def get_current_price(symbol):
    try:
        url = f"https://www.okx.com/api/v5/market/ticker?instId={symbol}"
        data = requests.get(url, timeout=5).json()
        if data.get("code") == "0" and data.get("data"):
            return float(data["data"][0]["last"])
    except:
        pass
    return None

def calculate_bollinger(df, period=20, std=2):
    df = df.copy()
    df["ma"] = df["close"].rolling(period).mean()
    df["std"] = df["close"].rolling(period).std()
    df["upper"] = df["ma"] + std * df["std"]
    df["lower"] = df["ma"] - std * df["std"]
    return df

def get_bb_info(symbol, interval):
    df = get_okx_klines(symbol, interval)
    if df is None or len(df) < 30:
        return None
    df = calculate_bollinger(df)
    latest = df.iloc[-1]
    price = latest["close"]
    lower = latest["lower"]
    upper = latest["upper"]
    mid = latest["ma"]
    dist_lower = (price - lower) / price * 100 if price > 0 else 999
    dist_upper = (upper - price) / price * 100 if price > 0 else 999
    dist_mid = abs(price - mid) / price * 100 if price > 0 else 999
    bbw = (upper - lower) / mid * 100 if mid > 0 else 999
    is_ranging = bbw < BBW_RANGE_THRESHOLD
    return {
        "price": price, "lower": lower, "upper": upper, "mid": mid,
        "dist_lower": dist_lower, "dist_upper": dist_upper, "dist_mid": dist_mid,
        "bbw": bbw, "is_ranging": is_ranging
    }

def check_pending_signals(symbol):
    state = states[symbol]
    now = now_hk()
    current_price = get_current_price(symbol)
    if current_price is None:
        return
    remaining = deque()
    name = "BTC" if "BTC" in symbol else "ETH"
    for sig in state["pending"]:
        elapsed = (now - sig["time"]).total_seconds()
        direction_text = "做多" if sig["direction"] == "long" else "做空"

        if not sig["checked_3m"] and elapsed >= 180:
            win = (current_price > sig["price"]) if sig["direction"] == "long" else (current_price < sig["price"])
            state["stats_3m"]["total"] += 1
            if win: state["stats_3m"]["win"] += 1
            sig["checked_3m"] = True
            rate = state["stats_3m"]["win"] / state["stats_3m"]["total"] * 100
            result = "成功 ✅" if win else "失败 ❌"
            send_wechat(f"【3分钟验证】{name} {direction_text}",
                        f"**结果**：{result}\n开仓价：{sig['price']:.2f}\n当前价：{current_price:.2f}\n3分钟成功率：{rate:.1f}%（{state['stats_3m']['win']}/{state['stats_3m']['total']}）")
            print(f">>> {name} 3分钟验证: {result}")

        if not sig["checked_5m"] and elapsed >= 300:
            win = (current_price > sig["price"]) if sig["direction"] == "long" else (current_price < sig["price"])
            state["stats_5m"]["total"] += 1
            if win: state["stats_5m"]["win"] += 1
            sig["checked_5m"] = True
            rate = state["stats_5m"]["win"] / state["stats_5m"]["total"] * 100
            result = "成功 ✅" if win else "失败 ❌"
            send_wechat(f"【5分钟验证】{name} {direction_text}",
                        f"**结果**：{result}\n开仓价：{sig['price']:.2f}\n当前价：{current_price:.2f}\n5分钟成功率：{rate:.1f}%（{state['stats_5m']['win']}/{state['stats_5m']['total']}）")
            print(f">>> {name} 5分钟验证: {result}")

        if not (sig["checked_3m"] and sig["checked_5m"]):
            remaining.append(sig)
    state["pending"] = remaining

def get_success_rate_text(symbol):
    s = states[symbol]
    r3 = s["stats_3m"]["win"] / s["stats_3m"]["total"] * 100 if s["stats_3m"]["total"] > 0 else 0
    r5 = s["stats_5m"]["win"] / s["stats_5m"]["total"] * 100 if s["stats_5m"]["total"] > 0 else 0
    return f"3分钟成功率: {r3:.1f}% ({s['stats_3m']['win']}/{s['stats_3m']['total']})\n5分钟成功率: {r5:.1f}% ({s['stats_5m']['win']}/{s['stats_5m']['total']})"

print("=" * 50)
print("BTC + ETH 合并监控已启动（新经验逻辑 + GMT+8）")
print("=" * 50)

while True:
    try:
        for symbol in SYMBOLS:
            name = "BTC" if "BTC" in symbol else "ETH"
            state = states[symbol]
            check_pending_signals(symbol)

            ok_bids, ok_asks = get_okx_orderbook(symbol)
            buy_sell_ratio = ok_bids / ok_asks if ok_asks > 0 else 0
            sell_buy_ratio = ok_asks / ok_bids if ok_bids > 0 else 0

            bb_15 = get_bb_info(symbol, "15m")
            bb_5  = get_bb_info(symbol, "5m")
            bb_3  = get_bb_info(symbol, "3m")

            if bb_15 is None or bb_5 is None or bb_3 is None:
                print(f"{name} 数据获取失败，跳过")
                continue

            price = bb_15["price"]
            now_str = now_hk().strftime("%H:%M:%S")
            now_ts = time.time()

            # 震荡状态
            ranging_15 = bb_15["is_ranging"]
            ranging_5  = bb_5["is_ranging"]
            ranging_3  = bb_3["is_ranging"]
            ranging_count = sum([ranging_3, ranging_5, ranging_15])

            # ---------- 做多条件（按新经验） ----------
            # 1. 15分钟优先：接近下轨 或 中轨下方
            near_lower_15 = bb_15["dist_lower"] < NEAR_LOWER_15
            near_mid_15   = bb_15["dist_mid"] < NEAR_MID_15 and price <= bb_15["mid"]
            pos_15_ok = near_lower_15 or near_mid_15

            # 2. 贴上轨不做多
            not_near_upper = bb_15["dist_upper"] > NEAR_UPPER_15

            # 3. 3/5分钟配合
            near_lower_5 = bb_5["dist_lower"] < NEAR_LOWER_5
            near_lower_3 = bb_3["dist_lower"] < NEAR_LOWER_3
            tf_support = near_lower_5 or near_lower_3 or ranging_5 or ranging_3

            # 4. 盘口
            book_ok = buy_sell_ratio >= MIN_BUY_SELL_RATIO

            # 5. 优先15分钟震荡
            long_signal = pos_15_ok and not_near_upper and tf_support and book_ok

            # ---------- 做空条件（对称） ----------
            near_upper_15 = bb_15["dist_upper"] < NEAR_LOWER_15
            near_mid_15_up = bb_15["dist_mid"] < NEAR_MID_15 and price >= bb_15["mid"]
            pos_15_short = near_upper_15 or near_mid_15_up

            not_near_lower = bb_15["dist_lower"] > NEAR_UPPER_15
            near_upper_5 = bb_5["dist_upper"] < NEAR_LOWER_5
            near_upper_3 = bb_3["dist_upper"] < NEAR_LOWER_3
            tf_support_short = near_upper_5 or near_upper_3 or ranging_5 or ranging_3
            book_ok_short = sell_buy_ratio >= MIN_SELL_BUY_RATIO

            short_signal = pos_15_short and not_near_lower and tf_support_short and book_ok_short

            rate_text = get_success_rate_text(symbol)
            print(f"[{now_str} GMT+8] {name}:{price:.2f} | 下轨:{bb_15['dist_lower']:.2f}% 中轨:{bb_15['dist_mid']:.2f}% 上轨:{bb_15['dist_upper']:.2f}% | 买/卖:{buy_sell_ratio:.2f}")
            print(f"         震荡:3m={ranging_3} 5m={ranging_5} 15m={ranging_15} | {rate_text}")

            # 做多推送
            if long_signal:
                price_diff = abs(price - state["last_long_price"]) / price * 100 if state["last_long_price"] > 0 else 999
                if (now_ts - state["last_long_time"] > SIGNAL_COOLDOWN) and (price_diff > PRICE_DIFF_LIMIT):
                    state["pending"].append({
                        "time": now_hk(), "price": price, "direction": "long",
                        "checked_3m": False, "checked_5m": False
                    })
                    flat_tag = f" | {ranging_count}个周期走平" if ranging_count >= 2 else ""
                    title = f"【{name}做多】建议开3-5分钟{flat_tag}"
                    content = f"""
**时间**：{now_str} (GMT+8)
**{name}当前价格**：{price:.2f}

**15分钟布林带**：
- 距离下轨：{bb_15['dist_lower']:.2f}%
- 距离中轨：{bb_15['dist_mid']:.2f}%
- 距离上轨：{bb_15['dist_upper']:.2f}%
- 状态：{'震荡' if ranging_15 else '趋势'}

**3/5分钟配合**：
- 5分钟距离下轨：{bb_5['dist_lower']:.2f}% {'✓' if near_lower_5 else ''}
- 3分钟距离下轨：{bb_3['dist_lower']:.2f}% {'✓' if near_lower_3 else ''}

**欧意买/卖比**：{buy_sell_ratio:.2f}
**走平周期数**：{ranging_count}

**历史成功率**：
{rate_text}

**操作建议**：最小仓位开【看涨】3分钟或5分钟事件合约
"""
                    send_wechat(title, content)
                    print(f">>> {name}做多信号已推送！")
                    state["last_long_time"] = now_ts
                    state["last_long_price"] = price
                else:
                    print(f"（{name}做多被防重复过滤）")

            # 做空推送
            if short_signal:
                price_diff = abs(price - state["last_short_price"]) / price * 100 if state["last_short_price"] > 0 else 999
                if (now_ts - state["last_short_time"] > SIGNAL_COOLDOWN) and (price_diff > PRICE_DIFF_LIMIT):
                    state["pending"].append({
                        "time": now_hk(), "price": price, "direction": "short",
                        "checked_3m": False, "checked_5m": False
                    })
                    flat_tag = f" | {ranging_count}个周期走平" if ranging_count >= 2 else ""
                    title = f"【{name}做空】建议开3-5分钟{flat_tag}"
                    content = f"""
**时间**：{now_str} (GMT+8)
**{name}当前价格**：{price:.2f}

**15分钟布林带**：
- 距离上轨：{bb_15['dist_upper']:.2f}%
- 距离中轨：{bb_15['dist_mid']:.2f}%
- 距离下轨：{bb_15['dist_lower']:.2f}%
- 状态：{'震荡' if ranging_15 else '趋势'}

**3/5分钟配合**：
- 5分钟距离上轨：{bb_5['dist_upper']:.2f}% {'✓' if near_upper_5 else ''}
- 3分钟距离上轨：{bb_3['dist_upper']:.2f}% {'✓' if near_upper_3 else ''}

**欧意卖/买比**：{sell_buy_ratio:.2f}
**走平周期数**：{ranging_count}

**历史成功率**：
{rate_text}

**操作建议**：最小仓位开【看跌】3分钟或5分钟事件合约
"""
                    send_wechat(title, content)
                    print(f">>> {name}做空信号已推送！")
                    state["last_short_time"] = now_ts
                    state["last_short_price"] = price
                else:
                    print(f"（{name}做空被防重复过滤）")

            if not long_signal and not short_signal:
                print(f"{name} 暂无信号")

    except Exception as e:
        print(f"主循环错误: {e}")
        time.sleep(10)

    time.sleep(CHECK_INTERVAL)
