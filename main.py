import asyncio
import os
import html
from io import BytesIO

import zbx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.types import InputFile
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
# ADMIN_CHAT_IDS: comma-separated list of chat IDs
ADMIN_CHAT_IDS = [int(cid) for cid in os.getenv("ADMIN_CHAT_IDS", "").split(",") if cid.strip()]
ZABBIX_WEB = os.getenv("ZABBIX_WEB")  # e.g. https://zabbix.example.com

# Initialize Bot and Dispatcher
bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()

# Initialize FastAPI app
app = FastAPI()

# Aiogram command handlers
@dp.message(Command("status"))
async def cmd_status(msg: types.Message):
    problems = await zbx.call("problem.get", {"output": ["severity"]})
    sev_map = {0: "Не классифицировано", 1: "Информация", 2: "Предупреждение", 3: "Средняя", 4: "Высокая", 5: "Критическая"}
    counts = {name: 0 for name in sev_map.values()}
    for pr in problems:
        counts[sev_map[int(pr["severity"])]] += 1
    lines = [f"{k}: <b>{v}</b>" for k, v in counts.items() if v]
    text = "✅ Проблем нет" if not lines else "🖥 <b>Сводка проблем</b>\n" + "\n".join(lines)
    await msg.answer(text)

@dp.message(Command("ping"))
async def cmd_ping(msg: types.Message, command: Command):
    if not command.args:
        return await msg.answer("Использование: /ping <host>")
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
        "/status — сводка открытых проблем\n"
        "/ping <host> — проверить доступность хоста\n"
        "/graph <itemid> [минут] — построить график метрики\n"
        "/help — показать эту справку"
    )
    await msg.answer(text)

# FastAPI endpoints
@app.get("/healthz")
def health():
    return {"status": "ok"}

@app.post("/zabbix")
async def zabbix_alert(req: Request):
    payload = await req.json()
    text = f"📡 <b>{html.escape(payload.get('subject', 'Zabbix alert'))}</b>\n{html.escape(str(payload.get('message', payload)))}"
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
