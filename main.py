#RED LUCKY XYZ

from __future__ import annotations

import asyncio
import threading
from flask import Flask, request
import html
import json
import logging
import os
import random
import re
import secrets
import string
import time
from datetime import datetime, timedelta

import aiosqlite
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, RetryAfter, TelegramError
from telegram.request import HTTPXRequest
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)


def _load_token() -> str:
    token = os.environ.get("BOT_TOKEN")
    if token:
        return token.strip()
    token_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "token.txt")
    if os.path.exists(token_file):
        with open(token_file, "r", encoding="utf-8") as f:
            t = f.read().strip()
            if t:
                return t
    raise RuntimeError(
        "No bot token found. Set BOT_TOKEN env var, or create a token.txt "
        "file next to main.py containing your bot token."
    )


BOT_TOKEN = _load_token()
OWNER_ID = int(os.environ.get("OWNER_ID", "8313599433"))
BOT_USERNAME = os.environ.get("BOT_USERNAME", "RedLuckyXyzStore_bot")
BOT_VERSION = "v1.0"

DB_PATH = os.path.join("/tmp" if os.environ.get("VERCEL") else os.path.dirname(os.path.abspath(__file__)), "database.db")

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("RedLuckyXyzStore_bot")

DEFAULT_SETTINGS = {
    "bot_name": "RED LUCKY XYZ STORE",
    "referral_reward": "20",
    "daily_bonus": "10",
    "premium_daily_bonus_multiplier": "2",
    "premium_daily_bonus": "40",
    "support_username": "Redluckyxyz",
    "payment_username": "Redluckyxyz",
    "force_join": "1",
    "maintenance": "0",
    "star_rate": "30",
    "coin_packages": json.dumps([
        {"coins": 100, "stars": 3},
        {"coins": 500, "stars": 15},
        {"coins": 1000, "stars": 28},
        {"coins": 2500, "stars": 65},
        {"coins": 5000, "stars": 120},
    ]),
    "premium_pricing": json.dumps([
        {"days": 7, "price": 300},
        {"days": 30, "price": 900},
        {"days": 90, "price": 2200},
        {"days": 0, "price": 6000},
    ]),
    "coin_rate_bdt": "5",
    "bdt_payment_details": "PhonePe (Send Money): 91XXXXXXXXXX\nGooglePay (Send Money): 91XXXXXXXXXX",
    "spin_settings": json.dumps({
        "chance_coins_low": 50,
        "chance_coins_high": 25,
        "chance_file": 10,
        "chance_premium": 15,
        "coins_low_min": 20,
        "coins_low_max": 140,
        "coins_high_min": 150,
        "coins_high_max": 300,
        "premium_days": 7,
    }),
    "mystery_box_pricing": json.dumps([
        {"tier": "random", "label": "🎁 Random File", "price": 100},
        {"tier": "rare", "label": "💎 Rare File", "price": 500},
        {"tier": "premium", "label": "👑 Premium Project", "price": 1000},
    ]),
    "referral_milestones": json.dumps([
        {"count": 5, "reward": 100},
        {"count": 10, "reward": 250},
        {"count": 25, "reward": 700},
        {"count": 50, "reward": 1500},
    ]),
    "streak_rewards": json.dumps([
        {"days": 3, "reward": 30},
        {"days": 7, "reward": 100},
        {"days": 14, "reward": 250},
        {"days": 30, "reward": 600},
    ]),
}

FILE_TYPE_ICON = {
    "document": "📄", "video": "🎬", "audio": "🎵", "photo": "🖼",
    "voice": "🎙", "animation": "🎞", "video_note": "🎥",
}

USE_CUSTOM_EMOJI = False
CUSTOM_EMOJI: dict[str, str] = {
}

DIVIDER = "───────────────────"


_db_lock = asyncio.Lock()


DUPLICATE_COOLDOWN_SECONDS = 0.6
FLOOD_WINDOW_SECONDS = 3.0
FLOOD_MAX_ACTIONS = 12

_last_action_by_key: dict[tuple[int, str], float] = {}
_recent_action_times: dict[int, list[float]] = {}

_user_action_locks: dict[int, asyncio.Lock] = {}
_user_action_locks_guard = asyncio.Lock()


async def get_user_action_lock(user_id: int) -> asyncio.Lock:
    async with _user_action_locks_guard:
        lock = _user_action_locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            _user_action_locks[user_id] = lock
        return lock


async def group_restriction_guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat is None or chat.type == "private":
        return

    if update.callback_query:
        is_bot_trigger = True
    else:
        msg = update.message
        if msg is None or not msg.text:
            return
        is_bot_trigger = msg.text.startswith("/") or msg.text in TEXT_ROUTES

    if not is_bot_trigger:
        return

    try:
        await update.effective_message.reply_text("📩 Please Use this bot in Bot DM.")
    except TelegramError:
        pass
    raise ApplicationHandlerStop


async def flood_guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return

    if update.callback_query:
        action_id = update.callback_query.data or ""
    elif update.message and update.message.text:
        action_id = update.message.text
    else:
        return

    now = time.monotonic()

    key = (user.id, action_id)
    last_same = _last_action_by_key.get(key, 0.0)
    _last_action_by_key[key] = now
    if now - last_same < DUPLICATE_COOLDOWN_SECONDS:
        if update.callback_query:
            try:
                await update.callback_query.answer()
            except TelegramError:
                pass
        raise ApplicationHandlerStop

    history = _recent_action_times.setdefault(user.id, [])
    history[:] = [t for t in history if now - t < FLOOD_WINDOW_SECONDS]
    history.append(now)
    if len(history) > FLOOD_MAX_ACTIONS:
        if update.callback_query:
            try:
                await update.callback_query.answer("⏳ Please slow down a little.", show_alert=False)
            except TelegramError:
                pass
        raise ApplicationHandlerStop


class RateLimiter:
    def __init__(self, rate: float):
        self.interval = 1.0 / rate
        self._lock = asyncio.Lock()
        self._next_time = time.monotonic()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._next_time - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = time.monotonic()
            self._next_time = now + self.interval


TELEGRAM_SEND_LIMITER = RateLimiter(25)

DB_POOL_SIZE = int(os.environ.get("DB_POOL_SIZE", "4" if os.environ.get("VERCEL") else "64"))
_db_pool: "asyncio.Queue[aiosqlite.Connection]" = None


async def init_db_pool() -> None:
    global _db_pool
    _db_pool = asyncio.Queue(maxsize=DB_POOL_SIZE)
    for _ in range(DB_POOL_SIZE):
        conn = await aiosqlite.connect(DB_PATH)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode = WAL;")
        await conn.execute("PRAGMA synchronous = NORMAL;")
        await conn.execute("PRAGMA busy_timeout = 8000;")
        await conn.execute("PRAGMA foreign_keys = ON;")
        await _db_pool.put(conn)
    log.info("DB connection pool ready (%d connections)", DB_POOL_SIZE)


async def close_db_pool() -> None:
    if _db_pool is None:
        return
    while not _db_pool.empty():
        conn = await _db_pool.get()
        await conn.close()


class _PooledConnection:
    """Async context manager — borrows a connection from the shared pool
    instead of opening a new one. Drop-in replacement for
    `aiosqlite.connect(DB_PATH)` at every call site."""

    async def __aenter__(self) -> aiosqlite.Connection:
        self.conn = await _db_pool.get()
        return self.conn

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await _db_pool.put(self.conn)


def db_conn() -> _PooledConnection:
    return _PooledConnection()


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                join_date TEXT,
                is_banned INTEGER DEFAULT 0,
                referred_by INTEGER,
                last_bonus_time TEXT,
                last_seen TEXT
            );

            CREATE TABLE IF NOT EXISTS files (
                file_pk INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_file_id TEXT NOT NULL,
                tg_file_unique_id TEXT,
                file_kind TEXT NOT NULL,
                file_name TEXT NOT NULL,
                file_size INTEGER DEFAULT 0,
                description TEXT DEFAULT '',
                price INTEGER DEFAULT 0,
                upload_date TEXT,
                downloads_count INTEGER DEFAULT 0,
                uploaded_by INTEGER,
                premium_only INTEGER DEFAULT 0,
                is_deleted INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS wallet (
                user_id INTEGER PRIMARY KEY,
                coins INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS transactions (
                tx_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                tx_type TEXT,
                amount INTEGER,
                description TEXT,
                timestamp TEXT
            );

            CREATE TABLE IF NOT EXISTS payments (
                payment_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                stars INTEGER,
                coins INTEGER,
                screenshot_file_id TEXT,
                status TEXT DEFAULT 'pending',
                admin_id INTEGER,
                reason TEXT,
                timestamp TEXT,
                payment_method TEXT DEFAULT 'stars',
                amount_bdt INTEGER
            );

            CREATE TABLE IF NOT EXISTS redeems (
                code TEXT PRIMARY KEY,
                coin_reward INTEGER DEFAULT 0,
                premium_days INTEGER DEFAULT 0,
                usage_limit INTEGER DEFAULT 0,
                used_count INTEGER DEFAULT 0,
                expiry_date TEXT,
                status TEXT DEFAULT 'active',
                created_by INTEGER,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS redeem_uses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT,
                user_id INTEGER,
                timestamp TEXT,
                UNIQUE(code, user_id)
            );

            CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER,
                new_user_id INTEGER UNIQUE,
                date TEXT,
                reward INTEGER
            );

            CREATE TABLE IF NOT EXISTS force_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE,
                chat_id INTEGER,
                title TEXT,
                invite_link TEXT,
                chat_type TEXT,
                added_date TEXT
            );

            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                added_date TEXT
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                file_pk INTEGER,
                timestamp TEXT
            );

            CREATE TABLE IF NOT EXISTS premium (
                user_id INTEGER PRIMARY KEY,
                expiry_date TEXT,
                is_lifetime INTEGER DEFAULT 0,
                granted_by INTEGER,
                granted_at TEXT
            );

            CREATE TABLE IF NOT EXISTS purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                file_pk INTEGER,
                price INTEGER,
                timestamp TEXT
            );

            CREATE TABLE IF NOT EXISTS tickets (
                ticket_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                subject TEXT,
                body TEXT,
                status TEXT DEFAULT 'open',
                admin_id INTEGER,
                created_at TEXT,
                closed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS admin_logs (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_id INTEGER,
                action_text TEXT,
                timestamp TEXT
            );

            CREATE TABLE IF NOT EXISTS file_requests (
                request_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                request_text TEXT,
                status TEXT DEFAULT 'open',
                timestamp TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_files_deleted ON files(is_deleted);
            CREATE INDEX IF NOT EXISTS idx_tx_user ON transactions(user_id);
            CREATE INDEX IF NOT EXISTS idx_purchases_user_file ON purchases(user_id, file_pk);
            CREATE INDEX IF NOT EXISTS idx_downloads_user ON downloads(user_id);
            """
        )
        await db.commit()

        migrations = [
            ("files", "premium_only", "INTEGER DEFAULT 0"),
            ("payments", "payment_method", "TEXT DEFAULT 'stars'"),
            ("payments", "amount_bdt", "INTEGER"),
            ("redeems", "source", "TEXT DEFAULT 'admin'"),
            ("admins", "role", "TEXT DEFAULT 'junior'"),
            ("users", "login_streak", "INTEGER DEFAULT 0"),
            ("users", "last_login_date", "TEXT"),
            ("users", "referral_milestone_reached", "INTEGER DEFAULT 0"),
            ("force_channels", "chat_type", "TEXT"),
            ("force_channels", "invite_link", "TEXT"),
            ("users", "spin_count_today", "INTEGER DEFAULT 0"),
            ("users", "spin_reset_date", "TEXT"),
            ("files", "mystery_tier", "TEXT"),
            ("files", "featured", "INTEGER DEFAULT 0"),
        ]
        for table, column, coltype in migrations:
            try:
                await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
                await db.commit()
                log.info("Migrated: added %s.%s", table, column)
            except aiosqlite.OperationalError:
                pass

        for k, v in DEFAULT_SETTINGS.items():
            await db.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v)
            )
        await db.execute(
            "INSERT OR IGNORE INTO admins (user_id, added_date) VALUES (?, ?)",
            (OWNER_ID, datetime.utcnow().isoformat()),
        )
        await db.commit()
    log.info("Database ready at %s", DB_PATH)



async def get_setting(key: str, default: str = "") -> str:
    async with db_conn() as db:
        cur = await db.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = await cur.fetchone()
        return row[0] if row else default


async def set_setting(key: str, value: str) -> None:
    async with db_conn() as db:
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        await db.commit()



async def ensure_user(user_id: int, username: str | None, first_name: str | None,
                       referred_by: int | None = None) -> bool:
    """Returns True if this is a newly created user."""
    async with db_conn() as db:
        cur = await db.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,))
        exists = await cur.fetchone()
        now = datetime.utcnow().isoformat()
        if exists:
            await db.execute(
                "UPDATE users SET username=?, first_name=?, last_seen=? WHERE user_id=?",
                (username, first_name, now, user_id),
            )
            await db.commit()
            return False
        await db.execute(
            "INSERT INTO users (user_id, username, first_name, join_date, referred_by, last_seen) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, username, first_name, now, referred_by, now),
        )
        await db.execute(
            "INSERT OR IGNORE INTO wallet (user_id, coins) VALUES (?, 0)", (user_id,)
        )
        await db.commit()
        return True


async def is_admin(user_id: int) -> bool:
    if user_id == OWNER_ID:
        return True
    async with db_conn() as db:
        cur = await db.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,))
        return (await cur.fetchone()) is not None


async def is_banned(user_id: int) -> bool:
    async with db_conn() as db:
        cur = await db.execute("SELECT is_banned FROM users WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        return bool(row and row[0])


async def get_user_row(user_id: int):
    async with db_conn() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        return await cur.fetchone()



async def get_balance(user_id: int) -> int:
    async with db_conn() as db:
        cur = await db.execute("SELECT coins FROM wallet WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        if row is None:
            await db.execute("INSERT OR IGNORE INTO wallet (user_id, coins) VALUES (?, 0)", (user_id,))
            await db.commit()
            return 0
        return row[0]


async def add_coins(user_id: int, amount: int, tx_type: str, description: str = "") -> int:
    async with _db_lock:
        async with db_conn() as db:
            await db.execute(
                "INSERT INTO wallet (user_id, coins) VALUES (?, 0) "
                "ON CONFLICT(user_id) DO NOTHING", (user_id,)
            )
            await db.execute(
                "UPDATE wallet SET coins = coins + ? WHERE user_id=?", (amount, user_id)
            )
            await db.execute(
                "INSERT INTO transactions (user_id, tx_type, amount, description, timestamp) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, tx_type, amount, description, datetime.utcnow().isoformat()),
            )
            await db.commit()
            cur = await db.execute("SELECT coins FROM wallet WHERE user_id=?", (user_id,))
            row = await cur.fetchone()
            return row[0]


async def remove_coins(user_id: int, amount: int, tx_type: str, description: str = "") -> bool:
    """Returns False if insufficient balance."""
    async with _db_lock:
        async with db_conn() as db:
            cur = await db.execute("SELECT coins FROM wallet WHERE user_id=?", (user_id,))
            row = await cur.fetchone()
            bal = row[0] if row else 0
            if bal < amount:
                return False
            await db.execute(
                "UPDATE wallet SET coins = coins - ? WHERE user_id=?", (amount, user_id)
            )
            await db.execute(
                "INSERT INTO transactions (user_id, tx_type, amount, description, timestamp) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, tx_type, -amount, description, datetime.utcnow().isoformat()),
            )
            await db.commit()
            return True



async def get_premium(user_id: int):
    async with db_conn() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM premium WHERE user_id=?", (user_id,))
        return await cur.fetchone()


async def is_premium_active(user_id: int) -> bool:
    row = await get_premium(user_id)
    if not row:
        return False
    if row["is_lifetime"]:
        return True
    if row["expiry_date"] and datetime.fromisoformat(row["expiry_date"]) > datetime.utcnow():
        return True
    return False


async def grant_premium(user_id: int, days: int | None, admin_id: int) -> None:
    async with db_conn() as db:
        if days is None:
            await db.execute(
                "INSERT INTO premium (user_id, expiry_date, is_lifetime, granted_by, granted_at) "
                "VALUES (?, NULL, 1, ?, ?) ON CONFLICT(user_id) DO UPDATE SET "
                "expiry_date=NULL, is_lifetime=1, granted_by=excluded.granted_by, granted_at=excluded.granted_at",
                (user_id, admin_id, datetime.utcnow().isoformat()),
            )
        else:
            expiry = (datetime.utcnow() + timedelta(days=days)).isoformat()
            await db.execute(
                "INSERT INTO premium (user_id, expiry_date, is_lifetime, granted_by, granted_at) "
                "VALUES (?, ?, 0, ?, ?) ON CONFLICT(user_id) DO UPDATE SET "
                "expiry_date=excluded.expiry_date, is_lifetime=0, granted_by=excluded.granted_by, granted_at=excluded.granted_at",
                (user_id, expiry, admin_id, datetime.utcnow().isoformat()),
            )
        await db.commit()


async def remove_premium(user_id: int) -> None:
    async with db_conn() as db:
        await db.execute("DELETE FROM premium WHERE user_id=?", (user_id,))
        await db.commit()



def user_reply_keyboard(admin: bool) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton("📁 Buy Files", style="primary"), KeyboardButton("💎 My Wallet", style="primary")],
        [KeyboardButton("🎁 Daily Bonus", style="primary"), KeyboardButton("🎰 Daily Spin", style="success")],
        [KeyboardButton("🎁 Mystery Box", style="success"), KeyboardButton("🚀 Invite Friends", style="primary")],
        [KeyboardButton("🎟️ Redeem Code", style="primary"), KeyboardButton("📊 My Stats", style="primary")],
        [KeyboardButton("🏆 Leaderboard", style="primary"), KeyboardButton("🎫 Support Ticket", style="primary")],
        [KeyboardButton("💬 Support", style="primary")],
    ]
    if admin:
        rows.append([KeyboardButton("👑 Admin Panel", style="primary")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def admin_reply_keyboard(is_owner: bool = True) -> ReplyKeyboardMarkup:
    """Full admin control keyboard — replaces the user keyboard entirely while
    the admin is inside the Admin Panel. Senior/Junior admins get a reduced
    set — Force Channels and Settings are Owner-only sections. Requires
    python-telegram-bot >= 22.7 (Bot API 9.4+) for `style` to render as
    colored buttons in Telegram; see requirements.txt."""
    admin_mgr_label = "🛡 Admin Manager" if is_owner else "📋 Admin List"
    buttons = ["📤 Upload File", "📂 Manage Files", "💰 Wallet Manager", "🎟 Redeem Manager"]
    if is_owner:
        buttons += ["📡 Force Channels"]
    buttons += ["📢 Broadcast", "👥 User Manager", "👤 All Users", "👑 Premium Manager", "📊 Statistics", "🛠 Maintenance"]
    if is_owner:
        buttons += ["⚙ Settings"]
    buttons += [admin_mgr_label]

    rows = [
        [KeyboardButton(buttons[i], style="primary")] + (
            [KeyboardButton(buttons[i + 1], style="primary")] if i + 1 < len(buttons) else []
        )
        for i in range(0, len(buttons), 2)
    ]
    rows.append([KeyboardButton("⬅️ Back to Main", style="primary")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def admin_panel_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📤 Upload File", callback_data="ad:upload", style="primary"),
         InlineKeyboardButton("📂 Manage Files", callback_data="ad:managefiles", style="primary")],
        [InlineKeyboardButton("💰 Wallet Manager", callback_data="ad:walletmgr", style="primary"),
         InlineKeyboardButton("🎟 Redeem Manager", callback_data="ad:redeemmgr", style="primary")],
        [InlineKeyboardButton("📡 Force Channels", callback_data="ad:fcmgr", style="primary"),
         InlineKeyboardButton("📢 Broadcast", callback_data="ad:broadcast", style="primary")],
        [InlineKeyboardButton("👥 User Manager", callback_data="ad:usermgr", style="primary"),
         InlineKeyboardButton("👑 Premium Manager", callback_data="ad:premiummgr", style="primary")],
        [InlineKeyboardButton("📊 Statistics", callback_data="ad:stats", style="primary"),
         InlineKeyboardButton("🛠 Maintenance", callback_data="ad:maintenance", style="primary")],
        [InlineKeyboardButton("⚙ Settings", callback_data="ad:settings", style="primary"),
         InlineKeyboardButton("🛡 Admin Manager", callback_data="ad:adminmgr", style="primary")],
    ]
    return InlineKeyboardMarkup(rows)


def back_button(cb: str = "ad:home") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅ Back", callback_data=cb, style="primary")]])



async def list_force_channels():
    async with db_conn() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM force_channels ORDER BY id")
        return await cur.fetchall()


async def check_force_join(bot, user_id: int) -> list[dict]:
    """Returns list of channels the user has NOT joined."""
    if (await get_setting("force_join", "1")) != "1":
        return []
    channels = await list_force_channels()
    missing = []
    for ch in channels:
        try:
            member = await bot.get_chat_member(chat_id=ch["chat_id"], user_id=user_id)
            if member.status in ("left", "kicked"):
                missing.append(dict(ch))
        except TelegramError:
            missing.append(dict(ch))
    return missing


async def send_force_join_prompt(update: Update, missing: list[dict]) -> None:
    buttons = []
    for ch in missing:
        if ch.get("username"):
            url = f"https://t.me/{ch['username'].lstrip('@')}"
        elif ch.get("invite_link"):
            url = ch["invite_link"]
        else:
            continue
        buttons.append([InlineKeyboardButton(f"📢 {ch['title']}", url=url, style="primary")])
    buttons.append([InlineKeyboardButton("✅ Verify", callback_data="verify_join", style="primary")])
    text = (
        "🔒 <b>Access Restricted</b>\n\n"
        "To use this bot, please join all required channels below, "
        "then tap <b>✅ Verify</b>."
    )
    if update.callback_query:
        await update.callback_query.message.reply_text(
            text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.HTML
        )
    else:
        await update.effective_message.reply_text(
            text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.HTML
        )



(UP_FILE, UP_NAME, UP_DESC, UP_PRICE, UP_PREVIEW) = range(5)
(BC_PKG, BC_STARS, BC_SCREENSHOT) = range(10, 13)
(RD_CODE,) = range(20, 21)
(FC_ADD,) = range(30, 31)
(UM_SEARCH, UM_AMOUNT) = range(40, 42)
(RM_CODE, RM_COINS, RM_PREMIUM, RM_LIMIT, RM_EXPIRY, RM_DELETE) = range(50, 56)
(BR_CONTENT,) = range(60, 61)
(ST_VALUE,) = range(70, 71)
(EF_FIELD,) = range(80, 81)
(WB_IDS, WB_ACTION, WB_AMOUNT, WB_PREMIUM) = range(90, 94)
(BDT_AMOUNT, BDT_PROOF) = range(94, 96)
(GC_AMOUNT,) = range(96, 97)
(AM_ADD_USER, AM_ADD_ROLE) = range(97, 99)
(TK_SUBJECT, TK_BODY, TK_REPLY, TK_USER_REPLY) = range(99, 103)
(FR_TEXT,) = range(103, 104)
(MF_SEARCH,) = range(104, 105)

ADMIN_ONLY_MSG = "🚫 This action is for admins only."
OWNER_ONLY_MSG = "🚫 This is restricted to the Owner only. Senior/Junior admins don't have access to this section."
ADMIN_MAX_REDEEM_COINS = 300
ADMIN_MIN_REDEEM_COINS = 20
ADMIN_DAILY_REDEEM_LIMIT = 2


async def require_owner(update: Update) -> bool:
    """Gate for Owner-only sections (Settings, Force Channels, Payment approval,
    Bulk User Manage, Manage Files list). Senior and Junior admins are both
    blocked — only OWNER_ID passes. Sends a denial message/alert and returns
    False if the caller isn't the owner."""
    if update.effective_user.id == OWNER_ID:
        return True
    if update.callback_query:
        await update.callback_query.answer(OWNER_ONLY_MSG, show_alert=True)
    else:
        await update.effective_message.reply_text(OWNER_ONLY_MSG)
    return False


def fmt_size(num: int) -> str:
    step = 1024.0
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if num < step:
            return f"{num:.1f} {unit}" if unit != "B" else f"{int(num)} {unit}"
        num /= step
    return f"{num:.1f} PB"


def fmt_date(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso).strftime("%d %b %Y, %H:%M")
    except Exception:
        return iso



async def _update_login_streak(bot, user_id: int) -> None:
    """Increments a user's consecutive-day login streak (resets if a day was
    missed), and grants a one-time bonus whenever a configured streak
    milestone is newly reached. Cheap no-op on repeat calls within the same
    day since guard_user runs on every interaction."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    async with db_conn() as db:
        cur = await db.execute("SELECT login_streak, last_login_date FROM users WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        if not row:
            return
        streak, last_date = row[0] or 0, row[1]
        if last_date == today:
            return

        yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
        new_streak = streak + 1 if last_date == yesterday else 1
        await db.execute(
            "UPDATE users SET login_streak=?, last_login_date=? WHERE user_id=?", (new_streak, today, user_id)
        )
        await db.commit()

    rewards = json.loads(await get_setting("streak_rewards", "[]"))
    for r in rewards:
        if r["days"] == new_streak:
            await add_coins(user_id, r["reward"], "login_streak", f"{new_streak}-day login streak bonus")
            try:
                await bot.send_message(
                    user_id,
                    f"🔥 <b>{new_streak}-Day Login Streak!</b>\n\n🪙 Bonus: <b>+{r['reward']} Coins</b>\n\nKeep it going!",
                    parse_mode=ParseMode.HTML,
                )
            except TelegramError:
                pass
            break


async def guard_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Returns True if the update should proceed normally."""
    user = update.effective_user
    if user is None:
        return False

    await ensure_user(user.id, user.username, user.first_name)
    await _update_login_streak(context.bot, user.id)

    admin = await is_admin(user.id)

    maintenance = (await get_setting("maintenance", "0")) == "1"
    if maintenance and not admin:
        await update.effective_message.reply_text(
            "🚧 <b>Bot is under maintenance.</b>\nPlease try again later.",
            parse_mode=ParseMode.HTML,
        )
        return False

    if await is_banned(user.id):
        await update.effective_message.reply_text(
            "🚫 You have been banned from using this bot. Contact support if you think this is a mistake."
        )
        return False

    if not admin:
        missing = await check_force_join(context.bot, user.id)
        if missing:
            await send_force_join_prompt(update, missing)
            return False

    return True



async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    args = context.args

    maintenance = (await get_setting("maintenance", "0")) == "1"
    admin = await is_admin(user.id)
    if maintenance and not admin:
        await update.effective_message.reply_text(
            "🚧 <b>Bot is under maintenance.</b>\nPlease try again later.",
            parse_mode=ParseMode.HTML,
        )
        return

    if await is_banned(user.id):
        await update.effective_message.reply_text("🚫 You have been banned from using this bot.")
        return

    referred_by = None
    if args and args[0].isdigit():
        ref_id = int(args[0])
        if ref_id != user.id:
            existing = await get_user_row(user.id)
            if existing is None:
                referred_by = ref_id

    is_new = await ensure_user(user.id, user.username, user.first_name, referred_by)

    if not admin:
        missing = await check_force_join(context.bot, user.id)
        if missing:
            await send_force_join_prompt(update, missing)
            return

    await maybe_process_referral(context.bot, user.id)

    await show_main_menu(update, context)


async def maybe_process_referral(bot, user_id: int) -> None:
    """Grants the referral reward once the referred user actually reaches the
    bot (i.e. after passing force-join, or immediately if none is required).
    Safe to call repeatedly — process_referral() is idempotent."""
    row = await get_user_row(user_id)
    if row and row["referred_by"]:
        await process_referral(bot, row["referred_by"], user_id)


async def process_referral(bot, referrer_id: int, new_user_id: int) -> None:
    async with db_conn() as db:
        cur = await db.execute("SELECT 1 FROM users WHERE user_id=?", (referrer_id,))
        if not await cur.fetchone():
            return
        cur = await db.execute("SELECT 1 FROM referrals WHERE new_user_id=?", (new_user_id,))
        if await cur.fetchone():
            return
        reward = int(await get_setting("referral_reward", "20"))
        await db.execute(
            "INSERT INTO referrals (referrer_id, new_user_id, date, reward) VALUES (?, ?, ?, ?)",
            (referrer_id, new_user_id, datetime.utcnow().isoformat(), reward),
        )
        await db.commit()

    new_balance = await add_coins(referrer_id, reward, "referral", f"Referral bonus for inviting user {new_user_id}")

    new_user_row = await get_user_row(new_user_id)
    if new_user_row and new_user_row["username"]:
        uname = f"@{new_user_row['username']}"
    elif new_user_row and new_user_row["first_name"]:
        uname = new_user_row["first_name"]
    else:
        uname = f"User {new_user_id}"

    try:
        await bot.send_message(
            referrer_id,
            "🎉 <b>New Referral Alert!</b> 🎉\n\n"
            f"👤 <b>User:</b> {html.escape(uname)}\n"
            f"🪙 <b>You earned:</b> +{reward} coins\n"
            f"💰 <b>Your new balance:</b> {new_balance} coins",
            parse_mode=ParseMode.HTML,
        )
    except TelegramError:
        pass

    await _check_referral_milestone(bot, referrer_id)


async def _check_referral_milestone(bot, referrer_id: int) -> None:
    """Grants a one-time bonus the first time a referrer's total count crosses
    a configured milestone (e.g. 5, 10, 25, 50 referrals)."""
    async with db_conn() as db:
        cur = await db.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id=?", (referrer_id,))
        total = (await cur.fetchone())[0]
        cur = await db.execute("SELECT referral_milestone_reached FROM users WHERE user_id=?", (referrer_id,))
        row = await cur.fetchone()
        already_reached = row[0] if row and row[0] else 0

    milestones = json.loads(await get_setting("referral_milestones", "[]"))
    for m in sorted(milestones, key=lambda x: x["count"]):
        if total >= m["count"] > already_reached:
            await add_coins(referrer_id, m["reward"], "referral_milestone", f"Referral milestone: {m['count']} referrals")
            async with db_conn() as db:
                await db.execute("UPDATE users SET referral_milestone_reached=? WHERE user_id=?", (m["count"], referrer_id))
                await db.commit()
            try:
                await bot.send_message(
                    referrer_id,
                    f"🏆 <b>Referral Milestone Reached!</b>\n\n"
                    f"🚀 You've referred <b>{m['count']}</b> people!\n"
                    f"🪙 Bonus: <b>+{m['reward']} Coins</b>",
                    parse_mode=ParseMode.HTML,
                )
            except TelegramError:
                pass
            break


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    admin = await is_admin(user.id)
    bot_name = await get_setting("bot_name", "RED LUCKY XYZ STORE")
    balance = await get_balance(user.id)

    async with db_conn() as db:
        cur = await db.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id=?", (user.id,))
        ref_count = (await cur.fetchone())[0]

    is_premium = await is_premium_active(user.id)
    premium_line = "👑 <b>Premium — Active</b>" if is_premium else "⭐ Premium: <i>Not Active</i>"
    name_tag = f"👑 {html.escape(user.first_name or 'Friend')}" if is_premium else html.escape(user.first_name or "Friend")

    text = (
        f"👋 <b>Welcome, {name_tag}!</b>\n\n"
        f"🏷 <b>{html.escape(bot_name)}</b> <i>({BOT_VERSION})</i>\n"
        f"{DIVIDER}\n"
        f"🪙 <b>Wallet:</b> {balance} Coins\n"
        f"👥 <b>Referrals:</b> {ref_count}\n"
        f"{premium_line}\n"
        f"{DIVIDER}\n\n"
        f"Use the menu below to get started 👇"
    )
    await update.effective_message.reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=user_reply_keyboard(admin)
    )


async def cb_verify_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    missing = await check_force_join(context.bot, user.id)
    if missing:
        await query.answer("❌ You haven't joined all channels yet.", show_alert=True)
        return
    await query.answer("✅ Verified!")
    try:
        await query.message.delete()
    except BadRequest:
        pass
    await ensure_user(user.id, user.username, user.first_name)
    await maybe_process_referral(context.bot, user.id)
    await show_main_menu(update, context)



async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard_user(update, context):
        return
    text = (
        "📖 <b>Help & Guide</b>\n\n"
        "📁 <b>Buy Files</b> — Browse and purchase files with coins.\n"
        "🪙 <b>Coins</b> — Buy coins with Telegram Stars, or earn them free via "
        "Daily Bonus, Referrals, and Redeem Codes.\n"
        "👥 <b>Referral</b> — Share your personal link, earn coins per new user.\n"
        "🎟️ <b>Redeem</b> — Enter a code from the admin to instantly get coins.\n"
        "💬 <b>Support</b> — Reach out any time via the Support menu.\n"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


async def handle_support(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard_user(update, context):
        return
    support_username = await get_setting("support_username", "Redluckyxyz")
    text = (
        "💬 <b>Support</b>\n\n"
        f"👤 Contact: @{html.escape(support_username)}\n"
        "🕒 Business Hours: 10:00 AM – 10:00 PM (IST)\n\n"
        "We usually reply within a few hours."
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)



async def handle_support_ticket_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await guard_user(update, context):
        return ConversationHandler.END
    await update.effective_message.reply_text(
        "🎫 <b>Open a Support Ticket</b>\n\nWhat's the subject? (short, one line):",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="ticket:cancel", style="primary")]]),
    )
    return TK_SUBJECT


