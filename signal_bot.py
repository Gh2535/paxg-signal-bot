import os
import requests
import pandas as pd
from ta.trend import EMAIndicator, MACD
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

SYMBOL = "GOLD(PAXG)USDT"
INTERVAL = "15m"
LIMIT = 250

def send_telegram(message):
    telegram_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(
        telegram_url,
        data={
            "chat_id": CHAT_ID,
            "text": message
        }
    )

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
        raise Exception(f"MEXC error: {data}")

    df = pd.DataFrame(data)
    df = df.iloc[:, :6]
    df.columns = ["time", "open", "high", "low", "close", "volume"]

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)

    return df

def add_indicators(df):
    df["ema20"] = EMAIndicator(close=df["close"], window=20).ema_indicator()
    df["ema50"] = EMAIndicator(close=df["close"], window=50).ema_indicator()
    df["ema200"] = EMAIndicator(close=df["close"], window=200).ema_indicator()
    df["rsi"] = RSIIndicator(close=df["close"], window=14).rsi()

    macd = MACD(close=df["close"])
    df["macd_hist"] = macd.macd_diff()

    atr = AverageTrueRange(
        high=df["high"],
        low=df["low"],
        close=df["close"],
        window=14
    )
    df["atr"] = atr.average_true_range()

    return df

df = get_klines(SYMBOL, INTERVAL, LIMIT)
df = add_indicators(df)

latest = df.iloc[-1]

price = latest["close"]
ema20 = latest["ema20"]
ema50 = latest["ema50"]
ema200 = latest["ema200"]
rsi = latest["rsi"]
macd_hist = latest["macd_hist"]
atr = latest["atr"]

reasons = []

# Trend check
trend_long = ema50 > ema200
trend_short = ema50 < ema200

# Momentum check
momentum_long = rsi > 50 and macd_hist > 0
momentum_short = rsi < 50 and macd_hist < 0

# Price position check
price_long = price > ema50
price_short = price < ema50

# Pullback / not chasing filter
distance_from_ema20 = abs(price - ema20) / price * 100
not_too_far = distance_from_ema20 <= 0.45

# Volatility filter
atr_percent = atr / price * 100
volatility_ok = atr_percent >= 0.10

signal = "NO TRADE"

if trend_long and momentum_long and price_long and not_too_far and volatility_ok:
    signal = "LONG"
elif trend_short and momentum_short and price_short and not_too_far and volatility_ok:
    signal = "SHORT"

# Reasons
if not trend_long and not trend_short:
    reasons.append("روند نامشخص است.")
elif trend_long:
    reasons.append("روند کلی 15m صعودی است.")
elif trend_short:
    reasons.append("روند کلی 15m نزولی است.")

if not momentum_long and not momentum_short:
    reasons.append("مومنتوم واضح نیست.")
elif momentum_long:
    reasons.append("مومنتوم به نفع LONG است.")
elif momentum_short:
    reasons.append("مومنتوم به نفع SHORT است.")

if not not_too_far:
    reasons.append(f"قیمت از EMA20 زیاد دور شده است: {distance_from_ema20:.2f}%")

if not volatility_ok:
    reasons.append(f"نوسان کم است؛ ATR% = {atr_percent:.3f}%")

if signal == "NO TRADE":
    reasons.append("همه شروط ورود همزمان کامل نشده‌اند.")

tp_percent = 1.0
sl_percent = 0.6

if signal == "LONG":
    entry = price
    tp = entry * (1 + tp_percent / 100)
    sl = entry * (1 - sl_percent / 100)
elif signal == "SHORT":
    entry = price
    tp = entry * (1 - tp_percent / 100)
    sl = entry * (1 + sl_percent / 100)
else:
    entry = None
    tp = None
    sl = None

message = f"""
PAXG SIGNAL BOT

Symbol: {SYMBOL}
Timeframe: {INTERVAL}

Signal: {signal}
Price: {price:.2f}

EMA20: {ema20:.2f}
EMA50: {ema50:.2f}
EMA200: {ema200:.2f}

RSI: {rsi:.2f}
MACD Hist: {macd_hist:.4f}
ATR: {atr:.2f}
ATR%: {atr_percent:.3f}%
Distance from EMA20: {distance_from_ema20:.3f}%

"""

if signal in ["LONG", "SHORT"]:
    message += f"""
Entry: {entry:.2f}
TP: {tp:.2f}
SL: {sl:.2f}
"""
else:
    message += "\nEntry: -\nTP: -\nSL: -\n"

message += "\nReasons:\n"
for r in reasons:
    message += f"- {r}\n"

send_telegram(message)
print(message)
