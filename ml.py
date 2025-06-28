"""Simple anomaly detection over event frequency using IsolationForest."""

import asyncio
import datetime as _dt
import os
import pickle
import sqlite3
from pathlib import Path

import numpy as np
from sklearn.ensemble import IsolationForest


DB_PATH = os.getenv("EVENTS_DB_PATH", "events.db")
MODEL_PATH = Path(os.getenv("ML_MODEL_PATH", "model.pkl"))


def _fetch_timestamps() -> list[str]:
    with sqlite3.connect(DB_PATH) as con:
        cur = con.execute("SELECT timestamp FROM events")
        return [row[0] for row in cur.fetchall()]


async def fetch_timestamps() -> list[str]:
    return await asyncio.to_thread(_fetch_timestamps)


def _hourly_counts(rows: list[str]) -> np.ndarray:
    counts: dict[_dt.datetime, int] = {}
    for ts in rows:
        dt = _dt.datetime.fromisoformat(ts)
        hr = dt.replace(minute=0, second=0, microsecond=0)
        counts[hr] = counts.get(hr, 0) + 1
    if not counts:
        return np.empty((0, 1))
    hours = sorted(counts)
    return np.array([counts[h] for h in hours], dtype=float).reshape(-1, 1)


async def train_model() -> None:
    X = _hourly_counts(await fetch_timestamps())
    if X.shape[0] < 5:
        return
    model = IsolationForest(contamination=0.2, random_state=0)
    model.fit(X)
    with MODEL_PATH.open("wb") as f:
        pickle.dump(model, f)


def _load_model() -> IsolationForest | None:
    if MODEL_PATH.exists():
        with MODEL_PATH.open("rb") as f:
            return pickle.load(f)
    return None


async def check_latest_anomaly() -> bool:
    X = _hourly_counts(await fetch_timestamps())
    if X.shape[0] < 5:
        return False
    model = _load_model()
    if model is None:
        await train_model()
        model = _load_model()
        if model is None:
            return False
    pred = model.predict(X[-1:])
    return int(pred[0]) == -1

