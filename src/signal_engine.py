"""
╔══════════════════════════════════════════════════════════════════╗
║          XAUUSD TRADING ALERT - SIGNAL ENGINE v1.0              ║
║          Strategi: Multi-Indicator H1 → Hold 4 Jam              ║
║          Author: AI Trading System                               ║
╚══════════════════════════════════════════════════════════════════╝

STRATEGI TRADING (Hold 4 Jam):
================================
Timeframe Analisis : H1 (1-Jam) sebagai trigger sinyal
Hold Duration     : ±4 jam (2-4 candle H1)
Instrumen        : XAU/USD (Gold)

INDIKATOR YANG DIGUNAKAN:
--------------------------
1. EMA 9 & EMA 21   → Trend Direction (Fast Cross)
2. EMA 50           → Trend Filter (hanya trade searah trend)
3. RSI (14)         → Momentum & Overbought/Oversold
4. MACD (12,26,9)   → Konfirmasi momentum & divergensi
5. Bollinger Bands  → Volatility & mean reversion
6. ATR (14)         → Kalkulasi SL & TP dinamis
7. Volume           → Konfirmasi kekuatan candle

ATURAN ENTRY:
-------------
BUY  : EMA9 > EMA21 > EMA50 + RSI antara 50-70 + MACD bullish cross
SELL : EMA9 < EMA21 < EMA50 + RSI antara 30-50 + MACD bearish cross

RISK MANAGEMENT (4 Jam Hold):
------------------------------
Stop Loss  : 1.5x ATR14
Take Profit: 3.0x ATR14  (Risk:Reward = 1:2)
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
    "symbol"         : "XAUUSD=X",        # Gold Futures (proxy XAU/USD)
    "symbol_display" : "XAU/USD",
    "timeframe"      : "1h",          # H1 untuk hold 4 jam
    "lookback_bars"  : 200,           # Candle historis untuk kalkulasi

    # Indikator
    "ema_fast"       : 9,
    "ema_mid"        : 21,
    "ema_slow"       : 50,
    "rsi_period"     : 14,
    "rsi_ob"         : 70,            # Overbought
    "rsi_os"         : 30,            # Oversold
    "bb_period"      : 20,
    "bb_std"         : 2,
    "atr_period"     : 14,

    # Risk Management (4 jam hold)
    "sl_multiplier"  : 1.5,           # SL = 1.5x ATR
    "tp_multiplier"  : 3.0,           # TP = 3.0x ATR (R:R = 1:2)

    # Kualitas sinyal minimum (dari 10)
    "min_score"      : 6,

    # Jam trading aktif (UTC) - London + NY session
    "active_hours_utc": list(range(7, 21)),  # 07:00 - 21:00 UTC
}


# ─── DATA FETCHER ─────────────────────────────────────────────────────────────
def fetch_ohlcv(symbol: str, timeframe: str, bars: int) -> pd.DataFrame:
    """Ambil data OHLCV dari Stooq (tidak butuh API key)"""
    log.info(f"Fetching data: {symbol} | TF: {timeframe} | Bars: {bars}")
    
    import urllib.request
    
    # Stooq - reliable, gratis, tanpa API key
    url = "https://stooq.com/q/d/l/?s=xauusd&i=h"
    
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0"
    })
    
    from io import StringIO
    with urllib.request.urlopen(req, timeout=30) as resp:
        content = resp.read().decode("utf-8")
    
    df = pd.read_csv(StringIO(content))
    df.columns = [c.lower() for c in df.columns]
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    df = df[["open", "high", "low", "close", "volume"]].dropna()
    df = df.tail(bars)
    
    if df.empty:
        raise ValueError(f"Data kosong dari Stooq")
    
    log.info(f"Data berhasil: {len(df)} candle | Terakhir: {df.index[-1]}")
    return df


# ─── INDIKATOR ENGINE ─────────────────────────────────────────────────────────
def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Hitung semua indikator teknikal"""
    
    # ── EMA (Trend Direction) ──────────────────────────────────────
    df["ema9"]  = ta.trend.EMAIndicator(df["close"], window=CONFIG["ema_fast"]).ema_indicator()
    df["ema21"] = ta.trend.EMAIndicator(df["close"], window=CONFIG["ema_mid"]).ema_indicator()
    df["ema50"] = ta.trend.EMAIndicator(df["close"], window=CONFIG["ema_slow"]).ema_indicator()
    
    # ── RSI (Momentum) ─────────────────────────────────────────────
    df["rsi"] = ta.momentum.RSIIndicator(df["close"], window=CONFIG["rsi_period"]).rsi()
    
    # ── MACD (Konfirmasi momentum) ─────────────────────────────────
    macd_obj = ta.trend.MACD(df["close"])
    df["macd"]        = macd_obj.macd()
    df["macd_signal"] = macd_obj.macd_signal()
    df["macd_hist"]   = macd_obj.macd_diff()
    
    # ── Bollinger Bands (Volatility) ───────────────────────────────
    bb = ta.volatility.BollingerBands(
        df["close"], 
        window=CONFIG["bb_period"], 
        window_dev=CONFIG["bb_std"]
    )
    df["bb_upper"] = bb.bollinger_hband()
    df["bb_mid"]   = bb.bollinger_mavg()
    df["bb_lower"] = bb.bollinger_lband()
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"] * 100  # %
    
    # ── ATR (Risk Management) ──────────────────────────────────────
    df["atr"] = ta.volatility.AverageTrueRange(
        df["high"], df["low"], df["close"],
        window=CONFIG["atr_period"]
    ).average_true_range()
    
    # ── Volume MA ──────────────────────────────────────────────────
    df["vol_ma"] = df["volume"].rolling(window=20).mean()
    df["vol_ratio"] = df["volume"] / df["vol_ma"]
    
    # ── Candle Patterns ────────────────────────────────────────────
    df["candle_body"] = abs(df["close"] - df["open"])
    df["candle_range"] = df["high"] - df["low"]
    df["is_bullish"] = df["close"] > df["open"]
    
    return df.dropna()


