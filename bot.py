import os
import json
import sqlite3
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)


# ============================================================
# CONFIGURATION
# ============================================================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))

PORT = int(os.environ.get("PORT", "8080"))

DATABASE = "bot.db"

WEBAPP_URL = "https://viewcashmoneybot-production.up.railway.app"

VIDEOS_PER_CYCLE = 20

COOLDOWN_SECONDS = 10 * 60 * 60

MIN_WITHDRAWAL = 500.0

# RÉCOMPENSE FIXE
REWARD_PER_VIDEO = 1.0


# ============================================================
# DATABASE
# ============================================================

db_lock = threading.Lock()


def get_db():

    connection = sqlite3.connect(
        DATABASE,
        check_same_thread=False
    )

    connection.row_factory = sqlite3.Row

    return connection


def init_db():

    with db_lock:

        db = get_db()

        cursor = db.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                balance REAL DEFAULT 0,
                completed_count INTEGER DEFAULT 0,
                cycle_started INTEGER DEFAULT 0,
                cooldown_until INTEGER DEFAULT 0,
                invited_by INTEGER DEFAULT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS offers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                type TEXT NOT NULL,
                url TEXT DEFAULT '',
                video_id TEXT DEFAULT '',
                reward REAL DEFAULT 1.0,
                active INTEGER DEFAULT 1
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS completions (
                user_id INTEGER NOT NULL,
                offer_id INTEGER NOT NULL,
                cycle INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY(user_id, offer_id, cycle)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                method TEXT NOT NULL,
                address TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at INTEGER NOT NULL
            )
        """)

        # Toutes les offres existantes passent à 1 €
        cursor.execute("""
            UPDATE offers
            SET reward = 1.0
        """)

        db.commit()

        db.close()


# ============================================================
# USERS
# ============================================================

def create_user(
    user_id,
    username="",
    first_name="",
    invited_by=None
):

    db = get_db()

    cursor = db.cursor()

    cursor.execute(
        "SELECT id FROM users WHERE id = ?",
        (user_id,)
    )

    existing = cursor.fetchone()

    if existing:

        cursor.execute("""
            UPDATE users
            SET username = ?,
                first_name = ?
            WHERE id = ?
        """, (
            username,
            first_name,
            user_id
        ))

        db.commit()

        db.close()

        return

    cursor.execute("""
        INSERT INTO users (
            id,
            username,
            first_name,
            balance,
            completed_count,
            cycle_started,
            cooldown_until,
            invited_by
        )
        VALUES (?, ?, ?, 0, 0, ?, 0, ?)
    """, (
        user_id,
        username,
        first_name,
        int(time.time()),
        invited_by
    ))

    db.commit()

    db.close()


def get_user(user_id):

    db = get_db()

    cursor = db.cursor()

    cursor.execute(
        "SELECT * FROM users WHERE id = ?",
        (user_id,)
    )

    user = cursor.fetchone()

    db.close()

    if not user:
        return None

    return dict(user)


def reset_cycle_if_needed(user):

    now = int(time.time())

    if user["completed_count"] < VIDEOS_PER_CYCLE:

        return user

    cooldown_until = user["cooldown_until"]

    if now >= cooldown_until:

        db = get_db()

        cursor = db.cursor()

        cursor.execute("""
            UPDATE users
            SET completed_count = 0,
                cycle_started = ?,
                cooldown_until = 0
            WHERE id = ?
        """, (
            now,
            user["id"]
        ))

        db.commit()

        db.close()

        return get_user(user["id"])

    return user


# ============================================================
# TELEGRAM INITDATA
# ============================================================

def get_user_from_initdata(init_data):

    if not init_data:
        return None

    try:

        params = parse_qs(init_data)

        user_data = params.get("user", [None])[0]

        if not user_data:
            return None

        user = json.loads(user_data)

        user_id = int(user["id"])

        return user_id

    except Exception:

        return None


# ============================================================
# OFFERS
# ============================================================

def get_available_offers(user_id):

    user = get_user(user_id)

    if not user:
        return []

    user = reset_cycle_if_needed(user)

    if user["completed_count"] >= VIDEOS_PER_CYCLE:

        return []

    cycle = user["cycle_started"]

    db = get_db()

    cursor = db.cursor()

    cursor.execute("""
        SELECT
            o.id,
            o.title,
            o.type,
            o.url,
            o.video_id,
            1.0 AS reward
        FROM offers o
        WHERE o.active = 1
        AND o.id NOT IN (
            SELECT offer_id
            FROM completions
            WHERE user_id = ?
            AND cycle = ?
        )
        ORDER BY o.id ASC
        LIMIT ?
    """, (
        user_id,
        cycle,
        VIDEOS_PER_CYCLE
    ))

    rows = cursor.fetchall()

    db.close()

    return [dict(row) for row in rows]


# ============================================================
# COMPLETE VIDEO
# ============================================================

def complete_offer(user_id, offer_id):

    db = get_db()

    cursor = db.cursor()

    cursor.execute(
        "SELECT * FROM users WHERE id = ?",
        (user_id,)
    )

    user = cursor.fetchone()

    if not user:

        db.close()

        return False, "Utilisateur introuvable.", None

    user = dict(user)

    user = reset_cycle_if_needed(user)

    if user["completed_count"] >= VIDEOS_PER_CYCLE:

        db.close()

        return False, "Tu dois attendre la fin du délai.", None

    cycle = user["cycle_started"]

    cursor.execute("""
        SELECT 1
        FROM completions
        WHERE user_id = ?
        AND offer_id = ?
        AND cycle = ?
    """, (
        user_id,
        offer_id,
        cycle
    ))

    already = cursor.fetchone()

    if already:

        db.close()

        return False, "Cette vidéo a déjà été récupérée.", None

    cursor.execute("""
        SELECT id
        FROM offers
        WHERE id = ?
        AND active = 1
    """, (
        offer_id,
    ))

    offer = cursor.fetchone()

    if not offer:

        db.close()

        return False, "Vidéo introuvable.", None

    # ========================================================
    # RÉCOMPENSE FIXE : EXACTEMENT 1 €
    # ========================================================

    reward = REWARD_PER_VIDEO

    cursor.execute("""
        INSERT INTO completions (
            user_id,
            offer_id,
            cycle,
            created_at
        )
        VALUES (?, ?, ?, ?)
    """, (
        user_id,
        offer_id,
        cycle,
        int(time.time())
    ))

    new_count = user["completed_count"] + 1

    new_balance = user["balance"] + reward

    cooldown_until = user["cooldown_until"]

    if new_count >= VIDEOS_PER_CYCLE:

        cooldown_until = (
            int(time.time()) +
            COOLDOWN_SECONDS
        )

    cursor.execute("""
        UPDATE users
        SET balance = ?,
            completed_count = ?,
            cooldown_until = ?
        WHERE id = ?
    """, (
        new_balance,
        new_count,
        cooldown_until,
        user_id
    ))

    db.commit()

    cursor.execute(
        "SELECT * FROM users WHERE id = ?",
        (user_id,)
    )

    updated = dict(cursor.fetchone())

    db.close()

    return True, "Récompense récupérée.", updated


# ============================================================
# WITHDRAW
# ============================================================

def create_withdrawal(
    user_id,
    amount,
    method,
    address
):

    db = get_db()

    cursor = db.cursor()

    cursor.execute(
        "SELECT * FROM users WHERE id = ?",
        (user_id,)
    )

    user = cursor.fetchone()

    if not user:

        db.close()

        return False, "Utilisateur introuvable.", None

    user = dict(user)

    if amount < MIN_WITHDRAWAL:

        db.close()

        return False, "Le retrait minimum est de 500 €.", None

    if amount > user["balance"]:

        db.close()

        return False, "Solde insuffisant.", None

    if not method or not address:

        db.close()

        return False, "Informations manquantes.", None

    cursor.execute("""
        INSERT INTO withdrawals (
            user_id,
            amount,
            method,
            address,
            status,
            created_at
        )
        VALUES (?, ?, ?, ?, 'pending', ?)
    """, (
        user_id,
        amount,
        method,
        address,
        int(time.time())
    ))

    new_balance = user["balance"] - amount

    cursor.execute("""
        UPDATE users
        SET balance = ?
        WHERE id = ?
    """, (
        new_balance,
        user_id
    ))

    db.commit()

    cursor.execute(
        "SELECT * FROM users WHERE id = ?",
        (user_id,)
    )

    updated = dict(cursor.fetchone())

    db.close()

    return True, "Demande envoyée.", updated


# ============================================================
# HTTP API
# ============================================================

class Handler(BaseHTTPRequestHandler):

    def send_json(self, data, status=200):

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

        self.end_headers()

        self.wfile.write(body)

    def read_json(self):

        length = int(
            self.headers.get(
                "Content-Length",
                "0"
            )
        )

        if length <= 0:

            return {}

        raw = self.rfile.read(length)

        return json.loads(
            raw.decode("utf-8")
        )

    def get_initdata(self):

        parsed = urlparse(
            self.path
        )

        query = parse_qs(
            parsed.query
        )

        return query.get(
            "initData",
            [""]
        )[0]

    def get_user_id(self):

        init_data = self.get_initdata()

        return get_user_from_initdata(
            init_data
        )

    def do_OPTIONS(self):

        self.send_response(200)

        self.send_header(
            "Access-Control-Allow-Origin",
            "*"
        )

        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, POST, OPTIONS"
        )

        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type"
        )

        self.end_headers()

    def do_GET(self):

        parsed = urlparse(
            self.path
        )

        path = parsed.path

        # ----------------------------------------------------
        # USER
        # ----------------------------------------------------

        if path == "/api/user":

            user_id = self.get_user_id()

            if not user_id:

                self.send_json({
                    "ok":False,
                    "error":"Utilisateur Telegram invalide."
                },401)

                return

            user = get_user(user_id)

            if not user:

                create_user(
                    user_id
                )

                user = get_user(
                    user_id
                )

            user = reset_cycle_if_needed(
                user
            )

            self.send_json({
                "ok":True,
                "user":user
            })

            return

        # ----------------------------------------------------
        # OFFERS
        # ----------------------------------------------------

        if path == "/api/offers":

            user_id = self.get_user_id()

            if not user_id:

                self.send_json({
                    "ok":False,
                    "error":"Utilisateur Telegram invalide."
                },401)

                return

            offers = get_available_offers(
                user_id
            )

            self.send_json({
                "ok":True,
                "offers":offers
            })

            return

        # ----------------------------------------------------
        # HEALTH
        # ----------------------------------------------------

        if path == "/health":

            self.send_json({
                "ok":True,
                "status":"online"
            })

            return

        self.send_json({
            "ok":False,
            "error":"Route introuvable."
        },404)

    def do_POST(self):

        parsed = urlparse(
            self.path
        )

        path = parsed.path

        try:

            data = self.read_json()

        except Exception:

            self.send_json({
                "ok":False,
                "error":"JSON invalide."
            },400)

            return

        init_data = data.get(
            "initData",
            ""
        )

        user_id = get_user_from_initdata(
            init_data
        )

        if not user_id:

            self.send_json({
                "ok":False,
                "error":"Utilisateur Telegram invalide."
            },401)

            return

        # ----------------------------------------------------
        # COMPLETE
        # ----------------------------------------------------

        if path == "/api/complete":

            offer_id = data.get(
                "offer_id"
            )

            try:

                offer_id = int(
                    offer_id
                )

            except Exception:

                self.send_json({
                    "ok":False,
                    "error":"Vidéo invalide."
                },400)

                return

            success, message, user = \
                complete_offer(
                    user_id,
                    offer_id
                )

            if not success:

                self.send_json({
                    "ok":False,
                    "error":message
                },400)

                return

            self.send_json({
                "ok":True,
                "reward":1.0,
                "message":message,
                "user":user
            })

            return

        # ----------------------------------------------------
        # WITHDRAW
        # ----------------------------------------------------

        if path == "/api/withdraw":

            try:

                amount = float(
                    data.get(
                        "amount",
                        0
                    )
                )

            except Exception:

                amount = 0

            method = str(
                data.get(
                    "method",
                    ""
                )
            ).strip()

            address = str(
                data.get(
                    "address",
                    ""
                )
            ).strip()

            success, message, user = \
                create_withdrawal(
                    user_id,
                    amount,
                    method,
                    address
                )

            if not success:

                self.send_json({
                    "ok":False,
                    "error":message
                },400)

                return

            self.send_json({
                "ok":True,
                "message":message,
                "user":user
            })

            return

        self.send_json({
            "ok":False,
            "error":"Route introuvable."
        },404)


# ============================================================
# TELEGRAM BOT
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    invited_by = None

    if context.args:

        try:

            invited_by = int(
                context.args[0]
            )

            if invited_by == user.id:

                invited_by = None

        except Exception:

            invited_by = None

    create_user(
        user.id,
        user.username or "",
        user.first_name or "",
        invited_by
    )

    keyboard = [[
        InlineKeyboardButton(
            "💰 OUVRIR VIEW CASH",
            web_app=WebAppInfo(
                url=WEBAPP_URL
            )
        )
    ]]

    await update.message.reply_text(
        "💰 Bienvenue sur View Cash !\n\n"
        "🎥 Regarde les vidéos.\n"
        "💶 1 € par vidéo terminée.\n"
        "⬆️ Glisse vers le haut après la vidéo.\n"
        "🎯 20 vidéos par cycle.\n"
        "⏳ 10 heures après un cycle complet.",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


# ============================================================
# ADMIN : AJOUTER UNE VIDÉO
# ============================================================

async def addoffer_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_user.id != ADMIN_ID:

        return

    if len(context.args) < 3:

        await update.message.reply_text(
            "Utilisation :\n"
            "/addoffer titre type url\n\n"
            "Type : mp4 ou tiktok"
        )

        return

    title = context.args[0]

    offer_type = context.args[1].lower()

    value = context.args[2]

    if offer_type not in (
        "mp4",
        "tiktok"
    ):

        await update.message.reply_text(
            "Type invalide. Utilise mp4 ou tiktok."
        )

        return

    db = get_db()

    cursor = db.cursor()

    if offer_type == "tiktok":

        cursor.execute("""
            INSERT INTO offers (
                title,
                type,
                url,
                video_id,
                reward,
                active
            )
            VALUES (?, 'tiktok', '', ?, 1.0, 1)
        """, (
            title,
            value
        ))

    else:

        cursor.execute("""
            INSERT INTO offers (
                title,
                type,
                url,
                video_id,
                reward,
                active
            )
            VALUES (?, 'mp4', ?, '', 1.0, 1)
        """, (
            title,
            value
        ))

    db.commit()

    db.close()

    await update.message.reply_text(
        "✅ Vidéo ajoutée.\n"
        "💰 Récompense : 1 €"
    )


# ============================================================
# ADMIN : LISTE VIDÉOS
# ============================================================

async def adminoffers_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_user.id != ADMIN_ID:

        return

    db = get_db()

    cursor = db.cursor()

    cursor.execute("""
        SELECT id,title,type,active
        FROM offers
        ORDER BY id
    """)

    rows = cursor.fetchall()

    db.close()

    if not rows:

        await update.message.reply_text(
            "Aucune vidéo."
        )

        return

    text = "🎥 VIDÉOS\n\n"

    for row in rows:

        text += (
            f"#{row['id']} - "
            f"{row['title']} - "
            f"{row['type']} - "
            f"{'ON' if row['active'] else 'OFF'}\n"
        )

    await update.message.reply_text(
        text
    )


# ============================================================
# SERVEUR
# ============================================================

def run_server():

    server = ThreadingHTTPServer(
        ("0.0.0.0", PORT),
        Handler
    )

    print(
        f"HTTP server running on port {PORT}"
    )

    server.serve_forever()


# ============================================================
# MAIN
# ============================================================

def main():

    if not TELEGRAM_TOKEN:

        raise RuntimeError(
            "TELEGRAM_TOKEN manquant."
        )

    init_db()

    server_thread = threading.Thread(
        target=run_server,
        daemon=True
    )

    server_thread.start()

    application = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start_command
        )
    )

    application.add_handler(
        CommandHandler(
            "addoffer",
            addoffer_command
        )
    )

    application.add_handler(
        CommandHandler(
            "adminoffers",
            adminoffers_command
        )
    )

    print(
        "View Cash bot started."
    )

    application.run_polling()


if __name__ == "__main__":

    main()
