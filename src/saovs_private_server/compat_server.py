from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import smtplib
import sqlite3
import uuid as uuidlib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from html import escape
from pathlib import Path
from re import search, sub
from urllib.parse import parse_qs, quote, unquote, urlparse

from flask import Flask, Response, abort, g, jsonify, redirect, request, send_file
from werkzeug.exceptions import HTTPException

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


app = Flask(__name__)


def load_env_file(path: Path) -> None:
    if not path.is_file():
        return

    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip().lstrip("\ufeff")
        value = value.strip()
        if not key or key in os.environ:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value


def load_local_env() -> None:
    candidates = [
        Path.cwd() / ".env",
        Path(__file__).resolve().parents[2] / ".env",
    ]
    loaded: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in loaded:
            continue
        loaded.add(resolved)
        load_env_file(resolved)


load_local_env()

SERVER_ROOT = Path(os.environ.get("SAOVS_SERVER_ROOT", Path(__file__).resolve().parents[2])).resolve()
LOG_DIR = Path(os.environ.get("SAOVS_LOG_DIR", SERVER_ROOT / "runtime" / "logs")).resolve()
DB_FILE = Path(os.environ.get("SAOVS_DB", SERVER_ROOT / "runtime" / "saovs.sqlite3")).resolve()
DEBUG_AUTH_CODE = os.environ.get("SAOVS_DEFAULT_AUTH_CODE", "LOCAL_TRANSFER_CODE")
DEBUG_SESSION = "debug-local-session"
DEBUG_USER_ID = int(os.environ.get("SAOVS_DEFAULT_USER_ID", "183705490"))
DEBUG_USER_CODE = int(os.environ.get("SAOVS_DEFAULT_USER_CODE", "46841725594"))
DEBUG_TUTORIAL_STEP = os.environ.get("SAOVS_DEFAULT_TUTORIAL_STEP", "999")
DEFAULT_USER_NAME = os.environ.get("SAOVS_DEFAULT_USER_NAME", "Kirito")
BOOTSTRAP_LOGIN_PASSWORD = "adam"
LOG_FILE = LOG_DIR / "saovs_private_server.log"
REQUEST_DUMP_DIR = LOG_DIR / "request_bodies"
ADMIN_STATIC_DIR = Path(__file__).with_name("admin_static")
ADMIN_LOG_READ_BYTES = int(os.environ.get("SAOVS_ADMIN_LOG_READ_BYTES", str(2 * 1024 * 1024)))
ADMIN_LOG_LIMIT = int(os.environ.get("SAOVS_ADMIN_LOG_LIMIT", "250"))
AUTH_CODE_TTL_SECONDS = int(os.environ.get("SAOVS_AUTH_CODE_TTL_SECONDS", "600"))
SESSION_ACTIVE_SECONDS = int(os.environ.get("SAOVS_SESSION_ACTIVE_SECONDS", "1800"))
PASSWORD_HASH_ITERATIONS = int(os.environ.get("SAOVS_PASSWORD_HASH_ITERATIONS", "180000"))
EMAIL_CODE_TTL_SECONDS = int(os.environ.get("SAOVS_EMAIL_CODE_TTL_SECONDS", "300"))
EMAIL_CODE_ATTEMPT_LIMIT = int(os.environ.get("SAOVS_EMAIL_CODE_ATTEMPT_LIMIT", "10"))
EMAIL_CODE_RESEND_SECONDS = int(os.environ.get("SAOVS_EMAIL_CODE_RESEND_SECONDS", "60"))
REQUIRE_EMAIL_VERIFICATION = os.environ.get("SAOVS_REQUIRE_EMAIL_VERIFICATION", "1") != "0"
SMTP_HOST = os.environ.get("SAOVS_SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SAOVS_SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SAOVS_SMTP_USERNAME", "")
SMTP_PASSWORD = os.environ.get("SAOVS_SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SAOVS_SMTP_FROM", SMTP_USERNAME or "noreply@saovs.com")
SMTP_USE_TLS = os.environ.get("SAOVS_SMTP_USE_TLS", "1") != "0"
DEFAULT_LOGIN_USERNAME = os.environ.get("SAOVS_DEFAULT_LOGIN_USERNAME", "adam")
DEFAULT_LOGIN_PASSWORD = os.environ.get("SAOVS_DEFAULT_LOGIN_PASSWORD", BOOTSTRAP_LOGIN_PASSWORD)
ACCOUNT_PAYLOAD_CACHE_VERSION = "account-json-v1"


def resolve_content_root() -> Path:
    configured = os.environ.get("SAOVS_CONTENT_ROOT")
    if configured:
        return Path(configured).resolve()

    candidates = [
        SERVER_ROOT / "content" / "files",
        SERVER_ROOT / "content" / "SAOVS" / "data1" / "com.bandainamcoent.saovsww" / "files",
        SERVER_ROOT / "content" / "SAOVS" / "data1" / "com.bandaicoent.saovswww" / "files",
        SERVER_ROOT.parent / "SAOVS_Project" / "SAOVS" / "data1" / "com.bandainamcoent.saovsww" / "files",
    ]
    for candidate in candidates:
        if (candidate / "sword.db").is_file():
            return candidate.resolve()

    return candidates[0].resolve()


SAVED_ANDROID_FILES = resolve_content_root()


def offline_login_value(name: str, default: str) -> str:
    path = SAVED_ANDROID_FILES / "OfflineApi" / "user_login.json"
    if not path.is_file():
        return default

    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return default

    value = payload.get(name)
    if value is None:
        return default
    return str(value)


SAOVS_OLD_KEY = b"ADrbjQw8UABp9zsBeZjaw7LbMxyfQRZD"
SAOVS_OLD_IV = b"jJkbN3VV9PAUhCLz"
SAOVS_NEW_KEY = b"6d14XUUQ9J1xjshP8u5avnqipObMa3tk"
SAOVS_NEW_IV = b"FJxIPPhFj8o85u9b"
DEFAULT_ASSET_BASE = "https://assets-os-login-lab.saovs.com/"
DEFAULT_ASSET_HOSTS = "assets-os-login-lab.saovs.com,assets-os.saovs.channel.or.jp"
DEFAULT_AUTH_RESULT_ORIGIN = DEFAULT_ASSET_BASE.rstrip("/")

SAOVS_ASSET_BASE = os.environ.get("SAOVS_ASSET_BASE", DEFAULT_ASSET_BASE)
SAOVS_ASSET_VER = os.environ.get("SAOVS_ASSET_VER", offline_login_value("assetver", "30000"))
SAOVS_MASTER_DATA_VER = os.environ.get("SAOVS_MASTER_DATA_VER", offline_login_value("masterver", "202"))
SAOVS_LOCALIZE_DATA_VER = int(os.environ.get("SAOVS_LOCALIZE_DATA_VER", offline_login_value("localizever", "161")))
SAOVS_ADMIN_TOKEN = os.environ.get("SAOVS_ADMIN_TOKEN", "")
SAOVS_ADMIN_USERNAME = os.environ.get("SAOVS_ADMIN_USERNAME", "admin")
SAOVS_ADMIN_PASSWORD = os.environ.get("SAOVS_ADMIN_PASSWORD", "admin")
SAOVS_ASSET_HOSTS = {
    host.strip().lower()
    for host in os.environ.get(
        "SAOVS_ASSET_HOSTS",
        DEFAULT_ASSET_HOSTS,
    ).split(",")
    if host.strip()
}
parsed_asset_host = urlparse(SAOVS_ASSET_BASE).hostname
if parsed_asset_host:
    SAOVS_ASSET_HOSTS.add(parsed_asset_host.lower())
ASSET_FILE_INDEX: dict[str, Path] | None = None
OFFLINE_API_ROUTE_FILES = {
    "ability/index": "ability_index.json",
    "character/index": "character_index.json",
    "equipment/index": "equipment_index.json",
    "greeting/list": "greeting_list.json",
    "home/index": "home_index.json",
    "party/index": "party_index.json",
    "stamp/getuserset": "stamp_getUserSet.json",
    "story/index": "story_index.json",
    "unlimiteddungeon/index": "unlimitedDungeon_index.json",
    "user/login": "user_login.json",
    "user/profile": "user_profile.json",
    "user/quest": "user_quest.json",
}


def setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("saovs_login_debug")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter("%(message)s")

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger


LOGGER = setup_logging()


def emit_log(message: str = "") -> None:
    LOGGER.info(message)


def db_connect() -> sqlite3.Connection:
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_HASH_ITERATIONS,
    )
    return "pbkdf2_sha256${}${}${}".format(
        PASSWORD_HASH_ITERATIONS,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(derived).decode("ascii"),
    )


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iteration_text, salt_text, hash_text = stored_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iteration_text)
        salt = base64.b64decode(salt_text)
        expected = base64.b64decode(hash_text)
    except Exception:
        return False

    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def normalize_login_name(value: str) -> str:
    return value.strip().lower()


def is_valid_email(value: str) -> bool:
    return bool(search(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value.strip()))


def bool_from_db(value: object) -> bool:
    return bool(int(value or 0))


def generate_account_uuid() -> str:
    return str(uuidlib.uuid4())


def generate_user_code(conn: sqlite3.Connection) -> int:
    for _ in range(64):
        user_code = 40_000_000_000 + secrets.randbelow(60_000_000_000)
        row = conn.execute("SELECT 1 FROM users WHERE user_code = ?", (user_code,)).fetchone()
        if row is None:
            return user_code
    raise RuntimeError("Could not allocate a unique user code.")


def ensure_default_account(conn: sqlite3.Connection) -> None:
    now = utc_text()
    conn.execute(
        """
        INSERT OR IGNORE INTO users
            (id, user_code, uuid, user_name, tutorial_step, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            DEBUG_USER_ID,
            DEBUG_USER_CODE,
            "00000000-0000-4000-8000-000000000001",
            DEFAULT_USER_NAME,
            DEBUG_TUTORIAL_STEP,
            now,
            now,
        ),
    )

    login_name = normalize_login_name(DEFAULT_LOGIN_USERNAME)
    if not login_name or not DEFAULT_LOGIN_PASSWORD:
        return

    row = conn.execute("SELECT * FROM login_users WHERE username = ?", (login_name,)).fetchone()
    legacy_login_name = normalize_login_name("adam")
    if row is None and login_name != legacy_login_name:
        legacy_row = conn.execute(
            "SELECT * FROM login_users WHERE username = ? AND user_id = ?",
            (legacy_login_name, DEBUG_USER_ID),
        ).fetchone()
        if legacy_row is not None:
            conn.execute(
                """
                UPDATE login_users
                SET username = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (login_name, now, int(legacy_row["id"])),
            )
            emit_log(f"[AUTH] migrated default login username from {legacy_login_name} to {login_name}")
            row = conn.execute("SELECT * FROM login_users WHERE username = ?", (login_name,)).fetchone()

    if row is None:
        conn.execute(
            """
            INSERT INTO login_users
                (username, password_hash, user_id, display_name, password_change_required, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                login_name,
                hash_password(DEFAULT_LOGIN_PASSWORD),
                DEBUG_USER_ID,
                DEFAULT_USER_NAME,
                1 if DEFAULT_LOGIN_PASSWORD == BOOTSTRAP_LOGIN_PASSWORD else 0,
                now,
                now,
            ),
        )
    elif (
        DEFAULT_LOGIN_PASSWORD == BOOTSTRAP_LOGIN_PASSWORD
        and not bool_from_db(row["password_change_required"])
        and verify_password(BOOTSTRAP_LOGIN_PASSWORD, str(row["password_hash"]))
    ):
        conn.execute(
            "UPDATE login_users SET password_change_required = 1, updated_at = ? WHERE id = ?",
            (now, int(row["id"])),
        )


def init_db() -> None:
    with db_connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_code INTEGER UNIQUE,
                uuid TEXT UNIQUE NOT NULL,
                user_name TEXT NOT NULL,
                tutorial_step TEXT NOT NULL DEFAULT '999',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);

            CREATE TABLE IF NOT EXISTS login_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                user_id INTEGER NOT NULL REFERENCES users(id),
                display_name TEXT NOT NULL,
                password_change_required INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_login_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_login_users_user_id ON login_users(user_id);

            CREATE TABLE IF NOT EXISTS auth_codes (
                code TEXT PRIMARY KEY,
                login_user_id INTEGER NOT NULL REFERENCES login_users(id),
                user_id INTEGER NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                redeemed_at TEXT,
                redeemed_platform_user_id TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_auth_codes_user_id ON auth_codes(user_id);

            CREATE TABLE IF NOT EXISTS email_verification_codes (
                email TEXT PRIMARY KEY,
                code_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_email_verification_codes_expires_at
                ON email_verification_codes(expires_at);

            CREATE TABLE IF NOT EXISTS password_reset_codes (
                email TEXT PRIMARY KEY,
                code_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_password_reset_codes_expires_at
                ON password_reset_codes(expires_at);

            CREATE TABLE IF NOT EXISTS device_links (
                platform_user_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                uuid TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_device_links_user_id ON device_links(user_id);

            CREATE TABLE IF NOT EXISTS account_payloads (
                user_id INTEGER NOT NULL REFERENCES users(id),
                route_key TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, route_key)
            );
            """
        )
        ensure_schema_migrations(conn)
        ensure_default_account(conn)


