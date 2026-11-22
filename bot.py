import os
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = os.getenv("TELEGRAM_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 Bienvenue sur ViewCash !\n\n"
        "Regarde les vidéos disponibles et découvre les offres.\n\n"
        "🎥 /videos — Voir les vidéos\n"
        "💰 /balance — Voir mon solde\n"
        "👥 /referral — Mon parrainage\n"
        "💸 /withdraw — Demander un retrait\n"
        "❓ /help — Aide"
    )

async def videos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 Vidéos disponibles\n\n"
        "Aucune vidéo disponible pour le moment."
    )

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("💰 Ton solde : 0,00 €")

async def referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👥 Ton lien de parrainage sera disponible ici."
    )

async def withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💸 Les retraits seront disponibles lorsque les conditions seront remplies."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❓ Aide\n\n"
        "/start — Commencer\n"
        "/videos — Voir les vidéos\n"
        "/balance — Voir le solde\n"
        "/referral — Parrainage\n"
        "/withdraw — Retrait"
    )

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("videos", videos))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("referral", referral))
    app.add_handler(CommandHandler("withdraw", withdraw))
    app.add_handler(CommandHandler("help", help_command))

    app.run_polling()

if __name__ == "__main__":
    main()
