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
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.types import InputFile
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


async def run_ping(host: str) -> str:
    """Execute ping and return formatted result."""
    proc = await asyncio.create_subprocess_exec(
        "ping",
        "-c",
        "4",
        host,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    ok = proc.returncode == 0
    return f"{'✅' if ok else '❌'} <b>{html.escape(host)}</b>\n<pre>{html.escape((out or err).decode())}</pre>"


async def fetch_hosts():
    """Return list of hosts with id and name sorted alphabetically."""
    return await zbx.call(
        "host.get",
        {"output": ["hostid", "name"], "sortfield": "name", "sortorder": "ASC"},
    )


async def fetch_host_name(host_id: str) -> str:
    info = await zbx.call("host.get", {"output": ["name"], "hostids": [host_id]})
    return info[0]["name"] if info else host_id


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


async def show_host_list(message: types.Message):
    hosts = await fetch_hosts()
    buttons = [
        [InlineKeyboardButton(text=h["name"], callback_data=f"host:{h['hostid']}")]
        for h in hosts[:50]
    ]
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("<b>Выберите хост:</b>", reply_markup=kb)


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
    await msg.answer(text, reply_markup=kb)


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
    await cb.message.answer(text, reply_markup=kb)
    await cb.answer()


@dp.callback_query(lambda c: c.data == "status_back")
async def cb_status_back(cb: types.CallbackQuery):
    text, kb, *_ = await build_status_summary()
    await cb.message.answer(text, reply_markup=kb)
    await cb.answer()

@dp.message(Command("ping"))
async def cmd_ping(msg: types.Message, command: Command):
    if not command.args:
        return await msg.answer("Использование: /ping &lt;host&gt;")
    host = command.args.strip()
    reply = await run_ping(host)
    await msg.answer(reply)


@dp.message(Command("hosts"))
async def cmd_hosts(msg: types.Message):
    await show_host_list(msg)


@dp.callback_query(lambda c: c.data == "hosts_list")
async def cb_hosts_list(cb: types.CallbackQuery):
    await show_host_list(cb.message)
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
    await cb.message.answer(f"<b>{html.escape(name)}</b>", reply_markup=kb)
    await cb.answer()


@dp.callback_query(lambda c: c.data.startswith("hostping:"))
async def cb_host_ping(cb: types.CallbackQuery):
    host_id = cb.data.split(":", 1)[1]
    name = await fetch_host_name(host_id)
    result = await run_ping(name)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Назад", callback_data=f"host:{host_id}")]])
    await cb.message.answer(result, reply_markup=kb)
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
    await cb.message.answer(text, reply_markup=kb)
    await cb.answer()

@dp.message(Command("graph"))
async def cmd_graph(msg: types.Message, command: Command):
    parts = (command.args or "").split()
    if not parts:
        return await msg.answer("Использование: /graph <itemid> [минут]")
    try:
        itemid = int(parts[0])
    except ValueError:
        return await msg.answer("❌ <b>Неверный itemid</b>\nИспользование: /graph <itemid> [минут]")
    minutes = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 60
    period = minutes * 60
    await msg.answer("⏳ Сбор данных и построение графика…")
    try:
        png = await zbx.chart_png(itemid, period)
        bio = BytesIO(png)
        bio.name = f"item_{itemid}.png"
        await msg.answer_document(InputFile(bio))
    except Exception as e:
        await msg.answer(f"⚠️ Ошибка при построении графика: <pre>{html.escape(str(e))}</pre>")

@dp.message(Command("help"))
async def cmd_help(msg: types.Message):
    text = (
        "<b>📖 Список команд Phystech Zabbix Bot:</b>\n\n"
        "/status — сводка открытых проблем (кнопка деталей)\n"
        "/ping &lt;host&gt; — проверить доступность хоста\n"
        "/hosts — выбрать хост для действий\n"
        "/graph &lt;itemid&gt; [минут] — построить график метрики\n"
        "/help — показать эту справку"
    )
    await msg.answer(text)

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

    text = (
        f"📡 <b>{html.escape(payload.get('subject', 'Zabbix alert'))}</b>\n"
        f"{html.escape(clean_message)}"
    )

    for chat_id in ADMIN_CHAT_IDS:
        await bot.send_message(chat_id, text)

    return JSONResponse({"ok": True})

# Startup and polling
async def on_startup():
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_my_commands([
        types.BotCommand(command="status", description="Сводка проблем"),
        types.BotCommand(command="ping",   description="Пинг хоста"),
        types.BotCommand(command="hosts",  description="Список хостов"),
        types.BotCommand(command="graph",  description="График метрики"),
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