async def cb_ticket_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("❌ Cancelled.")
    context.user_data.pop("tk_subject", None)
    return ConversationHandler.END


async def conv_ticket_subject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    subject = update.message.text.strip()
    if len(subject) > 100:
        await update.message.reply_text("❌ Please keep the subject under 100 characters.")
        return TK_SUBJECT
    context.user_data["tk_subject"] = subject
    await update.message.reply_text("📝 Now describe your issue in detail:")
    return TK_BODY


async def conv_ticket_body(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    body = update.message.text.strip()
    subject = context.user_data.pop("tk_subject", "")
    user = update.effective_user

    async with db_conn() as db:
        await db.execute(
            "INSERT INTO tickets (user_id, subject, body, status, created_at) VALUES (?, ?, ?, 'open', ?)",
            (user.id, subject, body, datetime.utcnow().isoformat()),
        )
        await db.commit()
        cur = await db.execute("SELECT last_insert_rowid()")
        ticket_id = (await cur.fetchone())[0]

    await update.message.reply_text(
        f"✅ <b>Ticket #{ticket_id} Submitted!</b>\n\nOur team will get back to you soon.",
        parse_mode=ParseMode.HTML,
    )

    uname = f"@{user.username}" if user.username else "—"
    notify = (
        f"🎫 <b>New Support Ticket #{ticket_id}</b>\n\n"
        f"👤 From: {html.escape(uname)}\n🆔 User ID: <code>{user.id}</code>\n"
        f"📌 Subject: {html.escape(subject)}\n\n📝 {html.escape(body)}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Reply", callback_data=f"ticket:reply:{ticket_id}", style="primary"),
         InlineKeyboardButton("✅ Close", callback_data=f"ticket:close:{ticket_id}", style="primary")],
    ])
    for admin_id in await get_all_admin_ids():
        try:
            await context.bot.send_message(admin_id, notify, parse_mode=ParseMode.HTML, reply_markup=kb)
        except TelegramError:
            pass
    return ConversationHandler.END


async def cb_ticket_reply_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not await is_admin(update.effective_user.id):
        await query.answer(ADMIN_ONLY_MSG, show_alert=True)
        return ConversationHandler.END
    await query.answer()
    ticket_id = int(query.data.split(":")[2])
    context.user_data["tk_reply_id"] = ticket_id
    await query.message.reply_text("💬 Type your reply — it will be sent to the user:")
    return TK_REPLY


async def conv_ticket_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ticket_id = context.user_data.pop("tk_reply_id", None)
    if ticket_id is None:
        await update.message.reply_text("❌ Something went wrong — please try again.")
        return ConversationHandler.END
    reply_text = update.message.text.strip()

    async with db_conn() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,))
        ticket = await cur.fetchone()

    if not ticket:
        await update.message.reply_text("❌ Ticket not found.")
        return ConversationHandler.END
    if ticket["status"] != "open":
        await update.message.reply_text("🚫 This ticket is already closed.")
        return ConversationHandler.END

    user_kb = InlineKeyboardMarkup([[InlineKeyboardButton("💬 Reply", callback_data=f"ticket:ureply:{ticket_id}", style="primary")]])
    try:
        await context.bot.send_message(
            ticket["user_id"],
            f"🎫 <b>Support Reply (Ticket #{ticket_id})</b>\n\n{html.escape(reply_text)}",
            parse_mode=ParseMode.HTML,
            reply_markup=user_kb,
        )
        await update.message.reply_text("✅ Reply sent to the user.")
    except TelegramError:
        await update.message.reply_text("⚠️ Couldn't deliver the reply — the user may have blocked the bot.")

    await log_admin_action(context.bot, update.effective_user.id, f"💬 Replied to ticket #{ticket_id}")
    return ConversationHandler.END


async def cb_ticket_user_reply_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    ticket_id = int(query.data.split(":")[2])
    async with db_conn() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,))
        ticket = await cur.fetchone()

    if not ticket:
        await query.answer("❌ Ticket not found.", show_alert=True)
        return ConversationHandler.END
    if ticket["user_id"] != update.effective_user.id:
        await query.answer("🚫 This isn't your ticket.", show_alert=True)
        return ConversationHandler.END
    if ticket["status"] != "open":
        await query.answer("🚫 This ticket is already closed.", show_alert=True)
        return ConversationHandler.END

    await query.answer()
    context.user_data["tk_user_reply_id"] = ticket_id
    await query.message.reply_text(
        "💬 Type your reply:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="ticket:cancel", style="primary")]]),
    )
    return TK_USER_REPLY


async def conv_ticket_user_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ticket_id = context.user_data.pop("tk_user_reply_id", None)
    if ticket_id is None:
        await update.message.reply_text("❌ Something went wrong — please try again.")
        return ConversationHandler.END
    reply_text = update.message.text.strip()
    user = update.effective_user

    async with db_conn() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,))
        ticket = await cur.fetchone()

    if not ticket:
        await update.message.reply_text("❌ Ticket not found.")
        return ConversationHandler.END
    if ticket["status"] != "open":
        await update.message.reply_text("🚫 This ticket is already closed.")
        return ConversationHandler.END

    await update.message.reply_text("✅ Reply sent to support.")

    uname = f"@{user.username}" if user.username else "—"
    notify = (
        f"🎫 <b>Ticket #{ticket_id} — User Reply</b>\n\n"
        f"👤 From: {html.escape(uname)}\n🆔 User ID: <code>{user.id}</code>\n\n💬 {html.escape(reply_text)}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Reply", callback_data=f"ticket:reply:{ticket_id}", style="primary"),
         InlineKeyboardButton("✅ Close", callback_data=f"ticket:close:{ticket_id}", style="primary")],
    ])
    for admin_id in await get_all_admin_ids():
        try:
            await context.bot.send_message(admin_id, notify, parse_mode=ParseMode.HTML, reply_markup=kb)
        except TelegramError:
            pass
    return ConversationHandler.END


async def cb_ticket_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not await is_admin(update.effective_user.id):
        await query.answer(ADMIN_ONLY_MSG, show_alert=True)
        return
    ticket_id = int(query.data.split(":")[2])
    async with db_conn() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,))
        ticket = await cur.fetchone()
        if not ticket:
            await query.answer("❌ Not found.", show_alert=True)
            return
        await db.execute(
            "UPDATE tickets SET status='closed', admin_id=?, closed_at=? WHERE ticket_id=?",
            (update.effective_user.id, datetime.utcnow().isoformat(), ticket_id),
        )
        await db.commit()
    await query.answer("✅ Closed")
    try:
        await query.message.delete()
    except TelegramError:
        pass
    try:
        await context.bot.send_message(
            ticket["user_id"], f"✅ <b>Your ticket #{ticket_id} has been closed.</b>", parse_mode=ParseMode.HTML
        )
    except TelegramError:
        pass
    await log_admin_action(context.bot, update.effective_user.id, f"✅ Closed ticket #{ticket_id}")



async def handle_file_request_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await guard_user(update, context):
        return ConversationHandler.END
    await update.effective_message.reply_text(
        "📥 <b>Request a File</b>\n\nTell us what file/project you're looking for — we'll try to add it:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="freq:cancel", style="primary")]]),
    )
    return FR_TEXT


async def cb_file_request_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END


