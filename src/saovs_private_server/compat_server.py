from __future__ import annotations

import argparse
import base64
import logging
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from re import search, sub
from urllib.parse import quote, unquote, urlparse

from flask import Flask, Response, abort, jsonify, redirect, request, send_file
from werkzeug.exceptions import HTTPException

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


app = Flask(__name__)

SERVER_ROOT = Path(os.environ.get("SAOVS_SERVER_ROOT", Path(__file__).resolve().parents[2])).resolve()
LOG_DIR = Path(os.environ.get("SAOVS_LOG_DIR", SERVER_ROOT / "runtime" / "logs")).resolve()
DB_FILE = Path(os.environ.get("SAOVS_DB", SERVER_ROOT / "runtime" / "saovs.sqlite3")).resolve()
DEBUG_AUTH_CODE = os.environ.get("SAOVS_DEFAULT_AUTH_CODE", "LOCAL_TRANSFER_CODE")
DEBUG_SESSION = "debug-local-session"
DEBUG_USER_ID = 1
DEBUG_USER_CODE = 100000000001
DEBUG_TUTORIAL_STEP = os.environ.get("SAOVS_DEFAULT_TUTORIAL_STEP", "999")
DEFAULT_USER_NAME = os.environ.get("SAOVS_DEFAULT_USER_NAME", "Kirito")
LOG_FILE = LOG_DIR / "saovs_private_server.log"
REQUEST_DUMP_DIR = LOG_DIR / "request_bodies"
SAVED_ANDROID_FILES = Path(
    os.environ.get("SAOVS_CONTENT_ROOT", SERVER_ROOT / "content" / "files")
).resolve()
SAOVS_OLD_KEY = b"ADrbjQw8UABp9zsBeZjaw7LbMxyfQRZD"
SAOVS_OLD_IV = b"jJkbN3VV9PAUhCLz"
SAOVS_NEW_KEY = b"6d14XUUQ9J1xjshP8u5avnqipObMa3tk"
SAOVS_NEW_IV = b"FJxIPPhFj8o85u9b"
SAOVS_ASSET_BASE = os.environ.get("SAOVS_ASSET_BASE", "https://assets-os.saovs.channel.or.jp/")
SAOVS_ASSET_VER = os.environ.get("SAOVS_ASSET_VER", "30000")
SAOVS_MASTER_DATA_VER = os.environ.get("SAOVS_MASTER_DATA_VER", "30000")
SAOVS_LOCALIZE_DATA_VER = int(os.environ.get("SAOVS_LOCALIZE_DATA_VER", "30000"))
SAOVS_ADMIN_TOKEN = os.environ.get("SAOVS_ADMIN_TOKEN", "")
SAOVS_ASSET_HOSTS = {
    host.strip().lower()
    for host in os.environ.get(
        "SAOVS_ASSET_HOSTS",
        "assets-os.saovs.channel.or.jp",
    ).split(",")
    if host.strip()
}
parsed_asset_host = urlparse(SAOVS_ASSET_BASE).hostname
if parsed_asset_host:
    SAOVS_ASSET_HOSTS.add(parsed_asset_host.lower())
