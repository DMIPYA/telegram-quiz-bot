import asyncio
import json
import os
import random
import logging

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

from question_generator import generate_for_all_categories

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в .env файле")

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
NVIDIA_BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "meta/llama-3.1-70b-instruct")

admin_ids_str = os.getenv("ADMIN_USER_IDS", "")
ADMIN_USER_IDS = {int(x.strip()) for x in admin_ids_str.split(",") if x.strip().isdigit()}

if not NVIDIA_API_KEY:
    raise ValueError("NVIDIA_API_KEY не найден в .env файле")

QUESTIONS_FILE = "questions.json"
STATS_FILE = "stats.json"
questions = []
stats_data = {}

DIFFICULTY_POINTS = {"easy": 1, "medium": 2, "hard": 3}

ROWS_PER_QUESTION = 5


async def safe_edit(query, text: str, reply_markup=None, parse_mode: str | None = "Markdown") -> None:
    """Безопасно редактирует сообщение, обрабатывая устаревшие кнопки."""
    try:
        if reply_markup:
            await query.edit_message_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
        else:
            await query.edit_message_text(text, parse_mode=parse_mode)
    except BadRequest as e:
        if "Message is not modified" in str(e):
            pass
        elif "message can't be edited" in str(e).lower() or "query is too old" in str(e).lower():
            logger.warning("Попытка редактировать устаревшее сообщение")
        else:
            logger.warning(f"Ошибка редактирования: {e}")


def load_questions() -> None:
    """Загружает вопросы из JSON-файла."""
    global questions
    try:
        with open(QUESTIONS_FILE, "r", encoding="utf-8") as f:
            questions = json.load(f)
        logger.info(f"Загружено {len(questions)} вопросов")
    except FileNotFoundError:
        logger.error(f"Файл {QUESTIONS_FILE} не найден")
        questions = []
    except json.JSONDecodeError:
        logger.error(f"Ошибка парсинга {QUESTIONS_FILE}")
        questions = []


def load_stats() -> None:
    """Загружает статистику из JSON-файла."""
    global stats_data
    try:
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            stats_data = json.load(f)
        logger.info(f"Загружена статистика для {len(stats_data)} пользователей")
    except (FileNotFoundError, json.JSONDecodeError):
        stats_data = {}


def save_stats() -> None:
    """Сохраняет статистику в JSON-файл (атомарная запись)."""
    try:
        tmp_file = STATS_FILE + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(stats_data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_file, STATS_FILE)
    except OSError as e:
        logger.error(f"Ошибка сохранения статистики: {e}")


def add_questions(new_questions: list[dict]) -> None:
    """Добавляет новые вопросы в questions.json и обновляет глобальный список."""
    global questions
    if not new_questions:
        return
    try:
        with open(QUESTIONS_FILE, "r", encoding="utf-8") as f:
            existing = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        existing = []

    existing.extend(new_questions)

    try:
        tmp_file = QUESTIONS_FILE + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_file, QUESTIONS_FILE)
    except OSError as e:
        logger.error(f"Ошибка сохранения вопросов: {e}")
        return

    questions = existing
    logger.info("Добавлено %d новых вопросов", len(new_questions))


def get_all_categories() -> list[str]:
    """Возвращает список всех уникальных категорий."""
    cats = sorted({q["category"] for q in questions})
    return cats


def get_categories() -> list[str]:
    """Возвращает список категорий, в которых >= 5 вопросов."""
    counts = {}
    for q in questions:
        cat = q["category"]
        counts[cat] = counts.get(cat, 0) + 1
    return sorted([cat for cat, count in counts.items() if count >= 5])


def get_random_questions(category: str | None, count: int = 5) -> list[dict]:
    """Возвращает случайные вопросы."""
    if category not in (None, "Любая"):
        pool = [q for q in questions if q["category"] == category]
    else:
        pool = list(questions)
    return random.sample(pool, min(count, len(pool)))


def get_level_stats(user_id: int | str) -> dict:
    """Возвращает статистику пользователя."""
    s = stats_data.get(str(user_id), {"total_questions": 0, "correct_answers": 0, "total_points": 0, "games_played": 0})
    total = s["total_questions"]
    correct = s["correct_answers"]
    if total == 0:
        percent = 0
    else:
        percent = round(correct / total * 100)

    if percent < 50:
        level = "🌱 Начинающий"
    elif percent < 75:
        level = "🔍 Любознательный"
    elif percent < 90:
        level = "📚 Знаток"
    else:
        level = "🏆 Эксперт"

    return {**s, "percent": percent, "level": level}


def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    """Главное меню."""
    keyboard = [
        [InlineKeyboardButton("🧠 Начать викторину", callback_data="quiz")],
        [InlineKeyboardButton("📊 Мой счёт", callback_data="stats")],
        [InlineKeyboardButton("❓ Помощь", callback_data="help")],
    ]
    return InlineKeyboardMarkup(keyboard)