async def conv_file_request_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    user = update.effective_user

    async with db_conn() as db:
        await db.execute(
            "INSERT INTO file_requests (user_id, request_text, status, timestamp) VALUES (?, ?, 'open', ?)",
            (user.id, text, datetime.utcnow().isoformat()),
        )
        await db.commit()
        cur = await db.execute("SELECT last_insert_rowid()")
        req_id = (await cur.fetchone())[0]

    await update.message.reply_text("✅ <b>Request Submitted!</b>\n\nThanks — we'll review it soon.", parse_mode=ParseMode.HTML)

    uname = f"@{user.username}" if user.username else "—"
    notify = (
        f"📥 <b>New File Request #{req_id}</b>\n\n"
        f"👤 From: {html.escape(uname)}\n🆔 User ID: <code>{user.id}</code>\n\n📝 {html.escape(text)}"
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Mark Fulfilled", callback_data=f"freq:done:{req_id}", style="primary")]])
    for admin_id in await get_all_admin_ids():
        try:
            await context.bot.send_message(admin_id, notify, parse_mode=ParseMode.HTML, reply_markup=kb)
        except TelegramError:
            pass
    return ConversationHandler.END


async def cb_file_request_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not await is_admin(update.effective_user.id):
        await query.answer(ADMIN_ONLY_MSG, show_alert=True)
        return
    req_id = int(query.data.split(":")[2])
    async with db_conn() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM file_requests WHERE request_id=?", (req_id,))
        req = await cur.fetchone()
        if not req:
            await query.answer("❌ Not found.", show_alert=True)
            return
        await db.execute("UPDATE file_requests SET status='fulfilled' WHERE request_id=?", (req_id,))
        await db.commit()
    await query.answer("✅ Marked fulfilled")
    try:
        await query.message.delete()
    except TelegramError:
        pass
    try:
        await context.bot.send_message(
            req["user_id"],
            f"✅ <b>Your file request has been fulfilled!</b>\n\n📝 {html.escape(req['request_text'])}\n\nCheck 📁 Buy Files!",
            parse_mode=ParseMode.HTML,
        )
    except TelegramError:
        pass
    await log_admin_action(context.bot, update.effective_user.id, f"✅ Fulfilled file request #{req_id}")


async def handle_my_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard_user(update, context):
        return
    await send_wallet_view(update, context)


async def send_wallet_view(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    user = update.effective_user
    balance = await get_balance(user.id)
    async with db_conn() as db:
        cur = await db.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id=?", (user.id,))
        ref_count = (await cur.fetchone())[0]
    daily_status = await bonus_status_text(user.id)
    is_premium = await is_premium_active(user.id)
    premium_line = "👑 <b>Premium — Active</b>" if is_premium else "⭐ Premium: <i>Not Active</i>"

    text = (
        "💎 <b>My Wallet</b>\n"
        f"{DIVIDER}\n"
        f"🪙 <b>Coins:</b> {balance}\n"
        f"👥 <b>Total Referrals:</b> {ref_count}\n"
        f"🎁 <b>Daily Bonus:</b> {daily_status}\n"
        f"{premium_line}\n"
        f"{DIVIDER}"
    )
    buttons = [
        [InlineKeyboardButton("⭐ Buy Coins (Stars)", callback_data="wallet:buycoins", style="success"),
         InlineKeyboardButton("💵 Buy Coins (₹Rs)", callback_data="wallet:buybdt", style="success")],
        [InlineKeyboardButton("📜 Transactions", callback_data="wallet:tx:0", style="primary")],
    ]
    if not is_premium:
        buttons.append([InlineKeyboardButton("👑 Buy Premium with Coins", callback_data="wallet:buypremium", style="primary")])
    kb = InlineKeyboardMarkup(buttons)
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def cb_wallet_buy_premium(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if await is_premium_active(update.effective_user.id):
        await query.answer("✅ You already have Premium active.", show_alert=True)
        return
    pricing = json.loads(await get_setting("premium_pricing", "[]"))
    balance = await get_balance(update.effective_user.id)
    lines = [f"👑 <b>Buy Premium with Coins</b>\n{DIVIDER}", f"🪙 Your balance: <b>{balance}</b>\n"]
    buttons = []
    for p in pricing:
        label = "Lifetime" if p["days"] == 0 else f"{p['days']} Days"
        lines.append(f"• {label} — 🪙 {p['price']}")
        buttons.append([InlineKeyboardButton(f"👑 {label} — {p['price']} coins", callback_data=f"premium:buy:{p['days']}:{p['price']}", style="primary")])
    buttons.append([InlineKeyboardButton("⬅ Back", callback_data="wallet:home", style="primary")])
    await query.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))


async def cb_premium_purchase(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    _, _, days, price = query.data.split(":")
    days, price = int(days), int(price)

    lock = await get_user_action_lock(user.id)
    async with lock:
        if await is_premium_active(user.id):
            await query.answer("✅ You already have Premium active.", show_alert=True)
            return
        ok = await remove_coins(user.id, price, "premium_purchase", f"Bought Premium ({'Lifetime' if days == 0 else f'{days} days'})")
        if not ok:
            await query.answer("❌ Not enough Coins for this plan.", show_alert=True)
            return
        await grant_premium(user.id, None if days == 0 else days, admin_id=0)

    await query.answer("👑 Premium activated!")
    label = "Lifetime" if days == 0 else f"{days} Days"
    await query.edit_message_text(
        f"🎉 <b>Premium Activated!</b>\n\n👑 Plan: <b>{label}</b>\n🪙 Paid: <b>{price} coins</b>\n\n"
        "Enjoy your Premium badge, bonus multiplier, and priority support!",
        parse_mode=ParseMode.HTML,
    )


async def bonus_status_text(user_id: int) -> str:
    row = await get_user_row(user_id)
    if row and row["last_bonus_time"]:
        last = datetime.fromisoformat(row["last_bonus_time"])
        elapsed = datetime.utcnow() - last
        if elapsed < timedelta(hours=24):
            remaining = timedelta(hours=24) - elapsed
            h, rem = divmod(int(remaining.total_seconds()), 3600)
            m = rem // 60
            return f"⏳ Available in {h}h {m}m"
    return "✅ Available now"


async def cb_wallet_tx(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    page = int(query.data.split(":")[2])
    per_page = 8
    async with db_conn() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM transactions WHERE user_id=? ORDER BY tx_id DESC LIMIT ? OFFSET ?",
            (user.id, per_page, page * per_page),
        )
        rows = await cur.fetchall()

    if not rows and page == 0:
        text = "📜 <b>Transactions</b>\n\nNo transactions yet."
    else:
        lines = ["📜 <b>Transactions</b>\n"]
        for r in rows:
            sign = "+" if r["amount"] >= 0 else ""
            lines.append(
                f"{'🟢' if r['amount']>=0 else '🔴'} {sign}{r['amount']} 🪙 — {html.escape(r['tx_type'])} "
                f"<i>({fmt_date(r['timestamp'])})</i>"
            )
        text = "\n".join(lines)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅ Prev", callback_data=f"wallet:tx:{page-1}", style="primary"))
    if len(rows) == per_page:
        nav.append(InlineKeyboardButton("Next ➡", callback_data=f"wallet:tx:{page+1}", style="primary"))
    buttons = [nav] if nav else []
    buttons.append([InlineKeyboardButton("⬅ Back", callback_data="wallet:home", style="primary")])
    await query.answer()
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))


async def cb_wallet_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    await send_wallet_view(update, context, edit=True)



async def cb_buycoins_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    packages = json.loads(await get_setting("coin_packages", "[]"))
    payment_username = await get_setting("payment_username", "Redluckyxyz")
    lines = ["⭐ <b>Buy Coins — Manual Stars Payment</b>\n"]
    for i, p in enumerate(packages):
        lines.append(f"🪙 {p['coins']} Coins = ⭐ {p['stars']} Stars")
    lines.append(f"\nSend Stars To: <b>@{html.escape(payment_username)}</b>")
    lines.append("\nAfter sending, tap <b>⭐ I Have Paid</b> below.")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⭐ I Have Paid", callback_data="buycoins:paid", style="primary")],
        [InlineKeyboardButton("⬅ Back", callback_data="wallet:home", style="primary")],
    ])
    await query.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=kb)
    return ConversationHandler.END


async def cb_buycoins_paid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "⭐ How many Stars did you send? (numbers only)",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="buycoins:cancel", style="primary")]]),
    )
    return BC_STARS


async def conv_buycoins_stars(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("❌ Please send a valid number of Stars.")
        return BC_STARS
    context.user_data["bc_stars"] = int(text)
    await update.message.reply_text("📷 Please send your payment screenshot now.")
    return BC_SCREENSHOT


async def conv_buycoins_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.photo and not update.message.document:
        await update.message.reply_text("❌ Please send a screenshot as a photo or document.")
        return BC_SCREENSHOT

    file_id = update.message.photo[-1].file_id if update.message.photo else update.message.document.file_id
    stars = context.user_data.pop("bc_stars")
    user = update.effective_user

    async with db_conn() as db:
        cur = await db.execute(
            "SELECT 1 FROM payments WHERE user_id=? AND status='pending'", (user.id,)
        )
        if await cur.fetchone():
            await update.message.reply_text(
                "⚠️ You already have a pending payment request. Please wait for admin review."
            )
            return ConversationHandler.END

        packages = json.loads(await get_setting("coin_packages", "[]"))
        coins = 0
        for p in packages:
            if p["stars"] == stars:
                coins = p["coins"]
                break
        if coins == 0:
            rate = float(await get_setting("star_rate", "30"))
            coins = int(stars * rate / 30 * 100)

        await db.execute(
            "INSERT INTO payments (user_id, stars, coins, screenshot_file_id, status, timestamp) "
            "VALUES (?, ?, ?, ?, 'pending', ?)",
            (user.id, stars, coins, file_id, datetime.utcnow().isoformat()),
        )
        await db.commit()
        cur = await db.execute("SELECT last_insert_rowid()")
        payment_id = (await cur.fetchone())[0]

    await update.message.reply_text(
        "✅ Your payment request has been submitted. You'll be notified once reviewed."
    )

    uname = f"@{user.username}" if user.username else "—"
    caption = (
        "💰 <b>New Payment Request</b>\n\n"
        f"👤 Username: {html.escape(uname)}\n"
        f"🆔 User ID: <code>{user.id}</code>\n"
        f"⭐ Stars: <b>{stars}</b>\n"
        f"🪙 Coins: <b>{coins}</b>\n"
        f"📅 Time: {fmt_date(datetime.utcnow().isoformat())}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Accept", callback_data=f"pay:accept:{payment_id}", style="primary"),
         InlineKeyboardButton("❌ Reject", callback_data=f"pay:reject:{payment_id}", style="primary")]
    ])
    admin_ids = [OWNER_ID]
    for admin_id in admin_ids:
        try:
            if update.message.photo:
                await context.bot.send_photo(admin_id, file_id, caption=caption, parse_mode=ParseMode.HTML, reply_markup=kb)
            else:
                await context.bot.send_document(admin_id, file_id, caption=caption, parse_mode=ParseMode.HTML, reply_markup=kb)
        except TelegramError:
            pass
    return ConversationHandler.END


async def cb_buycoins_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("❌ Cancelled.")
    context.user_data.pop("bc_stars", None)
    return ConversationHandler.END



async def handle_buy_coins_bdt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.callback_query:
        await update.callback_query.answer()
    if not await guard_user(update, context):
        return
    rate = await get_setting("coin_rate_bdt", "5")
    details = await get_setting("bdt_payment_details", "")
    text = (
        "💵 <b>Buy Coins with IND (₹Rs)</b>\n"
        f"{DIVIDER}\n"
        f"💱 Rate: <b>1 ₹Rs = {html.escape(rate)} Coins</b>\n\n"
        f"{html.escape(details)}\n\n"
        "Send the amount via PhonePe/GooglePay/Paytm (Send Money), then tap <b>💵 I Have Paid</b> below."
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("💵 I Have Paid", callback_data="buybdt:paid", style="primary")]])
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def cb_buybdt_paid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "💵 How many IND (₹Rs) did you send? (numbers only)",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="buybdt:cancel", style="primary")]]),
    )
    return BDT_AMOUNT


async def conv_buybdt_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("❌ Please send a valid IND amount.")
        return BDT_AMOUNT
    context.user_data["bdt_amount"] = int(text)
    await update.message.reply_text(
        "📷 Please send your payment screenshot now — or type your PhonePe/GooglePay/Paytm Transaction ID as text."
    )
    return BDT_PROOF


async def conv_buybdt_proof(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    amount = context.user_data.pop("bdt_amount", None)
    if amount is None:
        await update.message.reply_text("❌ Something went wrong — please start again from 💵 Buy Coins (₹Rs).")
        return ConversationHandler.END

    file_id = None
    is_photo = False
    txn_text = None
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        is_photo = True
    elif update.message.document:
        file_id = update.message.document.file_id
    elif update.message.text:
        txn_text = update.message.text.strip()
    else:
        await update.message.reply_text("❌ Please send a screenshot, or type your Transaction ID as text.")
        return BDT_PROOF

    user = update.effective_user

    async with db_conn() as db:
        cur = await db.execute("SELECT 1 FROM payments WHERE user_id=? AND status='pending'", (user.id,))
        if await cur.fetchone():
            await update.message.reply_text("⚠️ You already have a pending payment request. Please wait for admin review.")
            return ConversationHandler.END

        rate = float(await get_setting("coin_rate_bdt", "5"))
        coins = int(amount * rate)

        await db.execute(
            "INSERT INTO payments (user_id, coins, screenshot_file_id, status, timestamp, payment_method, amount_bdt) "
            "VALUES (?, ?, ?, 'pending', ?, 'bdt', ?)",
            (user.id, coins, file_id, datetime.utcnow().isoformat(), amount),
        )
        await db.commit()
        cur = await db.execute("SELECT last_insert_rowid()")
        payment_id = (await cur.fetchone())[0]

    await update.message.reply_text("✅ Your payment request has been submitted. You'll be notified once reviewed.")

    uname = f"@{user.username}" if user.username else "—"
    txn_note = f"\n🧾 Transaction ID: <code>{html.escape(txn_text)}</code>" if txn_text else ""
    caption = (
        "💵 <b>New BDT Payment Request</b>\n\n"
        f"👤 Username: {html.escape(uname)}\n"
        f"🆔 User ID: <code>{user.id}</code>\n"
        f"💵 Amount: <b>₹Rs{amount}</b>\n"
        f"🪙 Coins: <b>{coins}</b>{txn_note}\n"
        f"📅 Time: {fmt_date(datetime.utcnow().isoformat())}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Accept", callback_data=f"pay:accept:{payment_id}", style="primary"),
         InlineKeyboardButton("❌ Reject", callback_data=f"pay:reject:{payment_id}", style="primary")]
    ])
    admin_ids = [OWNER_ID]
    for admin_id in admin_ids:
        try:
            if file_id and is_photo:
                await context.bot.send_photo(admin_id, file_id, caption=caption, parse_mode=ParseMode.HTML, reply_markup=kb)
            elif file_id:
                await context.bot.send_document(admin_id, file_id, caption=caption, parse_mode=ParseMode.HTML, reply_markup=kb)
            else:
                await context.bot.send_message(admin_id, caption, parse_mode=ParseMode.HTML, reply_markup=kb)
        except TelegramError:
            pass
    return ConversationHandler.END


async def cb_buybdt_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("❌ Cancelled.")
    context.user_data.pop("bdt_amount", None)
    return ConversationHandler.END


async def get_all_admin_ids() -> list[int]:
    ids = {OWNER_ID}
    async with db_conn() as db:
        cur = await db.execute("SELECT user_id FROM admins")
        rows = await cur.fetchall()
        for r in rows:
            ids.add(r[0])
    return list(ids)


