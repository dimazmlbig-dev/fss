import asyncio
import hashlib
import hmac
import json
import os
import sqlite3
import urllib.parse
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
MINIAPP_URL = os.getenv("MINIAPP_URL", "")
DB_PATH = os.getenv("DB_PATH", "fss.db")
PORT = int(os.getenv("PORT", "8080"))

bot = Bot(token=BOT_TOKEN) if BOT_TOKEN else None
np = None
if bot is None:
    print("WARNING: BOT_TOKEN is not set. Bot functionality will be disabled.")

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
    c = conn.execute("SELECT COUNT(*) c FROM applications").fetchone()[0]
    if c == 0:
        # seed some demo rows (title, link, story, amount, raised, supporters)
        seed = [
            (
                "Долг по ЖКХ, мать с двумя детьми",
                "https://tips.cloudtips.ru/demo1",
                "После развода накопились коммунальные долги. Нужно оплатить текущие счета и закрыть просрочки.",
                50000,
                50000,
                120,
            ),
            (
                "Микрозаймы после сокращения",
                "https://tips.cloudtips.ru/demo2",
                "Попал под сокращение, перекрыл зарплату займами. Нужна помощь, чтобы пережить период поиска работы.",
                30000,
                30000,
                80,
            ),
            (
                "Кредитка после больницы",
                "https://tips.cloudtips.ru/demo4",
                "Три месяца без работы из-за операции. Долг по карте вырос и нужно вернуть платежеспособность.",
                40000,
                40000,
                95,
            ),
        ]
        for t, l, s, a, r, sup in seed:
            conn.execute(
                "INSERT INTO applications (title,link,story,amount,raised,supporters,status,demo) VALUES (?,?,?,?,?,?,?,?)",
                (t, l, s, a, r, sup, 'approved', 1),
            )
        conn.commit()


def make_title(story: str) -> str:
    t = story.replace("\n", " ").split(".")[0].split("!")[0].strip()
    return (t[:60] + "…") if len(t) > 60 else t


def row2dict(r):
    return dict(r) if r else None


# ═══════════ ПРОВЕРКА ПОДПИСИ TELEGRAM (Web App) ═══════════
def verify_init_data(init_data: str):
    """Verify Telegram Web App init data string and return parsed user dict or None.

    Telegram recommends building the data_check_string from received fields and
    comparing HMAC-SHA256 with a secret derived from the bot token:
      secret_key = sha256(bot_token)
      hash = hmac_sha256(secret_key, data_check_string)
    """
    # If we don't have init data or a bot token, verification is not possible
    if not init_data or not BOT_TOKEN:
        return None
    try:
        p = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        h = p.pop("hash", None)
        if not h:
            return None
        # build data_check_string
        data_check_arr = [f"{k}={v}" for k, v in sorted(p.items())]
        data_check_string = "\n".join(data_check_arr)
        # correct secret: SHA256 of bot token
        secret_key = hashlib.sha256(BOT_TOKEN.encode("utf-8")).digest()
        calc_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc_hash, h):
            return None
        # user field is JSON string
        user_json = p.get("user", "{}")
        try:
            return json.loads(user_json)
        except Exception:
            return None
    except Exception:
        return None


def get_user(req: web.Request):
    # Telegram WebApp init data may be sent in headers (X-Telegram-InitData) or query; adapt as needed
    init_data = req.headers.get("X-Telegram-InitData") or req.query.get("init_data")
    return verify_init_data(init_data)


def is_admin(user: dict):
    return bool(user and user.get("id") and int(user.get("id")) == ADMIN_CHAT_ID)


# ═══════════ УВЕДОМЛЕНИЯ и модерация ═══════════
async def notify_admin(app_id: int):
    a_row = conn.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone()
    a = row2dict(a_row)
    if not a or bot is None:
        return
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve:{app_id}"),
            types.InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{app_id}"),
        ],
        [
            types.InlineKeyboardButton(text="👤 Профиль автора", url=f"tg://user?id={a.get('user_id')}")
        ],
    ])
    try:
        msg = (
            f"📝 <b>Новая заявка #{a['id']}</b>\n\n"
            f"💰 Цель: <b>{int(a['amount']):,} ₽</b>\n"
            f"🔗 {a['link']}\n\n"
            f"📖 {a['story'][:800]}\n\n"
            f"👤 {a.get('first_name') or 'Аноним'}"
        )
        if a.get("username"):
            msg += f" (@{a.get('username')})"
        msg += f"\n🆔 ID: <code>{a.get('user_id','—')}</code>\n\n<i>Статус: ожидает модерации</i>"
        await bot.send_message(ADMIN_CHAT_ID, msg, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        print("notify_admin error:", e)


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
    a_row = conn.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone()
    a = row2dict(a_row)
    if a:
        asyncio.create_task(notify_user(a, status == "approved"))


# ═══════════ БОТ handlers ═══════════
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
        f"#{a['id']} — {int(a['amount']):,} ₽ · {em.get(a['status'],'?')}\n🔗 {a['link']}\n\n" for a in rows
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


# ═══════════ API (aiohttp) ═══════════
@web.middleware
async def cors(req, handler):
    if req.method == "OPTIONS":
        resp = web.Response()
    else:
        resp = await handler(req)
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
        return web.json_response({"ok": False, "error": "auth"}, status=401)
    rows = conn.execute("SELECT * FROM applications WHERE user_id=? ORDER BY id DESC", (user.get("id"),)).fetchall()
    return web.json_response({"ok": True, "items": [row2dict(r) for r in rows]})


async def h_submit(req):
    user = get_user(req)
    if not user:
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
    new_id = cur.lastrowid
    # notify admin asynchronously
    asyncio.create_task(notify_admin(new_id))
    return web.json_response({"ok": True, "id": new_id})


async def h_admin_stats(req):
    if not is_admin(get_user(req)):
        return web.json_response({"ok": False}, status=403)
    g = lambda s: conn.execute("SELECT COUNT(*) c, COALESCE(SUM(amount),0) s FROM applications WHERE status=?)", (s,)).fetchone()
    p, a = g("pending"), g("approved")
    return web.json_response({"ok": True, "pending": p[0], "approved": a[0], "approved_sum": a[1]})


async def h_admin_list(req):
    if not is_admin(get_user(req)):
        return web.json_response({"ok": False}, status=403)
    s = req.query.get("status")
    if s:
        rows = conn.execute("SELECT * FROM applications WHERE status=? ORDER BY id DESC", (s,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM applications ORDER BY id DESC").fetchall()
    return web.json_response({"ok": True, "items": [row2dict(r) for r in rows]})


async def h_admin_action(req):
    if not is_admin(get_user(req)):
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
    app.router.add_get("/api/admin/stats", h_admin_stats)
    app.router.add_get("/api/admin/list", h_admin_list)
    app.router.add_post("/api/admin/action", h_admin_action)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    print(f"🌐 API на порту {PORT}, 🤖 бот стартует…")
    if bot:
        await dp.start_polling(bot)
    else:
        # keep the web server running if bot is not configured
        while True:
            await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
