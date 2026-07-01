# 🥇₿ Multi-Asset Trading Alert System

> Sistem alert trading otomatis untuk **XAU/USD** dan **BTC/USD (Binance)** menggunakan GitHub Actions + Multi-Indikator Strategy + TwelveData

---

## 🪙 SYMBOL YANG DIDUKUNG

| Key | TwelveData Symbol | Exchange | Trading Hours | ATR SL Mult | ATR TP Mult | Price Move Threshold |
|-----|--------------------|----------|----------------|-------------|-------------|------------------------|
| `xau` | `XAU/USD` | — (spot aggregated) | Weekday, 07:00–21:00 UTC | 1.5x | 3.0x | $5 |
| `btc` | `BTC/USD` | Binance | 24/7 | 2.5x | 3.0x | $50 |

Kenapa BTC dapat multiplier lebih besar: volatilitas BTC jauh lebih tinggi dari XAU, jadi SL 1.5x ATR terlalu ketat dan gampang kena stop-out premature. State dan history disimpan di file Gist terpisah per symbol (`xauusd_state.json` vs `btcusd_state.json`) supaya tidak saling timpa dalam satu Gist ID.

---

## 📋 STRATEGI TRADING

### Konsep Dasar

Sistem ini memakai **timeframe M15 (15 menit)** sebagai basis sinyal, dengan target hold **beberapa puluh menit hingga 1-2 jam per posisi** — lebih cepat dari swing H1, tapi tidak secepat scalping M1/M5 murni. M15 dipilih secara sadar (bukan default lama) karena tiga alasan:

1. **Selaras dengan latensi infrastruktur** — sistem dipicu cron eksternal tiap ±10 menit lewat GitHub Actions, bukan eksekusi real-time. Di M15, jendela 10 menit itu masih berada di dalam satu candle yang sama, jadi sinyal tidak pernah "basi" sebelum sempat dibaca. Di M5, window trigger 10 menit bisa melompati satu candle penuh.
2. **Rasio spread terhadap SL lebih sehat** — ATR di M15 jauh lebih besar dari M5, sehingga spread broker riil menghabiskan porsi lebih kecil dari jarak Stop Loss. Di M5, spread bisa memakan 30-40%+ dari SL sebelum harga sempat bergerak.
3. **Sinyal lebih tersaring dari noise jangka sangat pendek**, tanpa kehilangan kecepatan reaksi yang berarti untuk trader yang mengeksekusi manual dari notifikasi Telegram.

> Catatan: TwelveData tidak menyediakan interval `10min` — pilihan interval intraday yang tersedia adalah `1min, 5min, 15min, 30min, 45min, 1h, 2h, 4h, 8h`. M15 adalah titik temu terbaik antara kecepatan dan keandalan sinyal untuk arsitektur cron-based sistem ini.

### Indikator yang Digunakan

| Indikator | Parameter | Peran |
|-----------|-----------|--------|
| EMA 9 / 21 / 50 | Fast / Mid / Slow | **Penentu arah (gerbang trend)** — menentukan `trend_bias`: up, down, atau neutral |
| RSI | 14 | **Timing entry**, bukan sinyal kontrarian independen (lihat di bawah) |
| MACD | 12, 26, 9 | Konfirmasi momentum searah trend |
| Bollinger Bands | 20, 2 | **Timing entry** tambahan, sama seperti RSI — hanya valid searah trend |
| ATR | 14 | Kalkulasi SL & TP |

> **Volume MA-20 belum diimplementasikan** di v3.2 — dihapus dari daftar aktif sampai benar-benar dibangun, supaya dokumentasi ini tidak menjanjikan indikator yang belum ada di kode.

### Filosofi: Trend-Following sebagai Arah, RSI/BB sebagai Timing (bukan Mean-Reversion Murni)

Versi sebelum v3.2 memakai RSI dan Bollinger Bands sebagai **sinyal kontrarian independen** (RSI oversold = BUY, tanpa peduli arah trend besar). Ini bisa membuat sistem "menangkap pisau jatuh" — misalnya tetap kasih sinyal BUY saat RSI oversold, padahal EMA-stack sedang menunjukkan downtrend kuat.

**v3.2 mengubah RSI & Bollinger jadi bergerbang oleh trend (`trend_bias`) dari EMA9/21/50:**

- RSI dip (< 40) atau sentuhan Bollinger band bawah **hanya dihitung sebagai skor BUY** kalau `trend_bias` bukan "down" — konsepnya "beli saat harga dip sesaat di dalam uptrend/kondisi netral", bukan "beli karena RSI rendah, titik."
- RSI rally (> 60) atau sentuhan band atas **hanya dihitung sebagai skor SELL** kalau `trend_bias` bukan "up" — konsepnya "jual saat harga rally sesaat di dalam downtrend/kondisi netral."
- Kalau trend_bias berlawanan arah dengan sinyal RSI/BB (misal RSI oversold di tengah downtrend kuat), sinyal itu **diabaikan sepenuhnya** — tidak menyumbang skor ke arah mana pun.