async def get_admin_role(user_id: int) -> str | None:
    """Returns 'owner', 'senior', 'junior', or None (not an admin)."""
    if user_id == OWNER_ID:
        return "owner"
    async with db_conn() as db:
        cur = await db.execute("SELECT role FROM admins WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        return row[0] if row else None


async def log_admin_action(bot, actor_id: int, action_text: str) -> None:
    """Audit trail with a Senior/Junior hierarchy:
    - Junior admin actions -> notify Owner + all Senior admins.
    - Senior admin actions -> notify Owner only.
    - Owner's own actions -> no notification needed.
    Every action (including the Owner's own) is persisted to admin_logs for
    the browsable Action Log Viewer."""
    async with db_conn() as db:
        await db.execute(
            "INSERT INTO admin_logs (actor_id, action_text, timestamp) VALUES (?, ?, ?)",
            (actor_id, action_text, datetime.utcnow().isoformat()),
        )
        await db.commit()

    if actor_id == OWNER_ID:
        return
    role = await get_admin_role(actor_id)
    actor_row = await get_user_row(actor_id)
    actor_label = f"@{actor_row['username']}" if actor_row and actor_row["username"] else str(actor_id)
    role_tag = f" [{role}]" if role else ""
    text = f"📋 <b>Admin Action</b>\n\n👤 By: {html.escape(actor_label)}{role_tag} (<code>{actor_id}</code>)\n{action_text}"

    recipients = {OWNER_ID}
    if role == "junior":
        async with db_conn() as db:
            cur = await db.execute("SELECT user_id FROM admins WHERE role='senior'")
            for r in await cur.fetchall():
                recipients.add(r[0])

    for uid in recipients:
        try:
            await bot.send_message(uid, text, parse_mode=ParseMode.HTML)
        except TelegramError:
            pass


async def cb_payment_accept(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    admin = update.effective_user
    if not await require_owner(update):
        return
    payment_id = int(query.data.split(":")[2])
    async with db_conn() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM payments WHERE payment_id=?", (payment_id,))
        payment = await cur.fetchone()
        if not payment or payment["status"] != "pending":
            await query.answer("⚠️ Already processed.", show_alert=True)
            return
        await db.execute(
            "UPDATE payments SET status='accepted', admin_id=? WHERE payment_id=?",
            (admin.id, payment_id),
        )
        await db.commit()

    method_note = f"({payment['stars']} stars)" if payment["payment_method"] != "bdt" else f"(₹Rs{payment['amount_bdt']})"
    await add_coins(payment["user_id"], payment["coins"], "payment", f"Payment accepted {method_note}")
    await query.answer("✅ Accepted")
    try:
        await query.message.delete()
    except (BadRequest, TelegramError):
        pass
    try:
        await context.bot.send_message(
            payment["user_id"],
            f"✅ <b>Payment Accepted!</b>\n🪙 {payment['coins']} Coins have been added to your wallet.",
            parse_mode=ParseMode.HTML,
        )
    except TelegramError:
        pass
    await log_admin_action(context.bot, admin.id, f"✅ Accepted payment #{payment_id} for user {payment['user_id']} ({payment['coins']} coins)")


async def cb_payment_reject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    admin = update.effective_user
    if not await require_owner(update):
        return
    payment_id = int(query.data.split(":")[2])
    context.user_data["reject_payment_id"] = payment_id
    context.user_data["reject_chat_msg"] = (query.message.chat_id, query.message.message_id)
    await query.answer()
    await query.message.reply_text(
        "✏️ Enter rejection reason (or send - to skip):"
    )
    context.user_data["awaiting_reject_reason"] = True


async def handle_reject_reason(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.user_data.get("awaiting_reject_reason"):
        return
    context.user_data["awaiting_reject_reason"] = False
    reason = update.message.text.strip()
    reason = "" if reason == "-" else reason
    payment_id = context.user_data.pop("reject_payment_id")
    reject_chat_msg = context.user_data.pop("reject_chat_msg", None)

    async with db_conn() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM payments WHERE payment_id=?", (payment_id,))
        payment = await cur.fetchone()
        if not payment or payment["status"] != "pending":
            await update.message.reply_text("⚠️ Already processed.")
            return
        await db.execute(
            "UPDATE payments SET status='rejected', admin_id=?, reason=? WHERE payment_id=?",
            (update.effective_user.id, reason, payment_id),
        )
        await db.commit()

    await update.message.reply_text("❌ Payment rejected and user notified.")
    if reject_chat_msg:
        try:
            await context.bot.delete_message(chat_id=reject_chat_msg[0], message_id=reject_chat_msg[1])
        except TelegramError:
            pass
    try:
        msg = "❌ <b>Payment Rejected</b>"
        if reason:
            msg += f"\nReason: {html.escape(reason)}"
        await context.bot.send_message(payment["user_id"], msg, parse_mode=ParseMode.HTML)
    except TelegramError:
        pass
    await log_admin_action(
        context.bot, update.effective_user.id,
        f"❌ Rejected payment #{payment_id} for user {payment['user_id']}" + (f"\nReason: {html.escape(reason)}" if reason else "")
    )



async def handle_daily_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard_user(update, context):
        return
    user = update.effective_user
    lock = await get_user_action_lock(user.id)
    async with lock:
        row = await get_user_row(user.id)
        if row and row["last_bonus_time"]:
            last = datetime.fromisoformat(row["last_bonus_time"])
            elapsed = datetime.utcnow() - last
            if elapsed < timedelta(hours=24):
                remaining = timedelta(hours=24) - elapsed
                h, rem = divmod(int(remaining.total_seconds()), 3600)
                m = rem // 60
                await update.effective_message.reply_text(
                    f"⏳ <b>Daily Bonus Already Claimed</b>\nCome back in <b>{h}h {m}m</b>.",
                    parse_mode=ParseMode.HTML,
                )
                return

        premium = await is_premium_active(user.id)
        if premium:
            reward = int(await get_setting("premium_daily_bonus", "40"))
            tx_label = "Premium daily bonus claim"
        else:
            reward = int(await get_setting("daily_bonus", "25"))
            tx_label = "Daily bonus claim"
        await add_coins(user.id, reward, "daily_bonus", tx_label)
        async with db_conn() as db:
            await db.execute(
                "UPDATE users SET last_bonus_time=? WHERE user_id=?",
                (datetime.utcnow().isoformat(), user.id),
            )
            await db.commit()
    badge = "\n👑 <i>Premium Daily Bonus</i>" if premium else ""
    await update.effective_message.reply_text(
        f"🎁 <b>Daily Bonus Claimed!</b>\n🪙 You received <b>{reward} Coins</b>.{badge}",
        parse_mode=ParseMode.HTML,
    )



async def _spins_left(user_id: int) -> int:
    row = await get_user_row(user_id)
    today = datetime.utcnow().strftime("%Y-%m-%d")
    used = row["spin_count_today"] if row and row["spin_reset_date"] == today else 0
    limit = 2 if await is_premium_active(user_id) else 1
    return max(0, limit - used)


async def handle_daily_spin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard_user(update, context):
        return
    user = update.effective_user
    left = await _spins_left(user.id)
    limit = 2 if await is_premium_active(user.id) else 1
    text = (
        "🎰 <b>Daily Spin</b>\n"
        f"{DIVIDER}\n"
        f"🎁 Coins (small): <b>50%</b>\n"
        f"💰 Coins (big): <b>25%</b>\n"
        f"📁 Random File: <b>10%</b>\n"
        f"👑 7-Day Premium: <b>15%</b>\n"
        f"{DIVIDER}\n"
        f"🔄 Spins left today: <b>{left}/{limit}</b>"
    )
    if left <= 0:
        kb = None
        text += "\n\n⏳ Come back tomorrow for more spins!"
    else:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🎰 SPIN NOW", callback_data="spin:go", style="success")]])
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def cb_spin_go(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    lock = await get_user_action_lock(user.id)

    async with lock:
        left = await _spins_left(user.id)
        if left <= 0:
            await query.answer("⏳ No spins left today — come back tomorrow!", show_alert=True)
            return
        await query.answer()

        today = datetime.utcnow().strftime("%Y-%m-%d")
        row = await get_user_row(user.id)
        used_today = row["spin_count_today"] if row and row["spin_reset_date"] == today else 0
        async with db_conn() as db:
            await db.execute(
                "UPDATE users SET spin_count_today=?, spin_reset_date=? WHERE user_id=?",
                (used_today + 1, today, user.id),
            )
            await db.commit()

        symbols = ["🍒", "🍋", "🍇", "🍉", "🍊", "⭐", "💎", "7️⃣"]

        def render_reels(reel, locked, caption):
            row = "│".join(f"  {s}  " for s in reel)
            marks = "".join(("🔒" if locked[i] else "  ") for i in range(3))
            return (
                f"🎰 <b>DAILY SPIN</b> 🎰\n{DIVIDER}\n\n"
                f"   ┌─────┬─────┬─────┐\n"
                f"   │{row}│\n"
                f"   └─────┴─────┴─────┘\n"
                f"     {marks}\n\n"
                f"<i>{caption}</i>"
            )

        try:
            await query.edit_message_text(
                f"🎰 <b>DAILY SPIN</b> 🎰\n{DIVIDER}\n\n"
                f"   ┌─────┬─────┬─────┐\n"
                f"   │  ❔  │  ❔  │  ❔  │\n"
                f"   └─────┴─────┴─────┘\n\n"
                f"<i>🎲 Pulling the lever...</i>",
                parse_mode=ParseMode.HTML,
            )
        except (BadRequest, TelegramError):
            pass
        await asyncio.sleep(0.5)

        frame_count = 16
        reel = [random.choice(symbols) for _ in range(3)]
        locked = [False, False, False]

        for i in range(frame_count):
            progress = i / frame_count
            if progress > 0.55:
                locked[0] = True
            if progress > 0.75:
                locked[1] = True
            if progress > 0.92:
                locked[2] = True
            for j in range(3):
                if not locked[j]:
                    reel[j] = random.choice(symbols)

            if progress < 0.55:
                caption = "🌀 Spinning" + "." * (1 + i % 3)
            elif progress < 0.85:
                caption = "⏳ Almost there..."
            else:
                caption = "🔥 Revealing..."

            try:
                await query.edit_message_text(render_reels(reel, locked, caption), parse_mode=ParseMode.HTML)
            except (BadRequest, TelegramError):
                pass
            await asyncio.sleep(0.18 + (progress ** 2) * 0.4)

        await asyncio.sleep(0.3)

        cfg = json.loads(await get_setting("spin_settings", "{}"))
        roll = random.uniform(0, 100)
        luck = random.randint(1, 100)
        c_low = cfg.get("chance_coins_low", 50)
        c_high = cfg.get("chance_coins_high", 25)
        c_file = cfg.get("chance_file", 10)

        if roll < c_low:
            win_symbol, category = "🍒", "coins_low"
        elif roll < c_low + c_high:
            win_symbol, category = "7️⃣", "coins_high"
        elif roll < c_low + c_high + c_file:
            win_symbol, category = "⭐", "file"
        else:
            win_symbol, category = "💎", "premium"

        try:
            await query.edit_message_text(
                render_reels([win_symbol] * 3, [True, True, True], "🎉 <b>JACKPOT LOCKED IN!</b> 🎉"),
                parse_mode=ParseMode.HTML,
            )
        except (BadRequest, TelegramError):
            pass
        await asyncio.sleep(0.6)

        if category == "coins_low":
            amount = random.randint(cfg.get("coins_low_min", 20), cfg.get("coins_low_max", 140))
            await add_coins(user.id, amount, "daily_spin", f"Daily spin win: {amount} coins")
            result = f"🪙 <b>You won {amount} Coins!</b>"
        elif category == "coins_high":
            amount = random.randint(cfg.get("coins_high_min", 150), cfg.get("coins_high_max", 300))
            await add_coins(user.id, amount, "daily_spin", f"Daily spin win: {amount} coins")
            result = f"💰 <b>Jackpot! You won {amount} Coins!</b>"
        elif category == "file":
            async with db_conn() as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute(
                    "SELECT * FROM files WHERE is_deleted=0 AND price>0 ORDER BY RANDOM() LIMIT 1"
                )
                f = await cur.fetchone()
                if not f:
                    cur = await db.execute("SELECT * FROM files WHERE is_deleted=0 ORDER BY RANDOM() LIMIT 1")
                    f = await cur.fetchone()
            if f:
                async with db_conn() as db:
                    await db.execute(
                        "INSERT INTO purchases (user_id, file_pk, price, timestamp) VALUES (?, ?, 0, ?)",
                        (user.id, f["file_pk"], datetime.utcnow().isoformat()),
                    )
                    await db.commit()
                result = f"📁 <b>You won a file!</b>\n{html.escape(f['file_name'])}"
            else:
                amount = random.randint(cfg.get("coins_low_min", 20), cfg.get("coins_low_max", 140))
                await add_coins(user.id, amount, "daily_spin", "Daily spin fallback (no files available)")
                result = f"🪙 <b>You won {amount} Coins!</b> (no files were available)"
        else:
            days = cfg.get("premium_days", 7)
            await grant_premium(user.id, days, admin_id=0)
            result = f"👑 <b>You won {days} Days of Premium!</b>"

        final_text = (
            f"🎊 <b>SPIN RESULT</b> 🎊\n{DIVIDER}\n\n"
            f"   ┌─────┬─────┬─────┐\n"
            f"   │  {win_symbol}  │  {win_symbol}  │  {win_symbol}  │\n"
            f"   └─────┴─────┴─────┘\n\n"
            f"{result}\n\n"
            f"🍀 Your Luck: <b>{luck}%</b>\n{DIVIDER}"
        )
        try:
            await query.edit_message_text(final_text, parse_mode=ParseMode.HTML)
        except (BadRequest, TelegramError):
            await query.message.reply_text(final_text, parse_mode=ParseMode.HTML)



async def handle_mystery_box(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard_user(update, context):
        return
    pricing = json.loads(await get_setting("mystery_box_pricing", "[]"))
    balance = await get_balance(update.effective_user.id)
    lines = [f"🎁 <b>Mystery Box</b>\n{DIVIDER}", f"🪙 Your balance: <b>{balance}</b>\n"]
    buttons = []
    for p in pricing:
        lines.append(f"{p['label']} — 🪙 {p['price']}")
        buttons.append([InlineKeyboardButton(
            f"{p['label']} ({p['price']} coins)", callback_data=f"mystery:open:{p['tier']}:{p['price']}", style="success"
        )])
    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons)
    )


async def cb_mystery_open(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    _, _, tier, price = query.data.split(":")
    price = int(price)
    lock = await get_user_action_lock(user.id)

    async with lock:
        ok = await remove_coins(user.id, price, "mystery_box", f"Opened {tier} mystery box")
        if not ok:
            await query.answer("❌ Not enough coins.", show_alert=True)
            return

        async with db_conn() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM files WHERE is_deleted=0 AND mystery_tier=? ORDER BY RANDOM() LIMIT 1", (tier,)
            )
            f = await cur.fetchone()

        if not f:
            await add_coins(user.id, price, "mystery_box_refund", f"No files available in {tier} tier — refunded")
            await query.answer("😢 No files available in this box right now — refunded.", show_alert=True)
            return

        async with db_conn() as db:
            await db.execute(
                "INSERT INTO purchases (user_id, file_pk, price, timestamp) VALUES (?, ?, ?, ?)",
                (user.id, f["file_pk"], price, datetime.utcnow().isoformat()),
            )
            await db.commit()

    await query.answer("🎉 You won a file!")
    icon = FILE_TYPE_ICON.get(f["file_kind"], "📄")
    await query.edit_message_text(
        f"🎉 <b>Mystery Box Opened!</b>\n\n{icon} <b>{html.escape(f['file_name'])}</b>\n📦 {fmt_size(f['file_size'])}",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬇ Download Now", callback_data=f"file:dl:{f['file_pk']}", style="success")]]),
    )



async def handle_invite_friends(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard_user(update, context):
        return
    user = update.effective_user
    reward = await get_setting("referral_reward", "20")
    async with db_conn() as db:
        cur = await db.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id=?", (user.id,))
        count = (await cur.fetchone())[0]
    link = f"https://t.me/{BOT_USERNAME}?start={user.id}"
    text = (
        "🚀 <b>Invite Friends</b>\n\n"
        f"Earn <b>{reward} Coins</b> for every friend who joins using your link!\n\n"
        f"🔗 <code>{link}</code>\n\n"
        f"👥 Total Referrals: <b>{count}</b>"
    )
    await update.effective_message.reply_text(
        text, parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👥 My Referrals", callback_data="myrefs:0", style="primary")]]),
    )


async def cb_my_referrals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    page = int(query.data.split(":")[1])
    per_page = 10
    user = update.effective_user
    async with db_conn() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT new_user_id, date, reward FROM referrals WHERE referrer_id=? ORDER BY date DESC LIMIT ? OFFSET ?",
            (user.id, per_page, page * per_page),
        )
        rows = await cur.fetchall()

    if not rows and page == 0:
        text = "👥 <b>My Referrals</b>\n\nYou haven't referred anyone yet."
    else:
        lines = ["👥 <b>My Referrals</b>\n"]
        for r in rows:
            u = await get_user_row(r["new_user_id"])
            label = f"@{u['username']}" if u and u["username"] else (u["first_name"] if u else f"User {r['new_user_id']}")
            lines.append(f"• {html.escape(label or '—')} — 🪙 +{r['reward']} <i>({fmt_date(r['date'])})</i>")
        text = "\n".join(lines)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅ Prev", callback_data=f"myrefs:{page-1}", style="primary"))
    if len(rows) == per_page:
        nav.append(InlineKeyboardButton("Next ➡", callback_data=f"myrefs:{page+1}", style="primary"))
    kb = InlineKeyboardMarkup([nav]) if nav else None
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def handle_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard_user(update, context):
        return
    async with db_conn() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT referrer_id, COUNT(*) as cnt FROM referrals GROUP BY referrer_id "
            "ORDER BY cnt DESC LIMIT 10"
        )
        rows = await cur.fetchall()

    if not rows:
        await update.effective_message.reply_text("🏆 <b>Leaderboard</b>\n\nNo referrals yet.", parse_mode=ParseMode.HTML)
        return

    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 <b>Top 10 Referrals</b>\n"]
    for i, r in enumerate(rows):
        u = await get_user_row(r["referrer_id"])
        name = html.escape(u["first_name"] or "Unknown") if u else "Unknown"
        rank = medals[i] if i < 3 else f"{i+1}."
        lines.append(f"{rank} {name} — <b>{r['cnt']}</b> referrals")
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)



async def handle_redeem_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard_user(update, context):
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑 Redeem a Code", callback_data="redeem:enter", style="primary")],
        [InlineKeyboardButton("🎁 Convert Coins to Gift Code", callback_data="redeem:gift", style="primary")],
    ])
    await update.effective_message.reply_text(
        "🎟️ <b>Redeem Center</b>\n\nWhat would you like to do?",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )


async def cb_redeem_enter_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "🎟️ Please enter your redeem code:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="redeem:cancel", style="primary")]]),
    )
    return RD_CODE


async def cb_redeem_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END


async def handle_redeem_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    code = update.message.text.strip().upper()
    user = update.effective_user
    lock = await get_user_action_lock(user.id)

    async with lock:
        async with db_conn() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM redeems WHERE code=?", (code,))
            redeem = await cur.fetchone()

            admin = await is_admin(user.id)
            if not redeem:
                await update.message.reply_text("❌ Invalid Redeem Code.", reply_markup=user_reply_keyboard(admin))
                return ConversationHandler.END

            if redeem["status"] != "active":
                await update.message.reply_text("❌ This code is no longer active.", reply_markup=user_reply_keyboard(admin))
                return ConversationHandler.END

            if redeem["expiry_date"]:
                try:
                    if datetime.fromisoformat(redeem["expiry_date"]) < datetime.utcnow():
                        await db.execute("UPDATE redeems SET status='expired' WHERE code=?", (code,))
                        await db.commit()
                        await update.message.reply_text("❌ This code has expired.", reply_markup=user_reply_keyboard(admin))
                        return ConversationHandler.END
                except ValueError:
                    pass

            if redeem["usage_limit"] and redeem["used_count"] >= redeem["usage_limit"]:
                await update.message.reply_text("❌ This code has reached its usage limit.", reply_markup=user_reply_keyboard(admin))
                return ConversationHandler.END

            cur = await db.execute(
                "SELECT 1 FROM redeem_uses WHERE code=? AND user_id=?", (code, user.id)
            )
            if await cur.fetchone():
                await update.message.reply_text("❌ You have already redeemed this code.", reply_markup=user_reply_keyboard(admin))
                return ConversationHandler.END

            await db.execute(
                "INSERT INTO redeem_uses (code, user_id, timestamp) VALUES (?, ?, ?)",
                (code, user.id, datetime.utcnow().isoformat()),
            )
            await db.execute("UPDATE redeems SET used_count = used_count + 1 WHERE code=?", (code,))
            await db.commit()

        if redeem["coin_reward"]:
            await add_coins(user.id, redeem["coin_reward"], "redeem", f"Redeem code {code}")
        if redeem["premium_days"]:
            await grant_premium(user.id, redeem["premium_days"], admin_id=0)

    msg = f"✅ <b>Redeem Successful!</b>\n🪙 +{redeem['coin_reward']} Coins"
    if redeem["premium_days"]:
        msg += f"\n⭐ +{redeem['premium_days']} Days Premium"
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=user_reply_keyboard(admin))
    return ConversationHandler.END



def _generate_gift_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "GIFT" + "".join(secrets.choice(alphabet) for _ in range(8))


async def cb_redeem_gift_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    balance = await get_balance(update.effective_user.id)
    await query.message.reply_text(
        f"🎁 <b>Convert Coins to Gift Code</b>\n\n"
        f"🪙 Your balance: <b>{balance}</b>\n\n"
        "Enter how many coins to convert into a one-time gift code "
        "(you can share it with anyone — they redeem it once for that many coins):",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="redeem:cancel", style="primary")]]),
    )
    return GC_AMOUNT


async def conv_gift_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("❌ Please enter a valid positive number of coins.")
        return GC_AMOUNT
    amount = int(text)
    user = update.effective_user
    lock = await get_user_action_lock(user.id)

    async with lock:
        ok = await remove_coins(user.id, amount, "gift_code_created", f"Converted {amount} coins to a gift code")
        if not ok:
            await update.message.reply_text("❌ You don't have enough coins for that.")
            return ConversationHandler.END

        code = _generate_gift_code()
        async with db_conn() as db:
            for _ in range(5):
                cur = await db.execute("SELECT 1 FROM redeems WHERE code=?", (code,))
                if not await cur.fetchone():
                    break
                code = _generate_gift_code()
            await db.execute(
                "INSERT INTO redeems (code, coin_reward, premium_days, usage_limit, status, created_by, created_at, source) "
                "VALUES (?, ?, 0, 1, 'active', ?, ?, 'user')",
                (code, amount, user.id, datetime.utcnow().isoformat()),
            )
            await db.commit()

    await update.message.reply_text(
        f"✅ <b>Gift Code Created!</b>\n\n🎟 Code: <code>{code}</code>\n🪙 Value: <b>{amount} Coins</b>\n\n"
        "Share this code with anyone — they can redeem it once via 🎟️ Redeem Code → Redeem a Code.",
        parse_mode=ParseMode.HTML,
    )
    return ConversationHandler.END



async def handle_my_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard_user(update, context):
        return
    user = update.effective_user
    row = await get_user_row(user.id)
    balance = await get_balance(user.id)
    async with db_conn() as db:
        cur = await db.execute("SELECT COUNT(*) FROM purchases WHERE user_id=?", (user.id,))
        purchased = (await cur.fetchone())[0]
        cur = await db.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id=?", (user.id,))
        refs = (await cur.fetchone())[0]
        cur = await db.execute("SELECT COUNT(*) FROM redeem_uses WHERE user_id=?", (user.id,))
        redeems = (await cur.fetchone())[0]

    is_premium = await is_premium_active(user.id)
    premium_line = "👑 <b>Premium — Active</b>" if is_premium else "⭐ Premium: <i>Not Active</i>"
    text = (
        "📊 <b>My Stats</b>\n"
        f"{DIVIDER}\n"
        f"👤 <b>User ID:</b> <code>{user.id}</code>\n"
        f"📅 <b>Join Date:</b> {fmt_date(row['join_date']) if row else '—'}\n"
        f"🪙 <b>Coins:</b> {balance}\n"
        f"📁 <b>Purchased Files:</b> {purchased}\n"
        f"👥 <b>Referrals:</b> {refs}\n"
        f"🎁 <b>Redeem Count:</b> {redeems}\n"
        f"{premium_line}\n"
        f"{DIVIDER}"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)



async def handle_buy_files(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard_user(update, context):
        return
    await send_file_list(update, context, page=0)


async def send_file_list(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int, edit: bool = False) -> None:
    per_page = 8
    async with db_conn() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT file_pk, file_name, price FROM files WHERE is_deleted=0 "
            "ORDER BY file_pk DESC LIMIT ? OFFSET ?",
            (per_page, page * per_page),
        )
        rows = await cur.fetchall()

    if not rows:
        text = "📁 <b>Buy Files</b>\n\nNo files available right now."
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("📁 My Purchases", callback_data="purchases:0", style="primary")]])
    else:
        text = "📁 <b>Buy Files</b>\n\nSelect a file to view details:"
        buttons = []
        for r in rows:
            tag = "🟢 FREE" if r["price"] == 0 else f"🪙 {r['price']}"
            buttons.append([InlineKeyboardButton(f"📄 {r['file_name']} — {tag}", callback_data=f"file:view:{r['file_pk']}", style="primary")])
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅ Prev", callback_data=f"files:list:{page-1}", style="primary"))
        if len(rows) == per_page:
            nav.append(InlineKeyboardButton("Next ➡", callback_data=f"files:list:{page+1}", style="primary"))
        if nav:
            buttons.append(nav)
        buttons.append([InlineKeyboardButton("📁 My Purchases", callback_data="purchases:0", style="primary")])
        kb = InlineKeyboardMarkup(buttons)

    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def cb_files_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    page = int(query.data.split(":")[2])
    await send_file_list(update, context, page, edit=True)


async def cb_file_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    file_pk = int(query.data.split(":")[2])
    user = update.effective_user

    async with db_conn() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM files WHERE file_pk=? AND is_deleted=0", (file_pk,))
        f = await cur.fetchone()
        if not f:
            await query.answer("❌ File not found.", show_alert=True)
            return
        cur = await db.execute(
            "SELECT 1 FROM purchases WHERE user_id=? AND file_pk=?", (user.id, file_pk)
        )
        owned = bool(await cur.fetchone())

    await query.answer()
    icon = FILE_TYPE_ICON.get(f["file_kind"], "📄")
    text = (
        f"{icon} <b>{html.escape(f['file_name'])}</b>\n\n"
        f"📦 Size: <b>{fmt_size(f['file_size'])}</b>\n"
        f"📄 Description: {html.escape(f['description']) if f['description'] else '—'}\n"
    )
    if f["price"] == 0 or owned:
        text += "\n🟢 <b>FREE</b>" if f["price"] == 0 and not owned else "\n✅ <b>You own this file</b>"
        buttons = [[InlineKeyboardButton("⬇ Download", callback_data=f"file:dl:{file_pk}", style="primary")]]
    else:
        text += f"\n🪙 <b>{f['price']} Coins</b>"
        buttons = [[InlineKeyboardButton("🛒 Buy Now", callback_data=f"file:buy:{file_pk}", style="primary")]]
    buttons.append([InlineKeyboardButton("⬅ Back", callback_data="files:list:0", style="primary")])
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))


async def cb_file_buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    file_pk = int(query.data.split(":")[2])
    user = update.effective_user
    lock = await get_user_action_lock(user.id)
    async with lock:
        async with db_conn() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM files WHERE file_pk=? AND is_deleted=0", (file_pk,))
            f = await cur.fetchone()
            if not f:
                await query.answer("❌ File not found.", show_alert=True)
                return
            cur = await db.execute("SELECT 1 FROM purchases WHERE user_id=? AND file_pk=?", (user.id, file_pk))
            if await cur.fetchone():
                await query.answer("✅ You already own this file.", show_alert=True)
                return

        if f["price"] > 0:
            ok = await remove_coins(user.id, f["price"], "purchase", f"Purchased file: {f['file_name']}")
            if not ok:
                await query.answer("❌ Not enough Coins.", show_alert=True)
                await query.edit_message_text(
                    "❌ <b>Not enough Coins.</b>\n\nPlease top up your wallet to continue.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💎 Open Wallet", callback_data="wallet:home", style="primary")]]),
                )
                return

        async with db_conn() as db:
            await db.execute(
                "INSERT INTO purchases (user_id, file_pk, price, timestamp) VALUES (?, ?, ?, ?)",
                (user.id, file_pk, f["price"], datetime.utcnow().isoformat()),
            )
            await db.commit()

    await query.answer("✅ Purchased!")
    await query.edit_message_text(
        f"✅ <b>Purchase Successful!</b>\n\nYou now own <b>{html.escape(f['file_name'])}</b> forever.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬇ Download Now", callback_data=f"file:dl:{file_pk}", style="primary")]]),
    )


