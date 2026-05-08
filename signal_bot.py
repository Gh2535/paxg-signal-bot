import os
import csv
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta, timezone
from ta.trend import EMAIndicator, MACD, ADXIndicator
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

DUPLICATE_SUPPRESSION_MINUTES = 60

SIGNALS_LOG_FILE = "signals_log.csv"
PAPER_TRADES_FILE = "paper_trades.csv"

MAX_OPEN_PAPER_TRADES = 1

ESTIMATED_ROUND_TRIP_FEE_PERCENT = 0.04

MIN_ADX_15M = 18.0
MIN_VOLUME_RATIO = 0.45
MAX_RECENT_MOVE_PERCENT = 0.75

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


SIGNAL_FIELDNAMES = [
    "timestamp_utc",
    "signal_candle_time",
    "symbol",
    "technical_signal",
    "final_signal",
    "block_reason",
    "strength",
    "price",
    "entry",
    "tp",
    "sl",
    "rsi_15m",
    "macd_hist_15m",
    "adx_15m",
    "volume_ratio_15m",
    "recent_move_percent",
    "atr_percent",
    "distance_from_ema20",
    "ema50_15m",
    "ema200_15m",
    "ema50_1h",
    "ema200_1h",
    "rsi_1h",
    "spread_percent",
    "news_risk",
    "news_summary",
    "telegram_sent",
    "duplicate_suppressed"
]


