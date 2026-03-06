# 💄 GLAM AI — Beauty Telegram Bot

Telegram-бот для создания вирусного бьюти-контента и генерации видео через Kling AI.

## Что умеет бот
- 💬 Чат с AI-агентом по бьюти-контенту
- 🎬 Генерация сценариев для TikTok / Reels / Shorts
- 💡 Идеи и тренды
- #️⃣ Хэштеги под все платформы
- 🎥 Генерация видео через Kling AI (fal.ai)

---

## Деплой на Railway.app

### Шаг 1 — Создай Telegram бота
1. Открой Telegram, найди @BotFather
2. Напиши `/newbot`
3. Придумай имя и username (например `GlamAIBot`)
4. Скопируй токен вида `7123456789:AAF...`

### Шаг 2 — Залей код на GitHub
1. Создай новый репозиторий на github.com
2. Загрузи файлы: `bot.py`, `requirements.txt`, `Procfile`

### Шаг 3 — Задеплой на Railway
1. Зайди на railway.app → New Project → Deploy from GitHub
2. Выбери свой репозиторий
3. Перейди в **Variables** и добавь три переменные:

```
TELEGRAM_TOKEN = твой_токен_от_BotFather
ANTHROPIC_API_KEY = твой_ключ_от_anthropic.com
FAL_API_KEY = твой_ключ_от_fal.ai
```

4. Railway автоматически запустит бота!

### Шаг 4 — Проверь
Открой бота в Telegram и напиши `/start`

---

## Переменные окружения

| Переменная | Где получить |
|---|---|
| `TELEGRAM_TOKEN` | @BotFather в Telegram |
| `ANTHROPIC_API_KEY` | console.anthropic.com → API Keys |
| `FAL_API_KEY` | fal.ai → Settings → API Keys |

---

## Команды бота

| Команда | Действие |
|---|---|
| `/start` | Главное меню |
| `/video` | Генерация видео |
| `/idea` | Идея для тренда |
| `/hashtags` | Подбор хэштегов |
| `/reset` | Сбросить историю чата |
| `/help` | Помощь |
