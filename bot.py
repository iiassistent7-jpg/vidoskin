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

# ── PROMPTS ──

SYSTEM_PROMPT = """Ты — GLAM AI, профессиональный бьюти-продюсер и сценарист. Общаешься по-человечески, тепло и по делу.

ФОРМАТИРОВАНИЕ — СТРОГО:
- НИКАКИХ звёздочек ** и * для выделения
- НИКАКИХ решёток # для заголовков
- Только обычный текст + эмодзи + переносы строк

СТИЛЬ: живо, как профессионал который реально хочет помочь сделать крутой контент.

ТЫ УМЕЕШЬ ГЕНЕРИРОВАТЬ ВИДЕО через Kling AI. Когда просят сделать видео — никогда не отказывай и не говори что не можешь. Скажи что запускаешь процесс.

Когда просят создать контент или видео — сначала задай 3 вопроса об аудитории, цели и тоне. Не создавай контент без ответов.
Отвечай на русском."""

SCENARIO_SYSTEM = """Ты — профессиональный сценарист бьюти-видео для TikTok/Reels/Shorts.

Создай сценарий разбитый на СЦЕНЫ. Каждая сцена — это отдельный клип 3-10 секунд.

ВАЖНО:
- Текст на экране и озвучку выноси ОТДЕЛЬНО от описания сцены
- В описании сцены — только то, что происходит визуально в кадре
- Никаких звёздочек ** и решёток #

ФОРМАТ КАЖДОЙ СЦЕНЫ:
СЦЕНА [N] ([длительность] сек)
Кадр: [что снимать, как двигается камера, крупность плана]
Атмосфера: [освещение, настроение, цвета]
---
ТЕКСТ НА ЭКРАНЕ: [что написать поверх видео]
ОЗВУЧКА: [что говорить за кадром или в кадре]
---

После всех сцен напиши:
ХЭШТЕГИ: [список]
МУЗЫКА: [описание трека — темп, настроение, жанр]

Отвечай на русском. Без markdown-разметки."""

DESIGN_SYSTEM = """Ты — арт-директор бьюти-контента.

По описанию видео создай документ визуального дизайна:

ВИЗУАЛЬНЫЙ СТИЛЬ:
[общая эстетика — цветовая палитра, настроение, референсы]

ОСВЕЩЕНИЕ:
[тип света, температура, источники]

ЦВЕТОКОРРЕКЦИЯ:
[тон, насыщенность, характер обработки]

ШРИФТ И ТИТРЫ:
[стиль текста на экране, цвет, размер, анимация]

ПЕРЕХОДЫ:
[как переходить между сценами]

Без звёздочек и решёток. На русском."""

VIDEO_PROMPT_SYSTEM = """You are an expert at writing Kling AI video generation prompts.

Given a scene description, create a precise English prompt for VISUAL ACTION ONLY.
STRICT RULES:
- NO text, words, letters, titles, subtitles, captions, or overlays of any kind
- NO signs, labels, or readable elements in the scene
- ONLY pure visual: camera movement, lighting, people, objects, colors, atmosphere
Include: exact camera angle, movement, lighting, subject details, color mood.
Cinematic and specific. Max 80 words. Return ONLY the prompt, nothing else."""


def strip_md(text: str) -> str:
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'\*(.+?)\*', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'#{1,6}\s*', '', text)
    text = re.sub(r'__(.+?)__', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'_(.+?)_', r'\1', text, flags=re.DOTALL)
    return text.strip()


async def claude_call(messages: list, system: str, max_tokens: int = 1500) -> str:
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
            return strip_md(data["content"][0]["text"])


user_histories = {}   # chat history per user
user_states = {}      # production state per user


def get_state(user_id: int) -> dict:
    if user_id not in user_states:
        user_states[user_id] = {}
    return user_states[user_id]


def get_history(user_id: int) -> list:
    if user_id not in user_histories:
        user_histories[user_id] = []
    return user_histories[user_id]


async def chat(user_id: int, message: str) -> str:
    history = get_history(user_id)
    history.append({"role": "user", "content": message})
    reply = await claude_call(history[-20:], SYSTEM_PROMPT)
    history.append({"role": "assistant", "content": reply})
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


