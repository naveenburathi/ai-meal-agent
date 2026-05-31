import asyncio
import json
import logging
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import DateTime, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters


load_dotenv()


FOOD_NAME_ALIASES = {
    "rotis": "roti",
    "chapati": "roti",
    "chapatis": "roti",
    "eggs": "egg",
    "egg whites": "egg white",
    "bananas": "banana",
    "oatmeal": "oats",
    "peanut butter": "peanut butter",
    "whey": "whey protein",
    "whey powder": "whey protein",
    "paneer sabzi": "paneer sabzi",
    "vegetable sabzi": "sabzi",
    "sabji": "sabzi",
}

UNKNOWN_QUANTITY_PHRASES = (
    "didn't specify",
    "did not specify",
    "not specified",
    "unspecified",
    "unknown",
    "not stated",
)

ESTIMATE_SYSTEM_PROMPT = (
    "Estimate calories, protein, carbs, and fat for meal tracking. "
    "Return one item per food. "
    "For quantity, use only short values like '3', '2 eggs', "
    "'1 bowl', or '1 serving'. If a quantity is not stated, "
    "use exactly '1 serving'. "
    "Calories and macros for each item must be the total for "
    "the stated quantity, not per unit. For example, 3 rotis "
    "is about 300-360 kcal, 9g protein, 60-75g carbs, and 6-12g fat total. "
    "2 eggs is about 140 kcal, 12g protein, 1g carbs, and 10g fat total. "
    "Use realistic Indian household serving estimates when exact grams are "
    "not given. Use integers only. Return JSON only."
)

CALIBRATED_FOODS = {
    "egg": {"calories": 70, "protein": 6, "carbs": 0, "fat": 5},
    "egg white": {"calories": 17, "protein": 4, "carbs": 0, "fat": 0},
    "roti": {"calories": 110, "protein": 3, "carbs": 22, "fat": 2},
    "banana": {"calories": 105, "protein": 1, "carbs": 27, "fat": 0},
}


class Base(DeclarativeBase):
    pass


class MealLog(Base):
    __tablename__ = "meal_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(Integer, index=True)
    telegram_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    meal_text: Mapped[str] = mapped_column(Text)
    foods_json: Mapped[str] = mapped_column(Text)
    calories: Mapped[int] = mapped_column(Integer)
    protein: Mapped[int] = mapped_column(Integer)
    carbs: Mapped[int] = mapped_column(Integer)
    fat: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class UserSetting(Base):
    __tablename__ = "user_settings"

    telegram_user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timezone: Mapped[str] = mapped_column(String(100), default="Asia/Kolkata")


class UserReminder(Base):
    __tablename__ = "user_meal_reminders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(Integer, index=True)
    reminder_time: Mapped[str] = mapped_column(String(5))  # "HH:MM" in 24-hour format


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///meals.db")
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class EstimatedFood(BaseModel):
    name: str = Field(description="Canonical food name in lowercase, singular when natural.")
    quantity: str = Field(description="Quantity as stated, or 1 serving when not stated.")
    calories: int = Field(description="Estimated calories for this food and quantity.")
    protein: int = Field(description="Estimated protein grams for this food and quantity.")
    carbs: int = Field(description="Estimated carbohydrate grams for this food and quantity.")
    fat: int = Field(description="Estimated fat grams for this food and quantity.")

    @field_validator("name")
    @classmethod
    def normalize_name(cls, name: str) -> str:
        normalized = name.strip().lower()
        return FOOD_NAME_ALIASES.get(normalized, normalized)

    @field_validator("quantity")
    @classmethod
    def default_quantity(cls, quantity: str) -> str:
        normalized = quantity.strip().lower()
        if not normalized or any(phrase in normalized for phrase in UNKNOWN_QUANTITY_PHRASES):
            return "1 serving"
        if normalized == "1":
            return "1 serving"
        return normalized

    @field_validator("calories", "protein", "carbs", "fat")
    @classmethod
    def keep_non_negative(cls, value: int) -> int:
        return max(0, value)