# ─── SIGNAL ANALYZER ──────────────────────────────────────────────────────────
def analyze_signal(df: pd.DataFrame) -> dict:
    """
    Scoring System (0-10):
    Setiap kondisi memberikan poin. Total >= 6 = sinyal valid
    """
    c = df.iloc[-1]   # Candle terkini
    p = df.iloc[-2]   # Candle sebelumnya
    
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

    # ═══════════════════════════════════════════════════════════════
    #  SCORING BUY CONDITIONS
    # ═══════════════════════════════════════════════════════════════
    buy_score = 0
    buy_conditions = {}

    # [BUY 1] EMA Stack Bullish (EMA9 > EMA21 > EMA50)  → 3 poin
    ema_bull = c["ema9"] > c["ema21"] > c["ema50"]
    buy_conditions["EMA Stack Bullish"] = "✅" if ema_bull else "❌"
    if ema_bull: buy_score += 3

    # [BUY 2] EMA9 cross EMA21 bullish baru  → 2 poin bonus
    ema_cross_bull = (c["ema9"] > c["ema21"]) and (p["ema9"] <= p["ema21"])
    buy_conditions["EMA9 Cross EMA21 Baru"] = "✅" if ema_cross_bull else "⚪"
    if ema_cross_bull: buy_score += 2

    # [BUY 3] RSI di zona bullish (50-70)  → 2 poin
    rsi_bull = 50 <= c["rsi"] <= CONFIG["rsi_ob"]
    buy_conditions["RSI Zona Bullish (50-70)"] = "✅" if rsi_bull else "❌"
    if rsi_bull: buy_score += 2

    # [BUY 4] MACD Histogram positif & naik  → 2 poin
    macd_bull = c["macd_hist"] > 0 and c["macd_hist"] > p["macd_hist"]
    buy_conditions["MACD Histogram Bullish"] = "✅" if macd_bull else "❌"
    if macd_bull: buy_score += 2

    # [BUY 5] Volume konfirmasi (vol_ratio > 1.2)  → 1 poin
    vol_ok = c["vol_ratio"] > 1.2
    buy_conditions["Volume Konfirmasi"] = "✅" if vol_ok else "⚪"
    if vol_ok: buy_score += 1

    # ═══════════════════════════════════════════════════════════════
    #  SCORING SELL CONDITIONS
    # ═══════════════════════════════════════════════════════════════
    sell_score = 0
    sell_conditions = {}

    # [SELL 1] EMA Stack Bearish (EMA9 < EMA21 < EMA50)  → 3 poin
    ema_bear = c["ema9"] < c["ema21"] < c["ema50"]
    sell_conditions["EMA Stack Bearish"] = "✅" if ema_bear else "❌"
    if ema_bear: sell_score += 3

    # [SELL 2] EMA9 cross EMA21 bearish baru  → 2 poin bonus
    ema_cross_bear = (c["ema9"] < c["ema21"]) and (p["ema9"] >= p["ema21"])
    sell_conditions["EMA9 Cross EMA21 Baru"] = "✅" if ema_cross_bear else "⚪"
    if ema_cross_bear: sell_score += 2

    # [SELL 3] RSI di zona bearish (30-50)  → 2 poin
    rsi_bear = CONFIG["rsi_os"] <= c["rsi"] <= 50
    sell_conditions["RSI Zona Bearish (30-50)"] = "✅" if rsi_bear else "❌"
    if rsi_bear: sell_score += 2

    # [SELL 4] MACD Histogram negatif & turun  → 2 poin
    macd_bear = c["macd_hist"] < 0 and c["macd_hist"] < p["macd_hist"]
    sell_conditions["MACD Histogram Bearish"] = "✅" if macd_bear else "❌"
    if macd_bear: sell_score += 2

    # [SELL 5] Volume konfirmasi  → 1 poin
    sell_conditions["Volume Konfirmasi"] = "✅" if vol_ok else "⚪"
    if vol_ok: sell_score += 1

    # ═══════════════════════════════════════════════════════════════
    #  TENTUKAN SINYAL FINAL
    # ═══════════════════════════════════════════════════════════════
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
    
    # Sabtu (5) dan Minggu (6) = Tutup
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
    
    # Estimasi durasi berdasarkan ATR (4 jam target)
    atr = signal["atr"]
    
    return {
        "risk_pips"   : round(risk_pips, 2),
        "reward_pips" : round(reward_pips, 2),
        "rr_ratio"    : round(rr_ratio, 2),
        "atr"         : round(atr, 2),
        "target_hold" : "±4 Jam (2-4 Candle H1)",
    }


