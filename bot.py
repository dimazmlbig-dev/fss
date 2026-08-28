import asyncio
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import urllib.parse

from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")
log = logging.getLogger("fss")

load_dotenv()
BOT_TOKEN     = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
MINIAPP_URL   = os.getenv("MINIAPP_URL", "")
DB_PATH       = os.getenv("DB_PATH", "fss.db")
PORT          = int(os.getenv("PORT", "8080"))

bot = Bot(token=BOT_TOKEN) if BOT_TOKEN else None
if bot is None:
    log.warning("BOT_TOKEN не задан — работает только API")

dp = Dispatcher()

# ═══════════ БАЗА (SQLite) ═══════════
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row

def init_db():
    conn.execute(
        """
    CREATE TABLE IF NOT EXISTS applications (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      title TEXT DEFAULT '',
      link TEXT NOT NULL,
      story TEXT NOT NULL,
      amount INTEGER NOT NULL,
      raised INTEGER DEFAULT 0,
      supporters INTEGER DEFAULT 0,
      status TEXT NOT NULL DEFAULT 'pending',
      user_id INTEGER,
      username TEXT DEFAULT '',
      first_name TEXT DEFAULT '',
      demo INTEGER DEFAULT 0,
      created_at TEXT DEFAULT (datetime('now','localtime'))
    )"""
    )
    conn.commit()
    if conn.execute("SELECT COUNT(*) c FROM applications").fetchone()["c"] == 0:
        seed = [
            ("Долг по ЖКХ, мать с двумя детьми", "https://tips.cloudtips.ru/demo1",
             "После развода накопились коммунальные долги. Нужно оплатить текущие счета и закрыть просрочки.", 50000, 50000, 120),
            ("Микрозаймы после сокращения", "https://tips.cloudtips.ru/demo2",
             "Попал под сокращение, перекрыл зарплату займами. Нужна помощь, чтобы пережить период поиска работы.", 30000, 30000, 80),
            ("Штраф — под угрозой автомобиль", "https://tips.cloudtips.ru/demo3",
             "Машина — единственный способ работать. Если её заберут за долг, останусь без дохода совсем.", 95000, 91400, 540),
            ("Кредитка после больницы", "https://tips.cloudtips.ru/demo4",
             "Три месяца без работы из-за операции. Долг по карте вырос и нужно вернуть платежеспособность.", 40000, 40000, 95),
        ]
        for t, l, s, a, r, sup in seed:
            conn.execute(
                "INSERT INTO applications (title,link,story,amount,raised,supporters,status,demo) VALUES (?,?,?,?,?,?,?,?)",
                (t, l, s, a, r, sup, "approved", 1),
            )
        conn.commit()

def make_title(story: str) -> str:
    t = story.replace("\n", " ").split(".")[0].split("!")[0].strip()
    return (t[:60] + "…") if len(t) > 60 else t

def row2dict(r):
    return dict(r) if r else None

# ═══════════ ПРОВЕРКА ПОДПИСИ TELEGRAM ═══════════
def verify_init_data(init_data: str):
    if not init_data:
        log.debug("verify: пустой init_data (открыто вне Telegram?)")
        return None
    if not BOT_TOKEN:
        log.warning("verify: BOT_TOKEN не задан — проверка невозможна")
        return None
    try:
        p = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        h = p.pop("hash", None)
        if not h:
            log.warning("verify: в init_data нет поля hash")
            return None
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(p.items()))
        # Официальный алгоритм: secret = HMAC_SHA256(key="WebAppData", msg=bot_token)
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode("utf-8"), hashlib.sha256).digest()
        calc_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc_hash, h):
            log.warning("verify: подпись init_data не совпала")
            return None
        try:
            return json.loads(p.get("user", "{}"))
        except Exception:
            log.warning("verify: не удалось распарсить поле user")
            return None
    except Exception as e:
        log.exception("verify: неожиданная ошибка: %s", e)
        return None

def get_user(req):
    init_data = req.headers.get("X-Telegram-InitData") or req.query.get("init_data")
    return verify_init_data(init_data)

def is_admin(user):
    return bool(user and user.get("id") and int(user.get("id")) == ADMIN_CHAT_ID)

