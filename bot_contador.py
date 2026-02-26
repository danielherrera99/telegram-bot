import os
import threading
import time
import datetime
from dataclasses import dataclass

from flask import Flask
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    ContextTypes,
    filters,
)
from telegram.request import HTTPXRequest


# =========================
# CONFIG
# =========================
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Falta BOT_TOKEN en variables de entorno (Render -> Environment).")

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("Falta DATABASE_URL en variables de entorno (Render -> Environment).")

# Anti-spam: máximo mensajes por usuario en X segundos (para contar)
SPAM_WINDOW_SECONDS = int(os.getenv("SPAM_WINDOW_SECONDS", "8"))
SPAM_MAX_MESSAGES = int(os.getenv("SPAM_MAX_MESSAGES", "6"))

# XP
XP_PER_MESSAGE = int(os.getenv("XP_PER_MESSAGE", "5"))


# =========================
# DB
# =========================
def db_connect():
    import psycopg2
    return psycopg2.connect(DATABASE_URL)


def init_db():
    with db_connect() as conn:
        with conn.cursor() as cur:
            # Contador por día
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

            # Perfil / progreso / antispam
            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_profile (
                    chat_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    username TEXT,
                    xp BIGINT NOT NULL DEFAULT 0,
                    level INTEGER NOT NULL DEFAULT 1,
                    last_message_at TIMESTAMP NULL,
                    spam_count INTEGER NOT NULL DEFAULT 0,
                    spam_window_start TIMESTAMP NULL,
                    PRIMARY KEY (chat_id, user_id)
                );
            """)
        conn.commit()


# Usar UTC evita desfases entre Render/DB
def hoy_utc():
    return datetime.datetime.utcnow().date()


def now_utc():
    return datetime.datetime.utcnow()


def day_range_for(mode: str):
    today = hoy_utc()
    if mode == "day":
        start = today
    elif mode == "week":
        start = today - datetime.timedelta(days=today.weekday())  # lunes
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


def get_top_all(chat_id: int):
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT user_id, COALESCE(MAX(username), 'Usuario') as username, SUM(messages) as total
                FROM msg_counts
                WHERE chat_id = %s
                GROUP BY user_id
                ORDER BY total DESC;
            """, (chat_id,))
            rows = cur.fetchall()
    return rows


@dataclass
class Profile:
    chat_id: int
    user_id: int
    username: str
    xp: int
    level: int
    spam_count: int
    spam_window_start: datetime.datetime | None


def get_or_create_profile(chat_id: int, user_id: int, username: str) -> Profile:
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT chat_id, user_id, COALESCE(username,''), xp, level, spam_count, spam_window_start
                FROM user_profile
                WHERE chat_id=%s AND user_id=%s;
            """, (chat_id, user_id))
            row = cur.fetchone()

            if not row:
                cur.execute("""
                    INSERT INTO user_profile (chat_id, user_id, username, xp, level, spam_count, spam_window_start)
                    VALUES (%s, %s, %s, 0, 1, 0, NULL);
                """, (chat_id, user_id, username))
                conn.commit()
                return Profile(chat_id, user_id, username, 0, 1, 0, None)

            return Profile(
                chat_id=row[0],
                user_id=row[1],
                username=row[2] or username,
                xp=row[3],
                level=row[4],
                spam_count=row[5],
                spam_window_start=row[6],
            )


def update_username(chat_id: int, user_id: int, username: str):
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE user_profile
                SET username=%s
                WHERE chat_id=%s AND user_id=%s;
            """, (username, chat_id, user_id))
        conn.commit()


def set_spam_state(chat_id: int, user_id: int, spam_count: int, window_start: datetime.datetime | None):
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE user_profile
                SET spam_count=%s, spam_window_start=%s
                WHERE chat_id=%s AND user_id=%s;
            """, (spam_count, window_start, chat_id, user_id))
        conn.commit()


def add_xp_and_maybe_level_up(chat_id: int, user_id: int, add_xp: int) -> tuple[int, int, bool]:
    """
    Devuelve: (nuevo_xp, nuevo_level, subio_nivel)
    Regla de niveles simple:
      xp_needed(level) = 100 * level
    """
    with db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT xp, level
                FROM user_profile
                WHERE chat_id=%s AND user_id=%s;
            """, (chat_id, user_id))
            row = cur.fetchone()
            if not row:
                # si por alguna razón no existe, lo creamos luego afuera
                return 0, 1, False

            xp, level = int(row[0]), int(row[1])
            xp += add_xp

            leveled = False
            while xp >= (100 * level):
                xp -= (100 * level)
                level += 1
                leveled = True

            cur.execute("""
                UPDATE user_profile
                SET xp=%s, level=%s
                WHERE chat_id=%s AND user_id=%s;
            """, (xp, level, chat_id, user_id))
        conn.commit()

    return xp, level, leveled


