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

# ====== TOKEN ======
TOKEN = os.getenv("BOT_TOKEN")  # <- viene de Render env vars
if not TOKEN:
    raise RuntimeError("Falta BOT_TOKEN en variables de entorno (Render -> Environment).")

# ====== DB (PostgreSQL) ======
DATABASE_URL = os.getenv("DATABASE_URL")

def db_connect():
    import psycopg2
    return psycopg2.connect(DATABASE_URL)

def init_db():
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS msg_counts (
                    chat_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    username TEXT,
                    day DATE NOT NULL,
                    messages INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (chat_id, user_id, day)
                );
            """)
        conn.commit()

def hoy():
    return datetime.date.today()

def day_range_for(mode: str):
    today = hoy()
    if mode == "day":
        start = today
    elif mode == "week":
        # lunes de esta semana
        start = today - datetime.timedelta(days=today.weekday())
    elif mode == "month":
        start = today.replace(day=1)
    else:
        start = today
    return start, today

def add_message(chat_id: int, user_id: int, username: str, day: datetime.date):
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO msg_counts (chat_id, user_id, username, day, messages)
                VALUES (%s, %s, %s, %s, 1)
                ON CONFLICT (chat_id, user_id, day)
                DO UPDATE SET messages = msg_counts.messages + 1,
                              username = EXCLUDED.username;
            """, (chat_id, user_id, username, day))
        conn.commit()

def get_top(chat_id: int, mode: str):
    start, end = day_range_for(mode)
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT user_id, COALESCE(MAX(username), 'Usuario') as username, SUM(messages) as total
                FROM msg_counts
                WHERE chat_id = %s AND day BETWEEN %s AND %s
                GROUP BY user_id
                ORDER BY total DESC;
            """, (chat_id, start, end))
            rows = cur.fetchall()
    return rows, start, end

# ====== HANDLERS ======
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Bot activo. Usa /top /topsemana /topmes")

async def contar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    chat_id = update.effective_chat.id
    user = update.effective_user
    username = user.first_name or user.username or "Usuario"
    add_message(chat_id, user.id, username, hoy())

def format_ranking(title: str, rows):
    if not rows:
        return "No hay mensajes en ese periodo."
    medallas = ["🥇", "🥈", "🥉"]
    text = f"📊 {title}\n\n"
    for i, (user_id, username, total) in enumerate(rows, 1):
        icono = medallas[i-1] if i <= 3 else f"{i}."
        text += f"{icono} {username} → {total} mensajes\n"
    return text

async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    rows, start, end = get_top(chat_id, "day")
    await update.message.reply_text(format_ranking("Ranking diario (hoy)", rows))

async def topsemana(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    rows, start, end = get_top(chat_id, "week")
    await update.message.reply_text(format_ranking(f"Ranking semanal ({start} a {end})", rows))

async def topmes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    rows, start, end = get_top(chat_id, "month")
    await update.message.reply_text(format_ranking(f"Ranking mensual ({start} a {end})", rows))

# ====== FLASK (para Render puerto) ======
app_flask = Flask(__name__)

@app_flask.get("/")
def home():
    return "OK", 200

def run_flask():
    port = int(os.getenv("PORT", "10000"))
    app_flask.run(host="0.0.0.0", port=port)

# ====== MAIN ======
def main():
    init_db()

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("top", top))
    app.add_handler(CommandHandler("topsemana", topsemana))
    app.add_handler(CommandHandler("topmes", topmes))
    app.add_handler(MessageHandler(~filters.COMMAND, contar))

    # Flask en hilo para que Render detecte puerto
    threading.Thread(target=run_flask, daemon=True).start()

    print("✅ Bot corriendo...")
    app.run_polling()

if __name__ == "__main__":
    main()