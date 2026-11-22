import os
import sqlite3
import json
import hashlib
import hmac
import urllib.parse
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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

# =========================
# CONFIGURATION
# =========================

TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

WEBAPP_URL = "https://viewcashmoneybot-production.up.railway.app"

DB_FILE = "bot.db"

if not TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN manquant")

# =========================
# DATABASE
# =========================

def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            balance REAL DEFAULT 0,
            referral_id INTEGER
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS offers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            reward REAL DEFAULT 0,
            video_url TEXT DEFAULT ''
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS completed (
            user_id INTEGER,
            offer_id INTEGER,
            PRIMARY KEY(user_id, offer_id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            method TEXT,
            details TEXT,
            status TEXT DEFAULT 'pending',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


def create_user(user_id, username="", referral_id=None):
    conn = db()

    user = conn.execute(
        "SELECT user_id FROM users WHERE user_id = ?",
        (user_id,)
    ).fetchone()

    if not user:
        conn.execute(
            """
            INSERT INTO users(user_id, username, balance, referral_id)
            VALUES (?, ?, 0, ?)
            """,
            (user_id, username or "", referral_id)
        )

        # Prime de parrainage
        if referral_id and referral_id != user_id:
            conn.execute(
                "UPDATE users SET balance = balance + 0.10 WHERE user_id = ?",
                (referral_id,)
            )

    else:
        conn.execute(
            "UPDATE users SET username = ? WHERE user_id = ?",
            (username or "", user_id)
        )

    conn.commit()
    conn.close()


# =========================
# TELEGRAM
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    referral_id = None

    if context.args:
        try:
            referral_id = int(context.args[0])
        except:
            referral_id = None

    create_user(
        user.id,
        user.username or "",
        referral_id
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "🚀 OUVRIR VIEW CASH",
                web_app=WebAppInfo(url=WEBAPP_URL)
            )
        ]
    ]

    await update.message.reply_text(
        "🎬 Bienvenue sur ViewCash !\n\n"
        "Gagne des récompenses en réalisant les offres disponibles.\n\n"
        "👇 Appuie sur le bouton pour ouvrir View Cash.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def adminoffers(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Accès refusé.")
        return

    conn = db()

    offers = conn.execute(
        "SELECT * FROM offers ORDER BY id DESC"
    ).fetchall()

    conn.close()

    if not offers:
        await update.message.reply_text(
            "📭 Aucune offre."
        )
        return

    text = "📋 OFFRES VIEWCASH\n\n"

    for offer in offers:
        text += (
            f"#{offer['id']} — {offer['title']}\n"
            f"💰 {offer['reward']:.2f} €\n"
            f"🔗 {offer['url']}\n\n"
        )

    await update.message.reply_text(text)


