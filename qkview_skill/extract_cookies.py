from __future__ import annotations

import hashlib
import configparser
import os
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional


CHROME_COOKIE_PATHS = (
    Path("~/Library/Application Support/Google/Chrome/Default/Cookies").expanduser(),
    Path("~/Library/Application Support/Google/Chrome/Profile 1/Cookies").expanduser(),
)

FIREFOX_PROFILES_INI = Path("~/Library/Application Support/Firefox/profiles.ini").expanduser()

COOKIE_QUERIES = (
    "select host_key, name, value, encrypted_value from cookies where name = ?",
    "select host_key, name, value, encrypted_value from cookies where host_key like ? and name = ?",
)


class CookieExtractionError(RuntimeError):
    pass


def _copy_cookie_db() -> Path:
    for candidate in CHROME_COOKIE_PATHS:
        if candidate.exists():
            temp_dir = Path(tempfile.mkdtemp(prefix="qkview-cookie-db-"))
            copied = temp_dir / "Cookies"
            shutil.copy2(candidate, copied)
            return copied
    raise CookieExtractionError("Could not find a Chrome cookie database in the default profiles")


def _chrome_safe_storage_password() -> str:
    commands = [
        ["security", "find-generic-password", "-w", "-s", "Chrome Safe Storage"],
        ["security", "find-generic-password", "-w", "-a", "Chrome", "-s", "Chrome Safe Storage"],
    ]
    for command in commands:
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip()
    raise CookieExtractionError("Could not read 'Chrome Safe Storage' from macOS Keychain")


def _decrypt_cookie(encrypted_value: bytes) -> str:
    if not encrypted_value:
        return ""
    if encrypted_value[:3] not in (b"v10", b"v11"):
        return encrypted_value.decode("utf-8", errors="ignore")

    ciphertext = encrypted_value[3:]
    key = hashlib.pbkdf2_hmac(
        "sha1",
        _chrome_safe_storage_password().encode("utf-8"),
        b"saltysalt",
        1003,
        dklen=16,
    )
    iv_hex = (b" " * 16).hex()
    process = subprocess.run(
        [
            "openssl",
            "enc",
            "-aes-128-cbc",
            "-d",
            "-nosalt",
            "-K",
            key.hex(),
            "-iv",
            iv_hex,
        ],
        input=ciphertext,
        capture_output=True,
    )
    if process.returncode != 0:
        raise CookieExtractionError(process.stderr.decode("utf-8", errors="ignore").strip() or "openssl failed to decrypt Chrome cookie")

    decrypted = process.stdout
    if decrypted:
        padding = decrypted[-1]
        if 0 < padding <= 16:
            decrypted = decrypted[:-padding]
    return decrypted.decode("utf-8", errors="ignore")


def extract_cookie_value(cookie_name: str, host_patterns: Iterable[str]) -> Optional[str]:
    copied_db = _copy_cookie_db()
    connection: Optional[sqlite3.Connection] = None
    try:
        connection = sqlite3.connect(str(copied_db))
        connection.row_factory = sqlite3.Row
        cursor = connection.cursor()

        for query in COOKIE_QUERIES:
            if query.count("?") == 1:
                rows = cursor.execute(query, (cookie_name,)).fetchall()
            else:
                rows = []
                for host_pattern in host_patterns:
                    rows.extend(cursor.execute(query, (host_pattern, cookie_name)).fetchall())

            for row in rows:
                value = row["value"] or ""
                if value:
                    return value
                encrypted = row["encrypted_value"] or b""
                if encrypted:
                    return _decrypt_cookie(encrypted)
        return None
    finally:
        try:
            if connection is not None:
                connection.close()
        except Exception:
            pass
        shutil.rmtree(copied_db.parent, ignore_errors=True)


def extract_ihealth_session_cookie() -> str:
    cookie = extract_cookie_value("JSESSIONID", ("%f5.com%", "%ihealth%"))
    if not cookie:
        raise CookieExtractionError("No iHealth JSESSIONID cookie found in Chrome")
    return cookie


def _firefox_cookie_paths() -> List[Path]:
    if not FIREFOX_PROFILES_INI.exists():
        return []

    parser = configparser.ConfigParser()
    parser.read(FIREFOX_PROFILES_INI)
    paths: List[Path] = []
    for section in parser.sections():
        if not section.startswith("Profile"):
            continue
        profile_path = parser[section].get("Path")
        if not profile_path:
            continue
        is_relative = parser[section].get("IsRelative", "1") == "1"
        base = FIREFOX_PROFILES_INI.parent if is_relative else Path("/")
        cookie_path = (base / profile_path / "cookies.sqlite").expanduser()
        if cookie_path.exists():
            paths.append(cookie_path)
    return paths


def _copy_first_existing(paths: Iterable[Path], label: str) -> Path:
    for candidate in paths:
        if candidate.exists():
            temp_dir = Path(tempfile.mkdtemp(prefix=f"qkview-{label}-cookie-db-"))
            copied = temp_dir / candidate.name
            shutil.copy2(candidate, copied)
            return copied
    raise CookieExtractionError(f"Could not find a {label} cookie database in the default profiles")


def firefox_cookie_header(request_host: str, domain_filter: str = "f5") -> str:
    rows = []
    cookie_paths = _firefox_cookie_paths()
    if not cookie_paths:
        raise CookieExtractionError("Could not find a firefox cookie database in the default profiles")

    for cookie_path in cookie_paths:
        copied_db = _copy_first_existing([cookie_path], "firefox")
        connection: Optional[sqlite3.Connection] = None
        try:
            connection = sqlite3.connect(str(copied_db))
            cursor = connection.cursor()
            rows.extend(
                cursor.execute(
                    "select host, name, value from moz_cookies where host like ? or host like ? order by host, name",
                    (f"%{domain_filter}%", "%ihealth%"),
                ).fetchall()
            )
        finally:
            try:
                if connection is not None:
                    connection.close()
            except Exception:
                pass
            shutil.rmtree(copied_db.parent, ignore_errors=True)

    cookie_pairs: List[str] = []
    seen: set[str] = set()
    for host, name, value in rows:
        normalized_host = str(host).lstrip(".")
        if request_host == normalized_host or request_host.endswith(f".{normalized_host}"):
            if value and f"{name}={value}" not in seen:
                seen.add(f"{name}={value}")
                cookie_pairs.append(f"{name}={value}")
    return "; ".join(cookie_pairs)


def browser_cookie_header(request_host: str, browser: str = "firefox") -> str:
    browser_name = browser.lower()
    if browser_name == "firefox":
        header = firefox_cookie_header(request_host)
        if header:
            return header
        raise CookieExtractionError(f"No matching Firefox cookies found for {request_host}")

    session = extract_ihealth_session_cookie()
    return f"JSESSIONID={session}"
