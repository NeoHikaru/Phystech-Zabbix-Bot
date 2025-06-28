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
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.types import BufferedInputFile
from aiogram.client.default import DefaultBotProperties
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
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


async def send_clean(chat_id: int, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> types.Message:
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


async def send_clean_document(chat_id: int, doc: BufferedInputFile, caption: str | None = None) -> types.Message:
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


async def run_ping(target: str, label: str | None = None) -> str:
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
            "sortorder": "ASC",
            "limit": 0,
        },
    )


async def fetch_host_name(host_id: str) -> str:
    info = await zbx.call("host.get", {"output": ["name"], "hostids": [host_id]})
    return info[0]["name"] if info else host_id


async def fetch_ping_target(host_id: str) -> str:
    """Return DNS or IP address best suited for ping."""
    interfaces = await zbx.call(
        "hostinterface.get",
        {"output": ["ip", "dns", "main"], "hostids": [host_id]},
    )
    if interfaces:
        iface = next((i for i in interfaces if str(i.get("main")) == "1"), interfaces[0])
        if iface.get("ip") and iface["ip"] != "0.0.0.0":
            return iface["ip"]
        if iface.get("dns"):
            return iface["dns"]
    info = await zbx.call("host.get", {"output": ["host"], "hostids": [host_id]})
    return info[0].get("host") if info else host_id


async def fetch_host_problems(host_id: str):
    return await zbx.call(
        "problem.get",
        {
            "output": ["name", "severity", "clock"],
            "hostids": [host_id],
            "sortfield": "eventid",
            "sortorder": "DESC",
        },
    )


HOSTS_PER_PAGE = 20


async def show_host_page(chat_id: int, page: int = 0):
    hosts = await fetch_hosts()
    total = (len(hosts) + HOSTS_PER_PAGE - 1) // HOSTS_PER_PAGE or 1
    page = max(0, min(page, total - 1))
    start = page * HOSTS_PER_PAGE
    subset = hosts[start : start + HOSTS_PER_PAGE]

    buttons = [
        [InlineKeyboardButton(text=h["name"], callback_data=f"host:{h['hostid']}")]
        for h in subset
    ]

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"hostpage:{page-1}"))
    if page < total - 1:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"hostpage:{page+1}"))
    nav.append(InlineKeyboardButton(text="Отмена", callback_data="hosts_cancel"))
    buttons.append(nav)

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await send_clean(chat_id, "<b>Выберите хост:</b>", reply_markup=kb)


async def build_status_summary():
    problems, host_map = await fetch_problems()

    counts = {name: 0 for name in SEVERITY_NAMES.values()}
    for pr in problems:
        counts[SEVERITY_NAMES[int(pr["severity"])]] += 1

    lines = [f"{k}: <b>{v}</b>" for k, v in counts.items() if v]
    text = "✅ Проблем нет" if not lines else "🖥 <b>Сводка проблем</b>\n" + "\n".join(lines)

    kb = None
    if problems:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Показать проблемы", callback_data="status_details")]]
        )

    return text, kb, problems, host_map


async def fetch_problems():
    """Return a list of current problems and a mapping of eventid -> host name."""
    params = {
        "output": ["eventid", "name", "severity", "clock"],
        "sortfield": "eventid",
        "sortorder": "DESC",
    }

    problems = await zbx.call("problem.get", {**params, "selectHosts": ["name"]})
    if problems:
        host_map = {p["eventid"]: p.get("hosts", [{}])[0].get("name") for p in problems}
    else:
        problems = await zbx.call("problem.get", params)
        event_ids = [p["eventid"] for p in problems]
        hosts_info = await zbx.call(
            "event.get",
            {"output": ["eventid"], "eventids": event_ids, "selectHosts": ["name"]},
        )
        host_map = {e["eventid"]: e.get("hosts", [{}])[0].get("name") for e in hosts_info}

    return problems, host_map

# Aiogram command handlers
@dp.message(Command("status"))
async def cmd_status(msg: types.Message):
    text, kb, *_ = await build_status_summary()
    await send_clean(msg.chat.id, text, reply_markup=kb)


@dp.callback_query(lambda c: c.data == "status_details")
async def cb_status_details(cb: types.CallbackQuery):
    _, _, problems, host_map = await build_status_summary()

    details = []
    for pr in problems:
        sev_name = SEVERITY_NAMES[int(pr["severity"])]
        host = host_map.get(pr["eventid"], "?")
        ts = datetime.datetime.fromtimestamp(int(pr.get("clock", 0))).strftime("%Y-%m-%d %H:%M")
        details.append(
            f"{sev_name} — <b>{html.escape(host)}</b>: {html.escape(pr['name'])} ({ts})"
        )

    text = "Нет проблем" if not details else "<b>Текущие проблемы:</b>\n" + "\n".join(details[:15])
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Назад", callback_data="status_back")]]
    )
    await send_clean(cb.message.chat.id, text, reply_markup=kb)
    await cb.answer()


