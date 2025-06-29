import asyncio
import os
import html
import re
import datetime
from io import BytesIO

from dotenv import load_dotenv

# Load environment variables before importing internal modules
load_dotenv()

import zbx
import storage
import ml
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.types import BufferedInputFile
from aiogram.client.default import DefaultBotProperties
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from typing import Optional
BOT_TOKEN = os.getenv("BOT_TOKEN")
# ADMIN_CHAT_IDS: comma-separated list of chat IDs
ADMIN_CHAT_IDS = [int(cid) for cid in os.getenv("ADMIN_CHAT_IDS", "").split(",") if cid.strip()]
ZABBIX_WEB = os.getenv("ZABBIX_WEB")  # e.g. https://zabbix.example.com

# Initialize Bot and Dispatcher
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# Initialize FastAPI app
app = FastAPI()

# Severity map for problem levels
SEVERITY_NAMES = {
    0: "Не классифицировано",
    1: "Информация",
    2: "Предупреждение",
    3: "Средняя",
    4: "Высокая",
    5: "Критическая",
}

# Track last bot message in each chat to delete it on next command
LAST_MESSAGES: dict[int, int] = {}


async def send_clean(chat_id: int, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None) -> types.Message:
    """Send message removing the previous one for this chat."""
    last_id = LAST_MESSAGES.get(chat_id)
    if last_id:
        try:
            await bot.delete_message(chat_id, last_id)
        except Exception:
            pass
    m = await bot.send_message(chat_id, text, reply_markup=reply_markup)
    LAST_MESSAGES[chat_id] = m.message_id
    return m


async def send_clean_document(chat_id: int, doc: BufferedInputFile, caption: Optional[str] = None) -> types.Message:
    """Send document after deleting previous bot message."""
    last_id = LAST_MESSAGES.get(chat_id)
    if last_id:
        try:
            await bot.delete_message(chat_id, last_id)
        except Exception:
            pass
    m = await bot.send_document(chat_id, doc, caption=caption)
    LAST_MESSAGES[chat_id] = m.message_id
    return m


