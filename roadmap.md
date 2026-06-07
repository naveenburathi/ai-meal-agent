# AI Meal Tracker Bot Roadmap

## Next Features

1. Timely reminders to log food.
2. End-of-day daily macro report after food has been logged.
3. Bot commands for adding user-specific food items and their macros.
   - User starts an add-item command.
   - Bot asks for macros one by one.
   - Saved items can be reused in future meal logs.
4. Daily macro stats based on the user's logged entries.

## Suggested Build Order

### 1. Persist Meal Logs

Before reminders and reports, the bot needs a place to store meal entries.

- Use SQLite for MVP.
- Store each logged meal with timestamp, user ID, foods, calories, protein, carbs, and fat.
- Keep the schema small and easy to change.

Status: Implemented locally with SQLAlchemy and `DATABASE_URL`. Default is `sqlite:///meals.db`, but the code is structured so a hosted Postgres URL can replace it later.

### 2. Daily Stats Command

Add a command such as `/today`.

- Sum all meal entries for the current day.
- Reply with calories, protein, carbs, and fat totals.
- This gives immediate value and verifies that persistence works.

Status: Implemented as `/today`.

### 3. User-Specific Food Items

Add commands to save custom foods.

- Example command: `/add_food`.
- Bot asks for name, serving label, calories, protein, carbs, and fat.
- Store these custom foods in SQLite per Telegram user.
- Prefer custom foods over AI estimates when names match.

### 4. Timely Reminders

Add scheduled prompts for meals.

- Breakfast reminder.
- Lunch reminder.
- Dinner reminder.
- Use Telegram job queue locally for MVP.
- Later, use Railway-hosted always-on scheduling.

### 5. End-of-Day Report

Send a daily summary automatically at night.

- Include daily macro totals.
- Include number of meals logged.
- Mention whether protein target was reached once goals exist.

## Design Notes

- Keep the MVP conversational and low-friction.
- Do not expose provider/API errors to users.
- AI can estimate unknown foods, but common foods and custom foods should override AI when available.
- Prefer simple SQLite tables before introducing larger architecture.
