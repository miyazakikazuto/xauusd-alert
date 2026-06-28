"""
XAU/USD Signal Engine v3.0
- v2.0: state persistence via Gist, anti-spam logic
- v3.0: tambah --daily-summary mode untuk rekap harian jam 21:00 UTC
"""

import os
import sys
import json
import time
import argparse
import requests
from datetime import datetime, timezone, timedelta


# ─── CONFIG ───────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]
TWELVEDATA_API_KEY = os.environ["TWELVEDATA_API_KEY"]
STATE_GIST_ID      = os.environ["STATE_GIST_ID"]
GH_PAT_GIST        = os.environ["GH_PAT_GIST"]

GIST_FILENAME_STATE   = "xauusd_state.json"
GIST_FILENAME_HISTORY = "xauusd_daily_history.json"

SYMBOL    = "XAU/USD"
INTERVAL  = "5min"
EMA_FAST  = 9
EMA_MID   = 21
EMA_SLOW  = 50
RSI_PERIOD = 14
SCORE_THRESHOLD = 6
PRICE_MOVE_THRESHOLD = 5.0   # $5 minimum move untuk re-alert


# ─── GIST HELPERS ─────────────────────────────────────────────────────────────
def _gist_headers():
    return {
        "Authorization": f"Bearer {GH_PAT_GIST}",
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def gist_read(filename: str) -> dict:
    """Baca satu file dari Gist, return {} jika tidak ada atau parse error."""
    url = f"https://api.github.com/gists/{STATE_GIST_ID}"
    r = requests.get(url, headers=_gist_headers(), timeout=10)
    r.raise_for_status()
    files = r.json().get("files", {})
    if filename not in files:
        return {}
    raw_url = files[filename]["raw_url"]
    r2 = requests.get(raw_url, timeout=10)
    try:
        return json.loads(r2.text)
    except Exception:
        return {}


def gist_write(filename: str, data: dict):
    """Tulis / update satu file di Gist."""
    url = f"https://api.github.com/gists/{STATE_GIST_ID}"
    payload = {"files": {filename: {"content": json.dumps(data, indent=2)}}}
    r = requests.patch(url, headers=_gist_headers(), json=payload, timeout=10)
    r.raise_for_status()


# ─── TELEGRAM ────────────────────────────────────────────────────────────────
def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    }
    r = requests.post(url, json=payload, timeout=10)
    r.raise_for_status()
    return r.json()


# ─── MARKET DATA ──────────────────────────────────────────────────────────────
def fetch_ohlcv(outputsize=100) -> list[dict]:
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "outputsize": outputsize,
        "apikey": TWELVEDATA_API_KEY,
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    if "values" not in data:
        raise ValueError(f"TwelveData error: {data}")
    # Diurutkan dari terlama ke terbaru
    return list(reversed(data["values"]))


# ─── INDICATORS ───────────────────────────────────────────────────────────────
def ema(closes: list[float], period: int) -> list[float]:
    k = 2 / (period + 1)
    result = [closes[0]]
    for price in closes[1:]:
        result.append(price * k + result[-1] * (1 - k))
    return result


def rsi(closes: list[float], period: int = 14) -> float:
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains  = [d if d > 0 else 0 for d in deltas[-period:]]
    losses = [-d if d < 0 else 0 for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def macd(closes: list[float]):
    fast = ema(closes, 12)
    slow = ema(closes, 26)
    macd_line = [f - s for f, s in zip(fast, slow)]
    signal    = ema(macd_line, 9)
    hist      = macd_line[-1] - signal[-1]
    return macd_line[-1], signal[-1], hist


def bollinger(closes: list[float], period: int = 20):
    window = closes[-period:]
    mid = sum(window) / period
    std = (sum((x - mid) ** 2 for x in window) / period) ** 0.5
    return mid, mid + 2 * std, mid - 2 * std


def atr(candles: list[dict], period: int = 14) -> float:
    trs = []
    for i in range(1, len(candles)):
        h = float(candles[i]["high"])
        l = float(candles[i]["low"])
        pc = float(candles[i - 1]["close"])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-period:]) / period