async def run_ping(target: str, label: Optional[str] = None) -> str:
    """Execute ping and return formatted result."""
    proc = await asyncio.create_subprocess_exec(
        "ping",
        "-c",
        "4",
        target,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    ok = proc.returncode == 0
    name = label or target
    return (
        f"{'✅' if ok else '❌'} <b>{html.escape(name)}</b>\n"
        f"<pre>{html.escape((out or err).decode())}</pre>"
    )


async def fetch_hosts():
    """Return list of all hosts with id and name sorted alphabetically."""
    return await zbx.call(
        "host.get",
        {
            "output": ["hostid", "name"],
            "sortfield": "name",
@@ -313,87 +316,157 @@ async def cb_host_problems(cb: types.CallbackQuery):
    text = f"<b>Проблемы {html.escape(name)}:</b>\n" + ("\n".join(lines) if lines else "Нет проблем")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Назад", callback_data=f"host:{host_id}")]])
    await send_clean(cb.message.chat.id, text, reply_markup=kb)
    await cb.answer()

@dp.message(Command("graph"))
async def cmd_graph(msg: types.Message, command: Command):
    parts = (command.args or "").split()
    if not parts:
        return await send_clean(msg.chat.id, "Использование: /graph &lt;itemid&gt; [минут]")
    try:
        itemid = int(parts[0])
    except ValueError:
        return await send_clean(msg.chat.id, "❌ <b>Неверный itemid</b>\nИспользование: /graph &lt;itemid&gt; [минут]")
    minutes = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 60
    period = minutes * 60
    await send_clean(msg.chat.id, "⏳ Сбор данных и построение графика…")
    try:
        png = await zbx.chart_png(itemid, period)
        bio = BytesIO(png)
        filename = f"item_{itemid}.png"
        await send_clean_document(msg.chat.id, BufferedInputFile(bio.getvalue(), filename))
    except Exception as e:
        await send_clean(msg.chat.id, f"⚠️ Ошибка при построении графика: <pre>{html.escape(str(e))}</pre>")


@dp.message(Command("events"))
async def cmd_events(msg: types.Message):
    rows = await storage.fetch_events(5)
    if not rows:
        return await send_clean(msg.chat.id, "Событий нет")
    lines = []
    for _id, ts, sub, _, label in rows:
        lbl = f" [{label}]" if label else ""
        lines.append(f"#{_id} {ts}: <i>{html.escape(sub)}</i>{lbl}")
    text = "<b>Последние события:</b>\n" + "\n".join(lines)
    await send_clean(msg.chat.id, text)


@dp.message(Command("anomaly"))
async def cmd_anomaly(msg: types.Message):
    is_bad = await ml.check_latest_anomaly()
    text = "Нет всплесков" if not is_bad else "❗️ Обнаружен всплеск событий"
    await send_clean(msg.chat.id, text)


@dp.message(Command("label"))
async def cmd_label(msg: types.Message, command: Command):
    parts = (command.args or "").split(maxsplit=1)
    if len(parts) != 2 or not parts[0].isdigit():
        return await send_clean(msg.chat.id, "Использование: /label <id> <метка>")
    event_id = int(parts[0])
    label = parts[1]
    await storage.update_label(event_id, label)
    await ml.train_classifier()
    await send_clean(msg.chat.id, f"Событие {event_id} помечено как {html.escape(label)}")


@dp.message(Command("forecast"))
async def cmd_forecast(msg: types.Message, command: Command):
    parts = (command.args or "").split()
    if not parts or not parts[0].isdigit():
        return await send_clean(msg.chat.id, "Использование: /forecast <itemid> [часов]")
    itemid = int(parts[0])
    hours = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
    await send_clean(msg.chat.id, "⏳ Прогнозирование…")
    try:
        values = await ml.forecast_item(itemid, hours)
    except Exception as e:
        return await send_clean(msg.chat.id, f"Ошибка: {html.escape(str(e))}")
    if not values:
        return await send_clean(msg.chat.id, "Недостаточно данных для прогноза")
    vals = ", ".join(f"{v:.2f}" for v in values)
    await send_clean(msg.chat.id, f"Прогноз: {vals}")

@dp.message(Command("help"))
async def cmd_help(msg: types.Message):
    text = (
        "<b>📖 Список команд Phystech Zabbix Bot:</b>\n\n"
        "/status — сводка открытых проблем (кнопка деталей)\n"
        "/ping &lt;host&gt; — проверить доступность хоста\n"
        "/hosts — выбрать хост для действий\n"
        "/graph &lt;itemid&gt; [минут] — построить график метрики\n"
        "/events — последние оповещения\n"
        "/anomaly — поиск всплесков событий\n"
        "/label <id> <метка> — пометить событие\n"
        "/forecast <itemid> [часов] — прогноз значения\n"
        "/help — показать эту справку"
    )
    await send_clean(msg.chat.id, text)

# FastAPI endpoints
@app.get("/healthz")
def health():
    return {"status": "ok"}

@app.post("/zabbix")
async def zabbix_alert(req: Request):
    """Receive alerts from Zabbix and forward them to Telegram."""
    payload = await req.json()

    # Remove links from the incoming message to avoid leaking internal URLs
    raw_message = str(payload.get("message", payload))
    clean_message = re.sub(r"<a[^>]*>.*?</a>", "", raw_message, flags=re.DOTALL)

    # Try to extract problem/event ID from a link like ?eventid=1234
    id_match = re.search(r"eventid=(\d+)", raw_message)
    if id_match:
        clean_message += f"\nНомер проблемы: {id_match.group(1)}"

    subject = payload.get('subject', 'Zabbix alert')
    text = (
        f"📡 <b>{html.escape(subject)}</b>\n"
        f"{html.escape(clean_message)}"
    )
    event_id = await storage.save_event(subject, clean_message)
    label = await ml.predict_label(subject, clean_message)
    if label:
        await storage.update_label(event_id, label)
        text += f"\nМетка: {html.escape(label)}"
    spike = await ml.check_latest_anomaly()

    for chat_id in ADMIN_CHAT_IDS:
        await bot.send_message(chat_id, text)
        if spike:
            await bot.send_message(chat_id, "⚠️ Обнаружен всплеск событий (ML)")

    return JSONResponse({"ok": True})

# Startup and polling
async def on_startup():
    await storage.init_db()
    await ml.train_model()
    await ml.train_classifier()
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_my_commands([
        types.BotCommand(command="status", description="Сводка проблем"),
        types.BotCommand(command="ping",   description="Пинг хоста"),
        types.BotCommand(command="hosts",  description="Список хостов"),
        types.BotCommand(command="graph",  description="График метрики"),
        types.BotCommand(command="events", description="Последние события"),
        types.BotCommand(command="anomaly", description="Поиск всплесков"),
        types.BotCommand(command="label", description="Пометить событие"),
        types.BotCommand(command="forecast", description="Прогноз метрики"),
        types.BotCommand(command="help",   description="Справка"),
    ])
    print("✅ Webhook удалён, команды зарегистрированы")

async def main():
    from uvicorn import Config, Server
    await on_startup()
    config = Config(app, host="0.0.0.0", port=8000, loop="asyncio")
    server = Server(config)
    await asyncio.gather(dp.start_polling(bot), server.serve())

if __name__ == "__main__":
    asyncio.run(main())