@dp.callback_query(lambda c: c.data == "status_back")
async def cb_status_back(cb: types.CallbackQuery):
    text, kb, *_ = await build_status_summary()
    await send_clean(cb.message.chat.id, text, reply_markup=kb)
    await cb.answer()

@dp.message(Command("ping"))
async def cmd_ping(msg: types.Message, command: Command):
    if not command.args:
        return await send_clean(msg.chat.id, "Использование: /ping &lt;host&gt;")
    host = command.args.strip()
    reply = await run_ping(host)
    await send_clean(msg.chat.id, reply)


@dp.message(Command("hosts"))
async def cmd_hosts(msg: types.Message):
    await show_host_page(msg.chat.id, 0)


@dp.callback_query(lambda c: c.data == "hosts_list")
async def cb_hosts_list(cb: types.CallbackQuery):
    await show_host_page(cb.message.chat.id, 0)
    await cb.answer()


@dp.callback_query(lambda c: c.data.startswith("hostpage:"))
async def cb_host_page(cb: types.CallbackQuery):
    page = int(cb.data.split(":", 1)[1])
    await show_host_page(cb.message.chat.id, page)
    await cb.answer()


@dp.callback_query(lambda c: c.data == "hosts_cancel")
async def cb_hosts_cancel(cb: types.CallbackQuery):
    try:
        await bot.delete_message(cb.message.chat.id, cb.message.message_id)
    finally:
        LAST_MESSAGES.pop(cb.message.chat.id, None)
    await cb.answer()


@dp.callback_query(lambda c: c.data.startswith("host:"))
async def cb_host_menu(cb: types.CallbackQuery):
    host_id = cb.data.split(":", 1)[1]
    name = await fetch_host_name(host_id)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Пинг", callback_data=f"hostping:{host_id}")],
            [InlineKeyboardButton(text="Проблемы", callback_data=f"hostproblems:{host_id}")],
            [InlineKeyboardButton(text="Назад", callback_data="hosts_list")],
        ]
    )
    await send_clean(cb.message.chat.id, f"<b>{html.escape(name)}</b>", reply_markup=kb)
    await cb.answer()


@dp.callback_query(lambda c: c.data.startswith("hostping:"))
async def cb_host_ping(cb: types.CallbackQuery):
    host_id = cb.data.split(":", 1)[1]
    name = await fetch_host_name(host_id)
    target = await fetch_ping_target(host_id)
    result = await run_ping(target, label=name)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Назад", callback_data=f"host:{host_id}")]])
    await send_clean(cb.message.chat.id, result, reply_markup=kb)
    await cb.answer()


@dp.callback_query(lambda c: c.data.startswith("hostproblems:"))
async def cb_host_problems(cb: types.CallbackQuery):
    host_id = cb.data.split(":", 1)[1]
    name = await fetch_host_name(host_id)
    problems = await fetch_host_problems(host_id)
    lines = []
    for pr in problems:
        ts = datetime.datetime.fromtimestamp(int(pr.get("clock", 0))).strftime("%Y-%m-%d %H:%M")
        sev = SEVERITY_NAMES[int(pr["severity"])]
        lines.append(f"{sev}: {html.escape(pr['name'])} ({ts})")
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
    lines = [f"{ts}: <i>{html.escape(sub)}</i>" for ts, sub, _ in rows]
    text = "<b>Последние события:</b>\n" + "\n".join(lines)
    await send_clean(msg.chat.id, text)

@dp.message(Command("help"))
async def cmd_help(msg: types.Message):
    text = (
        "<b>📖 Список команд Phystech Zabbix Bot:</b>\n\n"
        "/status — сводка открытых проблем (кнопка деталей)\n"
        "/ping &lt;host&gt; — проверить доступность хоста\n"
        "/hosts — выбрать хост для действий\n"
        "/graph &lt;itemid&gt; [минут] — построить график метрики\n"
        "/events — последние оповещения\n"
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
    await storage.save_event(subject, clean_message)

    for chat_id in ADMIN_CHAT_IDS:
        await bot.send_message(chat_id, text)

    return JSONResponse({"ok": True})

# Startup and polling
async def on_startup():
    await storage.init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_my_commands([
        types.BotCommand(command="status", description="Сводка проблем"),
        types.BotCommand(command="ping",   description="Пинг хоста"),
        types.BotCommand(command="hosts",  description="Список хостов"),
        types.BotCommand(command="graph",  description="График метрики"),
        types.BotCommand(command="events", description="Последние события"),
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
