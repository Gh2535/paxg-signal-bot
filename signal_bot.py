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
LIMIT = 200

url = f"https://api.mexc.com/api/v3/klines?symbol={SYMBOL}&interval={INTERVAL}&limit={LIMIT}"

response = requests.get(url)
data = response.json()

df = pd.DataFrame(data)

df = df.iloc[:, :6]
df.columns = ["time", "open", "high", "low", "close", "volume"]

for col in ["open", "high", "low", "close", "volume"]:
    df[col] = df[col].astype(float)

# Indicators
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

latest = df.iloc[-1]

signal = "NO TRADE"

if (
    latest["ema50"] > latest["ema200"]
    and latest["close"] > latest["ema50"]
    and latest["rsi"] > 50
    and latest["macd_hist"] > 0
):
    signal = "LONG"

elif (
    latest["ema50"] < latest["ema200"]
    and latest["close"] < latest["ema50"]
    and latest["rsi"] < 50
    and latest["macd_hist"] < 0
):
    signal = "SHORT"

message = f"""
PAXG SIGNAL

Signal: {signal}

Price: {latest['close']:.2f}
RSI: {latest['rsi']:.2f}
ATR: {latest['atr']:.2f}
"""

telegram_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

requests.post(
    telegram_url,
    data={
        "chat_id": CHAT_ID,
        "text": message
    }
)

print(message)
