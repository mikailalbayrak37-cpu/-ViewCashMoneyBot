import os
import json
import time
import hmac
import hashlib
import sqlite3
import threading

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, parse_qsl, unquote

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

VIDEOS_PER_CYCLE = 20

COOLDOWN_SECONDS = 10 * 60 * 60

MIN_WITHDRAWAL = 500.0


# ============================================================
# DATABASE
# ============================================================

def db():
    connection = sqlite3.connect(DB_FILE)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    connection = db()
    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            balance REAL DEFAULT 0,
            referral_id INTEGER DEFAULT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS offers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            reward REAL NOT NULL,
            video_url TEXT DEFAULT '',
            active INTEGER DEFAULT 1
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS completed (
            user_id INTEGER NOT NULL,
            offer_id INTEGER NOT NULL,
            cycle INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            PRIMARY KEY(user_id, offer_id, cycle)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_cycles (
            user_id INTEGER PRIMARY KEY,
            cycle INTEGER DEFAULT 1,
            started_at INTEGER DEFAULT 0,
            completed_count INTEGER DEFAULT 0
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            method TEXT NOT NULL,
            details TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at INTEGER NOT NULL
        )
    """)

    connection.commit()
    connection.close()


# ============================================================
# USERS
# ============================================================

def create_user(user_id, username=None, referral_id=None):
    connection = db()
    cursor = connection.cursor()

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

    else:
        cursor.execute(
            """
            INSERT INTO users(
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
                UPDATE users
                SET balance = balance + 0.10
                WHERE user_id = ?
                """,
                (referral_id,)
            )

    cursor.execute(
        """
        INSERT OR IGNORE INTO user_cycles(
            user_id,
            cycle,
            started_at,
            completed_count
        )
        VALUES (?, 1, ?, 0)
        """,
        (
            user_id,
            int(time.time())
        )
    )

    connection.commit()
    connection.close()


def get_user(user_id):
    connection = db()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT *
        FROM users
        WHERE user_id = ?
        """,
        (user_id,)
    )

    user = cursor.fetchone()

    connection.close()

    return user


# ============================================================
# CYCLES
# ============================================================

def get_cycle(user_id):
    connection = db()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT *
        FROM user_cycles
        WHERE user_id = ?
        """,
        (user_id,)
    )

    cycle = cursor.fetchone()

    connection.close()

    return cycle


def refresh_cycle_if_needed(user_id):

    connection = db()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            cycle,
            started_at,
            completed_count
        FROM user_cycles
        WHERE user_id = ?
        """,
        (user_id,)
    )

    row = cursor.fetchone()

    if not row:

        cursor.execute(
            """
            INSERT INTO user_cycles(
                user_id,
                cycle,
                started_at,
                completed_count
            )
            VALUES (?, 1, ?, 0)
            """,
            (
                user_id,
                int(time.time())
            )
        )

        connection.commit()
        connection.close()

        return

    now = int(time.time())

    if (
        row["completed_count"] >= VIDEOS_PER_CYCLE
        and now >= row["started_at"] + COOLDOWN_SECONDS
    ):

        cursor.execute(
            """
            UPDATE user_cycles
            SET
                cycle = cycle + 1,
                started_at = ?,
                completed_count = 0
            WHERE user_id = ?
            """,
            (
                now,
                user_id
            )
        )

        connection.commit()

    connection.close()


# ============================================================
# OFFERS
# ============================================================

def get_active_offers():

    connection = db()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            id,
            title,
            url,
            reward,
            video_url
        FROM offers
        WHERE active = 1
        ORDER BY id ASC
        """
    )

    offers = cursor.fetchall()

    connection.close()

    return offers


def get_available_offers(user_id):

    refresh_cycle_if_needed(user_id)

    cycle = get_cycle(user_id)

    if not cycle:
        return []

    current_cycle = cycle["cycle"]

    connection = db()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            id,
            title,
            url,
            reward,
            video_url
        FROM offers
        WHERE active = 1
        ORDER BY id ASC
        LIMIT ?
        """,
        (VIDEOS_PER_CYCLE,)
    )

    offers = cursor.fetchall()

    result = []

    for offer in offers:

        cursor.execute(
            """
            SELECT 1
            FROM completed
            WHERE user_id = ?
              AND offer_id = ?
              AND cycle = ?
            """,
            (
                user_id,
                offer["id"],
                current_cycle
            )
        )

        completed = cursor.fetchone()

        if not completed:
            result.append(dict(offer))

    connection.close()

    return result