# ─── TELEGRAM ALERT ───────────────────────────────────────────────────────────
def send_telegram_alert(signal: dict, risk: dict) -> bool:
    """Kirim alert ke Telegram"""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id   = os.environ.get("TELEGRAM_CHAT_ID")
    
    if not bot_token or not chat_id:
        log.warning("Telegram credentials tidak ditemukan di secrets!")
        return False
    
    s = signal["signal"]
    emoji_map = {"BUY": "🟢", "SELL": "🔴", "WAIT": "⚪"}
    emoji = emoji_map.get(s, "⚪")
    
    # Format kondisi
    conditions_text = ""
    for k, v in signal.get("conditions", {}).items():
        conditions_text += f"  {v} {k}\n"
    
    # Format pesan
    now_wib = datetime.now(pytz.timezone("Asia/Jakarta")).strftime("%d/%m/%Y %H:%M WIB")
    
    msg = f"""
{emoji} *XAUUSD SIGNAL ALERT* {emoji}

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

⚠️ _Ini bukan saran keuangan. Selalu gunakan manajemen risiko yang baik._
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
def save_signal_log(signal: dict, risk: dict, market_status: str):
    """Simpan log sinyal ke file JSON"""
    log_data = {
        "run_time"      : datetime.now(pytz.utc).isoformat(),
        "market_status" : market_status,
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
    log.info("  XAUUSD TRADING SIGNAL ENGINE - STARTED")
    log.info("=" * 60)
    
    # 1. Cek jam trading
    is_active, market_status = is_market_active()
    log.info(f"Market Status: {market_status}")
    
    if not is_active:
        log.info(f"⏭️  Skip analisis: {market_status}")
        save_signal_log({"signal": "SKIP", "reason": market_status}, {}, market_status)
        return
    
    # 2. Ambil data
    df = fetch_ohlcv(CONFIG["symbol"], CONFIG["timeframe"], CONFIG["lookback_bars"])
    
    # 3. Hitung indikator
    df = calculate_indicators(df)
    log.info(f"Indikator dihitung: {len(df)} candle valid")
    
    # 4. Analisis sinyal
    signal = analyze_signal(df)
    risk = calculate_risk_metrics(signal)
    
    log.info(f"SINYAL: {signal['signal']} | Score: {signal['score']}/{signal['score_max']}")
    log.info(f"Harga: ${signal['price']:,.2f} | RSI: {signal['rsi']} | ATR: {signal['atr']}")
    
    if signal["signal"] != "WAIT":
        log.info(f"Entry: ${signal['entry']:,.2f} | SL: ${signal['stop_loss']:,.2f} | TP: ${signal['take_profit']:,.2f}")
        log.info(f"R:R Ratio: 1:{risk.get('rr_ratio', 0):.1f}")
        
        # 5. Kirim alert
        send_telegram_alert(signal, risk)
    else:
        log.info("Tidak ada sinyal kuat. Menunggu setup yang lebih baik...")
    
    # 6. Simpan log
    save_signal_log(signal, risk, market_status)
    
    log.info("=" * 60)
    log.info("  ANALISIS SELESAI")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