async def generate_video(prompt: str, ratio: str = "9:16") -> str:
    MODEL = "fal-ai/kling-video/v1.6/standard/text-to-video"
    headers = {"Content-Type": "application/json", "Authorization": f"Key {FAL_KEY}"}

    async with aiohttp.ClientSession() as session:
        # Submit to queue
        async with session.post(
            f"https://queue.fal.run/{MODEL}",
            headers=headers,
            json={"prompt": prompt, "duration": "5", "aspect_ratio": ratio}
        ) as resp:
            if not resp.ok:
                body = await resp.text()
                raise Exception(f"Submit failed {resp.status}: {body[:200]}")
            data = await resp.json(content_type=None)
            request_id = data.get("request_id")
            response_url = data.get("response_url", "")
            status_url = data.get("status_url", f"https://queue.fal.run/{MODEL}/requests/{request_id}/status")
            logger.info(f"Submitted video job {request_id}")

        # Poll status
        for attempt in range(80):
            await asyncio.sleep(5)
            try:
                async with session.get(status_url, headers={"Authorization": f"Key {FAL_KEY}"}) as poll:
                    poll_text = await poll.text()
                    try:
                        result = json.loads(poll_text)
                    except Exception:
                        logger.warning(f"Non-JSON poll #{attempt}: {poll_text[:100]}")
                        continue

                    status = result.get("status", "")
                    logger.info(f"Video status #{attempt}: {status}")

                    if status == "COMPLETED":
                        # Get result via response_url or request_id
                        result_url = response_url or f"https://queue.fal.run/{MODEL}/requests/{request_id}"
                        async with session.get(result_url, headers={"Authorization": f"Key {FAL_KEY}"}) as res:
                            final_text = await res.text()
                            final = json.loads(final_text)
                            # Try different response shapes
                            video_url = (
                                final.get("video", {}).get("url") or
                                (final.get("videos") or [{}])[0].get("url") or
                                final.get("url", "")
                            )
                            if video_url:
                                return video_url
                            raise Exception(f"No video URL in response: {final_text[:300]}")

                    if status in ("FAILED", "ERROR"):
                        raise Exception(f"Generation failed: {result.get('error', 'unknown')}")

            except Exception as e:
                if "failed" in str(e).lower() or "No video" in str(e):
                    raise
                logger.warning(f"Poll error #{attempt}: {e}")
                continue

    raise Exception("Время ожидания истекло (6 минут)")


async def send(update: Update, text: str, reply_markup=None):
    text = strip_md(text)
    msg = update.effective_message
    if len(text) > 4096:
        parts = [text[i:i+4096] for i in range(0, len(text), 4096)]
        for i, part in enumerate(parts):
            if i == len(parts) - 1 and reply_markup:
                await msg.reply_text(part, reply_markup=reply_markup)
            else:
                await msg.reply_text(part)
    else:
        await msg.reply_text(text, reply_markup=reply_markup)


# ── PRODUCTION FLOW ──

