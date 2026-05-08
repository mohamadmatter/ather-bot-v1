import os
import time
import requests
import telebot
import pandas as pd
import mplfinance as mpf
from datetime import datetime
import matplotlib
from fastapi import FastAPI, Request

matplotlib.use('Agg')

# ========= 🔐 ENV =========
TOKEN = os.getenv("BOT_TOKEN")
TD_KEY = os.getenv("TD_KEY")
CHAT_ID = os.getenv("CHAT_ID")

bot = telebot.TeleBot(TOKEN, threaded=False)
app = FastAPI()

ACCOUNT_BALANCE = 600
RISK_PERCENT = 1.0

# ========= 📊 DATA =========
def fetch_data(interval="1h", outputsize=40):
    try:
        ts_url = f"https://api.twelvedata.com/time_series?symbol=XAUUSD&interval={interval}&outputsize={outputsize}&apikey={TD_KEY}"
        ts_data = requests.get(ts_url, timeout=10).json()

        if "values" not in ts_data:
            return None, None, None

        values = ts_data["values"]

        rsi_url = f"https://api.twelvedata.com/rsi?symbol=XAUUSD&interval={interval}&time_period=14&apikey={TD_KEY}"
        rsi_data = requests.get(rsi_url, timeout=10).json()

        if "values" not in rsi_data:
            return None, None, None

        rsi = float(rsi_data["values"][0]["rsi"])

        return float(values[0]["close"]), rsi, values

    except Exception as e:
        print("Fetch Error:", e)
        return None, None, None

# ========= 🧠 LOGIC =========
def detect_liquidity(values):
    highs = [float(x["high"]) for x in values[1:25]]
    lows = [float(x["low"]) for x in values[1:25]]
    return max(highs), min(lows)

# ========= 💰 RISK =========
def build_trade(price, liq_up, liq_down, decision):
    if decision == "BUY":
        sl = liq_down - 2
        tp = price + (price - sl) * 2
    elif decision == "SELL":
        sl = liq_up + 2
        tp = price - (sl - price) * 2
    else:
        return None

    return {"entry": price, "sl": sl, "tp": tp}

# ========= 📊 CHART =========
def plot_chart(data, liq_up, liq_down, trade=None):
    df = pd.DataFrame(data)

    for col in ["open","high","low","close"]:
        df[col] = df[col].astype(float)

    df = df.iloc[::-1]
    df.index = pd.date_range(end=datetime.now(), periods=len(df), freq="15min")

    apds = [
        mpf.make_addplot([liq_up]*len(df)),
        mpf.make_addplot([liq_down]*len(df))
    ]

    if trade:
        apds.append(mpf.make_addplot([trade["entry"]]*len(df)))
        apds.append(mpf.make_addplot([trade["sl"]]*len(df)))
        apds.append(mpf.make_addplot([trade["tp"]]*len(df)))

    filename = "/tmp/chart.png" # Vercel allows writing to /tmp

    mpf.plot(
        df,
        type="candle",
        style="charles",
        addplot=apds,
        savefig=filename
    )

    return filename

# ========= 🤖 MONITOR LOGIC (For Cron) =========
def run_monitor_once():
    try:
        price, rsi, data = fetch_data("1h")
        if not data:
            return "No data"

        liq_up, liq_down = detect_liquidity(data)

        decision = "NO TRADE"
        if rsi < 35:
            decision = "BUY"
        elif rsi > 65:
            decision = "SELL"

        if decision != "NO TRADE":
            trade = build_trade(price, liq_up, liq_down, decision)
            chart = plot_chart(data, liq_up, liq_down, trade)

            msg = f"🚨 SIGNAL: {decision}\nPrice: {price}"

            with open(chart, "rb") as img:
                bot.send_photo(CHAT_ID, img, caption=msg)
            return f"Signal sent: {decision}"
        
        return "No signal"

    except Exception as e:
        return f"Error: {e}"

# ========= 📡 HANDLERS =========
@bot.message_handler(commands=["start", "help"])
def send_welcome(message):
    bot.reply_to(message, "🦁 ATHER BOT is active on Vercel!\nUse /scan to check current status.")

@bot.message_handler(commands=["scan"])
def scan(m):
    price, rsi, data = fetch_data()

    if not data:
        bot.send_message(m.chat.id, "❌ Data error")
        return

    liq_up, liq_down = detect_liquidity(data)
    decision = "BUY" if rsi < 35 else "SELL" if rsi > 65 else "NO TRADE"
    trade = build_trade(price, liq_up, liq_down, decision)
    chart = plot_chart(data, liq_up, liq_down, trade)

    msg = f"🦁 ATHER\n\nPrice: {price}\nRSI: {round(rsi,2)}\n\nDecision: {decision}"

    with open(chart, "rb") as img:
        bot.send_photo(m.chat.id, img, caption=msg)

# ========= 🌐 VERCEL ENDPOINTS =========
@app.post("/api/webhook")
async def telegram_webhook(request: Request):
    json_str = await request.body()
    update = telebot.types.Update.de_json(json_str.decode("utf-8"))
    bot.process_new_updates([update])
    return {"status": "ok"}

@app.get("/api/cron")
def cron_job():
    # This will be called by Vercel Cron every X minutes
    result = run_monitor_once()
    return {"status": "cron executed", "result": result}

@app.get("/")
def index(request: Request):
    # Set webhook automatically when someone visits the home page
    url = str(request.base_url) + "api/webhook"
    bot.remove_webhook()
    time.sleep(0.1)
    if bot.set_webhook(url=url):
        return {"status": "Bot is running", "webhook_set_to": url}
    else:
        return {"status": "Bot is running", "webhook_error": "Failed to set webhook"}