async def cb_file_download(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    file_pk = int(query.data.split(":")[2])
    user = update.effective_user

    async with db_conn() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM files WHERE file_pk=? AND is_deleted=0", (file_pk,))
        f = await cur.fetchone()
        if not f:
            await query.answer("❌ File not found.", show_alert=True)
            return
        if f["price"] > 0:
            cur = await db.execute("SELECT 1 FROM purchases WHERE user_id=? AND file_pk=?", (user.id, file_pk))
            if not await cur.fetchone():
                await query.answer("❌ You need to purchase this file first.", show_alert=True)
                return

    await query.answer("⬇ Sending file...")
    try:
        await context.bot.send_document(
            chat_id=user.id, document=f["tg_file_id"], caption=f"📄 {html.escape(f['file_name'])}", parse_mode=ParseMode.HTML
        )
    except (BadRequest, TelegramError):
        try:
            await context.bot.send_message(user.id, "⚠️ Could not deliver file, please contact support.")
        except TelegramError:
            pass
        return

    async with db_conn() as db:
        await db.execute(
            "INSERT INTO downloads (user_id, file_pk, timestamp) VALUES (?, ?, ?)",
            (user.id, file_pk, datetime.utcnow().isoformat()),
        )
        await db.execute("UPDATE files SET downloads_count = downloads_count + 1 WHERE file_pk=?", (file_pk,))
        await db.commit()


async def cb_my_purchases(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    page = int(query.data.split(":")[1])
    per_page = 8
    user = update.effective_user
    async with db_conn() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT p.*, f.file_name FROM purchases p JOIN files f ON p.file_pk=f.file_pk "
            "WHERE p.user_id=? ORDER BY p.id DESC LIMIT ? OFFSET ?",
            (user.id, per_page, page * per_page),
        )
        rows = await cur.fetchall()

    if not rows:
        text = "📁 <b>My Purchases</b>\n\nYou haven't purchased any files yet."
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅ Back", callback_data="files:list:0", style="primary")]])
    else:
        text = "📁 <b>My Purchases</b>\n\n"
        buttons = []
        for r in rows:
            buttons.append([InlineKeyboardButton(f"⬇ {r['file_name']}", callback_data=f"file:dl:{r['file_pk']}", style="primary")])
        buttons.append([InlineKeyboardButton("⬅ Back", callback_data="files:list:0", style="primary")])
        kb = InlineKeyboardMarkup(buttons)

    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)



async def cb_admin_upload_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await is_admin(update.effective_user.id):
        if update.callback_query:
            await update.callback_query.answer(ADMIN_ONLY_MSG, show_alert=True)
        else:
            await update.effective_message.reply_text(ADMIN_ONLY_MSG)
        return ConversationHandler.END
    if update.callback_query:
        await update.callback_query.answer()
    await update.effective_message.reply_text(
        "📤 <b>Upload File</b>\n\nSend me the file you want to add to the store.",
        parse_mode=ParseMode.HTML,
    )
    return UP_FILE


async def conv_upload_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.message
    kind = None
    tg_file = None
    file_name = None
    file_size = 0

    if msg.document:
        kind, tg_file = "document", msg.document
        file_name, file_size = tg_file.file_name or "file", tg_file.file_size or 0
    elif msg.video:
        kind, tg_file = "video", msg.video
        file_name = tg_file.file_name or f"video_{tg_file.file_unique_id}.mp4"
        file_size = tg_file.file_size or 0
    elif msg.audio:
        kind, tg_file = "audio", msg.audio
        file_name = tg_file.file_name or f"audio_{tg_file.file_unique_id}.mp3"
        file_size = tg_file.file_size or 0
    elif msg.photo:
        tg_file = msg.photo[-1]
        kind = "photo"
        file_name = f"photo_{tg_file.file_unique_id}.jpg"
        file_size = tg_file.file_size or 0
    elif msg.voice:
        kind, tg_file = "voice", msg.voice
        file_name = f"voice_{tg_file.file_unique_id}.ogg"
        file_size = tg_file.file_size or 0
    elif msg.animation:
        kind, tg_file = "animation", msg.animation
        file_name = tg_file.file_name or f"gif_{tg_file.file_unique_id}.mp4"
        file_size = tg_file.file_size or 0
    else:
        await msg.reply_text("❌ Unsupported file type. Please send a valid file.")
        return UP_FILE

    context.user_data["up_kind"] = kind
    context.user_data["up_file_id"] = tg_file.file_id
    context.user_data["up_unique_id"] = tg_file.file_unique_id
    context.user_data["up_orig_name"] = file_name
    context.user_data["up_size"] = file_size

    await msg.reply_text(
        f"✅ File received ({fmt_size(file_size)}).\n\n📝 Enter File Name (or send - to use original filename):"
    )
    return UP_NAME


async def conv_upload_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    name = context.user_data["up_orig_name"] if text == "-" else text
    context.user_data["up_name"] = name
    await update.message.reply_text("📄 Enter Description (or send - to skip):")
    return UP_DESC


async def conv_upload_desc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    context.user_data["up_desc"] = "" if text == "-" else text
    await update.message.reply_text("💰 Enter Coin Price (0 = FREE):\n\nExample: 0, 50, 100, 250, 500, 1000")
    return UP_PRICE


async def conv_upload_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("❌ Please enter a valid number.")
        return UP_PRICE
    context.user_data["up_price"] = int(text)
    return await show_upload_preview(update, context)


async def show_upload_preview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    d = context.user_data
    text = (
        "👀 <b>Preview</b>\n\n"
        f"📁 File Name: {html.escape(d['up_name'])}\n"
        f"📦 File Size: {fmt_size(d['up_size'])}\n"
        f"📄 Description: {html.escape(d['up_desc']) if d['up_desc'] else '—'}\n"
        f"💰 Coin Price: {d['up_price']}\n"
        f"📅 Upload Date: {fmt_date(datetime.utcnow().isoformat())}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Publish", callback_data="up:publish", style="primary"),
         InlineKeyboardButton("✏ Edit", callback_data="up:edit", style="primary")],
        [InlineKeyboardButton("❌ Cancel", callback_data="up:cancel", style="primary")],
    ])
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    return UP_PREVIEW


async def cb_upload_publish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    d = context.user_data
    user = update.effective_user
    async with db_conn() as db:
        await db.execute(
            "INSERT INTO files (tg_file_id, tg_file_unique_id, file_kind, file_name, file_size, "
            "description, price, upload_date, uploaded_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (d["up_file_id"], d["up_unique_id"], d["up_kind"], d["up_name"], d["up_size"],
             d["up_desc"], d["up_price"], datetime.utcnow().isoformat(), user.id),
        )
        await db.commit()
    await query.edit_message_text("✅ <b>File Published Successfully!</b>", parse_mode=ParseMode.HTML)
    for key in ["up_kind", "up_file_id", "up_unique_id", "up_orig_name", "up_size", "up_name", "up_desc", "up_price"]:
        context.user_data.pop(key, None)
    return ConversationHandler.END


async def cb_upload_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("📝 Enter File Name (or send - to use original filename):")
    return UP_NAME


async def cb_upload_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("❌ Upload cancelled.")
    for key in ["up_kind", "up_file_id", "up_unique_id", "up_orig_name", "up_size", "up_name", "up_desc", "up_price"]:
        context.user_data.pop(key, None)
    return ConversationHandler.END



async def cb_admin_managefiles(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update.effective_user.id):
        if update.callback_query:
            await update.callback_query.answer(ADMIN_ONLY_MSG, show_alert=True)
        else:
            await update.effective_message.reply_text(ADMIN_ONLY_MSG)
        return
    if update.callback_query:
        await update.callback_query.answer()
    await send_manage_files_list(update, context, page=0)


async def send_manage_files_list(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int) -> None:
    viewer_id = update.effective_user.id
    is_owner = viewer_id == OWNER_ID
    per_page = 8
    async with db_conn() as db:
        db.row_factory = aiosqlite.Row
        if is_owner:
            cur = await db.execute(
                "SELECT file_pk, file_name, price, downloads_count FROM files WHERE is_deleted=0 "
                "ORDER BY file_pk DESC LIMIT ? OFFSET ?",
                (per_page, page * per_page),
            )
        else:
            cur = await db.execute(
                "SELECT file_pk, file_name, price, downloads_count FROM files WHERE is_deleted=0 AND uploaded_by=? "
                "ORDER BY file_pk DESC LIMIT ? OFFSET ?",
                (viewer_id, per_page, page * per_page),
            )
        rows = await cur.fetchall()

    text = "📂 <b>Manage Files</b>\n\n"
    text += "Select a file to edit or delete:" if is_owner else "Files you've uploaded — select one to edit or delete:"
    buttons = []
    for r in rows:
        buttons.append([InlineKeyboardButton(
            f"📄 {r['file_name']} ({r['downloads_count']}⬇)", callback_data=f"mf:view:{r['file_pk']}:{page}"
        , style="primary")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅ Prev", callback_data=f"mf:list:{page-1}", style="primary"))
    if len(rows) == per_page:
        nav.append(InlineKeyboardButton("Next ➡", callback_data=f"mf:list:{page+1}", style="primary"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("🔍 Search Files", callback_data="mf:searchstart", style="primary")])
    buttons.append([InlineKeyboardButton("⬅ Back to Admin Panel", callback_data="ad:home", style="primary")])

    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))


async def cb_mf_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    page = int(query.data.split(":")[2])
    await send_manage_files_list(update, context, page)


async def cb_mf_search_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("🔍 Enter a keyword to search your files by name:")
    return MF_SEARCH


async def conv_mf_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyword = update.message.text.strip()
    viewer_id = update.effective_user.id
    is_owner = viewer_id == OWNER_ID
    async with db_conn() as db:
        db.row_factory = aiosqlite.Row
        if is_owner:
            cur = await db.execute(
                "SELECT file_pk, file_name FROM files WHERE is_deleted=0 AND file_name LIKE ? LIMIT 15",
                (f"%{keyword}%",),
            )
        else:
            cur = await db.execute(
                "SELECT file_pk, file_name FROM files WHERE is_deleted=0 AND uploaded_by=? AND file_name LIKE ? LIMIT 15",
                (viewer_id, f"%{keyword}%"),
            )
        rows = await cur.fetchall()

    if not rows:
        await update.message.reply_text("❌ No files matched that keyword.")
        return ConversationHandler.END

    buttons = [[InlineKeyboardButton(f"📄 {r['file_name']}", callback_data=f"mf:view:{r['file_pk']}:0", style="primary")] for r in rows]
    await update.message.reply_text(
        f"🔍 Found <b>{len(rows)}</b> matching file(s):", parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return ConversationHandler.END


async def cb_mf_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, _, file_pk, page = query.data.split(":")
    await _render_mf_view(update, int(file_pk), int(page))


async def _render_mf_view(update: Update, file_pk: int, page: int) -> None:
    query = update.callback_query
    async with db_conn() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM files WHERE file_pk=?", (file_pk,))
        f = await cur.fetchone()
    if not f:
        await query.edit_message_text("❌ File not found.")
        return
    if update.effective_user.id != OWNER_ID and f["uploaded_by"] != update.effective_user.id:
        await query.edit_message_text("🚫 You can only manage files you uploaded.")
        return
    text = (
        f"📄 <b>{html.escape(f['file_name'])}</b>\n\n"
        f"📦 Size: {fmt_size(f['file_size'])}\n"
        f"📄 Description: {html.escape(f['description']) if f['description'] else '—'}\n"
        f"💰 Price: {f['price']} coins\n"
        f"⬇ Downloads: {f['downloads_count']}\n"
        f"🎁 Mystery Tier: {f['mystery_tier'] or 'None'}\n"
        f"📌 Featured: {'Yes' if f['featured'] else 'No'}\n"
        f"📅 Uploaded: {fmt_date(f['upload_date'])}"
    )
    feature_label = "📌 Unfeature" if f["featured"] else "📌 Make Featured"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Edit Name", callback_data=f"mf:ename:{file_pk}", style="primary"),
         InlineKeyboardButton("✏️ Edit Desc", callback_data=f"mf:edesc:{file_pk}", style="primary")],
        [InlineKeyboardButton("✏️ Edit Price", callback_data=f"mf:eprice:{file_pk}", style="primary"),
         InlineKeyboardButton("🎁 Set Mystery Tier", callback_data=f"mf:etier:{file_pk}", style="primary")],
        [InlineKeyboardButton(feature_label, callback_data=f"mf:tfeat:{file_pk}:{page}", style="primary")],
        [InlineKeyboardButton("🗑 Delete File", callback_data=f"mf:delete:{file_pk}", style="primary")],
        [InlineKeyboardButton("⬅ Back", callback_data=f"mf:list:{page}", style="primary")],
    ])
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def cb_mf_toggle_featured(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, _, file_pk, page = query.data.split(":")
    file_pk = int(file_pk)
    if not await _can_manage_file(update.effective_user.id, file_pk):
        await query.answer("🚫 You can only manage files you uploaded.", show_alert=True)
        return
    async with db_conn() as db:
        cur = await db.execute("SELECT featured FROM files WHERE file_pk=?", (file_pk,))
        row = await cur.fetchone()
        new_val = 0 if (row and row[0]) else 1
        await db.execute("UPDATE files SET featured=? WHERE file_pk=?", (new_val, file_pk))
        await db.commit()
    await query.answer("📌 Featured!" if new_val else "Unfeatured")
    await _render_mf_view(update, file_pk, int(page))


async def _can_manage_file(user_id: int, file_pk: int) -> bool:
    if user_id == OWNER_ID:
        return True
    async with db_conn() as db:
        cur = await db.execute("SELECT uploaded_by FROM files WHERE file_pk=?", (file_pk,))
        row = await cur.fetchone()
        return bool(row and row[0] == user_id)


async def cb_mf_edit_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    _, field, file_pk = query.data.split(":")
    file_pk = int(file_pk)
    if not await _can_manage_file(update.effective_user.id, file_pk):
        await query.answer("🚫 You can only manage files you uploaded.", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    context.user_data["ef_file_pk"] = file_pk
    context.user_data["ef_field"] = field
    prompts = {
        "ename": "📝 Enter new File Name:",
        "edesc": "📄 Enter new Description:",
        "eprice": "💰 Enter new Coin Price:",
        "etier": "🎁 Enter Mystery Tier — <code>random</code>, <code>rare</code>, <code>premium</code>, or <code>none</code>:",
    }
    await query.message.reply_text(prompts[field], parse_mode=ParseMode.HTML)
    return EF_FIELD


async def conv_edit_field_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    field = context.user_data.pop("ef_field")
    file_pk = context.user_data.pop("ef_file_pk")
    value = update.message.text.strip()

    column_map = {"ename": "file_name", "edesc": "description", "eprice": "price", "etier": "mystery_tier"}
    column = column_map[field]

    if field == "eprice":
        if not value.isdigit():
            await update.message.reply_text("❌ Please enter a valid number.")
            context.user_data["ef_field"] = field
            context.user_data["ef_file_pk"] = file_pk
            return EF_FIELD
        value = int(value)
    elif field == "etier":
        value = value.lower()
        if value not in ("random", "rare", "premium", "none"):
            await update.message.reply_text("❌ Must be one of: random, rare, premium, none.")
            context.user_data["ef_field"] = field
            context.user_data["ef_file_pk"] = file_pk
            return EF_FIELD
        value = None if value == "none" else value

    async with db_conn() as db:
        await db.execute(f"UPDATE files SET {column}=? WHERE file_pk=?", (value, file_pk))
        await db.commit()

    await update.message.reply_text("✅ File updated successfully.")
    return ConversationHandler.END


async def cb_mf_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    file_pk = int(query.data.split(":")[2])
    if not await _can_manage_file(update.effective_user.id, file_pk):
        await query.answer("🚫 You can only manage files you uploaded.", show_alert=True)
        return
    await query.answer()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Yes, Delete", callback_data=f"mf:delconfirm:{file_pk}", style="primary"),
         InlineKeyboardButton("❌ No", callback_data=f"mf:view:{file_pk}:0", style="primary")],
    ])
    await query.edit_message_text("⚠️ Are you sure you want to delete this file?", reply_markup=kb)


async def cb_mf_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    file_pk = int(query.data.split(":")[2])
    if not await _can_manage_file(update.effective_user.id, file_pk):
        await query.answer("🚫 You can only manage files you uploaded.", show_alert=True)
        return
    async with db_conn() as db:
        await db.execute("UPDATE files SET is_deleted=1 WHERE file_pk=?", (file_pk,))
        await db.commit()
    await query.answer("🗑 Deleted")
    await query.edit_message_text("🗑 <b>File deleted successfully.</b>", parse_mode=ParseMode.HTML)



