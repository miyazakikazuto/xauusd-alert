"""
╔══════════════════════════════════════════════════════════════════╗
║          XAUUSD TRADING ALERT - SIGNAL ENGINE v2.0              ║
║          Strategi: Multi-Indicator H1 → Hold 4 Jam              ║
║          Author: AI Trading System                               ║
║          v2.0: State Persistence via GitHub Gist                ║
╚══════════════════════════════════════════════════════════════════╝

PERUBAHAN v2.0:
================================
- State persistence via GitHub Gist
- Alert hanya dikirim saat sinyal BERUBAH
- WAIT → tidak spam, hanya kirim sekali saat transisi ke WAIT
- BUY/SELL → kirim saat pertama muncul + update entry price jika berubah signifikan
"""

import os
import sys
import json
import logging
import pytz
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf
import ta
import requests

# ─── SETUP LOGGING ────────────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(f"logs/signal_{datetime.now().strftime('%Y%m%d')}.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger(__name__)


# ─── KONFIGURASI ──────────────────────────────────────────────────────────────
CONFIG = {
    "symbol"         : "XAUUSD=X",
    "symbol_display" : "XAU/USD",
    "timeframe"      : "1h",
    "lookback_bars"  : 200,

    "ema_fast"       : 9,
    "ema_mid"        : 21,
    "ema_slow"       : 50,
    "rsi_period"     : 14,
    "rsi_ob"         : 70,
    "rsi_os"         : 30,
    "bb_period"      : 20,
    "bb_std"         : 2,
    "atr_period"     : 14,

    "sl_multiplier"  : 1.5,
    "tp_multiplier"  : 3.0,

    "min_score"      : 6,

    "active_hours_utc": list(range(7, 21)),

    # State persistence
    # Nama file di dalam Gist (bebas, tapi harus konsisten)
    "gist_filename"  : "xauusd_last_signal.json",
    
    # Threshold harga berubah untuk re-alert BUY/SELL yang sama
    # Contoh: 5.0 = kalau harga bergerak >$5 dari entry awal, kirim ulang
    "price_change_threshold": 5.0,
}


# ═══════════════════════════════════════════════════════════════════════════════
#  STATE MANAGER — GitHub Gist
# ═══════════════════════════════════════════════════════════════════════════════

def _gist_headers() -> dict:
    """Header auth untuk GitHub API"""
    token = os.environ.get("GITHUB_TOKEN", "")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def load_last_state() -> dict:
    """
    Ambil state terakhir dari GitHub Gist.
    Return dict kosong kalau belum ada / error.
    
    State format:
    {
        "signal": "BUY" | "SELL" | "WAIT",
        "entry": 3050.00,
        "score": 7,
        "timestamp": "2025-01-01T10:00:00"
    }
    """
    gist_id = os.environ.get("STATE_GIST_ID", "")
    
    if not gist_id:
        log.warning("STATE_GIST_ID tidak diset — state persistence dinonaktifkan, selalu kirim alert")
        return {}
    
    try:
        resp = requests.get(
            f"https://api.github.com/gists/{gist_id}",
            headers=_gist_headers(),
            timeout=10
        )
        
        if resp.status_code == 404:
            log.info("Gist tidak ditemukan — state kosong (pertama kali run)")
            return {}
        
        resp.raise_for_status()
        gist_data = resp.json()
        
        filename = CONFIG["gist_filename"]
        if filename not in gist_data["files"]:
            log.info(f"File '{filename}' belum ada di Gist — state kosong")
            return {}
        
        content = gist_data["files"][filename]["content"]
        state = json.loads(content)
        log.info(f"State loaded: sinyal={state.get('signal')} | entry={state.get('entry')}")
        return state
        
    except Exception as e:
        log.error(f"Gagal load state dari Gist: {e}")
        return {}


