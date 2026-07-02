"""
Multi-Asset Signal Engine v3.2
- v2.0: state persistence via Gist, anti-spam logic
- v3.0: tambah --mode daily-summary untuk rekap harian jam 21:00 UTC
- v3.1: config-driven multi-symbol (XAU/USD + BTC/USD via Binance),
        per-symbol ATR multiplier, per-symbol Gist state key,
        24/7 market hours untuk crypto
- v3.2: INTERVAL 5min → 15min (latensi cron ±10 menit membuat M5 tidak
        andal — window trigger bisa melompati satu candle penuh di M5,
        tapi masih di dalam satu candle yang sama di M15); RSI & Bollinger
        Bands diubah dari sinyal kontrarian independen menjadi timing
        signal yang di-gate oleh trend_bias (EMA9/21/50) — mencegah
        sistem "menangkap pisau jatuh" (mis. BUY saat RSI oversold di
        tengah downtrend kuat). Lihat README bagian "Aturan Entry" untuk
        spesifikasi skor lengkap — kode ini WAJIB selalu match dengan
        spek di README tersebut.
"""

import os
import sys
import json
import time
import argparse
import requests
from datetime import datetime, timezone, timedelta

# ─── TIMEZONE ─────────────────────────────────────────────────────────────────
WIB = timezone(timedelta(hours=7))

def now_wib() -> datetime:
    return datetime.now(WIB)

def to_wib_str(dt_utc: datetime, fmt: str = "%H:%M") -> str:
    """Konversi naive UTC datetime ke string WIB."""
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    return dt_utc.astimezone(WIB).strftime(fmt)


# ─── CONFIG (SECRETS) ──────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]
TWELVEDATA_API_KEY = os.environ["TWELVEDATA_API_KEY"]
STATE_GIST_ID      = os.environ["STATE_GIST_ID"]
GH_PAT_GIST        = os.environ["GH_PAT_GIST"]

INTERVAL   = "15min"
EMA_FAST   = 9
EMA_MID    = 21
EMA_SLOW   = 50
RSI_PERIOD = 14
SCORE_THRESHOLD = 6

# ─── CONFIG (PER-SYMBOL) ────────────────────────────────────────────────────────
# Setiap symbol punya: parameter TwelveData, nama file Gist sendiri (state +
# history terpisah supaya tidak saling timpa di Gist yang sama), ATR multiplier
# sendiri (BTC lebih volatile → SL lebih lebar), threshold anti-spam sendiri,
# dan flag trades_24_7 (BTC tidak libur weekend/jam market seperti forex/gold).
SYMBOLS = {
    "xau": {
        "td_symbol":            "XAU/USD",
        "td_exchange":          None,
        "display":              "XAU/USD",
        "asset_emoji":          "🥇",
        "gist_state":           "xauusd_state.json",
        "gist_history":         "xauusd_daily_history.json",
        "atr_sl_mult":          1.5,
        "atr_tp_mult":          3.0,
        "price_move_threshold": 5.0,
        "trades_24_7":          False,
    },
    "btc": {
        "td_symbol":            "BTC/USD",
        "td_exchange":          "Binance",
        "display":              "BTC/USD",
        "asset_emoji":          "₿",
        "gist_state":           "btcusd_state.json",
        "gist_history":         "btcusd_daily_history.json",
        "atr_sl_mult":          2.5,
        "atr_tp_mult":          3.0,
        "price_move_threshold": 50.0,
        "trades_24_7":          True,
    },
}


def get_symbol_config(symbol_key: str) -> dict:
    if symbol_key not in SYMBOLS:
        raise ValueError(f"Unknown symbol '{symbol_key}'. Pilihan: {list(SYMBOLS.keys())}")
    return SYMBOLS[symbol_key]


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
    """Tulis / update satu file di Gist. Retry sekali jika 409 Conflict."""
    url = f"https://api.github.com/gists/{STATE_GIST_ID}"
    payload = {"files": {filename: {"content": json.dumps(data, indent=2)}}}
    for attempt in range(2):
        r = requests.patch(url, headers=_gist_headers(), json=payload, timeout=10)
        if r.status_code == 409 and attempt == 0:
            print(f"Gist 409 conflict — retry setelah 2 detik...")
            time.sleep(2)
            continue
        r.raise_for_status()
        break


