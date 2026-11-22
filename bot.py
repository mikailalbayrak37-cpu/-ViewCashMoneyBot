import os
import sqlite3

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    ConversationHandler,
    filters,
)

TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

DB = "bot.db"

VIDEO, TITLE, REWARD = range(3)


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
            reward REAL NOT NULL,
            video_id TEXT
        )
    """)

    # Pour les anciennes bases
    try:
        cur.execute("ALTER TABLE offers ADD COLUMN video_id TEXT")
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
            INSERT INTO users (user_id, balance, referral_id)
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

    cur.execute("""
        SELECT id, title, url, reward, video_id
        FROM offers
        ORDER BY id DESC
    """)

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

    if update.callback_query:
        await update.callback_query.answer()

    for offer_id, title, url, reward, video_id in offers:

        text = (
            f"🎬 {title}\n\n"
            f"💰 Récompense : {reward:.2f} €\n\n"
        )

        if video_id:
            await update.effective_chat.send_video(
                video=video_id,
                caption=text
            )
        elif url:
            keyboard = [
                [
                    InlineKeyboardButton(
                        "🎬 Ouvrir l'offre",
                        url=url
                    )
                ]
            ]

            await update.effective_chat.send_message(
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

        keyboard = [
            [
                InlineKeyboardButton(
                    "✅ J'ai terminé",
                    callback_data=f"complete:{offer_id}"
                )
            ]
        ]

        await update.effective_chat.send_message(
            "⚠️ Valide uniquement si les conditions de l'offre "
            "ont réellement été remplies.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


# =========================
# VALIDATION
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
        """
        SELECT 1 FROM completed
        WHERE user_id = ? AND offer_id = ?
        """,
        (user_id, offer_id)
    )

    if cur.fetchone():
        conn.close()
        await query.message.reply_text(
            "⚠️ Cette offre a déjà été validée."
        )
        return

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS completed (
            user_id INTEGER,
            offer_id INTEGER,
            PRIMARY KEY (user_id, offer_id)
        )
        """
    )

    cur.execute(
        """
        INSERT INTO completed (user_id, offer_id)
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
        f"✅ Offre enregistrée !\n\n"
        f"🎬 {title}\n"
        f"💰 +{reward:.2f} €"
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

    amount = result[0] if result else 0

    if amount < 5:
        text = (
            "💸 Retrait\n\n"
            f"Ton solde : {amount:.2f} €\n\n"
            "❌ Le retrait est disponible à partir de 5 €."
        )
    else:
        text = (
            "💸 Retrait\n\n"
            f"Ton solde : {amount:.2f} €\n\n"
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
        "/help — Aide\n\n"
        "👑 Commandes administrateur :\n"
        "/addvideo — Ajouter une vidéo\n"
        "/adminoffers — Voir les offres"
    )

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text)
    else:
        await update.message.reply_text(text)


# =========================
# ADMIN : AJOUT VIDÉO
# =========================

async def addvideo_start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Accès refusé.")
        return ConversationHandler.END

    await update.message.reply_text(
        "🎬 Envoie-moi maintenant la vidéo."
    )

    return VIDEO


async def receive_video(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    if not update.message.video:
        await update.message.reply_text(
            "❌ Envoie une vidéo Telegram."
        )
        return VIDEO

    context.user_data["video_id"] = update.message.video.file_id

    await update.message.reply_text(
        "✅ Vidéo reçue !\n\n"
        "Quel est le titre de l'offre ?"
    )

    return TITLE


async def receive_title(update: Update, context: ContextTypes.DEFAULT_TYPE):

    context.user_data["title"] = update.message.text

    await update.message.reply_text(
        "💰 Quelle récompense ?\n\n"
        "Exemple : 0.10"
    )

    return REWARD


async def receive_reward(update: Update, context: ContextTypes.DEFAULT_TYPE):

    try:
        reward = float(update.message.text.replace(",", "."))
    except ValueError:
        await update.message.reply_text(
            "❌ Montant incorrect.\n"
            "Exemple : 0.10"
        )
        return REWARD

    title = context.user_data["title"]
    video_id = context.user_data["video_id"]

    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO offers (title, url, reward, video_id)
        VALUES (?, ?, ?, ?)
        """,
        (title, None, reward, video_id)
    )

    conn.commit()
    conn.close()

    context.user_data.clear()

    await update.message.reply_text(
        "✅ Offre créée !\n\n"
        f"🎬 {title}\n"
        f"💰 Récompense : {reward:.2f} €\n\n"
        "Les utilisateurs peuvent maintenant la voir avec /videos."
    )

    return ConversationHandler.END


async def cancel_addvideo(update: Update, context: ContextTypes.DEFAULT_TYPE):

    context.user_data.clear()

    await update.message.reply_text(
        "❌ Ajout de vidéo annulé."
    )

    return ConversationHandler.END


# =========================
# ADMIN : LISTE DES OFFRES
# =========================

async def adminoffers(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Accès refusé.")
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
            "📋 Aucune offre."
        )
        return

    text = "📋 Offres actuelles :\n\n"

    for offer_id, title, reward in offers:
        text += (
            f"#{offer_id} — {title}\n"
            f"💰 {reward:.2f} €\n\n"
        )

    await update.message.reply_text(text)


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
        raise RuntimeError(
            "TELEGRAM_TOKEN est manquant."
        )

    init_db()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("videos", videos))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("referral", referral))
    app.add_handler(CommandHandler("withdraw", withdraw))
    app.add_handler(CommandHandler("help", help_command))

    app.add_handler(CommandHandler("adminoffers", adminoffers))

    video_conversation = ConversationHandler(
        entry_points=[
            CommandHandler("addvideo", addvideo_start)
        ],
        states={
            VIDEO: [
                MessageHandler(
                    filters.VIDEO,
                    receive_video
                )
            ],
            TITLE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_title
                )
            ],
            REWARD: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_reward
                )
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_addvideo)
        ],
    )

    app.add_handler(video_conversation)

    app.add_handler(
        CallbackQueryHandler(button_handler)
    )

    print("ViewCashBot démarré.")

    app.run_polling()


if __name__ == "__main__":
    main()