# =========================
# FORMATO
# =========================
def format_ranking(title: str, rows, limit=10):
    if not rows:
        return "No hay mensajes en ese periodo."
    medallas = ["🥇", "🥈", "🥉"]
    text = f"📊 {title}\n\n"
    for i, (_, username, total) in enumerate(rows[:limit], 1):
        icono = medallas[i - 1] if i <= 3 else f"{i}."
        text += f"{icono} {username} → {total} mensajes\n"
    return text


# =========================
# HANDLERS
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ Bot activo.\n"
        "Comandos:\n"
        "• /top (hoy)\n"
        "• /topsemana\n"
        "• /topmes\n"
        "• /topall (histórico)\n"
        "• /stats (tu progreso)\n"
    )


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    username = user.first_name or user.username or "Usuario"

    prof = get_or_create_profile(chat_id, user.id, username)
    update_username(chat_id, user.id, username)

    xp_needed = 100 * prof.level
    await update.message.reply_text(
        f"📈 Stats de {username}\n\n"
        f"⭐ Nivel: {prof.level}\n"
        f"🧠 XP: {prof.xp}/{xp_needed}\n"
        f"🛡 Anti-spam: {SPAM_MAX_MESSAGES} msgs / {SPAM_WINDOW_SECONDS}s\n"
    )


async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    rows, start, end = get_top(chat_id, "day")
    await update.message.reply_text(format_ranking("Ranking diario (hoy UTC)", rows))


async def topsemana(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    rows, start, end = get_top(chat_id, "week")
    await update.message.reply_text(format_ranking(f"Ranking semanal (UTC) {start} a {end}", rows))


async def topmes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    rows, start, end = get_top(chat_id, "month")
    await update.message.reply_text(format_ranking(f"Ranking mensual (UTC) {start} a {end}", rows))


async def topall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    rows = get_top_all(chat_id)
    await update.message.reply_text(format_ranking("Ranking histórico (desde que el bot cuenta)", rows))


async def contar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    chat_id = update.effective_chat.id
    user = update.effective_user
    username = user.first_name or user.username or "Usuario"
    now = now_utc()

    # Perfil (crea si no existe)
    prof = get_or_create_profile(chat_id, user.id, username)
    update_username(chat_id, user.id, username)

    # --- Anti-spam ---
    window_start = prof.spam_window_start
    spam_count = prof.spam_count

    if window_start is None or (now - window_start).total_seconds() > SPAM_WINDOW_SECONDS:
        # reiniciar ventana
        window_start = now
        spam_count = 0

    spam_count += 1
    set_spam_state(chat_id, user.id, spam_count, window_start)

    if spam_count > SPAM_MAX_MESSAGES:
        # No contamos este mensaje
        if spam_count == SPAM_MAX_MESSAGES + 1:
            await update.message.reply_text("🛡️ Anti-spam: vas muy rápido 😅 Espera un toque para que cuente.")
        return

    # --- Contar mensaje (DB por día UTC) ---
    add_message(chat_id, user.id, username, hoy_utc())

    # --- XP / niveles ---
    new_xp, new_level, leveled = add_xp_and_maybe_level_up(chat_id, user.id, XP_PER_MESSAGE)
    if leveled:
        await update.message.reply_text(f"🎉 {username} subió a **Nivel {new_level}**!", parse_mode="Markdown")


# =========================
# FLASK (Render port)
# =========================
app_flask = Flask(__name__)

@app_flask.get("/")
def home():
    return "OK", 200

def run_flask():
    port = int(os.getenv("PORT", "10000"))
    app_flask.run(host="0.0.0.0", port=port)


# =========================
# BOT runner robusto
# =========================
def build_application():
    request = HTTPXRequest(
        connect_timeout=30,
        read_timeout=30,
        write_timeout=30,
        pool_timeout=30,
    )

    app = ApplicationBuilder().token(TOKEN).request(request).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("top", top))
    app.add_handler(CommandHandler("topsemana", topsemana))
    app.add_handler(CommandHandler("topmes", topmes))
    app.add_handler(CommandHandler("topall", topall))
    app.add_handler(MessageHandler(~filters.COMMAND, contar))
    return app


def run_polling_forever():
    while True:
        try:
            app = build_application()
            print("✅ Bot corriendo (polling)...")
            app.run_polling(drop_pending_updates=True)
        except Exception as e:
            print("❌ Bot se cayó. Reintentando en 5s:", repr(e))
            time.sleep(5)


# =========================
# MAIN
# =========================
def main():
    init_db()

    # Flask en hilo para que Render detecte puerto
    threading.Thread(target=run_flask, daemon=True).start()

    # Polling robusto
    run_polling_forever()


if __name__ == "__main__":
    main()