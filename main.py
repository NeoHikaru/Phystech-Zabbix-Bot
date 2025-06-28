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
BOT_TOKEN = os.getenv("BOT_TOKEN")
# ADMIN_CHAT_IDS: comma-separated list of chat IDs
ADMIN_CHAT_IDS = [int(cid) for cid in os.getenv("ADMIN_CHAT_IDS", "").split(",") if cid.strip()]
ZABBIX_WEB = os.getenv("ZABBIX_WEB")  # e.g. https://zabbix.example.com

# Initialize Bot and Dispatcher
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# Initialize FastAPI app
app = FastAPI()

# Aiogram command handlers
@dp.message(Command("status"))
async def cmd_status(msg: types.Message):
    params = {
        "output": ["eventid", "name", "severity", "clock"],
        "sortfield": "clock",
        "sortorder": "DESC",
    }
    # Try requesting hosts directly; older Zabbix versions don't support it
    problems = await zbx.call("problem.get", {**params, "selectHosts": ["name"]})
    if not problems:
        problems = await zbx.call("problem.get", params)
        event_ids = [p["eventid"] for p in problems]
        hosts_info = await zbx.call(
            "event.get",
            {"output": ["eventid"], "eventids": event_ids, "selectHosts": ["name"]},
        )
        host_map = {e["eventid"]: e.get("hosts", [{}])[0].get("name") for e in hosts_info}
    else:
        host_map = {
            p["eventid"]: p.get("hosts", [{}])[0].get("name") for p in problems
        }

    sev_map = {
        0: "Не классифицировано",
        1: "Информация",
        2: "Предупреждение",
        3: "Средняя",
        4: "Высокая",
        5: "Критическая",
    }

    counts = {name: 0 for name in sev_map.values()}
    details = []
    for pr in problems:
        sev_name = sev_map[int(pr["severity"])]
        counts[sev_name] += 1

        host = host_map.get(pr["eventid"], "?")
        clock = int(pr.get("clock", 0))
        ts = datetime.datetime.fromtimestamp(clock).strftime("%Y-%m-%d %H:%M")
        details.append(
            f"{sev_name} — <b>{html.escape(host)}</b>: {html.escape(pr['name'])} ({ts})"
        )

    lines = [f"{k}: <b>{v}</b>" for k, v in counts.items() if v]

    text = "✅ Проблем нет" if not lines else "🖥 <b>Сводка проблем</b>\n" + "\n".join(lines)
    if details:
        text += "\n\n<b>Текущие проблемы:</b>\n" + "\n".join(details[:15])

    await msg.answer(text)

@dp.message(Command("ping"))
async def cmd_ping(msg: types.Message, command: Command):
    if not command.args:
        return await msg.answer("Использование: /ping &lt;host&gt;")
    host = command.args.strip()
    proc = await asyncio.create_subprocess_exec(
        "ping", "-c", "4", host,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    ok = proc.returncode == 0
    reply = f"{'✅' if ok else '❌'} <b>{html.escape(host)}</b>\n<pre>{html.escape((out or err).decode())}</pre>"
    await msg.answer(reply)

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
        "/status — сводка и список открытых проблем\n"
        "/ping &lt;host&gt; — проверить доступность хоста\n"
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
