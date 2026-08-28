import asyncio, hashlib, hmac, json, os, sqlite3, urllib.parse
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN     = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
MINIAPP_URL   = os.getenv("MINIAPP_URL", "")
DB_PATH       = os.getenv("DB_PATH", "fss.db")
PORT          = int(os.getenv("PORT", "8080"))

bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher()

# ═══════════ БАЗА (SQLite) ═══════════
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row

def init_db():
    conn.execute("""
    CREATE TABLE IF NOT EXISTS applications (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      title TEXT DEFAULT '', link TEXT NOT NULL, story TEXT NOT NULL,
      amount INTEGER NOT NULL, raised INTEGER DEFAULT 0, supporters INTEGER DEFAULT 0,
      status TEXT NOT NULL DEFAULT 'pending',
      user_id INTEGER, username TEXT DEFAULT '', first_name TEXT DEFAULT '',
      demo INTEGER DEFAULT 0,
      created_at TEXT DEFAULT (datetime('now','localtime'))
    )""")
    conn.commit()
    if conn.execute("SELECT COUNT(*) c FROM applications").fetchone()["c"] == 0:
        seed = [
            ("Долг по ЖКХ, мать с двумя детьми","https://tips.cloudtips.ru/demo1","После развода накопились коммунальные долги. Боюсь, что зимой отключат свет и воду.",120000,48200,312),
            ("Микрозаймы после сокращения","https://tips.cloudtips.ru/demo2","Попал под сокращение, перекрыл зарплату займами. Проценты съедают всё, что нахожу.",60000,17900,128),
            ("Штраф — под угрозой автомобиль","https://tips.cloudtips.ru/demo3","Машина — единственный способ работать. Если её заберут за долг, останусь без дохода.",95000,91400,540),
            ("Кредитка после больницы","https://tips.cloudtips.ru/demo4","Три месяца без работы из-за операции. Долг по карте рос каждый день.",75000,32500,201),
        ]
        for t,l,s,a,r,sup in seed:
            conn.execute(
                "INSERT INTO applications (title,link,story,amount,raised,supporters,status,demo) VALUES (?,?,?,?,?,?, 'approved',1)",
                (t,l,s,a,r,sup))
        conn.commit()

def make_title(story):
    t = story.replace("\n"," ").split(".")[0].split("!")[0].strip()
    return (t[:60]+"…") if len(t)>60 else t

def row2dict(r): return dict(r)

# ═══════════ ПРОВЕРКА ПОДПИСИ TELEGRAM ═══════════
def verify_init_data(init_data):
    if not init_data: return None
    try:
        p = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        h = p.pop("hash", None)
        if not h: return None
        dcs = "\n".join(f"{k}={v}" for k,v in sorted(p.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest(), h):
            return None
        return json.loads(p.get("user","{}"))
    except Exception:
        return None

def get_user(req):  return verify_init_data(req.headers.get("X-Telegram-InitData",""))
def is_admin(user): return user and user.get("id") == ADMIN_CHAT_ID

# ═══════════ УВЕДОМЛЕНИЯ ═══════════
async def notify_admin(app_id):
    a = row2dict(conn.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone())
    kb = types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve:{app_id}"),
        types.InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{app_id}")]])
    try:
        await bot.send_message(ADMIN_CHAT_ID,
            f"📝 <b>Новая заявка #{a['id']}</b>\n\n💰 Цель: <b>{a['amount']:,} ₽</b>\n🔗 {a['link']}\n\n📖 {a['story']}\n\n👤 {a['first_name'] or 'Аноним'} (@{a['username'] or '—'})",
            parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        print("notify_admin error:", e)

async def notify_user(a, approved):
    if not a["user_id"]: return
    try:
        if approved:
            await bot.send_message(a["user_id"], f"🎉 Заявка <b>#{a['id']}</b> одобрена и появилась в ленте!", parse_mode="HTML")
        else:
            await bot.send_message(a["user_id"], f"😔 Заявка #{a['id']} отклонена.")
    except Exception: pass

def set_status(app_id, status):
    conn.execute("UPDATE applications SET status=? WHERE id=?", (status, app_id))
    conn.commit()
    a = row2dict(conn.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone())
    if a: asyncio.create_task(notify_user(a, status=="approved"))

# ═══════════ БОТ ═══════════
@dp.message(Command("start"))
async def cmd_start(m: types.Message):
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text=" Подать заявку", web_app=types.WebAppInfo(url=MINIAPP_URL))],
        [types.InlineKeyboardButton(text="📊 Мои заявки", callback_data="my_apps")]])
    await m.answer("👋 Взаимопомощь: анонимные сборы на закрытие долгов.\nКрути колесо добра и поддерживай — или подай свою заявку.", reply_markup=kb)

