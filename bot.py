import asyncio, base64, hashlib, hmac, json, logging, os, time, urllib.parse, re as _re
from collections import defaultdict, deque
import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")
log = logging.getLogger("fss")
load_dotenv()
BOT_TOKEN=(os.getenv("BOT_TOKEN") or "").strip() or None
ADMIN_CHAT_ID=int(os.getenv("ADMIN_CHAT_ID","0") or "0")
MINIAPP_URL=(os.getenv("MINIAPP_URL") or "").strip()
CHANNEL_ID=(os.getenv("CHANNEL_ID") or "").strip()
PORT=int(os.getenv("PORT","8080") or "8080")
ALLOWED_SUPPORT_AMOUNTS=(100,300,500,1000)
DAILY_COINS=10
SITE_URL=os.getenv("SITE_URL","https://xn--80af0ahbd4ib.online")
VK_APP_ID=os.getenv("VK_APP_ID","")
VK_SECRET=os.getenv("VK_SECRET","")
SECRET_KEY=os.getenv("SECRET_KEY","fss-super-secret-2026")
DB_HOST=os.getenv("DB_HOST","")
DB_PORT=int(os.getenv("DB_PORT","5432"))
DB_NAME=os.getenv("DB_NAME","postgres")
DB_USER=os.getenv("DB_USER","postgres")
DB_PASS=os.getenv("DB_PASS","")
EMAIL_RX=_re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

bot = Bot(token=BOT_TOKEN) if BOT_TOKEN else None
if bot is None: log.warning("BOT_TOKEN не задан — работает только API")
dp = Dispatcher()

METRICS={"requests":0,"cache_hit":0,"cache_miss":0,"started":time.time()}
CACHE={}
def cache_get(key,ttl):
    e=CACHE.get(key)
    if e and time.time()-e[0]<ttl: return e[1]
    return None
def cache_set(key,val): CACHE[key]=(time.time(),val)
def bust(): CACHE.clear()

def get_db():
    return psycopg2.connect(host=DB_HOST, port=DB_PORT, database=DB_NAME, user=DB_USER, password=DB_PASS)

def init_db():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM applications")
            if cur.fetchone()[0]==0:
                seed=[
                 ("Долг по ЖКХ, мать с двумя детьми","https://tips.cloudtips.ru/demo1","После развода накопились коммунальные долги. Нужно оплатить текущие счета и закрыть просрочки.",50000,50000,120),
                 ("Микрозаймы после сокращения","https://tips.cloudtips.ru/demo2","Попал под сокращение, перекрыл зарплату займами. Нужна помощь, чтобы пережить период поиска работы.",30000,30000,80),
                 ("Штраф — под угрозой автомобиль","https://tips.cloudtips.ru/demo3","Машина — единственный способ работать. Если её заберут за долг, останусь без дохода совсем.",95000,91400,540),
                 ("Кредитка после больницы","https://tips.cloudtips.ru/demo4","Три месяца без работы из-за операции. Долг по карте вырос и нужно вернуть платежеспособность.",40000,40000,95)]
                for t,l,s,a,r,sup in seed:
                    cur.execute("INSERT INTO applications (title,link,story,amount,raised,supporters,status) VALUES (%s,%s,%s,%s,%s,%s,'approved')",(t,l,s,a,r,sup))
                conn.commit()
    finally:
        conn.close()

def make_title(story):
    t=story.replace("\n"," ").split(".")[0].split("!")[0].strip()
    return (t[:60]+"…") if len(t)>60 else t

RL=defaultdict(deque)
def rate_limited(key,max_n,window):
    now=time.time(); dq=RL[key]
    while dq and now-dq[0]>window: dq.popleft()
    if len(dq)>=max_n: return True
    dq.append(now); return False

def verify_init_data(init_data):
    if not init_data: return None
    if not BOT_TOKEN: return None
    try:
        p=dict(urllib.parse.parse_qsl(init_data,keep_blank_values=True))
        h=p.pop("hash",None)
        if not h: return None
        try: auth_date=int(p.get("auth_date","0"))
        except Exception: auth_date=0
        if not auth_date or abs(time.time()-auth_date)>86400: return None
        dcs="\n".join(f"{k}={v}" for k,v in sorted(p.items()))
        secret=hmac.new(b"WebAppData",BOT_TOKEN.encode("utf-8"),hashlib.sha256).digest()
        calc=hmac.new(secret,dcs.encode("utf-8"),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc,h): return None
        return json.loads(p.get("user","{}"))
    except Exception: return None