def save_current_state(signal: dict) -> bool:
    """
    Simpan state sinyal terkini ke GitHub Gist.
    Kalau Gist ID belum ada, buat Gist baru dan print ID-nya.
    """
    gist_id = os.environ.get("STATE_GIST_ID", "")
    
    state = {
        "signal"    : signal["signal"],
        "entry"     : signal.get("entry"),
        "score"     : signal.get("score"),
        "price"     : signal.get("price"),
        "timestamp" : datetime.now(pytz.utc).isoformat(),
    }
    
    payload = {
        "description": "XAUUSD Signal State — Auto Updated",
        "public": False,
        "files": {
            CONFIG["gist_filename"]: {
                "content": json.dumps(state, indent=2)
            }
        }
    }
    
    try:
        if gist_id:
            # Update Gist yang sudah ada
            resp = requests.patch(
                f"https://api.github.com/gists/{gist_id}",
                headers=_gist_headers(),
                json=payload,
                timeout=10
            )
        else:
            # Buat Gist baru (pertama kali setup)
            resp = requests.post(
                "https://api.github.com/gists",
                headers=_gist_headers(),
                json=payload,
                timeout=10
            )
            if resp.status_code == 201:
                new_id = resp.json()["id"]
                log.info(f"✅ Gist baru dibuat! ID: {new_id}")
                log.info(f"   → Tambahkan STATE_GIST_ID={new_id} ke GitHub Secrets")
        
        if resp.status_code in [200, 201]:
            log.info(f"✅ State berhasil disimpan: {state['signal']}")
            return True
        else:
            log.error(f"Gagal simpan state: {resp.status_code} | {resp.text}")
            return False
            
    except Exception as e:
        log.error(f"Error simpan state: {e}")
        return False


def should_send_alert(current_signal: dict, last_state: dict) -> tuple[bool, str]:
    """
    Logika kapan alert dikirim:
    
    KIRIM jika:
    1. Belum pernah ada state (pertama kali run)
    2. Sinyal berubah: WAIT→BUY, BUY→SELL, SELL→WAIT, dll
    3. Sinyal sama BUY/SELL tapi harga bergerak > threshold dari entry awal
    
    SKIP jika:
    1. WAIT→WAIT (tidak ada yang berubah)
    2. BUY→BUY dan harga tidak bergerak signifikan
    3. SELL→SELL dan harga tidak bergerak signifikan
    """
    current = current_signal["signal"]
    
    # Kondisi 1: Tidak ada state sebelumnya
    if not last_state:
        return True, "Pertama kali run — kirim initial state"
    
    last = last_state.get("signal", "")
    
    # Kondisi 2: Sinyal berubah
    if current != last:
        return True, f"Sinyal berubah: {last} → {current}"
    
    # Kondisi 3: Sinyal sama BUY/SELL, cek apakah harga bergerak signifikan
    if current in ["BUY", "SELL"]:
        last_entry = last_state.get("entry") or last_state.get("price", 0)
        current_price = current_signal.get("price", 0)
        
        if last_entry and current_price:
            price_diff = abs(current_price - last_entry)
            threshold = CONFIG["price_change_threshold"]
            
            if price_diff >= threshold:
                return True, f"Harga bergerak ${price_diff:.1f} dari entry awal (threshold: ${threshold})"
    
    # Default: skip
    reason = f"Sinyal tetap {current} — tidak ada perubahan signifikan"
    return False, reason