async def cb_admin_fcmgr(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_owner(update):
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Channel", callback_data="fc:add", style="primary"),
         InlineKeyboardButton("➖ Remove Channel", callback_data="fc:remove", style="primary")],
        [InlineKeyboardButton("📋 Channel List", callback_data="fc:list", style="primary")],
        [InlineKeyboardButton("⬅ Back", callback_data="ad:home", style="primary")],
    ])
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("📡 <b>Force Channel Manager</b>", parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await update.effective_message.reply_text("📡 <b>Force Channel Manager</b>", parse_mode=ParseMode.HTML, reply_markup=kb)


async def cb_fc_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await require_owner(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "➕ <b>Add Force-Join Channel/Group</b>\n\n"
        "Send either:\n"
        "• A public <b>@username</b>\n"
        "• Or a <b>https://t.me/username</b> link\n\n"
        "I need to already be a <b>member</b> of it — admin rights aren't required.",
        parse_mode=ParseMode.HTML,
    )
    return FC_ADD


async def conv_fc_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.message
    try:
        text = (msg.text or "").strip()
        username = None

        if text.startswith("@") and re.match(r"^@[A-Za-z0-9_]{5,32}$", text):
            username = text
        else:
            m = re.match(r"^https?://t\.me/([A-Za-z0-9_]{5,32})/?$", text)
            if m:
                username = f"@{m.group(1)}"

        if not username:
            await msg.reply_text("❌ Please send a valid @username or a https://t.me/username link.")
            return FC_ADD

        try:
            chat = await context.bot.get_chat(username)
        except TelegramError as e:
            await msg.reply_text(
                f"❌ Couldn't find that channel/group ({html.escape(str(e))}).\n"
                "Make sure the username is correct and I've already been added to it.",
                parse_mode=ParseMode.HTML,
            )
            return FC_ADD

        try:
            me = await context.bot.get_me()
            member = await context.bot.get_chat_member(chat.id, me.id)
            if member.status in ("left", "kicked"):
                await msg.reply_text("❌ I'm not currently a member of that chat. Please add me first, then try again.")
                return FC_ADD
        except TelegramError as e:
            await msg.reply_text(
                f"❌ I can't access that chat's member list ({html.escape(str(e))}).\n"
                "Please make sure I've been added to it.",
                parse_mode=ParseMode.HTML,
            )
            return FC_ADD

        resolved_username = f"@{chat.username}" if chat.username else username
        await _save_force_channel(chat.id, resolved_username, chat.title, str(chat.type))
        await msg.reply_text(f"✅ Added: <b>{html.escape(chat.title)}</b>", parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    except Exception as e:
        log.exception("Unexpected error in conv_fc_add")
        await msg.reply_text(
            f"⚠️ <b>Something went wrong adding this channel.</b>\n<code>{html.escape(str(e))}</code>\n\n"
            "This has been logged — please share this exact message so it can be fixed.",
            parse_mode=ParseMode.HTML,
        )
        return ConversationHandler.END


async def _save_force_channel(chat_id: int, username: str, title: str, chat_type: str) -> None:
    async with db_conn() as db:
        await db.execute(
            "INSERT INTO force_channels (username, chat_id, title, chat_type, added_date) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(username) DO UPDATE SET "
            "chat_id=excluded.chat_id, title=excluded.title, chat_type=excluded.chat_type",
            (username, chat_id, title, chat_type, datetime.utcnow().isoformat()),
        )
        await db.commit()


async def cb_fc_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    channels = await list_force_channels()
    if not channels:
        text = "📋 <b>Channel List</b>\n\nNo channels added yet."
    else:
        lines = ["📋 <b>Channel List</b>\n"]
        for ch in channels:
            lines.append(f"• <b>{html.escape(ch['title'])}</b> — added {fmt_date(ch['added_date'])}")
        text = "\n".join(lines)
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=back_button("ad:fcmgr"))


async def cb_fc_remove_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    channels = await list_force_channels()
    if not channels:
        await query.edit_message_text("No channels to remove.", reply_markup=back_button("ad:fcmgr"))
        return
    buttons = [[InlineKeyboardButton(f"➖ {ch['title']}", callback_data=f"fc:rm:{ch['id']}", style="primary")] for ch in channels]
    buttons.append([InlineKeyboardButton("⬅ Back", callback_data="ad:fcmgr", style="primary")])
    await query.edit_message_text("➖ Select a channel to remove:", reply_markup=InlineKeyboardMarkup(buttons))


async def cb_fc_remove_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    ch_id = int(query.data.split(":")[2])
    async with db_conn() as db:
        await db.execute("DELETE FROM force_channels WHERE id=?", (ch_id,))
        await db.commit()
    await query.answer("➖ Removed")
    await cb_fc_remove_menu(update, context)



async def cb_admin_walletmgr(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update.effective_user.id):
        if update.callback_query:
            await update.callback_query.answer(ADMIN_ONLY_MSG, show_alert=True)
        else:
            await update.effective_message.reply_text(ADMIN_ONLY_MSG)
        return
    is_owner = update.effective_user.id == OWNER_ID
    rows = [
        [InlineKeyboardButton("➕ Add Coins", callback_data="wm:add", style="primary"),
         InlineKeyboardButton("➖ Remove Coins", callback_data="wm:remove", style="primary")],
        [InlineKeyboardButton("♻ Reset Coins", callback_data="wm:reset", style="primary")],
    ]
    if is_owner:
        rows.append([InlineKeyboardButton("🎯 Set Exact Balance", callback_data="wm:setbal", style="primary")])
        rows.append([InlineKeyboardButton("👥 Bulk Manage Users", callback_data="wm:bulk", style="primary")])
        rows.append([InlineKeyboardButton("📋 Pending Payments", callback_data="pp:list:0", style="primary")])
    rows.append([InlineKeyboardButton("📜 Wallet History", callback_data="wm:history", style="primary")])
    rows.append([InlineKeyboardButton("⬅ Back", callback_data="ad:home", style="primary")])
    kb = InlineKeyboardMarkup(rows)
    text = "💰 <b>Wallet Manager</b>\n\nEnter a User ID to manage their wallet, or view recent history."
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def cb_wm_action_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    action = query.data.split(":")[1]
    if action == "setbal" and not await require_owner(update):
        return ConversationHandler.END
    await query.answer()
    context.user_data["wm_action"] = action
    await query.message.reply_text("👤 Enter the target User ID:")
    return UM_SEARCH


async def conv_wm_user_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("❌ Please send a valid numeric User ID.")
        return UM_SEARCH
    target_id = int(text)
    row = await get_user_row(target_id)
    if not row:
        await update.message.reply_text("❌ User not found.")
        return ConversationHandler.END
    context.user_data["wm_target"] = target_id
    action = context.user_data["wm_action"]
    if action == "reset":
        async with db_conn() as db:
            await db.execute("UPDATE wallet SET coins=0 WHERE user_id=?", (target_id,))
            await db.execute(
                "INSERT INTO transactions (user_id, tx_type, amount, description, timestamp) VALUES (?, ?, ?, ?, ?)",
                (target_id, "admin_reset", 0, "Admin reset wallet to 0", datetime.utcnow().isoformat()),
            )
            await db.commit()
        await update.message.reply_text(f"♻ Wallet reset to 0 for user {target_id}.")
        await log_admin_action(context.bot, update.effective_user.id, f"♻ Reset wallet to 0 for user {target_id}")
        return ConversationHandler.END

    if action == "setbal":
        await update.message.reply_text("🎯 Enter the exact balance to set:")
        return UM_AMOUNT

    await update.message.reply_text("🪙 Enter the amount:")
    return UM_AMOUNT


async def conv_wm_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    action = context.user_data.get("wm_action")
    min_ok = 0 if action == "setbal" else 1
    if not text.isdigit() or int(text) < min_ok:
        await update.message.reply_text("❌ Please enter a valid number." if action == "setbal" else "❌ Please enter a valid positive number.")
        return UM_AMOUNT
    amount = int(text)
    target_id = context.user_data.pop("wm_target")
    action = context.user_data.pop("wm_action")

    if action == "setbal":
        async with db_conn() as db:
            await db.execute(
                "INSERT INTO wallet (user_id, coins) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET coins=excluded.coins",
                (target_id, amount),
            )
            await db.execute(
                "INSERT INTO transactions (user_id, tx_type, amount, description, timestamp) VALUES (?, ?, ?, ?, ?)",
                (target_id, "admin_setbal", amount, f"Admin set exact balance to {amount}", datetime.utcnow().isoformat()),
            )
            await db.commit()
        await update.message.reply_text(f"🎯 Balance for user {target_id} set to {amount} coins.")
        await log_admin_action(context.bot, update.effective_user.id, f"🎯 Set exact balance for user {target_id} to {amount} coins")
        return ConversationHandler.END

    if action == "add":
        new_bal = await add_coins(target_id, amount, "admin_gift", f"Admin added {amount} coins")
        await update.message.reply_text(f"✅ Added {amount} coins. New balance: {new_bal}")
        await log_admin_action(context.bot, update.effective_user.id, f"➕ Added {amount} coins to user {target_id}")
        try:
            await context.bot.send_message(target_id, f"🎁 <b>{amount} Coins</b> have been added to your wallet by admin!", parse_mode=ParseMode.HTML)
        except TelegramError:
            pass
    else:
        ok = await remove_coins(target_id, amount, "admin_deduct", f"Admin removed {amount} coins")
        if ok:
            await update.message.reply_text(f"✅ Removed {amount} coins.")
            await log_admin_action(context.bot, update.effective_user.id, f"➖ Removed {amount} coins from user {target_id}")
        else:
            await update.message.reply_text("❌ User doesn't have enough coins to remove that amount.")
    return ConversationHandler.END


async def cb_wm_bulk_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await require_owner(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "👥 <b>Bulk Manage Users</b>\n\n"
        "Send the target users separated by space, comma, or a new line — "
        "numeric User IDs, @usernames, or a mix of both:\n"
        "<code>111 222 @alice @bob</code>",
        parse_mode=ParseMode.HTML,
    )
    return WB_IDS


async def conv_wm_bulk_ids(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip()
    tokens = [tok for tok in re.split(r"[\s,]+", raw) if tok]
    if not tokens:
        await update.message.reply_text("❌ Please send at least one User ID or @username.")
        return WB_IDS

    resolved: list[int] = []
    not_found: list[str] = []

    async with db_conn() as db:
        for tok in tokens:
            if tok.startswith("@"):
                uname = tok.lstrip("@")
                cur = await db.execute("SELECT user_id FROM users WHERE username=?", (uname,))
                row = await cur.fetchone()
                if row:
                    resolved.append(row[0])
                else:
                    not_found.append(tok)
            elif tok.isdigit():
                resolved.append(int(tok))
            else:
                not_found.append(tok)

    if not resolved:
        await update.message.reply_text(
            "❌ None of those could be resolved to a real user. Check the IDs/usernames and try again."
        )
        return WB_IDS

    context.user_data["wb_ids"] = resolved
    msg = f"✅ Resolved <b>{len(resolved)}</b> users.\n\nWhat should be applied to all of them?"
    if not_found:
        msg += f"\n\n⚠️ Not found (skipped): {', '.join(not_found[:15])}"
        if len(not_found) > 15:
            msg += f" (+{len(not_found) - 15} more)"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Coins", callback_data="wb:add", style="primary"),
         InlineKeyboardButton("➖ Remove Coins", callback_data="wb:remove", style="primary")],
        [InlineKeyboardButton("🚫 Ban All", callback_data="wb:ban", style="primary"),
         InlineKeyboardButton("✅ Unban All", callback_data="wb:unban", style="primary")],
        [InlineKeyboardButton("👑 Give Premium", callback_data="wb:premium", style="primary")],
        [InlineKeyboardButton("❌ Cancel", callback_data="wb:cancel", style="primary")],
    ])
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=kb)
    return WB_ACTION


async def cb_wb_action_coins(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["wb_coin_action"] = "add" if query.data == "wb:add" else "remove"
    verb = "add to" if query.data == "wb:add" else "remove from"
    await query.message.reply_text(f"🪙 Enter the coin amount to {verb} all {len(context.user_data['wb_ids'])} users:")
    return WB_AMOUNT


async def conv_wm_bulk_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("❌ Please enter a valid positive number.")
        return WB_AMOUNT
    amount = int(text)
    target_ids = context.user_data.pop("wb_ids")
    action = context.user_data.pop("wb_coin_action")
    is_add = action == "add"

    verb = "Adding" if is_add else "Removing"
    status_msg = await update.message.reply_text(f"⏳ {verb} {amount} coins for {len(target_ids)} users...")
    ok_list: list[int] = []
    skipped: list[int] = []

    async def process_one(uid: int) -> None:
        row = await get_user_row(uid)
        if not row:
            skipped.append(uid)
            return
        if is_add:
            await add_coins(uid, amount, "admin_gift", f"Bulk admin gift: {amount} coins")
            success = True
        else:
            success = await remove_coins(uid, amount, "admin_deduct", f"Bulk admin deduct: {amount} coins")
        if not success:
            skipped.append(uid)
            return
        ok_list.append(uid)
        await TELEGRAM_SEND_LIMITER.acquire()
        try:
            verb_msg = f"🎁 <b>{amount} Coins</b> have been added to your wallet by admin!" if is_add \
                else f"⚠️ <b>{amount} Coins</b> have been deducted from your wallet by admin."
            await context.bot.send_message(uid, verb_msg, parse_mode=ParseMode.HTML)
        except TelegramError:
            pass

    await asyncio.gather(*(process_one(uid) for uid in target_ids))

    past_verb = "Added" if is_add else "Removed"
    result = f"✅ <b>Bulk {past_verb} Complete</b>\n\n🪙 {past_verb} {amount} coins for <b>{len(ok_list)}</b> users."
    if skipped:
        result += f"\n⚠️ Skipped (not found or insufficient balance): {', '.join(str(u) for u in skipped[:20])}"
        if len(skipped) > 20:
            result += f" (+{len(skipped) - 20} more)"
    await status_msg.edit_text(result, parse_mode=ParseMode.HTML)
    return ConversationHandler.END


async def cb_wb_action_ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    target_ids = context.user_data.pop("wb_ids")
    banned = 1 if query.data == "wb:ban" else 0
    async with db_conn() as db:
        for uid in target_ids:
            await db.execute("UPDATE users SET is_banned=? WHERE user_id=?", (banned, uid))
        await db.commit()
    action_word = "Banned" if banned else "Unbanned"
    await query.message.reply_text(f"✅ <b>{action_word}</b> {len(target_ids)} users.", parse_mode=ParseMode.HTML)
    return ConversationHandler.END


async def cb_wb_action_premium_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("7 Days", callback_data="wb:pset:7", style="primary"),
         InlineKeyboardButton("30 Days", callback_data="wb:pset:30", style="primary")],
        [InlineKeyboardButton("90 Days", callback_data="wb:pset:90", style="primary"),
         InlineKeyboardButton("Lifetime", callback_data="wb:pset:0", style="primary")],
    ])
    await query.message.reply_text("👑 Select Premium duration to grant to all selected users:", reply_markup=kb)
    return WB_PREMIUM


async def cb_wb_action_premium_set(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    days = int(query.data.split(":")[2])
    target_ids = context.user_data.pop("wb_ids")
    for uid in target_ids:
        await grant_premium(uid, None if days == 0 else days, update.effective_user.id)
        await TELEGRAM_SEND_LIMITER.acquire()
        try:
            label = "Lifetime" if days == 0 else f"{days} days"
            await context.bot.send_message(
                uid, f"👑 <b>You've been granted Premium!</b>\nDuration: {label}", parse_mode=ParseMode.HTML
            )
        except TelegramError:
            pass
    label = "Lifetime" if days == 0 else f"{days} Days"
    await query.message.reply_text(f"✅ Granted <b>{label}</b> Premium to {len(target_ids)} users.", parse_mode=ParseMode.HTML)
    return ConversationHandler.END


async def cb_wb_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data.pop("wb_ids", None)
    context.user_data.pop("wb_coin_action", None)
    await query.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END



async def cb_pending_payments_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not await require_owner(update):
        return
    await query.answer()
    page = int(query.data.split(":")[2])
    per_page = 10
    async with db_conn() as db:
        total = (await (await db.execute("SELECT COUNT(*) FROM payments WHERE status='pending'")).fetchone())[0]
        cur = await db.execute(
            "SELECT * FROM payments WHERE status='pending' ORDER BY payment_id ASC LIMIT ? OFFSET ?",
            (per_page, page * per_page),
        )
        rows = await cur.fetchall()

    if not rows and page == 0:
        text = "📋 <b>Pending Payments</b>\n\nNothing pending right now — all caught up! ✅"
        buttons = [[InlineKeyboardButton("⬅ Back", callback_data="ad:walletmgr", style="primary")]]
    else:
        text = f"📋 <b>Pending Payments</b>  —  Total: <b>{total}</b>\n\nTap Accept/Reject on any request below:\n"
        buttons = []
        for r in rows:
            urow = await get_user_row(r["user_id"])
            uname = f"@{urow['username']}" if urow and urow["username"] else (urow["first_name"] if urow else f"ID {r['user_id']}")
            text += f"\n👤 <b>{html.escape(uname or str(r['user_id']))}</b> — ⭐{r['stars']} → 🪙{r['coins']} <i>({fmt_date(r['timestamp'])})</i>"
            buttons.append([
                InlineKeyboardButton(f"✅ Accept #{r['payment_id']}", callback_data=f"pay:accept:{r['payment_id']}", style="primary"),
                InlineKeyboardButton(f"❌ Reject #{r['payment_id']}", callback_data=f"pay:reject:{r['payment_id']}", style="primary"),
            ])
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅ Prev", callback_data=f"pp:list:{page-1}", style="primary"))
        if (page + 1) * per_page < total:
            nav.append(InlineKeyboardButton("Next ➡", callback_data=f"pp:list:{page+1}", style="primary"))
        if nav:
            buttons.append(nav)
        buttons.append([InlineKeyboardButton("⬅ Back", callback_data="ad:walletmgr", style="primary")])

    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))


async def cb_wm_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    async with db_conn() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM transactions ORDER BY tx_id DESC LIMIT 15")
        rows = await cur.fetchall()
    if not rows:
        text = "📜 No transactions yet."
    else:
        lines = ["📜 <b>Recent Wallet History</b>\n"]
        for r in rows:
            lines.append(f"👤 <code>{r['user_id']}</code> {'+' if r['amount']>=0 else ''}{r['amount']} — {html.escape(r['tx_type'])}")
        text = "\n".join(lines)
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=back_button("ad:walletmgr"))



async def cb_admin_redeemmgr(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update.effective_user.id):
        if update.callback_query:
            await update.callback_query.answer(ADMIN_ONLY_MSG, show_alert=True)
        else:
            await update.effective_message.reply_text(ADMIN_ONLY_MSG)
        return
    is_owner = update.effective_user.id == OWNER_ID
    rows = [
        [InlineKeyboardButton("➕ Create Code", callback_data="rm:create", style="primary")],
        [InlineKeyboardButton("📋 List Codes", callback_data="rm:list", style="primary")],
        [InlineKeyboardButton("🗑 Delete Code", callback_data="rm:delstart", style="primary")],
    ]
    if is_owner:
        rows.append([InlineKeyboardButton("🗑 Delete ALL Codes", callback_data="rm:delall", style="primary")])
    rows.append([InlineKeyboardButton("⬅ Back", callback_data="ad:home", style="primary")])
    kb = InlineKeyboardMarkup(rows)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("🎟 <b>Redeem Manager</b>", parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await update.effective_message.reply_text("🎟 <b>Redeem Manager</b>", parse_mode=ParseMode.HTML, reply_markup=kb)


async def cb_rm_create_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user_id = update.effective_user.id
    if user_id != OWNER_ID:
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        async with db_conn() as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM redeems WHERE created_by=? AND created_at >= ?",
                (user_id, today_start),
            )
            count_today = (await cur.fetchone())[0]
        if count_today >= ADMIN_DAILY_REDEEM_LIMIT:
            await query.answer(
                f"🚫 Daily limit reached — admins can create up to {ADMIN_DAILY_REDEEM_LIMIT} redeem codes per day.",
                show_alert=True,
            )
            return ConversationHandler.END
    await query.answer()
    await query.message.reply_text("🎟 Enter the redeem code (e.g. WELCOME500):")
    return RM_CODE


async def conv_rm_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    code = update.message.text.strip().upper()
    if not re.match(r"^[A-Z0-9_\-]{3,32}$", code):
        await update.message.reply_text("❌ Invalid code format. Use letters/numbers only (3-32 chars).")
        return RM_CODE
    async with db_conn() as db:
        cur = await db.execute("SELECT 1 FROM redeems WHERE code=?", (code,))
        if await cur.fetchone():
            await update.message.reply_text("❌ This code already exists. Try another.")
            return RM_CODE
    context.user_data["rm_code"] = code
    await update.message.reply_text("💰 Enter Coin Reward (e.g. 500):")
    return RM_COINS


async def conv_rm_coins(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("❌ Please enter a valid number.")
        return RM_COINS
    amount = int(text)
    if update.effective_user.id != OWNER_ID:
        if amount > ADMIN_MAX_REDEEM_COINS or amount < ADMIN_MIN_REDEEM_COINS:
            await update.message.reply_text(
                f"🚫 Admins can only create codes worth between {ADMIN_MIN_REDEEM_COINS}–{ADMIN_MAX_REDEEM_COINS} coins."
            )
            return RM_COINS
    context.user_data["rm_coins"] = amount
    await update.message.reply_text("⭐ Enter Premium Reward in days (0 for none):")
    return RM_PREMIUM


async def conv_rm_premium(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("❌ Please enter a valid number.")
        return RM_PREMIUM
    context.user_data["rm_premium"] = int(text)
    await update.message.reply_text("🔢 Enter Usage Limit (0 for unlimited):")
    return RM_LIMIT


async def conv_rm_limit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("❌ Please enter a valid number.")
        return RM_LIMIT
    context.user_data["rm_limit"] = int(text)
    await update.message.reply_text("📅 Enter Expiry Date (DD-MM-YYYY) or send - for no expiry:")
    return RM_EXPIRY


async def conv_rm_expiry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    expiry_iso = None
    if text != "-":
        try:
            expiry_iso = datetime.strptime(text, "%d-%m-%Y").isoformat()
        except ValueError:
            await update.message.reply_text("❌ Invalid date format. Use DD-MM-YYYY or send -.")
            return RM_EXPIRY

    d = context.user_data
    async with db_conn() as db:
        await db.execute(
            "INSERT INTO redeems (code, coin_reward, premium_days, usage_limit, expiry_date, status, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'active', ?, ?)",
            (d["rm_code"], d["rm_coins"], d["rm_premium"], d["rm_limit"], expiry_iso,
             update.effective_user.id, datetime.utcnow().isoformat()),
        )
        await db.commit()

    await update.message.reply_text(
        f"✅ <b>Redeem Code Created!</b>\n\n🎟 Code: <code>{d['rm_code']}</code>\n💰 Coins: {d['rm_coins']}\n"
        f"⭐ Premium Days: {d['rm_premium']}\n🔢 Usage Limit: {d['rm_limit'] or 'Unlimited'}\n"
        f"📅 Expiry: {text if text != '-' else 'Never'}",
        parse_mode=ParseMode.HTML,
    )
    await log_admin_action(
        context.bot, update.effective_user.id,
        f"🎟 Created redeem code <code>{d['rm_code']}</code> — {d['rm_coins']} coins"
    )
    for k in ["rm_code", "rm_coins", "rm_premium", "rm_limit"]:
        context.user_data.pop(k, None)
    return ConversationHandler.END


async def cb_rm_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    async with db_conn() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM redeems ORDER BY created_at DESC LIMIT 15")
        rows = await cur.fetchall()
    if not rows:
        text = "📋 No redeem codes yet."
    else:
        lines = ["📋 <b>Redeem Codes</b>\n"]
        for r in rows:
            lines.append(
                f"🎟 <code>{r['code']}</code> — {r['coin_reward']}🪙 | "
                f"{r['used_count']}/{r['usage_limit'] or '∞'} used | {r['status']}"
            )
        text = "\n".join(lines)
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=back_button("ad:redeemmgr"))


async def cb_rm_delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "🗑 Enter the exact redeem code to delete:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="rm:delcancel", style="primary")]]),
    )
    return RM_DELETE


async def conv_rm_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    code = update.message.text.strip().upper()
    async with db_conn() as db:
        cur = await db.execute("SELECT 1 FROM redeems WHERE code=?", (code,))
        if not await cur.fetchone():
            await update.message.reply_text("❌ No such code found. Please check and try again, or /cancel.")
            return RM_DELETE
        await db.execute("DELETE FROM redeems WHERE code=?", (code,))
        await db.execute("DELETE FROM redeem_uses WHERE code=?", (code,))
        await db.commit()
    await update.message.reply_text(f"✅ Deleted code: <code>{html.escape(code)}</code>", parse_mode=ParseMode.HTML)
    return ConversationHandler.END


async def cb_rm_delete_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END


async def cb_rm_delete_all_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_owner(update):
        return
    query = update.callback_query
    await query.answer()
    async with db_conn() as db:
        cur = await db.execute("SELECT COUNT(*) FROM redeems")
        count = (await cur.fetchone())[0]
    if count == 0:
        await query.edit_message_text("No redeem codes to delete.", reply_markup=back_button("ad:redeemmgr"))
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Yes, Delete ALL", callback_data="rm:delallconfirm", style="primary"),
         InlineKeyboardButton("❌ No", callback_data="ad:redeemmgr", style="primary")],
    ])
    await query.edit_message_text(
        f"⚠️ <b>Delete ALL {count} Redeem Codes?</b>\n\nThis cannot be undone.",
        parse_mode=ParseMode.HTML, reply_markup=kb,
    )


async def cb_rm_delete_all_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_owner(update):
        return
    query = update.callback_query
    async with db_conn() as db:
        cur = await db.execute("SELECT COUNT(*) FROM redeems")
        count = (await cur.fetchone())[0]
        await db.execute("DELETE FROM redeems")
        await db.execute("DELETE FROM redeem_uses")
        await db.commit()
    await query.answer("🗑 Deleted all codes")
    await query.edit_message_text(f"🗑 <b>Deleted {count} redeem codes.</b>", parse_mode=ParseMode.HTML)
    await log_admin_action(context.bot, update.effective_user.id, f"🗑 Deleted ALL {count} redeem codes")