def make_token(uid,name=""):
    payload=base64.urlsafe_b64encode(json.dumps({"uid":uid,"name":name,"exp":int(time.time())+30*86400}).encode()).decode()
    sig=hmac.new(SECRET_KEY.encode(),payload.encode(),hashlib.sha256).hexdigest()[:32]
    return payload+"."+sig

def parse_token(tok):
    try:
        payload,sig=tok.split(".")
        if not hmac.compare_digest(hmac.new(SECRET_KEY.encode(),payload.encode(),hashlib.sha256).hexdigest()[:32],sig): return None
        d=json.loads(base64.urlsafe_b64decode(payload))
        if d["exp"]<time.time(): return None
        return d
    except Exception: return None

def get_user(req):
    u=verify_init_data(req.headers.get("X-Telegram-InitData") or req.query.get("init_data") or "")
    if u: return u
    tok=(req.headers.get("Authorization") or "").replace("Bearer ","").strip() or (req.headers.get("X-Auth-Token") or "").strip()
    if tok:
        d=parse_token(tok)
        if d: return {"id":d["uid"],"first_name":d.get("name",""),"username":"","web":d["uid"]<0}
    return None

def is_admin(user):
    return bool(user and user.get("id") and int(user.get("id"))==ADMIN_CHAT_ID)

async def notify_admin(app_id):
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM applications WHERE id=%s",(app_id,))
            a=cur.fetchone()
    finally:
        conn.close()
    if not a or bot is None: return
    kb=types.InlineKeyboardMarkup(inline_keyboard=[
      [types.InlineKeyboardButton(text="✅ Одобрить",callback_data=f"approve:{app_id}"),
       types.InlineKeyboardButton(text="❌ Отклонить",callback_data=f"reject:{app_id}")],
      [types.InlineKeyboardButton(text="👤 Профиль автора",url=f"tg://user?id={a.get('user_id')}")]])
    try:
        await bot.send_message(ADMIN_CHAT_ID,
          f"📝 <b>Новая заявка #{a['id']}</b>\n\n💰 Цель: <b>{int(a['amount']):,} ₽</b>\n🔗 {a['link']}\n\n📖 {a['story'][:800]}\n\n👤 {a.get('first_name') or 'Аноним'}\n🆔 <code>{a.get('user_id','—')}</code>",
          parse_mode="HTML",reply_markup=kb)
    except Exception as e: log.error("notify_admin: %s",e)

async def notify_user(a,approved):
    if not a or not a.get("user_id") or a["user_id"]<0 or bot is None: return
    try:
        if approved: await bot.send_message(a["user_id"],f"🎉 Заявка <b>#{a['id']}</b> одобрена и появилась в ленте!",parse_mode="HTML")
        else: await bot.send_message(a["user_id"],f"😔 Заявка #{a['id']} отклонена.")
    except Exception: pass

async def notify_channel(a):
    if not CHANNEL_ID or bot is None: return
    kb=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="❤️ Поддержать",url=a["link"] or MINIAPP_URL)]])
    try:
        await bot.send_message(CHANNEL_ID,
          f"🤝 <b>Новый сбор</b>\n\n{a['title']}\n\n📖 {a['story'][:500]}\n\n💰 Цель: <b>{int(a['amount']):,} ₽</b>",
          parse_mode="HTML",reply_markup=kb,disable_web_page_preview=True)
    except Exception as e: log.error("notify_channel: %s",e)

def set_status(app_id,status):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE applications SET status=%s WHERE id=%s",(status,app_id))
            conn.commit()
            cur.execute("SELECT * FROM applications WHERE id=%s",(app_id,))
            a=cur.fetchone()
    finally:
        conn.close()
    bust()
    if a:
        asyncio.create_task(notify_user(a,status=="approved"))
        if status=="approved": asyncio.create_task(notify_channel(a))