ASSET_FILE_INDEX: dict[str, Path] | None = None


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
            """
        )


def utc_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


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


def get_user_by_uuid(uuid: str) -> dict[str, object] | None:
    with db_connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE uuid = ?", (uuid,)).fetchone()
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
    existing = get_user_by_uuid(uuid)
    if existing:
        return existing

    now = utc_text()
    with db_connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO users (uuid, user_name, tutorial_step, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (uuid, user_name or DEFAULT_USER_NAME, DEBUG_TUTORIAL_STEP, now, now),
        )
        user_id = int(cursor.lastrowid)
        user_code = 100000000000 + user_id
        conn.execute("UPDATE users SET user_code = ? WHERE id = ?", (user_code, user_id))
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        emit_log(f"[ACCOUNT] created user id={user_id} user_code={user_code} uuid={uuid}")
        return dict(row)


def current_user() -> dict[str, object]:
    session = current_saovs_header_value("session", "")
    if isinstance(session, str) and session:
        user = get_user_by_session(session)
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


def session_for_response() -> str:
    request_session = current_saovs_header_value("session", "")
    if isinstance(request_session, str) and request_session:
        return request_session

    if current_saovs_request_key() == "new":
        user = current_user()
        return issue_session(int(user["id"]))

    return ""


def require_admin() -> None:
    if not SAOVS_ADMIN_TOKEN:
        return

    token = request.headers.get("X-Admin-Token") or request.args.get("token") or ""
    if not secrets.compare_digest(token, SAOVS_ADMIN_TOKEN):
        abort(401)


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
    emit_log(f"[RESPONSE] {request.method} {request.path} -> {response.status_code}")
    return response


@app.errorhandler(Exception)
def log_exception(error: Exception) -> Response | tuple[Response, int]:
    if isinstance(error, HTTPException):
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
</head>
<body>
  <input type="hidden" id="page_name" value="puzzle_auth_result_page">
  <input type="hidden" id="auth_result" value="{safe_code}">

  <script>
    function notifyUnity() {{
      var pageName = document.getElementById("page_name").value;
      if (pageName !== "puzzle_auth_result_page") return;

      var authResult = document.getElementById("auth_result").value;

      try {{
        if (window.Unity && typeof window.Unity.call === "function") {{
          window.Unity.call(authResult);
          console.log("[SAOVS DEBUG] Unity.call auth_result:", authResult);
        }}
      }} catch (e) {{
        console.log("[SAOVS DEBUG] Unity.call failed:", e);
      }}
    }}

    window.addEventListener("load", function() {{
      setTimeout(notifyUnity, 250);
      setTimeout(notifyUnity, 1000);
      setTimeout(notifyUnity, 2500);
    }});
  </script>
</body>
</html>
"""
    return Response(html, mimetype="text/html")


@app.route("/", methods=["GET"])
def index() -> Response:
    log_request()
    original = request.args.get("original", "")
    if original:
        emit_log(f"[LOCAL LOGIN] Auto-returning auth_result for original: {unquote(original)}")
        return puzzle_auth_page(request.args.get("auth_code") or DEBUG_AUTH_CODE)

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


@app.route("/login.html", methods=["GET"])
def login_html() -> Response:
    log_request()
    return redirect("/?original=" + quote(request.url, safe=""), code=302)


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
    session = issue_session(int(user["id"]))
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
    user = get_or_create_user(current_request_uuid())
    if wants_saovs_frame():
        return make_saovs_frame_response(
            {
                "uuid": str(user["uuid"]),
                "userCode": str(user["user_code"]),
            }
        )

    # This is only a JSON-shaped probe. The real client may expect the normal
    # SAOVS encrypted MessagePack frame before it accepts this response.
    return jsonify(
        {
            "statusCode": 10000,
            "response": {
                "uuid": str(user["uuid"]),
                "userCode": str(user["user_code"]),
            },
        }
    )


@app.route("/transfer/setBNID", methods=["GET", "POST"])
@app.route("/api/local/saovs/transfer/setBNID", methods=["GET", "POST"])
def transfer_set_bnid() -> Response:
    log_request()
    if wants_saovs_frame():
        return make_saovs_frame_response({})
    return jsonify({"statusCode": 10000, "response": {}})


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
        }
    )


@app.route("/admin/users", methods=["GET"])
def admin_users() -> Response:
    require_admin()
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT users.id, users.user_code, users.uuid, users.user_name, users.tutorial_step,
                   users.created_at, users.updated_at, COUNT(sessions.token) AS session_count
            FROM users
            LEFT JOIN sessions ON sessions.user_id = users.id
            GROUP BY users.id
            ORDER BY users.id
            """
        ).fetchall()
    return jsonify({"users": [dict(row) for row in rows]})


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