class MealEstimate(BaseModel):
    foods: list[EstimatedFood]
    total_calories: int = Field(description="Estimated total meal calories.")
    total_protein: int = Field(description="Estimated total meal protein grams.")
    total_carbs: int = Field(description="Estimated total meal carbohydrate grams.")
    total_fat: int = Field(description="Estimated total meal fat grams.")

    @field_validator("total_calories", "total_protein", "total_carbs", "total_fat")
    @classmethod
    def keep_total_non_negative(cls, value: int) -> int:
        return max(0, value)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = (
        "Welcome to the AI Meal Tracker Bot! 🍽️\n\n"
        "Send me any meal in natural language (e.g., '3 rotis and 2 eggs'), "
        "and I will estimate and track your calories and protein.\n\n"
        "<b>Commands:</b>\n"
        "📅 /today - Show today's macro stats\n"
        "⏰ /reminders - View your active reminders\n"
        "🔔 /set_reminder <code>HH:MM</code> - Add a reminder (24-hour format, e.g. `/set_reminder 08:30`)\n"
        "🌐 /timezone <code>timezone</code> - Configure your timezone\n"
        "❓ /help - Show this help message again"
    )
    await update.message.reply_text(msg, parse_mode='HTML')


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


def quantity_count(quantity: str) -> float:
    match = re.search(r"\d+(?:\.\d+)?", quantity)
    if not match:
        return 1
    return float(match.group())


def calibrate_estimate(estimate: MealEstimate) -> MealEstimate:
    for food in estimate.foods:
        calibrated_food = CALIBRATED_FOODS.get(food.name)
        if not calibrated_food:
            continue

        count = quantity_count(food.quantity)
        food.calories = round(calibrated_food["calories"] * count)
        food.protein = round(calibrated_food["protein"] * count)
        food.carbs = round(calibrated_food["carbs"] * count)
        food.fat = round(calibrated_food["fat"] * count)

    estimate.total_calories = sum(food.calories for food in estimate.foods)
    estimate.total_protein = sum(food.protein for food in estimate.foods)
    estimate.total_carbs = sum(food.carbs for food in estimate.foods)
    estimate.total_fat = sum(food.fat for food in estimate.foods)
    return estimate


def format_estimate(estimate: MealEstimate) -> str:
    lines = [
        "Meal logged ✅",
        f"Calories: ~{estimate.total_calories} kcal",
        f"Protein: ~{estimate.total_protein}g",
        f"Carbs: ~{estimate.total_carbs}g",
        f"Fat: ~{estimate.total_fat}g",
    ]

    if estimate.foods:
        lines.append("")
        lines.append("Items:")
        for food in estimate.foods:
            lines.append(
                f"- {food.name} ({food.quantity}): "
                f"~{food.calories} kcal, P {food.protein}g, "
                f"C {food.carbs}g, F {food.fat}g"
            )

    return "\n".join(lines)


def save_meal_log(user, meal_text: str, estimate: MealEstimate) -> None:
    meal_log = MealLog(
        telegram_user_id=user.id,
        telegram_username=user.username,
        meal_text=meal_text,
        foods_json=json.dumps([food.model_dump() for food in estimate.foods]),
        calories=estimate.total_calories,
        protein=estimate.total_protein,
        carbs=estimate.total_carbs,
        fat=estimate.total_fat,
        created_at=datetime.now(timezone.utc),
    )
    with SessionLocal() as session:
        session.add(meal_log)
        session.commit()


def today_bounds() -> tuple[datetime, datetime]:
    app_timezone = ZoneInfo(os.getenv("APP_TIMEZONE", "Asia/Kolkata"))
    today = datetime.now(app_timezone).date()
    start = datetime.combine(today, time.min, tzinfo=app_timezone)
    end = datetime.combine(today, time.max, tzinfo=app_timezone)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def get_today_logs(user_id: int) -> list[MealLog]:
    start, end = today_bounds()
    statement = (
        select(MealLog)
        .where(MealLog.telegram_user_id == user_id)
        .where(MealLog.created_at >= start)
        .where(MealLog.created_at <= end)
        .order_by(MealLog.created_at.asc())
    )
    with SessionLocal() as session:
        return list(session.scalars(statement))