# ──────────────────────── ХЕНДЛЕРЫ ────────────────────────


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /start — приветствие и главное меню."""
    user = update.effective_user
    greeting = (
        f"👋 Привет, {user.first_name}!\n\n"
        f"🧠 Добро пожаловать в Quiz Bot — викторину на эрудицию!\n"
        f"Проверь свои знания в 12 категориях: от истории до космоса.\n\n"
        f"👇 Выбери действие в меню ниже:"
    )
    await update.message.reply_text(greeting, reply_markup=get_main_menu_keyboard())


async def generate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /generate — генерация новых вопросов через NVIDIA NIM."""
    user_id = update.effective_user.id
    if user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("❌ У вас нет прав на генерацию вопросов.")
        return

    if not questions:
        await update.message.reply_text(
            "❌ Нет загруженных вопросов. Проверьте questions.json."
        )
        return

    categories = get_all_categories()
    if not categories:
        await update.message.reply_text("❌ Нет доступных категорий для генерации.")
        return

    await update.message.reply_text("⏳ Генерирую новые вопросы через NVIDIA NIM...")

    try:
        new_questions = await asyncio.to_thread(
            generate_for_all_categories,
            NVIDIA_API_KEY,
            NVIDIA_BASE_URL,
            NVIDIA_MODEL,
            categories,
            3,
        )
    except Exception as e:
        logger.exception("Ошибка при генерации вопросов")
        await update.message.reply_text(f"❌ Ошибка генерации: {e}")
        return

    if not new_questions:
        await update.message.reply_text("❌ Не удалось сгенерировать ни одного валидного вопроса.")
        return

    add_questions(new_questions)
    await update.message.reply_text(
        f"✅ Сгенерировано {len(new_questions)} новых вопросов!\n"
        f"📂 Категории: {', '.join(categories)}"
    )


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Центральный обработчик всех inline-кнопок."""
    query = update.callback_query
    await query.answer()

    data = query.data
    user_id = str(query.from_user.id)

    if data == "quiz":
        await show_categories(query)

    elif data == "stats":
        stats = get_level_stats(user_id)
        text = (
            f"📊 **Мой счёт**\n\n"
            f"📝 Всего вопросов: **{stats['total_questions']}**\n"
            f"✅ Правильных: **{stats['correct_answers']}**\n"
            f"📈 Процент: **{stats['percent']}%**\n"
            f"⭐ Всего баллов: **{stats['total_points']}**\n"
            f"🎮 Сыграно раундов: **{stats['games_played']}**\n"
            f"🎯 Уровень: **{stats['level']}**\n\n"
            f"Продолжай играть, чтобы повысить уровень! 💪"
        )
        keyboard = [[InlineKeyboardButton("🔙 Главное меню", callback_data="menu")]]
        await safe_edit(query, text, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "help":
        text = (
            "❓ **Помощь**\n\n"
            "🧠 **Как играть в викторину?**\n\n"
            "1. Нажми «Начать викторину» в главном меню\n"
            "2. Выбери категорию (или «Любая»)\n"
            "3. Ответь на 5 вопросов подряд\n"
            "4. Получи очки за правильные ответы\n"
            "5. Следи за прогрессом в «Мой счёт»\n\n"
            "**Сложность вопросов:**\n"
            "🟢 Лёгкий — 1 балл\n"
            "🟡 Средний — 2 балла\n"
            "🔴 Сложный — 3 балла\n\n"
            "**Уровни:**\n"
            "🌱 Начинающий — < 50%\n"
            "🔍 Любознательный — 50–74%\n"
            "📚 Знаток — 75–89%\n"
            "🏆 Эксперт — 90%+\n\n"
            "Вопросы добавляются регулярно. Удачи! 🍀"
        )
        keyboard = [[InlineKeyboardButton("🔙 Главное меню", callback_data="menu")]]
        await safe_edit(query, text, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "menu":
        await safe_edit(query, "👋 Возвращайся в любое время!", reply_markup=get_main_menu_keyboard())

    elif data.startswith("cat_"):
        category = data[4:]
        await start_round(query, context, category)

    elif data.startswith("ans_"):
        parts = data.split("_")
        try:
            selected = int(parts[1])
        except (ValueError, IndexError):
            await query.edit_message_text("😕 Ошибка обработки ответа. Попробуй ещё раз.")
            return
        await handle_answer(query, context, selected)

    elif data == "next":
        await next_question(query, context)

    elif data == "replay":
        category = context.user_data.get("round_category", "Любая")
        await start_round(query, context, category)

    else:
        await query.edit_message_text("🤷 Неизвестная команда.")


# ──────────────────────── ВИКТОРИНА ────────────────────────


async def show_categories(query) -> None:
    """Показывает выбор категорий."""
    cats = get_categories()
    if not cats:
        await query.edit_message_text(
            "😕 К сожалению, пока нет доступных категорий с достаточным количеством вопросов.\n"
            "Попробуй заглянуть позже!"
        )
        return

    keyboard = []
    row = []
    for i, cat in enumerate(cats):
        row.append(InlineKeyboardButton(cat, callback_data=f"cat_{cat}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    keyboard.append([InlineKeyboardButton("🎲 Любая", callback_data="cat_Любая")])
    keyboard.append([InlineKeyboardButton("🔙 Главное меню", callback_data="menu")])

    await safe_edit(
        query,
        "📂 **Выбери категорию:**\n\nВыбери тему для викторины или нажми «Любая», чтобы получить вопросы из всех категорий!",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def start_round(query, context: ContextTypes.DEFAULT_TYPE, category: str) -> None:
    """Начинает раунд викторины."""
    questions_pool = get_random_questions(category, ROWS_PER_QUESTION)
    if not questions_pool:
        await query.edit_message_text(
            f"😕 В категории «{category}» недостаточно вопросов. Выбери другую!",
        )
        return

    context.user_data["round_category"] = category
    context.user_data["round"] = {
        "questions": questions_pool,
        "current_index": 0,
        "correct_count": 0,
        "total_points": 0,
    }

    await show_current_question(query, context)


async def show_current_question(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает текущий вопрос."""
    round_data = context.user_data.get("round")
    if not round_data:
        await query.edit_message_text("😕 Что-то пошло не так. Начни заново!", reply_markup=get_main_menu_keyboard())
        return

    idx = round_data["current_index"]
    q = round_data["questions"][idx]

    difficulty_emoji = {"easy": "🟢", "medium": "🟡", "hard": "🔴"}
    diff = difficulty_emoji.get(q["difficulty"], "")

    text = (
        f"**Вопрос {idx + 1} из {ROWS_PER_QUESTION}**\n\n"
        f"{q['text']}\n\n"
        f"{diff} Сложность: {q['difficulty']}"
    )

    keyboard = []
    for i, option in enumerate(q["options"]):
        keyboard.append([InlineKeyboardButton(option, callback_data=f"ans_{i}")])

    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_answer(query, context: ContextTypes.DEFAULT_TYPE, selected: int) -> None:
    """Обрабатывает ответ пользователя."""
    round_data = context.user_data.get("round")
    if not round_data:
        await query.edit_message_text("😕 Что-то пошло не так. Начни заново!", reply_markup=get_main_menu_keyboard())
        return

    idx = round_data["current_index"]
    q = round_data["questions"][idx]
    correct = q["correct_option_index"]
    user_id = str(query.from_user.id)

    is_correct = selected == correct
    points = DIFFICULTY_POINTS.get(q["difficulty"], 1) if is_correct else 0

    if is_correct:
        round_data["correct_count"] += 1
        round_data["total_points"] += points

    # Статистика (персистентная)
    if user_id not in stats_data:
        stats_data[user_id] = {"total_questions": 0, "correct_answers": 0, "total_points": 0, "games_played": 0}
    stats_data[user_id]["total_questions"] += 1
    if is_correct:
        stats_data[user_id]["correct_answers"] += 1
    stats_data[user_id]["total_points"] += points
    save_stats()

    # Формируем текст результата
    difficulty_emoji = {"easy": "🟢", "medium": "🟡", "hard": "🔴"}
    diff = difficulty_emoji.get(q["difficulty"], "")

    if is_correct:
        result_icon = "✅ **Верно!**"
    else:
        correct_text = q["options"][correct]
        result_icon = f"❌ **Неверно!** Правильный ответ: **{correct_text}**"

    text = (
        f"**Вопрос {idx + 1} из {ROWS_PER_QUESTION}**\n\n"
        f"{q['text']}\n\n"
        f"{result_icon}\n\n"
        f"**Пояснение:** {q['explanation']}\n\n"
        f"{diff} Сложность: {q['difficulty']} | "
        f"{'✅' if is_correct else '❌'} {'+' if is_correct else ''}{points} {'балл' if points == 1 else 'балла' if 2 <= points <= 3 else 'баллов'}"
    )

    keyboard = [[InlineKeyboardButton("➡️ Дальше", callback_data="next")]]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def next_question(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Переходит к следующему вопросу или показывает итог."""
    round_data = context.user_data.get("round")
    if not round_data:
        await query.edit_message_text("😕 Что-то пошло не так. Начни заново!", reply_markup=get_main_menu_keyboard())
        return

    idx = round_data["current_index"] + 1

    if idx < ROWS_PER_QUESTION:
        round_data["current_index"] = idx
        await show_current_question(query, context)
    else:
        # Итог раунда
        user_id = str(query.from_user.id)
        stats_data[user_id]["games_played"] += 1
        save_stats()

        total = ROWS_PER_QUESTION
        correct = round_data["correct_count"]
        points = round_data["total_points"]

        text = (
            f"🏁 **Раунд завершён!**\n\n"
            f"✅ Правильных ответов: **{correct} из {total}**\n"
            f"⭐ Набрано баллов: **{points}**\n"
        )

        keyboard = [
            [InlineKeyboardButton("🔄 Ещё раунд", callback_data="replay")],
            [InlineKeyboardButton("🔙 Главное меню", callback_data="menu")],
        ]

        context.user_data.pop("round", None)
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


# ──────────────────────── ЗАПУСК ────────────────────────


def main() -> None:
    load_questions()
    load_stats()
    if not questions:
        logger.warning("Нет загруженных вопросов! Викторина будет недоступна.")

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("generate", generate))
    application.add_handler(CallbackQueryHandler(callback_handler))

    logger.info("Бот запущен и готов к работе!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
