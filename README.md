# 🤝 Взаимопомощь

Telegram Mini App для анонимных сборов на закрытие долгов.

## Как работает

1. Пользователь открывает Mini App кнопкой в боте
2. Заполняет заявку: ссылка на цель, история, сумма
3. Заявка улетает в бот админу на модерацию
4. После одобрения сбор появляется в ленте

## Файлы

- index.html — Mini App (GitHub Pages / static)
- bot.py — Telegram-бот + API (aiogram 3 + aiohttp)
- requirements.txt — зависимости Python
- .env.example — шаблон секретов

## Переменные окружения

Пример файла `.env` (на основе `.env.example`):

```
BOT_TOKEN=токен_от_BotFather
ADMIN_CHAT_ID=ваш_telegram_id
MINIAPP_URL=https://yourdomain.example/fss/index.html
DB_PATH=fss.db
PORT=8080
```

- BOT_TOKEN — токен бота (обязателен для работы Telegram‑функций)
- ADMIN_CHAT_ID — Telegram ID администратора (для уведомлений и модерации)
- MINIAPP_URL — URL Mini App (Telegram Web App). Для локальной разработки используйте `http://localhost:8000/index.html?api=http://localhost:8080` (см. раздел ниже)
- DB_PATH — путь к SQLite базе (по умолчанию fss.db)
- PORT — порт API (по умолчанию 8080)

## Как запустить локально

1. Клонируйте репозиторий

```
git clone https://github.com/dimazmlbig-dev/fss.git
cd fss
```

2. Создайте виртуальное окружение и установите зависимости

```bash
python -m venv venv
# macOS / Linux
source venv/bin/activate
# Windows
# venv\Scripts\activate
pip install -r requirements.txt
```

3. Создайте файл `.env` по шаблону `.env.example` и заполните значения.

4. Запустите API + бот:

```bash
python bot.py
```

5. Раздайте статику (для локальной отладки Mini App)

```bash
# в папке с index.html
python -m http.server 8000
# и откройте в браузере
# http://localhost:8000/index.html?api=http://localhost:8080&admin=YOUR_ADMIN_ID
```

> Примечание: для полноценного теста как Telegram Web App нужно развернуть index.html на HTTPS и указать MINIAPP_URL в настройках бота через @BotFather.

## Конфигурация Mini App (index.html)

Клиент теперь поддерживает несколько способов задать базовый URL API и локальный ADMIN_ID (используется только для UX):

- query params: `?api=https://api.example.com&admin=1720219688`
- meta tag в head: `<meta name="api-base" content="https://api.example.com">`
- fallback: встроенный production URL

Сервер всегда проверяет подпись Telegram — не полагайтесь на клиентскую проверку для безопасности.

## CI / линтинг

Добавлен простой GitHub Actions workflow `.github/workflows/lint.yml`, который проверяет синтаксис Python и запускает ruff (устанавливается в workflow). Это базовая точка для автоматического линтинга.

## Рекомендации и дальнейшие улучшения

- Для продакшна: рассмотреть замену SQLite на Postgres, если ожидается одновременных подключений и рост данных.
- Добавить тесты (pytest) и расширенный CI (security scans, mypy, unit tests).
- Убедиться, что BOT_TOKEN задан в окружении CI/деплоя и MINIAPP_URL доступен по HTTPS для Telegram Web App.

---

Если нужно — могу добавить инструкции по деплою на Railway / Heroku / Vercel и пример systemd unit для запуска сервера.
