import os
import re
import asyncio
import aiohttp
import logging
import tempfile
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.constants import ChatAction

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]
FAL_KEY = os.environ["FAL_API_KEY"]

SYSTEM_PROMPT = """Ты — GLAM AI, живой харизматичный бьюти-агент. Общаешься как умная подруга.

КРИТИЧЕСКИ ВАЖНО — ФОРМАТИРОВАНИЕ:
- НИКОГДА не используй звёздочки ** для жирного текста
- НИКОГДА не используй # для заголовков
- НИКОГДА не используй *курсив*
- Пиши ТОЛЬКО обычным текстом + эмодзи + переносы строк
- Telegram не рендерит markdown в обычных сообщениях

СТИЛЬ:
- Живо, по-человечески, с теплотой
- Можешь шутить и удивляться
- Короткие абзацы, много воздуха

САМОЕ ВАЖНОЕ — УТОЧНЯЮЩИЕ ВОПРОСЫ:
Когда человек просит создать ЛЮБОЙ контент (сценарий, идею, пост, хэштеги, видео) — 
СНАЧАЛА задай ровно 3 вопроса, все в одном сообщении:

1️⃣ Для кого этот контент? Опиши свою аудиторию (возраст, пол, интересы)
2️⃣ Какая цель? (продажи, охваты, подписчики, узнаваемость)
3️⃣ Какой тон? (экспертный, дружеский, с юмором, вдохновляющий)

Только после получения ответов — создавай контент.

ФОРМАТ СЦЕНАРИЯ (без звёздочек и решёток):
🎬 ХУК (первые 3 секунды):
[текст]

📝 СЦЕНАРИЙ:
Секунда 1-5: [описание]
Секунда 6-15: [описание]
и т.д.

#️⃣ ХЭШТЕГИ:
[список через пробел]

💡 ФИШКА:
[совет]

Отвечай на русском языке."""

VIDEO_CLARIFY_QUESTIONS = (
    "Отлично, давай сделаем крутое видео! 🎬\n\n"
    "Чтобы результат был именно таким, как ты хочешь — ответь на 3 вопроса:\n\n"
    "1️⃣ Что должно быть в кадре? (продукт, процедура, модель — что именно показываем?)\n\n"
    "2️⃣ Какое настроение или атмосфера? (роскошь, натуральность, энергия, нежность...)\n\n"
    "3️⃣ Для какой платформы? (TikTok/Reels — вертикаль, YouTube — горизонталь)\n\n"
    "Можешь ответить на все три сразу одним сообщением 👇"
)

VIDEO_PROMPT_SYSTEM = """You are an expert at writing prompts for Kling AI video generation.
Given a beauty video description, create a detailed English prompt.
Include: camera style, lighting, movement, makeup/product details, atmosphere, mood.
Return ONLY the prompt in English, no explanations, max 120 words."""

user_histories = {}
user_states = {}


def strip_markdown(text: str) -> str):
    """Remove markdown formatting that Telegram shows as raw symbols"""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'#{1,6}\s+', '', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    text = re.sub(r'_(.+?)_', r'\1', text)
    return text


async def claude_request(messages: list, system: str, max_tokens: int = 1200) -> str:
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "Content-Type": "application/json",
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01"
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": max_tokens,
                "system": system,
                "messages": messages
            }
        ) as resp:
            data = await resp.json()
            return data["content"][0]["text"]


async def claude_chat(user_id: int, message: str) -> str:
    if user_id not in user_histories:
        user_histories[user_id] = []
    user_histories[user_id].append({"role": "user", "content": message})
    history = user_histories[user_id][-20:]
    reply = await claude_request(history, SYSTEM_PROMPT)
    reply = strip_markdown(reply)
    user_histories[user_id].append({"role": "assistant", "content": reply})
    return reply


async def transcribe_voice(file_path: str) -> str:
    async with aiohttp.ClientSession() as session:
        with open(file_path, "rb") as f:
            form = aiohttp.FormData()
            form.add_field("file", f, filename="voice.ogg", content_type="audio/ogg")
            async with session.post(
                "https://fal.run/fal-ai/whisper",
                headers={"Authorization": f"Key {FAL_KEY}"},
                data=form
            ) as resp:
                if resp.ok:
                    result = await resp.json()
                    return result.get("text", "")
    return ""


async def generate_video_prompt(description: str) -> str:
    return await claude_request(
        [{"role": "user", "content": description}],
        VIDEO_PROMPT_SYSTEM,
        max_tokens=200
    )


