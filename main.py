import logging
import asyncio
import os
import random
import json
from aiogram import Bot, Dispatcher
from aiogram_dialog import Dialog, DialogManager, Window, StartMode
from aiogram_dialog.widgets.kbd import Button, Row, Column
from aiogram_dialog.widgets.text import Const, Format
from dotenv import load_dotenv
from openai import OpenAI

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения
load_dotenv()
API_TOKEN = os.getenv('BOT_TOKEN')  # Telegram Bot Token
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')  # OpenRouter API Key

if not API_TOKEN:
    raise ValueError("Токен бота не найден. Убедитесь, что переменная окружения BOT_TOKEN установлена.")
if not OPENROUTER_API_KEY:
    raise ValueError("Ключ API OpenRouter не найден. Убедитесь, что переменная окружения OPENROUTER_API_KEY установлена.")

# Инициализация бота и диспетчера
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# Инициализация OpenAI клиента
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)

# Словарь для хранения активных тестов пользователей
active_quizzes = {}

# Словарь для хранения статистики пользователей
user_stats = {}

# Генерация тестов через OpenAI
async def generate_quiz(subject: str, difficulty: str) -> list:
    formatted_prompt = (
        f"Сгенерируй 5 вопросов по теме {subject.capitalize()} на уровне {difficulty}. "
        "Ответ должен быть строго на русском языке в формате JSON. Каждый вопрос должен быть объектом с ключами:\n\n"
        "- 'question': текст вопроса\n"
        "- 'correct_answer': правильный ответ\n"
        "- 'incorrect_answers': список из трёх неправильных ответов\n\n"
        "Пример правильного ответа:\n\n"
        "[\n"
        "    {\n"
        "        \"question\": \"Сколько будет 2 + 2?\",\n"
        "        \"correct_answer\": \"4\",\n"
        "        \"incorrect_answers\": [\"3\", \"5\", \"6\"]\n"
        "    },\n"
        "    {\n"
        "        \"question\": \"Сколько градусов в прямом угле?\",\n"
        "        \"correct_answer\": \"90\",\n"
        "        \"incorrect_answers\": [\"45\", \"180\", \"360\"]\n"
        "    }\n"
        "]\n\n"
        "Сгенерируй только 5 объектов в указанном формате JSON. "
        "Если формат не соблюдается, повтори попытку и исправь ошибки."
    )

    try:
        completion = client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": formatted_prompt}]
        )
        response = completion.choices[0].message.content
        logger.info(f"Ответ от ИИ:\n{response}")
        return process_generated_quizzes(response)
    except Exception as e:
        logger.error(f"Ошибка при генерации тестов: {e}")
        return []

# Обработка JSON-ответа от OpenAI
def process_generated_quizzes(response: str) -> list:
    try:
        data = json.loads(response)
        quizzes = []
        for item in data:
            question = item.get("question", "").strip()
            correct_answer = item.get("correct_answer", "").strip()
            incorrect_answers = item.get("incorrect_answers", [])

            if not question or not correct_answer or len(incorrect_answers) != 3:
                logger.error(f"Некорректный объект вопроса: {item}")
                continue

            answers = [correct_answer] + incorrect_answers
            random.shuffle(answers)
            correct_answer_index = answers.index(correct_answer)

            quizzes.append({
                'question': question,
                'answers': answers,
                'correct_option_id': correct_answer_index
            })
        return quizzes
    except json.JSONDecodeError as e:
        logger.error(f"Ошибка парсинга JSON: {e}")
        return []

# Окно выбора предмета
async def on_subject_selected(c, button: Button, dialog_manager: DialogManager):
    subject = button.widget_id
    dialog_manager.current_context().dialog_data["subject"] = subject
    await c.message.answer(f"Вы выбрали предмет: {subject}. Теперь выберите уровень сложности.")
    await dialog_manager.switch_to("difficulty")

# Окно выбора уровня сложности
async def on_difficulty_selected(c, button: Button, dialog_manager: DialogManager):
    difficulty = button.widget_id
    subject = dialog_manager.current_context().dialog_data["subject"]
    dialog_manager.current_context().dialog_data["difficulty"] = difficulty

    user_id = c.from_user.id
    quizzes = await generate_quiz(subject, difficulty)
    if not quizzes:
        await c.message.answer("Не удалось сгенерировать тесты. Попробуйте ещё раз.")
        return

    active_quizzes[user_id] = quizzes
    dialog_manager.current_context().dialog_data["question_id"] = 0

    await c.message.answer(f"Вы выбрали уровень сложности: {difficulty}. Начинаем тест!")
    await dialog_manager.switch_to("quiz")

# Окно с вопросами
async def on_answer_selected(c, button: Button, dialog_manager: DialogManager):
    user_id = c.from_user.id
    data = dialog_manager.current_context().dialog_data
    question_id = data.get("question_id", 0)
    quizzes = active_quizzes.get(user_id, [])

    if not quizzes or question_id >= len(quizzes):
        await c.message.answer("Тест завершён!")
        await dialog_manager.switch_to("end")
        return

    quiz = quizzes[question_id]
    if button.widget_id == "correct":
        user_stats[user_id]["correct_answers"] = user_stats.get(user_id, {}).get("correct_answers", 0) + 1
        await c.message.answer("Правильно! 🎉")
    else:
        user_stats[user_id]["wrong_answers"] = user_stats.get(user_id, {}).get("wrong_answers", 0) + 1
        await c.message.answer("Неправильно. 😢")

    question_id += 1
    if question_id < len(quizzes):
        data["question_id"] = question_id
        await dialog_manager.switch_to("quiz")
    else:
        await dialog_manager.switch_to("end")

# Окно завершения теста
async def on_finish(c, button: Button, dialog_manager: DialogManager):
    await dialog_manager.start(StartMode.RESET_STACK)

# Диалог
dialog = Dialog(
    Window(
        Const("Выберите предмет:"),
        Row(
            Button(Const("Математика"), id="math", on_click=on_subject_selected),
            Button(Const("Русский язык"), id="russian", on_click=on_subject_selected),
        ),
        Row(
            Button(Const("Информатика"), id="informatics", on_click=on_subject_selected),
            Button(Const("Английский язык"), id="english", on_click=on_subject_selected),
        ),
        state="subject",
    ),
    Window(
        Const("Выберите уровень сложности:"),
        Row(
            Button(Const("Легкий"), id="easy", on_click=on_difficulty_selected),
            Button(Const("Средний"), id="medium", on_click=on_difficulty_selected),
            Button(Const("Сложный"), id="hard", on_click=on_difficulty_selected),
        ),
        state="difficulty",
    ),
    Window(
        Format("{question}"),
        Row(
            Button(Const("1"), id="correct", on_click=on_answer_selected),
            Button(Const("2"), id="wrong", on_click=on_answer_selected),
        ),
        state="quiz",
    ),
    Window(
        Const("Тест завершён!"),
        Row(
            Button(Const("Ещё вопросы"), id="more", on_click=on_finish),
            Button(Const("Сменить сложность"), id="change", on_click=on_finish),
            Button(Const("Статистика"), id="stats", on_click=on_finish),
        ),
        state="end",
    ),
)

# Регистрация
dp.include_router(dialog)

async def main():
    dp.include_router(dialog)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")

if __name__ == '__main__':
    asyncio.run(main())