import logging
import os
import random
import json
import asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
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
dp = Dispatcher(storage=MemoryStorage())

# Инициализация OpenAI клиента
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)

# Словарь для хранения активных тестов пользователей
active_quizzes = {}

# Словарь для хранения статистики пользователей
user_stats = {}

# Определение состояний
class QuizStates(StatesGroup):
    SUBJECT = State()
    DIFFICULTY = State()
    QUIZ = State()
    END = State()

# Генерация тестов через OpenAI
async def generate_quiz(subject: str, difficulty: str) -> list:
    formatted_prompt = (
        f"Сгенерируй 5 вопросов по теме {subject.capitalize()}, уровень сложности - вступительные экзамены в ВУЗы."
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
            model="openrouter/cypher-alpha:free",
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
        response = response.strip().strip("```json").strip("```")
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
        logger.error(f"Полученный ответ: {response}")
        return []

# Обработчики
@dp.message(Command("start"))
async def start_command_handler(message: Message, state: FSMContext):
    await state.set_state(QuizStates.SUBJECT)

    # Создаём клавиатуру для выбора предметов
    subject_keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Математика"), KeyboardButton(text="Русский язык")],
            [KeyboardButton(text="Информатика"), KeyboardButton(text="Английский язык")],
            [KeyboardButton(text="Показать статистику")]
        ],
        resize_keyboard=True
    )

    await message.answer(
        "Добро пожаловать в викторину! Выберите предмет:",
        reply_markup=subject_keyboard
    )

@dp.message(QuizStates.SUBJECT)
async def select_subject(message: Message, state: FSMContext):
    subjects = ["Математика", "Русский язык", "Информатика", "Английский язык"]
    if message.text == "Показать статистику":
        stats = user_stats.get(message.from_user.id, {"correct_answers": 0, "wrong_answers": 0})
        await message.answer(
            f"Ваша статистика:\n"
            f"Правильных ответов: {stats['correct_answers']}\n"
            f"Неправильных ответов: {stats['wrong_answers']}"
        )
        return

    if message.text not in subjects:
        await message.answer("Пожалуйста, выберите предмет из предложенных.")
        return

    subject = message.text
    await state.update_data(subject=subject)
    await message.answer(f"Вы выбрали предмет: {subject}. Пожалуйста, подождите. Генерируем вопросы...")

    # Генерация вопросов
    quizzes = await generate_quiz(subject, "Средний")  # Уровень сложности фиксирован
    if not quizzes:
        await message.answer("Не удалось сгенерировать тесты. Попробуйте ещё раз.")
        return

    active_quizzes[message.from_user.id] = quizzes
    await state.update_data(question_id=0)
    await state.set_state(QuizStates.QUIZ)

    # Отправляем первый вопрос
    await send_question(message, quizzes[0], 0)

async def send_question(message: Message, quiz: dict, question_id: int):
    """Отправляет вопрос с текстом вариантов и кнопками с цифрами."""
    answers = quiz.get("answers", [])
    if not answers or len(answers) != 4:  # Проверяем, что есть 4 варианта ответа
        logger.error(f"Некорректные данные для вопроса: {quiz}")
        await message.answer("Произошла ошибка при загрузке вопроса. Попробуйте позже.")
        return

    # Формируем текст вопроса с вариантами
    question_text = f"Вопрос: {quiz['question']}\n\n"
    for i, answer in enumerate(answers, start=1):
        question_text += f"{i}. {answer}\n"

    # Создаём инлайн-клавиатуру с цифрами
    keyboard_buttons = [
        [InlineKeyboardButton(text=str(i), callback_data=f"answer:{question_id}:{i-1}")]
        for i in range(1, 5)
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

    # Отправляем сообщение с вопросом и клавиатурой
    await message.answer(question_text, reply_markup=keyboard)

@dp.callback_query(F.data.startswith("answer:"))
async def handle_answer(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    data = await state.get_data()
    question_id = int(callback.data.split(":")[1])
    selected_answer = int(callback.data.split(":")[2])
    quizzes = active_quizzes.get(user_id, [])

    if not quizzes or question_id >= len(quizzes):
        await callback.message.answer("Тест завершён!")
        await state.set_state(QuizStates.END)
        return

    quiz = quizzes[question_id]
    correct_option_id = quiz["correct_option_id"]

    # Обновляем статистику
    user_stats[user_id] = user_stats.get(user_id, {"correct_answers": 0, "wrong_answers": 0})
    if selected_answer == correct_option_id:
        user_stats[user_id]["correct_answers"] += 1
        await callback.message.answer("Правильно! 🎉")
    else:
        user_stats[user_id]["wrong_answers"] += 1
        await callback.message.answer(f"Неправильно. Правильный ответ: {quiz['answers'][correct_option_id]}")

    # Переходим к следующему вопросу
    question_id += 1
    if question_id < len(quizzes):
        await state.update_data(question_id=question_id)
        await send_question(callback.message, quizzes[question_id], question_id)
    else:
        await callback.message.answer("Тест завершён! Спасибо за участие.")
        await state.set_state(QuizStates.END)

@dp.message(QuizStates.END)
async def end_quiz(message: Message, state: FSMContext):
    stats = user_stats.get(message.from_user.id, {"correct_answers": 0, "wrong_answers": 0})
    await message.answer(
        f"Ваши результаты:\n"
        f"Правильных ответов: {stats['correct_answers']}\n"
        f"Неправильных ответов: {stats['wrong_answers']}\n"
        "Чтобы начать заново, выберите предмет из меню.",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Математика"), KeyboardButton(text="Русский язык")],
                [KeyboardButton(text="Информатика"), KeyboardButton(text="Английский язык")],
                [KeyboardButton(text="Показать статистику")]
            ],
            resize_keyboard=True
        )
    )
    await state.clear()

# Запуск бота
async def main():
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")

if __name__ == "__main__":
    asyncio.run(main())