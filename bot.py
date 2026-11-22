import os
import sqlite3
import threading
import hashlib
import hmac
import json
from urllib.parse import parse_qsl

from flask import Flask, jsonify, request, send_from_directory

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

WEBAPP_URL = os.getenv("WEBAPP_URL", "").rstrip("/")
PORT = int(os.getenv("PORT", "8080"))

DB = "bot.db"

app = Flask(__name__, static_folder="static")


# =========================
# BASE DE DONNÉES
# =========================

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():

    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            balance REAL DEFAULT 0,
            referral_id INTEGER
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS offers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            reward REAL NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS completed (
            user_id INTEGER,
            offer_id INTEGER,
            PRIMARY KEY (user_id, offer_id)
        )
    """)

    conn.commit()
    conn.close()


def create_user(user_id, referral_id=None):

    conn = get_db()

    user = conn.execute(
        "SELECT user_id FROM users WHERE user_id = ?",
        (user_id,)
    ).fetchone()

    if user is None:

        valid_referral = None

        if referral_id and referral_id != user_id:

            ref = conn.execute(
                "SELECT user_id FROM users WHERE user_id = ?",
                (referral_id,)
            ).fetchone()

            if ref:
                valid_referral = referral_id

        conn.execute(
            """
            INSERT INTO users(user_id, balance, referral_id)
            VALUES (?, 0, ?)
            """,
            (user_id, valid_referral)
        )

        if valid_referral:

            conn.execute(
                """
                UPDATE users
                SET balance = balance + 0.10
                WHERE user_id = ?
                """,
                (valid_referral,)
            )

    conn.commit()
    conn.close()


# =========================
# TELEGRAM WEB APP
# =========================

def validate_webapp(init_data):

    if not TOKEN or not init_data:
        return None

    try:

        data = dict(
            parse_qsl(
                init_data,
                keep_blank_values=True
            )
        )

        received_hash = data.pop("hash", None)

        if not received_hash:
            return None

        check_string = "\n".join(
            f"{key}={data[key]}"
            for key in sorted(data)
        )

        secret_key = hmac.new(
            b"WebAppData",
            TOKEN.encode(),
            hashlib.sha256
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(
            calculated_hash,
            received_hash
        ):
            return None

        user = json.loads(
            data.get("user", "{}")
        )

        return int(user["id"])

    except Exception:
        return None


def get_webapp_user():

    init_data = request.headers.get(
        "X-Telegram-Init-Data",
        ""
    )

    user_id = validate_webapp(init_data)

    if user_id:
        create_user(user_id)

    return user_id


# =========================
# MINI APPLICATION
# =========================

@app.route("/")
def home():

    return send_from_directory(
        "static",
        "index.html"
    )


@app.route("/api/offers")
def api_offers():

    user_id = get_webapp_user()

    conn = get_db()

    offers = conn.execute(
        """
        SELECT id, title, url, reward
        FROM offers
        ORDER BY id DESC
        """
    ).fetchall()

    completed = set()

    if user_id:

        rows = conn.execute(
            """
            SELECT offer_id
            FROM completed
            WHERE user_id = ?
            """,
            (user_id,)
        ).fetchall()

        completed = {
            row["offer_id"]
            for row in rows
        }

    conn.close()

    result = []

    for offer in offers:

        result.append({
            "id": offer["id"],
            "title": offer["title"],
            "url": offer["url"],
            "reward": offer["reward"],
            "completed": offer["id"] in completed
        })

    return jsonify({
        "offers": result
    })


@app.route("/api/me")
def api_me():

    user_id = get_webapp_user()

    if not user_id:

        return jsonify({
            "ok": False,
            "error": "Utilisateur Telegram non authentifié"
        }), 401

    conn = get_db()

    user = conn.execute(
        """
        SELECT balance
        FROM users
        WHERE user_id = ?
        """,
        (user_id,)
    ).fetchone()

    conn.close()

    balance = user["balance"] if user else 0

    bot_username = os.getenv(
        "BOT_USERNAME",
        "ViewCashBot"
    )

    referral_link = (
        f"https://t.me/"
        f"{bot_username}"
        f"?start={user_id}"
    )

    return jsonify({
        "ok": True,
        "user_id": user_id,
        "balance": balance,
        "referral_link": referral_link
    })


@app.route(
    "/api/complete/<int:offer_id>",
    methods=["POST"]
)
def complete_offer_webapp(offer_id):

    user_id = get_webapp_user()

    if not user_id:

        return jsonify({
            "ok": False,
            "error": "Non authentifié"
        }), 401

    conn = get_db()

    offer = conn.execute(
        """
        SELECT title, reward
        FROM offers
        WHERE id = ?
        """,
        (offer_id,)
    ).fetchone()

    if not offer:

        conn.close()

        return jsonify({
            "ok": False,
            "error": "Offre introuvable"
        }), 404

    already = conn.execute(
        """
        SELECT 1
        FROM completed
        WHERE user_id = ?
        AND offer_id = ?
        """,
        (user_id, offer_id)
    ).fetchone()

    if already:

        conn.close()

        return jsonify({
            "ok": False,
            "error": "Cette offre a déjà été validée."
        }), 400

    conn.execute(
        """
        INSERT INTO completed(user_id, offer_id)
        VALUES (?, ?)
        """,
        (user_id, offer_id)
    )

    conn.execute(
        """
        UPDATE users
        SET balance = balance + ?
        WHERE user_id = ?
        """,
        (offer["reward"], user_id)
    )

    conn.commit()

    user = conn.execute(
        """
        SELECT balance
        FROM users
        WHERE user_id = ?
        """,
        (user_id,)
    ).fetchone()

    conn.close()

    return jsonify({
        "ok": True,
        "reward": offer["reward"],
        "balance": user["balance"]
    })


# =========================
# BOT TELEGRAM
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id

    referral_id = None

    if context.args:

        try:
            referral_id = int(context.args[0])
        except:
            referral_id = None

    create_user(
        user_id,
        referral_id
    )

    buttons = []

    if WEBAPP_URL:

        buttons.append([
            InlineKeyboardButton(
                "▶️ Ouvrir ViewCash",
                web_app=WebAppInfo(
                    url=WEBAPP_URL
                )
            )
        ])

    buttons.extend([
        [
            InlineKeyboardButton(
                "🎬 Voir les offres",
                callback_data="offers"
            )
        ],
        [
            InlineKeyboardButton(
                "💰 Mon solde",
                callback_data="balance"
            )
        ],
        [
            InlineKeyboardButton(
                "👥 Parrainage",
                callback_data="referral"
            )
        ],
        [
            InlineKeyboardButton(
                "💸 Retrait",
                callback_data="withdraw"
            )
        ]
    ])

    await update.message.reply_text(
        "🎬 Bienvenue sur ViewCash !\n\n"
        "Regarde les offres disponibles "
        "et gagne des récompenses lorsque "
        "les conditions sont réellement remplies.",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# =========================
# OFFRES TELEGRAM
# =========================

async def videos(update: Update, context: ContextTypes.DEFAULT_TYPE):

    conn = get_db()

    offers = conn.execute(
        """
        SELECT id, title, url, reward
        FROM offers
        ORDER BY id DESC
        """
    ).fetchall()

    conn.close()

    if not offers:

        await update.message.reply_text(
            "🎬 Offres disponibles\n\n"
            "Aucune offre disponible pour le moment."
        )

        return

    for offer in offers:

        keyboard = [
            [
                InlineKeyboardButton(
                    "🎬 Ouvrir l'offre",
                    url=offer["url"]
                )
            ]
        ]

        await update.message.reply_text(
            f"🎬 {offer['title']}\n\n"
            f"💰 Récompense : "
            f"{offer['reward']:.2f} €",
            reply_markup=InlineKeyboardMarkup(
                keyboard
            )
        )


# =========================
# SOLDE
# =========================

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):

    create_user(
        update.effective_user.id
    )

    conn = get_db()

    user = conn.execute(
        """
        SELECT balance
        FROM users
        WHERE user_id = ?
        """,
        (update.effective_user.id,)
    ).fetchone()

    conn.close()

    amount = user["balance"] if user else 0

    await update.message.reply_text(
        f"💰 Ton solde : {amount:.2f} €"
    )


# =========================
# PARRAINAGE
# =========================

async def referral(update: Update, context: ContextTypes.DEFAULT_TYPE):

    create_user(
        update.effective_user.id
    )

    bot = await context.bot.get_me()

    link = (
        f"https://t.me/"
        f"{bot.username}"
        f"?start={update.effective_user.id}"
    )

    await update.message.reply_text(
        "👥 Parrainage\n\n"
        "Invite tes amis avec ton lien :\n\n"
        f"{link}\n\n"
        "💰 Tu reçois 0,10 € lorsqu'un "
        "nouvel utilisateur rejoint le bot."
    )


# =========================
# RETRAIT
# =========================

async def withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):

    create_user(
        update.effective_user.id
    )

    conn = get_db()

    user = conn.execute(
        """
        SELECT balance
        FROM users
        WHERE user_id = ?
        """,
        (update.effective_user.id,)
    ).fetchone()

    conn.close()

    amount = user["balance"] if user else 0

    if amount < 5:

        await update.message.reply_text(
            "💸 Retrait\n\n"
            f"Ton solde : {amount:.2f} €\n\n"
            "❌ Retrait disponible à partir de 5 €."
        )

    else:

        await update.message.reply_text(
            "💸 Retrait\n\n"
            f"Ton solde : {amount:.2f} €\n\n"
            "✅ Tu peux demander un retrait.\n"
            "Contacte l'administrateur."
        )


# =========================
# AJOUTER UNE OFFRE
# =========================

async def add_offer(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id != ADMIN_ID:

        await update.message.reply_text(
            "❌ Accès refusé."
        )

        return

    if len(context.args) < 3:

        await update.message.reply_text(
            "Format :\n\n"
            "/addoffer Titre URL récompense\n\n"
            "Exemple :\n"
            "/addoffer Video https://example.com/video.mp4 0.10"
        )

        return

    try:

        reward = float(
            context.args[-1].replace(",", ".")
        )

    except:

        await update.message.reply_text(
            "❌ Récompense invalide."
        )

        return

    url = context.args[-2]

    title = " ".join(
        context.args[:-2]
    )

    try:

        conn = get_db()

        conn.execute(
            """
            INSERT INTO offers(title, url, reward)
            VALUES (?, ?, ?)
            """,
            (
                title,
                url,
                reward
            )
        )

        conn.commit()
        conn.close()

        await update.message.reply_text(
            "✅ Offre ajoutée !\n\n"
            f"🎬 {title}\n"
            f"💰 {reward:.2f} €"
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ Erreur : {e}"
        )


# =========================
# ADMIN OFFRES
# =========================

async def adminoffers(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id != ADMIN_ID:

        await update.message.reply_text(
            "❌ Accès refusé."
        )

        return

    conn = get_db()

    offers = conn.execute(
        """
        SELECT id, title, reward, url
        FROM offers
        ORDER BY id DESC
        """
    ).fetchall()

    conn.close()

    if not offers:

        await update.message.reply_text(
            "📋 Aucune offre."
        )

        return

    text = "📋 Offres disponibles :\n\n"

    for offer in offers:

        text += (
            f"#{offer['id']} — "
            f"{offer['title']}\n"
            f"💰 {offer['reward']:.2f} €\n"
            f"{offer['url']}\n\n"
        )

    await update.message.reply_text(
        text
    )


# =========================
# AIDE
# =========================

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "❓ Aide\n\n"
        "/start — Accueil\n"
        "/videos — Voir les offres\n"
        "/balance — Mon solde\n"
        "/referral — Parrainage\n"
        "/withdraw — Retrait\n"
        "/help — Aide"
    )


# =========================
# SERVEUR WEB
# =========================

def run_web():

    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False
    )


# =========================
# MAIN
# =========================

def main():

    if not TOKEN:

        raise RuntimeError(
            "TELEGRAM_TOKEN est manquant."
        )

    init_db()

    threading.Thread(
        target=run_web,
        daemon=True
    ).start()

    application = (
        Application
        .builder()
        .token(TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("videos", videos)
    )

    application.add_handler(
        CommandHandler("balance", balance)
    )

    application.add_handler(
        CommandHandler("referral", referral)
    )

    application.add_handler(
        CommandHandler("withdraw", withdraw)
    )

    application.add_handler(
        CommandHandler("help", help_command)
    )

    application.add_handler(
        CommandHandler("addoffer", add_offer)
    )

    application.add_handler(
        CommandHandler("adminoffers", adminoffers)
    )

    print("ViewCashBot démarré.")

    application.run_polling()


if __name__ == "__main__":
    main()
