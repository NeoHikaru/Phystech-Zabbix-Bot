"""Simple ML helpers for the Zabbix bot."""

import asyncio
import datetime as _dt
import os
import pickle
import sqlite3
from pathlib import Path
from typing import List, Optional

import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.naive_bayes import MultinomialNB
from sklearn.pipeline import Pipeline
from statsmodels.tsa.arima.model import ARIMA

import storage
import zbx


DB_PATH = os.getenv("EVENTS_DB_PATH", "events.db")
MODEL_PATH = Path(os.getenv("ML_MODEL_PATH", "model.pkl"))
CLF_PATH = Path(os.getenv("ML_CLF_PATH", "classifier.pkl"))


def _fetch_timestamps() -> List[str]:
    with sqlite3.connect(DB_PATH) as con:
        cur = con.execute("SELECT timestamp FROM events")
        return [row[0] for row in cur.fetchall()]


async def fetch_timestamps() -> List[str]:
    return await asyncio.to_thread(_fetch_timestamps)


def _hourly_counts(rows: List[str]) -> np.ndarray:
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


def _load_model() -> Optional[IsolationForest]:
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


def _load_classifier() -> Optional[Pipeline]:
    if CLF_PATH.exists():
        with CLF_PATH.open("rb") as f:
            return pickle.load(f)
    return None


async def train_classifier() -> None:
    rows = await storage.fetch_labeled()
    if not rows:
        return
    texts = [r[0] for r in rows]
    labels = [r[1] for r in rows]
    if len(set(labels)) < 2:
        return
    pipeline = Pipeline([
        ("tfidf", TfidfVectorizer()),
        ("clf", MultinomialNB()),
    ])
    pipeline.fit(texts, labels)
    with CLF_PATH.open("wb") as f:
        pickle.dump(pipeline, f)


async def predict_label(subject: str, message: str) -> Optional[str]:
    clf = _load_classifier()
    if clf is None:
        await train_classifier()
        clf = _load_classifier()
    if clf is None:
        return None
    text = f"{subject} {message}"
    return str(clf.predict([text])[0])


async def forecast_values(values: List[float], steps: int = 5) -> List[float]:
    if len(values) < 3:
        return []
    model = ARIMA(values, order=(1, 1, 1))
    res = model.fit()
    forecast = res.forecast(steps=steps)
    return forecast.tolist()


async def forecast_item(itemid: int, hours: int = 1) -> List[float]:
    end = int(_dt.datetime.now().timestamp())
    start = end - hours * 3600
    history = await zbx.call(
        "history.get",
        {
            "history": 0,
            "itemids": [itemid],
            "time_from": start,
            "time_till": end,
            "output": "extend",
        },
    )
    values = [float(h["value"]) for h in history]
    return await forecast_values(values)