@dp.callback_query(F.data == "my_apps")
async def cb_my(cb: types.CallbackQuery):
    await cb.answer()
    rows = conn.execute("SELECT * FROM applications WHERE user_id=? ORDER BY id DESC", (cb.from_user.id,)).fetchall()
    if not rows:
        await cb.message.answer("Пока нет заявок. Подай через «Подать заявку»!"); return
    em = {"pending":"⏳","approved":"✅","rejected":"❌"}
    txt = "📊 <b>Твои заявки:</b>\n\n" + "".join(
        f"#{a['id']} — {a['amount']:,} ₽ · {em.get(a['status'],'?')}\n🔗 {a['link']}\n\n" for a in rows)
    await cb.message.answer(txt, parse_mode="HTML", disable_web_page_preview=True)

@dp.callback_query(F.data.startswith(("approve:","reject:")))
async def cb_mod(cb: types.CallbackQuery):
    if cb.from_user.id != ADMIN_CHAT_ID:
        return await cb.answer("Не твои кнопки 🙂", show_alert=True)
    act, app_id = cb.data.split(":")
    set_status(int(app_id), "approved" if act=="approve" else "rejected")
    await cb.answer("Одобрено ✅" if act=="approve" else "Отклонено ❌")
    try: await cb.message.edit_reply_markup(reply_markup=None)
    except Exception: pass

# ═══════════ API ═══════════
@web.middleware
async def cors(req, handler):
    resp = web.Response() if req.method=="OPTIONS" else await handler(req)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Telegram-InitData"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp

async def h_collections(req):
    rows = conn.execute("SELECT * FROM applications WHERE status='approved' ORDER BY id DESC").fetchall()
    return web.json_response({"ok":True,"items":[row2dict(r) for r in rows]})

async def h_my(req):
    user = get_user(req)
    if not user: return web.json_response({"ok":False,"error":"auth"}, status=401)
    rows = conn.execute("SELECT * FROM applications WHERE user_id=? ORDER BY id DESC", (user.get("id"),)).fetchall()
    return web.json_response({"ok":True,"items":[row2dict(r) for r in rows]})

async def h_submit(req):
    user = get_user(req)
    if not user: return web.json_response({"ok":False,"error":"Открой приложение из Telegram"}, status=401)
    b = await req.json()
    link, story, amount = str(b.get("link","")).strip(), str(b.get("story","")).strip(), int(b.get("amount") or 0)
    if not (link.startswith("http") and len(story)>=10 and amount>0):
        return web.json_response({"ok":False,"error":"Проверьте поля"}, status=400)
    cur = conn.execute(
        "INSERT INTO applications (title,link,story,amount,user_id,username,first_name) VALUES (?,?,?,?,?,?,?)",
        (make_title(story), link, story, amount, user.get("id"), user.get("username",""), user.get("first_name","")))
    conn.commit()
    asyncio.create_task(notify_admin(cur.lastrowid))
    return web.json_response({"ok":True,"id":cur.lastrowid})

async def h_admin_stats(req):
    if not is_admin(get_user(req)): return web.json_response({"ok":False}, status=403)
    g = lambda s: conn.execute("SELECT COUNT(*) c, COALESCE(SUM(amount),0) s FROM applications WHERE status=?", (s,)).fetchone()
    p, a = g("pending"), g("approved")
    return web.json_response({"ok":True,"pending":p["c"],"approved":a["c"],"approved_sum":a["s"]})

async def h_admin_list(req):
    if not is_admin(get_user(req)): return web.json_response({"ok":False}, status=403)
    s = req.query.get("status")
    rows = conn.execute("SELECT * FROM applications WHERE status=? ORDER BY id DESC", (s,)).fetchall() if s \
        else conn.execute("SELECT * FROM applications ORDER BY id DESC").fetchall()
    return web.json_response({"ok":True,"items":[row2dict(r) for r in rows]})

async def h_admin_action(req):
    if not is_admin(get_user(req)): return web.json_response({"ok":False}, status=403)
    b = await req.json()
    set_status(int(b["id"]), "approved" if b["action"]=="approve" else "rejected")
    return web.json_response({"ok":True})

# ═══════════ ЗАПУСК ═══════════
async def main():
    init_db()
    app = web.Application(middlewares=[cors])
    app.router.add_get("/api/health", lambda r: web.json_response({"ok":True}))
    app.router.add_get("/api/collections", h_collections)
    app.router.add_get("/api/my", h_my)
    app.router.add_post("/api/applications", h_submit)
    app.router.add_get("/api/admin/stats", h_admin_stats)
    app.router.add_get("/api/admin/list", h_admin_list)
    app.router.add_post("/api/admin/action", h_admin_action)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    print(f"🌐 API на порту {PORT}, 🤖 бот стартует…")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
