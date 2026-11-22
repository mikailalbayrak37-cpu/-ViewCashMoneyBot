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
            url TEXT,
            video_url TEXT,
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

    # Compatibilité avec une ancienne base
    try:
        cur.execute("ALTER TABLE offers ADD COLUMN video_url TEXT")
    except sqlite3.OperationalError:
        pass

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
            """
            INSERT INTO users
            (user_id, balance, referral_id)
            VALUES (?, ?, ?)
            """,
            (user_id, 0, referral_id)
        )

        if referral_id and referral_id != user_id:
            cur.execute(
                """
                UPDATE users
                SET balance = balance + 0.10
                WHERE user_id = ?
                """,
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
        [InlineKeyboardButton(
            "🎬 Voir les vidéos",
            callback_data="offers"
        )],
        [InlineKeyboardButton(
            "💰 Mon solde",
            callback_data="balance"
        )],
        [InlineKeyboardButton(
            "👥 Parrainage",
            callback_data="referral"
        )],
        [InlineKeyboardButton(
            "💸 Retrait",
            callback_data="withdraw"
        )],
        [InlineKeyboardButton(
            "❓ Aide",
            callback_data="help"
        )],
    ]

    await update.message.reply_text(
        "🎬 Bienvenue sur ViewCash !\n\n"
        "Regarde les vidéos et découvre les offres disponibles.\n\n"
        "💰 Gagne des récompenses lorsque les conditions "
        "des offres sont réellement remplies.\n\n"
        "👥 Invite tes amis avec ton lien de parrainage.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =========================
# VIDÉOS / OFFRES
# =========================

async def videos(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id
    create_user(user_id)

    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    cur.execute("""
        SELECT id, title, url, video_url, reward
        FROM offers
        ORDER BY id DESC
    """)

    offers = cur.fetchall()
    conn.close()

    if not offers:

        text = (
            "🎬 Vidéos disponibles\n\n"
            "Aucune vidéo disponible pour le moment."
        )

        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.message.reply_text(text)
        else:
            await update.message.reply_text(text)

        return

    if update.callback_query:
        await update.callback_query.answer()

    for offer_id, title, url, video_url, reward in offers:

        keyboard = [
            [
                InlineKeyboardButton(
                    "✅ J'ai terminé",
                    callback_data=f"complete:{offer_id}"
                )
            ]
        ]

        markup = InlineKeyboardMarkup(keyboard)

        caption = (
            f"🎬 {title}\n\n"
            f"💰 Récompense : {reward:.2f} €\n\n"
            "Regarde la vidéo puis appuie sur "
            "« ✅ J'ai terminé » lorsque tu as réellement "
            "terminé l'offre."
        )

        # Vidéo directement dans Telegram
        if video_url:

            try:
                await update.effective_chat.send_video(
                    video=video_url,
                    caption=caption,
                    reply_markup=markup,
                    supports_streaming=True
                )

            except Exception:
                await update.effective_chat.send_message(
                    "❌ Impossible de charger cette vidéo."
                )

        # Si aucune vidéo n'est définie,
        # on garde éventuellement le lien de l'offre.
        elif url:

            keyboard_url = [
                [
                    InlineKeyboardButton(
                        "🎬 Ouvrir l'offre",
                        url=url
                    )
                ],
                [
                    InlineKeyboardButton(
                        "✅ J'ai terminé",
                        callback_data=f"complete:{offer_id}"
                    )
                ]
            ]

            await update.effective_chat.send_message(
                caption,
                reply_markup=InlineKeyboardMarkup(keyboard_url)
            )

        else:

            await update.effective_chat.send_message(
                caption,
                reply_markup=markup
            )


# =========================
# VALIDATION
# =========================

async def complete_offer(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    try:
        offer_id = int(query.data.split(":")[1])
    except (ValueError, IndexError):
        await query.message.reply_text("❌ Offre invalide.")
        return

    create_user(user_id)

    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    cur.execute(
        """
        SELECT title, reward
        FROM offers
        WHERE id = ?
        """,
        (offer_id,)
    )

    offer = cur.fetchone()

    if not offer:
        conn.close()
        await query.message.reply_text(
            "❌ Cette offre n'existe plus."
        )
        return

    title, reward = offer

    cur.execute(
        """
        SELECT 1
        FROM completed
        WHERE user_id = ? AND offer_id = ?
        """,
        (user_id, offer_id)
    )

    if cur.fetchone():
        conn.close()

        await query.message.reply_text(
            "⚠️ Tu as déjà validé cette offre."
        )
        return

    cur.execute(
        """
        INSERT INTO completed
        (user_id, offer_id)
        VALUES (?, ?)
        """,
        (user_id, offer_id)
    )

    cur.execute(
        """
        UPDATE users
        SET balance = balance + ?
        WHERE user_id = ?
        """,
        (reward, user_id)
    )

    conn.commit()
    conn.close()

    await query.message.reply_text(
        f"✅ Offre validée !\n\n"
        f"🎬 {title}\n"
        f"💰 +{reward:.2f} €\n\n"
        "⚠️ La validation est déclarative : "
        "le bot ne peut pas confirmer automatiquement "
        "que toute la vidéo a été regardée."
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
        """
        SELECT balance
        FROM users
        WHERE user_id = ?
        """,
        (user_id,)
    )

    result = cur.fetchone()
    conn.close()

    amount = result[0] if result else 0

    text = (
        "💰 Mon solde\n\n"
        f"💵 {amount:.2f} €"
    )

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text)
    else:
        await update.message.reply_text(text)


# =========================
# PARRAINAGE
# =========================

async def referral(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    create_user(user_id)

    username = context.bot.username

    link = f"https://t.me/{username}?start={user_id}"

    text = (
        "👥 Parrainage\n\n"
        "Invite tes amis avec ton lien :\n\n"
        f"{link}\n\n"
        "💰 Tu reçois 0,10 € lorsqu'un nouvel "
        "utilisateur rejoint le bot avec ton lien."
    )

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text)
    else:
        await update.message.reply_text(text)


# =========================
# RETRAIT
# =========================

async def withdraw(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id
    create_user(user_id)

    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    cur.execute(
        """
        SELECT balance
        FROM users
        WHERE user_id = ?
        """,
        (user_id,)
    )

    result = cur.fetchone()
    conn.close()

    balance_amount = result[0] if result else 0

    if balance_amount < 5:

        text = (
            "💸 Retrait\n\n"
            f"💰 Solde : {balance_amount:.2f} €\n\n"
            "❌ Le retrait est disponible à partir de 5 €."
        )

    else:

        text = (
            "💸 Retrait\n\n"
            f"💰 Solde : {balance_amount:.2f} €\n\n"
            "✅ Tu peux demander un retrait.\n\n"
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

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = (
        "❓ Aide\n\n"
        "/start — Commencer\n"
        "/videos — Voir les vidéos\n"
        "/balance — Voir le solde\n"
        "/referral — Parrainage\n"
        "/withdraw — Retrait\n"
        "/help — Aide\n\n"
        "Les récompenses doivent correspondre "
        "à des offres réellement réalisées."
    )

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text)
    else:
        await update.message.reply_text(text)


# =========================
# ADMIN : AJOUTER UNE VIDÉO
# =========================

async def add_offer(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_user.id != ADMIN_ID:

        await update.message.reply_text(
            "❌ Accès refusé."
        )

        return

    if len(context.args) < 3:

        await update.message.reply_text(
            "📌 Format :\n\n"
            "/addoffer Titre VIDEO_URL récompense\n\n"
            "Exemple :\n"
            "/addoffer Ma vidéo https://site.com/video.mp4 0.10"
        )

        return

    try:
        reward = float(context.args[-1])
    except ValueError:

        await update.message.reply_text(
            "❌ La récompense doit être un nombre.\n\n"
            "Exemple : 0.10"
        )

        return

    video_url = context.args[-2]

    title = " ".join(context.args[:-2])

    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO offers
        (title, url, video_url, reward)
        VALUES (?, ?, ?, ?)
        """,
        (
            title,
            video_url,
            video_url,
            reward
        )
    )

    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"✅ Vidéo ajoutée !\n\n"
        f"🎬 {title}\n"
        f"💰 Récompense : {reward:.2f} €"
    )


# =========================
# ADMIN : LISTE DES OFFRES
# =========================

async def admin_offers(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_user.id != ADMIN_ID:

        await update.message.reply_text(
            "❌ Accès refusé."
        )

        return

    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, title, reward
        FROM offers
        ORDER BY id DESC
        """
    )

    offers = cur.fetchall()
    conn.close()

    if not offers:

        await update.message.reply_text(
            "📭 Aucune offre."
        )

        return

    text = "📋 Offres disponibles\n\n"

    for offer_id, title, reward in offers:

        text += (
            f"🆔 {offer_id}\n"
            f"🎬 {title}\n"
            f"💰 {reward:.2f} €\n\n"
        )

    await update.message.reply_text(text)


# =========================
# BOUTONS
# =========================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

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
        raise RuntimeError(
            "TELEGRAM_TOKEN est manquant."
        )

    if ADMIN_ID == 0:
        print(
            "⚠️ ADMIN_ID n'est pas configuré."
        )

    init_db()

    app = (
        Application
        .builder()
        .token(TOKEN)
        .build()
    )

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CommandHandler("videos", videos)
    )

    app.add_handler(
        CommandHandler("balance", balance)
    )

    app.add_handler(
        CommandHandler("referral", referral)
    )

    app.add_handler(
        CommandHandler("withdraw", withdraw)
    )

    app.add_handler(
        CommandHandler("help", help_command)
    )

    app.add_handler(
        CommandHandler("addoffer", add_offer)
    )

    app.add_handler(
        CommandHandler("adminoffers", admin_offers)
    )

    app.add_handler(
        CallbackQueryHandler(button_handler)
    )

    print("ViewCashBot démarré.")

    app.run_polling()


if __name__ == "__main__":
    main()
