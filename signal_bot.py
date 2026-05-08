import os
import requests
import pandas as pd
from ta.trend import EMAIndicator, MACD
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

SYMBOL = "GOLD(PAXG)USDT"
LIMIT = 250

TP_PERCENT = 1.0
SL_PERCENT = 0.6

MAX_SPREAD_PERCENT = 0.10


def send_telegram(message):
    telegram_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    response = requests.post(
        telegram_url,
        data={
            "chat_id": CHAT_ID,
            "text": message
        },
        timeout=20
    )
    print(response.status_code)
    print(response.text)


def get_klines(symbol, interval, limit):
    url = "https://api.mexc.com/api/v3/klines"
    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    }

    response = requests.get(url, params=params, timeout=20)
    data = response.json()

    if not isinstance(data, list):
        raise Exception(f"MEXC klines error: {data}")

    df = pd.DataFrame(data)
    df = df.iloc[:, :6]
    df.columns = ["time", "open", "high", "low", "close", "volume"]

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)

    return df


def get_spread(symbol):
    url = "https://api.mexc.com/api/v3/ticker/bookTicker"
    params = {
        "symbol": symbol
    }

    response = requests.get(url, params=params, timeout=20)
    data = response.json()

    if not isinstance(data, dict):
        raise Exception(f"MEXC spread error: {data}")

    if "bidPrice" not in data or "askPrice" not in data:
        raise Exception(f"MEXC spread response missing bid/ask: {data}")

    bid = float(data["bidPrice"])
    ask = float(data["askPrice"])

    if bid <= 0 or ask <= 0:
        raise Exception(f"Invalid bid/ask: bid={bid}, ask={ask}")

    mid = (bid + ask) / 2
    spread_percent = (ask - bid) / mid * 100

    return bid, ask, spread_percent


def add_indicators(df):
    df["ema20"] = EMAIndicator(close=df["close"], window=20).ema_indicator()
    df["ema50"] = EMAIndicator(close=df["close"], window=50).ema_indicator()
    df["ema200"] = EMAIndicator(close=df["close"], window=200).ema_indicator()

    df["rsi"] = RSIIndicator(close=df["close"], window=14).rsi()

    macd = MACD(close=df["close"], window_slow=26, window_fast=12, window_sign=9)
    df["macd_hist"] = macd.macd_diff()

    atr = AverageTrueRange(
        high=df["high"],
        low=df["low"],
        close=df["close"],
        window=14
    )
    df["atr"] = atr.average_true_range()

    return df


