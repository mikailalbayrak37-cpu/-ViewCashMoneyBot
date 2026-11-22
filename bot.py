import os
import sqlite3
import json
import hashlib
import hmac
import urllib.parse
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

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


# ============================================================
# CONFIGURATION
# ============================================================

TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

WEBAPP_URL = "https://viewcashmoneybot-production.up.railway.app"

DB_FILE = "bot.db"

MIN_WITHDRAWAL = 500.0


if not TOKEN:
    raise RuntimeError(
        "TELEGRAM_TOKEN n'est pas configuré dans Railway."
    )


# ============================================================
# BASE DE DONNÉES
# ============================================================

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            balance REAL DEFAULT 0,
            referral_id INTEGER
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS offers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            reward REAL NOT NULL,
            video_url TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS completed (
            user_id INTEGER,
            offer_id INTEGER,
            PRIMARY KEY (user_id, offer_id)
        )
    """)

    cursor.execute("""
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


# ============================================================
# UTILISATEURS
# ============================================================

def create_user(user_id, username=None, referral_id=None):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT user_id FROM users WHERE user_id = ?",
        (user_id,)
    )

    existing = cursor.fetchone()

    if existing:
        cursor.execute(
            """
            UPDATE users
            SET username = ?
            WHERE user_id = ?
            """,
            (username, user_id)
        )

        conn.commit()
        conn.close()
        return

    cursor.execute(
        """
        INSERT INTO users (
            user_id,
            username,
            balance,
            referral_id
        )
        VALUES (?, ?, 0, ?)
        """,
        (
            user_id,
            username,
            referral_id
        )
    )

    if referral_id and referral_id != user_id:

        cursor.execute(
            """
            SELECT user_id
            FROM users
            WHERE user_id = ?
            """,
            (referral_id,)
        )

        referrer = cursor.fetchone()

        if referrer:

            cursor.execute(
                """
                UPDATE users
                SET balance = balance + 0.10
                WHERE user_id = ?
                """,
                (referral_id,)
            )

    conn.commit()
    conn.close()


def get_user(user_id):
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT *
        FROM users
        WHERE user_id = ?
        """,
        (user_id,)
    )

    user = cursor.fetchone()

    conn.close()

    return user


# ============================================================
# /START
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    referral_id = None

    if context.args:

        try:
            referral_id = int(context.args[0])

        except ValueError:
            referral_id = None

    create_user(
        user.id,
        user.username,
        referral_id
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "🚀 OUVRIR VIEW CASH",
                web_app=WebAppInfo(
                    url=WEBAPP_URL
                )
            )
        ]
    ]

    reply_markup = InlineKeyboardMarkup(
        keyboard
    )

    await update.message.reply_text(
        "💰 Bienvenue sur View Cash !\n\n"
        "Regarde les vidéos disponibles et récupère tes récompenses.\n\n"
        "👇 Clique sur le bouton pour ouvrir l'application.",
        reply_markup=reply_markup
    )


# ============================================================
# ADMIN : AFFICHER LES OFFRES
# ============================================================

async def adminoffers(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_user.id != ADMIN_ID:
        return

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            id,
            title,
            url,
            reward,
            video_url
        FROM offers
        ORDER BY id DESC
        """
    )

    offers = cursor.fetchall()

    conn.close()

    if not offers:

        await update.message.reply_text(
            "📭 Aucune offre."
        )

        return

    text = "🎬 OFFRES DISPONIBLES\n\n"

    for offer in offers:

        text += (
            f"🆔 ID : {offer['id']}\n"
            f"📌 {offer['title']}\n"
            f"💰 {offer['reward']:.2f} €\n"
            f"🔗 {offer['url']}\n"
        )

        if offer["video_url"]:
            text += (
                f"🎥 {offer['video_url']}\n"
            )

        text += "\n"

    await update.message.reply_text(text)


# ============================================================
# ADMIN : AJOUTER UNE OFFRE
# ============================================================

async def addoffer(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_user.id != ADMIN_ID:
        return

    if len(context.args) < 3:

        await update.message.reply_text(
            "❌ Utilisation :\n\n"
            "/addoffer Titre URL Récompense VideoURL\n\n"
            "Exemple :\n"
            "/addoffer Video1 https://exemple.com 0.50 https://exemple.com/video.mp4"
        )

        return

    title = context.args[0]
    url = context.args[1]

    try:
        reward = float(context.args[2])

    except ValueError:

        await update.message.reply_text(
            "❌ La récompense doit être un nombre."
        )

        return

    video_url = None

    if len(context.args) >= 4:
        video_url = context.args[3]

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO offers (
            title,
            url,
            reward,
            video_url
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            title,
            url,
            reward,
            video_url
        )
    )

    offer_id = cursor.lastrowid

    conn.commit()
    conn.close()

    await update.message.reply_text(
        "✅ Offre ajoutée !\n\n"
        f"🆔 ID : {offer_id}\n"
        f"📌 {title}\n"
        f"💰 {reward:.2f} €\n"
        f"🎥 {video_url or 'Aucune vidéo'}"
    )