# ============================================================
# TELEGRAM /START
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    referral_id = None

    if context.args:

        try:
            referral_id = int(context.args[0])
        except Exception:
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

    await update.message.reply_text(
        "💰 Bienvenue sur View Cash !\n\n"
        "🎬 Regarde les vidéos disponibles.\n"
        "🎁 Termine-les pour récupérer tes récompenses.\n\n"
        "Clique ci-dessous pour ouvrir l'application.",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


# ============================================================
# ADMIN : OFFRES
# ============================================================

async def adminoffers(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_user.id != ADMIN_ID:
        return

    offers = get_active_offers()

    if not offers:

        await update.message.reply_text(
            "❌ Aucune offre."
        )

        return

    message = "🎬 OFFRES ACTIVES\n\n"

    for offer in offers:

        message += (
            f"#{offer['id']} - {offer['title']}\n"
            f"💰 {offer['reward']:.2f} €\n"
            f"🎥 {offer['video_url']}\n\n"
        )

    await update.message.reply_text(
        message
    )


# ============================================================
# ADMIN : AJOUTER UNE OFFRE
# ============================================================

async def addoffer(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_user.id != ADMIN_ID:
        return

    args = context.args

    if len(args) < 3:

        await update.message.reply_text(
            "❌ Format incorrect.\n\n"
            "Utilise :\n"
            "/addoffer Titre URL Récompense VIDEO\n\n"
            "Exemple :\n"
            "/addoffer MaVideo https://google.com 0.10 https://www.tiktok.com/@user/video/123"
        )

        return

    reward_index = None

    for i, value in enumerate(args):

        try:
            float(
                value.replace(",", ".")
            )

            reward_index = i

            break

        except Exception:
            pass

    if reward_index is None or reward_index < 2:

        await update.message.reply_text(
            "❌ Impossible de trouver le montant."
        )

        return

    title = " ".join(
        args[:reward_index - 1]
    )

    url = args[reward_index - 1]

    try:

        reward = float(
            args[reward_index].replace(",", ".")
        )

    except Exception:

        await update.message.reply_text(
            "❌ Récompense invalide."
        )

        return

    video_url = ""

    if len(args) > reward_index + 1:

        video_url = args[
            reward_index + 1
        ]

    connection = db()
    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO offers(
            title,
            url,
            reward,
            video_url,
            active
        )
        VALUES (?, ?, ?, ?, 1)
        """,
        (
            title,
            url,
            reward,
            video_url
        )
    )

    connection.commit()

    offer_id = cursor.lastrowid

    connection.close()

    await update.message.reply_text(
        f"✅ Offre ajoutée !\n\n"
        f"ID : {offer_id}\n"
        f"🎬 {title}\n"
        f"💰 {reward:.2f} €\n"
        f"🎥 {video_url or 'Aucune vidéo'}"
    )


# ============================================================
# TELEGRAM WEBAPP VALIDATION
# ============================================================

def validate_telegram_data(init_data):

    if not init_data:
        print("Telegram validation: initData vide")
        return None

    if not TOKEN:
        print("Telegram validation: TELEGRAM_TOKEN manquant")
        return None

    try:

        # Telegram WebApp envoie une query string.
        parsed_data = dict(
            parse_qsl(
                init_data,
                keep_blank_values=True
            )
        )

        received_hash = parsed_data.pop(
            "hash",
            None
        )

        if not received_hash:

            print(
                "Telegram validation: hash absent"
            )

            return None

        data_check_string = "\n".join(
            f"{key}={parsed_data[key]}"
            for key in sorted(parsed_data.keys())
        )

        secret_key = hmac.new(
            b"WebAppData",
            TOKEN.encode("utf-8"),
            hashlib.sha256
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(
            calculated_hash,
            received_hash
        ):

            print(
                "Telegram validation: hash invalide"
            )

            return None

        user_string = parsed_data.get(
            "user"
        )

        if not user_string:

            print(
                "Telegram validation: user absent"
            )

            return None

        user_data = json.loads(
            user_string
        )

        if "id" not in user_data:

            print(
                "Telegram validation: ID utilisateur absent"
            )

            return None

        print(
            "Telegram utilisateur validé:",
            user_data["id"]
        )

        return user_data

    except Exception as e:

        print(
            "Erreur validation Telegram:",
            repr(e)
        )

        return None


# ============================================================
# HTTP SERVER
# ============================================================

class WebHandler(BaseHTTPRequestHandler):

    def send_json(
        self,
        status,
        data
    ):

        body = json.dumps(
            data,
            ensure_ascii=False
        ).encode("utf-8")

        self.send_response(status)

        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
        )

        self.send_header(
            "Content-Length",
            str(len(body))
        )

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

        self.wfile.write(body)

    def read_json(self):

        try:

            length = int(
                self.headers.get(
                    "Content-Length",
                    0
                )
            )

            raw = self.rfile.read(
                length
            )

            return json.loads(
                raw.decode("utf-8")
            )

        except Exception:

            return {}

    def get_user_from_request(
        self,
        data
    ):

        init_data = data.get(
            "initData",
            ""
        )

        user_data = validate_telegram_data(
            init_data
        )

        if not user_data:
            return None

        return user_data

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

    # ========================================================
    # GET
    # ========================================================

    def do_GET(self):

        parsed = urlparse(
            self.path
        )

        # ----------------------------------------------------
        # PAGE
        # ----------------------------------------------------

        if parsed.path == "/":

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

                self.wfile.write(
                    content
                )

            except Exception as e:

                print(
                    "Erreur page:",
                    repr(e)
                )

                self.send_json(
                    500,
                    {
                        "error":
                        "Impossible de charger l'application"
                    }
                )

            return

        # ----------------------------------------------------
        # OFFRES
        # ----------------------------------------------------

        if parsed.path == "/api/offers":

            params = parse_qs(
                parsed.query
            )

            init_data = params.get(
                "initData",
                [""]
            )[0]

            # Si initData a été envoyé non encodé,
            # on tente également de récupérer la valeur brute.
            if not init_data:

                raw_query = parsed.query

                if "initData=" in raw_query:

                    init_data = raw_query.split(
                        "initData=",
                        1
                    )[1]

                    init_data = unquote(
                        init_data
                    )

            user_data = validate_telegram_data(
                init_data
            )

            if not user_data:

                self.send_json(
                    401,
                    {
                        "error":
                        "Utilisateur Telegram invalide"
                    }
                )

                return

            try:

                user_id = int(
                    user_data["id"]
                )

            except Exception:

                self.send_json(
                    401,
                    {
                        "error":
                        "ID Telegram invalide"
                    }
                )

                return

            create_user(
                user_id,
                user_data.get(
                    "username"
                )
            )

            refresh_cycle_if_needed(
                user_id
            )

            cycle = get_cycle(
                user_id
            )

            offers = get_available_offers(
                user_id
            )

            now = int(
                time.time()
            )

            cooldown = 0

            if cycle:

                if (
                    cycle["completed_count"]
                    >= VIDEOS_PER_CYCLE
                ):

                    cooldown = max(
                        0,
                        cycle["started_at"]
                        + COOLDOWN_SECONDS
                        - now
                    )

            self.send_json(
                200,
                {
                    "offers": offers,
                    "cycle":
                        cycle["cycle"]
                        if cycle
                        else 1,
                    "completed":
                        cycle["completed_count"]
                        if cycle
                        else 0,
                    "total":
                        VIDEOS_PER_CYCLE,
                    "cooldown":
                        cooldown
                }
            )

            return

        self.send_json(
            404,
            {
                "error":
                "Route introuvable"
            }
        )

    # ========================================================
    # POST
    # ========================================================

    def do_POST(self):

        parsed = urlparse(
            self.path
        )

        data = self.read_json()

        user_data = self.get_user_from_request(
            data
        )

        if not user_data:

            self.send_json(
                401,
                {
                    "error":
                    "Session Telegram invalide"
                }
            )

            return

        try:

            user_id = int(
                user_data["id"]
            )

        except Exception:

            self.send_json(
                401,
                {
                    "error":
                    "ID Telegram invalide"
                }
            )

            return

        username = user_data.get(
            "username"
        )

        create_user(
            user_id,
            username
        )

        # ====================================================
        # USER
        # ====================================================

        if parsed.path == "/api/user":

            user = get_user(
                user_id
            )

            cycle = get_cycle(
                user_id
            )

            self.send_json(
                200,
                {
                    "id": user_id,
                    "username": username,
                    "balance":
                        user["balance"],
                    "cycle":
                        cycle["cycle"]
                        if cycle
                        else 1,
                    "completed":
                        cycle["completed_count"]
                        if cycle
                        else 0,
                    "total":
                        VIDEOS_PER_CYCLE
                }
            )

            return

        # ====================================================
        # COMPLETE
        # ====================================================

        if parsed.path == "/api/complete":

            try:

                offer_id = int(
                    data.get(
                        "offer_id"
                    )
                )

            except Exception:

                self.send_json(
                    400,
                    {
                        "error":
                        "Offre invalide"
                    }
                )

                return

            refresh_cycle_if_needed(
                user_id
            )

            cycle = get_cycle(
                user_id
            )

            if not cycle:

                self.send_json(
                    400,
                    {
                        "error":
                        "Cycle introuvable"
                    }
                )

                return

            current_cycle = cycle[
                "cycle"
            ]

            if (
                cycle["completed_count"]
                >= VIDEOS_PER_CYCLE
            ):

                self.send_json(
                    400,
                    {
                        "error":
                        "Les 20 vidéos sont terminées. Attends le prochain cycle."
                    }
                )

                return

            connection = db()
            cursor = connection.cursor()

            cursor.execute(
                """
                SELECT *
                FROM offers
                WHERE id = ?
                  AND active = 1
                """,
                (offer_id,)
            )

            offer = cursor.fetchone()

            if not offer:

                connection.close()

                self.send_json(
                    404,
                    {
                        "error":
                        "Offre introuvable"
                    }
                )

                return

            cursor.execute(
                """
                SELECT 1
                FROM completed
                WHERE user_id = ?
                  AND offer_id = ?
                  AND cycle = ?
                """,
                (
                    user_id,
                    offer_id,
                    current_cycle
                )
            )

            already = cursor.fetchone()

            if already:

                connection.close()

                self.send_json(
                    400,
                    {
                        "error":
                        "Cette vidéo est déjà terminée."
                    }
                )

                return

            now = int(
                time.time()
            )

            cursor.execute(
                """
                INSERT INTO completed(
                    user_id,
                    offer_id,
                    cycle,
                    created_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    user_id,
                    offer_id,
                    current_cycle,
                    now
                )
            )

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
                UPDATE user_cycles
                SET completed_count =
                    completed_count + 1
                WHERE user_id = ?
                """,
                (user_id,)
            )

            connection.commit()

            cursor.execute(
                """
                SELECT balance
                FROM users
                WHERE user_id = ?
                """,
                (user_id,)
            )

            balance = cursor.fetchone()[
                "balance"
            ]

            cursor.execute(
                """
                SELECT completed_count
                FROM user_cycles
                WHERE user_id = ?
                """,
                (user_id,)
            )

            completed_count = cursor.fetchone()[
                "completed_count"
            ]

            connection.close()

            self.send_json(
                200,
                {
                    "success": True,
                    "reward":
                        offer["reward"],
                    "balance":
                        balance,
                    "completed":
                        completed_count,
                    "total":
                        VIDEOS_PER_CYCLE
                }
            )

            return

        # ====================================================
        # WITHDRAW
        # ====================================================

        if parsed.path == "/api/withdraw":

            try:

                amount = float(
                    data.get(
                        "amount"
                    )
                )

            except Exception:

                self.send_json(
                    400,
                    {
                        "error":
                        "Montant invalide"
                    }
                )

                return

            method = str(
                data.get(
                    "method",
                    ""
                )
            ).strip()

            details = str(
                data.get(
                    "details",
                    ""
                )
            ).strip()

            if amount < MIN_WITHDRAWAL:

                self.send_json(
                    400,
                    {
                        "error":
                        f"Le retrait minimum est de {MIN_WITHDRAWAL:.0f} €."
                    }
                )

                return

            if not method or not details:

                self.send_json(
                    400,
                    {
                        "error":
                        "Informations de retrait manquantes."
                    }
                )

                return

            connection = db()
            cursor = connection.cursor()

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

                connection.close()

                self.send_json(
                    400,
                    {
                        "error":
                        "Utilisateur introuvable."
                    }
                )

                return

            if user["balance"] < amount:

                connection.close()

                self.send_json(
                    400,
                    {
                        "error":
                        "Solde insuffisant."
                    }
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
                INSERT INTO withdrawals(
                    user_id,
                    amount,
                    method,
                    details,
                    status,
                    created_at
                )
                VALUES (?, ?, ?, ?, 'pending', ?)
                """,
                (
                    user_id,
                    amount,
                    method,
                    details,
                    int(time.time())
                )
            )

            connection.commit()
            connection.close()

            self.send_json(
                200,
                {
                    "success": True,
                    "message":
                    "Demande de retrait envoyée."
                }
            )

            return

        self.send_json(
            404,
            {
                "error":
                "Route introuvable"
            }
        )


# ============================================================
# SERVER
# ============================================================

def run_server():

    port = int(
        os.getenv(
            "PORT",
            "8080"
        )
    )

    server = ThreadingHTTPServer(
        (
            "0.0.0.0",
            port
        ),
        WebHandler
    )

    print(
        f"Web server running on port {port}"
    )

    server.serve_forever()


# ============================================================
# MAIN
# ============================================================

def main():

    if not TOKEN:

        raise RuntimeError(
            "TELEGRAM_TOKEN manquant dans Railway."
        )

    init_db()

    threading.Thread(
        target=run_server,
        daemon=True
    ).start()

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
        "ViewCash bot started."
    )

    application.run_polling()


if __name__ == "__main__":
    main()