async def generate_video(prompt: str, duration: str = "5", ratio: str = "9:16") -> str:
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://queue.fal.run/fal-ai/kling-video/v1.6/standard/text-to-video",
            headers={"Content-Type": "application/json", "Authorization": f"Key {FAL_KEY}"},
            json={"prompt": prompt, "duration": duration, "aspect_ratio": ratio}
        ) as resp:
            if not resp.ok:
                err = await resp.json()
                raise Exception(err.get("detail", "Ошибка fal.ai"))
            data = await resp.json()
            request_id = data["request_id"]

        for _ in range(72):
            await asyncio.sleep(5)
            async with session.get(
                f"https://queue.fal.run/fal-ai/kling-video/v1.6/standard/text-to-video/requests/{request_id}",
                headers={"Authorization": f"Key {FAL_KEY}"}
            ) as poll:
                result = await poll.json()
                if result.get("status") == "COMPLETED" and result.get("video", {}).get("url"):
                    return result["video"]["url"]
                if result.get("status") == "FAILED":
                    raise Exception("Генерация не удалась")
        raise Exception("Время ожидания истекло")


async def send_long(update: Update, text: str):
    text = strip_markdown(text)
    msg = update.effective_message
    if len(text) > 4096:
        for i in range(0, len(text), 4096):
            await msg.reply_text(text[i:i+4096])
    else:
        await msg.reply_text(text)