def ensure_schema_migrations(conn: sqlite3.Connection) -> None:
    login_columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(login_users)").fetchall()
    }
    if "password_change_required" not in login_columns:
        conn.execute(
            "ALTER TABLE login_users ADD COLUMN password_change_required INTEGER NOT NULL DEFAULT 0"
        )


def utc_text(value: datetime | None = None) -> str:
    value = value or datetime.now(timezone.utc)
    return value.strftime("%Y-%m-%d %H:%M:%S")


def row_to_dict(row: sqlite3.Row | None) -> dict[str, object] | None:
    if row is None:
        return None
    return dict(row)


def current_request_uuid(default: str = "00000000-0000-4000-8000-000000000001") -> str:
    frame = current_saovs_request_frame()
    if frame and isinstance(frame.get("value"), list) and frame["value"]:
        header = frame["value"][0]
        if isinstance(header, dict) and isinstance(header.get("uuid"), str) and header["uuid"]:
            return header["uuid"]
    return default


def current_saovs_payload() -> dict[str, object] | None:
    frame = current_saovs_request_frame()
    if not frame or not isinstance(frame.get("value"), list) or len(frame["value"]) < 2:
        return None

    payload = frame["value"][1]
    return payload if isinstance(payload, dict) else None


def current_platform_user_id() -> str:
    payload = current_saovs_payload()
    if payload and isinstance(payload.get("platformUserId"), str):
        return payload["platformUserId"].strip()
    return ""


def get_user_by_uuid(uuid: str) -> dict[str, object] | None:
    with db_connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE uuid = ?", (uuid,)).fetchone()
        return row_to_dict(row)


def get_user_by_id(user_id: int) -> dict[str, object] | None:
    with db_connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return row_to_dict(row)


def get_user_by_platform_user_id(platform_user_id: str) -> dict[str, object] | None:
    if not platform_user_id:
        return None

    with db_connect() as conn:
        row = conn.execute(
            """
            SELECT users.*
            FROM device_links
            JOIN users ON users.id = device_links.user_id
            WHERE device_links.platform_user_id = ?
            """,
            (platform_user_id,),
        ).fetchone()
        return row_to_dict(row)


def get_user_by_session(session: str) -> dict[str, object] | None:
    if not session:
        return None

    now = utc_text()
    with db_connect() as conn:
        row = conn.execute(
            """
            SELECT users.*
            FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token = ?
            """,
            (session,),
        ).fetchone()
        if row:
            conn.execute("UPDATE sessions SET last_seen_at = ? WHERE token = ?", (now, session))
        return row_to_dict(row)


def get_or_create_user(uuid: str, user_name: str | None = None) -> dict[str, object]:
    account_uuid = uuid.strip() if uuid else ""
    existing = get_user_by_uuid(account_uuid) if account_uuid else None
    if existing:
        return existing

    now = utc_text()
    with db_connect() as conn:
        account_uuid = account_uuid or generate_account_uuid()
        user_code = generate_user_code(conn)
        cursor = conn.execute(
            """
            INSERT INTO users
                (user_code, uuid, user_name, tutorial_step, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user_code,
                account_uuid,
                user_name or DEFAULT_USER_NAME,
                DEBUG_TUTORIAL_STEP,
                now,
                now,
            ),
        )
        row = conn.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()
        emit_log(f"[ACCOUNT] created user id={row['id']} user_code={user_code} uuid={account_uuid}")
        return dict(row)


def create_game_account(user_name: str | None = None) -> dict[str, object]:
    return get_or_create_user(generate_account_uuid(), user_name or DEFAULT_USER_NAME)


def link_device_to_user(platform_user_id: str, user_id: int, account_uuid: str | None = None) -> None:
    if not platform_user_id:
        return

    now = utc_text()
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO device_links (platform_user_id, user_id, uuid, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(platform_user_id) DO UPDATE SET
                user_id = excluded.user_id,
                uuid = excluded.uuid,
                updated_at = excluded.updated_at
            """,
            (platform_user_id, user_id, account_uuid or "", now, now),
        )


def current_user() -> dict[str, object]:
    session = current_saovs_header_value("session", "")
    if isinstance(session, str) and session:
        user = get_user_by_session(session)
        if user:
            return user

    platform_user_id = current_platform_user_id()
    if platform_user_id:
        user = get_user_by_platform_user_id(platform_user_id)
        if user:
            return user

    return get_or_create_user(current_request_uuid())


def issue_session(user_id: int) -> str:
    token = "ps-" + secrets.token_urlsafe(32)
    now = utc_text()
    with db_connect() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, last_seen_at) VALUES (?, ?, ?, ?)",
            (token, user_id, now, now),
        )
    return token


def issue_or_reuse_session(user_id: int) -> str:
    request_session = current_saovs_header_value("session", "")
    if isinstance(request_session, str) and request_session:
        user = get_user_by_session(request_session)
        if user and int(user["id"]) == int(user_id):
            return request_session
    return issue_session(user_id)


def create_login_user(username: str, password: str, display_name: str) -> tuple[dict[str, object] | None, str | None]:
    login_name = normalize_login_name(username)
    player_name = (display_name or username or DEFAULT_USER_NAME).strip()[:32] or DEFAULT_USER_NAME
    if not is_valid_email(login_name):
        return None, "Enter a valid email address."
    if len(password) < 4:
        return None, "Password must be at least 4 characters."

    now = utc_text()
    try:
        with db_connect() as conn:
            existing = conn.execute("SELECT 1 FROM login_users WHERE username = ?", (login_name,)).fetchone()
            if existing is not None:
                return None, "That email is already registered."

            account_uuid = generate_account_uuid()
            user_code = generate_user_code(conn)
            cursor = conn.execute(
                """
                INSERT INTO users
                    (user_code, uuid, user_name, tutorial_step, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_code, account_uuid, player_name, DEBUG_TUTORIAL_STEP, now, now),
            )
            user_id = int(cursor.lastrowid)
            conn.execute(
                """
                INSERT INTO login_users
                    (username, password_hash, user_id, display_name, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (login_name, hash_password(password), user_id, player_name, now, now),
            )
            row = conn.execute("SELECT * FROM login_users WHERE username = ?", (login_name,)).fetchone()
            emit_log(f"[AUTH] registered email={login_name} user_id={user_id}")
            return dict(row), None
    except sqlite3.IntegrityError:
        return None, "That email is already registered."


def authenticate_login_user(username: str, password: str) -> dict[str, object] | None:
    login_name = normalize_login_name(username)
    with db_connect() as conn:
        row = conn.execute("SELECT * FROM login_users WHERE username = ?", (login_name,)).fetchone()
        if row is None or not verify_password(password, str(row["password_hash"])):
            return None
        now = utc_text()
        conn.execute(
            "UPDATE login_users SET last_login_at = ?, updated_at = ? WHERE id = ?",
            (now, now, int(row["id"])),
        )
        emit_log(f"[AUTH] login username={login_name} user_id={row['user_id']}")
        row = conn.execute("SELECT * FROM login_users WHERE id = ?", (int(row["id"]),)).fetchone()
        return dict(row)


def generate_email_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def create_email_verification_code(email: str) -> str:
    login_name = normalize_login_name(email)
    code = generate_email_code()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=EMAIL_CODE_TTL_SECONDS)
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO email_verification_codes
                (email, code_hash, created_at, expires_at, attempts)
            VALUES (?, ?, ?, ?, 0)
            ON CONFLICT(email) DO UPDATE SET
                code_hash = excluded.code_hash,
                created_at = excluded.created_at,
                expires_at = excluded.expires_at,
                attempts = 0
            """,
            (login_name, hash_password(code), utc_text(now), utc_text(expires_at)),
        )
    return code


def send_email_code(email: str, code: str, subject: str, purpose: str) -> bool:
    if not SMTP_HOST:
        emit_log(f"[EMAIL DEV] {purpose} code for {email}: {code}")
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = SMTP_FROM
    message["To"] = email
    message.set_content(
        f"Your SAOVS {purpose} code is {code}.\n\n"
        f"It expires in {max(1, EMAIL_CODE_TTL_SECONDS // 60)} minutes."
    )

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as smtp:
            if SMTP_USE_TLS:
                smtp.starttls()
            if SMTP_USERNAME or SMTP_PASSWORD:
                smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
            smtp.send_message(message)
        emit_log(f"[EMAIL] sent {purpose} code to {email}")
        return True
    except Exception as exc:
        emit_log(f"[EMAIL] failed to send {purpose} code to {email}: {exc}")
        emit_log(f"[EMAIL DEV] {purpose} code for {email}: {code}")
        return False


def send_verification_email(email: str, code: str) -> bool:
    return send_email_code(email, code, "SAOVS registration code", "registration")


def send_password_reset_email(email: str, code: str) -> bool:
    return send_email_code(email, code, "SAOVS password reset code", "password reset")


def request_registration_code(email: str) -> tuple[bool, str]:
    login_name = normalize_login_name(email)
    if not is_valid_email(login_name):
        return False, "Enter a valid email address."

    resend_cutoff = utc_text(datetime.now(timezone.utc) - timedelta(seconds=EMAIL_CODE_RESEND_SECONDS))
    with db_connect() as conn:
        existing = conn.execute("SELECT 1 FROM login_users WHERE username = ?", (login_name,)).fetchone()
        recent_code = conn.execute(
            "SELECT created_at FROM email_verification_codes WHERE email = ? AND created_at > ?",
            (login_name, resend_cutoff),
        ).fetchone()
    if existing is not None:
        return False, "That email is already registered."
    if recent_code is not None:
        return False, "Please wait before requesting another code."

    code = create_email_verification_code(login_name)
    sent = send_verification_email(login_name, code)
    if sent:
        return True, "Verification code sent. It expires in 5 minutes."
    return True, "Verification code generated. Check the server log. It expires in 5 minutes."


def verify_registration_code(email: str, code: str) -> tuple[bool, str | None]:
    if not REQUIRE_EMAIL_VERIFICATION:
        return True, None

    login_name = normalize_login_name(email)
    submitted_code = code.strip()
    if not search(r"^\d{6}$", submitted_code):
        return False, "Enter the 6-digit verification code."

    now = utc_text()
    with db_connect() as conn:
        row = conn.execute(
            "SELECT * FROM email_verification_codes WHERE email = ?",
            (login_name,),
        ).fetchone()
        if row is None:
            return False, "Request a verification code first."
        if str(row["expires_at"]) < now:
            conn.execute("DELETE FROM email_verification_codes WHERE email = ?", (login_name,))
            return False, "Verification code expired. Request a new one."
        if int(row["attempts"]) >= EMAIL_CODE_ATTEMPT_LIMIT:
            conn.execute("DELETE FROM email_verification_codes WHERE email = ?", (login_name,))
            return False, "Too many code attempts. Request a new one."
        if not verify_password(submitted_code, str(row["code_hash"])):
            conn.execute(
                "UPDATE email_verification_codes SET attempts = attempts + 1 WHERE email = ?",
                (login_name,),
            )
            return False, "Invalid verification code."

        conn.execute("DELETE FROM email_verification_codes WHERE email = ?", (login_name,))
        return True, None


def create_password_reset_code(email: str) -> str:
    login_name = normalize_login_name(email)
    code = generate_email_code()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=EMAIL_CODE_TTL_SECONDS)
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO password_reset_codes
                (email, code_hash, created_at, expires_at, attempts)
            VALUES (?, ?, ?, ?, 0)
            ON CONFLICT(email) DO UPDATE SET
                code_hash = excluded.code_hash,
                created_at = excluded.created_at,
                expires_at = excluded.expires_at,
                attempts = 0
            """,
            (login_name, hash_password(code), utc_text(now), utc_text(expires_at)),
        )
    return code