TRADE_FIELDNAMES = [
    "trade_id",
    "symbol",
    "side",
    "status",
    "open_time_utc",
    "close_time_utc",
    "signal_candle_time",
    "last_checked_candle_time",
    "entry",
    "tp",
    "sl",
    "exit_price",
    "result",
    "gross_profit_percent",
    "estimated_fee_percent",
    "net_profit_percent",
    "bars_held",
    "max_high_seen",
    "min_low_seen",
    "mfe_percent",
    "mae_percent",
    "close_reason",
    "strength_at_entry",
    "rsi_15m_at_entry",
    "macd_hist_15m_at_entry",
    "adx_15m_at_entry",
    "volume_ratio_15m_at_entry",
    "recent_move_percent_at_entry",
    "atr_percent_at_entry",
    "spread_percent_at_entry",
    "news_risk_at_entry",
    "news_summary_at_entry"
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

    adx = ADXIndicator(
        high=df["high"],
        low=df["low"],
        close=df["close"],
        window=14
    )
    df["adx"] = adx.adx()

    df["volume_sma20"] = df["volume"].rolling(window=20).mean()

    return df


def get_latest_closed_candle(df):
    # آخرین کندل معمولاً در حال تشکیل است؛ برای تصمیم‌گیری از کندل بسته‌شده قبلی استفاده می‌کنیم.
    return df.iloc[-2]


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


def ensure_csv_header(file_name, fieldnames):
    file_path = Path(file_name)

    if not file_path.exists():
        return

    try:
        with open(file_path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            existing_rows = list(reader)
            existing_fields = reader.fieldnames or []
    except Exception:
        return

    if existing_fields == fieldnames:
        return

    with open(file_path, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for row in existing_rows:
            cleaned = {field: row.get(field, "") for field in fieldnames}
            writer.writerow(cleaned)


def append_signal_log(row):
    ensure_csv_header(SIGNALS_LOG_FILE, SIGNAL_FIELDNAMES)

    file_path = Path(SIGNALS_LOG_FILE)
    file_exists = file_path.exists()

    with open(file_path, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SIGNAL_FIELDNAMES)

        if not file_exists:
            writer.writeheader()

        writer.writerow(row)


def read_trades():
    ensure_csv_header(PAPER_TRADES_FILE, TRADE_FIELDNAMES)

    file_path = Path(PAPER_TRADES_FILE)

    if not file_path.exists():
        return []

    with open(file_path, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def write_trades(trades):
    file_path = Path(PAPER_TRADES_FILE)

    with open(file_path, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TRADE_FIELDNAMES)
        writer.writeheader()

        for trade in trades:
            cleaned = {field: trade.get(field, "") for field in TRADE_FIELDNAMES}
            writer.writerow(cleaned)


def parse_float(value, default=0.0):
    try:
        if value in ["", None]:
            return default
        return float(value)
    except Exception:
        return default


def parse_int(value, default=0):
    try:
        if value in ["", None]:
            return default
        return int(float(value))
    except Exception:
        return default


def parse_log_timestamp(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def is_duplicate_recent(now_utc, technical_signal, final_signal, block_reason):
    file_path = Path(SIGNALS_LOG_FILE)

    if not file_path.exists():
        return False

    cutoff = now_utc - timedelta(minutes=DUPLICATE_SUPPRESSION_MINUTES)

    try:
        with open(file_path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
    except Exception:
        return False

    for row in reversed(rows):
        row_time = parse_log_timestamp(row.get("timestamp_utc", ""))

        if row_time is None:
            continue

        if row_time < cutoff:
            break

        same_state = (
            row.get("technical_signal") == technical_signal
            and row.get("final_signal") == final_signal
            and row.get("block_reason") == block_reason
        )

        was_sent = row.get("telegram_sent") == "yes"

        if same_state and was_sent:
            return True

    return False


def calculate_trade_performance(side, entry, max_high_seen, min_low_seen):
    if side == "LONG":
        mfe_percent = (max_high_seen - entry) / entry * 100
        mae_percent = (min_low_seen - entry) / entry * 100
    else:
        mfe_percent = (entry - min_low_seen) / entry * 100
        mae_percent = (entry - max_high_seen) / entry * 100

    return mfe_percent, mae_percent


def update_open_paper_trades(latest_15m, now_utc):
    trades = read_trades()
    if not trades:
        return [], []

    candle_time = str(int(latest_15m["time"]))
    high = float(latest_15m["high"])
    low = float(latest_15m["low"])

    updated_messages = []
    changed = False

    for trade in trades:
        if trade.get("status") != "OPEN":
            continue

        if trade.get("last_checked_candle_time") == candle_time:
            continue

        side = trade.get("side")
        entry = parse_float(trade.get("entry"))
        tp = parse_float(trade.get("tp"))
        sl = parse_float(trade.get("sl"))

        old_max_high = parse_float(trade.get("max_high_seen"), entry)
        old_min_low = parse_float(trade.get("min_low_seen"), entry)

        max_high_seen = max(old_max_high, high)
        min_low_seen = min(old_min_low, low)

        trade["max_high_seen"] = round(max_high_seen, 4)
        trade["min_low_seen"] = round(min_low_seen, 4)
        trade["last_checked_candle_time"] = candle_time

        bars_held = parse_int(trade.get("bars_held"), 0) + 1
        trade["bars_held"] = bars_held

        mfe_percent, mae_percent = calculate_trade_performance(
            side=side,
            entry=entry,
            max_high_seen=max_high_seen,
            min_low_seen=min_low_seen
        )

        trade["mfe_percent"] = round(mfe_percent, 4)
        trade["mae_percent"] = round(mae_percent, 4)

        close_reason = ""
        exit_price = None
        result = ""
        gross_profit_percent = 0.0

        if side == "LONG":
            tp_hit = high >= tp
            sl_hit = low <= sl

            if tp_hit and sl_hit:
                close_reason = "TP_AND_SL_SAME_CANDLE_ASSUMED_SL"
                exit_price = sl
                result = "LOSS"
                gross_profit_percent = (exit_price - entry) / entry * 100
            elif tp_hit:
                close_reason = "TP_HIT"
                exit_price = tp
                result = "WIN"
                gross_profit_percent = (exit_price - entry) / entry * 100
            elif sl_hit:
                close_reason = "SL_HIT"
                exit_price = sl
                result = "LOSS"
                gross_profit_percent = (exit_price - entry) / entry * 100

        elif side == "SHORT":
            tp_hit = low <= tp
            sl_hit = high >= sl

            if tp_hit and sl_hit:
                close_reason = "TP_AND_SL_SAME_CANDLE_ASSUMED_SL"
                exit_price = sl
                result = "LOSS"
                gross_profit_percent = (entry - exit_price) / entry * 100
            elif tp_hit:
                close_reason = "TP_HIT"
                exit_price = tp
                result = "WIN"
                gross_profit_percent = (entry - exit_price) / entry * 100
            elif sl_hit:
                close_reason = "SL_HIT"
                exit_price = sl
                result = "LOSS"
                gross_profit_percent = (entry - exit_price) / entry * 100

        if close_reason:
            net_profit_percent = gross_profit_percent - ESTIMATED_ROUND_TRIP_FEE_PERCENT

            trade["status"] = "CLOSED"
            trade["close_time_utc"] = now_utc.strftime("%Y-%m-%d %H:%M:%S")
            trade["exit_price"] = round(exit_price, 4)
            trade["result"] = result
            trade["gross_profit_percent"] = round(gross_profit_percent, 4)
            trade["estimated_fee_percent"] = ESTIMATED_ROUND_TRIP_FEE_PERCENT
            trade["net_profit_percent"] = round(net_profit_percent, 4)
            trade["close_reason"] = close_reason

            updated_messages.append(
                f"""
📌 PAPER TRADE CLOSED

Trade ID: {trade.get("trade_id")}
Side: {side}
Result: {result}
Close Reason: {close_reason}

Entry: {entry:.2f}
Exit: {exit_price:.2f}

Gross P/L: {gross_profit_percent:.3f}%
Estimated Fee: {ESTIMATED_ROUND_TRIP_FEE_PERCENT:.3f}%
Net P/L: {net_profit_percent:.3f}%

Bars Held: {bars_held}
MFE: {mfe_percent:.3f}%
MAE: {mae_percent:.3f}%
"""
            )

        changed = True

    if changed:
        write_trades(trades)

    return trades, updated_messages


def count_open_trades(trades):
    return sum(1 for trade in trades if trade.get("status") == "OPEN")


def create_paper_trade(
    now_utc,
    signal_candle_time,
    side,
    entry,
    tp,
    sl,
    strength,
    rsi,
    macd_hist,
    adx,
    volume_ratio,
    recent_move_percent,
    atr_percent,
    spread_percent,
    news_risk,
    news_summary
):
    trade_id = f"{now_utc.strftime('%Y%m%d%H%M%S')}_{side}"

    return {
        "trade_id": trade_id,
        "symbol": SYMBOL,
        "side": side,
        "status": "OPEN",
        "open_time_utc": now_utc.strftime("%Y-%m-%d %H:%M:%S"),
        "close_time_utc": "",
        "signal_candle_time": signal_candle_time,
        "last_checked_candle_time": signal_candle_time,
        "entry": round(entry, 4),
        "tp": round(tp, 4),
        "sl": round(sl, 4),
        "exit_price": "",
        "result": "",
        "gross_profit_percent": "",
        "estimated_fee_percent": ESTIMATED_ROUND_TRIP_FEE_PERCENT,
        "net_profit_percent": "",
        "bars_held": 0,
        "max_high_seen": round(entry, 4),
        "min_low_seen": round(entry, 4),
        "mfe_percent": 0,
        "mae_percent": 0,
        "close_reason": "",
        "strength_at_entry": strength,
        "rsi_15m_at_entry": round(rsi, 4),
        "macd_hist_15m_at_entry": round(macd_hist, 6),
        "adx_15m_at_entry": round(adx, 4),
        "volume_ratio_15m_at_entry": round(volume_ratio, 4),
        "recent_move_percent_at_entry": round(recent_move_percent, 4),
        "atr_percent_at_entry": round(atr_percent, 6),
        "spread_percent_at_entry": round(spread_percent, 6),
        "news_risk_at_entry": news_risk,
        "news_summary_at_entry": news_summary
    }


def calculate_signal():
    now_utc = datetime.now(timezone.utc)

    news = get_news_risk()
    news_ok = news["trade_allowed"]

    bid, ask, spread_percent = get_spread(SYMBOL)
    spread_ok = spread_percent <= MAX_SPREAD_PERCENT

    df_15m = get_klines(SYMBOL, "15m", LIMIT)
    df_15m = add_indicators(df_15m)
    latest_15m = get_latest_closed_candle(df_15m)

    df_1h = get_klines(SYMBOL, "60m", LIMIT)
    df_1h = add_indicators(df_1h)
    latest_1h = get_latest_closed_candle(df_1h)

    trades, trade_update_messages = update_open_paper_trades(latest_15m, now_utc)

    signal_candle_time = str(int(latest_15m["time"]))

    price = latest_15m["close"]

    ema20 = latest_15m["ema20"]
    ema50 = latest_15m["ema50"]
    ema200 = latest_15m["ema200"]

    rsi = latest_15m["rsi"]
    macd_hist = latest_15m["macd_hist"]
    atr = latest_15m["atr"]
    adx = latest_15m["adx"]

    volume = latest_15m["volume"]
    volume_sma20 = latest_15m["volume_sma20"]

    if volume_sma20 and volume_sma20 > 0:
        volume_ratio = volume / volume_sma20
    else:
        volume_ratio = 0

    ema50_1h = latest_1h["ema50"]
    ema200_1h = latest_1h["ema200"]
    rsi_1h = latest_1h["rsi"]

    trend_1h_long = ema50_1h > ema200_1h
    trend_1h_short = ema50_1h < ema200_1h

    trend_15m_long = ema50 > ema200
    trend_15m_short = ema50 < ema200

    momentum_long = rsi > 52 and macd_hist > 0
    momentum_short = rsi < 48 and macd_hist < 0

    price_long = price > ema50
    price_short = price < ema50

    distance_from_ema20 = abs(price - ema20) / price * 100
    not_too_far = distance_from_ema20 <= 0.45

    atr_percent = atr / price * 100
    volatility_ok = atr_percent >= 0.10

    adx_ok = adx >= MIN_ADX_15M

    volume_ok = volume_ratio >= MIN_VOLUME_RATIO

    recent_start_close = df_15m.iloc[-5]["close"]
    recent_move_percent = abs(price - recent_start_close) / recent_start_close * 100
    recent_move_ok = recent_move_percent <= MAX_RECENT_MOVE_PERCENT

    technical_signal = "NO TRADE"

    if (
        trend_1h_long
        and trend_15m_long
        and momentum_long
        and price_long
        and not_too_far
        and volatility_ok
        and adx_ok
        and volume_ok
        and recent_move_ok
    ):
        technical_signal = "LONG"

    elif (
        trend_1h_short
        and trend_15m_short
        and momentum_short
        and price_short
        and not_too_far
        and volatility_ok
        and adx_ok
        and volume_ok
        and recent_move_ok
    ):
        technical_signal = "SHORT"

    final_signal = technical_signal
    block_reasons = []

    if technical_signal in ["LONG", "SHORT"]:
        if not spread_ok:
            final_signal = "NO TRADE"
            block_reasons.append("اسپرد بیشتر از حد مجاز است.")

        if not news_ok:
            final_signal = "NO TRADE"
            block_reasons.append("ریسک خبری بالا است.")

    if block_reasons:
        block_reason_text = " / ".join(block_reasons)
    else:
        block_reason_text = "-"

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
    if adx_ok:
        score += 1
    if volume_ok:
        score += 1
    if recent_move_ok:
        score += 1
    if spread_ok:
        score += 1
    if news_ok and news["risk"] in ["LOW", "MEDIUM"]:
        score += 1

    if score >= 10:
        strength = "Strong"
    elif score >= 7:
        strength = "Medium"
    else:
        strength = "Weak"

    if final_signal == "LONG":
        entry = ask
        tp = entry * (1 + TP_PERCENT / 100)
        sl = entry * (1 - SL_PERCENT / 100)
    elif final_signal == "SHORT":
        entry = bid
        tp = entry * (1 - TP_PERCENT / 100)
        sl = entry * (1 + SL_PERCENT / 100)
    else:
        entry = None
        tp = None
        sl = None

    paper_trade_message = ""
    paper_trade_created = False

    current_open_trades = count_open_trades(read_trades())

    if final_signal in ["LONG", "SHORT"] and current_open_trades < MAX_OPEN_PAPER_TRADES:
        new_trade = create_paper_trade(
            now_utc=now_utc,
            signal_candle_time=signal_candle_time,
            side=final_signal,
            entry=entry,
            tp=tp,
            sl=sl,
            strength=strength,
            rsi=rsi,
            macd_hist=macd_hist,
            adx=adx,
            volume_ratio=volume_ratio,
            recent_move_percent=recent_move_percent,
            atr_percent=atr_percent,
            spread_percent=spread_percent,
            news_risk=news["risk"],
            news_summary=news["summary"]
        )

        all_trades = read_trades()
        all_trades.append(new_trade)
        write_trades(all_trades)
        paper_trade_created = True

        paper_trade_message = f"""
🧪 PAPER TRADE OPENED

Trade ID: {new_trade["trade_id"]}
Side: {final_signal}

Entry: {entry:.2f}
TP: {tp:.2f}
SL: {sl:.2f}

This is paper trading only. No real order was sent.
"""

    elif final_signal in ["LONG", "SHORT"] and current_open_trades >= MAX_OPEN_PAPER_TRADES:
        block_reason_text += " / معامله فرضی باز وجود دارد؛ معامله جدید ثبت نشد."

    reasons = []

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

    if adx_ok:
        reasons.append(f"قدرت روند با ADX قابل قبول است: {adx:.2f}")
    else:
        reasons.append(f"ADX ضعیف است؛ احتمال رنج بودن بازار بالاتر است: {adx:.2f}")

    if volume_ok:
        reasons.append(f"حجم قابل قبول است؛ Volume Ratio = {volume_ratio:.2f}")
    else:
        reasons.append(f"حجم ضعیف است؛ Volume Ratio = {volume_ratio:.2f}")

    if recent_move_ok:
        reasons.append(f"حرکت چند کندل اخیر بیش از حد تند نیست: {recent_move_percent:.3f}%")
    else:
        reasons.append(f"حرکت چند کندل اخیر تند بوده؛ ریسک تعقیب قیمت بالاست: {recent_move_percent:.3f}%")

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

    if technical_signal == "NO TRADE":
        reasons.append("سیگنال تکنیکال کامل نشده است.")

    if technical_signal in ["LONG", "SHORT"] and final_signal == "NO TRADE":
        reasons.append("سیگنال تکنیکال وجود داشت، اما فیلترهای ریسک اجازه ورود ندادند.")

    news_events_text = format_news_events(news["events"])

    should_send_message_raw = (
        final_signal in ["LONG", "SHORT"]
        or technical_signal in ["LONG", "SHORT"]
        or bool(block_reasons)
        or paper_trade_created
        or len(trade_update_messages) > 0
    )

    duplicate_recent = False

    if should_send_message_raw:
        duplicate_recent = is_duplicate_recent(
            now_utc=now_utc,
            technical_signal=technical_signal,
            final_signal=final_signal,
            block_reason=block_reason_text
        )

    should_send_signal_message = should_send_message_raw and not duplicate_recent

    message = f"""
PAXG SIGNAL BOT

Symbol: {SYMBOL}
Main Timeframe: 15m
Confirm Timeframe: 1h

Technical Signal: {technical_signal}
Final Signal: {final_signal}
Strength: {strength}
Block Reason: {block_reason_text}

Signal Candle Time: {signal_candle_time}
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

--- 15m Closed Candle ---
EMA20: {ema20:.2f}
EMA50: {ema50:.2f}
EMA200: {ema200:.2f}
RSI: {rsi:.2f}
MACD Hist: {macd_hist:.4f}
ADX: {adx:.2f}
ATR: {atr:.2f}
ATR%: {atr_percent:.3f}%
Distance from EMA20: {distance_from_ema20:.3f}%
Volume Ratio: {volume_ratio:.2f}
Recent Move: {recent_move_percent:.3f}%

--- 1h Closed Candle ---
EMA50: {ema50_1h:.2f}
EMA200: {ema200_1h:.2f}
RSI: {rsi_1h:.2f}
"""

    if final_signal in ["LONG", "SHORT"]:
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

    if paper_trade_message:
        message += "\n" + paper_trade_message

    if duplicate_recent:
        message += f"\nDuplicate Filter: پیام مشابه در {DUPLICATE_SUPPRESSION_MINUTES} دقیقه اخیر ارسال شده؛ تلگرام دوباره ارسال نمی‌شود.\n"

    append_signal_log({
        "timestamp_utc": now_utc.strftime("%Y-%m-%d %H:%M:%S"),
        "signal_candle_time": signal_candle_time,
        "symbol": SYMBOL,
        "technical_signal": technical_signal,
        "final_signal": final_signal,
        "block_reason": block_reason_text,
        "strength": strength,
        "price": round(price, 4),
        "entry": round(entry, 4) if entry else "",
        "tp": round(tp, 4) if tp else "",
        "sl": round(sl, 4) if sl else "",
        "rsi_15m": round(rsi, 4),
        "macd_hist_15m": round(macd_hist, 6),
        "adx_15m": round(adx, 4),
        "volume_ratio_15m": round(volume_ratio, 4),
        "recent_move_percent": round(recent_move_percent, 4),
        "atr_percent": round(atr_percent, 6),
        "distance_from_ema20": round(distance_from_ema20, 6),
        "ema50_15m": round(ema50, 4),
        "ema200_15m": round(ema200, 4),
        "ema50_1h": round(ema50_1h, 4),
        "ema200_1h": round(ema200_1h, 4),
        "rsi_1h": round(rsi_1h, 4),
        "spread_percent": round(spread_percent, 6),
        "news_risk": news["risk"],
        "news_summary": news["summary"],
        "telegram_sent": "yes" if should_send_signal_message else "no",
        "duplicate_suppressed": "yes" if duplicate_recent else "no"
    })

    return message, should_send_signal_message, trade_update_messages


if __name__ == "__main__":
    try:
        message, should_send_signal_message, trade_update_messages = calculate_signal()

        print(message)

        for trade_message in trade_update_messages:
            send_telegram(trade_message)

        if should_send_signal_message:
            send_telegram(message)
        else:
            print("No signal Telegram message sent.")

    except Exception as e:
        error_message = f"❌ PAXG Signal Bot Error:\n{str(e)}"
        send_telegram(error_message)
        raise