# ─── DATA FETCHER ─────────────────────────────────────────────────────────────
def fetch_ohlcv(symbol: str, timeframe: str, bars: int) -> pd.DataFrame:
    """Ambil data OHLCV dari Twelve Data API"""
    log.info(f"Fetching data: XAU/USD | TF: {timeframe} | Bars: {bars}")
    
    api_key = os.environ.get("TWELVEDATA_API_KEY", "demo")
    
    url = (
        f"https://api.twelvedata.com/time_series"
        f"?symbol=XAU/USD"
        f"&interval=1h"
        f"&outputsize={bars}"
        f"&apikey={api_key}"
    )
    
    resp = requests.get(url, timeout=30)
    data = resp.json()
    
    if "values" not in data:
        raise ValueError(f"API error: {data.get('message', data)}")
    
    rows = data["values"]
    df = pd.DataFrame(rows)
    df = df.rename(columns={
        "datetime": "date",
        "open": "open", "high": "high",
        "low": "low",   "close": "close",
        "volume": "volume"
    })
    
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    
    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col])
    
    if "volume" not in df.columns:
        df["volume"] = 1000
    else:
        df["volume"] = pd.to_numeric(df["volume"])
    
    df = df[["open","high","low","close","volume"]].dropna()
    
    if df.empty:
        raise ValueError("Data kosong dari Twelve Data")
    
    log.info(f"Data berhasil: {len(df)} candle | Terakhir: {df.index[-1]}")
    return df