@dp.message(Command("start"))
async def cmd_start(m):
    kb=types.InlineKeyboardMarkup(inline_keyboard=[
      [types.InlineKeyboardButton(text="📝 Подать заявку",web_app=types.WebAppInfo(url=MINIAPP_URL))],
      [types.InlineKeyboardButton(text="📊 Мои заявки",callback_data="my_apps")]])
    await m.answer("👋 Взаимопомощь: анонимные сборы на закрытие долгов.",reply_markup=kb)

@dp.callback_query(F.data=="my_apps")
async def cb_my(cb):
    await cb.answer()
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM applications WHERE user_id=%s ORDER BY id DESC",(cb.from_user.id,))
            rows=cur.fetchall()
    finally:
        conn.close()
    if not rows: return await cb.message.answer("Пока нет заявок. Подай через «Подать заявку»!")
    em={"pending":"⏳","approved":"✅","rejected":"❌","hidden":"🚩"}
    await cb.message.answer("📊 <b>Твои заявки:</b>\n\n"+"".join(
      f"#{a['id']} — {int(a['amount']):,} ₽ · {em.get(a['status'],'?')}\n🔗 {a['link']}\n\n" for a in rows),
      parse_mode="HTML",disable_web_page_preview=True)

@dp.callback_query(F.data.startswith(("approve:","reject:")))
async def cb_mod(cb):
    if cb.from_user.id!=ADMIN_CHAT_ID: return await cb.answer("Не твои кнопки 🙂",show_alert=True)
    act,app_id=cb.data.split(":")
    set_status(int(app_id),"approved" if act=="approve" else "rejected")
    await cb.answer("Одобрено ✅" if act=="approve" else "Отклонено ❌")
    try: await cb.message.edit_reply_markup(reply_markup=None)
    except Exception: pass

@web.middleware
async def limiter(req,handler):
    METRICS["requests"]+=1
    ip=req.remote or "unknown"
    if rate_limited("ip:"+ip,120,60): return web.json_response({"ok":False,"error":"too many requests"},status=429)
    return await handler(req)

@web.middleware
async def cors(req,handler):
    resp=web.Response() if req.method=="OPTIONS" else await handler(req)
    resp.headers["Access-Control-Allow-Origin"]="*"
    resp.headers["Access-Control-Allow-Headers"]="Content-Type, X-Telegram-InitData, Authorization, X-Auth-Token"
    resp.headers["Access-Control-Allow-Methods"]="GET, POST, OPTIONS"
    resp.headers["X-Content-Type-Options"]="nosniff"
    resp.headers["X-Frame-Options"]="DENY"
    resp.headers["Referrer-Policy"]="no-referrer"
    return resp

async def h_health(req):
    try:
        conn=get_db()
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        conn.close()
        return web.json_response({"ok":True,"db":True})
    except Exception as e:
        return web.json_response({"ok":True,"db":False,"error":str(e)})

async def h_metrics(req):
    if not is_admin(get_user(req)): return web.json_response({"ok":False},status=403)
    return web.json_response({"ok":True,"requests":METRICS["requests"],"cache_hit":METRICS["cache_hit"],"cache_miss":METRICS["cache_miss"],"uptime":int(time.time()-METRICS["started"])})

async def h_config(req):
    return web.json_response({"ok":True,"vk_app_id":VK_APP_ID,"site":SITE_URL})

async def h_auth_register(req):
    if rate_limited("reg:"+(req.remote or ""),5,3600): return web.json_response({"ok":False,"error":"Слишком много попыток"},status=429)
    b=await req.json()
    email=str(b.get("email","")).strip().lower()[:255]; password=str(b.get("password","")); name=str(b.get("name","")).strip()[:100]
    if not EMAIL_RX.match(email) or len(password)<6: return web.json_response({"ok":False,"error":"Проверь email и пароль (мин. 6 символов)"},status=400)
    pw=hashlib.sha256(password.encode()).hexdigest()
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM users WHERE email=%s",(email,))
            if cur.fetchone(): return web.json_response({"ok":False,"error":"Email уже занят"},status=400)
            cur.execute("INSERT INTO users (email,password_hash,first_name,auth_type) VALUES (%s,%s,%s,'email') RETURNING user_id",(email,pw,name or email.split("@")[0]))
            uid=cur.fetchone()[0]; conn.commit()
    finally:
        conn.close()
    return web.json_response({"ok":True,"token":make_token(-uid,name or email.split("@")[0])})

