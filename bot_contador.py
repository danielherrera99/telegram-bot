import os
import datetime
from collections import defaultdict
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    ContextTypes,
    filters,
)

# 🔐 Token desde variable de entorno (RENDER)
TOKEN = os.environ.get("BOT_TOKEN")

if not TOKEN:
    raise ValueError("No se encontró BOT_TOKEN en las variables de entorno")

# contador[(chat_id, user_id)] = mensajes
contador = defaultdict(int)
nombres = {}
fecha_actual = {}

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
    await update.message.reply_text("✅ Bot activo en este grupo. Escribe mensajes y usa /top")

async def contar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    chat_id = update.effective_chat.id
    user = update.effective_user

    print("ENTRÓ contar | chat:", chat_id, "| user:", user.first_name)

    reset_si_cambia_dia(chat_id)

    key = (chat_id, user.id)
    contador[key] += 1
    nombres[key] = user.first_name

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
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("top", top))
    app.add_handler(MessageHandler(~filters.COMMAND, contar))

    print("🚀 Bot corriendo en Render...")
    app.run_polling()

if __name__ == "__main__":
    main()