async def start_production(update: Update, brief: str, user_id: int):
    """Step 1: Generate scenario broken into scenes"""
    state = get_state(user_id)
    state["brief"] = brief
    state["mode"] = "awaiting_scenario_approval"

    await update.effective_message.reply_text("Генерирую сценарий по сценам... ✍️")
    await update.effective_message.chat.send_action(ChatAction.TYPING)

    scenario = await claude_call(
        [{"role": "user", "content": f"Создай сценарий для видео: {brief}"}],
        SCENARIO_SYSTEM,
        max_tokens=2000
    )

    state["scenario"] = scenario

    keyboard = [
        [InlineKeyboardButton("✅ Одобряю, к дизайну!", callback_data="approve_scenario")],
        [InlineKeyboardButton("✏️ Внести правки", callback_data="edit_scenario")],
    ]
    await send(update,
        f"Вот сценарий по сценам 🎬\n\n{scenario}\n\n"
        f"Как тебе? Можем сразу перейти к визуальному дизайну или внесёшь правки?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_design(obj, user_id: int):
    """Step 2: Generate visual design document"""
    state = get_state(user_id)
    state["mode"] = "awaiting_design_approval"

    # obj can be Update or CallbackQuery
    msg = obj.message if hasattr(obj, 'message') and not hasattr(obj, 'effective_message') else obj.effective_message

    await msg.reply_text("Разрабатываю визуальный стиль... 🎨")
    await msg.chat.send_action(ChatAction.TYPING)

    design = await claude_call(
        [{"role": "user", "content": f"Создай визуальный дизайн для видео: {state['brief']}\n\nСценарий: {state['scenario']}"}],
        DESIGN_SYSTEM,
        max_tokens=1000
    )

    state["design"] = design

    keyboard = [
        [InlineKeyboardButton("✅ Отлично, начинаем съёмку!", callback_data="approve_design")],
        [InlineKeyboardButton("✏️ Изменить стиль", callback_data="edit_design")],
    ]
    await msg.reply_text(
        strip_md(f"Визуальный стиль 🎨\n\n{design}\n\nВсё ок? Или хочешь что-то изменить?"),
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_scene(obj, user_id: int):
    """Step 3+: Show current scene and offer to generate video"""
    state = get_state(user_id)
    scenes = state.get("scenes", [])
    current = state.get("current_scene", 0)

    if current >= len(scenes):
        await wrap_up(obj, user_id)
        return

    scene = scenes[current]
    state["mode"] = "awaiting_scene_approval"

    msg = obj.message if hasattr(obj, 'message') and not hasattr(obj, 'effective_message') else obj.effective_message

    keyboard = [
        [InlineKeyboardButton("🎥 Генерировать эту сцену", callback_data="gen_scene")],
        [InlineKeyboardButton("✏️ Изменить сцену", callback_data="edit_scene")],
        [InlineKeyboardButton("⏭ Пропустить сцену", callback_data="skip_scene")],
    ]

    await msg.reply_text(
        strip_md(
            f"Сцена {current + 1} из {len(scenes)} 🎬\n\n"
            f"{scene['description']}\n\n"
            f"———\n"
            f"📝 ТЕКСТ НА ЭКРАНЕ:\n{scene.get('text_overlay', '—')}\n\n"
            f"🎙 ОЗВУЧКА:\n{scene.get('voiceover', '—')}\n\n"
            f"Генерируем эту сцену или сначала внесёшь правки?"
        ),
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def parse_scenes(scenario: str) -> list:
    """Parse scenario text into list of scene dicts"""
    parse_prompt = f"""Из этого сценария извлеки каждую сцену и верни в формате JSON.
Для каждой сцены:
- "description": только визуальное описание (что в кадре, движение камеры, свет) — БЕЗ текста и озвучки
- "text_overlay": текст который должен появиться на экране (или "нет")
- "voiceover": что говорится за кадром или в кадре (или "нет")
- "duration": длительность в секундах (число)

Верни ТОЛЬКО валидный JSON массив, без пояснений:
[{{"description":"...","text_overlay":"...","voiceover":"...","duration":5}}, ...]

Сценарий:
{scenario}"""

    result = await claude_call(
        [{"role": "user", "content": parse_prompt}],
        "Ты парсишь сценарий в JSON. Возвращай ТОЛЬКО валидный JSON без markdown, без пояснений.",
        max_tokens=2000
    )

    # Clean and parse JSON
    result = result.strip()
    result = re.sub(r'^```json\s*', '', result)
    result = re.sub(r'^```\s*', '', result)
    result = re.sub(r'\s*```$', '', result)

    import json
    try:
        return json.loads(result)
    except Exception as e:
        logger.error(f"JSON parse error: {e}\nRaw: {result}")
        return []


async def generate_video_with_updates(msg, prompt: str, ratio: str = "9:16") -> str:
    """Generate video and send periodic status updates to keep user informed"""
    MODEL = "fal-ai/kling-video/v1.6/standard/text-to-video"
    headers = {"Content-Type": "application/json", "Authorization": f"Key {FAL_KEY}"}

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"https://queue.fal.run/{MODEL}",
            headers=headers,
            json={"prompt": prompt, "duration": "5", "aspect_ratio": ratio}
        ) as resp:
            if not resp.ok:
                body = await resp.text()
                raise Exception(f"Submit failed {resp.status}: {body[:200]}")
            data = await resp.json(content_type=None)
            request_id = data.get("request_id")
            response_url = data.get("response_url", "")
            status_url = data.get("status_url", f"https://queue.fal.run/{MODEL}/requests/{request_id}/status")
            logger.info(f"Video job submitted: {request_id}")

        updates_sent = 0
        for attempt in range(80):
            await asyncio.sleep(5)

            # Send keepalive every ~30 seconds
            if attempt > 0 and attempt % 6 == 0:
                minutes = (attempt * 5) // 60
                seconds = (attempt * 5) % 60
                updates_sent += 1
                try:
                    await msg.reply_text(f"Kling AI обрабатывает... {minutes}м {seconds}с ⏳")
                except Exception:
                    pass

            try:
                async with session.get(status_url, headers={"Authorization": f"Key {FAL_KEY}"}) as poll:
                    poll_text = await poll.text()
                    try:
                        result = json.loads(poll_text)
                    except Exception:
                        logger.warning(f"Non-JSON #{attempt}: {poll_text[:100]}")
                        continue

                    status = result.get("status", "")
                    logger.info(f"Status #{attempt}: {status}")

                    if status == "COMPLETED":
                        # Try response_url from status, then from initial submit, then construct
                        res_url = (
                            result.get("response_url") or
                            response_url or
                            f"https://queue.fal.run/{MODEL}/requests/{request_id}"
                        )
                        logger.info(f"Fetching result from: {res_url}")
                        async with session.get(res_url, headers={"Authorization": f"Key {FAL_KEY}"}) as res:
                            final_text = await res.text()
                            logger.info(f"Result response: {final_text[:300]}")
                            try:
                                final = json.loads(final_text)
                            except Exception:
                                raise Exception(f"Result not JSON: {final_text[:200]}")
                            video_url = (
                                final.get("video", {}).get("url") or
                                (final.get("videos") or [{}])[0].get("url") or
                                final.get("url", "")
                            )
                            if video_url:
                                logger.info(f"Got video URL: {video_url[:80]}")
                                return video_url
                            raise Exception(f"No video URL in: {str(final)[:300]}")

                    if status in ("FAILED", "ERROR"):
                        raise Exception(f"Kling AI: {result.get('error', 'generation failed')}")

            except Exception as e:
                if any(x in str(e) for x in ["failed", "No video", "FAILED", "Kling"]):
                    raise
                logger.warning(f"Poll error #{attempt}: {e}")
                continue

    raise Exception("Время ожидания истекло (6 минут)")


async def generate_scene_video(obj, user_id: int):
    """Generate video for current scene"""
    state = get_state(user_id)
    scenes = state.get("scenes", [])
    current = state.get("current_scene", 0)
    scene = scenes[current]
    design = state.get("design", "")

    msg = obj.message if hasattr(obj, 'message') and not hasattr(obj, 'effective_message') else obj.effective_message
    await msg.reply_text("Генерирую промпт для сцены... ✍️")

    # Build prompt from scene + design
    prompt_input = f"Scene: {scene['description']}\nVisual style: {design[:300]}"
    video_prompt = await claude_call(
        [{"role": "user", "content": prompt_input}],
        VIDEO_PROMPT_SYSTEM,
        max_tokens=150
    )
    logger.info(f"Video prompt: {video_prompt}")

    await msg.reply_text(f"Промпт готов. Отправляю в Kling AI... ⏳\n\n_{video_prompt[:100]}_")

    try:
        ratio = state.get("ratio", "9:16")
        # Run generation with periodic keepalive messages
        url = await generate_video_with_updates(msg, video_prompt, ratio)

        keyboard = [
            [InlineKeyboardButton("✅ Следующая сцена!", callback_data="next_scene")],
            [InlineKeyboardButton("🔄 Перегенерировать", callback_data="regen_scene")],
            [InlineKeyboardButton("✏️ Изменить и перегенерировать", callback_data="edit_regen_scene")],
        ]

        await msg.reply_video(
            url,
            caption=strip_md(
                f"Сцена {current + 1} готова! 🎬\n\n"
                f"📝 Текст на экране: {scene.get('text_overlay','—')}\n"
                f"🎙 Озвучка: {scene.get('voiceover','—')}"
            ),
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        state["last_video_prompt"] = video_prompt
    except Exception as e:
        logger.error(f"Video generation error: {e}")
        await msg.reply_text(f"Ошибка генерации: {e}\n\nПопробуй ещё раз.")


async def wrap_up(obj, user_id: int):
    """All scenes done"""
    state = get_state(user_id)
    scenario = state.get("scenario", "")

    # Extract hashtags and music from scenario
    hashtags = ""
    music = ""
    for line in scenario.split("\n"):
        if "ХЭШТЕГИ" in line.upper():
            hashtags = line.replace("ХЭШТЕГИ:", "").strip()
        if "МУЗЫКА" in line.upper():
            music = line.replace("МУЗЫКА:", "").strip()

    state["mode"] = None

    keyboard = [
        [InlineKeyboardButton("🎬 Создать новое видео", callback_data="new_video")],
        [InlineKeyboardButton("💡 Придумать идею", callback_data="idea")],
    ]

    msg = obj.message if hasattr(obj, 'message') and not hasattr(obj, 'effective_message') else obj.effective_message
    await msg.reply_text(
        strip_md(
            "Все сцены готовы! 🎉\n\n"
            "Теперь у тебя есть все клипы для монтажа.\n\n"
            f"#️⃣ Хэштеги: {hashtags}\n\n"
            f"🎵 Музыка: {music}\n\n"
            "Удачного монтажа! Если нужно переснять какую-то сцену — просто скажи 💪"
        ),
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ── COMMAND HANDLERS ──

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_histories[user_id] = []
    user_states[user_id] = {}
    name = update.effective_user.first_name or "красотка"

    keyboard = [
        [InlineKeyboardButton("🎬 Создать видео", callback_data="new_video")],
        [InlineKeyboardButton("💡 Идея для тренда", callback_data="idea")],
        [InlineKeyboardButton("#️⃣ Хэштеги", callback_data="hashtags")],
    ]
    await update.message.reply_text(
        f"Привет, {name}! 💄\n\n"
        f"Я GLAM AI — твой бьюти-продюсер.\n\n"
        f"Работаем так:\n"
        f"1️⃣ Ты описываешь идею\n"
        f"2️⃣ Я пишу сценарий по сценам\n"
        f"3️⃣ Согласовываем визуальный стиль\n"
        f"4️⃣ Генерируем каждую сцену отдельно\n"
        f"5️⃣ Текст и озвучку получаешь отдельно\n"
        f"6️⃣ Монтируешь сам!\n\n"
        f"Что делаем?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def reset_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_histories[user_id] = []
    user_states[user_id] = {}
    await update.message.reply_text("Начинаем с чистого листа! 😊")


# ── BUTTON HANDLER ──

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    state = get_state(user_id)
    data = query.data

    if data == "new_video":
        state["mode"] = "ask_topic"
        await query.message.reply_text(
            "Отлично, делаем видео! 🎬\n\n"
            "О чём будет видео? Опиши тему, продукт или услугу 👇"
        )

    elif data == "idea":
        state["mode"] = "clarify_idea"
        state["clarify_answers"] = []
        await query.message.reply_text(
            "Придумаем крутую идею! 💡\n\n"
            "3 быстрых вопроса:\n\n"
            "1️⃣ Какая тема или продукт?\n\n"
            "2️⃣ Для какой платформы? (TikTok, Reels, Shorts)\n\n"
            "3️⃣ Кто твоя аудитория?\n\n"
            "Пиши всё одним сообщением 👇"
        )

    elif data == "hashtags":
        state["mode"] = "clarify_hashtags"
        await query.message.reply_text(
            "Подберём хэштеги! #️⃣\n\n"
            "Опиши тему видео и платформу (TikTok/Instagram/YouTube) 👇"
        )

    elif data == "approve_scenario":
        await show_design(query, user_id)

    elif data == "edit_scenario":
        state["mode"] = "editing_scenario"
        await query.message.reply_text("Какие правки внести в сценарий? Опиши 👇")

    elif data == "approve_design":
        await query.message.reply_text("Разбиваю сценарий на сцены... ✂️")
        scenes = await parse_scenes(state.get("scenario", ""))
        if not scenes:
            await query.message.reply_text("Не удалось разобрать сцены 😕 Попробуй создать заново /start")
            return
        state["scenes"] = scenes
        state["current_scene"] = 0
        await query.message.reply_text(
            strip_md(
                f"Отлично! Сценарий разбит на {len(scenes)} сцен.\n\n"
                f"Буду присылать каждую сцену с описанием, текстом и озвучкой — ты решаешь генерировать или нет.\n\n"
                f"Поехали! 🚀"
            )
        )
        await show_scene(query, user_id)

    elif data == "edit_design":
        state["mode"] = "editing_design"
        await query.message.reply_text("Что изменить в визуальном стиле? 👇")

    elif data == "gen_scene":
        await generate_scene_video(query, user_id)

    elif data == "skip_scene":
        state["current_scene"] = state.get("current_scene", 0) + 1
        await show_scene(query, user_id)

    elif data == "edit_scene":
        state["mode"] = "editing_scene"
        await query.message.reply_text("Что изменить в этой сцене? 👇")

    elif data == "next_scene":
        state["current_scene"] = state.get("current_scene", 0) + 1
        await show_scene(query, user_id)

    elif data == "regen_scene":
        await generate_scene_video(query, user_id)

    elif data == "edit_regen_scene":
        state["mode"] = "editing_scene"
        await query.message.reply_text("Что изменить перед перегенерацией? 👇")


# ── VOICE HANDLER ──

async def handle_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text("Слушаю... 🎙")
    try:
        tg_file = await ctx.bot.get_file(update.message.voice.file_id)
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            await tg_file.download_to_drive(tmp.name)
            text = await transcribe_voice(tmp.name)
        if not text:
            await update.message.reply_text("Не удалось распознать 😕 Напиши текстом.")
            return
        await update.message.reply_text(f"Услышал: «{text}»\n\nОтвечаю... 💭")
        await process_message(update, ctx, user_id, text)
    except Exception as e:
        logger.error(f"Voice error: {e}")
        await update.message.reply_text("Не получилось обработать голосовое 😕")


# ── MAIN MESSAGE PROCESSOR ──

async def process_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE, user_id: int, text: str):
    state = get_state(user_id)
    mode = state.get("mode")

    await update.effective_message.chat.send_action(ChatAction.TYPING)

    # ── Clarification flows ──
    if mode == "ask_topic":
        state["mode"] = "clarify_video"
        state["clarify_answers"] = []
        state["brief_topic"] = text + " / "
        await update.effective_message.reply_text(
            "Отлично! Теперь 3 быстрых вопроса:\n\n"
            "1️⃣ Для кого снимаем? (возраст, пол, интересы)\n"
            "2️⃣ Какая цель? (продажи, охваты, подписчики)\n"
            "3️⃣ Какой тон? (экспертный, дружеский, с юмором)\n\n"
            "Можешь ответить на все три сразу одним сообщением 👇"
        )
        return

    if mode == "clarify_video":
        # Accept all answers in one message OR collect one by one
        answers = state.get("clarify_answers", [])
        answers.append(text)
        state["clarify_answers"] = answers

        # If message contains numbered answers (1. / 1) / 1️⃣) treat as all 3
        import re as _re
        has_multiple = bool(_re.search(r'(1[.\)️⃣]|во-первых|аудитор)', text, _re.IGNORECASE))

        if len(answers) >= 3 or has_multiple:
            state["mode"] = None
            brief = state.get("brief_topic", "") + " / ".join(answers)
            await start_production(update, brief, user_id)
        elif len(answers) == 1:
            await update.effective_message.reply_text(
                "Записал! Теперь ответь на вопросы 2 и 3:\n\n"
                "2️⃣ Какая цель? (продажи, охваты, подписчики)\n"
                "3️⃣ Какой тон? (экспертный, дружеский, с юмором)"
            )
        elif len(answers) == 2:
            await update.effective_message.reply_text(
                "Отлично! И последнее — какой тон?\n(экспертный, дружеский, с юмором) 👇"
            )
        return

    if mode in ("clarify_idea", "clarify_hashtags"):
        state["mode"] = None
        reply = await chat(user_id, text)
        await send(update, reply)
        return

    # ── Editing flows ──
    if mode == "editing_scenario":
        state["mode"] = None
        await update.effective_message.reply_text("Вношу правки в сценарий... ✍️")
        new_scenario = await claude_call(
            [{"role": "user", "content": f"Исходный сценарий:\n{state.get('scenario','')}\n\nПравки: {text}\n\nПерепиши сценарий с учётом правок."}],
            SCENARIO_SYSTEM,
            max_tokens=2000
        )
        state["scenario"] = new_scenario
        keyboard = [
            [InlineKeyboardButton("✅ Одобряю!", callback_data="approve_scenario")],
            [InlineKeyboardButton("✏️ Ещё правки", callback_data="edit_scenario")],
        ]
        await send(update, f"Обновлённый сценарий:\n\n{new_scenario}", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if mode == "editing_design":
        state["mode"] = None
        await update.effective_message.reply_text("Обновляю стиль... 🎨")
        new_design = await claude_call(
            [{"role": "user", "content": f"Исходный стиль:\n{state.get('design','')}\n\nПравки: {text}\n\nОбнови."}],
            DESIGN_SYSTEM,
            max_tokens=1000
        )
        state["design"] = new_design
        keyboard = [
            [InlineKeyboardButton("✅ Отлично!", callback_data="approve_design")],
            [InlineKeyboardButton("✏️ Ещё правки", callback_data="edit_design")],
        ]
        await send(update, f"Обновлённый стиль:\n\n{new_design}", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if mode == "editing_scene":
        state["mode"] = None
        current = state.get("current_scene", 0)
        scenes = state.get("scenes", [])
        if current < len(scenes):
            await update.effective_message.reply_text("Обновляю сцену... ✍️")
            scene = scenes[current]
            updated = await claude_call(
                [{"role": "user", "content": f"Сцена: {scene}\nПравки: {text}\nОбнови описание сцены. Верни JSON одной сцены."}],
                "Обновляй описание сцены. Возвращай ТОЛЬКО JSON объект сцены без пояснений.",
                max_tokens=500
            )
            import json
            try:
                updated_clean = re.sub(r'```.*?```', '', updated, flags=re.DOTALL).strip()
                scenes[current] = json.loads(updated_clean)
                state["scenes"] = scenes
            except Exception:
                pass
            await generate_scene_video(update, user_id)
        return

    # ── Default: check if production request ──
    # If user is in the middle of approval flow — remind them to use buttons
    approval_modes = ["awaiting_scenario_approval", "awaiting_design_approval", "awaiting_scene_approval"]
    if mode in approval_modes:
        await update.effective_message.reply_text(
            "Используй кнопки выше для продолжения. "
            "Хочешь внести правки - нажми на кнопку Внести правки."
        )
        return

    keywords = ["видео", "сценарий", "снять", "ролик", "reels", "tiktok", "shorts"]
    is_production = any(kw in text.lower() for kw in keywords)

    if is_production:
        state["mode"] = "ask_topic"
        state["brief_topic"] = text + " / "
        await send(update,
            "Отличная идея! 🎯\n\n"
            "Уточни тему — о чём конкретно видео? (продукт, услуга, тема) 👇"
        )
        return

    # Regular chat
    reply = await chat(user_id, text)
    await send(update, reply)


async def text_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await process_message(update, ctx, update.effective_user.id, update.message.text)


async def error_handler(update, context):
    """Silently ignore Conflict errors on startup"""
    from telegram.error import Conflict, NetworkError
    if isinstance(context.error, Conflict):
        logger.warning("Startup conflict resolved, continuing...")
        return
    if isinstance(context.error, NetworkError):
        return
    logger.error(f"Update error: {context.error}")


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_error_handler(error_handler)
    logger.info("GLAM AI Bot started!")

    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query"]
    )


if __name__ == "__main__":
    main()