def request_password_reset_code(email: str) -> tuple[bool, str]:
    login_name = normalize_login_name(email)
    generic_message = "If that email is registered, a reset code was sent. It expires in 5 minutes."
    if not is_valid_email(login_name):
        return True, generic_message

    resend_cutoff = utc_text(datetime.now(timezone.utc) - timedelta(seconds=EMAIL_CODE_RESEND_SECONDS))
    with db_connect() as conn:
        existing = conn.execute("SELECT 1 FROM login_users WHERE username = ?", (login_name,)).fetchone()
        recent_code = conn.execute(
            "SELECT created_at FROM password_reset_codes WHERE email = ? AND created_at > ?",
            (login_name, resend_cutoff),
        ).fetchone()

    if existing is None:
        return True, generic_message
    if recent_code is not None:
        return False, "Please wait before requesting another code."

    code = create_password_reset_code(login_name)
    send_password_reset_email(login_name, code)
    return True, generic_message


def verify_password_reset_code(email: str, code: str) -> tuple[bool, str | None]:
    login_name = normalize_login_name(email)
    submitted_code = code.strip()
    if not search(r"^\d{6}$", submitted_code):
        return False, "Enter the 6-digit reset code."

    now = utc_text()
    with db_connect() as conn:
        row = conn.execute(
            "SELECT * FROM password_reset_codes WHERE email = ?",
            (login_name,),
        ).fetchone()
        if row is None:
            return False, "Request a reset code first."
        if str(row["expires_at"]) < now:
            conn.execute("DELETE FROM password_reset_codes WHERE email = ?", (login_name,))
            return False, "Reset code expired. Request a new one."
        if int(row["attempts"]) >= EMAIL_CODE_ATTEMPT_LIMIT:
            conn.execute("DELETE FROM password_reset_codes WHERE email = ?", (login_name,))
            return False, "Too many code attempts. Request a new one."
        if not verify_password(submitted_code, str(row["code_hash"])):
            conn.execute(
                "UPDATE password_reset_codes SET attempts = attempts + 1 WHERE email = ?",
                (login_name,),
            )
            return False, "Invalid reset code."

        return True, None


def reset_login_password(username: str, code: str, new_password: str) -> tuple[dict[str, object] | None, str | None]:
    login_name = normalize_login_name(username)
    if len(new_password) < 4:
        return None, "New password must be at least 4 characters."
    if new_password == BOOTSTRAP_LOGIN_PASSWORD:
        return None, "Choose a different password."

    ok, error = verify_password_reset_code(login_name, code)
    if not ok:
        return None, error or "Could not verify reset code."

    with db_connect() as conn:
        row = conn.execute("SELECT * FROM login_users WHERE username = ?", (login_name,)).fetchone()
        if row is None:
            return None, "Invalid email or reset code."

        now = utc_text()
        conn.execute(
            """
            UPDATE login_users
            SET password_hash = ?,
                password_change_required = 0,
                updated_at = ?
            WHERE id = ?
            """,
            (hash_password(new_password), now, int(row["id"])),
        )
        conn.execute("DELETE FROM password_reset_codes WHERE email = ?", (login_name,))
        emit_log(f"[AUTH] reset password username={login_name} user_id={row['user_id']}")
        row = conn.execute("SELECT * FROM login_users WHERE id = ?", (int(row["id"]),)).fetchone()
        return dict(row), None


def delete_player_account(user_id: int) -> tuple[dict[str, object] | None, str | None]:
    if int(user_id) == DEBUG_USER_ID:
        return None, "The bootstrap Adam account cannot be deleted from the dashboard."

    with db_connect() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
        if user is None:
            return None, "Player not found."

        login_rows = conn.execute(
            "SELECT id, username FROM login_users WHERE user_id = ?",
            (int(user_id),),
        ).fetchall()
        login_ids = [int(row["id"]) for row in login_rows]
        login_names = [str(row["username"]) for row in login_rows]
        counts: dict[str, int] = {}

        for table in ("sessions", "device_links", "account_payloads"):
            cursor = conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (int(user_id),))
            counts[table] = cursor.rowcount if cursor.rowcount >= 0 else 0

        auth_count = 0
        for login_id in login_ids:
            cursor = conn.execute("DELETE FROM auth_codes WHERE login_user_id = ?", (login_id,))
            auth_count += cursor.rowcount if cursor.rowcount >= 0 else 0
        counts["auth_codes"] = auth_count

        email_code_count = 0
        reset_code_count = 0
        for login_name in login_names:
            cursor = conn.execute("DELETE FROM email_verification_codes WHERE email = ?", (login_name,))
            email_code_count += cursor.rowcount if cursor.rowcount >= 0 else 0
            cursor = conn.execute("DELETE FROM password_reset_codes WHERE email = ?", (login_name,))
            reset_code_count += cursor.rowcount if cursor.rowcount >= 0 else 0
        counts["email_verification_codes"] = email_code_count
        counts["password_reset_codes"] = reset_code_count

        cursor = conn.execute("DELETE FROM login_users WHERE user_id = ?", (int(user_id),))
        counts["login_users"] = cursor.rowcount if cursor.rowcount >= 0 else 0
        cursor = conn.execute("DELETE FROM users WHERE id = ?", (int(user_id),))
        counts["users"] = cursor.rowcount if cursor.rowcount >= 0 else 0

        emit_log(
            f"[ADMIN] deleted player user_id={int(user_id)} "
            f"user_name={str(user['user_name'])} login_count={len(login_names)}"
        )
        return {
            "userId": int(user_id),
            "userName": str(user["user_name"]),
            "loginNames": login_names,
            "deleted": counts,
        }, None


def issue_auth_code(login_user: dict[str, object]) -> str:
    code = "ps-auth-" + secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=AUTH_CODE_TTL_SECONDS)
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO auth_codes
                (code, login_user_id, user_id, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                code,
                int(login_user["id"]),
                int(login_user["user_id"]),
                utc_text(now),
                utc_text(expires_at),
            ),
        )
    return code


def redeem_auth_code(auth_code: str, platform_user_id: str = "") -> dict[str, object] | None:
    code = (auth_code or "").strip()
    if not code:
        return None

    if code == DEBUG_AUTH_CODE:
        user = get_user_by_id(DEBUG_USER_ID)
        if user:
            link_device_to_user(platform_user_id, int(user["id"]), str(user["uuid"]))
        return user

    now = utc_text()
    with db_connect() as conn:
        row = conn.execute(
            """
            SELECT auth_codes.*, users.uuid
            FROM auth_codes
            JOIN users ON users.id = auth_codes.user_id
            WHERE auth_codes.code = ? AND auth_codes.expires_at >= ?
            """,
            (code, now),
        ).fetchone()
        if row is None:
            emit_log("[AUTH] rejected expired or unknown auth code")
            return None

        conn.execute(
            """
            UPDATE auth_codes
            SET redeemed_at = COALESCE(redeemed_at, ?),
                redeemed_platform_user_id = ?
            WHERE code = ?
            """,
            (now, platform_user_id, code),
        )
        if platform_user_id:
            conn.execute(
                """
                INSERT INTO device_links (platform_user_id, user_id, uuid, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(platform_user_id) DO UPDATE SET
                    user_id = excluded.user_id,
                    uuid = excluded.uuid,
                    updated_at = excluded.updated_at
                """,
                (platform_user_id, int(row["user_id"]), str(row["uuid"]), now, now),
            )

    return get_user_by_id(int(row["user_id"]))


def session_for_response() -> str:
    request_session = current_saovs_header_value("session", "")
    if isinstance(request_session, str) and request_session:
        return request_session

    if current_saovs_request_key() == "new":
        user = current_user()
        return issue_session(int(user["id"]))

    return ""


def is_admin_authorized() -> bool:
    token = request.headers.get("X-Admin-Token") or request.args.get("token") or ""
    if SAOVS_ADMIN_TOKEN and secrets.compare_digest(token, SAOVS_ADMIN_TOKEN):
        return True

    auth = request.authorization
    if auth and secrets.compare_digest(auth.username or "", SAOVS_ADMIN_USERNAME):
        return secrets.compare_digest(auth.password or "", SAOVS_ADMIN_PASSWORD)
    return False


def admin_auth_required_response() -> Response:
    response = Response("Admin authentication required.", status=401)
    response.headers["WWW-Authenticate"] = 'Basic realm="SAOVS Admin", charset="UTF-8"'
    return response


def require_admin() -> None:
    if not is_admin_authorized():
        abort(admin_auth_required_response())