async def h_auth_login(req):
    b=await req.json()
    email=str(b.get("email","")).strip().lower()[:255]; password=str(b.get("password",""))
    pw=hashlib.sha256(password.encode()).hexdigest()
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE email=%s AND password_hash=%s",(email,pw))
            row=cur.fetchone()
    finally:
        conn.close()
    if not row: return web.json_response({"ok":False,"error":"Неверный email или пароль"},status=401)
    return web.json_response({"ok":True,"token":make_token(-row["user_id"],row["first_name"])})

async def h_auth_tg(req):
    b=await req.json()
    h=b.pop("hash",None)
    if not h or not BOT_TOKEN: return web.json_response({"ok":False,"error":"bad params"},status=400)
    try:
        dcs="\n".join(f"{k}={b[k]}" for k in sorted(b))
        secret=hashlib.sha256(BOT_TOKEN.encode()).digest()
        calc=hmac.new(secret,dcs.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc,h): return web.json_response({"ok":False,"error":"hash"},status=401)
        if abs(time.time()-int(b.get("auth_date",0)))>86400: return web.json_response({"ok":False,"error":"expired"},status=401)
    except Exception: return web.json_response({"ok":False,"error":"bad params"},status=400)
    tg_id=str(b.get("id")); name=(str(b.get("first_name",""))+" "+str(b.get("last_name",""))).strip() or str(b.get("username","")) or "TG"
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (telegram_id,username,first_name,auth_type) VALUES (%s,%s,%s,'telegram') ON CONFLICT (telegram_id) DO UPDATE SET username=EXCLUDED.username,first_name=EXCLUDED.first_name RETURNING user_id",(tg_id,str(b.get("username","")),name))
            conn.commit()
    finally:
        conn.close()
    return web.json_response({"ok":True,"token":make_token(int(tg_id),name)})

async def h_auth_vk(req):
    code=req.query.get("code")
    redir="https://fss-b1mw.onrender.com/api/auth/vk"
    if not code or not VK_APP_ID: raise web.HTTPFound(SITE_URL+"/#auth=err")
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://oauth.vk.com/access_token?client_id={VK_APP_ID}&client_secret={VK_SECRET}&code={code}&redirect_uri={urllib.parse.quote(redir,safe='')}&v=5.131") as r: j=await r.json()
            if "access_token" not in j: raise web.HTTPFound(SITE_URL+"/#auth=err")
            vk_uid=str(j.get("user_id","")); email=str(j.get("email",""))
            name="VK "+vk_uid
            try:
                async with s.get(f"https://api.vk.com/method/users.get?user_ids={vk_uid}&access_token={j['access_token']}&v=5.131") as r2: uj=await r2.json()
                u0=(uj.get("response") or [{}])[0]
                name=((u0.get("first_name","")+" "+u0.get("last_name","")).strip()) or name
            except Exception: pass
    except web.HTTPFound: raise
    except Exception: raise web.HTTPFound(SITE_URL+"/#auth=err")
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (vk_id,email,first_name,auth_type) VALUES (%s,%s,%s,'vk') ON CONFLICT (vk_id) DO UPDATE SET first_name=EXCLUDED.first_name RETURNING user_id",(vk_uid,email,name))
            uid=cur.fetchone()[0]; conn.commit()
    finally:
        conn.close()
    raise web.HTTPFound(SITE_URL+f"/#token={make_token(-uid,name)}")

async def h_collections(req):
    hit=cache_get("collections",30)
    if hit is not None:
        METRICS["cache_hit"]+=1
        return web.json_response(hit)
    METRICS["cache_miss"]+=1
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM applications WHERE status='approved' ORDER BY id DESC")
            rows=cur.fetchall()
    finally:
        conn.close()
    items=[]
    for r in rows:
        r["raised"]=(r.get("raised") or 0)+(r.get("ct_raised") or 0)
        items.append(r)
    payload={"ok":True,"items":items}
    cache_set("collections",payload)
    return web.json_response(payload)