# ═══════════ УВЕДОМЛЕНИЯ И МОДЕРАЦИЯ ═══════════
async def notify_admin(app_id: int):
    a = row2dict(conn.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone())
    if not a or bot is None:
        return
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve:{app_id}"),
         types.InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{app_id}")],
        [types.InlineKeyboardButton(text="👤 Профиль автора", url=f"tg://user?id={a.get('user_id')}")],
    ])
    try:
        msg = (
            f"📝 <b>Новая заявка #{a['id']}</b>\n\n"
            f"💰 Цель: <b>{int(a['amount']):,} ₽</b>\n🔗 {a['link']}\n\n"
            f"📖 {a['story'][:800]}\n\n"
            f"👤 {a.get('first_name') or 'Аноним'}"
            + (f" (@{a.get('username')})" if a.get('username') else "")
            + f"\n🆔 ID: <code>{a.get('user_id', '—')}</code>\n\n<i>Статус: ожидает модерации</i>"
        )
        await bot.send_message(ADMIN_CHAT_ID, msg, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        log.error("notify_admin error: %s", e)

async def notify_user(a: dict, approved: bool):
    if not a or not a.get("user_id") or bot is None:
        return
    try:
        if approved:
            await bot.send_message(a["user_id"], f"🎉 Заявка <b>#{a['id']}</b> одобрена и появилась в ленте!", parse_mode="HTML")
        else:
            await bot.send_message(a["user_id"], f"😔 Заявка #{a['id']} отклонена.")
    except Exception:
        pass

def set_status(app_id: int, status: str):
    conn.execute("UPDATE applications SET status=? WHERE id=?", (status, app_id))
    conn.commit()
    a = row2dict(conn.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone())
    if a:
        asyncio.create_task(notify_user(a, status == "approved"))

# ═══════════ БОТ ═══════════
@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="📝 Подать заявку", web_app=types.WebAppInfo(url=MINIAPP_URL))],
        [types.InlineKeyboardButton(text="📊 Мои заявки", callback_data="my_apps")],
    ])
    await m.answer(
        "👋 Взаимопомощь: анонимные сборы на закрытие долгов.\n"
        "Нажми «Подать заявку», чтобы открыть форму Mini App.",
        reply_markup=kb,
    )

@dp.callback_query(F.data == "my_apps")
async def cb_my(cb: types.CallbackQuery):
    await cb.answer()
    rows = conn.execute("SELECT * FROM applications WHERE user_id=? ORDER BY id DESC", (cb.from_user.id,)).fetchall()
    if not rows:
        await cb.message.answer("Пока нет заявок. Подай через «Подать заявку»!")
        return
    em = {"pending": "⏳", "approved": "✅", "rejected": "❌"}
    txt = "📊 <b>Твои заявки:</b>\n\n" + "".join(
        f"#{a['id']} — {int(a['amount']):,} ₽ · {em.get(a['status'], '?')}\n🔗 {a['link']}\n\n" for a in rows
    )
    await cb.message.answer(txt, parse_mode="HTML", disable_web_page_preview=True)

