import os
import asyncio
import aiohttp
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.constants import ChatAction

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]
FAL_KEY = os.environ["FAL_API_KEY"]

SYSTEM_PROMPT = """Ты — GLAM AI, профессиональный агент по созданию вирусного бьюти-контента для TikTok, Instagram Reels и YouTube Shorts.

Ты умеешь:
1. Генерировать сценарии/скрипты для видео с хуком, основной частью и CTA
2. Создавать идеи и тренды в бьюти-сфере
3. Писать хэштеги и описания под каждую платформу
4. Анализировать формулу успешных видео

Формат ответов:
- Используй эмодзи уместно
- Для сценариев: 🎬 ХУК → 📝 СЦЕНАРИЙ → #️⃣ ХЭШТЕГИ
- Указывай платформу и длину видео
- Отвечай на русском языке
- Сообщения для Telegram — без лишней разметки, читаемо"""

VIDEO_PROMPT_SYSTEM = """You are an expert at writing prompts for Kling AI video generation.
Given a beauty video description, create a detailed English prompt for Kling AI.
Include: camera style, lighting, movement, makeup/product details, atmosphere.
Return ONLY the prompt in English, no explanations, max 100 words."""

# Store conversation history per user
user_histories = {}

async def claude_chat(user_id: int, message: str) -> str:
    if user_id not in user_histories:
        user_histories[user_id] = []
    
    user_histories[user_id].append({"role": "user", "content": message})
    
    # Keep last 20 messages to avoid token limits
    history = user_histories[user_id][-20:]
    
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.anthropic.com/v1/messages",
            headers={"Content-Type": "application/json", "x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01"},
            json={"model": "claude-sonnet-4-20250514", "max_tokens": 1000, "system": SYSTEM_PROMPT, "messages": history}
        ) as resp:
            data = await resp.json()
            reply = data["content"][0]["text"]
            user_histories[user_id].append({"role": "assistant", "content": reply})
            return reply

async def generate_video_prompt(description: str) -> str:
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.anthropic.com/v1/messages",
            headers={"Content-Type": "application/json", "x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01"},
            json={"model": "claude-sonnet-4-20250514", "max_tokens": 200, "system": VIDEO_PROMPT_SYSTEM,
                  "messages": [{"role": "user", "content": description}]}
        ) as resp:
            data = await resp.json()
            return data["content"][0]["text"].strip()

async def generate_video(prompt: str, duration: str = "5", ratio: str = "9:16") -> str:
    async with aiohttp.ClientSession() as session:
        # Submit job
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

        # Poll for result
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

# ── HANDLERS ──

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("💡 Идея для тренда", callback_data="idea")],
        [InlineKeyboardButton("🎬 Сценарий TikTok", callback_data="script_tiktok"),
         InlineKeyboardButton("📱 Сценарий Reels", callback_data="script_reels")],
        [InlineKeyboardButton("🎥 Сгенерировать видео", callback_data="video_menu")],
        [InlineKeyboardButton("#️⃣ Хэштеги", callback_data="hashtags")],
    ]
    await update.message.reply_text(
        "💄 *GLAM AI* — твой агент по вирусному бьюти-контенту!\n\n"
        "Я умею:\n"
        "• Писать сценарии для TikTok, Reels, Shorts\n"
        "• Генерировать идеи и тренды\n"
        "• Подбирать хэштеги\n"
        "• 🎥 Создавать видео через Kling AI\n\n"
        "Выбери действие или просто напиши мне:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 *Команды:*\n\n"
        "/start — главное меню\n"
        "/video — генерация видео\n"
        "/idea — идея для тренда\n"
        "/hashtags — хэштеги\n"
        "/reset — сбросить историю чата\n\n"
        "Или просто пиши — я отвечу как бьюти-агент 💄",
        parse_mode="Markdown"
    )

async def reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_histories[user_id] = []
    await update.message.reply_text("✅ История чата сброшена. Начинаем заново!")