async def h_my(req):
    user=get_user(req)
    if not user: return web.json_response({"ok":False,"error":"auth"},status=401)
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM applications WHERE user_id=%s ORDER BY id DESC",(user.get("id"),))
            rows=cur.fetchall()
    finally:
        conn.close()
    return web.json_response({"ok":True,"items":rows})

async def h_submit(req):
    user=get_user(req)
    if not user: return web.json_response({"ok":False,"error":"Войди в аккаунт"},status=401)
    if rate_limited(f"sub:{user.get('id')}",5,600): return web.json_response({"ok":False,"error":"Слишком много заявок"},status=429)
    b=await req.json()
    link=str(b.get("link","")).strip()[:300]; story=str(b.get("story","")).strip()[:500]
    try: amount=int(b.get("amount") or 0)
    except Exception: amount=0
    if not (link.startswith("https://") and len(story)>=10 and 0<amount<=10_000_000):
        return web.json_response({"ok":False,"error":"Проверьте поля"},status=400)
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO applications (user_id,title,link,story,amount,username,first_name) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
              (user.get("id"),make_title(story),link,story,amount,user.get("username",""),user.get("first_name","")))
            app_id=cur.fetchone()[0]
            conn.commit()
    finally:
        conn.close()
    asyncio.create_task(notify_admin(app_id))
    return web.json_response({"ok":True,"id":app_id})

async def h_support(req):
    user=get_user(req)
    if not user: return web.json_response({"ok":False,"error":"auth"},status=401)
    if rate_limited(f"supfast:{user.get('id')}",1,3) or rate_limited(f"sup:{user.get('id')}",30,3600):
        return web.json_response({"ok":False,"error":"Помедленнее 🐢"},status=429)
    try:
        b=await req.json(); app_id=int(b.get("id") or 0); amount=int(b.get("amount") or 0)
    except Exception: return web.json_response({"ok":False,"error":"bad params"},status=400)
    if app_id<=0 or amount not in ALLOWED_SUPPORT_AMOUNTS: return web.json_response({"ok":False,"error":"bad params"},status=400)
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("UPDATE applications SET raised=raised+%s, supporters=supporters+1 WHERE id=%s AND status='approved' RETURNING raised,supporters",(amount,app_id))
            row=cur.fetchone()
            cur.execute("INSERT INTO supports(user_id,app_id,amount) VALUES(%s,%s,%s)",(user.get("id"),app_id,amount))
            conn.commit()
    finally:
        conn.close()
    bust()
    if not row: return web.json_response({"ok":False,"error":"not found"},status=404)
    return web.json_response({"ok":True,"raised":row["raised"],"supporters":row["supporters"]})