@dp.callback_query(F.data.startswith(("approve:", "reject:")))
async def cb_mod(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_CHAT_ID:
        return await cb.answer("Не твои кнопки 🙂", show_alert=True)
    act, app_id = cb.data.split(":")
    set_status(int(app_id), "approved" if act == "approve" else "rejected")
    await cb.answer("Одобрено ✅" if act == "approve" else "Отклонено ❌")
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

# ═══════════ API ═══════════
@web.middleware
async def cors(req, handler):
    resp = web.Response() if req.method == "OPTIONS" else await handler(req)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Telegram-InitData"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp

async def h_collections(req):
    rows = conn.execute("SELECT * FROM applications WHERE status='approved' ORDER BY id DESC").fetchall()
    return web.json_response({"ok": True, "items": [row2dict(r) for r in rows]})

async def h_my(req):
    user = get_user(req)
    if not user:
        log.info("401 | /api/my")
        return web.json_response({"ok": False, "error": "auth"}, status=401)
    rows = conn.execute("SELECT * FROM applications WHERE user_id=? ORDER BY id DESC", (user.get("id"),)).fetchall()
    return web.json_response({"ok": True, "items": [row2dict(r) for r in rows]})

async def h_submit(req):
    user = get_user(req)
    if not user:
        log.info("401 | /api/applications")
        return web.json_response({"ok": False, "error": "Открой приложение из Telegram"}, status=401)
    b = await req.json()
    link = str(b.get("link", "")).strip()
    story = str(b.get("story", "")).strip()
    try:
        amount = int(b.get("amount") or 0)
    except Exception:
        amount = 0
    if not (link.startswith("http") and len(story) >= 10 and amount > 0):
        return web.json_response({"ok": False, "error": "Проверьте поля"}, status=400)
    cur = conn.execute(
        "INSERT INTO applications (title,link,story,amount,user_id,username,first_name) VALUES (?,?,?,?,?,?,?)",
        (make_title(story), link, story, amount, user.get("id"), user.get("username", ""), user.get("first_name", "")),
    )
    conn.commit()
    asyncio.create_task(notify_admin(cur.lastrowid))
    return web.json_response({"ok": True, "id": cur.lastrowid})

async def h_support(req):
    try:
        b = await req.json()
        app_id = int(b.get("id") or 0)
        amount = int(b.get("amount") or 0)
    except Exception:
        return web.json_response({"ok": False, "error": "bad params"}, status=400)
    if app_id <= 0 or amount < 0:
        return web.json_response({"ok": False, "error": "bad params"}, status=400)
    conn.execute(
        "UPDATE applications SET raised = raised + ?, supporters = supporters + 1 WHERE id=? AND status='approved'",
        (amount, app_id),
    )
    conn.commit()
    row = conn.execute("SELECT raised, supporters FROM applications WHERE id=?", (app_id,)).fetchone()
    if not row:
        return web.json_response({"ok": False, "error": "not found"}, status=404)
    log.info("support | app=%s +%s ₽ → raised=%s", app_id, amount, row[0])
    return web.json_response({"ok": True, "raised": row[0], "supporters": row[1]})

async def h_admin_stats(req):
    if not is_admin(get_user(req)):
        log.info("403 | /api/admin/stats")
        return web.json_response({"ok": False}, status=403)
    g = lambda s: conn.execute("SELECT COUNT(*) c, COALESCE(SUM(amount),0) s FROM applications WHERE status=?", (s,)).fetchone()
    p, a = g("pending"), g("approved")
    return web.json_response({"ok": True, "pending": p[0], "approved": a[0], "approved_sum": a[1]})

async def h_admin_list(req):
    if not is_admin(get_user(req)):
        log.info("403 | /api/admin/list")
        return web.json_response({"ok": False}, status=403)
    s = req.query.get("status")
    if s:
        rows = conn.execute("SELECT * FROM applications WHERE status=? ORDER BY id DESC", (s,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM applications ORDER BY id DESC").fetchall()
    return web.json_response({"ok": True, "items": [row2dict(r) for r in rows]})

async def h_admin_action(req):
    if not is_admin(get_user(req)):
        log.info("403 | /api/admin/action")
        return web.json_response({"ok": False}, status=403)
    b = await req.json()
    set_status(int(b["id"]), "approved" if b.get("action") == "approve" else "rejected")
    return web.json_response({"ok": True})

# ═══════════ ЗАПУСК ═══════════
async def main():
    init_db()
    app = web.Application(middlewares=[cors])
    app.router.add_get("/api/health", lambda r: web.json_response({"ok": True}))
    app.router.add_get("/api/collections", h_collections)
    app.router.add_get("/api/my", h_my)
    app.router.add_post("/api/applications", h_submit)
    app.router.add_post("/api/support", h_support)
    app.router.add_get("/api/admin/stats", h_admin_stats)
    app.router.add_get("/api/admin/list", h_admin_list)
    app.router.add_post("/api/admin/action", h_admin_action)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    log.info("🌐 API на порту %s, 🤖 бот стартует…", PORT)
    if bot:
        await dp.start_polling(bot)
    else:
        while True:
            await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
