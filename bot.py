import os
import sqlite3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

DB = "bot.db"

# =========================
# BASE DE DONNÉES
# =========================

def init_db():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            balance REAL DEFAULT 0,
            referral_id INTEGER
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS offers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            reward REAL NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS completed (
            user_id INTEGER,
            offer_id INTEGER,
            PRIMARY KEY (user_id, offer_id)
        )
    """)

    conn.commit()
    conn.close()


def create_user(user_id, referral_id=None):
    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    cur.execute(
        "SELECT user_id FROM users WHERE user_id = ?",
        (user_id,)
    )

    if cur.fetchone() is None:
        cur.execute(
            "INSERT INTO users (user_id, balance, referral_id) VALUES (?, ?, ?)",
            (user_id, 0, referral_id)
        )

        # Récompense de parrainage
        if referral_id and referral_id != user_id:
            cur.execute(
                "UPDATE users SET balance = balance + 0.10 WHERE user_id = ?",
                (referral_id,)
            )

    conn.commit()
    conn.close()


# =========================
# /START
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id
    referral_id = None

    if context.args:
        try:
            referral_id = int(context.args[0])
        except ValueError:
            referral_id = None

    create_user(user_id, referral_id)

    keyboard = [
        [InlineKeyboardButton("🎬 Voir les offres", callback_data="offers")],
        [InlineKeyboardButton("💰 Mon solde", callback_data="balance")],
        [InlineKeyboardButton("👥 Parrainage", callback_data="referral")],
        [InlineKeyboardButton("💸 Retrait", callback_data="withdraw")],
        [InlineKeyboardButton("❓ Aide", callback_data="help")],
    ]

    await update.message.reply_text(
        "🎬 Bienvenue sur ViewCash !\n\n"
        "Découvre les offres disponibles et gagne des récompenses "
        "lorsque les conditions des offres sont réellement remplies.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =========================
# OFFRES
# =========================

async def videos(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id
    create_user(user_id)

    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    cur.execute(
        "SELECT id, title, url, reward FROM offers ORDER BY id DESC"
    )

    offers = cur.fetchall()
    conn.close()

    if not offers:
        text = (
            "🎬 Offres disponibles\n\n"
            "Aucune offre disponible pour le moment."
        )

        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.message.reply_text(text)
        else:
            await update.message.reply_text(text)

        return

    for offer_id, title, url, reward in offers:

        keyboard = [
            [InlineKeyboardButton(
                f"🎬 Ouvrir l'offre",
                url=url
            )],
            [InlineKeyboardButton(
                f"✅ J'ai terminé",
                callback_data=f"complete:{offer_id}"
            )]
        ]

        text = (
            f"🎬 {title}\n\n"
            f"💰 Récompense : {reward:.2f} €\n\n"
            "⚠️ La récompense ne doit être validée que si "
            "les conditions de l'offre sont réellement remplies."
        )

        if update.callback_query:
            await update.callback_query.message.reply_text(
                text,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await update.message.reply_text(
                text,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

    if update.callback_query:
        await update.callback_query.answer()


# =========================
# VALIDATION D'UNE OFFRE
# =========================

async def complete_offer(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    offer_id = int(query.data.split(":")[1])

    create_user(user_id)

    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    cur.execute(
        "SELECT title, reward FROM offers WHERE id = ?",
        (offer_id,)
    )

    offer = cur.fetchone()

    if not offer:
        conn.close()
        await query.message.reply_text("❌ Offre introuvable.")
        return

    title, reward = offer

    cur.execute(
        "SELECT 1 FROM completed WHERE user_id = ? AND offer_id = ?",
        (user_id, offer_id)
    )

    if cur.fetchone():
        conn.close()
        await query.message.reply_text(
            "⚠️ Cette offre a déjà été validée."
        )
        return

    cur.execute(
        "INSERT INTO completed (user_id, offer_id) VALUES (?, ?)",
        (user_id, offer_id)
    )

    cur.execute(
        "UPDATE users SET balance = balance + ? WHERE user_id = ?",
        (reward, user_id)
    )

    conn.commit()
    conn.close()

    await query.message.reply_text(
        f"✅ Offre enregistrée !\n\n"
        f"🎬 {title}\n"
        f"💰 +{reward:.2f} €\n\n"
        "⚠️ Cette validation est déclarative : "
        "le bot ne vérifie pas automatiquement que la vidéo a été regardée."
    )


# =========================
# SOLDE
# =========================

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id
    create_user(user_id)

    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    cur.execute(
        "SELECT balance FROM users WHERE user_id = ?",
        (user_id,)
    )

    result = cur.fetchone()
    conn.close()

    amount = result[0] if result else 0

    text = f"💰 Ton solde : {amount:.2f} €"

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text)
    else:
        await update.message.reply_text(text)


# =========================
# PARRAINAGE
# =========================

async def referral(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id
    create_user(user_id)

    username = context.bot.username

    link = f"https://t.me/{username}?start={user_id}"

    text = (
        "👥 Ton parrainage\n\n"
        "Invite tes amis avec ton lien :\n\n"
        f"{link}\n\n"
        "💰 Tu reçois 0,10 € lorsqu'un nouvel utilisateur "
        "rejoint le bot avec ton lien."
    )

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text)
    else:
        await update.message.reply_text(text)


# =========================
# RETRAIT
# =========================

async def withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id
    create_user(user_id)

    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    cur.execute(
        "SELECT balance FROM users WHERE user_id = ?",
        (user_id,)
    )

    result = cur.fetchone()
    conn.close()

    balance_amount = result[0] if result else 0

    if balance_amount < 5:
        text = (
            "💸 Retrait\n\n"
            f"Ton solde : {balance_amount:.2f} €\n\n"
            "❌ Le retrait est disponible à partir de 5 €."
        )
    else:
        text = (
            "💸 Retrait\n\n"
            f"Ton solde : {balance_amount:.2f} €\n\n"
            "✅ Tu peux demander un retrait.\n"
            "Contacte l'administrateur pour le traitement."
        )

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text)
    else:
        await update.message.reply_text(text)


# =========================
# AIDE
# =========================

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = (
        "❓ Aide\n\n"
        "/start — Commencer\n"
        "/videos — Voir les offres\n"
        "/balance — Voir le solde\n"
        "/referral — Parrainage\n"
        "/withdraw — Retrait\n"
        "/help — Aide"
    )

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text)
    else:
        await update.message.reply_text(text)


# =========================
# ADMIN : AJOUTER UNE OFFRE
# =========================

async def add_offer(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Accès refusé.")
        return

    if len(context.args) < 3:
        await update.message.reply_text(
            "Format :\n"
            "/addoffer Titre URL récompense\n\n"
            "Exemple :\n"
            "/addoffer Video https://example.com 0.10"
        )
        return

    reward = float(context.args[-1])
    url = context.args[-2]
    title = " ".join(context.args[:-2])

    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO offers (title, url, reward) VALUES (?, ?, ?)",
        (title, url, reward)
    )

    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"✅ Offre ajoutée !\n\n"
        f"🎬 {title}\n"
        f"💰 {reward:.2f} €"
    )


# =========================
# BOUTONS
# =========================

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    if query.data == "offers":
        await videos(update, context)

    elif query.data == "balance":
        await balance(update, context)

    elif query.data == "referral":
        await referral(update, context)

    elif query.data == "withdraw":
        await withdraw(update, context)

    elif query.data == "help":
        await help_command(update, context)

    elif query.data.startswith("complete:"):
        await complete_offer(update, context)


# =========================
# MAIN
# =========================

def main():

    if not TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN est manquant.")

    init_db()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("videos", videos))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("referral", referral))
    app.add_handler(CommandHandler("withdraw", withdraw))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("addoffer", add_offer))

    app.add_handler(CallbackQueryHandler(button_handler))

    print("ViewCashBot démarré.")

    app.run_polling()


if __name__ == "__main__":
    main()
