# 🥇 XAUUSD Trading Alert System

> Sistem alert trading otomatis untuk XAU/USD menggunakan GitHub Actions + Multi-Indikator Strategy

---

## 📋 STRATEGI TRADING

### Konsep Dasar
Sistem ini dirancang untuk **hold posisi ±4 jam** menggunakan **timeframe H1** sebagai trigger sinyal.

### Indikator yang Digunakan

| Indikator | Parameter | Fungsi |
|-----------|-----------|--------|
| EMA 9 | Fast | Trend jangka pendek |
| EMA 21 | Mid | Trend konfirmasi |
| EMA 50 | Slow | Filter trend utama |
| RSI | 14 | Momentum & OS/OB |
| MACD | 12, 26, 9 | Konfirmasi momentum |
| Bollinger Bands | 20, 2 | Volatility gauge |
| ATR | 14 | Kalkulasi SL & TP |
| Volume | MA-20 | Konfirmasi sinyal |

### Aturan Entry

**✅ BUY Signal (Semua harus terpenuhi):**
```
EMA9 > EMA21 > EMA50   (Stack bullish)
RSI antara 50 - 70     (Momentum bullish, belum OB)
MACD Histogram > 0     (Momentum positif)
Score minimal 6/10     (Minimum 3 kondisi valid)
```

**🔴 SELL Signal (Semua harus terpenuhi):**
```
EMA9 < EMA21 < EMA50   (Stack bearish)
RSI antara 30 - 50     (Momentum bearish, belum OS)
MACD Histogram < 0     (Momentum negatif)
Score minimal 6/10     (Minimum 3 kondisi valid)
```

### Risk Management

```
Stop Loss  = 1.5x ATR14  (Risk dinamis sesuai volatility)
Take Profit = 3.0x ATR14  (Risk:Reward = 1:2)
Target Hold = ±4 Jam (2-4 candle H1)
```

---

## 🚀 CARA SETUP

### Step 1 — Fork Repository
```bash
# Fork repo ini ke akun GitHub kamu
# Lalu clone
git clone https://github.com/USERNAME/xauusd-alert.git
cd xauusd-alert
```

### Step 2 — Buat Bot Telegram

1. Buka Telegram, cari **@BotFather**
2. Ketik `/newbot` dan ikuti instruksi
3. Simpan **Bot Token** yang diberikan
4. Cari **@userinfobot** untuk dapat **Chat ID** kamu
5. Atau buka: `https://api.telegram.org/bot{TOKEN}/getUpdates`

### Step 3 — Setup GitHub Secrets

Pergi ke `Settings → Secrets → Actions → New repository secret`:

| Secret Name | Value |
|-------------|-------|
| `TELEGRAM_BOT_TOKEN` | Token dari BotFather |
| `TELEGRAM_CHAT_ID` | Chat ID kamu |

### Step 4 — Aktifkan GitHub Actions

1. Pergi ke tab **Actions** di repository
2. Klik **"I understand my workflows, go ahead and enable them"**
3. Workflow akan berjalan otomatis setiap 10 menit!

### Step 5 — Test Manual

1. Pergi ke **Actions → XAUUSD Trading Alert**
2. Klik **"Run workflow"**
3. Centang **"Force check"** untuk bypass cek jam trading
4. Klik **"Run workflow"**

---

## 📱 FORMAT ALERT TELEGRAM

```
🟢 XAUUSD SIGNAL ALERT 🟢

🕐 Waktu: 25/06/2026 15:30 WIB
📊 Instrumen: XAU/USD (H1)
💡 Sinyal: BUY
🎯 Kekuatan: 8/10 poin

💰 HARGA SEKARANG: $2,345.50

⬆️ ENTRY:       $2,345.50
🛑 Stop Loss:   $2,330.20  (15.3 pips)
🎯 Take Profit: $2,376.00  (30.5 pips)
⚖️ Risk:Reward: 1 : 2.0
⏰ Target Hold: ±4 Jam (2-4 Candle H1)

📈 KONDISI SINYAL:
  ✅ EMA Stack Bullish
  ✅ EMA9 Cross EMA21 Baru
  ✅ RSI Zona Bullish (50-70)
  ✅ MACD Histogram Bullish
  ✅ Volume Konfirmasi

📉 INDIKATOR UTAMA:
  • EMA 9 : 2,342.30
  • EMA 21: 2,338.90
  • EMA 50: 2,328.50
  • RSI   : 58.4
  • MACD  : +0.823 (histogram)
  • Volume: 1.45x rata-rata
  • ATR   : 10.20
```

---

## ⚙️ KONFIGURASI LANJUTAN

Edit file `src/signal_engine.py` bagian `CONFIG`:

```python
CONFIG = {
    "min_score"      : 6,    # Naikkan ke 7-8 untuk sinyal lebih selektif
    "sl_multiplier"  : 1.5,  # Ubah SL lebih ketat/longgar
    "tp_multiplier"  : 3.0,  # Ubah TP target
    "active_hours_utc": list(range(7, 21)),  # Jam aktif UTC
}
```

---

## ⚠️ DISCLAIMER

> Sistem ini adalah **alat bantu analisis teknikal**, bukan jaminan profit.  
> Selalu gunakan **manajemen risiko** yang baik.  
> **Jangan pernah trading dengan uang yang tidak siap kamu rugi.**

---

## 📊 STRUKTUR FILE

```
xauusd-alert/
├── .github/
│   └── workflows/
│       └── xauusd_signal.yml    # Cron trigger setiap 10 menit
├── src/
│   └── signal_engine.py         # Engine analisis utama
├── logs/                        # Log sinyal (auto-generated)
├── requirements.txt             # Python dependencies
└── README.md                    # Dokumentasi ini
```