async def h_top(req):
    hit=cache_get("top",60)
    if hit is not None:
        METRICS["cache_hit"]+=1
        return web.json_response(hit)
    METRICS["cache_miss"]+=1
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""SELECT s.user_id, SUM(s.amount) as sum, u.first_name, u.username
              FROM supports s LEFT JOIN users u ON u.telegram_id=CAST(s.user_id AS TEXT)
              WHERE s.created_at >= NOW() - INTERVAL '7 days'
              GROUP BY s.user_id, u.first_name, u.username ORDER BY sum DESC LIMIT 5""")
            rows=cur.fetchall()
    finally:
        conn.close()
    payload={"ok":True,"items":rows}
    cache_set("top",payload)
    return web.json_response(payload)

async def h_coins_claim(req):
    user=get_user(req)
    if not user: return web.json_response({"ok":False,"error":"auth"},status=401)
    uid=user.get("id"); today=time.strftime("%Y-%m-%d")
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM users WHERE telegram_id=%s",(str(uid) if uid>0 else None,))
            row=cur.fetchone()
            if row and row["last_claim"]==today:
                return web.json_response({"ok":True,"coins":row["coins"],"streak":row["streak"],"claimed":True})
            yesterday=time.strftime("%Y-%m-%d",time.localtime(time.time()-86400))
            streak=(row["streak"]+1) if (row and row["last_claim"]==yesterday) else 1
            earned=DAILY_COINS+min(streak,5)
            coins=(row["coins"] if row else 0)+earned
            if uid>0:
                cur.execute("""INSERT INTO users (telegram_id,username,first_name,coins,streak,last_claim) VALUES (%s,%s,%s,%s,%s,%s)
                  ON CONFLICT (telegram_id) DO UPDATE SET coins=EXCLUDED.coins,streak=EXCLUDED.streak,last_claim=EXCLUDED.last_claim,username=EXCLUDED.username,first_name=EXCLUDED.first_name""",
                  (str(uid),user.get("username",""),user.get("first_name",""),coins,streak,today))
            else:
                cur.execute("UPDATE users SET coins=%s,streak=%s,last_claim=%s WHERE user_id=%s",(coins,streak,today,-uid))
            conn.commit()
    finally:
        conn.close()
    return web.json_response({"ok":True,"coins":coins,"streak":streak,"claimed":False,"earned":earned})

async def h_comments_get(req):
    app_id=int(req.query.get("app") or 0)
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT name,text,created_at FROM comments WHERE app_id=%s ORDER BY id DESC LIMIT 50",(app_id,))
            rows=cur.fetchall()
    finally:
        conn.close()
    return web.json_response({"ok":True,"items":rows})

async def h_comments_post(req):
    user=get_user(req)
    if not user: return web.json_response({"ok":False,"error":"auth"},status=401)
    if rate_limited(f"com:{user.get('id')}",10,3600): return web.json_response({"ok":False,"error":"Помедленнее 🐢"},status=429)
    b=await req.json()
    app_id=int(b.get("app_id") or 0); text=str(b.get("text") or "").strip()[:300]
    if app_id<=0 or len(text)<2: return web.json_response({"ok":False,"error":"bad params"},status=400)
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO comments(app_id,user_id,name,text) VALUES(%s,%s,%s,%s)",
              (app_id,user.get("id"),user.get("first_name") or "Аноним",text))
            conn.commit()
    finally:
        conn.close()
    return web.json_response({"ok":True})

async def h_report(req):
    user=get_user(req)
    if not user: return web.json_response({"ok":False,"error":"auth"},status=401)
    if rate_limited(f"rep:{user.get('id')}",5,3600): return web.json_response({"ok":False,"error":"Лимит жалоб"},status=429)
    b=await req.json(); app_id=int(b.get("app_id") or 0)
    if app_id<=0: return web.json_response({"ok":False},status=400)
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM reports WHERE app_id=%s AND user_id=%s",(app_id,user.get("id")))
            if cur.fetchone(): return web.json_response({"ok":True,"already":True})
            cur.execute("INSERT INTO reports (app_id,user_id) VALUES (%s,%s)",(app_id,user.get("id")))
            cur.execute("SELECT COUNT(*) FROM reports WHERE app_id=%s",(app_id,))
            cnt=cur.fetchone()[0]
            if cnt>=2:
                cur.execute("UPDATE applications SET status='hidden' WHERE id=%s",(app_id,))
            conn.commit()
    finally:
        conn.close()
    bust()
    if cnt>=2 and bot:
        try: await bot.send_message(ADMIN_CHAT_ID,f"🚩 Сбор #{app_id} скрыт автоматически: {cnt} жалобы. Проверь вручную.")
        except Exception: pass
    return web.json_response({"ok":True,"count":cnt})

async def h_account(req):
    user=get_user(req)
    if not user: return web.json_response({"ok":False,"error":"auth"},status=401)
    uid=user["id"]
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if uid>0:
                cur.execute("SELECT user_id,username,first_name,email,coins,streak,auth_type,created_at FROM users WHERE telegram_id=%s",(str(uid),))
            else:
                cur.execute("SELECT user_id,username,first_name,email,coins,streak,auth_type,created_at FROM users WHERE user_id=%s",(-uid,))
            acc=cur.fetchone()
            cur.execute("SELECT * FROM applications WHERE user_id=%s ORDER BY id DESC",(uid,))
            apps=cur.fetchall()
    finally:
        conn.close()
    return web.json_response({"ok":True,"account":acc,"applications":apps})

async def h_admin_stats(req):
    if not is_admin(get_user(req)): return web.json_response({"ok":False},status=403)
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT COUNT(*) as c, COALESCE(SUM(amount),0) as s FROM applications WHERE status=%s",("pending",))
            p=cur.fetchone()
            cur.execute("SELECT COUNT(*) as c, COALESCE(SUM(amount),0) as s FROM applications WHERE status=%s",("approved",))
            a=cur.fetchone()
            cur.execute("SELECT COUNT(*) as c FROM applications WHERE status=%s",("hidden",))
            h=cur.fetchone()
    finally:
        conn.close()
    return web.json_response({"ok":True,"pending":p["c"],"approved":a["c"],"approved_sum":a["s"],"hidden":h["c"]})

async def h_admin_list(req):
    if not is_admin(get_user(req)): return web.json_response({"ok":False},status=403)
    s=req.query.get("status")
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if s: cur.execute("SELECT * FROM applications WHERE status=%s ORDER BY id DESC",(s,))
            else: cur.execute("SELECT * FROM applications ORDER BY id DESC")
            rows=cur.fetchall()
    finally:
        conn.close()
    return web.json_response({"ok":True,"items":rows})

async def h_admin_action(req):
    if not is_admin(get_user(req)): return web.json_response({"ok":False},status=403)
    b=await req.json(); set_status(int(b["id"]),"approved" if b.get("action")=="approve" else "rejected")
    return web.json_response({"ok":True})

async def h_admin_delete(req):
    if not is_admin(get_user(req)): return web.json_response({"ok":False},status=403)
    try: app_id=int((await req.json()).get("id") or 0)
    except Exception: app_id=0
    if app_id<=0: return web.json_response({"ok":False,"error":"bad params"},status=400)
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM applications WHERE id=%s",(app_id,))
            conn.commit()
    finally:
        conn.close()
    bust()
    return web.json_response({"ok":True})

async def h_debug_auth(req):
    init_data=req.headers.get("X-Telegram-InitData") or ""
    rep={"header_present":bool(init_data),"len":len(init_data),"token_set":bool(BOT_TOKEN),"admin_id":ADMIN_CHAT_ID}
    if init_data:
        p=dict(urllib.parse.parse_qsl(init_data,keep_blank_values=True))
        h=p.pop("hash",None)
        rep["auth_date"]=p.get("auth_date"); rep["now"]=int(time.time())
        try: rep["user_id"]=json.loads(p.get("user","{}")).get("id")
        except Exception: rep["user_id"]=None
        if h and BOT_TOKEN:
            dcs="\n".join(f"{k}={v}" for k,v in sorted(p.items()))
            secret=hmac.new(b"WebAppData",BOT_TOKEN.encode("utf-8"),hashlib.sha256).digest()
            rep["hash_match"]=hmac.compare_digest(hmac.new(secret,dcs.encode("utf-8"),hashlib.sha256).hexdigest(),h)
    return web.json_response(rep)

async def main():
    init_db()
    app=web.Application(middlewares=[limiter,cors],client_max_size=64*1024)
    app.router.add_get("/api/health",h_health)
    app.router.add_get("/api/metrics",h_metrics)
    app.router.add_get("/api/config",h_config)
    app.router.add_post("/api/auth/register",h_auth_register)
    app.router.add_post("/api/auth/login",h_auth_login)
    app.router.add_post("/api/auth/tg",h_auth_tg)
    app.router.add_get("/api/auth/vk",h_auth_vk)
    app.router.add_get("/api/debug_auth",h_debug_auth)
    app.router.add_get("/api/collections",h_collections)
    app.router.add_get("/api/my",h_my)
    app.router.add_post("/api/applications",h_submit)
    app.router.add_post("/api/support",h_support)
    app.router.add_get("/api/top",h_top)
    app.router.add_post("/api/coins/claim",h_coins_claim)
    app.router.add_get("/api/comments",h_comments_get)
    app.router.add_post("/api/comments",h_comments_post)
    app.router.add_post("/api/report",h_report)
    app.router.add_get("/api/account",h_account)
    app.router.add_get("/api/admin/stats",h_admin_stats)
    app.router.add_get("/api/admin/list",h_admin_list)
    app.router.add_post("/api/admin/action",h_admin_action)
    app.router.add_post("/api/admin/delete",h_admin_delete)
    runner=web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner,"0.0.0.0",PORT).start()
    log.info("🌐 API на порту %s (без коммерции), 🤖 бот стартует…",PORT)
    if bot: await dp.start_polling(bot)
    else:
        while True: await asyncio.sleep(3600)

if __name__=="__main__":
    asyncio.run(main())