def format_daily_stats(logs: list[MealLog]) -> str:
    if not logs:
        return "No meals logged today yet."

    calories = sum(log.calories for log in logs)
    protein = sum(log.protein for log in logs)
    carbs = sum(log.carbs for log in logs)
    fat = sum(log.fat for log in logs)

    return "\n".join(
        [
            "Today Summary",
            f"Meals logged: {len(logs)}",
            f"Calories: ~{calories} kcal",
            f"Protein: ~{protein}g",
            f"Carbs: ~{carbs}g",
            f"Fat: ~{fat}g",
        ]
    )


def get_user_timezone_name(user_id: int) -> str:
    with SessionLocal() as session:
        setting = session.get(UserSetting, user_id)
        if setting:
            return setting.timezone
        return os.getenv("APP_TIMEZONE", "Asia/Kolkata")


def set_user_timezone(user_id: int, tz_name: str) -> None:
    with SessionLocal() as session:
        setting = session.get(UserSetting, user_id)
        if not setting:
            setting = UserSetting(telegram_user_id=user_id, timezone=tz_name)
            session.add(setting)
        else:
            setting.timezone = tz_name
        session.commit()


def get_next_reminder_time(user_id: int, current_time_str: str) -> str | None:
    with SessionLocal() as session:
        reminders = session.scalars(
            select(UserReminder)
            .where(UserReminder.telegram_user_id == user_id)
            .order_by(UserReminder.reminder_time.asc())
        ).all()

        times = [r.reminder_time for r in reminders]
        if not times:
            return None

        # Find the next reminder time chronologically after current_time_str
        for t in times:
            if t > current_time_str:
                return t

        # If none are after current_time_str, wrap around to the first reminder
        return times[0]


async def send_reminder_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.job
    user_id = job.data["user_id"]
    time_str = job.data["time_str"]

    next_time = await asyncio.to_thread(get_next_reminder_time, user_id, time_str)

    if next_time:
        text = (
            f"This is a meal reminder that you set up.\n"
            f"Next reminder: {next_time}"
        )
    else:
        text = "This is a meal reminder that you set up."

    await context.bot.send_message(
        chat_id=user_id,
        text=text
    )


def schedule_user_reminder(job_queue, user_id: int, time_str: str, tz_name: str) -> None:
    time_key = time_str.replace(":", "")
    job_name = f"reminder_{user_id}_{time_key}"

    current_jobs = job_queue.get_jobs_by_name(job_name)
    for job in current_jobs:
        job.schedule_removal()

    try:
        hour_str, min_str = time_str.split(":")
        hour = int(hour_str)
        minute = int(min_str)
    except ValueError:
        raise ValueError("Time must be in HH:MM format.")

    try:
        user_tz = ZoneInfo(tz_name)
    except Exception:
        user_tz = ZoneInfo("Asia/Kolkata")

    reminder_time = time(hour=hour, minute=minute, tzinfo=user_tz)

    job_queue.run_daily(
        callback=send_reminder_callback,
        time=reminder_time,
        name=job_name,
        data={"user_id": user_id, "time_str": time_str}
    )


def cancel_user_reminder(job_queue, user_id: int, time_str: str) -> None:
    time_key = time_str.replace(":", "")
    job_name = f"reminder_{user_id}_{time_key}"
    current_jobs = job_queue.get_jobs_by_name(job_name)
    for job in current_jobs:
        job.schedule_removal()


def reschedule_user_all_reminders(job_queue, user_id: int, tz_name: str) -> None:
    with SessionLocal() as session:
        reminders = session.scalars(
            select(UserReminder)
            .where(UserReminder.telegram_user_id == user_id)
        ).all()
        for r in reminders:
            try:
                schedule_user_reminder(job_queue, user_id, r.reminder_time, tz_name)
            except Exception:
                pass