async def addoffer(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Accès refusé.")
        return

    if len(context.args) < 3:
        await update.message.reply_text(
            "Utilisation :\n\n"
            "/addoffer Titre URL Récompense [VideoURL]"
        )
        return

    reward = None

    try:
        reward = float(context.args[2])
    except:
        await update.message.reply_text(
            "❌ La récompense doit être un nombre."
        )
        return

    title = context.args[0]
    url = context.args[1]
    video_url = context.args[3] if len(context.args) >= 4 else ""

    conn = db()

    conn.execute(
        """
        INSERT INTO offers(title, url, reward, video_url)
        VALUES (?, ?, ?, ?)
        """,
        (title, url, reward, video_url)
    )

    conn.commit()
    conn.close()

    await update.message.reply_text(
        "✅ Offre ajoutée !\n\n"
        f"🎬 {title}\n"
        f"💰 {reward:.2f} €"
    )


# =========================
# TELEGRAM WEBAPP API
# =========================

def validate_telegram_data(init_data):

    if not init_data:
        return None

    try:
        parsed = urllib.parse.parse_qs(init_data)

        received_hash = parsed.get("hash", [None])[0]

        if not received_hash:
            return None

        data_check = []

        for key in sorted(parsed.keys()):
            if key == "hash":
                continue

            value = parsed[key][0]
            data_check.append(f"{key}={value}")

        data_check_string = "\n".join(data_check)

        secret_key = hmac.new(
            b"WebAppData",
            TOKEN.encode(),
            hashlib.sha256
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(
            calculated_hash,
            received_hash
        ):
            return None

        user_data = json.loads(
            parsed.get("user", ["{}"])[0]
        )

        return user_data

    except Exception:
        return None


def json_response(handler, data, status=200):

    content = json.dumps(
        data,
        ensure_ascii=False
    ).encode("utf-8")

    handler.send_response(status)
    handler.send_header(
        "Content-Type",
        "application/json; charset=utf-8"
    )
    handler.send_header(
        "Content-Length",
        str(len(content))
    )
    handler.send_header(
        "Access-Control-Allow-Origin",
        "*"
    )
    handler.end_headers()

    handler.wfile.write(content)


# =========================
# WEB SERVER
# =========================

class WebHandler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):

        self.send_response(200)
        self.send_header(
            "Access-Control-Allow-Origin",
            "*"
        )
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type"
        )
        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, POST, OPTIONS"
        )
        self.end_headers()


    def do_GET(self):

        path = self.path.split("?")[0]

        # APPLICATION
        if path in ["/", "/index.htm", "/index.html"]:

            try:
                with open("index.htm", "rb") as file:
                    content = file.read()

                self.send_response(200)
                self.send_header(
                    "Content-Type",
                    "text/html; charset=utf-8"
                )
                self.send_header(
                    "Content-Length",
                    str(len(content))
                )
                self.end_headers()

                self.wfile.write(content)

            except Exception as e:

                self.send_response(500)
                self.end_headers()

            return

        # OFFRES
        if path == "/api/offers":

            conn = db()

            offers = conn.execute(
                "SELECT * FROM offers ORDER BY id DESC"
            ).fetchall()

            conn.close()

            data = []

            for offer in offers:

                data.append({
                    "id": offer["id"],
                    "title": offer["title"],
                    "url": offer["url"],
                    "reward": offer["reward"],
                    "video_url": offer["video_url"]
                })

            json_response(
                self,
                {"offers": data}
            )

            return

        json_response(
            self,
            {"error": "Not found"},
            404
        )


    def do_POST():

        pass


    def do_POST(self):

        path = self.path.split("?")[0]

        length = int(
            self.headers.get("Content-Length", 0)
        )

        body = self.rfile.read(length)

        try:
            data = json.loads(body.decode("utf-8"))
        except:
            json_response(
                self,
                {"error": "JSON invalide"},
                400
            )
            return

        # =========================
        # AUTHENTIFICATION
        # =========================

        init_data = data.get("initData", "")

        telegram_user = validate_telegram_data(
            init_data
        )

        if not telegram_user:

            json_response(
                self,
                {"error": "Utilisateur Telegram invalide"},
                401
            )
            return

        user_id = int(
            telegram_user["id"]
        )

        username = telegram_user.get(
            "username",
            ""
        )

        create_user(
            user_id,
            username
        )

        # =========================
        # USER
        # =========================

        if path == "/api/user":

            conn = db()

            user = conn.execute(
                """
                SELECT user_id, username, balance, referral_id
                FROM users
                WHERE user_id = ?
                """,
                (user_id,)
            ).fetchone()

            conn.close()

            json_response(
                self,
                {
                    "user_id": user["user_id"],
                    "username": user["username"],
                    "balance": user["balance"],
                    "referral_id": user["referral_id"]
                }
            )

            return

        # =========================
        # COMPLETE OFFER
        # =========================

        if path == "/api/complete":

            offer_id = int(
                data.get("offer_id", 0)
            )

            conn = db()

            offer = conn.execute(
                "SELECT * FROM offers WHERE id = ?",
                (offer_id,)
            ).fetchone()

            if not offer:

                conn.close()

                json_response(
                    self,
                    {"error": "Offre introuvable"},
                    404
                )

                return

            already = conn.execute(
                """
                SELECT * FROM completed
                WHERE user_id = ? AND offer_id = ?
                """,
                (user_id, offer_id)
            ).fetchone()

            if already:

                conn.close()

                json_response(
                    self,
                    {"error": "Offre déjà terminée"},
                    400
                )

                return

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
                "SELECT balance FROM users WHERE user_id = ?",
                (user_id,)
            ).fetchone()

            conn.close()

            json_response(
                self,
                {
                    "success": True,
                    "reward": offer["reward"],
                    "balance": user["balance"]
                }
            )

            return

        # =========================
        # WITHDRAW
        # =========================

        if path == "/api/withdraw":

            amount = float(
                data.get("amount", 0)
            )

            method = str(
                data.get("method", "")
            )

            details = str(
                data.get("details", "")
            )

            if amount < 5:

                json_response(
                    self,
                    {
                        "error":
                        "Le retrait minimum est de 5 €."
                    },
                    400
                )

                return

            conn = db()

            user = conn.execute(
                """
                SELECT balance
                FROM users
                WHERE user_id = ?
                """,
                (user_id,)
            ).fetchone()

            if not user or user["balance"] < amount:

                conn.close()

                json_response(
                    self,
                    {"error": "Solde insuffisant"},
                    400
                )

                return

            conn.execute(
                """
                UPDATE users
                SET balance = balance - ?
                WHERE user_id = ?
                """,
                (amount, user_id)
            )

            conn.execute(
                """
                INSERT INTO withdrawals
                (user_id, amount, method, details)
                VALUES (?, ?, ?, ?)
                """,
                (
                    user_id,
                    amount,
                    method,
                    details
                )
            )

            conn.commit()
            conn.close()

            json_response(
                self,
                {
                    "success": True,
                    "message":
                    "Demande de retrait envoyée."
                }
            )

            return

        json_response(
            self,
            {"error": "Route inconnue"},
            404
        )


# =========================
# START WEB SERVER
# =========================

def start_web_server():

    port = int(
        os.getenv("PORT", "8080")
    )

    server = ThreadingHTTPServer(
        ("0.0.0.0", port),
        WebHandler
    )

    print(
        f"🌐 ViewCash lancé sur le port {port}"
    )

    server.serve_forever()


# =========================
# MAIN
# =========================

def main():

    init_db()

    web_thread = threading.Thread(
        target=start_web_server,
        daemon=True
    )

    web_thread.start()

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
        CommandHandler(
            "adminoffers",
            adminoffers
        )
    )

    app.add_handler(
        CommandHandler(
            "addoffer",
            addoffer
        )
    )

    print("🤖 ViewCash Bot démarré")

    app.run_polling()


if __name__ == "__main__":
    main()