async def video_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("✨ Хайлайтер", callback_data="vid_highlighter"),
         InlineKeyboardButton("💄 Помада", callback_data="vid_lipstick")],
        [InlineKeyboardButton("🌸 Скинкер", callback_data="vid_skincare"),
         InlineKeyboardButton("👁️ Стрелки", callback_data="vid_eyeliner")],
        [InlineKeyboardButton("✍️ Свой промпт", callback_data="vid_custom")],
    ]
    await update.message.reply_text(
        "🎥 *Генерация видео через Kling AI*\n\nВыбери пресет или напиши своё описание:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    quick_actions = {
        "idea": "Придумай вирусную идею для бьюти-видео, которая сейчас в тренде",
        "script_tiktok": "Напиши сценарий для TikTok о макияже на каждый день, 30-60 секунд",
        "script_reels": "Напиши сценарий для Instagram Reels о вечернем макияже, 15-30 секунд",
        "hashtags": "Подбери хэштеги для бьюти-контента под TikTok, Instagram и YouTube одновременно",
    }

    video_presets = {
        "vid_highlighter": "Close-up beauty shot of a model applying shimmery highlighter on cheekbones, soft studio lighting, golden hour glow, slow motion, 4K cinematic, bokeh background, luxury aesthetic",
        "vid_lipstick": "Extreme close-up of glossy lips with red lipstick application, macro lens, soft pink background, gentle lighting, slow dramatic reveal, high-end beauty commercial style",
        "vid_skincare": "Elegant skincare routine, woman applying serum with glowing dewy skin, clean minimalist bathroom, morning light, slow motion drops, luxury spa aesthetic, 4K cinematic",
        "vid_eyeliner": "Close-up eye makeup application, precise eyeliner stroke, dramatic lashes, soft warm lighting, shallow depth of field, beauty tutorial style, high definition",
    }

    if data in quick_actions:
        await query.message.reply_text("⏳ Генерирую...")
        await query.message.chat.send_action(ChatAction.TYPING)
        try:
            reply = await claude_chat(user_id, quick_actions[data])
            await query.message.reply_text(reply)
        except Exception as e:
            await query.message.reply_text(f"❌ Ошибка: {e}")

    elif data == "video_menu":
        keyboard = [
            [InlineKeyboardButton("✨ Хайлайтер", callback_data="vid_highlighter"),
             InlineKeyboardButton("💄 Помада", callback_data="vid_lipstick")],
            [InlineKeyboardButton("🌸 Скинкер", callback_data="vid_skincare"),
             InlineKeyboardButton("👁️ Стрелки", callback_data="vid_eyeliner")],
            [InlineKeyboardButton("✍️ Свой промпт", callback_data="vid_custom")],
        ]
        await query.message.reply_text(
            "🎥 *Генерация видео*\n\nВыбери пресет или напиши своё описание:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data in video_presets:
        await query.message.reply_text("🎬 Запускаю генерацию видео... Это займёт 1-3 минуты ⏳")
        try:
            url = await generate_video(video_presets[data])
            await query.message.reply_video(url, caption="✨ Видео готово! Скачай и публикуй 🚀")
        except Exception as e:
            await query.message.reply_text(f"❌ Ошибка генерации: {e}")

    elif data == "vid_custom":
        ctx.user_data["awaiting_video_desc"] = True
        await query.message.reply_text(
            "✍️ Опиши видео, которое хочешь создать:\n\n"
            "_Например: девушка наносит хайлайтер, роскошная атмосфера, мягкий свет..._",
            parse_mode="Markdown"
        )

async def message_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    # Check if waiting for video description
    if ctx.user_data.get("awaiting_video_desc"):
        ctx.user_data["awaiting_video_desc"] = False
        await update.message.reply_text("🪄 Создаю промпт для Kling AI...")
        await update.message.chat.send_action(ChatAction.TYPING)
        try:
            prompt = await generate_video_prompt(text)
            await update.message.reply_text(f"📝 *Промпт:*\n`{prompt}`\n\n🎬 Запускаю генерацию видео... 1-3 мин ⏳", parse_mode="Markdown")
            url = await generate_video(prompt)
            await update.message.reply_video(url, caption="✨ Видео готово! 🚀")
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {e}")
        return

    # Regular chat
    await update.message.chat.send_action(ChatAction.TYPING)
    try:
        reply = await claude_chat(user_id, text)
        # Split long messages
        if len(reply) > 4096:
            for i in range(0, len(reply), 4096):
                await update.message.reply_text(reply[i:i+4096])
        else:
            await update.message.reply_text(reply)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("video", video_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    logger.info("GLAM AI Bot started!")
    app.run_polling()

if __name__ == "__main__":
    main()
