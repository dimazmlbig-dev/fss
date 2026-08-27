# 🤝 Взаимопомощь

Telegram Mini App для анонимных сборов на закрытие долгов.

## Как работает

1. Пользователь открывает Mini App кнопкой в боте
2. Заполняет заявку: ссылка на цель, история, сумма
3. Заявка улетает в бот админу на модерацию
4. После одобрения сбор появляется в ленте

## Файлы

- index.html — Mini App (GitHub Pages)
- bot.py — Telegram-бот (aiogram 3)
- requirements.txt — зависимости Python
- .env.example — шаблон секретов

## Запуск бота

    python -m venv venv
    venv\Scripts\activate        (Windows)
    pip install -r requirements.txt
    copy .env.example .env       (заполнить значения)
    python bot.py
