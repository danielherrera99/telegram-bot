import os
import threading
import datetime
from collections import defaultdict

from flask import Flask
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    ContextTypes,
    filters,
)

# ✅ 1) TOKEN: usa variable de entorno si existe, si no usa el texto de abajo
# En Render te recomiendo usar BOT_TOKEN como Environment Variable.
TOKEN = os.getenv("BOT_TOKEN", "8713356304:AAHTwOvCH2TRYM_awCvSbv-63Fp_VZsIYIk")

# -----------------------
# ✅ Mini servidor Flask (para Render)
# -----------------------
flask_app = Flask(__name__)

@flask_app.get("/")
def home():
    return "OK - Bot activo ✅", 200

def run_web():
    port = int(os.environ.get("PORT", "10000"))
    flask_app.run(host="0.0.0.0", port=port)

# -----------------------
# ✅ Contador por grupo
# -----------------------
contador = defaultdict(int)  # contador[(chat_id, user_id)] = mensajes
nombres = {}                 # nombres[(chat_id, user_id)] = nombre
fecha_actual = {}            # fecha_actual[chat_id] = date

def hoy():
    return datetime.date.today()

def reset_si_cambia_dia(chat_id):
    if fecha_actual.get(chat_id) != hoy():
        keys = [k for k in list(contador.keys()) if k[0] == chat_id]
        for k in keys:
            contador.pop(k, None)
            nombres.pop(k, None)
        fecha_actual[chat_id] = hoy()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Bot activo. Escribe mensajes y usa /top")

async def contar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    chat_id = update.effective_chat.id
    user = update.effective_user

    reset_si_cambia_dia(chat_id)

    key = (chat_id, user.id)
    contador[key] += 1
    nombres[key] = user.first_name or "Usuario"

async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    reset_si_cambia_dia(chat_id)

    items = [((c, u), n) for (c, u), n in contador.items() if c == chat_id]
    if not items:
        await update.message.reply_text("No hay mensajes hoy en este grupo.")
        return

    orden = sorted(items, key=lambda x: x[1], reverse=True)

    ranking = "📊 Ranking diario (este grupo):\n\n"
    medallas = ["🥇", "🥈", "🥉"]

    for i, ((c, user_id), mensajes) in enumerate(orden, 1):
        nombre = nombres.get((c, user_id), "Usuario")
        icono = medallas[i-1] if i <= 3 else f"{i}."
        ranking += f"{icono} {nombre} → {mensajes} mensajes\n"

    await update.message.reply_text(ranking)

def main():
    # ✅ Levanta el servidor web en segundo plano
    threading.Thread(target=run_web, daemon=True).start()

    # ✅ Bot
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("top", top))
    app.add_handler(MessageHandler(~filters.COMMAND, contar))

    print("✅ Web + Bot corriendo en Render...")
    app.run_polling()

if __name__ == "__main__":
    main()