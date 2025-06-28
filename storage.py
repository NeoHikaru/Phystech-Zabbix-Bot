import os
import sqlite3
import asyncio

DB_PATH = os.getenv("EVENTS_DB_PATH", "events.db")

def _init_db() -> None:
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                subject TEXT,
                message TEXT
            )
            """
        )
        con.commit()

def _save_event(subject: str, message: str) -> None:
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            "INSERT INTO events(timestamp, subject, message)"
            " VALUES(datetime('now'), ?, ?)",
            (subject, message),
        )
        con.commit()

async def init_db() -> None:
    await asyncio.to_thread(_init_db)

async def save_event(subject: str, message: str) -> None:
    await asyncio.to_thread(_save_event, subject, message)


def _fetch_events(limit: int) -> list[tuple[str, str, str]]:
    with sqlite3.connect(DB_PATH) as con:
        cur = con.execute(
            "SELECT timestamp, subject, message FROM events"
            " ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return cur.fetchall()


async def fetch_events(limit: int = 5) -> list[tuple[str, str, str]]:
    return await asyncio.to_thread(_fetch_events, limit)