# ─── INDIKATOR ENGINE ─────────────────────────────────────────────────────────
def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Hitung semua indikator teknikal"""
    
    df["ema9"]  = ta.trend.EMAIndicator(df["close"], window=CONFIG["ema_fast"]).ema_indicator()
    df["ema21"] = ta.trend.EMAIndicator(df["close"], window=CONFIG["ema_mid"]).ema_indicator()
    df["ema50"] = ta.trend.EMAIndicator(df["close"], window=CONFIG["ema_slow"]).ema_indicator()
    
    df["rsi"] = ta.momentum.RSIIndicator(df["close"], window=CONFIG["rsi_period"]).rsi()
    
    macd_obj = ta.trend.MACD(df["close"])
    df["macd"]        = macd_obj.macd()
    df["macd_signal"] = macd_obj.macd_signal()
    df["macd_hist"]   = macd_obj.macd_diff()
    
    bb = ta.volatility.BollingerBands(
        df["close"], 
        window=CONFIG["bb_period"], 
        window_dev=CONFIG["bb_std"]
    )
    df["bb_upper"] = bb.bollinger_hband()
    df["bb_mid"]   = bb.bollinger_mavg()
    df["bb_lower"] = bb.bollinger_lband()
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"] * 100
    
    df["atr"] = ta.volatility.AverageTrueRange(
        df["high"], df["low"], df["close"],
        window=CONFIG["atr_period"]
    ).average_true_range()
    
    df["vol_ma"]    = df["volume"].rolling(window=20).mean()
    df["vol_ratio"] = df["volume"] / df["vol_ma"]
    
    df["candle_body"]  = abs(df["close"] - df["open"])
    df["candle_range"] = df["high"] - df["low"]
    df["is_bullish"]   = df["close"] > df["open"]
    
    return df.dropna()


# ─── SIGNAL ANALYZER ──────────────────────────────────────────────────────────
def analyze_signal(df: pd.DataFrame) -> dict:
    """Scoring System (0-10)"""
    c = df.iloc[-1]
    p = df.iloc[-2]
    
    result = {
        "timestamp"    : str(df.index[-1]),
        "price"        : round(c["close"], 2),
        "signal"       : "WAIT",
        "score"        : 0,
        "score_max"    : 10,
        "conditions"   : {},
        "entry"        : None,
        "stop_loss"    : None,
        "take_profit"  : None,
        "atr"          : round(c["atr"], 2),
        "rsi"          : round(c["rsi"], 1),
        "indicators"   : {
            "ema9"        : round(c["ema9"], 2),
            "ema21"       : round(c["ema21"], 2),
            "ema50"       : round(c["ema50"], 2),
            "macd"        : round(c["macd"], 3),
            "macd_signal" : round(c["macd_signal"], 3),
            "macd_hist"   : round(c["macd_hist"], 3),
            "bb_upper"    : round(c["bb_upper"], 2),
            "bb_lower"    : round(c["bb_lower"], 2),
            "bb_width"    : round(c["bb_width"], 2),
            "vol_ratio"   : round(c["vol_ratio"], 2),
        }
    }

    # ── BUY SCORING ───────────────────────────────────────────────
    buy_score = 0
    buy_conditions = {}

    ema_bull = c["ema9"] > c["ema21"] > c["ema50"]
    buy_conditions["EMA Stack Bullish"] = "✅" if ema_bull else "❌"
    if ema_bull: buy_score += 3

    ema_cross_bull = (c["ema9"] > c["ema21"]) and (p["ema9"] <= p["ema21"])
    buy_conditions["EMA9 Cross EMA21 Baru"] = "✅" if ema_cross_bull else "⚪"
    if ema_cross_bull: buy_score += 2

    rsi_bull = 50 <= c["rsi"] <= CONFIG["rsi_ob"]
    buy_conditions["RSI Zona Bullish (50-70)"] = "✅" if rsi_bull else "❌"
    if rsi_bull: buy_score += 2

    macd_bull = c["macd_hist"] > 0 and c["macd_hist"] > p["macd_hist"]
    buy_conditions["MACD Histogram Bullish"] = "✅" if macd_bull else "❌"
    if macd_bull: buy_score += 2

    vol_ok = c["vol_ratio"] > 1.2
    buy_conditions["Volume Konfirmasi"] = "✅" if vol_ok else "⚪"
    if vol_ok: buy_score += 1

    # ── SELL SCORING ──────────────────────────────────────────────
    sell_score = 0
    sell_conditions = {}

    ema_bear = c["ema9"] < c["ema21"] < c["ema50"]
    sell_conditions["EMA Stack Bearish"] = "✅" if ema_bear else "❌"
    if ema_bear: sell_score += 3

    ema_cross_bear = (c["ema9"] < c["ema21"]) and (p["ema9"] >= p["ema21"])
    sell_conditions["EMA9 Cross EMA21 Baru"] = "✅" if ema_cross_bear else "⚪"
    if ema_cross_bear: sell_score += 2

    rsi_bear = CONFIG["rsi_os"] <= c["rsi"] <= 50
    sell_conditions["RSI Zona Bearish (30-50)"] = "✅" if rsi_bear else "❌"
    if rsi_bear: sell_score += 2

    macd_bear = c["macd_hist"] < 0 and c["macd_hist"] < p["macd_hist"]
    sell_conditions["MACD Histogram Bearish"] = "✅" if macd_bear else "❌"
    if macd_bear: sell_score += 2

    sell_conditions["Volume Konfirmasi"] = "✅" if vol_ok else "⚪"
    if vol_ok: sell_score += 1

    # ── SINYAL FINAL ──────────────────────────────────────────────
    min_score = CONFIG["min_score"]
    atr = c["atr"]
    price = c["close"]

    if buy_score >= min_score and buy_score > sell_score:
        result["signal"] = "BUY"
        result["score"] = buy_score
        result["conditions"] = buy_conditions
        result["entry"] = round(price, 2)
        result["stop_loss"] = round(price - (CONFIG["sl_multiplier"] * atr), 2)
        result["take_profit"] = round(price + (CONFIG["tp_multiplier"] * atr), 2)

    elif sell_score >= min_score and sell_score > buy_score:
        result["signal"] = "SELL"
        result["score"] = sell_score
        result["conditions"] = sell_conditions
        result["entry"] = round(price, 2)
        result["stop_loss"] = round(price + (CONFIG["sl_multiplier"] * atr), 2)
        result["take_profit"] = round(price - (CONFIG["tp_multiplier"] * atr), 2)

    else:
        result["signal"] = "WAIT"
        result["score"] = max(buy_score, sell_score)
        result["conditions"] = buy_conditions if buy_score > sell_score else sell_conditions
        log.info(f"Sinyal WAIT | BUY score: {buy_score} | SELL score: {sell_score}")

    return result


# ─── MARKET HOURS CHECK ───────────────────────────────────────────────────────
def is_market_active() -> tuple[bool, str]:
    """Cek apakah ini jam trading aktif (London + New York session)"""
    force = os.environ.get("FORCE_CHECK", "false").lower() == "true"
    if force:
        return True, "FORCED (Manual Trigger)"
    
    utc_now = datetime.now(pytz.utc)
    hour = utc_now.hour
    weekday = utc_now.weekday()
    
    if weekday >= 5:
        return False, f"Weekend ({utc_now.strftime('%A')})"
    
    if hour in CONFIG["active_hours_utc"]:
        return True, f"London/NY Session ({utc_now.strftime('%H:%M')} UTC)"
    
    return False, f"Di luar jam aktif ({utc_now.strftime('%H:%M')} UTC)"


# ─── RISK CALCULATOR ──────────────────────────────────────────────────────────
def calculate_risk_metrics(signal: dict) -> dict:
    """Hitung metrik risiko untuk alert"""
    if signal["signal"] == "WAIT":
        return {}
    
    entry = signal["entry"]
    sl = signal["stop_loss"]
    tp = signal["take_profit"]
    
    risk_pips = abs(entry - sl)
    reward_pips = abs(entry - tp)
    rr_ratio = reward_pips / risk_pips if risk_pips > 0 else 0
    
    atr = signal["atr"]
    
    return {
        "risk_pips"   : round(risk_pips, 2),
        "reward_pips" : round(reward_pips, 2),
        "rr_ratio"    : round(rr_ratio, 2),
        "atr"         : round(atr, 2),
        "target_hold" : "±4 Jam (2-4 Candle H1)",
    }


# ─── TELEGRAM ALERT ───────────────────────────────────────────────────────────
def send_telegram_alert(signal: dict, risk: dict, change_reason: str = "") -> bool:
    """Kirim alert ke Telegram — hanya saat sinyal berubah"""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id   = os.environ.get("TELEGRAM_CHAT_ID")
    
    if not bot_token or not chat_id:
        log.warning("Telegram credentials tidak ditemukan di secrets!")
        return False
    
    s = signal["signal"]
    emoji_map = {"BUY": "🟢", "SELL": "🔴", "WAIT": "⚪"}
    emoji = emoji_map.get(s, "⚪")
    
    conditions_text = ""
    for k, v in signal.get("conditions", {}).items():
        conditions_text += f"  {v} {k}\n"
    
    now_wib = datetime.now(pytz.timezone("Asia/Jakarta")).strftime("%d/%m/%Y %H:%M WIB")
    
    # Tambahkan keterangan perubahan sinyal
    change_note = f"\n🔔 *Update:* _{change_reason}_\n" if change_reason else ""
    
    msg = f"""
{emoji} *XAUUSD SIGNAL ALERT* {emoji}
{change_note}
🕐 *Waktu:* {now_wib}
📊 *Instrumen:* {CONFIG['symbol_display']} (H1)
💡 *Sinyal:* `{s}`
🎯 *Kekuatan:* {signal['score']}/{signal['score_max']} poin
💰 *HARGA SEKARANG: ${signal['price']:,.2f}*
"""

    if s in ["BUY", "SELL"]:
        direction_arrow = "⬆️" if s == "BUY" else "⬇️"
        msg += f"""
{direction_arrow} *ENTRY:* `${signal['entry']:,.2f}`
🛑 *Stop Loss:* `${signal['stop_loss']:,.2f}` ({risk.get('risk_pips', 0):.1f} pips)
🎯 *Take Profit:* `${signal['take_profit']:,.2f}` ({risk.get('reward_pips', 0):.1f} pips)
⚖️ *Risk:Reward:* `1 : {risk.get('rr_ratio', 0):.1f}`
⏰ *Target Hold:* {risk.get('target_hold', '~4 Jam')}
"""

    msg += f"""