def clamp_int(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def log_file_info() -> dict[str, object]:
    exists = LOG_FILE.is_file()
    size = LOG_FILE.stat().st_size if exists else 0
    return {
        "path": str(LOG_FILE),
        "exists": exists,
        "size": size,
        "maxReadBytes": ADMIN_LOG_READ_BYTES,
        "truncated": size > ADMIN_LOG_READ_BYTES,
    }


def read_log_tail(max_bytes: int = ADMIN_LOG_READ_BYTES) -> str:
    if not LOG_FILE.is_file():
        return ""

    size = LOG_FILE.stat().st_size
    with LOG_FILE.open("rb") as handle:
        if size <= max_bytes:
            return handle.read().decode("utf-8", errors="replace")

        handle.seek(max(0, size - max_bytes))
        text = handle.read().decode("utf-8", errors="replace")
        first_newline = text.find("\n")
        if first_newline >= 0:
            text = text[first_newline + 1 :]
        return f"[LOG TAIL] Showing the last {max_bytes} bytes of {size} total bytes.\n{text}"


def log_entry_category(path: str, raw: str) -> str:
    lower_path = path.lower()
    lower_raw = raw.lower()
    if "[asset]" in lower_raw or is_asset_path(lower_path):
        return "asset"
    if lower_path.startswith("/admin"):
        return "admin"
    if (
        "transfer" in lower_path
        or "bnid" in lower_path
        or "login.html" in lower_path
        or "register.html" in lower_path
        or "change-password.html" in lower_path
        or "reset-password.html" in lower_path
    ):
        return "auth"
    if lower_path.startswith("/api/") or "/api/" in lower_path:
        return "api"
    if "[exception]" in lower_raw or "[http error]" in lower_raw:
        return "error"
    return "server"


def is_asset_path(path: str) -> bool:
    suffixes = (".bundle", ".hash", ".db", ".json", ".png", ".jpg", ".jpeg", ".mp4", "__data")
    return any(path.endswith(suffix) for suffix in suffixes)


def route_short_name(path: str) -> str:
    clean = path.strip("/") or "/"
    if len(clean) <= 52:
        return clean
    return "..." + clean[-49:]


def parse_log_block(lines: list[str], index: int) -> dict[str, object]:
    raw = "\n".join(lines).strip()
    digest = hashlib.sha1(f"{index}:{raw}".encode("utf-8", errors="replace")).hexdigest()[:16]
    entry: dict[str, object] = {
        "id": digest,
        "index": index,
        "timestamp": "",
        "remote": "",
        "method": "",
        "path": "",
        "status": "",
        "category": "server",
        "requestKey": "",
        "responseKey": "",
        "host": "",
        "bodyDump": "",
        "summary": "Server event",
        "preview": raw[:360],
        "detail": raw,
    }

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[TIME] "):
            entry["timestamp"] = stripped.removeprefix("[TIME] ").strip()
        elif stripped.startswith("[REMOTE] "):
            entry["remote"] = stripped.removeprefix("[REMOTE] ").strip()
        elif stripped.startswith("[REQUEST] "):
            match = search(r"^\[REQUEST\]\s+([A-Z]+)\s+(.+)$", stripped)
            if match:
                entry["method"] = match.group(1)
                entry["path"] = match.group(2)
        elif stripped.lower().startswith("host:"):
            entry["host"] = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("[BODY DUMP] "):
            entry["bodyDump"] = stripped.removeprefix("[BODY DUMP] ").strip()
        elif stripped.startswith("[SAOVS FRAME] "):
            match = search(r"'key':\s*'([^']+)'", stripped)
            if match:
                entry["requestKey"] = match.group(1)
        elif stripped.startswith("[SAOVS RESPONSE KEY] "):
            entry["responseKey"] = stripped.removeprefix("[SAOVS RESPONSE KEY] ").strip()
        elif stripped.startswith("[RESPONSE] "):
            match = search(r"^\[RESPONSE\]\s+([A-Z]+)\s+(.+?)\s+->\s+(\d+)", stripped)
            if match:
                entry["method"] = entry["method"] or match.group(1)
                entry["path"] = entry["path"] or match.group(2)
                entry["status"] = match.group(3)
        elif stripped.startswith("[ASSET] serving "):
            entry["summary"] = stripped
        elif stripped.startswith("[ASSET] missing "):
            entry["summary"] = stripped
            entry["status"] = entry["status"] or "404"
        elif stripped.startswith("[HTTP ERROR] "):
            entry["summary"] = stripped
            entry["category"] = "error"
        elif stripped.startswith("[EXCEPTION] "):
            entry["summary"] = stripped
            entry["category"] = "error"

    path = str(entry["path"])
    method = str(entry["method"])
    status = str(entry["status"])
    if path:
        entry["category"] = log_entry_category(path, raw)
        entry["summary"] = f"{method or 'EVENT'} {route_short_name(path)}"
        if status:
            entry["summary"] = f"{entry['summary']} -> {status}"
    elif entry["category"] == "server" and lines:
        entry["summary"] = lines[0].strip()[:90] or "Server event"

    return entry


def parse_log_entries(text: str) -> list[dict[str, object]]:
    marker = "================ SAOVS DEBUG REQUEST ================"
    blocks: list[list[str]] = []
    current: list[str] = []
    standalone: list[str] = []

    for line in text.splitlines():
        if marker in line:
            if current:
                blocks.append(current)
            elif standalone:
                blocks.append(standalone)
                standalone = []
            current = [line]
            continue

        if current:
            current.append(line)
        elif line.strip():
            standalone.append(line)

    if current:
        blocks.append(current)
    elif standalone:
        blocks.append(standalone)

    entries = [parse_log_block(block, index) for index, block in enumerate(blocks, start=1)]
    entries.reverse()
    return entries


def summarize_log_entries(entries: list[dict[str, object]]) -> dict[str, object]:
    status_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    for entry in entries:
        status = str(entry.get("status") or "event")
        category = str(entry.get("category") or "server")
        status_counts[status] = status_counts.get(status, 0) + 1
        category_counts[category] = category_counts.get(category, 0) + 1

    newest = entries[0]["timestamp"] if entries and entries[0].get("timestamp") else ""
    return {
        "total": len(entries),
        "newest": newest,
        "statusCounts": status_counts,
        "categoryCounts": category_counts,
    }


def truncate_server_logs(clear_request_bodies: bool = False) -> int:
    removed_dumps = 0
    formatter = logging.Formatter("%(message)s")
    for handler in list(LOGGER.handlers):
        if isinstance(handler, logging.FileHandler):
            LOGGER.removeHandler(handler)
            handler.close()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    LOG_FILE.write_text("", encoding="utf-8")

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(formatter)
    LOGGER.addHandler(file_handler)

    if clear_request_bodies and REQUEST_DUMP_DIR.is_dir():
        for item in REQUEST_DUMP_DIR.iterdir():
            if item.is_file():
                item.unlink()
                removed_dumps += 1

    return removed_dumps


def load_offline_api_payload(file_name: str) -> dict[str, object] | None:
    path = SAVED_ANDROID_FILES / "OfflineApi" / file_name
    if not path.is_file():
        return None

    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception as exc:
        emit_log(f"[OFFLINE API] failed to read {path}: {exc}")
        return None

    if isinstance(payload, dict):
        return payload

    emit_log(f"[OFFLINE API] ignored non-object payload: {path}")
    return None


def offline_payload_cache_key(file_name: str) -> str:
    path = SAVED_ANDROID_FILES / "OfflineApi" / file_name
    try:
        stat = path.stat()
        stamp = f"{stat.st_size}:{stat.st_mtime_ns}"
    except OSError:
        stamp = "missing"
    return f"{ACCOUNT_PAYLOAD_CACHE_VERSION}:{file_name}:{stamp}"


def account_payload_from_cache(file_name: str, user: dict[str, object]) -> dict[str, object] | None:
    route_key = offline_payload_cache_key(file_name)
    with db_connect() as conn:
        row = conn.execute(
            """
            SELECT payload_json
            FROM account_payloads
            WHERE user_id = ? AND route_key = ?
            """,
            (int(user["id"]), route_key),
        ).fetchone()
        if row:
            try:
                payload = json.loads(str(row["payload_json"]))
                return align_user_identity(payload, user) if isinstance(payload, dict) else None
            except Exception:
                conn.execute(
                    "DELETE FROM account_payloads WHERE user_id = ? AND route_key = ?",
                    (int(user["id"]), route_key),
                )

    base_payload = load_offline_api_payload(file_name)
    if base_payload is None:
        return None

    payload = align_user_identity(base_payload, user)
    now = utc_text()
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO account_payloads (user_id, route_key, payload_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, route_key) DO UPDATE SET
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at
            """,
            (
                int(user["id"]),
                route_key,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                now,
                now,
            ),
        )
    return payload


def align_user_identity(payload: dict[str, object], user: dict[str, object] | None = None) -> dict[str, object]:
    user = user or current_user()
    return align_identity_value(payload, user)


def align_identity_value(value: object, user: dict[str, object]) -> object:
    if isinstance(value, dict):
        aligned: dict[object, object] = {}
        for key, item in value.items():
            if key in {"userId", "playerId"}:
                aligned[key] = int(user["id"])
            elif key == "userCode":
                aligned[key] = int(user["user_code"])
            elif key == "userName":
                aligned[key] = str(user["user_name"])
            elif key == "uuid":
                aligned[key] = str(user["uuid"])
            else:
                aligned[key] = align_identity_value(item, user)
        return aligned

    if isinstance(value, list):
        return [align_identity_value(item, user) for item in value]

    return value


init_db()


def log_request() -> None:
    emit_log("\n================ SAOVS DEBUG REQUEST ================")
    emit_log(f"[TIME] {datetime.now(timezone.utc).isoformat()}")
    emit_log(f"[REMOTE] {request.remote_addr}")
    emit_log(f"[REQUEST] {request.method} {request.path}")
    emit_log(f"[ARGS] {dict(request.args)}")
    emit_log("[HEADERS]")
    for key, value in request.headers.items():
        emit_log(f"  {key}: {value}")

    body = request.get_data(cache=True)
    if body:
        if is_sensitive_form_request():
            emit_log("[BODY] <redacted login form>")
            emit_log("=====================================================\n")
            return
        preview = body[:512]
        emit_log(f"[BODY] {preview!r}")
        emit_log(f"[BODY HEX] {preview.hex(' ')}")
        dump_path = dump_request_body(body)
        emit_log(f"[BODY DUMP] {dump_path}")
        decoded = decode_saovs_body_for_log(body)
        if decoded is not None:
            emit_log(f"[SAOVS FRAME] {decoded!r}")
        if len(body) > len(preview):
            emit_log(f"[BODY] ... truncated, total={len(body)} bytes")
    emit_log("=====================================================\n")


def is_sensitive_form_request() -> bool:
    if request.path not in {
        "/",
        "/login.html",
        "/register.html",
        "/change-password.html",
        "/reset-password.html",
        "/bnid/login",
        "/bnid/login.html",
    }:
        return False
    content_type = request.headers.get("Content-Type", "")
    return "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type


def dump_request_body(body: bytes) -> Path:
    REQUEST_DUMP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    safe_path = sub(r"[^A-Za-z0-9_.-]+", "_", request.path.strip("/") or "root")
    path = REQUEST_DUMP_DIR / f"{timestamp}_{request.method}_{safe_path}.bin"
    path.write_bytes(body)
    return path


def is_asset_host() -> bool:
    host = request.host.split(":", 1)[0].lower()
    return host in SAOVS_ASSET_HOSTS


def asset_content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return "application/json"
    if suffix == ".hash":
        return "text/plain"
    if suffix == ".db":
        return "application/octet-stream"
    return "application/octet-stream"


def build_asset_file_index() -> dict[str, Path]:
    global ASSET_FILE_INDEX

    if ASSET_FILE_INDEX is not None:
        return ASSET_FILE_INDEX

    index: dict[str, Path] = {}
    if not SAVED_ANDROID_FILES.exists():
        emit_log(f"[ASSET] saved Android files directory missing: {SAVED_ANDROID_FILES}")
        ASSET_FILE_INDEX = index
        return index

    for path in SAVED_ANDROID_FILES.rglob("*"):
        if not path.is_file():
            continue

        rel = path.relative_to(SAVED_ANDROID_FILES).as_posix()
        keys = {
            rel.lower(),
            path.name.lower(),
        }

        if path.name == "__data" and path.parent.name:
            cache_hash = path.parent.name.lower()
            keys.add(cache_hash)
            keys.add(f"{cache_hash}.bundle")

        for key in keys:
            index.setdefault(key, path)

    emit_log(f"[ASSET] indexed {len(index)} asset lookup keys from {SAVED_ANDROID_FILES}")
    ASSET_FILE_INDEX = index
    return index


def resolve_asset_file(path: str) -> Path | None:
    clean_path = unquote(path).split("?", 1)[0].replace("\\", "/").lstrip("/")
    parts = [part for part in clean_path.split("/") if part and part != "."]
    variants: list[str] = []

    if parts:
        variants.append("/".join(parts))
        if parts[0].isdigit() and len(parts) > 1:
            variants.append("/".join(parts[1:]))
        variants.append(parts[-1])

        bundle_hash = search(r"([0-9a-fA-F]{32})(?:\.bundle)?$", parts[-1])
        if bundle_hash:
            variants.append(bundle_hash.group(1))
            variants.append(f"{bundle_hash.group(1)}.bundle")

    index = build_asset_file_index()
    for variant in variants:
        matched = index.get(variant.lower())
        if matched and matched.exists():
            return matched

    return None


def serve_asset_file(path: str) -> Response | None:
    if not is_asset_host():
        return None

    asset_path = resolve_asset_file(path)
    if asset_path is None:
        emit_log(f"[ASSET] missing asset file for /{path}")
        return Response("asset not found", status=404, mimetype="text/plain")

    rel = asset_path.relative_to(SAVED_ANDROID_FILES).as_posix()
    emit_log(f"[ASSET] serving /{path} from {rel} ({asset_path.stat().st_size} bytes)")
    return send_file(
        asset_path,
        mimetype=asset_content_type(asset_path),
        as_attachment=False,
        conditional=True,
        etag=True,
        max_age=0,
    )


def pkcs7_unpad(data: bytes) -> bytes:
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(data) + unpadder.finalize()


def pkcs7_pad(data: bytes) -> bytes:
    padder = padding.PKCS7(128).padder()
    return padder.update(data) + padder.finalize()


def aes_cbc_decrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return decryptor.update(data) + decryptor.finalize()


def aes_cbc_encrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return encryptor.update(data) + encryptor.finalize()


def decode_saovs_body(body: bytes) -> dict[str, object] | None:
    try:
        encrypted = base64.b64decode(body, validate=True)
    except Exception:
        return None

    for label, key, iv in (
        ("old", SAOVS_OLD_KEY, SAOVS_OLD_IV),
        ("new", SAOVS_NEW_KEY, SAOVS_NEW_IV),
    ):
        try:
            plain = pkcs7_unpad(aes_cbc_decrypt(encrypted, key, iv))
            value, offset = msgpack_unpack_one(plain, 0)
            if offset != len(plain):
                return {"key": label, "value": value, "trailingBytes": len(plain) - offset}
            return {"key": label, "value": value}
        except Exception:
            continue

    return None


def decode_saovs_body_for_log(body: bytes) -> object | None:
    return decode_saovs_body(body)


def current_saovs_request_frame() -> dict[str, object] | None:
    return decode_saovs_body(request.get_data(cache=True))


def current_saovs_header_value(name: str, default: object = "") -> object:
    frame = current_saovs_request_frame()
    if not frame or not isinstance(frame.get("value"), list) or not frame["value"]:
        return default

    header = frame["value"][0]
    if not isinstance(header, dict):
        return default

    value = header.get(name)
    return default if value is None else value


def current_saovs_request_key(default: str = "old") -> str:
    frame = current_saovs_request_frame()
    if frame and frame.get("key") in {"old", "new"}:
        return str(frame["key"])
    return default


def saovs_crypto_material(label: str) -> tuple[bytes, bytes]:
    if label == "new":
        return SAOVS_NEW_KEY, SAOVS_NEW_IV
    return SAOVS_OLD_KEY, SAOVS_OLD_IV


def compact_msgpack_value(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: compact_msgpack_value(item)
            for key, item in value.items()
            if item is not None
        }

    if isinstance(value, list):
        return [compact_msgpack_value(item) for item in value]

    if isinstance(value, tuple):
        return tuple(compact_msgpack_value(item) for item in value)

    return value


def msgpack_pack(value: object) -> bytes:
    if value is None:
        return b"\xc0"

    if value is False:
        return b"\xc2"

    if value is True:
        return b"\xc3"

    if isinstance(value, int):
        if 0 <= value <= 0x7F:
            return bytes([value])
        if -32 <= value < 0:
            return bytes([0xE0 | (value + 32)])
        if -(1 << 31) <= value < (1 << 31):
            return b"\xd2" + value.to_bytes(4, "big", signed=True)
        return b"\xd3" + value.to_bytes(8, "big", signed=True)

    if isinstance(value, str):
        data = value.encode("utf-8")
        length = len(data)
        if length < 32:
            return bytes([0xA0 | length]) + data
        if length <= 0xFF:
            return b"\xd9" + bytes([length]) + data
        if length <= 0xFFFF:
            return b"\xda" + length.to_bytes(2, "big") + data
        return b"\xdb" + length.to_bytes(4, "big") + data

    if isinstance(value, bytes):
        length = len(value)
        if length <= 0xFF:
            return b"\xc4" + bytes([length]) + value
        if length <= 0xFFFF:
            return b"\xc5" + length.to_bytes(2, "big") + value
        return b"\xc6" + length.to_bytes(4, "big") + value

    if isinstance(value, (list, tuple)):
        length = len(value)
        header = bytes([0x90 | length]) if length < 16 else b"\xdc" + length.to_bytes(2, "big")
        return header + b"".join(msgpack_pack(item) for item in value)

    if isinstance(value, dict):
        length = len(value)
        header = bytes([0x80 | length]) if length < 16 else b"\xde" + length.to_bytes(2, "big")
        return header + b"".join(
            msgpack_pack(key) + msgpack_pack(item)
            for key, item in value.items()
        )

    raise TypeError(f"Unsupported MessagePack value: {type(value).__name__}")


def msgpack_unpack_one(data: bytes, offset: int = 0) -> tuple[object, int]:
    tag = data[offset]
    offset += 1

    if tag <= 0x7F:
        return tag, offset
    if tag >= 0xE0:
        return tag - 0x100, offset
    if 0x80 <= tag <= 0x8F:
        return msgpack_unpack_map(data, offset, tag & 0x0F)
    if 0x90 <= tag <= 0x9F:
        return msgpack_unpack_array(data, offset, tag & 0x0F)
    if 0xA0 <= tag <= 0xBF:
        return msgpack_unpack_str(data, offset, tag & 0x1F)
    if tag == 0xC0:
        return None, offset
    if tag == 0xC2:
        return False, offset
    if tag == 0xC3:
        return True, offset
    if tag == 0xC4:
        length = data[offset]
        offset += 1
        return data[offset:offset + length], offset + length
    if tag == 0xC5:
        length = int.from_bytes(data[offset:offset + 2], "big")
        offset += 2
        return data[offset:offset + length], offset + length
    if tag == 0xCC:
        return data[offset], offset + 1
    if tag == 0xCD:
        return int.from_bytes(data[offset:offset + 2], "big"), offset + 2
    if tag == 0xCE:
        return int.from_bytes(data[offset:offset + 4], "big"), offset + 4
    if tag == 0xCF:
        return int.from_bytes(data[offset:offset + 8], "big"), offset + 8
    if tag == 0xD0:
        return int.from_bytes(data[offset:offset + 1], "big", signed=True), offset + 1
    if tag == 0xD1:
        return int.from_bytes(data[offset:offset + 2], "big", signed=True), offset + 2
    if tag == 0xD2:
        return int.from_bytes(data[offset:offset + 4], "big", signed=True), offset + 4
    if tag == 0xD3:
        return int.from_bytes(data[offset:offset + 8], "big", signed=True), offset + 8
    if tag == 0xD9:
        length = data[offset]
        offset += 1
        return msgpack_unpack_str(data, offset, length)
    if tag == 0xDA:
        length = int.from_bytes(data[offset:offset + 2], "big")
        offset += 2
        return msgpack_unpack_str(data, offset, length)
    if tag == 0xDC:
        length = int.from_bytes(data[offset:offset + 2], "big")
        offset += 2
        return msgpack_unpack_array(data, offset, length)
    if tag == 0xDE:
        length = int.from_bytes(data[offset:offset + 2], "big")
        offset += 2
        return msgpack_unpack_map(data, offset, length)

    raise ValueError(f"Unsupported MessagePack tag 0x{tag:02x}")


def msgpack_unpack_str(data: bytes, offset: int, length: int) -> tuple[str, int]:
    end = offset + length
    return data[offset:end].decode("utf-8"), end


def msgpack_unpack_array(data: bytes, offset: int, length: int) -> tuple[list[object], int]:
    values = []
    for _ in range(length):
        value, offset = msgpack_unpack_one(data, offset)
        values.append(value)
    return values, offset


def msgpack_unpack_map(data: bytes, offset: int, length: int) -> tuple[dict[object, object], int]:
    values = {}
    for _ in range(length):
        key, offset = msgpack_unpack_one(data, offset)
        value, offset = msgpack_unpack_one(data, offset)
        values[key] = value
    return values, offset


def make_saovs_header(status: int = 10000, session: str | None = None) -> dict[str, object]:
    if session is None:
        request_session = current_saovs_header_value("session", "")
        if isinstance(request_session, str) and request_session:
            session = request_session
        elif current_saovs_request_key() == "new":
            session = session_for_response()
        else:
            session = ""

    return {
        "status": status,
        "session": session,
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "assetVer": SAOVS_ASSET_VER,
        "masterDataVer": SAOVS_MASTER_DATA_VER,
        "localizeDataVer": SAOVS_LOCALIZE_DATA_VER,
    }


def make_saovs_frame_response(
    payload: dict[str, object] | None = None,
    footer: dict[str, object] | None = None,
    status: int = 10000,
    session: str | None = None,
    response_key: str | None = None,
) -> Response:
    frame = [
        make_saovs_header(status, session),
        compact_msgpack_value(payload or {}),
        compact_msgpack_value(footer or {}),
    ]
    emit_log(f"[SAOVS RESPONSE FRAME] {frame!r}")
    response_key = response_key or current_saovs_request_key()
    key, iv = saovs_crypto_material(response_key)
    emit_log(f"[SAOVS RESPONSE KEY] {response_key}")
    plain = msgpack_pack(frame)
    encrypted = aes_cbc_encrypt(pkcs7_pad(plain), key, iv)
    encoded = base64.b64encode(encrypted)
    return Response(encoded, mimetype="application/x-msgpack")


def wants_saovs_frame() -> bool:
    if is_asset_host():
        return False

    host = request.host.split(":", 1)[0]
    content_type = request.headers.get("Content-Type", "")
    return (
        host.endswith("saovs.channel.or.jp")
        or "application/x-msgpack" in content_type
        or request.path.startswith("/api/")
    )


@app.after_request
def log_response(response: Response) -> Response:
    if getattr(g, "skip_response_log", False) or request.path.startswith("/admin") or request.path == "/favicon.ico":
        return response
    emit_log(f"[RESPONSE] {request.method} {request.path} -> {response.status_code}")
    return response


@app.errorhandler(Exception)
def log_exception(error: Exception) -> Response | tuple[Response, int]:
    if isinstance(error, HTTPException):
        if request.path.startswith("/admin") and error.response is not None:
            return error.response
        if request.path.startswith("/admin"):
            return jsonify({"statusCode": 10001, "error": error.description}), error.code or 500
        emit_log(f"[HTTP ERROR] {request.method} {request.path} -> {error.code} {error.description}")
        return jsonify({"statusCode": 10001, "error": error.description}), error.code or 500

    LOGGER.exception("[EXCEPTION] Unhandled server error")
    return jsonify({"statusCode": 10001, "error": "debug server exception"}), 500


def puzzle_auth_page(auth_code: str = DEBUG_AUTH_CODE) -> Response:
    auth_result = f"test.html?code={auth_code}"
    safe_code = escape(auth_result, quote=True)
    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>SAOVS BNID Debug Callback</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    html, body {{
      background: #050816;
      color: #dbeafe;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      height: 100%;
      margin: 0;
    }}
    body {{
      align-items: center;
      display: flex;
      justify-content: center;
    }}
    main {{
      text-align: center;
    }}
    .status {{
      color: #7dd3fc;
      font-size: 14px;
      letter-spacing: .08em;
      text-transform: uppercase;
    }}
  </style>
</head>
<body>
  <main>
    <div class="status">Returning to SAOVS</div>
  </main>
  <input type="hidden" id="page_name" value="puzzle_auth_result_page">
  <input type="hidden" id="auth_result" value="{safe_code}">

  <script>
    if (!window.Unity || typeof window.Unity.call !== "function") {{
      window.Unity = {{
        call: function(msg) {{
          var iframe = document.createElement("IFRAME");
          iframe.setAttribute("src", "unity:" + msg);
          document.documentElement.appendChild(iframe);
          iframe.parentNode.removeChild(iframe);
          iframe = null;
        }}
      }};
    }}

    function notifyUnity() {{
      var pageName = document.getElementById("page_name").value;
      if (pageName !== "puzzle_auth_result_page") return;

      var authResult = document.getElementById("auth_result").value;

      try {{
        Unity.call(authResult);
        console.log("[SAOVS DEBUG] Unity.call auth_result:", authResult);
      }} catch (e) {{
        console.log("[SAOVS DEBUG] Unity.call failed:", e);
      }}
    }}

    window.addEventListener("load", function() {{
      notifyUnity();
      setTimeout(notifyUnity, 250);
      setTimeout(notifyUnity, 1000);
      setTimeout(notifyUnity, 2500);
    }});
    setTimeout(notifyUnity, 5000);
  </script>
</body>
</html>
"""
    return Response(html, mimetype="text/html")


def account_page_url(path: str, redirect_uri: str | None = None) -> str:
    if not redirect_uri:
        return path
    return f"{path}?redirect_uri={quote(redirect_uri, safe='')}"


def current_login_action() -> str:
    if request.path in {"/login.html", "/bnid/login", "/bnid/login.html"}:
        return request.path
    return "/login.html"


def render_account_shell(
    title: str,
    intro: str,
    body: str,
    error: str = "",
    message: str = "",
) -> Response:
    safe_title = escape(title, quote=False)
    safe_intro = escape(intro, quote=False)
    safe_error = escape(error, quote=False)
    safe_message = escape(message, quote=False)
    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>{safe_title}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    * {{ box-sizing: border-box; }}
    html, body {{
      min-height: 100%;
      margin: 0;
      background: #07111f;
      color: #eef6ff;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    body {{
      display: grid;
      place-items: center;
      padding: 18px;
    }}
    main {{
      width: min(430px, 100%);
      border: 1px solid rgba(56, 189, 248, 0.28);
      border-radius: 8px;
      background: #101b2b;
      padding: 22px;
      box-shadow: 0 18px 45px rgba(0, 0, 0, 0.36);
    }}
    h1 {{
      margin: 0 0 6px;
      font-size: 24px;
      line-height: 1.2;
    }}
    p {{
      margin: 0 0 18px;
      color: #a8bbd1;
      line-height: 1.45;
    }}
    label {{
      display: block;
      margin: 11px 0 6px;
      color: #cad9ea;
      font-size: 13px;
      font-weight: 700;
    }}
    input {{
      width: 100%;
      min-height: 46px;
      border: 1px solid rgba(148, 163, 184, 0.45);
      border-radius: 8px;
      background: #071120;
      color: #f8fbff;
      font-size: 16px;
      padding: 10px 12px;
    }}
    button, .link-button {{
      align-items: center;
      display: inline-flex;
      justify-content: center;
      width: 100%;
      min-height: 46px;
      border: 0;
      border-radius: 8px;
      background: #31d7ff;
      color: #03111d;
      font-size: 16px;
      font-weight: 800;
      text-decoration: none;
    }}
    button {{
      margin-top: 14px;
    }}
    .button-row {{
      display: grid;
      gap: 10px;
      grid-template-columns: 1fr;
      margin-top: 14px;
    }}
    .button-row button {{
      margin-top: 0;
    }}
    .secondary {{
      background: #26394f;
      border: 1px solid rgba(125, 211, 252, 0.32);
      color: #eef6ff;
    }}
    .links {{
      display: grid;
      gap: 10px;
      grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
      margin-top: 16px;
    }}
    .links.single {{
      grid-template-columns: 1fr;
    }}
    .error, .message {{
      margin-bottom: 14px;
      border-radius: 8px;
      padding: 10px 12px;
      line-height: 1.35;
    }}
    .error {{
      border: 1px solid rgba(255, 109, 116, 0.55);
      background: rgba(255, 109, 116, 0.11);
      color: #ffd3d7;
    }}
    .message {{
      border: 1px solid rgba(52, 211, 153, 0.5);
      background: rgba(52, 211, 153, 0.1);
      color: #c7ffe7;
    }}
    .hint {{
      margin: 8px 0 0;
      color: #9fb4cc;
      font-size: 13px;
    }}
    @media (min-width: 440px) {{
      .button-row {{
        grid-template-columns: 1fr 1fr;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <h1>{safe_title}</h1>
    <p>{safe_intro}</p>
    {f'<div class="error">{safe_error}</div>' if error else ''}
    {f'<div class="message">{safe_message}</div>' if message else ''}
    {body}
  </main>
</body>
</html>
"""
    return Response(html, mimetype="text/html")


def render_login_page(
    redirect_uri: str | None = None,
    error: str = "",
    username: str = "",
) -> Response:
    safe_action = escape(current_login_action(), quote=True)
    safe_redirect = escape(redirect_uri or "", quote=True)
    safe_username = escape(username, quote=True)
    register_href = escape(account_page_url("/register.html", redirect_uri), quote=True)
    reset_href = escape(account_page_url("/reset-password.html", redirect_uri), quote=True)
    body = f"""
    <form method="post" action="{safe_action}">
      <input type="hidden" name="mode" value="login">
      <input type="hidden" name="redirect_uri" value="{safe_redirect}">
      <label for="login-email">Email</label>
      <input id="login-email" name="email" value="{safe_username}" autocomplete="username" required>
      <label for="login-password">Password</label>
      <input id="login-password" name="password" type="password" autocomplete="current-password" required>
      <button type="submit">Login</button>
    </form>
    <div class="links">
      <a class="link-button secondary" href="{register_href}">Create Account</a>
      <a class="link-button secondary" href="{reset_href}">Reset Password</a>
    </div>
"""
    return render_account_shell("SAOVS Login", "Use your private-server account to continue.", body, error=error)


def render_register_page(
    redirect_uri: str | None = None,
    error: str = "",
    message: str = "",
    email: str = "",
    player_name: str = "",
) -> Response:
    safe_action = escape(account_page_url("/register.html", redirect_uri), quote=True)
    safe_redirect = escape(redirect_uri or "", quote=True)
    safe_email = escape(email, quote=True)
    safe_player_name = escape(player_name or "", quote=True)
    login_href = escape(account_page_url("/login.html", redirect_uri), quote=True)
    verification_fields = ""
    send_code_button = ""
    if REQUIRE_EMAIL_VERIFICATION:
        verification_fields = """
      <label for="register-code">6-Digit Code</label>
      <input id="register-code" name="verification_code" inputmode="numeric" pattern="[0-9]{6}" maxlength="6">
"""
        send_code_button = '<button class="secondary" type="submit" name="mode" value="send_code">Send Code</button>'

    body = f"""
    <form method="post" action="{safe_action}">
      <input type="hidden" name="redirect_uri" value="{safe_redirect}">
      <label for="register-email">Email</label>
      <input id="register-email" name="email" value="{safe_email}" type="email" autocomplete="email" required>
      {verification_fields}
      <label for="register-password">Password</label>
      <input id="register-password" name="password" type="password" autocomplete="new-password">
      <label for="player-name">Player Name</label>
      <input id="player-name" name="player_name" value="{safe_player_name}" maxlength="32">
      <div class="button-row">
        {send_code_button}
        <button type="submit" name="mode" value="register">Create Account</button>
      </div>
    </form>
    <div class="links single">
      <a class="link-button secondary" href="{login_href}">Back To Login</a>
    </div>
"""
    return render_account_shell(
        "Create Account",
        "Register with email verification before entering the game.",
        body,
        error=error,
        message=message,
    )


def render_reset_password_page(
    redirect_uri: str | None = None,
    error: str = "",
    message: str = "",
    email: str = "",
) -> Response:
    safe_action = escape(account_page_url("/reset-password.html", redirect_uri), quote=True)
    safe_redirect = escape(redirect_uri or "", quote=True)
    safe_email = escape(email, quote=True)
    login_href = escape(account_page_url("/login.html", redirect_uri), quote=True)
    body = f"""
    <form method="post" action="{safe_action}">
      <input type="hidden" name="redirect_uri" value="{safe_redirect}">
      <label for="reset-email">Email</label>
      <input id="reset-email" name="email" value="{safe_email}" type="email" autocomplete="email" required>
      <label for="reset-code">6-Digit Code</label>
      <input id="reset-code" name="reset_code" inputmode="numeric" pattern="[0-9]{{6}}" maxlength="6">
      <label for="reset-password">New Password</label>
      <input id="reset-password" name="new_password" type="password" autocomplete="new-password">
      <div class="button-row">
        <button class="secondary" type="submit" name="mode" value="send_reset_code">Send Code</button>
        <button type="submit" name="mode" value="reset_password">Reset Password</button>
      </div>
    </form>
    <div class="links single">
      <a class="link-button secondary" href="{login_href}">Back To Login</a>
    </div>
"""
    return render_account_shell(
        "Reset Password",
        "Use an email code to set a new password.",
        body,
        error=error,
        message=message,
    )


def render_bnid_login_page(
    redirect_uri: str | None = None,
    error: str = "",
    username: str = "",
    player_name: str = "",
    require_password_change: bool = False,
) -> Response:
    if require_password_change:
        return render_reset_password_page(redirect_uri, error, email=username)
    return render_login_page(redirect_uri, error, username)


def handle_bnid_login_post() -> Response:
    mode = request.form.get("mode", "login")
    if mode in {"register", "send_code"}:
        return handle_register_post()
    if mode in {"send_reset_code", "reset_password"}:
        return handle_reset_password_post()

    redirect_uri = request.form.get("redirect_uri") or request.args.get("redirect_uri")
    username = request.form.get("email") or request.form.get("username", "")
    password = request.form.get("password", "")

    login_user = authenticate_login_user(username, password)
    if login_user is None:
        return render_login_page(redirect_uri, "Invalid email or password.", username)
    if bool_from_db(login_user.get("password_change_required")):
        return render_reset_password_page(
            redirect_uri,
            "Reset this password by email before continuing.",
            email=username,
        )

    auth_code = issue_auth_code(login_user)
    target = local_auth_result_url(auth_code, redirect_uri)
    emit_log(f"[AUTH] returning auth_result for login={normalize_login_name(username)} user_id={login_user['user_id']}")
    return redirect(target, code=302)


def handle_register_post() -> Response:
    redirect_uri = request.form.get("redirect_uri") or request.args.get("redirect_uri")
    email = request.form.get("email") or request.form.get("username", "")
    login_email = normalize_login_name(email)
    password = request.form.get("password", "")
    player_name = request.form.get("player_name", "")
    mode = request.form.get("mode", "register")

    if mode == "send_code":
        ok, message = request_registration_code(email)
        if ok:
            return render_register_page(redirect_uri, message=message, email=login_email, player_name=player_name)
        return render_register_page(redirect_uri, error=message, email=email, player_name=player_name)

    if not is_valid_email(login_email):
        return render_register_page(redirect_uri, error="Enter a valid email address.", email=email, player_name=player_name)
    if len(password) < 4:
        return render_register_page(redirect_uri, error="Password must be at least 4 characters.", email=login_email, player_name=player_name)
    with db_connect() as conn:
        existing = conn.execute("SELECT 1 FROM login_users WHERE username = ?", (login_email,)).fetchone()
    if existing is not None:
        return render_register_page(redirect_uri, error="That email is already registered.", email=login_email, player_name=player_name)

    if REQUIRE_EMAIL_VERIFICATION:
        ok, error = verify_registration_code(email, request.form.get("verification_code", ""))
        if not ok:
            return render_register_page(redirect_uri, error=error or "Could not verify email.", email=email, player_name=player_name)

    display_name = player_name or login_email.split("@", 1)[0] or DEFAULT_USER_NAME
    login_user, error = create_login_user(login_email, password, display_name)
    if error or login_user is None:
        return render_register_page(redirect_uri, error=error or "Could not create account.", email=login_email, player_name=player_name)

    auth_code = issue_auth_code(login_user)
    target = local_auth_result_url(auth_code, redirect_uri)
    emit_log(f"[AUTH] returning auth_result for login={login_email} user_id={login_user['user_id']}")
    return redirect(target, code=302)


def handle_reset_password_post() -> Response:
    redirect_uri = request.form.get("redirect_uri") or request.args.get("redirect_uri")
    email = request.form.get("email") or request.form.get("username", "")
    login_email = normalize_login_name(email)
    mode = request.form.get("mode", "reset_password")

    if mode == "send_reset_code":
        ok, message = request_password_reset_code(login_email)
        if ok:
            return render_reset_password_page(redirect_uri, message=message, email=login_email)
        return render_reset_password_page(redirect_uri, error=message, email=login_email)

    new_password = request.form.get("new_password", "")
    reset_code = request.form.get("reset_code", "")
    login_user, error = reset_login_password(login_email, reset_code, new_password)
    if error or login_user is None:
        return render_reset_password_page(redirect_uri, error=error or "Could not reset password.", email=login_email)

    auth_code = issue_auth_code(login_user)
    target = local_auth_result_url(auth_code, redirect_uri)
    emit_log(f"[AUTH] returning auth_result after password reset for login={login_email} user_id={login_user['user_id']}")
    return redirect(target, code=302)


def local_auth_result_url(auth_code: str = DEBUG_AUTH_CODE, redirect_uri: str | None = None) -> str:
    if redirect_uri:
        redirect_uri = redirect_uri.strip()
        separator = "&" if "?" in redirect_uri else "?"
        if is_relative_bnid_redirect(redirect_uri):
            path = redirect_uri if redirect_uri.startswith("/") else f"/{redirect_uri}"
            separator = "&" if "?" in path else "?"
            origin = os.environ.get(
                "SAOVS_RELATIVE_AUTH_RESULT_ORIGIN",
                os.environ.get("SAOVS_AUTH_RESULT_ORIGIN", DEFAULT_AUTH_RESULT_ORIGIN),
            ).rstrip("/")
            return f"{origin}{path}{separator}code={quote(auth_code, safe='')}"
        return f"{redirect_uri}{separator}code={quote(auth_code, safe='')}"

    origin = os.environ.get("SAOVS_AUTH_RESULT_ORIGIN", DEFAULT_AUTH_RESULT_ORIGIN).rstrip("/")
    return f"{origin}/test.html?code={quote(auth_code, safe='')}"


def is_relative_bnid_redirect(redirect_uri: str | None) -> bool:
    if not redirect_uri:
        return False

    lowered = redirect_uri.strip().lower()
    return (
        lowered == "test.html"
        or lowered.startswith("test.html?")
        or lowered == "/test.html"
        or lowered.startswith("/test.html?")
    )


@app.route("/", methods=["GET"])
def index() -> Response:
    log_request()
    original = request.args.get("original", "")
    if original:
        decoded_original = unquote(original)
        query = parse_qs(urlparse(decoded_original).query)
        redirect_uri = request.args.get("redirect_uri") or (query.get("redirect_uri") or [""])[0]
        emit_log(f"[LOCAL LOGIN] Rendering account login for original: {decoded_original}")
        return render_bnid_login_page(redirect_uri)

    original_text = escape(unquote(original), quote=False)
    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>SAOVS BNID Debug Login</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
</head>
<body>
  <h1>SAOVS BNID Debug Login</h1>
  <p>Original URL:</p>
  <pre>{original_text}</pre>
  <form method="get" action="/callback">
    <input name="auth_code" value="{escape(DEBUG_AUTH_CODE, quote=True)}">
    <button type="submit">Return auth_result</button>
  </form>
</body>
</html>
"""
    return Response(html, mimetype="text/html")


@app.route("/callback", methods=["GET"])
@app.route("/test.html", methods=["GET"])
def callback() -> Response:
    log_request()
    auth_code = (
        request.args.get("auth_code")
        or request.args.get("code")
        or request.args.get("authenticationCode")
        or request.args.get("access_token")
        or DEBUG_AUTH_CODE
    )
    return puzzle_auth_page(auth_code)


@app.route("/login.html", methods=["GET", "POST"])
def login_html() -> Response:
    log_request()
    redirect_uri = request.args.get("redirect_uri")
    if request.method == "POST":
        return handle_bnid_login_post()
    if is_relative_bnid_redirect(redirect_uri):
        emit_log(f"[LOCAL LOGIN] Relative redirect_uri={redirect_uri!r}; rendering account login page")
    return render_bnid_login_page(redirect_uri)


@app.route("/register.html", methods=["GET", "POST"])
def register_html() -> Response:
    log_request()
    redirect_uri = request.args.get("redirect_uri")
    if request.method == "POST":
        return handle_register_post()
    return render_register_page(redirect_uri)


@app.route("/change-password.html", methods=["GET", "POST"])
def legacy_change_password_html() -> Response:
    log_request()
    redirect_uri = request.form.get("redirect_uri") or request.args.get("redirect_uri")
    return redirect(account_page_url("/reset-password.html", redirect_uri), code=302)


@app.route("/reset-password.html", methods=["GET", "POST"])
def reset_password_html() -> Response:
    log_request()
    redirect_uri = request.args.get("redirect_uri")
    if request.method == "POST":
        return handle_reset_password_post()
    return render_reset_password_page(redirect_uri)


@app.route("/bnid/login", methods=["GET", "POST"])
@app.route("/bnid/login.html", methods=["GET", "POST"])
def bnid_login_html() -> Response:
    log_request()
    redirect_uri = request.args.get("redirect_uri")
    if request.method == "POST":
        return handle_bnid_login_post()
    if is_relative_bnid_redirect(redirect_uri):
        emit_log(f"[BNID LOGIN] Relative redirect_uri={redirect_uri!r}; rendering account login page")
    return render_bnid_login_page(redirect_uri)


@app.route("/bnid/callback", methods=["GET"])
@app.route("/bnid/test.html", methods=["GET"])
def bnid_callback() -> Response:
    log_request()
    auth_code = (
        request.args.get("auth_code")
        or request.args.get("code")
        or request.args.get("authenticationCode")
        or request.args.get("access_token")
        or DEBUG_AUTH_CODE
    )
    emit_log(f"[BNID TEST CALLBACK] auth_code={auth_code}")
    return puzzle_auth_page(auth_code)


@app.route("/api/user/checkVersion", methods=["GET", "POST"])
@app.route("/user/checkVersion", methods=["GET", "POST"])
def user_check_version() -> Response:
    log_request()
    return make_saovs_frame_response(
        {
            "needRedirect": False,
            "redirectUrl": "",
            "assetUrl": SAOVS_ASSET_BASE,
            "staticFileUrl": SAOVS_ASSET_BASE,
            "movieUrl": SAOVS_ASSET_BASE,
        }
    )


def make_user_login_payload(user: dict[str, object] | None = None) -> dict[str, object]:
    user = user or current_user()
    offline = account_payload_from_cache("user_login.json", user)
    if offline is not None:
        return offline

    return {
        "callLoginBonus": False,
        "isResume": 0,
        "userId": int(user["id"]),
        "userCode": int(user["user_code"]),
        "altSvc": "",
        "userName": str(user["user_name"]),
        "isConsent": True,
        "isReleaseTimerBan": False,
        "expiredGashaExchangePointPreNotice": [],
        "gashaExchangePointFlag": False,
        "nonce": "local-private-nonce",
        "eventNoticeInfo": [],
        "tutorialStep": str(user["tutorial_step"]),
    }


@app.route("/api/user/login", methods=["GET", "POST"])
@app.route("/user/login", methods=["GET", "POST"])
def user_login() -> Response:
    log_request()
    user = current_user()
    platform_user_id = current_platform_user_id()
    if platform_user_id:
        link_device_to_user(platform_user_id, int(user["id"]), str(user["uuid"]))
    session = issue_or_reuse_session(int(user["id"]))
    payload = make_user_login_payload(user)
    if wants_saovs_frame():
        return make_saovs_frame_response(payload, session=session)

    return jsonify(
        {
            "statusCode": 10000,
            "session": session,
            "response": payload,
        }
    )


def debug_datetime() -> str:
    return "2026-05-09 00:00:00"


def make_user_growth_info() -> dict[str, object]:
    return {
        "hpStage": 0,
        "strStage": 0,
        "intStage": 0,
        "vitStage": 0,
        "mndStage": 0,
        "cooltimeStage": 0,
        "criticalRateStage": 0,
        "criticalDamageStage": 0,
        "speedStage": 0,
        "totalPoint": 0,
    }


def make_user_info(user: dict[str, object] | None = None) -> dict[str, object]:
    user = user or current_user()
    home = account_payload_from_cache("home_index.json", user)
    if home and isinstance(home.get("userInfo"), dict):
        return align_user_identity(home["userInfo"], user)

    return {
        "userId": int(user["id"]),
        "userCode": int(user["user_code"]),
        "userName": str(user["user_name"]),
        "exp": 0,
        "xr": 100,
        "xrExp": 999265,
        "goldNum": 0,
        "freeStone": 0,
        "paidStone": 0,
        "titleCode": 0,
        "symbolIconCode": 0,
        "abilityCardSum": 0,
        "abilityCardMax": 500,
        "equipmentNumMax": 500,
        "growthInfo": make_user_growth_info(),
        "favoriteCharacterCode": 0,
        "limitQuestPoint": 0,
        "tutorialEndAt": debug_datetime(),
        "comeBackAt": "",
    }


def make_profile_quest_info() -> dict[str, object]:
    return {
        "latestMainQuestCode": 0,
        "latestChallengeQuestCode": 0,
        "extraQuestClearCount": 0,
        "limitedRiskRank": 0,
        "limitedQuestCode": 0,
    }


def make_stamina_info() -> dict[str, object]:
    return {
        "num": 100,
        "max": 100,
        "fullRecoverDate": "",
        "recoverCount": 0,
    }


def make_debug_party() -> dict[str, object]:
    return {
        "id": 1,
        "name": "Debug Party",
        "mainCharacterCode": 0,
        "characterCode": [],
        "members": [],
    }


def bootstrap_payload_for_path(path: str) -> dict[str, object] | None:
    normalized = path.lower()
    user = current_user()
    for suffix, file_name in OFFLINE_API_ROUTE_FILES.items():
        if normalized.endswith(suffix):
            payload = account_payload_from_cache(file_name, user)
            if payload is not None:
                if isinstance(payload.get("userInfo"), dict):
                    payload = dict(payload)
                    payload["userInfo"] = align_user_identity(payload["userInfo"], user)
                else:
                    payload = align_user_identity(payload, user) if suffix == "user/login" else payload
                emit_log(f"[OFFLINE API] {path} <- {file_name}")
                return payload

    if normalized.endswith("ability/index"):
        return {"abilities": []}

    if normalized.endswith("character/index"):
        return {
            "characters": [],
            "releasedColors": [],
            "accessoryEquips": [],
            "abilityEquips": [],
            "armorEquips": [],
            "setSupportCharacter": [],
            "presets": [],
            "remainExp": 0,
        }

    if normalized.endswith("equipment/index"):
        return {"equipments": []}

    if normalized.endswith("party/index"):
        return {
            "parties": [make_debug_party()],
            "mainPartyId": 1,
        }

    if normalized.endswith("user/profile"):
        return {
            "userInfo": make_user_info(),
            "totalLoginCount": 1,
            "consecutiveLoginCount": 1,
            "battleRecords": [],
            "comment": "",
            "wallPaperCode": 0,
            "favoriteAbilityId": 0,
            "questData": make_profile_quest_info(),
            "eventEntryNum": 0,
            "towerClearFloorNum": 0,
            "battleRoyalEntryNum": 0,
            "battleRoyalTotalDamage": 0,
            "eventRankingInfo": [],
            "battleRoyalRankingInfo": [],
            "battleRoyalSeasonInfo": [],
        }

    if normalized.endswith("user/consent"):
        return {
            "isDisplayConsent": False,
            "consentUrl": "",
        }

    if normalized.endswith("user/getconsentstatus"):
        return {
            "isConsented": True,
            "analysisStatus": 1,
            "advertisementStatus": 1,
        }

    if normalized.endswith("title/index"):
        return {"titlesInfo": []}

    if normalized.endswith("stamp/getuserset") or normalized.endswith("stamp/setuserset"):
        return {"stampsInfo": []}

    if normalized.endswith("stamp/index"):
        return {"stamps": []}

    if normalized.endswith("unlimiteddungeon/index"):
        return {
            "userDungeonLevel": 1,
            "freeChallengeRemainCount": 0,
        }

    if normalized.endswith("mission/index"):
        return {"missionsInfo": []}

    if normalized.endswith("notice/index"):
        return {"allNotices": []}

    if normalized.endswith("loginbonus/execute") or normalized.endswith("loginbonus/get"):
        return {
            "loginBonusesInfo": [],
            "premiumLoginBonusesInfo": [],
        }

    if normalized.endswith("home/index"):
        return {
            "userInfo": make_user_info(),
            "presentBadge": 0,
            "missionBadge": 0,
            "returnDispatch": 0,
            "badgeEventCodes": [],
            "stamina": make_stamina_info(),
            "rankingRewardReceived": False,
            "isGhostQuestResult": False,
        }

    return None


@app.route("/transfer/executeBNID", methods=["GET", "POST"])
@app.route("/api/local/saovs/transfer/executeBNID", methods=["GET", "POST"])
def transfer_execute_bnid() -> Response:
    log_request()
    payload_in = current_saovs_payload() or {}
    auth_code = (
        payload_in.get("authenticationCode")
        or payload_in.get("auth_code")
        or payload_in.get("code")
        or request.args.get("authenticationCode")
        or request.args.get("auth_code")
        or request.args.get("code")
        or ""
    )
    platform_user_id = current_platform_user_id()
    user = redeem_auth_code(str(auth_code), platform_user_id)
    if user is None:
        emit_log("[AUTH] transfer/executeBNID rejected missing or invalid auth code")
        if wants_saovs_frame():
            return make_saovs_frame_response({}, status=10001)
        return jsonify({"statusCode": 10001, "error": "Invalid or expired auth code"}), 401

    link_device_to_user(platform_user_id, int(user["id"]), str(user["uuid"]))
    payload = {
        "uuid": str(user["uuid"]),
        "userCode": str(user["user_code"]),
        "userId": int(user["id"]),
        "playerId": int(user["id"]),
        "id": int(user["id"]),
    }
    if wants_saovs_frame():
        return make_saovs_frame_response(payload)

    # This is only a JSON-shaped probe. The real client may expect the normal
    # SAOVS encrypted MessagePack frame before it accepts this response.
    return jsonify(
        {
            "statusCode": 10000,
            "response": payload,
        }
    )


@app.route("/transfer/setBNID", methods=["GET", "POST"])
@app.route("/api/local/saovs/transfer/setBNID", methods=["GET", "POST"])
def transfer_set_bnid() -> Response:
    log_request()
    if wants_saovs_frame():
        return make_saovs_frame_response({})
    return jsonify({"statusCode": 10000, "response": {}})


@app.route("/admin", methods=["GET"])
@app.route("/admin/", methods=["GET"])
def admin_dashboard() -> Response:
    require_admin()
    return send_file(ADMIN_STATIC_DIR / "dashboard.html", mimetype="text/html")


@app.route("/admin/assets/<path:filename>", methods=["GET"])
def admin_dashboard_asset(filename: str) -> Response:
    require_admin()
    asset_path = (ADMIN_STATIC_DIR / filename).resolve()
    if filename.startswith("media/") and not asset_path.is_file():
        return Response(status=204)
    if not asset_path.is_relative_to(ADMIN_STATIC_DIR.resolve()) or not asset_path.is_file():
        abort(404)
    return send_file(asset_path)


@app.route("/favicon.ico", methods=["GET"])
def favicon_ico() -> Response:
    g.skip_response_log = True
    return Response(status=204)


@app.route("/admin/api/logs", methods=["GET"])
def admin_api_logs() -> Response:
    require_admin()
    limit = clamp_int(request.args.get("limit"), ADMIN_LOG_LIMIT, 1, 1000)
    category = (request.args.get("category") or "all").lower()
    status = (request.args.get("status") or "all").lower()
    query = (request.args.get("q") or "").lower().strip()

    entries = parse_log_entries(read_log_tail())
    if category != "all":
        entries = [entry for entry in entries if str(entry.get("category", "")).lower() == category]
    if status != "all":
        entries = [entry for entry in entries if str(entry.get("status") or "event").lower() == status]
    if query:
        entries = [
            entry
            for entry in entries
            if query in str(entry.get("summary", "")).lower()
            or query in str(entry.get("path", "")).lower()
            or query in str(entry.get("detail", "")).lower()
        ]

    return jsonify(
        {
            "logFile": log_file_info(),
            "summary": summarize_log_entries(entries),
            "entries": entries[:limit],
            "available": len(entries),
            "shown": min(limit, len(entries)),
        }
    )


@app.route("/admin/api/logs/clear", methods=["POST"])
def admin_api_clear_logs() -> Response:
    require_admin()
    payload = request.get_json(silent=True) or {}
    clear_request_bodies = bool(payload.get("requestBodies"))
    removed_dumps = truncate_server_logs(clear_request_bodies)
    g.skip_response_log = True
    return jsonify(
        {
            "ok": True,
            "logFile": str(LOG_FILE),
            "removedRequestBodies": removed_dumps,
        }
    )


@app.route("/admin/health", methods=["GET"])
def admin_health() -> Response:
    require_admin()
    content_ok = SAVED_ANDROID_FILES.exists()
    return jsonify(
        {
            "ok": True,
            "serverRoot": str(SERVER_ROOT),
            "database": str(DB_FILE),
            "contentRoot": str(SAVED_ANDROID_FILES),
            "contentRootExists": content_ok,
            "assetBase": SAOVS_ASSET_BASE,
            "assetHosts": sorted(SAOVS_ASSET_HOSTS),
            "authResultOrigin": os.environ.get("SAOVS_AUTH_RESULT_ORIGIN", ""),
            "relativeAuthResultOrigin": os.environ.get("SAOVS_RELATIVE_AUTH_RESULT_ORIGIN", ""),
        }
    )


@app.route("/admin/users", methods=["GET"])
def admin_users() -> Response:
    require_admin()
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT users.id, users.user_code, users.uuid, users.user_name, users.tutorial_step,
                   users.created_at, users.updated_at,
                   (SELECT COUNT(*) FROM sessions WHERE sessions.user_id = users.id) AS total_session_count,
                   (
                       SELECT COUNT(*)
                       FROM sessions
                       WHERE sessions.user_id = users.id
                         AND datetime(sessions.last_seen_at) >= datetime('now', ?)
                   ) AS active_session_count,
                   (
                       SELECT GROUP_CONCAT(login_users.username, ', ')
                       FROM login_users
                       WHERE login_users.user_id = users.id
                   ) AS login_names
            FROM users
            ORDER BY users.id
            """,
            (f"-{SESSION_ACTIVE_SECONDS} seconds",),
        ).fetchall()
    users = []
    for row in rows:
        item = dict(row)
        item["session_count"] = item["active_session_count"]
        item["can_delete"] = int(item["id"]) != DEBUG_USER_ID
        users.append(item)
    return jsonify({"users": users, "activeSessionSeconds": SESSION_ACTIVE_SECONDS})


@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
def admin_delete_user(user_id: int) -> Response:
    require_admin()
    result, error = delete_player_account(user_id)
    if error or result is None:
        return jsonify({"ok": False, "error": error or "Could not delete player."}), 400
    return jsonify({"ok": True, "result": result})


@app.route("/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def catch_all(path: str) -> Response:
    log_request()
    asset_response = serve_asset_file(path)
    if asset_response is not None:
        return asset_response

    if path.endswith("transfer/executeBNID"):
        emit_log(f"[CATCH-ALL ROUTE] Treating /{path} as transfer/executeBNID")
        return transfer_execute_bnid()

    if path.endswith("transfer/setBNID"):
        emit_log(f"[CATCH-ALL ROUTE] Treating /{path} as transfer/setBNID")
        return transfer_set_bnid()

    if wants_saovs_frame():
        payload = bootstrap_payload_for_path(path)
        if payload is not None:
            emit_log(f"[CATCH-ALL ROUTE] Returning bootstrap SAOVS frame for /{path}")
            return make_saovs_frame_response(payload)

        emit_log(f"[CATCH-ALL ROUTE] Returning encrypted empty SAOVS frame for /{path}")
        return make_saovs_frame_response({})

    return jsonify(
        {
            "statusCode": 10000,
            "response": {},
            "debug": {
                "path": path,
                "note": "catch-all JSON probe; implement real frame format when this route is identified",
            },
        }
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SAOVS local login/API debug server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=80)
    parser.add_argument("--ssl-cert")
    parser.add_argument("--ssl-key")
    args = parser.parse_args()

    ssl_context = None
    if args.ssl_cert or args.ssl_key:
        if not args.ssl_cert or not args.ssl_key:
            parser.error("--ssl-cert and --ssl-key must be provided together")
        ssl_context = (args.ssl_cert, args.ssl_key)

    scheme = "https" if ssl_context else "http"
    emit_log(f"[STARTUP] SAOVS private server writing to {LOG_FILE}")
    emit_log(f"[STARTUP] database: {DB_FILE}")
    emit_log(f"[STARTUP] content root: {SAVED_ANDROID_FILES}")
    emit_log(f"[STARTUP] Listening on {scheme}://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False, ssl_context=ssl_context)
