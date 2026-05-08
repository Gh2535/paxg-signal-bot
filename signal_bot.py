import os
import requests
import pandas as pd
from datetime import datetime, timedelta, timezone
from ta.trend import EMAIndicator, MACD
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")

SYMBOL = "GOLD(PAXG)USDT"
LIMIT = 250

TP_PERCENT = 1.0
SL_PERCENT = 0.6

MAX_SPREAD_PERCENT = 0.10

NEWS_BLOCK_BEFORE_MINUTES = 90
NEWS_BLOCK_AFTER_MINUTES = 60
NEWS_MEDIUM_LOOKAHEAD_HOURS = 24

IMPORTANT_NEWS_KEYWORDS = [
    "cpi",
    "consumer price",
    "inflation",
    "ppi",
    "producer price",
    "non farm",
    "non-farm",
    "nfp",
    "payroll",
    "unemployment",
    "jobless",
    "fomc",
    "federal funds",
    "fed interest",
    "interest rate",
    "rate decision",
    "powell",
    "federal reserve",
    "gdp",
    "retail sales",
    "pce",
    "core pce",
    "ism",
    "manufacturing pmi",
    "services pmi",
]


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


def parse_finnhub_time(value):
    if not value:
        return None

    value = str(value).strip()

    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(value, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def is_us_event(event):
    country = str(event.get("country", "")).lower()
    return country in ["us", "usa", "united states", "united states of america"]


def is_important_gold_event(event):
    title = str(event.get("event", "")).lower()
    return any(keyword in title for keyword in IMPORTANT_NEWS_KEYWORDS)


def get_news_risk():
    if not FINNHUB_API_KEY:
        return {
            "risk": "UNKNOWN",
            "trade_allowed": True,
            "summary": "FINNHUB_API_KEY تنظیم نشده است.",
            "events": []
        }

    now = datetime.now(timezone.utc)
    date_from = now.date().isoformat()
    date_to = (now + timedelta(days=1)).date().isoformat()

    url = "https://finnhub.io/api/v1/calendar/economic"
    params = {
        "from": date_from,
        "to": date_to,
        "token": FINNHUB_API_KEY
    }

    try:
        response = requests.get(url, params=params, timeout=25)
        data = response.json()
    except Exception as e:
        return {
            "risk": "UNKNOWN",
            "trade_allowed": True,
            "summary": f"خطا در دریافت تقویم اقتصادی: {str(e)}",
            "events": []
        }

    events = data.get("economicCalendar", [])

    if not isinstance(events, list):
        return {
            "risk": "UNKNOWN",
            "trade_allowed": True,
            "summary": f"پاسخ Finnhub غیرمنتظره بود: {data}",
            "events": []
        }

    relevant_events = []

    for event in events:
        if not is_us_event(event):
            continue

        if not is_important_gold_event(event):
            continue

        event_name = str(event.get("event", "Unknown Event"))
        event_time_raw = event.get("time") or event.get("date")
        event_time = parse_finnhub_time(event_time_raw)

        relevant_events.append({
            "name": event_name,
            "time_raw": event_time_raw,
            "time": event_time,
            "actual": event.get("actual", ""),
            "estimate": event.get("estimate", ""),
            "previous": event.get("prev", event.get("previous", ""))
        })

    if not relevant_events:
        return {
            "risk": "LOW",
            "trade_allowed": True,
            "summary": "خبر مهم اقتصادی آمریکا برای طلا در بازه امروز/فردا پیدا نشد.",
            "events": []
        }

    high_risk_events = []
    medium_risk_events = []
    unknown_time_events = []

    for event in relevant_events:
        event_time = event["time"]

        if event_time is None:
            unknown_time_events.append(event)
            continue

        minutes_to_event = (event_time - now).total_seconds() / 60

        if -NEWS_BLOCK_AFTER_MINUTES <= minutes_to_event <= NEWS_BLOCK_BEFORE_MINUTES:
            high_risk_events.append(event)
        elif 0 < minutes_to_event <= NEWS_MEDIUM_LOOKAHEAD_HOURS * 60:
            medium_risk_events.append(event)

    if high_risk_events:
        return {
            "risk": "HIGH",
            "trade_allowed": False,
            "summary": "خبر مهم اقتصادی نزدیک است یا به‌تازگی منتشر شده؛ معامله ممنوع.",
            "events": high_risk_events[:5]
        }

    if medium_risk_events:
        return {
            "risk": "MEDIUM",
            "trade_allowed": True,
            "summary": "خبر مهم اقتصادی در ۲۴ ساعت آینده وجود دارد؛ ریسک سیگنال بالاتر است.",
            "events": medium_risk_events[:5]
        }

    if unknown_time_events:
        return {
            "risk": "MEDIUM",
            "trade_allowed": True,
            "summary": "خبر مهم پیدا شد ولی زمان دقیقش قابل تشخیص نبود؛ احتیاط لازم است.",
            "events": unknown_time_events[:5]
        }

    return {
        "risk": "LOW",
        "trade_allowed": True,
        "summary": "خبر مهم نزدیک وجود ندارد.",
        "events": relevant_events[:5]
    }


def format_news_events(events):
    if not events:
        return "- رویداد مهمی برای نمایش نیست.\n"

    text = ""
    for event in events:
        event_time = event.get("time")
        if event_time:
            time_text = event_time.strftime("%Y-%m-%d %H:%M UTC")
        else:
            time_text = str(event.get("time_raw", "Unknown time"))

        name = event.get("name", "Unknown Event")
        actual = event.get("actual", "")
        estimate = event.get("estimate", "")
        previous = event.get("previous", "")

        text += f"- {name} | {time_text}"

        details = []
        if estimate not in ["", None]:
            details.append(f"Est: {estimate}")
        if previous not in ["", None]:
            details.append(f"Prev: {previous}")
        if actual not in ["", None]:
            details.append(f"Actual: {actual}")

        if details:
            text += " | " + " / ".join(details)

        text += "\n"

    return text


def calculate_signal():
    # News risk
    news = get_news_risk()
    news_ok = news["trade_allowed"]

    # Market spread
    bid, ask, spread_percent = get_spread(SYMBOL)
    spread_ok = spread_percent <= MAX_SPREAD_PERCENT

    # 15m data
    df_15m = get_klines(SYMBOL, "15m", LIMIT)
    df_15m = add_indicators(df_15m)
    latest_15m = df_15m.iloc[-1]

    # 1h confirmation data
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
        and news_ok
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
        and news_ok
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

    if news_ok and news["risk"] in ["LOW", "MEDIUM"]:
        score += 1

    if score >= 8:
        strength = "Strong"
    elif score >= 6:
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

    if news["risk"] == "HIGH":
        reasons.append("ریسک خبری بالا است؛ معامله ممنوع.")
    elif news["risk"] == "MEDIUM":
        reasons.append("ریسک خبری متوسط است؛ ورود فقط با احتیاط.")
    elif news["risk"] == "LOW":
        reasons.append("ریسک خبری فعلاً پایین است.")
    else:
        reasons.append("وضعیت خبر نامشخص است؛ با احتیاط.")

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

    news_events_text = format_news_events(news["events"])

    message = f"""
PAXG SIGNAL BOT

Symbol: {SYMBOL}
Main Timeframe: 15m
Confirm Timeframe: 1h

Signal: {signal}
Strength: {strength}

Price: {price:.2f}

--- News Risk ---
News Risk: {news["risk"]}
Trading Allowed By News Filter: {news_ok}
News Summary: {news["summary"]}

Important Events:
{news_events_text}
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