# ============================================================
# VALIDATION TELEGRAM WEB APP
# ============================================================

def validate_init_data(init_data):

    if not init_data:
        return None

    try:

        parsed = urllib.parse.parse_qs(
            init_data,
            keep_blank_values=True
        )

        received_hash = parsed.get(
            "hash",
            [None]
        )[0]

        if not received_hash:
            return None

        data_check = []

        for key in sorted(parsed.keys()):

            if key == "hash":
                continue

            value = parsed[key][0]

            data_check.append(
                f"{key}={value}"
            )

        data_check_string = "\n".join(
            data_check
        )

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

        user_data = parsed.get(
            "user",
            [None]
        )[0]

        if not user_data:
            return None

        return json.loads(user_data)

    except Exception:
        return None


# ============================================================
# RÉPONSE JSON
# ============================================================

def send_json(handler, data, status=200):

    body = json.dumps(
        data,
        ensure_ascii=False
    ).encode("utf-8")

    handler.send_response(status)

    handler.send_header(
        "Content-Type",
        "application/json; charset=utf-8"
    )

    handler.send_header(
        "Access-Control-Allow-Origin",
        "*"
    )

    handler.send_header(
        "Access-Control-Allow-Headers",
        "Content-Type"
    )

    handler.send_header(
        "Access-Control-Allow-Methods",
        "GET, POST, OPTIONS"
    )

    handler.end_headers()

    handler.wfile.write(body)


# ============================================================
# SERVEUR WEB
# ============================================================

class WebHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        return

    # --------------------------------------------------------
    # OPTIONS
    # --------------------------------------------------------

    def do_OPTIONS(self):

        self.send_response(204)

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

    # --------------------------------------------------------
    # GET
    # --------------------------------------------------------

    def do_GET(self):

        parsed = urllib.parse.urlparse(
            self.path
        )

        path = parsed.path

        # ====================================================
        # INDEX
        # ====================================================

        if path == "/" or path == "/index.htm":

            try:

                with open(
                    "index.htm",
                    "rb"
                ) as file:

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

                send_json(
                    self,
                    {
                        "error":
                            "Impossible de charger index.htm",
                        "details": str(e)
                    },
                    500
                )

            return

        # ====================================================
        # OFFRES
        # ====================================================

        if path == "/api/offers":

            conn = get_db()
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT
                    id,
                    title,
                    url,
                    reward,
                    video_url
                FROM offers
                ORDER BY id DESC
                """
            )

            offers = cursor.fetchall()

            conn.close()

            result = []

            for offer in offers:

                result.append(
                    {
                        "id": offer["id"],
                        "title": offer["title"],
                        "url": offer["url"],
                        "reward": offer["reward"],
                        "video_url": offer["video_url"]
                    }
                )

            send_json(
                self,
                {
                    "offers": result
                }
            )

            return

        send_json(
            self,
            {
                "error": "Route introuvable"
            },
            404
        )

    # --------------------------------------------------------
    # POST
    # --------------------------------------------------------

    def do_POST(self):

        parsed = urllib.parse.urlparse(
            self.path
        )

        path = parsed.path

        try:

            content_length = int(
                self.headers.get(
                    "Content-Length",
                    0
                )
            )

            body = self.rfile.read(
                content_length
            )

            data = json.loads(
                body.decode("utf-8")
            )

        except Exception:

            send_json(
                self,
                {
                    "error": "Données invalides."
                },
                400
            )

            return

        # ====================================================
        # USER
        # ====================================================

        if path == "/api/user":

            user_data = validate_init_data(
                data.get("initData")
            )

            if not user_data:

                send_json(
                    self,
                    {
                        "error":
                            "Session Telegram invalide."
                    },
                    401
                )

                return

            user_id = int(
                user_data["id"]
            )

            username = user_data.get(
                "username"
            )

            create_user(
                user_id,
                username
            )

            user = get_user(
                user_id
            )

            send_json(
                self,
                {
                    "user_id": user_id,
                    "username": username,
                    "balance": user["balance"]
                }
            )

            return

        # ====================================================
        # COMPLETE
        # ====================================================

        if path == "/api/complete":

            user_data = validate_init_data(
                data.get("initData")
            )

            if not user_data:

                send_json(
                    self,
                    {
                        "error":
                            "Session Telegram invalide."
                    },
                    401
                )

                return

            offer_id = data.get(
                "offer_id"
            )

            if not offer_id:

                send_json(
                    self,
                    {
                        "error": "Offre invalide."
                    },
                    400
                )

                return

            user_id = int(
                user_data["id"]
            )

            create_user(
                user_id,
                user_data.get("username")
            )

            conn = get_db()
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT *
                FROM offers
                WHERE id = ?
                """,
                (offer_id,)
            )

            offer = cursor.fetchone()

            if not offer:

                conn.close()

                send_json(
                    self,
                    {
                        "error":
                            "Cette offre n'existe pas."
                    },
                    404
                )

                return

            cursor.execute(
                """
                SELECT *
                FROM completed
                WHERE user_id = ?
                AND offer_id = ?
                """,
                (
                    user_id,
                    offer_id
                )
            )

            already_completed = cursor.fetchone()

            if already_completed:

                cursor.execute(
                    """
                    SELECT balance
                    FROM users
                    WHERE user_id = ?
                    """,
                    (user_id,)
                )

                user = cursor.fetchone()

                conn.close()

                send_json(
                    self,
                    {
                        "error":
                            "Récompense déjà récupérée.",
                        "balance":
                            user["balance"]
                    },
                    400
                )

                return

            cursor.execute(
                """
                UPDATE users
                SET balance = balance + ?
                WHERE user_id = ?
                """,
                (
                    offer["reward"],
                    user_id
                )
            )

            cursor.execute(
                """
                INSERT INTO completed (
                    user_id,
                    offer_id
                )
                VALUES (?, ?)
                """,
                (
                    user_id,
                    offer_id
                )
            )

            cursor.execute(
                """
                SELECT balance
                FROM users
                WHERE user_id = ?
                """,
                (user_id,)
            )

            user = cursor.fetchone()

            conn.commit()
            conn.close()

            send_json(
                self,
                {
                    "success": True,
                    "reward": offer["reward"],
                    "balance": user["balance"]
                }
            )

            return

        # ====================================================
        # RETRAIT
        # ====================================================

        if path == "/api/withdraw":

            user_data = validate_init_data(
                data.get("initData")
            )

            if not user_data:

                send_json(
                    self,
                    {
                        "error":
                            "Session Telegram invalide."
                    },
                    401
                )

                return

            try:

                amount = float(
                    data.get("amount")
                )

            except Exception:

                send_json(
                    self,
                    {
                        "error":
                            "Montant invalide."
                    },
                    400
                )

                return

            method = data.get(
                "method"
            )

            details = data.get(
                "details"
            )

            if amount < MIN_WITHDRAWAL:

                send_json(
                    self,
                    {
                        "error":
                            "Le retrait minimum est de 500 €."
                    },
                    400
                )

                return

            if not method:

                send_json(
                    self,
                    {
                        "error":
                            "Choisis une méthode de paiement."
                    },
                    400
                )

                return

            if not details:

                send_json(
                    self,
                    {
                        "error":
                            "Indique tes informations de paiement."
                    },
                    400
                )

                return

            user_id = int(
                user_data["id"]
            )

            create_user(
                user_id,
                user_data.get("username")
            )

            conn = get_db()
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT balance
                FROM users
                WHERE user_id = ?
                """,
                (user_id,)
            )

            user = cursor.fetchone()

            if not user:

                conn.close()

                send_json(
                    self,
                    {
                        "error":
                            "Utilisateur introuvable."
                    },
                    404
                )

                return

            balance = float(
                user["balance"]
            )

            if amount > balance:

                conn.close()

                send_json(
                    self,
                    {
                        "error":
                            "Solde insuffisant."
                    },
                    400
                )

                return

            cursor.execute(
                """
                UPDATE users
                SET balance = balance - ?
                WHERE user_id = ?
                """,
                (
                    amount,
                    user_id
                )
            )

            cursor.execute(
                """
                INSERT INTO withdrawals (
                    user_id,
                    amount,
                    method,
                    details,
                    status
                )
                VALUES (?, ?, ?, ?, 'pending')
                """,
                (
                    user_id,
                    amount,
                    method,
                    details
                )
            )

            cursor.execute(
                """
                SELECT balance
                FROM users
                WHERE user_id = ?
                """,
                (user_id,)
            )

            new_user = cursor.fetchone()

            conn.commit()
            conn.close()

            send_json(
                self,
                {
                    "success": True,
                    "balance":
                        new_user["balance"],
                    "message":
                        "Demande de retrait envoyée."
                }
            )

            return

        send_json(
            self,
            {
                "error": "Route introuvable"
            },
            404
        )


# ============================================================
# SERVEUR WEB
# ============================================================

def start_web_server():

    port = int(
        os.getenv(
            "PORT",
            "8080"
        )
    )

    server = HTTPServer(
        ("0.0.0.0", port),
        WebHandler
    )

    print(
        f"🌐 Serveur Web démarré sur le port {port}"
    )

    server.serve_forever()


# ============================================================
# BOT
# ============================================================

def main():

    init_db()

    web_thread = threading.Thread(
        target=start_web_server,
        daemon=True
    )

    web_thread.start()

    application = (
        Application.builder()
        .token(TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    application.add_handler(
        CommandHandler(
            "adminoffers",
            adminoffers
        )
    )

    application.add_handler(
        CommandHandler(
            "addoffer",
            addoffer
        )
    )

    print(
        "🤖 View Cash Bot démarré."
    )

    application.run_polling(
        drop_pending_updates=True
    )


# ============================================================
# DÉMARRAGE
# ============================================================

if __name__ == "__main__":
    main()