def load_all_reminders(application) -> None:
    if not application.job_queue:
        print("JobQueue is not initialized. Skipping loading reminders.")
        return

    with SessionLocal() as session:
        settings = session.scalars(select(UserSetting)).all()
        tz_map = {s.telegram_user_id: s.timezone for s in settings}

        reminders = session.scalars(select(UserReminder)).all()

        for r in reminders:
            tz_name = tz_map.get(r.telegram_user_id, os.getenv("APP_TIMEZONE", "Asia/Kolkata"))
            try:
                schedule_user_reminder(
                    application.job_queue,
                    r.telegram_user_id,
                    r.reminder_time,
                    tz_name
                )
            except Exception as e:
                print(f"Failed to schedule reminder at {r.reminder_time} for user {r.telegram_user_id}: {e}")


def request_json(url: str, payload: dict, headers: dict, timeout: int = 45) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def estimate_meal_with_openrouter(text: str) -> MealEstimate:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")

    model = os.getenv("OPENROUTER_MODEL", "google/gemini-2.5-flash-lite")
    schema = MealEstimate.model_json_schema()
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": ESTIMATE_SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "meal_estimate",
                "strict": True,
                "schema": schema,
            },
        },
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "http://localhost"),
        "X-Title": os.getenv("OPENROUTER_APP_NAME", "AI Meal Agent"),
    }
    data = request_json(
        "https://openrouter.ai/api/v1/chat/completions",
        payload,
        headers,
    )
    content = data["choices"][0]["message"]["content"]
    estimate = MealEstimate.model_validate_json(content)
    return calibrate_estimate(estimate)