# ─── SCORING ──────────────────────────────────────────────────────────────────
def compute_signal(candles: list[dict]) -> dict:
    closes = [float(c["close"]) for c in candles]
    price  = closes[-1]

    ema9_vals  = ema(closes, EMA_FAST)
    ema21_vals = ema(closes, EMA_MID)
    ema50_vals = ema(closes, EMA_SLOW)

    ema9  = ema9_vals[-1]
    ema21 = ema21_vals[-1]
    ema50 = ema50_vals[-1]

    rsi_val = rsi(closes, RSI_PERIOD)
    macd_line, macd_signal, macd_hist = macd(closes)
    bb_mid, bb_upper, bb_lower = bollinger(closes)
    atr_val = atr(candles)

    score_buy  = 0
    score_sell = 0

    # EMA trend
    if price > ema9 > ema21 > ema50:
        score_buy += 3
    elif price < ema9 < ema21 < ema50:
        score_sell += 3
    elif ema9 > ema21:
        score_buy += 1
    elif ema9 < ema21:
        score_sell += 1

    # RSI
    if rsi_val < 30:
        score_buy += 2
    elif rsi_val > 70:
        score_sell += 2
    elif rsi_val < 50:
        score_buy += 1
    else:
        score_sell += 1

    # MACD
    if macd_line > macd_signal and macd_hist > 0:
        score_buy += 2
    elif macd_line < macd_signal and macd_hist < 0:
        score_sell += 2

    # Bollinger
    if price <= bb_lower:
        score_buy += 2
    elif price >= bb_upper:
        score_sell += 2

    # Determine signal
    if score_buy >= SCORE_THRESHOLD:
        direction = "BUY"
        score = score_buy
    elif score_sell >= SCORE_THRESHOLD:
        direction = "SELL"
        score = score_sell
    else:
        direction = "WAIT"
        score = max(score_buy, score_sell)

    sl = atr_val * 1.5
    tp = atr_val * 3.0

    return {
        "direction": direction,
        "score": score,
        "score_buy": score_buy,
        "score_sell": score_sell,
        "price": round(price, 2),
        "rsi": round(rsi_val, 2),
        "ema9": round(ema9, 2),
        "ema21": round(ema21, 2),
        "ema50": round(ema50, 2),
        "macd_hist": round(macd_hist, 4),
        "bb_upper": round(bb_upper, 2),
        "bb_lower": round(bb_lower, 2),
        "atr": round(atr_val, 2),
        "sl": round(sl, 2),
        "tp": round(tp, 2),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ─── ALERT SIGNAL (mode default) ──────────────────────────────────────────────
def run_alert():
    now_utc = datetime.now(timezone.utc)
    if now_utc.weekday() >= 5:
        print("Weekend — skip")
        return
    if not (7 <= now_utc.hour < 21):
        print("Outside market hours — skip")
        return

    candles = fetch_ohlcv(100)
    sig     = compute_signal(candles)
    state   = gist_read(GIST_FILENAME_STATE)

    prev_direction = state.get("direction", "WAIT")
    prev_entry     = state.get("entry_price", sig["price"])

    direction_changed = sig["direction"] != prev_direction
    price_moved       = abs(sig["price"] - prev_entry) >= PRICE_MOVE_THRESHOLD
    has_signal        = sig["direction"] in ("BUY", "SELL")

    should_alert = has_signal and (direction_changed or price_moved)

    if should_alert:
        emoji = "🟢" if sig["direction"] == "BUY" else "🔴"
        msg = (
            f"{emoji} <b>XAU/USD Signal: {sig['direction']}</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"💰 Price : <b>${sig['price']}</b>\n"
            f"📊 Score : {sig['score']}/10\n"
            f"📈 RSI   : {sig['rsi']}\n"
            f"📉 MACD  : {sig['macd_hist']:+.4f}\n"
            f"━━━━━━━━━━━━━━\n"
            f"🛡️ SL    : ${round(sig['price'] - sig['sl'], 2) if sig['direction']=='BUY' else round(sig['price'] + sig['sl'], 2)}\n"
            f"🎯 TP    : ${round(sig['price'] + sig['tp'], 2) if sig['direction']=='BUY' else round(sig['price'] - sig['tp'], 2)}\n"
            f"⏰ {now_utc.strftime('%H:%M')} UTC"
        )
        send_telegram(msg)
        print(f"Alert sent: {sig['direction']} @ {sig['price']}")
        _append_daily_history(sig, alerted=True)
    else:
        print(f"No alert: {sig['direction']} score={sig['score']} price={sig['price']}")
        _append_daily_history(sig, alerted=False)  # tetap catat untuk summary

    # ✅ FIX 1: Selalu update state — bukan hanya saat alert
    new_state = {
        "direction": sig["direction"],
        "entry_price": sig["price"] if should_alert else prev_entry,
        "score": sig["score"],
        "last_checked": sig["timestamp"],
        "alerted": should_alert,
    }
    gist_write(GIST_FILENAME_STATE, new_state)


# ─── HISTORY HELPER ───────────────────────────────────────────────────────────
def _append_daily_history(sig: dict, alerted: bool):
    # ✅ FIX 2: Hapus early return — simpan semua entry
    # Tapi filter: hanya simpan tiap 30 menit jika tidak alert (hemat storage)
    now_utc = datetime.now(timezone.utc)
    
    if not alerted:
        # Throttle: simpan hanya di menit 0 atau 30 (setiap 30 menit)
        if now_utc.minute not in range(0, 5) and now_utc.minute not in range(30, 35):
            print(f"History throttled (non-alert) — skip write")
            return

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    history = gist_read(GIST_FILENAME_HISTORY)

    if today not in history:
        history[today] = []

    history[today].append({
        "time": now_utc.strftime("%H:%M"),
        "direction": sig["direction"],
        "price": sig["price"],
        "score": sig["score"],
        "rsi": sig["rsi"],
        "sl": sig["sl"],
        "tp": sig["tp"],
        "alerted": alerted,  # ← tambah flag ini untuk daily summary
    })

    history[today] = history[today][-50:]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    history = {k: v for k, v in history.items() if k >= cutoff}

    gist_write(GIST_FILENAME_HISTORY, history)


# ─── DAILY SUMMARY (mode baru) ────────────────────────────────────────────────
def run_daily_summary():
    """
    Mode daily summary: baca history hari ini dari Gist, hitung stats,
    kirim rekap ke Telegram. Dipanggil jam 21:00 UTC.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    history = gist_read(GIST_FILENAME_HISTORY)
    entries = history.get(today, [])

    # Ambil harga penutup saat ini untuk referensi
    try:
        candles = fetch_ohlcv(5)
        current_price = float(candles[-1]["close"])
    except Exception:
        current_price = None

    if not entries:
        msg = (
            f"📋 <b>XAU/USD Daily Summary</b>\n"
            f"📅 {today} | Close London Session\n"
            f"━━━━━━━━━━━━━━\n"
            f"Tidak ada sinyal aktif hari ini.\n"
        )
        if current_price:
            msg += f"💰 Harga penutup : <b>${current_price:,.2f}</b>"
        send_telegram(msg)
        print("Daily summary sent: no signals today")
        return

    # Hitung statistik
    buy_signals  = [e for e in entries if e["direction"] == "BUY"]
    sell_signals = [e for e in entries if e["direction"] == "SELL"]
    total        = len(entries)

    # Dominant direction
    dominant = "BUY" if len(buy_signals) >= len(sell_signals) else "SELL"
    dominant_pct = round(
        (len(buy_signals) / total * 100) if dominant == "BUY"
        else (len(sell_signals) / total * 100)
    )

    # First dan last signal
    first = entries[0]
    last  = entries[-1]

    # Average score
    avg_score = round(sum(e["score"] for e in entries) / total, 1)

    # Price range dari entries
    prices = [e["price"] for e in entries]
    high_price = max(prices)
    low_price  = min(prices)

    # Bias emoji
    bias_emoji = "🟢" if dominant == "BUY" else "🔴"

    # Format signal timeline (max 5 terakhir)
    timeline_entries = entries[-5:] if len(entries) > 5 else entries
    timeline_lines = []
    for e in timeline_entries:
        e_emoji = "🟢" if e["direction"] == "BUY" else "🔴"
        timeline_lines.append(f"  {e['time']} {e_emoji} {e['direction']} @ ${e['price']}")
    timeline_str = "\n".join(timeline_lines)

    msg = (
        f"📊 <b>XAU/USD Daily Summary</b>\n"
        f"📅 {today} | London Close 21:00 UTC\n"
        f"━━━━━━━━━━━━━━\n"
        f"{bias_emoji} <b>Bias Hari Ini: {dominant}</b> ({dominant_pct}%)\n"
        f"\n"
        f"📈 Total Signal  : {total} alert\n"
        f"🟢 BUY Signal    : {len(buy_signals)}x\n"
        f"🔴 SELL Signal   : {len(sell_signals)}x\n"
        f"⭐ Avg Score     : {avg_score}/10\n"
        f"\n"
        f"💰 Range Harga   : ${low_price:,.2f} – ${high_price:,.2f}\n"
    )

    if current_price:
        msg += f"📍 Harga Sekarang: <b>${current_price:,.2f}</b>\n"

    msg += (
        f"\n"
        f"⏱️ <b>5 Signal Terakhir:</b>\n"
        f"{timeline_str}\n"
        f"━━━━━━━━━━━━━━\n"
        f"🕘 First: {first['time']} UTC | Last: {last['time']} UTC"
    )

    send_telegram(msg)
    print(f"Daily summary sent: {total} signals, dominant={dominant}")

    # Reset history hari ini setelah summary dikirim (opsional)
    # Hapus hari ini dari history supaya besok fresh
    # history.pop(today, None)
    # gist_write(GIST_FILENAME_HISTORY, history)
    # — Dikomentari dulu, bisa diaktifkan jika mau auto-reset


# ─── ENTRYPOINT ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="XAU/USD Signal Engine v3.0")
    parser.add_argument(
        "--mode",
        choices=["alert", "daily-summary"],
        default="alert",
        help="Mode: 'alert' (default) atau 'daily-summary'",
    )
    args = parser.parse_args()

    if args.mode == "daily-summary":
        run_daily_summary()
    else:
        run_alert()