### Aturan Entry

**✅ BUY — kontribusi skor (maksimum 10):**
```
+3   EMA9 > EMA21 > EMA50 dan price di atas ketiganya   (trend bullish penuh)
+1   EMA9 > EMA21 saja                                   (trend bullish parsial)
+2   RSI < 40   — HANYA jika trend_bias ≠ "down"          (dip di dalam uptrend/netral)
+2   MACD histogram > 0 dan MACD line > signal line
+2   Price ≤ Bollinger band bawah — HANYA jika trend_bias ≠ "down"
Sinyal BUY terpicu jika skor ≥ 6/10
```

**🔴 SELL — kontribusi skor (maksimum 10):**
```
+3   EMA9 < EMA21 < EMA50 dan price di bawah ketiganya   (trend bearish penuh)
+1   EMA9 < EMA21 saja                                   (trend bearish parsial)
+2   RSI > 60   — HANYA jika trend_bias ≠ "up"            (rally di dalam downtrend/netral)
+2   MACD histogram < 0 dan MACD line < signal line
+2   Price ≥ Bollinger band atas — HANYA jika trend_bias ≠ "up"
Sinyal SELL terpicu jika skor ≥ 6/10
```

### Risk Management

```
Stop Loss   = ATR(14) di M15 × atr_sl_mult per-symbol   (1.5x untuk XAU, 2.5x untuk BTC)
Take Profit = ATR(14) di M15 × atr_tp_mult per-symbol   (3.0x untuk keduanya, Risk:Reward = 1:2)
Target Hold = beberapa puluh menit – 1-2 jam per posisi (bukan lagi ±4 jam basis H1)
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

1. Pergi ke **Actions → Multi-Asset Alert System v3.1**
2. Klik **"Run workflow"**
3. Pilih **mode** (`alert` atau `daily-summary`) dan **symbol** (`xau` atau `btc`)
4. Klik **"Run workflow"**

### Step 6 — Setup cron-job.org untuk BTC (opsional, kalau mau alert otomatis)

Symbol `xau` sudah jalan otomatis lewat cron job yang existing. Untuk `btc`, tambahkan **job cron-job.org baru** yang trigger `workflow_dispatch` dengan body berbeda:

```json
{"ref":"main","inputs":{"mode":"alert","symbol":"btc"}}
```

Karena BTC trading 24/7, jadwal cron-nya bisa `* * * * *` tiap 10 menit **tanpa batas jam** (beda dari job XAU yang dibatasi weekday 07:00–21:00 UTC). Header dan endpoint sama seperti job XAU (`POST /repos/{owner}/{repo}/actions/workflows/xauusd_signal.yml/dispatches`).

---

## 📱 FORMAT ALERT TELEGRAM

Ini adalah format yang **benar-benar dikirim** oleh `run_alert()` di v3.2 (bukan mockup) — apa yang kamu lihat di Telegram akan persis seperti ini:

```
🟢 🥇 XAU/USD Signal: BUY
━━━━━━━━━━━━━━
💰 Price : $2,345.50
📊 Score : 8/10
📈 RSI   : 38.4
📉 MACD  : +0.0823
━━━━━━━━━━━━━━
🛡️ SL    : $2,330.20
🎯 TP    : $2,376.00
⏰ 15:30 WIB
```

Kalau kamu ingin format yang lebih kaya seperti breakdown kondisi sinyal per-indikator (EMA stack, konfirmasi MACD, dll.) atau info pips, itu **belum diimplementasikan** — perlu ditambahkan secara eksplisit di fungsi `run_alert()`, bukan cuma diasumsikan ada.

---

## ⚙️ KONFIGURASI LANJUTAN

Edit file `src/signal_engine.py` bagian `SYMBOLS`. Tambah key baru untuk menambah symbol lain (misal ETH/USD):

```python
SYMBOLS = {
    "xau": {
        "td_symbol": "XAU/USD", "td_exchange": None,
        "gist_state": "xauusd_state.json", "gist_history": "xauusd_daily_history.json",
        "atr_sl_mult": 1.5, "atr_tp_mult": 3.0,
        "price_move_threshold": 5.0, "trades_24_7": False,
    },
    "btc": {
        "td_symbol": "BTC/USD", "td_exchange": "Binance",
        "gist_state": "btcusd_state.json", "gist_history": "btcusd_daily_history.json",
        "atr_sl_mult": 2.5, "atr_tp_mult": 3.0,
        "price_move_threshold": 50.0, "trades_24_7": True,
    },
    # "eth": { ... }  ← tinggal tambah di sini + registrasi di --symbol choices
}
```

`SCORE_THRESHOLD` (default 6/10) masih global untuk semua symbol — belum per-symbol, karena scoring logic-nya sama.

Jalankan manual dari CLI:
```bash
python src/signal_engine.py --mode alert --symbol btc
python src/signal_engine.py --mode daily-summary --symbol xau
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