📈 *KONDISI SINYAL:*
{conditions_text}
📉 *INDIKATOR UTAMA:*
  • EMA 9 : `{signal['indicators']['ema9']:,.2f}`
  • EMA 21: `{signal['indicators']['ema21']:,.2f}`
  • EMA 50: `{signal['indicators']['ema50']:,.2f}`
  • RSI   : `{signal['rsi']:.1f}`
  • MACD  : `{signal['indicators']['macd_hist']:+.3f}` (histogram)
  • Volume: `{signal['indicators']['vol_ratio']:.2f}x` rata-rata
  • ATR   : `{signal['atr']:.2f}`
"""
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        resp = requests.post(url, json={
            "chat_id"    : chat_id,
            "text"       : msg.strip(),
            "parse_mode" : "Markdown"
        }, timeout=15)
        
        if resp.status_code == 200:
            log.info("✅ Telegram alert berhasil dikirim!")
            return True
        else:
            log.error(f"Telegram error: {resp.status_code} | {resp.text}")
            return False
            
    except Exception as e:
        log.error(f"Gagal kirim Telegram: {e}")
        return False


# ─── SAVE SIGNAL LOG ──────────────────────────────────────────────────────────
def save_signal_log(signal: dict, risk: dict, market_status: str, alert_sent: bool = False, reason: str = ""):
    """Simpan log sinyal ke file JSON"""
    log_data = {
        "run_time"      : datetime.now(pytz.utc).isoformat(),
        "market_status" : market_status,
        "alert_sent"    : alert_sent,
        "skip_reason"   : reason if not alert_sent else "",
        "signal"        : signal,
        "risk_metrics"  : risk,
    }
    
    log_file = f"logs/signals_{datetime.now().strftime('%Y%m%d')}.jsonl"
    with open(log_file, "a") as f:
        f.write(json.dumps(log_data) + "\n")
    
    log.info(f"Log disimpan: {log_file}")


# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 60)
    log.info("  XAUUSD TRADING SIGNAL ENGINE v2.0 - STARTED")
    log.info("=" * 60)
    
    # 1. Cek jam trading
    is_active, market_status = is_market_active()
    log.info(f"Market Status: {market_status}")
    
    if not is_active:
        log.info(f"⏭️  Skip analisis: {market_status}")
        save_signal_log({"signal": "SKIP", "reason": market_status}, {}, market_status)
        return
    
    # 2. Load state terakhir dari Gist
    last_state = load_last_state()
    
    # 3. Ambil data
    df = fetch_ohlcv(CONFIG["symbol"], CONFIG["timeframe"], CONFIG["lookback_bars"])
    
    # 4. Hitung indikator
    df = calculate_indicators(df)
    log.info(f"Indikator dihitung: {len(df)} candle valid")
    
    # 5. Analisis sinyal
    signal = analyze_signal(df)
    risk = calculate_risk_metrics(signal)
    
    log.info(f"SINYAL: {signal['signal']} | Score: {signal['score']}/{signal['score_max']}")
    log.info(f"Harga: ${signal['price']:,.2f} | RSI: {signal['rsi']} | ATR: {signal['atr']}")
    
    if signal["signal"] != "WAIT":
        log.info(f"Entry: ${signal['entry']:,.2f} | SL: ${signal['stop_loss']:,.2f} | TP: ${signal['take_profit']:,.2f}")
        log.info(f"R:R Ratio: 1:{risk.get('rr_ratio', 0):.1f}")
    
    # 6. Cek apakah perlu kirim alert
    should_alert, reason = should_send_alert(signal, last_state)
    
    log.info(f"Kirim alert: {'✅ YA' if should_alert else '⏭️ SKIP'} | Alasan: {reason}")
    
    alert_sent = False
    if should_alert:
        alert_sent = send_telegram_alert(signal, risk, change_reason=reason)
        # 7. Simpan state baru ke Gist (hanya jika alert dikirim)
        save_current_state(signal)
    
    # 8. Simpan log lokal
    save_signal_log(signal, risk, market_status, alert_sent=alert_sent, reason=reason)
    
    log.info("=" * 60)
    log.info("  ANALISIS SELESAI")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
