# Старт проекта и /start

Status: ready-for-agent

## What to build

Создать скелет проекта:

- `requirements.txt` с зависимостями (python-telegram-bot, python-dotenv)
- `.env.example` с шаблоном `BOT_TOKEN=`
- `bot.py` — точка входа
- Обработчик команды `/start`: отправляет приветственное сообщение с эмодзи
- Главное меню с тремя inline-кнопками: «Начать викторину», «Мой счёт», «Помощь»
- Кнопки показывают заглушки (сообщение «Скоро будет доступно»)

Использовать `python-telegram-bot` v20+ (async), `Application` + `CommandHandler`.

## Acceptance criteria

- [ ] `requirements.txt` содержит `python-telegram-bot`, `python-dotenv`
- [ ] `.env.example` создан
- [ ] `/start` показывает приветствие + 3 inline-кнопки в главном меню
- [ ] Каждая кнопка при нажатии показывает заглушку
- [ ] Бот запускается без ошибок (проверка синтаксиса)

## Blocked by

None — can start immediately
