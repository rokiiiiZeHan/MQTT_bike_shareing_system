import sqlite3
from datetime import datetime
from pathlib import Path


DB_PATH = Path(__file__).resolve().parent / "bike_tracker.db"


def get_connection():
    """Return a SQLite connection for the bike tracker database."""
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def _row_to_dict(row):
    """Convert a sqlite3.Row object to a plain dict."""
    return dict(row) if row is not None else None


def init_db():
    """Create tables and ensure the default admin account exists."""
    with get_connection() as connection:
        cursor = connection.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS admins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'admin',
                created_at TEXT NOT NULL
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS bikes (
                bike_id TEXT PRIMARY KEY,
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                status TEXT NOT NULL,
                battery INTEGER NOT NULL,
                lock TEXT NOT NULL,
                current_zone TEXT,
                last_update TEXT NOT NULL
            )
            """
        )
        # The bikes table stores only the latest state of each bike.

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS violations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bike_id TEXT NOT NULL,
                type TEXT NOT NULL,
                message TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                lat REAL,
                lon REAL
            )
            """
        )
        # The violations table stores historical violation events.

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                balance REAL NOT NULL DEFAULT 50.0,
                created_at TEXT NOT NULL
            )
            """
        )
        # Prototype only: passwords are stored as plain text.
        # In a production system, passwords should be hashed.

        cursor.execute(
            """
            INSERT OR IGNORE INTO admins (username, password_hash, role, created_at)
            VALUES (?, ?, ?, ?)
            """,
            ("admin", "admin123", "super_admin", datetime.utcnow().isoformat()),
        )

        connection.commit()


def upsert_bike(bike_data: dict):
    """Insert or update the latest state for one bike."""
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO bikes (
                bike_id, lat, lon, status, battery, lock, current_zone, last_update
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(bike_id) DO UPDATE SET
                lat = excluded.lat,
                lon = excluded.lon,
                status = excluded.status,
                battery = excluded.battery,
                lock = excluded.lock,
                current_zone = excluded.current_zone,
                last_update = excluded.last_update
            """,
            (
                bike_data["bike_id"],
                bike_data["lat"],
                bike_data["lon"],
                bike_data["status"],
                bike_data["battery"],
                bike_data["lock"],
                bike_data.get("current_zone"),
                bike_data["last_update"],
            ),
        )
        connection.commit()


def insert_violation(violation: dict):
    """Insert one historical violation event."""
    location = violation.get("location") or {}

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO violations (bike_id, type, message, timestamp, lat, lon)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                violation["bike_id"],
                violation["type"],
                violation["message"],
                violation["timestamp"],
                location.get("lat"),
                location.get("lon"),
            ),
        )
        connection.commit()


def fetch_all_bikes():
    """Return all bikes ordered by bike_id."""
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT bike_id, lat, lon, status, battery, lock, current_zone, last_update
            FROM bikes
            ORDER BY bike_id
            """
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def fetch_recent_violations(limit=50):
    """Return recent violations ordered by newest first."""
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, bike_id, type, message, timestamp, lat, lon
            FROM violations
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def fetch_admin_by_username(username):
    """Return one admin by username or None."""
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT id, username, password_hash, role, created_at
            FROM admins
            WHERE username = ?
            """,
            (username,),
        ).fetchone()
    return _row_to_dict(row)


def create_or_get_user(username: str, password: str):
    """
    Create a new user with default balance if the username does not exist.
    If the user exists, check whether the password matches.
    """
    with get_connection() as connection:
        existing_user = connection.execute(
            """
            SELECT id, username, password, balance, created_at
            FROM users
            WHERE username = ?
            """,
            (username,),
        ).fetchone()

        if existing_user:
            user = _row_to_dict(existing_user)
            if user["password"] != password:
                return None
            user.pop("password", None)
            return user

        connection.execute(
            """
            INSERT INTO users (username, password, balance, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (username, password, 50.0, datetime.utcnow().isoformat()),
        )
        connection.commit()

        new_user = connection.execute(
            """
            SELECT id, username, balance, created_at
            FROM users
            WHERE username = ?
            """,
            (username,),
        ).fetchone()

    return _row_to_dict(new_user)


def fetch_user_by_id(user_id: int):
    """Return one user by id without password."""
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT id, username, balance, created_at
            FROM users
            WHERE id = ?
            """,
            (user_id,),
        ).fetchone()

    return _row_to_dict(row)


def update_user_balance(user_id: int, new_balance: float):
    """Update user balance and return the updated user."""
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE users
            SET balance = ?
            WHERE id = ?
            """,
            (new_balance, user_id),
        )
        connection.commit()

        row = connection.execute(
            """
            SELECT id, username, balance, created_at
            FROM users
            WHERE id = ?
            """,
            (user_id,),
        ).fetchone()

    return _row_to_dict(row)

if __name__ == "__main__":
    init_db()
