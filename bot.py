import asyncio
import json
import os
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from dotenv import load_dotenv

# Загружаем переменные из .env
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID"))
MINIAPP_URL = os.getenv("MINIAPP_URL")

# Файл, где будут храниться все заявки
DATA_FILE = Path("applications.json")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# ─────────────────────────────────────────────
# Работа с хранилищем (простой JSON-файл)
# ─────────────────────────────────────────────
def load_data() -> dict:
    """Загрузить данные из файла, если его нет — пустая структура."""
    if DATA_FILE.exists():
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"applications": []}


def save_data(data: dict):
    """Сохранить данные в файл."""
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def add_application(payload: dict) -> int:
    """Добавить заявку и вернуть её ID."""
    data = load_data()
    apps = data["applications"]
    new_id = (max(a["id"] for a in apps) + 1) if apps else 1
    apps.append({
        "id": new_id,
        "link": payload["link"],
        "story": payload["story"],
        "amount": payload["amount"],
        "status": "pending",          # pending → approved → rejected
        "user_id": payload.get("user", {}).get("id"),
        "username": payload.get("user", {}).get("username", ""),
        "first_name": payload.get("user", {}).get("first_name", ""),
        "created_at": datetime.now().isoformat(),
    })
    save_data(data)
    return new_id


# ─────────────────────────────────────────────
# Команды бота
# ─────────────────────────────────────────────
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Приветствие с кнопкой Mini App."""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(
                text="📝 Подать заявку",
                web_app=WebAppInfo(url=MINIAPP_URL)
            )],
            [KeyboardButton(text="📊 Мои заявки")],
        ],
        resize_keyboard=True,
    )
    await message.answer(
        "👋 Привет! Это бот взаимопомощи.\n\n"
        "Нажми «Подать заявку», чтобы открыть форму.",
        reply_markup=keyboard,
    )


@dp.message(F.text == "📊 Мои заявки")
async def cmd_my_apps(message: types.Message):
    """Показать заявки текущего пользователя."""
    data = load_data()
    my_apps = [a for a in data["applications"] if a["user_id"] == message.from_user.id]

    if not my_apps:
        await message.answer("У тебя пока нет заявок. Подай первую через кнопку «Подать заявку»!")
        return

    status_emoji = {"pending": "⏳", "approved": "✅", "rejected": "❌"}
    text = "📊 <b>Твои заявки:</b>\n\n"
    for a in my_apps:
        text += (
            f"#{a['id']} — {a['amount']:,} ₽\n"
            f"Статус: {status_emoji.get(a['status'], '?')} {a['status']}\n"
            f"🔗 <a href=\"{a['link']}\">Цель</a>\n\n"
        )
    await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)


# ─────────────────────────────────────────────
# Приём данных из Mini App
# ─────────────────────────────────────────────
@dp.message(F.web_app_data)
async def handle_webapp(message: types.Message):
    """Главный обработчик: получает данные из Mini App."""
    try:
        raw = message.web_app_data.data
        payload = json.loads(raw)

        if payload.get("type") != "new_application":
            await message.answer("Неизвестный тип данных")
            return

        # 1. Сохраняем заявку
        app_id = add_application(payload)

        # 2. Формируем сообщение админу
        user = payload.get("user") or {}
        admin_text = (
            f"📝 <b>Новая заявка #{app_id}</b>\n\n"
            f"💰 Сумма: <b>{payload['amount']:,} ₽</b>\n"
            f"🔗 Ссылка: {payload['link']}\n\n"
            f"📖 <b>История:</b>\n{payload['story']}\n\n"
            f"👤 <b>От:</b> {user.get('first_name', 'Аноним')}"
            f"{' (@' + user.get('username') + ')' if user.get('username') else ''}\n"
            f"🆔 ID: <code>{user.get('id', '—')}</code>\n\n"
            f"<i>Статус: ожидает модерации</i>"
        )

        # 3. Отправляем админу с кнопками «Одобрить / Отклонить»
        inline_kb = types.InlineKeyboardMarkup(inline_keyboard=[
            [
                types.InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve:{app_id}"),
                types.InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{app_id}"),
            ],
            [types.InlineKeyboardButton(text="👤 Профиль автора", url=f"tg://user?id={user.get('id')}")]
        ])

        await bot.send_message(ADMIN_CHAT_ID, admin_text, parse_mode="HTML", reply_markup=inline_kb)

        # 4. Подтверждение пользователю
        await message.answer(
            f"✅ Заявка <b>#{app_id}</b> отправлена!\n"
            f"Ожидай модерации — админ проверит её в течение 24 часов.\n\n"
            f"Посмотреть статус: /start → «Мои заявки»",
            parse_mode="HTML",
        )

    except Exception as e:
        await message.answer(f"❌ Ошибка обработки: {e}")
        await bot.send_message(ADMIN_CHAT_ID, f"⚠️ Ошибка в web_app_data:\n{e}\nRaw: {raw}")


# ─────────────────────────────────────────────
# Действия админа (одобрить / отклонить)
# ─────────────────────────────────────────────
@dp.callback_query(F.data.startswith("approve:"))
async def cb_approve(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_CHAT_ID:
        return await callback.answer("Недостаточно прав", show_alert=True)

    app_id = int(callback.data.split(":")[1])
    data = load_data()
    for a in data["applications"]:
        if a["id"] == app_id:
            a["status"] = "approved"
            # уведомим автора
            if a.get("user_id"):
                try:
                    await bot.send_message(
                        a["user_id"],
                        f"🎉 Твоя заявка <b>#{app_id}</b> одобрена!\n"
                        f"Она появится в ленте сборов.\n"
                        f"🔗 {a['link']}",
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
            break
    save_data(data)
    await callback.message.edit_text(callback.message.text + "\n\n✅ <b>Одобрено</b>", parse_mode="HTML")
    await callback.answer("Одобрено")


@dp.callback_query(F.data.startswith("reject:"))
async def cb_reject(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_CHAT_ID:
        return await callback.answer("Недостаточно прав", show_alert=True)

    app_id = int(callback.data.split(":")[1])
    data = load_data()
    for a in data["applications"]:
        if a["id"] == app_id:
            a["status"] = "rejected"
            if a.get("user_id"):
                try:
                    await bot.send_message(a["user_id"], f"😔 Твоя заявка #{app_id} отклонена.")
                except Exception:
                    pass
            break
    save_data(data)
    await callback.message.edit_text(callback.message.text + "\n\n❌ <b>Отклонено</b>", parse_mode="HTML")
    await callback.answer("Отклонено")


# ─────────────────────────────────────────────
# Запуск
# ─────────────────────────────────────────────
async def main():
    print("🤖 Бот запущен. Ctrl+C для остановки.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