def gist_write_multi(files: dict):
    """Tulis beberapa file ke Gist dalam SATU request (hindari 409 race condition).
    files = {filename: data_dict, ...}
    """
    url = f"https://api.github.com/gists/{STATE_GIST_ID}"
    payload = {
        "files": {
            fname: {"content": json.dumps(fdata, indent=2)}
            for fname, fdata in files.items()
        }
    }
    for attempt in range(2):
        r = requests.patch(url, headers=_gist_headers(), json=payload, timeout=10)
        if r.status_code == 409 and attempt == 0:
            print(f"Gist 409 conflict — retry setelah 2 detik...")
            time.sleep(2)
            continue
        r.raise_for_status()
        break


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
def fetch_ohlcv(cfg: dict, outputsize=100) -> list[dict]:
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": cfg["td_symbol"],
        "interval": INTERVAL,
        "outputsize": outputsize,
        "apikey": TWELVEDATA_API_KEY,
    }
    if cfg.get("td_exchange"):
        params["exchange"] = cfg["td_exchange"]
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    if "values" not in data:
        raise ValueError(f"TwelveData error [{cfg['display']}]: {data}")
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
def compute_signal(cfg: dict, candles: list[dict]) -> dict:
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

    # ─── TREND BIAS (gerbang arah) ──────────────────────────────────────────
    # trend_bias dihitung SEKALI dari EMA stack, lalu dipakai sebagai gate
    # untuk RSI & Bollinger di bawah. Ini satu-satunya sumber kebenaran arah
    # trend — jangan duplikasi logika "naik/turun" di indikator lain.
    if price > ema9 > ema21 > ema50:
        trend_bias = "up"
    elif price < ema9 < ema21 < ema50:
        trend_bias = "down"
    else:
        trend_bias = "neutral"

    # EMA trend (kontribusi skor arah)
    if trend_bias == "up":
        score_buy += 3
    elif trend_bias == "down":
        score_sell += 3
    elif ema9 > ema21:
        score_buy += 1
    elif ema9 < ema21:
        score_sell += 1

    # RSI — TIMING signal, bukan kontrarian independen.
    # RSI dip hanya dihitung sebagai skor BUY kalau trend_bias != "down"
    # (dip di dalam uptrend/netral). RSI rally hanya skor SELL kalau
    # trend_bias != "up". Kalau trend_bias berlawanan arah dengan RSI,
    # sinyal diabaikan sepenuhnya — tidak menyumbang skor ke arah mana pun.
    if rsi_val < 40 and trend_bias != "down":
        score_buy += 2
    elif rsi_val > 60 and trend_bias != "up":
        score_sell += 2

    # MACD — konfirmasi momentum, tidak di-gate (independen dari trend_bias
    # by design, karena MACD sendiri sudah mengukur arah momentum).
    if macd_line > macd_signal and macd_hist > 0:
        score_buy += 2
    elif macd_line < macd_signal and macd_hist < 0:
        score_sell += 2

    # Bollinger Bands — TIMING signal, sama seperti RSI, di-gate trend_bias.
    if price <= bb_lower and trend_bias != "down":
        score_buy += 2
    elif price >= bb_upper and trend_bias != "up":
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

    sl = atr_val * cfg["atr_sl_mult"]
    tp = atr_val * cfg["atr_tp_mult"]

    return {
        "direction": direction,
        "trend_bias": trend_bias,
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
def run_alert(cfg: dict):
    now_utc = datetime.now(timezone.utc)

    if not cfg["trades_24_7"]:
        if now_utc.weekday() >= 5:
            print(f"[{cfg['display']}] Weekend — skip")
            return
        if not (7 <= now_utc.hour < 21):
            print(f"[{cfg['display']}] Outside market hours — skip")
            return

    candles = fetch_ohlcv(cfg, 100)
    sig     = compute_signal(cfg, candles)
    state   = gist_read(cfg["gist_state"])

    prev_direction = state.get("direction", "WAIT")
    prev_entry     = state.get("entry_price", sig["price"])

    direction_changed = sig["direction"] != prev_direction
    price_moved       = abs(sig["price"] - prev_entry) >= cfg["price_move_threshold"]
    has_signal        = sig["direction"] in ("BUY", "SELL")

    should_alert = has_signal and (direction_changed or price_moved)

    # Timestamp WIB untuk display
    wib_now = now_wib()
    wib_time_str = wib_now.strftime("%H:%M")

    if should_alert:
        emoji = "🟢" if sig["direction"] == "BUY" else "🔴"
        msg = (
            f"{emoji} {cfg['asset_emoji']} <b>{cfg['display']} Signal: {sig['direction']}</b>\n"
            f"━━━━━━━━━━━━━━\n"
            f"💰 Price : <b>${sig['price']}</b>\n"
            f"📊 Score : {sig['score']}/10\n"
            f"🧭 Trend : {sig['trend_bias']}\n"
            f"📈 RSI   : {sig['rsi']}\n"
            f"📉 MACD  : {sig['macd_hist']:+.4f}\n"
            f"━━━━━━━━━━━━━━\n"
            f"🛡️ SL    : ${round(sig['price'] - sig['sl'], 2) if sig['direction']=='BUY' else round(sig['price'] + sig['sl'], 2)}\n"
            f"🎯 TP    : ${round(sig['price'] + sig['tp'], 2) if sig['direction']=='BUY' else round(sig['price'] - sig['tp'], 2)}\n"
            f"⏰ {wib_time_str} WIB"
        )
        send_telegram(msg)
        print(f"[{cfg['display']}] Alert sent: {sig['direction']} @ {sig['price']}")

    else:
        print(f"[{cfg['display']}] No alert: {sig['direction']} score={sig['score']} price={sig['price']}")

    # SL/TP dalam bentuk price level aktual (bukan jarak ATR mentah), sama
    # seperti yang ditampilkan di pesan Telegram — biar konsisten untuk audit.
    if sig["direction"] == "BUY":
        sl_price = round(sig["price"] - sig["sl"], 2)
        tp_price = round(sig["price"] + sig["tp"], 2)
    elif sig["direction"] == "SELL":
        sl_price = round(sig["price"] + sig["sl"], 2)
        tp_price = round(sig["price"] - sig["tp"], 2)
    else:
        sl_price = None
        tp_price = None

    # ✅ FIX 409: Gabung state + history dalam SATU gist_write_multi call
    new_state = {
        "direction": sig["direction"],
        "entry_price": sig["price"] if should_alert else prev_entry,
        "score": sig["score"],
        "sl": sl_price,
        "tp": tp_price,
        "time": wib_now.strftime("%Y-%m-%d %H:%M") + " WIB",
        "alerted": should_alert,
    }
    updated_history = _build_history_update(cfg, sig, alerted=should_alert)

    if updated_history is not None:
        # Tulis state + history sekaligus → satu PATCH request
        gist_write_multi({
            cfg["gist_state"]: new_state,
            cfg["gist_history"]: updated_history,
        })
    else:
        # History di-throttle, tulis state saja
        gist_write(cfg["gist_state"], new_state)


# ─── HISTORY HELPER ───────────────────────────────────────────────────────────
def _build_history_update(cfg: dict, sig: dict, alerted: bool) -> dict | None:
    """
    Bangun dict history yang sudah diupdate.
    Return None jika throttled (tidak perlu write).
    Timestamp disimpan dalam WIB.
    """
    now_utc = datetime.now(timezone.utc)

    if not alerted:
        # Throttle: simpan tiap 10 menit (menit 0,10,20,30,40,50)
        if now_utc.minute % 10 not in range(0, 3):
            print(f"[{cfg['display']}] History throttled (non-alert) — skip write")
            return None

    wib_now    = now_utc.astimezone(WIB)
    today      = wib_now.strftime("%Y-%m-%d")   # tanggal WIB
    time_str   = wib_now.strftime("%H:%M")       # jam WIB

    history = gist_read(cfg["gist_history"])

    if today not in history:
        history[today] = []

    history[today].insert(0, {
        "time": time_str,
        "direction": sig["direction"],
        "trend_bias": sig["trend_bias"],
        "price": sig["price"],
        "score": sig["score"],
        "rsi": sig["rsi"],
        "sl": sig["sl"],
        "tp": sig["tp"],
        "alerted": alerted,
    })

    # Newest-first: entry terbaru selalu di index 0. Ambil 50 TERBARU
    # (bukan 50 terakhir dari urutan lama) → slice dari depan.
    history[today] = history[today][:50]
    cutoff = (wib_now - timedelta(days=7)).strftime("%Y-%m-%d")
    history = {k: v for k, v in history.items() if k >= cutoff}

    return history


# ─── DAILY SUMMARY (mode baru) ────────────────────────────────────────────────
def run_daily_summary(cfg: dict):
    """
    Mode daily summary: baca history hari ini dari Gist, hitung stats,
    kirim rekap ke Telegram. Dipanggil jam 21:00 UTC (= 04:00 WIB besok).
    History key sudah dalam WIB, jadi pakai today WIB.
    """
    today = now_wib().strftime("%Y-%m-%d")   # ← WIB date
    history = gist_read(cfg["gist_history"])
    entries = history.get(today, [])

    # Ambil harga penutup saat ini untuk referensi
    try:
        candles = fetch_ohlcv(cfg, 5)
        current_price = float(candles[-1]["close"])
    except Exception:
        current_price = None

    if not entries:
        msg = (
            f"📋 {cfg['asset_emoji']} <b>{cfg['display']} Daily Summary</b>\n"
            f"📅 {today} | Close London Session\n"
            f"━━━━━━━━━━━━━━\n"
            f"Tidak ada sinyal aktif hari ini.\n"
        )
        if current_price:
            msg += f"💰 Harga penutup : <b>${current_price:,.2f}</b>"
        send_telegram(msg)
        print(f"[{cfg['display']}] Daily summary sent: no signals today")
        return

    # Hitung statistik
    buy_signals  = [e for e in entries if e["direction"] == "BUY"]
    sell_signals = [e for e in entries if e["direction"] == "SELL"]
    wait_signals = [e for e in entries if e["direction"] == "WAIT"]
    total        = len(entries)

    # ✅ FIX: Handle kasus semua WAIT
    alerted_entries = [e for e in entries if e["direction"] in ("BUY", "SELL")]

    if not alerted_entries:
        dominant = "WAIT"
        dominant_pct = 100
        bias_emoji = "⚪"
    else:
        dominant = "BUY" if len(buy_signals) >= len(sell_signals) else "SELL"
        dominant_pct = round(
            (len(buy_signals) / len(alerted_entries) * 100) if dominant == "BUY"
            else (len(sell_signals) / len(alerted_entries) * 100)
        )
        bias_emoji = "🟢" if dominant == "BUY" else "🔴"

    # entries sekarang newest-first (index 0 = paling baru)
    # → first entry kronologis = paling akhir di list, last entry = index 0
    first = entries[-1]
    last  = entries[0]

    # Average score
    avg_score = round(sum(e["score"] for e in entries) / total, 1)

    # Price range dari entries
    prices = [e["price"] for e in entries]
    high_price = max(prices)
    low_price  = min(prices)

    # ✅ FIX: Emoji WAIT di timeline
    def dir_emoji(d):
        return "🟢" if d == "BUY" else ("🔴" if d == "SELL" else "⚪")

    # Format signal timeline (5 terbaru, urutan newest-first — sama dengan Gist)
    timeline_entries = entries[:5]
    timeline_lines = []
    for e in timeline_entries:
        timeline_lines.append(
            f"  {e['time']} {dir_emoji(e['direction'])} {e['direction']} @ ${e['price']}"
        )
    timeline_str = "\n".join(timeline_lines)

    msg = (
        f"📊 {cfg['asset_emoji']} <b>{cfg['display']} Daily Summary</b>\n"
        f"📅 {today} | London Close 04:00 WIB\n"
        f"━━━━━━━━━━━━━━\n"
        f"{bias_emoji} <b>Bias Hari Ini: {dominant}</b> ({dominant_pct}%)\n"
        f"\n"
        f"📈 Total Tercatat : {total} data\n"
        f"🟢 BUY Signal     : {len(buy_signals)}x\n"
        f"🔴 SELL Signal    : {len(sell_signals)}x\n"
        f"⚪ WAIT           : {len(wait_signals)}x\n"
        f"⭐ Avg Score      : {avg_score}/10\n"
        f"\n"
        f"💰 Range Harga    : ${low_price:,.2f} – ${high_price:,.2f}\n"
    )

    if current_price:
        msg += f"📍 Harga Sekarang : <b>${current_price:,.2f}</b>\n"

    msg += (
        f"\n"
        f"⏱️ <b>5 Data Terakhir:</b>\n"
        f"{timeline_str}\n"
        f"━━━━━━━━━━━━━━\n"
        f"🕘 First: {first['time']} WIB | Last: {last['time']} WIB"
    )

    send_telegram(msg)
    print(f"[{cfg['display']}] Daily summary sent: {total} entries, dominant={dominant}")

    # Reset history hari ini setelah summary dikirim (opsional)
    # Hapus hari ini dari history supaya besok fresh
    # history.pop(today, None)
    # gist_write(cfg["gist_history"], history)
    # — Dikomentari dulu, bisa diaktifkan jika mau auto-reset


# ─── ENTRYPOINT ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Multi-Asset Signal Engine v3.1")
    parser.add_argument(
        "--mode",
        choices=["alert", "daily-summary"],
        default="alert",
        help="Mode: 'alert' (default) atau 'daily-summary'",
    )
    parser.add_argument(
        "--symbol",
        choices=list(SYMBOLS.keys()),
        default="xau",
        help="Symbol: 'xau' (default, XAU/USD) atau 'btc' (BTC/USD via Binance)",
    )
    args = parser.parse_args()

    symbol_cfg = get_symbol_config(args.symbol)

    if args.mode == "daily-summary":
        run_daily_summary(symbol_cfg)
    else:
        run_alert(symbol_cfg)