async def cb_admin_usermgr(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await is_admin(update.effective_user.id):
        if update.callback_query:
            await update.callback_query.answer(ADMIN_ONLY_MSG, show_alert=True)
        else:
            await update.effective_message.reply_text(ADMIN_ONLY_MSG)
        return ConversationHandler.END
    if update.callback_query:
        await update.callback_query.answer()
    await update.effective_message.reply_text("🔍 Enter User ID or @username to search:")
    return UM_SEARCH


async def conv_um_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    async with db_conn() as db:
        db.row_factory = aiosqlite.Row
        if text.isdigit():
            cur = await db.execute("SELECT * FROM users WHERE user_id=?", (int(text),))
        else:
            uname = text.lstrip("@")
            cur = await db.execute("SELECT * FROM users WHERE username=?", (uname,))
        row = await cur.fetchone()

    if not row:
        await update.message.reply_text("❌ User not found.")
        return ConversationHandler.END

    await send_user_profile(update.message, row["user_id"])
    return ConversationHandler.END


async def send_user_profile(message_or_query, target_id: int, edit: bool = False):
    row = await get_user_row(target_id)
    if not row:
        return
    balance = await get_balance(target_id)
    async with db_conn() as db:
        cur = await db.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id=?", (target_id,))
        refs = (await cur.fetchone())[0]
    premium = "⭐ Active" if await is_premium_active(target_id) else "❌ None"
    banned = "🚫 Banned" if row["is_banned"] else "✅ Active"

    text = (
        f"👤 <b>User Profile</b>\n\n"
        f"🆔 ID: <code>{target_id}</code>\n"
        f"📛 Name: {html.escape(row['first_name'] or '—')}\n"
        f"🔗 Username: @{html.escape(row['username']) if row['username'] else '—'}\n"
        f"📅 Joined: {fmt_date(row['join_date'])}\n"
        f"🪙 Wallet: {balance}\n"
        f"👥 Referrals: {refs}\n"
        f"⭐ Premium: {premium}\n"
        f"🔒 Status: {banned}"
    )
    is_owner = message_or_query.from_user.id == OWNER_ID
    rows = [
        [InlineKeyboardButton("➕ Add Coins", callback_data=f"um:addc:{target_id}", style="primary"),
         InlineKeyboardButton("➖ Remove Coins", callback_data=f"um:remc:{target_id}", style="primary")],
    ]
    if is_owner:
        rows.append([InlineKeyboardButton("🚫 Ban", callback_data=f"um:ban:{target_id}", style="primary"),
                     InlineKeyboardButton("✅ Unban", callback_data=f"um:unban:{target_id}", style="primary")])
    rows.append([InlineKeyboardButton("👑 Give Premium", callback_data=f"um:givep:{target_id}", style="primary"),
                 InlineKeyboardButton("❌ Remove Premium", callback_data=f"um:remp:{target_id}", style="primary")])
    kb = InlineKeyboardMarkup(rows)
    if hasattr(message_or_query, "edit_message_text") and edit:
        await message_or_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await message_or_query.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)



async def cb_admin_all_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update.effective_user.id):
        if update.callback_query:
            await update.callback_query.answer(ADMIN_ONLY_MSG, show_alert=True)
        else:
            await update.effective_message.reply_text(ADMIN_ONLY_MSG)
        return
    if update.callback_query:
        await update.callback_query.answer()
    await send_all_users_list(update, context, page=0)


async def send_all_users_list(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int) -> None:
    per_page = 10
    async with db_conn() as db:
        db.row_factory = aiosqlite.Row
        total = (await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        cur = await db.execute(
            "SELECT user_id, username, first_name, is_banned FROM users "
            "ORDER BY join_date DESC LIMIT ? OFFSET ?",
            (per_page, page * per_page),
        )
        rows = await cur.fetchall()

    text = f"👤 <b>All Users</b>\n\n👥 Total Registered: <b>{total}</b>\n\nTap a user to view or manage their profile:"
    buttons = []
    for r in rows:
        label = f"@{r['username']}" if r["username"] else (r["first_name"] or f"ID {r['user_id']}")
        tag = "🚫 " if r["is_banned"] else "👤 "
        buttons.append([InlineKeyboardButton(f"{tag}{label}", callback_data=f"au:view:{r['user_id']}:{page}", style="primary")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅ Prev", callback_data=f"au:list:{page-1}", style="primary"))
    if (page + 1) * per_page < total:
        nav.append(InlineKeyboardButton("Next ➡", callback_data=f"au:list:{page+1}", style="primary"))
    if nav:
        buttons.append(nav)

    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))


async def cb_au_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    page = int(query.data.split(":")[2])
    await send_all_users_list(update, context, page)


async def cb_au_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    target_id = int(query.data.split(":")[2])
    await send_user_profile(query, target_id, edit=True)


async def cb_um_ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_owner(update):
        return
    query = update.callback_query
    action, target_id = query.data.split(":")[1], int(query.data.split(":")[2])
    banned = 1 if action == "ban" else 0
    async with db_conn() as db:
        await db.execute("UPDATE users SET is_banned=? WHERE user_id=?", (banned, target_id))
        await db.commit()
    await query.answer("✅ Updated")
    await log_admin_action(context.bot, update.effective_user.id, f"{'🚫 Banned' if banned else '✅ Unbanned'} user {target_id}")
    await send_user_profile(query, target_id, edit=True)


async def cb_um_premium(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    action, target_id = query.data.split(":")[1], int(query.data.split(":")[2])
    if action == "remp":
        await remove_premium(target_id)
        await query.answer("✅ Premium removed")
        await send_user_profile(query, target_id, edit=True)
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("7 Days", callback_data=f"um:pset:{target_id}:7", style="primary"),
         InlineKeyboardButton("30 Days", callback_data=f"um:pset:{target_id}:30", style="primary")],
        [InlineKeyboardButton("90 Days", callback_data=f"um:pset:{target_id}:90", style="primary"),
         InlineKeyboardButton("Lifetime", callback_data=f"um:pset:{target_id}:0", style="primary")],
        [InlineKeyboardButton("⬅ Back", callback_data=f"um:back:{target_id}", style="primary")],
    ])
    await query.answer()
    await query.edit_message_text("👑 Select Premium Duration:", reply_markup=kb)


async def cb_um_pset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, _, target_id, days = query.data.split(":")
    target_id, days = int(target_id), int(days)
    await grant_premium(target_id, None if days == 0 else days, update.effective_user.id)
    await query.answer("✅ Premium granted")
    try:
        await context.bot.send_message(
            target_id,
            f"👑 <b>You've been granted Premium!</b>\nDuration: {'Lifetime' if days==0 else f'{days} days'}",
            parse_mode=ParseMode.HTML,
        )
    except TelegramError:
        pass
    await log_admin_action(
        context.bot, update.effective_user.id,
        f"👑 Granted {'Lifetime' if days == 0 else f'{days} days'} Premium to user {target_id}"
    )
    await send_user_profile(query, target_id, edit=True)


async def cb_um_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    target_id = int(query.data.split(":")[2])
    await query.answer()
    await send_user_profile(query, target_id, edit=True)


async def cb_um_coins_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    action, target_id = query.data.split(":")[1], int(query.data.split(":")[2])
    context.user_data["um_action"] = "add" if action == "addc" else "remove"
    context.user_data["um_target"] = target_id
    await query.message.reply_text("🪙 Enter the coin amount:")
    return UM_AMOUNT


async def conv_um_coins_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("❌ Please enter a valid positive number.")
        return UM_AMOUNT
    amount = int(text)
    target_id = context.user_data.pop("um_target")
    action = context.user_data.pop("um_action")
    if action == "add":
        await add_coins(target_id, amount, "admin_gift", "Admin added coins via User Manager")
        await update.message.reply_text(f"✅ Added {amount} coins to user {target_id}.")
    else:
        ok = await remove_coins(target_id, amount, "admin_deduct", "Admin removed coins via User Manager")
        await update.message.reply_text(
            f"✅ Removed {amount} coins from user {target_id}." if ok else "❌ Insufficient balance to remove that amount."
        )
    return ConversationHandler.END



async def cb_admin_premiummgr(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update.effective_user.id):
        if update.callback_query:
            await update.callback_query.answer(ADMIN_ONLY_MSG, show_alert=True)
        else:
            await update.effective_message.reply_text(ADMIN_ONLY_MSG)
        return
    if update.callback_query:
        await update.callback_query.answer()
    async with db_conn() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM premium ORDER BY granted_at DESC LIMIT 20")
        rows = await cur.fetchall()
    if not rows:
        text = "👑 <b>Premium Manager</b>\n\nNo premium users yet.\n\nUse 👥 User Manager to grant premium to a specific user."
    else:
        lines = ["👑 <b>Premium Users</b>\n"]
        for r in rows:
            status = "Lifetime" if r["is_lifetime"] else fmt_date(r["expiry_date"])
            lines.append(f"🆔 <code>{r['user_id']}</code> — {status}")
        text = "\n".join(lines)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=back_button("ad:home"))
    else:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)



async def cb_admin_adminmgr(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update.effective_user.id):
        if update.callback_query:
            await update.callback_query.answer(ADMIN_ONLY_MSG, show_alert=True)
        else:
            await update.effective_message.reply_text(ADMIN_ONLY_MSG)
        return
    if update.callback_query:
        await update.callback_query.answer()

    async with db_conn() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM admins ORDER BY added_date")
        rows = await cur.fetchall()

    lines = ["🛡 <b>Admin Manager</b>\n"]
    lines.append(f"👑 Owner: <code>{OWNER_ID}</code>")
    for r in rows:
        if r["user_id"] == OWNER_ID:
            continue
        u = await get_user_row(r["user_id"])
        label = f"@{u['username']}" if u and u["username"] else (u["first_name"] if u else "Unknown")
        role_tag = "🥇 Senior" if r["role"] == "senior" else "🥈 Junior"
        lines.append(f"🛡 <code>{r['user_id']}</code> — {html.escape(label or '—')} ({role_tag})")
    text = "\n".join(lines)

    buttons = []
    is_owner = update.effective_user.id == OWNER_ID
    if is_owner:
        buttons.append([InlineKeyboardButton("➕ Add Admin", callback_data="am:add", style="primary"),
                         InlineKeyboardButton("➖ Remove Admin", callback_data="am:remove", style="primary")])
        buttons.append([InlineKeyboardButton("📜 Action Log", callback_data="am:log:0", style="primary")])
    buttons.append([InlineKeyboardButton("⬅ Back", callback_data="ad:home", style="primary")])
    kb = InlineKeyboardMarkup(buttons)

    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def cb_am_log_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not await require_owner(update):
        return
    await query.answer()
    page = int(query.data.split(":")[2])
    per_page = 10
    async with db_conn() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM admin_logs ORDER BY log_id DESC LIMIT ? OFFSET ?", (per_page, page * per_page)
        )
        rows = await cur.fetchall()

    if not rows and page == 0:
        text = "📜 <b>Admin Action Log</b>\n\nNo actions recorded yet."
    else:
        lines = ["📜 <b>Admin Action Log</b>\n"]
        for r in rows:
            u = await get_user_row(r["actor_id"])
            label = f"@{u['username']}" if u and u["username"] else str(r["actor_id"])
            lines.append(f"👤 {html.escape(label)} — {r['action_text']}\n<i>{fmt_date(r['timestamp'])}</i>")
        text = "\n\n".join(lines)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅ Prev", callback_data=f"am:log:{page-1}", style="primary"))
    if len(rows) == per_page:
        nav.append(InlineKeyboardButton("Next ➡", callback_data=f"am:log:{page+1}", style="primary"))
    buttons = [nav] if nav else []
    buttons.append([InlineKeyboardButton("⬅ Back", callback_data="ad:adminmgr", style="primary")])
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))


async def cb_am_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 Only the owner can add admins.", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    await query.message.reply_text("👤 Send the User ID or @username to promote to admin:")
    return AM_ADD_USER


async def conv_am_add_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    target_id = None
    async with db_conn() as db:
        if text.startswith("@"):
            cur = await db.execute("SELECT user_id FROM users WHERE username=?", (text.lstrip("@"),))
            row = await cur.fetchone()
            if row:
                target_id = row[0]
        elif text.isdigit():
            target_id = int(text)

    if target_id is None:
        await update.message.reply_text(
            "❌ Couldn't find that user. They need to have started the bot at least once. Try again, or /cancel."
        )
        return AM_ADD_USER

    context.user_data["am_target_id"] = target_id
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🥇 Senior Admin", callback_data="am:role:senior", style="primary"),
         InlineKeyboardButton("🥈 Junior Admin", callback_data="am:role:junior", style="primary")],
    ])
    await update.message.reply_text(
        "🛡 Choose their role:\n\n"
        "🥇 <b>Senior</b> — their actions are only reported to you (the Owner).\n"
        "🥈 <b>Junior</b> — their actions are reported to you AND all Senior admins.",
        parse_mode=ParseMode.HTML, reply_markup=kb,
    )
    return AM_ADD_ROLE


async def cb_am_add_role(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    role = query.data.split(":")[2]
    target_id = context.user_data.pop("am_target_id", None)
    if target_id is None:
        await query.message.reply_text("❌ Something went wrong — please start again.")
        return ConversationHandler.END

    async with db_conn() as db:
        await db.execute(
            "INSERT INTO admins (user_id, added_date, role) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET role=excluded.role",
            (target_id, datetime.utcnow().isoformat(), role),
        )
        await db.commit()

    role_label = "Senior Admin 🥇" if role == "senior" else "Junior Admin 🥈"
    await query.message.reply_text(
        f"✅ User <code>{target_id}</code> is now a <b>{role_label}</b>.", parse_mode=ParseMode.HTML
    )
    try:
        await context.bot.send_message(
            target_id, f"🛡 <b>You've been made a {role_label} of this bot!</b>", parse_mode=ParseMode.HTML
        )
    except TelegramError:
        pass
    return ConversationHandler.END


async def cb_am_remove_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 Only the owner can remove admins.", show_alert=True)
        return
    await query.answer()
    async with db_conn() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM admins WHERE user_id != ? ORDER BY added_date", (OWNER_ID,))
        rows = await cur.fetchall()
    if not rows:
        await query.edit_message_text("No sub-admins to remove.", reply_markup=back_button("ad:adminmgr"))
        return
    buttons = []
    for r in rows:
        u = await get_user_row(r["user_id"])
        label = f"@{u['username']}" if u and u["username"] else str(r["user_id"])
        buttons.append([InlineKeyboardButton(f"➖ {label}", callback_data=f"am:rm:{r['user_id']}", style="primary")])
    buttons.append([InlineKeyboardButton("⬅ Back", callback_data="ad:adminmgr", style="primary")])
    await query.edit_message_text("➖ Select an admin to remove:", reply_markup=InlineKeyboardMarkup(buttons))


async def cb_am_remove_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if update.effective_user.id != OWNER_ID:
        await query.answer("🚫 Only the owner can remove admins.", show_alert=True)
        return
    target_id = int(query.data.split(":")[2])
    if target_id == OWNER_ID:
        await query.answer("🚫 Can't remove the owner.", show_alert=True)
        return
    async with db_conn() as db:
        await db.execute("DELETE FROM admins WHERE user_id=?", (target_id,))
        await db.commit()
    await query.answer("✅ Removed")
    await cb_am_remove_menu(update, context)



async def cb_admin_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await is_admin(update.effective_user.id):
        if update.callback_query:
            await update.callback_query.answer(ADMIN_ONLY_MSG, show_alert=True)
        else:
            await update.effective_message.reply_text(ADMIN_ONLY_MSG)
        return ConversationHandler.END
    if update.callback_query:
        await update.callback_query.answer()
    await update.effective_message.reply_text(
        "📢 Send the message you want to broadcast (text, photo, video, animation, sticker, document, voice, or audio).\n\n"
        "Send /cancel to abort."
    )
    return BR_CONTENT


async def conv_broadcast_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.message
    async with db_conn() as db:
        cur = await db.execute("SELECT user_id FROM users")
        all_users = [r[0] for r in await cur.fetchall()]

    status_msg = await msg.reply_text(f"📢 Broadcasting to {len(all_users)} users...")
    counters = {"done": 0, "failed": 0}
    counters_lock = asyncio.Lock()

    async def send_one(uid: int) -> None:
        await TELEGRAM_SEND_LIMITER.acquire()
        try:
            await context.bot.copy_message(chat_id=uid, from_chat_id=msg.chat_id, message_id=msg.message_id)
            async with counters_lock:
                counters["done"] += 1
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after + 0.5)
            try:
                await context.bot.copy_message(chat_id=uid, from_chat_id=msg.chat_id, message_id=msg.message_id)
                async with counters_lock:
                    counters["done"] += 1
            except TelegramError:
                async with counters_lock:
                    counters["failed"] += 1
        except TelegramError:
            async with counters_lock:
                counters["failed"] += 1

    await asyncio.gather(*(send_one(uid) for uid in all_users))

    await status_msg.edit_text(
        f"✅ <b>Broadcast Complete</b>\n\n✅ Sent: {counters['done']}\n❌ Failed: {counters['failed']}",
        parse_mode=ParseMode.HTML,
    )
    await log_admin_action(
        context.bot, update.effective_user.id,
        f"📢 Sent a broadcast — reached {counters['done']}, failed {counters['failed']}"
    )
    return ConversationHandler.END


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    admin = await is_admin(update.effective_user.id)
    await update.message.reply_text("❌ Cancelled.", reply_markup=user_reply_keyboard(admin))
    context.user_data.clear()
    return ConversationHandler.END



async def cb_admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update.effective_user.id):
        if update.callback_query:
            await update.callback_query.answer(ADMIN_ONLY_MSG, show_alert=True)
        else:
            await update.effective_message.reply_text(ADMIN_ONLY_MSG)
        return
    if update.callback_query:
        await update.callback_query.answer()

    async with db_conn() as db:
        total_users = (await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        active_users = (await (await db.execute(
            "SELECT COUNT(*) FROM users WHERE last_seen > ?",
            ((datetime.utcnow() - timedelta(days=7)).isoformat(),)
        )).fetchone())[0]
        premium_users = (await (await db.execute(
            "SELECT COUNT(*) FROM premium WHERE is_lifetime=1 OR expiry_date > ?",
            (datetime.utcnow().isoformat(),)
        )).fetchone())[0]
        total_files = (await (await db.execute("SELECT COUNT(*) FROM files WHERE is_deleted=0")).fetchone())[0]
        total_downloads = (await (await db.execute("SELECT COUNT(*) FROM downloads")).fetchone())[0]
        coins_distributed = (await (await db.execute(
            "SELECT COALESCE(SUM(amount),0) FROM transactions WHERE amount > 0"
        )).fetchone())[0]
        redeem_count = (await (await db.execute("SELECT COUNT(*) FROM redeem_uses")).fetchone())[0]
        referral_count = (await (await db.execute("SELECT COUNT(*) FROM referrals")).fetchone())[0]
        revenue_stars = (await (await db.execute(
            "SELECT COALESCE(SUM(stars),0) FROM payments WHERE status='accepted'"
        )).fetchone())[0]
        pending_payments = (await (await db.execute(
            "SELECT COUNT(*) FROM payments WHERE status='pending'"
        )).fetchone())[0]

    text = (
        "📊 <b>Statistics</b>\n\n"
        f"👥 Total Users: <b>{total_users}</b>\n"
        f"🟢 Active (7d): <b>{active_users}</b>\n"
        f"⭐ Premium Users: <b>{premium_users}</b>\n"
        f"📁 Total Files: <b>{total_files}</b>\n"
        f"⬇ Downloads: <b>{total_downloads}</b>\n"
        f"🪙 Coins Distributed: <b>{coins_distributed}</b>\n"
        f"🎟 Redeem Count: <b>{redeem_count}</b>\n"
        f"🚀 Referral Count: <b>{referral_count}</b>\n"
        f"💰 Revenue: <b>{revenue_stars} ⭐</b>\n"
        f"⏳ Pending Payments: <b>{pending_payments}</b>"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📈 Top Downloaded Files", callback_data="stats:topfiles", style="primary")],
        [InlineKeyboardButton("⬅ Back", callback_data="ad:home", style="primary")],
    ])
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def cb_stats_top_files(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not await is_admin(update.effective_user.id):
        await query.answer(ADMIN_ONLY_MSG, show_alert=True)
        return
    await query.answer()
    async with db_conn() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT file_name, downloads_count, price FROM files WHERE is_deleted=0 "
            "ORDER BY downloads_count DESC LIMIT 10"
        )
        rows = await cur.fetchall()

    if not rows:
        text = "📈 <b>Top Downloaded Files</b>\n\nNo files yet."
    else:
        medals = ["🥇", "🥈", "🥉"]
        lines = ["📈 <b>Top 10 Downloaded Files</b>\n"]
        for i, r in enumerate(rows):
            rank = medals[i] if i < 3 else f"{i+1}."
            tag = "FREE" if r["price"] == 0 else f"{r['price']} coins"
            lines.append(f"{rank} {html.escape(r['file_name'])} — <b>{r['downloads_count']}</b> downloads ({tag})")
        text = "\n".join(lines)
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=back_button("ad:stats"))



