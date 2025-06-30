import os
import sqlite3
import asyncio
from typing import List, Tuple, Optional

DB_PATH = os.getenv("EVENTS_DB_PATH", "events.db")

def _init_db() -> None:
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                subject TEXT,
                message TEXT,
                label TEXT
            )
            """
        )
        con.commit()

def _save_event(subject: str, message: str) -> int:
    with sqlite3.connect(DB_PATH) as con:
        cur = con.execute(
            "INSERT INTO events(timestamp, subject, message)"
            " VALUES(datetime('now'), ?, ?)",
            (subject, message),
        )
        con.commit()
        return cur.lastrowid

async def init_db() -> None:
    await asyncio.to_thread(_init_db)

async def save_event(subject: str, message: str) -> int:
    return await asyncio.to_thread(_save_event, subject, message)


def _update_label(event_id: int, label: str) -> None:
    with sqlite3.connect(DB_PATH) as con:
        con.execute("UPDATE events SET label=? WHERE id=?", (label, event_id))
        con.commit()


async def update_label(event_id: int, label: str) -> None:
    await asyncio.to_thread(_update_label, event_id, label)


def _fetch_events(limit: int) -> List[Tuple[int, str, str, Optional[str]]]:
    with sqlite3.connect(DB_PATH) as con:
        cur = con.execute(
            "SELECT id, timestamp, subject, message, label FROM events"
            " ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return cur.fetchall()


async def fetch_events(limit: int = 5) -> List[Tuple[int, str, str, Optional[str]]]:
    return await asyncio.to_thread(_fetch_events, limit)


def _fetch_labeled() -> List[Tuple[str, str]]:
    with sqlite3.connect(DB_PATH) as con:
        cur = con.execute(
            "SELECT subject || ' ' || message, label FROM events WHERE label IS NOT NULL"
        )
        return cur.fetchall()


async def fetch_labeled() -> List[Tuple[str, str]]:
    return await asyncio.to_thread(_fetch_labeled)