def estimate_meal_with_ollama(text: str) -> MealEstimate:
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    model = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
    payload = {
        "model": model,
        "stream": False,
        "format": MealEstimate.model_json_schema(),
        "options": {"temperature": 0},
        "messages": [
            {"role": "system", "content": ESTIMATE_SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
    }
    data = request_json(
        f"{base_url}/api/chat",
        payload,
        {"Content-Type": "application/json"},
    )

    content = data["message"]["content"]
    estimate = MealEstimate.model_validate_json(content)
    return calibrate_estimate(estimate)


logger = logging.getLogger(__name__)


def estimate_meal(text: str) -> MealEstimate:
    try:
        return estimate_meal_with_openrouter(text)
    except Exception as e:
        logger.warning("OpenRouter failed: %s", e)
        return estimate_meal_with_ollama(text)


async def handle_meal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text
    try:
        estimate = await asyncio.to_thread(estimate_meal, text)
    except Exception as e:
        logger.error("Both LLM providers failed for '%s': %s", text, e)
        await update.message.reply_text(
            "I couldn't understand that meal clearly yet. "
            "Try sending it like: 3 rotis, paneer sabzi, curd."
        )
        return

    await asyncio.to_thread(save_meal_log, update.effective_user, text, estimate)
    await update.message.reply_text(format_estimate(estimate))


async def today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logs = await asyncio.to_thread(get_today_logs, update.effective_user.id)
    await update.message.reply_text(format_daily_stats(logs))


async def list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    tz_name = await asyncio.to_thread(get_user_timezone_name, user_id)

    def get_reminders():
        with SessionLocal() as session:
            return session.scalars(
                select(UserReminder)
                .where(UserReminder.telegram_user_id == user_id)
                .order_by(UserReminder.reminder_time.asc())
            ).all()

    reminders = await asyncio.to_thread(get_reminders)
    times = [r.reminder_time for r in reminders]

    if not times:
        active_list = "No active reminders set."
    else:
        active_list = "\n".join(f"• `{t}`" for t in times)

    msg = (
        f"⏰ *Your Meal Reminders*\n"
        f"Timezone: `{tz_name}`\n\n"
        f"**Active Reminders:**\n"
        f"{active_list}\n\n"
        f"✍️ *To add a reminder:*\n"
        f"`/set_reminder <HH:MM>` (Must be 24-hour format)\n"
        f"Example: `/set_reminder 08:30`\n"
        f"Example: `/set_reminder 21:00`\n\n"
        f"❌ *To remove a reminder:*\n"
        f"`/set_reminder <HH:MM> off`\n"
        f"Example: `/set_reminder 08:30 off`\n\n"
        f"🌐 *To change timezone:*\n"
        f"`/timezone <timezone_name>`\n"
        f"Example: `/timezone Asia/Kolkata`"
    )
    await update.message.reply_markdown(msg)


async def set_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if len(args) < 1 or len(args) > 2:
        await update.message.reply_text(
            "Usage:\n"
            "• To add: /set_reminder <HH:MM> (e.g., /set_reminder 09:30)\n"
            "• To remove: /set_reminder <HH:MM> off (e.g., /set_reminder 09:30 off)\n"
            "Note: Please use 24-hour format (00:00 to 23:59)."
        )
        return

    time_str = args[0].strip()
    action = args[1].strip().lower() if len(args) == 2 else "on"

    # Validate format
    if not re.match(r"^\d{2}:\d{2}$", time_str):
        await update.message.reply_text(
            "Invalid format. Time must be in 24-hour HH:MM format (e.g., 09:30 or 21:00)."
        )
        return

    try:
        h, m = map(int, time_str.split(":"))
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError()
    except ValueError:
        await update.message.reply_text(
            "Invalid time values. Hours must be 00-23 and minutes 00-59."
        )
        return

    user_id = update.effective_user.id
    tz_name = await asyncio.to_thread(get_user_timezone_name, user_id)

    if action == "off":
        def remove():
            with SessionLocal() as session:
                reminder = session.scalars(
                    select(UserReminder)
                    .where(UserReminder.telegram_user_id == user_id)
                    .where(UserReminder.reminder_time == time_str)
                ).first()
                if reminder:
                    session.delete(reminder)
                    session.commit()
                    return True
                return False

        existed = await asyncio.to_thread(remove)
        if existed:
            cancel_user_reminder(context.job_queue, user_id, time_str)
            await update.message.reply_text(f"Removed reminder for {time_str}.")
        else:
            await update.message.reply_text(f"No reminder was set for {time_str}.")
    else:
        def add():
            with SessionLocal() as session:
                reminder = session.scalars(
                    select(UserReminder)
                    .where(UserReminder.telegram_user_id == user_id)
                    .where(UserReminder.reminder_time == time_str)
                ).first()
                if not reminder:
                    reminder = UserReminder(telegram_user_id=user_id, reminder_time=time_str)
                    session.add(reminder)
                    session.commit()

        await asyncio.to_thread(add)

        try:
            schedule_user_reminder(context.job_queue, user_id, time_str, tz_name)
        except Exception as e:
            await update.message.reply_text(f"Failed to schedule reminder: {e}")
            return

        await update.message.reply_text(f"Set reminder for {time_str} ({tz_name}) in 24-hour format.")


async def set_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if len(args) != 1:
        await update.message.reply_text(
            "Usage: /timezone <timezone_name>\n"
            "Example: /timezone Asia/Kolkata\n"
            "Example: /timezone America/New_York"
        )
        return

    tz_name = args[0].strip()

    try:
        ZoneInfo(tz_name)
    except Exception:
        await update.message.reply_text(
            f"'{tz_name}' is not a recognized timezone name.\n"
            "Please use a standard IANA timezone name like 'Asia/Kolkata', 'America/New_York', or 'Europe/London'."
        )
        return

    user_id = update.effective_user.id

    await asyncio.to_thread(set_user_timezone, user_id, tz_name)
    await asyncio.to_thread(reschedule_user_all_reminders, context.job_queue, user_id, tz_name)

    await update.message.reply_text(f"Your timezone has been updated to {tz_name}. Existing reminders rescheduled.")


async def post_init(application: Application) -> None:
    await application.bot.set_my_commands([
        BotCommand("today", "Show today's macro stats"),
        BotCommand("reminders", "View your active reminders"),
        BotCommand("set_reminder", "Add/remove reminder time (HH:MM [off])"),
        BotCommand("timezone", "Set your local timezone"),
        BotCommand("help", "Show help instructions"),
    ])


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN in .env")

    init_db()

    app = Application.builder().token(token).post_init(post_init).build()

    # Load and schedule existing reminders from the database
    load_all_reminders(app)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("today", today))
    app.add_handler(CommandHandler("reminders", list_reminders))
    app.add_handler(CommandHandler("set_reminder", set_reminder))
    app.add_handler(CommandHandler("timezone", set_timezone))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_meal))

    print("Bot is running. Press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