def calculate_signal():
    # Market spread
    bid, ask, spread_percent = get_spread(SYMBOL)
    spread_ok = spread_percent <= MAX_SPREAD_PERCENT

    # 15m data
    df_15m = get_klines(SYMBOL, "15m", LIMIT)
    df_15m = add_indicators(df_15m)
    latest_15m = df_15m.iloc[-1]

    # 1h confirmation data
    # MEXC uses 60m instead of 1h for this symbol
    df_1h = get_klines(SYMBOL, "60m", LIMIT)
    df_1h = add_indicators(df_1h)
    latest_1h = df_1h.iloc[-1]

    price = latest_15m["close"]

    ema20 = latest_15m["ema20"]
    ema50 = latest_15m["ema50"]
    ema200 = latest_15m["ema200"]

    rsi = latest_15m["rsi"]
    macd_hist = latest_15m["macd_hist"]
    atr = latest_15m["atr"]

    ema50_1h = latest_1h["ema50"]
    ema200_1h = latest_1h["ema200"]
    rsi_1h = latest_1h["rsi"]

    # Trend filters
    trend_1h_long = ema50_1h > ema200_1h
    trend_1h_short = ema50_1h < ema200_1h

    trend_15m_long = ema50 > ema200
    trend_15m_short = ema50 < ema200

    # Momentum filters
    momentum_long = rsi > 52 and macd_hist > 0
    momentum_short = rsi < 48 and macd_hist < 0

    # Price position
    price_long = price > ema50
    price_short = price < ema50

    # Pullback / not chasing filter
    distance_from_ema20 = abs(price - ema20) / price * 100
    not_too_far = distance_from_ema20 <= 0.45

    # Volatility filter
    atr_percent = atr / price * 100
    volatility_ok = atr_percent >= 0.10

    signal = "NO TRADE"
    reasons = []

    if (
        trend_1h_long
        and trend_15m_long
        and momentum_long
        and price_long
        and not_too_far
        and volatility_ok
        and spread_ok
    ):
        signal = "LONG"

    elif (
        trend_1h_short
        and trend_15m_short
        and momentum_short
        and price_short
        and not_too_far
        and volatility_ok
        and spread_ok
    ):
        signal = "SHORT"

    # Strength score
    score = 0

    if trend_1h_long or trend_1h_short:
        score += 1

    if trend_15m_long or trend_15m_short:
        score += 1

    if momentum_long or momentum_short:
        score += 1

    if price_long or price_short:
        score += 1

    if not_too_far:
        score += 1

    if volatility_ok:
        score += 1

    if spread_ok:
        score += 1

    if score >= 7:
        strength = "Strong"
    elif score >= 5:
        strength = "Medium"
    else:
        strength = "Weak"

    # Reasons
    if trend_1h_long:
        reasons.append("روند 1h صعودی است.")
    elif trend_1h_short:
        reasons.append("روند 1h نزولی است.")
    else:
        reasons.append("روند 1h نامشخص است.")

    if trend_15m_long:
        reasons.append("روند 15m صعودی است.")
    elif trend_15m_short:
        reasons.append("روند 15m نزولی است.")
    else:
        reasons.append("روند 15m نامشخص است.")

    if momentum_long:
        reasons.append("مومنتوم 15m به نفع LONG است.")
    elif momentum_short:
        reasons.append("مومنتوم 15m به نفع SHORT است.")
    else:
        reasons.append("مومنتوم 15m ضعیف یا خنثی است.")

    if not not_too_far:
        reasons.append(f"قیمت از EMA20 زیاد دور شده است: {distance_from_ema20:.3f}%")

    if not volatility_ok:
        reasons.append(f"نوسان کافی نیست: ATR% = {atr_percent:.3f}%")

    if spread_ok:
        reasons.append(f"اسپرد قابل قبول است: {spread_percent:.4f}%")
    else:
        reasons.append(
            f"اسپرد زیاد است: {spread_percent:.4f}%؛ حد مجاز: {MAX_SPREAD_PERCENT:.2f}%"
        )

    if signal == "NO TRADE":
        reasons.append("همه شروط ورود همزمان کامل نشده‌اند.")

    # TP / SL
    if signal == "LONG":
        entry = price
        tp = entry * (1 + TP_PERCENT / 100)
        sl = entry * (1 - SL_PERCENT / 100)
    elif signal == "SHORT":
        entry = price
        tp = entry * (1 - TP_PERCENT / 100)
        sl = entry * (1 + SL_PERCENT / 100)
    else:
        entry = None
        tp = None
        sl = None

    message = f"""
PAXG SIGNAL BOT

Symbol: {SYMBOL}
Main Timeframe: 15m
Confirm Timeframe: 1h

Signal: {signal}
Strength: {strength}

Price: {price:.2f}

--- Market Spread ---
Bid: {bid:.2f}
Ask: {ask:.2f}
Spread: {spread_percent:.4f}%
Max Allowed Spread: {MAX_SPREAD_PERCENT:.2f}%

--- 15m ---
EMA20: {ema20:.2f}
EMA50: {ema50:.2f}
EMA200: {ema200:.2f}
RSI: {rsi:.2f}
MACD Hist: {macd_hist:.4f}
ATR: {atr:.2f}
ATR%: {atr_percent:.3f}%
Distance from EMA20: {distance_from_ema20:.3f}%

--- 1h ---
EMA50: {ema50_1h:.2f}
EMA200: {ema200_1h:.2f}
RSI: {rsi_1h:.2f}
"""

    if signal in ["LONG", "SHORT"]:
        message += f"""
Entry: {entry:.2f}
TP ({TP_PERCENT}%): {tp:.2f}
SL ({SL_PERCENT}%): {sl:.2f}
"""
    else:
        message += """
Entry: -
TP: -
SL: -
"""

    message += "\nReasons:\n"
    for reason in reasons:
        message += f"- {reason}\n"

    return message


if __name__ == "__main__":
    try:
        message = calculate_signal()
        send_telegram(message)
        print(message)
    except Exception as e:
        error_message = f"❌ PAXG Signal Bot Error:\n{str(e)}"
        send_telegram(error_message)
        raise