async def cb_admin_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update.effective_user.id):
        if update.callback_query:
            await update.callback_query.answer(ADMIN_ONLY_MSG, show_alert=True)
        else:
            await update.effective_message.reply_text(ADMIN_ONLY_MSG)
        return
    current = await get_setting("maintenance", "0")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Enable", callback_data="mt:on", style="primary"),
         InlineKeyboardButton("❌ Disable", callback_data="mt:off", style="primary")],
        [InlineKeyboardButton("⬅ Back", callback_data="ad:home", style="primary")],
    ])
    status = "🚧 ENABLED" if current == "1" else "✅ DISABLED"
    text = f"🛠 <b>Maintenance Mode</b>\n\nCurrent status: <b>{status}</b>"
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def cb_mt_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    val = "1" if query.data == "mt:on" else "0"
    await set_setting("maintenance", val)
    await query.answer("✅ Updated")
    await log_admin_action(context.bot, update.effective_user.id, f"🛠 Maintenance mode turned {'ON' if val == '1' else 'OFF'}")
    await cb_admin_maintenance(update, context)



SETTINGS_FIELDS = {
    "referral_reward": "🚀 Referral Reward (coins)",
    "daily_bonus": "🎁 Daily Bonus (coins)",
    "premium_daily_bonus": "👑 Premium Daily Bonus (coins)",
    "coin_packages": "🪙 Coin Packages (JSON list)",
    "premium_pricing": "👑 Premium Pricing (JSON list)",
    "coin_rate_bdt": "💵 Coins per 1 ₹Rs",
    "bdt_payment_details": "💵 IND Payment Details ",
    "spin_settings": "🎰 Daily Spin Settings (JSON)",
    "mystery_box_pricing": "🎁 Mystery Box Pricing (JSON)",
    "referral_milestones": "🚀 Referral Milestone Rewards (JSON)",
    "streak_rewards": "🔥 Login Streak Rewards (JSON)",
    "star_rate": "⭐ Star Rate",
    "support_username": "💬 Support Username",
    "payment_username": "💰 Payment Username (Stars)",
    "bot_name": "🏷 Bot Name",
    "force_join": "📡 Force Join (1/0)",
}


async def cb_admin_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_owner(update):
        return
    buttons = [[InlineKeyboardButton(label, callback_data=f"set:{key}", style="primary")] for key, label in SETTINGS_FIELDS.items()]
    buttons.append([InlineKeyboardButton("⬅ Back", callback_data="ad:home", style="primary")])
    text = "⚙ <b>Settings</b>\n\nSelect a setting to edit:"
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))


async def cb_settings_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await require_owner(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    key = query.data.split(":")[1]
    current = await get_setting(key, "")
    context.user_data["st_key"] = key
    await query.message.reply_text(
        f"✏️ Current value of <b>{SETTINGS_FIELDS.get(key, key)}</b>:\n<code>{html.escape(current)}</code>\n\n"
        f"Send the new value:",
        parse_mode=ParseMode.HTML,
    )
    return ST_VALUE


async def conv_settings_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    key = context.user_data.pop("st_key")
    value = update.message.text.strip()
    if key == "coin_packages":
        try:
            parsed = json.loads(value)
            assert isinstance(parsed, list)
        except Exception:
            await update.message.reply_text(
                '❌ Invalid JSON. Example: [{"coins": 100, "stars": 3}, {"coins": 500, "stars": 15}]'
            )
            context.user_data["st_key"] = key
            return ST_VALUE
    await set_setting(key, value)
    await update.message.reply_text("✅ Setting updated successfully.")
    return ConversationHandler.END



async def handle_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update.effective_user.id):
        await update.effective_message.reply_text(ADMIN_ONLY_MSG)
        return
    is_owner = update.effective_user.id == OWNER_ID
    await update.effective_message.reply_text(
        f"👑 <b>Admin Panel</b> <i>({BOT_VERSION})</i>\n\nAll admin controls are now available on your keyboard below.\n"
        "Tap <b>⬅️ Back to Main</b> anytime to return to the normal menu.",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_reply_keyboard(is_owner),
    )


async def handle_admin_back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update.effective_user.id):
        return
    await update.effective_message.reply_text(
        "🏠 <b>Main Menu</b>", parse_mode=ParseMode.HTML, reply_markup=user_reply_keyboard(True)
    )


async def cb_admin_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("👑 <b>Admin Panel</b>\n\nSelect an option below:", parse_mode=ParseMode.HTML, reply_markup=admin_panel_keyboard())



async def job_expire_premium(context: ContextTypes.DEFAULT_TYPE) -> None:
    async with db_conn() as db:
        await db.execute(
            "DELETE FROM premium WHERE is_lifetime=0 AND expiry_date IS NOT NULL AND expiry_date < ?",
            (datetime.utcnow().isoformat(),),
        )
        await db.commit()


async def job_expire_redeem_codes(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Auto-deletes redeem codes past their expiry_date, along with their
    usage records, so admins never have to clean these up manually."""
    now = datetime.utcnow().isoformat()
    async with db_conn() as db:
        cur = await db.execute(
            "SELECT code FROM redeems WHERE expiry_date IS NOT NULL AND expiry_date < ?", (now,)
        )
        expired = [r[0] for r in await cur.fetchall()]
        if expired:
            placeholders = ",".join("?" * len(expired))
            await db.execute(f"DELETE FROM redeems WHERE code IN ({placeholders})", expired)
            await db.execute(f"DELETE FROM redeem_uses WHERE code IN ({placeholders})", expired)
            await db.commit()
            log.info("Auto-deleted %d expired redeem code(s)", len(expired))



async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Exception while handling update:", exc_info=context.error)



TEXT_ROUTES = {
    "📁 Buy Files": handle_buy_files,
    "💎 My Wallet": handle_my_wallet,
    "🎰 Daily Spin": handle_daily_spin,
    "🎁 Mystery Box": handle_mystery_box,
    "🎫 Support Ticket": handle_support_ticket_prompt,
    "🎁 Daily Bonus": handle_daily_bonus,
    "🚀 Invite Friends": handle_invite_friends,
    "📊 My Stats": handle_my_stats,
    "🏆 Leaderboard": handle_leaderboard,
    "💬 Support": handle_support,
    "👑 Admin Panel": handle_admin_panel,
    "📂 Manage Files": cb_admin_managefiles,
    "💰 Wallet Manager": cb_admin_walletmgr,
    "🎟 Redeem Manager": cb_admin_redeemmgr,
    "📡 Force Channels": cb_admin_fcmgr,
    "👤 All Users": cb_admin_all_users,
    "👑 Premium Manager": cb_admin_premiummgr,
    "🛡 Admin Manager": cb_admin_adminmgr,
    "📋 Admin List": cb_admin_adminmgr,
    "📊 Statistics": cb_admin_stats,
    "🛠 Maintenance": cb_admin_maintenance,
    "⚙ Settings": cb_admin_settings,
    "⬅️ Back to Main": handle_admin_back_to_main,
}


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None or update.message.text is None:
        return
    if context.user_data is not None and context.user_data.get("awaiting_reject_reason"):
        await handle_reject_reason(update, context)
        return
    text = update.message.text
    handler = TEXT_ROUTES.get(text)
    if handler:
        await handler(update, context)



def build_application() -> Application:
    concurrent_updates = int(os.environ.get("CONCURRENT_UPDATES", "512"))
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .concurrent_updates(concurrent_updates)
        .request(HTTPXRequest(
            connection_pool_size=concurrent_updates,
            pool_timeout=20.0,
            connect_timeout=15.0,
            read_timeout=20.0,
            write_timeout=20.0,
        ))
        .get_updates_request(HTTPXRequest(connection_pool_size=64, pool_timeout=20.0))
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(MessageHandler(filters.ALL, group_restriction_guard), group=-2)
    app.add_handler(CallbackQueryHandler(group_restriction_guard), group=-2)

    app.add_handler(MessageHandler(filters.ALL, flood_guard), group=-1)
    app.add_handler(CallbackQueryHandler(flood_guard), group=-1)

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))

    app.add_handler(CallbackQueryHandler(cb_verify_join, pattern="^verify_join$"))

    app.add_handler(CallbackQueryHandler(cb_wallet_home, pattern="^wallet:home$"))
    app.add_handler(CallbackQueryHandler(cb_wallet_tx, pattern="^wallet:tx:"))
    app.add_handler(CallbackQueryHandler(cb_wallet_buy_premium, pattern="^wallet:buypremium$"))
    app.add_handler(CallbackQueryHandler(handle_buy_coins_bdt, pattern="^wallet:buybdt$"))

    app.add_handler(CallbackQueryHandler(cb_spin_go, pattern="^spin:go$"))
    app.add_handler(CallbackQueryHandler(cb_mystery_open, pattern=r"^mystery:open:\w+:\d+$"))

    ticket_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🎫 Support Ticket$"), handle_support_ticket_prompt)],
        states={
            TK_SUBJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_ticket_subject)],
            TK_BODY: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_ticket_body)],
        },
        fallbacks=[CallbackQueryHandler(cb_ticket_cancel, pattern="^ticket:cancel$"), CommandHandler("cancel", cmd_cancel)],
        per_message=False,
    )
    app.add_handler(ticket_conv)

    ticket_reply_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_ticket_reply_start, pattern=r"^ticket:reply:\d+$")],
        states={TK_REPLY: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_ticket_reply)]},
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        per_message=False,
    )
    app.add_handler(ticket_reply_conv)

    ticket_user_reply_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_ticket_user_reply_start, pattern=r"^ticket:ureply:\d+$")],
        states={TK_USER_REPLY: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_ticket_user_reply)]},
        fallbacks=[CallbackQueryHandler(cb_ticket_cancel, pattern="^ticket:cancel$"), CommandHandler("cancel", cmd_cancel)],
        per_message=False,
    )
    app.add_handler(ticket_user_reply_conv)
    app.add_handler(CallbackQueryHandler(cb_ticket_close, pattern=r"^ticket:close:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_premium_purchase, pattern=r"^premium:buy:\d+:\d+$"))

    buycoins_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_buycoins_paid, pattern="^buycoins:paid$")],
        states={
            BC_STARS: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_buycoins_stars)],
            BC_SCREENSHOT: [MessageHandler((filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND, conv_buycoins_screenshot)],
        },
        fallbacks=[CallbackQueryHandler(cb_buycoins_cancel, pattern="^buycoins:cancel$"), CommandHandler("cancel", cmd_cancel)],
        per_message=False,
    )
    app.add_handler(CallbackQueryHandler(cb_buycoins_start, pattern="^wallet:buycoins$"))
    app.add_handler(buycoins_conv)
    app.add_handler(CallbackQueryHandler(cb_payment_accept, pattern=r"^pay:accept:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_payment_reject, pattern=r"^pay:reject:\d+$"))

    buybdt_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_buybdt_paid, pattern="^buybdt:paid$")],
        states={
            BDT_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_buybdt_amount)],
            BDT_PROOF: [MessageHandler((filters.PHOTO | filters.Document.ALL | filters.TEXT) & ~filters.COMMAND, conv_buybdt_proof)],
        },
        fallbacks=[CallbackQueryHandler(cb_buybdt_cancel, pattern="^buybdt:cancel$"), CommandHandler("cancel", cmd_cancel)],
        per_message=False,
    )
    app.add_handler(buybdt_conv)

    app.add_handler(MessageHandler(filters.Regex("^🎟️ Redeem Code$"), handle_redeem_prompt))
    app.add_handler(CallbackQueryHandler(cb_redeem_cancel, pattern="^redeem:cancel$"))

    redeem_enter_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_redeem_enter_start, pattern="^redeem:enter$")],
        states={RD_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_redeem_code)]},
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        per_message=False,
    )
    app.add_handler(redeem_enter_conv)

    redeem_gift_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_redeem_gift_start, pattern="^redeem:gift$")],
        states={GC_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_gift_amount)]},
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        per_message=False,
    )
    app.add_handler(redeem_gift_conv)

    app.add_handler(CallbackQueryHandler(cb_files_list, pattern=r"^files:list:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_file_view, pattern=r"^file:view:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_file_buy, pattern=r"^file:buy:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_file_download, pattern=r"^file:dl:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_my_purchases, pattern=r"^purchases:\d+$"))

    app.add_handler(CallbackQueryHandler(cb_admin_home, pattern="^ad:home$"))
    app.add_handler(CallbackQueryHandler(cb_admin_managefiles, pattern="^ad:managefiles$"))
    app.add_handler(CallbackQueryHandler(cb_admin_walletmgr, pattern="^ad:walletmgr$"))
    app.add_handler(CallbackQueryHandler(cb_admin_redeemmgr, pattern="^ad:redeemmgr$"))
    app.add_handler(CallbackQueryHandler(cb_admin_fcmgr, pattern="^ad:fcmgr$"))
    app.add_handler(CallbackQueryHandler(cb_admin_premiummgr, pattern="^ad:premiummgr$"))
    app.add_handler(CallbackQueryHandler(cb_admin_adminmgr, pattern="^ad:adminmgr$"))
    app.add_handler(CallbackQueryHandler(cb_am_remove_menu, pattern="^am:remove$"))
    app.add_handler(CallbackQueryHandler(cb_am_log_view, pattern=r"^am:log:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_am_remove_confirm, pattern=r"^am:rm:\d+$"))

    am_add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_am_add_start, pattern="^am:add$")],
        states={
            AM_ADD_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_am_add_user)],
            AM_ADD_ROLE: [CallbackQueryHandler(cb_am_add_role, pattern=r"^am:role:(senior|junior)$")],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        per_message=False,
    )
    app.add_handler(am_add_conv)
    app.add_handler(CallbackQueryHandler(cb_admin_stats, pattern="^ad:stats$"))
    app.add_handler(CallbackQueryHandler(cb_stats_top_files, pattern="^stats:topfiles$"))
    app.add_handler(CallbackQueryHandler(cb_admin_maintenance, pattern="^ad:maintenance$"))
    app.add_handler(CallbackQueryHandler(cb_mt_toggle, pattern="^mt:(on|off)$"))
    app.add_handler(CallbackQueryHandler(cb_admin_settings, pattern="^ad:settings$"))

    upload_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_admin_upload_start, pattern="^ad:upload$"),
            MessageHandler(filters.Regex("^📤 Upload File$"), cb_admin_upload_start),
        ],
        states={
            UP_FILE: [MessageHandler(
                (filters.Document.ALL | filters.VIDEO | filters.AUDIO | filters.PHOTO | filters.VOICE | filters.ANIMATION) & ~filters.COMMAND,
                conv_upload_file
            )],
            UP_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_upload_name)],
            UP_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_upload_desc)],
            UP_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_upload_price)],
            UP_PREVIEW: [
                CallbackQueryHandler(cb_upload_publish, pattern="^up:publish$"),
                CallbackQueryHandler(cb_upload_edit, pattern="^up:edit$"),
                CallbackQueryHandler(cb_upload_cancel, pattern="^up:cancel$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        per_message=False,
    )
    app.add_handler(upload_conv)

    app.add_handler(CallbackQueryHandler(cb_mf_list, pattern=r"^mf:list:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_mf_view, pattern=r"^mf:view:\d+:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_mf_delete, pattern=r"^mf:delete:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_mf_delete_confirm, pattern=r"^mf:delconfirm:\d+$"))

    edit_field_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_mf_edit_field, pattern=r"^mf:(ename|edesc|eprice|etier):\d+$")],
        states={EF_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_edit_field_value)]},
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        per_message=False,
    )
    app.add_handler(edit_field_conv)

    fc_add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_fc_add_start, pattern="^fc:add$")],
        states={
            FC_ADD: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_fc_add)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        per_message=False,
    )
    app.add_handler(fc_add_conv)
    app.add_handler(CallbackQueryHandler(cb_fc_list, pattern="^fc:list$"))
    app.add_handler(CallbackQueryHandler(cb_fc_remove_menu, pattern="^fc:remove$"))
    app.add_handler(CallbackQueryHandler(cb_fc_remove_confirm, pattern=r"^fc:rm:\d+$"))

    wm_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_wm_action_start, pattern="^wm:(add|remove|reset|setbal)$")],
        states={
            UM_SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_wm_user_id)],
            UM_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_wm_amount)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        per_message=False,
    )
    app.add_handler(wm_conv)
    app.add_handler(CallbackQueryHandler(cb_wm_history, pattern="^wm:history$"))

    wb_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_wm_bulk_start, pattern="^wm:bulk$")],
        states={
            WB_IDS: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_wm_bulk_ids)],
            WB_ACTION: [
                CallbackQueryHandler(cb_wb_action_coins, pattern="^wb:(add|remove)$"),
                CallbackQueryHandler(cb_wb_action_ban, pattern="^wb:(ban|unban)$"),
                CallbackQueryHandler(cb_wb_action_premium_menu, pattern="^wb:premium$"),
                CallbackQueryHandler(cb_wb_cancel, pattern="^wb:cancel$"),
            ],
            WB_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_wm_bulk_amount)],
            WB_PREMIUM: [CallbackQueryHandler(cb_wb_action_premium_set, pattern=r"^wb:pset:\d+$")],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        per_message=False,
    )
    app.add_handler(wb_conv)

    app.add_handler(CallbackQueryHandler(cb_pending_payments_list, pattern=r"^pp:list:\d+$"))

    rm_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_rm_create_start, pattern="^rm:create$")],
        states={
            RM_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_rm_code)],
            RM_COINS: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_rm_coins)],
            RM_PREMIUM: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_rm_premium)],
            RM_LIMIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_rm_limit)],
            RM_EXPIRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_rm_expiry)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        per_message=False,
    )
    app.add_handler(rm_conv)
    app.add_handler(CallbackQueryHandler(cb_rm_list, pattern="^rm:list$"))
    app.add_handler(CallbackQueryHandler(cb_rm_delete_all_confirm, pattern="^rm:delall$"))
    app.add_handler(CallbackQueryHandler(cb_rm_delete_all_execute, pattern="^rm:delallconfirm$"))

    rm_delete_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_rm_delete_start, pattern="^rm:delstart$")],
        states={RM_DELETE: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_rm_delete)]},
        fallbacks=[CallbackQueryHandler(cb_rm_delete_cancel, pattern="^rm:delcancel$"), CommandHandler("cancel", cmd_cancel)],
        per_message=False,
    )
    app.add_handler(rm_delete_conv)

    um_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_admin_usermgr, pattern="^ad:usermgr$"),
            MessageHandler(filters.Regex("^👥 User Manager$"), cb_admin_usermgr),
            CallbackQueryHandler(cb_um_coins_start, pattern=r"^um:(addc|remc):\d+$"),
        ],
        states={
            UM_SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_um_search)],
            UM_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_um_coins_amount)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        per_message=False,
    )
    app.add_handler(um_conv)
    app.add_handler(CallbackQueryHandler(cb_um_ban, pattern=r"^um:(ban|unban):\d+$"))
    app.add_handler(CallbackQueryHandler(cb_um_premium, pattern=r"^um:(givep|remp):\d+$"))
    app.add_handler(CallbackQueryHandler(cb_um_pset, pattern=r"^um:pset:\d+:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_um_back, pattern=r"^um:back:\d+$"))

    app.add_handler(CallbackQueryHandler(cb_au_list, pattern=r"^au:list:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_au_view, pattern=r"^au:view:\d+:\d+$"))

    broadcast_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_admin_broadcast_start, pattern="^ad:broadcast$"),
            MessageHandler(filters.Regex("^📢 Broadcast$"), cb_admin_broadcast_start),
        ],
        states={BR_CONTENT: [MessageHandler(
            (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.ANIMATION | filters.Sticker.ALL |
             filters.Document.ALL | filters.VOICE | filters.AUDIO) & ~filters.COMMAND,
            conv_broadcast_content
        )]},
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        per_message=False,
    )
    app.add_handler(broadcast_conv)

    settings_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_settings_field, pattern=r"^set:\w+$")],
        states={ST_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, conv_settings_value)]},
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        per_message=False,
    )
    app.add_handler(settings_conv)

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    app.add_handler(CommandHandler("cancel", cmd_cancel), group=1)

    app.add_error_handler(error_handler)
    return app


async def post_init(app: Application) -> None:
    await init_db()
    await init_db_pool()
    if app.job_queue:
        app.job_queue.run_repeating(job_expire_premium, interval=3600, first=10)
        app.job_queue.run_repeating(job_expire_redeem_codes, interval=3600, first=15)
    log.info("Bot initialized and ready.")


async def post_shutdown(app: Application) -> None:
    await close_db_pool()


# ---------------------------------------------------------------------------
# Hosting support
#
# Normal Python hosts (Render/Railway/etc.) -> Telegram long polling.
# Vercel -> Flask serverless webhook endpoint.
#
# NOTE: Vercel's filesystem is ephemeral. SQLite data in /tmp can disappear
# on a cold start, so permanent production data needs an external database.
# ---------------------------------------------------------------------------

web_app = Flask(__name__)


@web_app.get("/")
def web_home():
    return "RED LUCKY XYZ STORE bot is running."


@web_app.get("/health")
def web_health():
    return "OK"


async def _process_webhook_update(payload: dict) -> None:
    application = build_application()
    await application.initialize()
    await init_db()
    await init_db_pool()

    try:
        update = Update.de_json(payload, application.bot)
        if update is not None:
            await application.process_update(update)
    finally:
        await close_db_pool()
        await application.shutdown()


@web_app.post("/api/telegram")
def telegram_webhook():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return {"ok": False, "error": "Invalid JSON"}, 400

    try:
        asyncio.run(_process_webhook_update(payload))
        return {"ok": True}
    except Exception:
        log.exception("Webhook update failed")
        return {"ok": False, "error": "Internal server error"}, 500


def main() -> None:
    # Vercel imports web_app as the WSGI application.
    if os.environ.get("VERCEL"):
        port = int(os.environ.get("PORT", "8080"))
        web_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
        return

    application = build_application()
    log.info("Starting RED LUCKY XYZ STORE...")
    application.run_polling(
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
