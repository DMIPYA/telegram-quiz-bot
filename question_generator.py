"""Генерация вопросов для викторины через NVIDIA NIM API."""

from __future__ import annotations

import json
import logging
from typing import Any

from openai import OpenAI

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Ты — генератор вопросов для викторины на русском языке.\n"
    "Отвечай ТОЛЬКО JSON, без лишнего текста, без markdown-разметки."
)

REQUIRED_FIELDS = {"text", "options", "correct_option_index", "explanation", "difficulty", "category"}
VALID_DIFFICULTIES = {"easy", "medium", "hard"}


MAX_RETRIES = 2


def build_prompt(category: str, count: int = 5) -> str:
    """Формирует промпт для генерации вопросов по категории."""
    return (
        f'Сгенерируй {count} вопросов для викторины на русском языке по теме "{category}".\n\n'
        'Каждый вопрос — это JSON-объект:\n'
        '  "text": "Текст вопроса"\n'
        '  "options": ["Вариант A", "Вариант B", "Вариант C", "Вариант D"]\n'
        '  "correct_option_index": 0 (индекс правильного ответа в options, от 0 до 3)\n'
        '  "explanation": "Пояснение"\n'
        '  "difficulty": "easy", "medium" или "hard"\n'
        f'  "category": "{category}"\n\n'
        'Требования:\n'
        '- Все на русском языке\n'
        '- Ровно 4 варианта ответа\n'
        '- Вопросы интересные и неочевидные\n'
        f'- Верни ТОЛЬКО JSON-массив из {count} вопросов, без лишнего текста\n'
        f'- Пример: [{{"text": "...", "options": ["...", "...", "...", "..."], "correct_option_index": 0, "explanation": "...", "difficulty": "easy", "category": "{category}"}}]\n'
    )


def _try_extract_questions(content: str) -> Any | None:
    """Пытается распарсить JSON из ответа модели.

    Модель может вернуть: массив вопросов, объект с вопросами внутри,
    или JSON с лишним текстом вокруг. Пробуем разные варианты.
    """
    content = content.strip()

    try:
        data = json.loads(content)
        return data
    except json.JSONDecodeError:
        pass

    # Ответ может содержать JSON внутри ```json ... ```
    for marker in ("```json", "```"):
        start = content.find(marker)
        if start == -1:
            continue
        start = content.index("\n", start) + 1
        end = content.rfind("```")
        if end <= start:
            continue
        block = content[start:end].strip()
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            continue

    logger.warning("Не удалось извлечь JSON из ответа модели")
    return None


def normalize_response(raw: Any) -> list[dict] | None:
    """Приводит ответ модели к списку вопросов.

    Поддерживает:
    - прямой массив [{...}, ...]
    - один объект {...} (один вопрос)
    - {"questions": [...]} или {"questions": {...}}
    - другие известные обёртки
    """
    if isinstance(raw, list):
        return raw

    if not isinstance(raw, dict):
        return None

    # Одиночный вопрос
    if REQUIRED_FIELDS.issubset(raw.keys()):
        return [raw]

    # Поиск массива внутри по известным ключам-обёрткам
    for key in ("questions", "question", "data", "items", "results", "quizzes"):
        val = raw.get(key)
        if isinstance(val, list):
            return val
        if isinstance(val, dict) and REQUIRED_FIELDS.issubset(val.keys()):
            return [val]

    return None


def call_nim(api_key: str, base_url: str, model: str, prompt: str) -> str | None:
    """Вызывает NVIDIA NIM API и возвращает сырой текст ответа."""
    client = OpenAI(api_key=api_key, base_url=base_url)
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=4096,
        )
        return response.choices[0].message.content
    except Exception:
        logger.exception("Ошибка вызова NVIDIA NIM API")
        return None


def validate_questions(raw_data: Any) -> list[dict]:
    """Проверяет и возвращает только валидные вопросы из сырых данных."""
    if not isinstance(raw_data, list):
        logger.warning("Ответ API не является массивом")
        return []

    valid: list[dict] = []
    for i, q in enumerate(raw_data):
        if not isinstance(q, dict):
            logger.warning("Вопрос %d: не является dict", i)
            continue

        missing = REQUIRED_FIELDS - q.keys()
        if missing:
            logger.warning("Вопрос %d: отсутствуют поля %s", i, missing)
            continue

        options = q.get("options")
        if not isinstance(options, list) or len(options) != 4:
            logger.warning("Вопрос %d: options должен быть списком из 4 элементов", i)
            continue

        idx = q.get("correct_option_index")
        if not isinstance(idx, int) or not (0 <= idx <= 3):
            logger.warning("Вопрос %d: correct_option_index должен быть int от 0 до 3", i)
            continue

        if q.get("difficulty") not in VALID_DIFFICULTIES:
            logger.warning("Вопрос %d: неверная сложность '%s'", i, q.get("difficulty"))
            continue

        if not isinstance(q.get("text"), str) or not q["text"].strip():
            logger.warning("Вопрос %d: пустой текст вопроса", i)
            continue

        if not isinstance(q.get("explanation"), str) or not q["explanation"].strip():
            logger.warning("Вопрос %d: пустое пояснение", i)
            continue

        valid.append(q)

    return valid


def generate_questions(api_key: str, base_url: str, model: str, category: str, count: int = 5) -> list[dict]:
    """Генерирует вопросы для одной категории через NVIDIA NIM с retry."""
    for attempt in range(MAX_RETRIES):
        prompt = build_prompt(category, count)
        content = call_nim(api_key, base_url, model, prompt)
        if not content:
            continue

        data = _try_extract_questions(content)
        if data is None:
            logger.warning("Попытка %d: не удалось распарсить ответ (категория «%s»)", attempt + 1, category)
            continue

        normalized = normalize_response(data)
        if normalized is None:
            logger.warning(
                "Попытка %d: неизвестная структура ответа (категория «%s»): %s...",
                attempt + 1,
                category,
                content[:200],
            )
            continue

        questions = validate_questions(normalized)
        if questions:
            logger.info(
                "Сгенерировано %d/%d валидных вопросов для категории «%s»",
                len(questions),
                count,
                category,
            )
            return questions

        logger.warning(
            "Попытка %d: 0 валидных вопросов (категория «%s»). Сырой ответ: %s...",
            attempt + 1,
            category,
            content[:300],
        )

    logger.warning("Не удалось сгенерировать вопросы для категории «%s» после %d попыток", category, MAX_RETRIES)
    return []


def generate_for_all_categories(
    api_key: str,
    base_url: str,
    model: str,
    categories: list[str],
    count_per: int = 3,
) -> list[dict]:
    """Генерирует вопросы для всех указанных категорий."""
    all_questions: list[dict] = []
    for cat in categories:
        questions = generate_questions(api_key, base_url, model, cat, count_per)
        all_questions.extend(questions)
    return all_questions