# ── COMMANDS ──

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_histories[user_id] = []
    user_states[user_id] = {}
    name = update.effective_user.first_name or "красотка"

    keyboard = [
        [InlineKeyboardButton("💡 Идея для тренда", callback_data="idea")],
        [InlineKeyboardButton("🎬 Сценарий TikTok", callback_data="script_tiktok"),
         InlineKeyboardButton("📱 Сценарий Reels", callback_data="script_reels")],
        [InlineKeyboardButton("🎥 Сгенерировать видео", callback_data="video_menu")],
        [InlineKeyboardButton("#️⃣ Хэштеги", callback_data="hashtags")],
    ]
    await update.message.reply_text(
        f"Привет, {name}! 💄\n\n"
        f"Я GLAM AI — твой личный агент по бьюти-контенту. "
        f"Помогу придумать идеи, написать сценарии и создать видео через ИИ.\n\n"
        f"Можешь написать текстом, отправить голосовое 🎙 или выбрать из меню:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def reset_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_histories[user_id] = []
    user_states[user_id] = {}
    await update.message.reply_text("Начинаем с чистого листа! С чего начнём? 😊")


async def video_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_states:
        user_states[user_id] = {}
    user_states[user_id]["mode"] = "video_clarify"
    user_states[user_id]["video_answers"] = []
    await update.message.reply_text(VIDEO_CLARIFY_QUESTIONS)


# ── BUTTONS ──

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if user_id not in user_states:
        user_states[user_id] = {}
    if user_id not in user_histories:
        user_histories[user_id] = []

    data = query.data

    # Quick content requests — ask clarifying questions first
    content_requests = {
        "idea": "Хочу вирусную идею для бьюти-видео",
        "script_tiktok": "Напиши сценарий для TikTok",
        "script_reels": "Напиши сценарий для Instagram Reels",
        "hashtags": "Подбери хэштеги для бьюти-контента",
    }

    video_presets = {
        "vid_highlighter": "Close-up beauty shot of a model applying shimmery highlighter on cheekbones, soft studio lighting, golden hour glow, slow motion, 4K cinematic, bokeh background, luxury aesthetic, warm tones",
        "vid_lipstick": "Extreme close-up of glossy lips with red lipstick application, macro lens, soft pink background, gentle lighting, slow dramatic reveal, high-end beauty commercial style",
        "vid_skincare": "Elegant skincare routine, woman applying serum with glowing dewy skin, clean minimalist bathroom, morning light, slow motion drops, luxury spa aesthetic, 4K cinematic",
        "vid_eyeliner": "Close-up eye makeup application, precise eyeliner stroke, dramatic lashes, soft warm studio lighting, shallow depth of field, beauty tutorial, high definition",
    }

    if data in content_requests:
        # Always ask 3 questions before generating content
        user_states[user_id]["mode"] = "content_clarify"
        user_states[user_id]["content_request"] = content_requests[data]
        user_states[user_id]["clarify_answers"] = []
        await query.message.reply_text(
            "Прежде чем начну — хочу сделать это максимально точно под тебя 🎯\n\n"
            "Ответь на 3 вопроса:\n\n"
            "1️⃣ Для кого этот контент? Опиши аудиторию (возраст, пол, интересы)\n\n"
            "2️⃣ Какая цель? (продажи, охваты, подписчики, узнаваемость бренда)\n\n"
            "3️⃣ Какой тон? (экспертный, дружеский, с юмором, вдохновляющий)\n\n"
            "Можешь ответить на все три сразу 👇"
        )

    elif data == "video_menu":
        keyboard = [
            [InlineKeyboardButton("✨ Хайлайтер", callback_data="vid_highlighter"),
             InlineKeyboardButton("💄 Помада", callback_data="vid_lipstick")],
            [InlineKeyboardButton("🌸 Скинкер", callback_data="vid_skincare"),
             InlineKeyboardButton("👁️ Стрелки", callback_data="vid_eyeliner")],
            [InlineKeyboardButton("✍️ Своя идея", callback_data="vid_custom")],
        ]
        await query.message.reply_text(
            "Давай создадим видео! 🎥\n\nВыбери пресет или расскажи свою идею:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data in video_presets:
        await query.message.reply_text("Запускаю генерацию — займёт 1-3 минуты ⏳✨")
        try:
            url = await generate_video(video_presets[data])
            await query.message.reply_video(url, caption="Готово! Скачивай и публикуй 🚀")
        except Exception as e:
            await query.message.reply_text(f"Упс, не получилось: {e}\n\nПопробуй чуть позже.")

    elif data == "vid_custom":
        user_states[user_id]["mode"] = "video_clarify"
        user_states[user_id]["video_answers"] = []
        await query.message.reply_text(VIDEO_CLARIFY_QUESTIONS)


# ── VOICE ──

async def handle_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text("Слушаю... 🎙")
    await update.message.chat.send_action(ChatAction.TYPING)

    try:
        tg_file = await ctx.bot.get_file(update.message.voice.file_id)
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            await tg_file.download_to_drive(tmp.name)
            tmp_path = tmp.name

        text = await transcribe_voice(tmp_path)

        if not text:
            await update.message.reply_text("Не удалось распознать 😕 Попробуй написать текстом.")
            return

        await update.message.reply_text(f"Услышала: «{text}»\n\nОтвечаю... 💭")
        await process_message(update, ctx, user_id, text)

    except Exception as e:
        logger.error(f"Voice error: {e}")
        await update.message.reply_text("Не получилось обработать голосовое 😕 Напиши текстом!")


# ── MAIN PROCESSOR ──

async def process_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE, user_id: int, text: str):
    if user_id not in user_states:
        user_states[user_id] = {}
    if user_id not in user_histories:
        user_histories[user_id] = []

    state = user_states[user_id]
    mode = state.get("mode")

    # ── Video clarification flow ──
    if mode == "video_clarify":
        answers = state.get("video_answers", [])
        answers.append(text)
        state["video_answers"] = answers

        if len(answers) >= 3:
            state["mode"] = None
            state["video_answers"] = []
            combined = " / ".join(answers)
            await update.effective_message.reply_text(
                "Отлично, всё понятно! Создаю промпт и запускаю генерацию...\n"
                "Это займёт 1-3 минуты ⏳🎬"
            )
            try:
                prompt = await generate_video_prompt(combined)
                ratio = "16:9" if any(w in text.lower() for w in ["youtube", "горизонт", "широк"]) else "9:16"
                url = await generate_video(prompt, ratio=ratio)
                await update.effective_message.reply_video(url, caption="Вот твоё видео! 🎬✨")
            except Exception as e:
                await update.effective_message.reply_text(f"Упс, ошибка генерации: {e}\n\nПопробуй ещё раз.")
        else:
            remaining = 3 - len(answers)
            await update.effective_message.reply_text(
                f"Записала! Осталось ответить на {remaining} {'вопрос' if remaining == 1 else 'вопроса'} выше 👆"
            )
        return

    # ── Content clarification flow ──
    if mode == "content_clarify":
        answers = state.get("clarify_answers", [])
        answers.append(text)
        state["clarify_answers"] = answers

        if len(answers) >= 1:
            # Got answers, now generate content with context
            state["mode"] = None
            original = state.get("content_request", "создай контент")
            context = f"{original}. Детали об аудитории и целях: {' / '.join(answers)}"
            await update.effective_message.chat.send_action(ChatAction.TYPING)
            try:
                reply = await claude_chat(user_id, context)
                await send_long(update, reply)
            except Exception as e:
                await update.effective_message.reply_text(f"Что-то пошло не так: {e}")
        return

    # ── Regular chat ──
    await update.effective_message.chat.send_action(ChatAction.TYPING)
    try:
        reply = await claude_chat(user_id, text)
        await send_long(update, reply)
    except Exception as e:
        logger.error(f"Chat error: {e}")
        await update.effective_message.reply_text("Что-то пошло не так, попробуй ещё раз 🙏")


async def text_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await process_message(update, ctx, update.effective_user.id, update.message.text)


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset_cmd))
    app.add_handler(CommandHandler("video", video_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    logger.info("GLAM AI Bot started!")
    app.run_polling()


if __name__ == "__main__":
    main()
