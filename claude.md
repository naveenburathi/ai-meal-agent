# Claude Developer Guide (`claude.md`)

This guide outlines specific constraints, recommendations, and code patterns for **Claude** (e.g., Claude 3.5 Sonnet) when modifying this project.

---

## ⚠️ Critical Warning: Pydantic v2 vs v1

Claude has a strong tendency to hallucinate Pydantic v1 code (e.g., using `class Config:`, `@validator`, or `.dict()`) because a significant amount of its pre-training data contains Pydantic v1 syntax.

This project is built on **Pydantic v2** (`pydantic==2.13.4`). You **must** adhere to the following v2 conventions:

| Action | Pydantic v1 (DO NOT USE) | Pydantic v2 (USE THIS) |
| :--- | :--- | :--- |
| **Field Validation** | `@validator("name")` | `@field_validator("name")` + `@classmethod` |
| **Model Validation (JSON)**| `MealEstimate.parse_raw(...)` | `MealEstimate.model_validate_json(...)` |
| **Get JSON Schema** | `MealEstimate.schema()` | `MealEstimate.model_json_schema()` |
| **Convert to Dict** | `estimate.dict()` | `estimate.model_dump()` |

Example validator structure in `app.py`:
```python
class EstimatedFood(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def normalize_name(cls, name: str) -> str:
        return name.strip().lower()
```

---

## 🌐 Network Operations (`urllib.request`)

Do **not** introduce external networking packages (like `requests` or `httpx`) to the core server implementation unless explicitly directed.
- The project implements a lightweight synchronous post helper `request_json(...)` using built-in `urllib.request`.
- Keep the current structure to avoid adding unneeded dependencies.
- When calling `urllib.request.urlopen`, ensure timeouts are passed (default is 45s).

---

## 🤖 Telegram Bot (v20+) Async/Await

`python-telegram-bot` version 22.7 is used. It is fully asynchronous.
- All handler functions (`start`, `handle_meal`) **must** be async (`async def`).
- Synchronous calls inside handlers (like `estimate_meal`, which communicates with external APIs synchronously via `urllib`) **must** be run using `asyncio.to_thread` to prevent blocking the main event loop:
  ```python
  estimate = await asyncio.to_thread(estimate_meal, text)
  ```
- Always use `await update.message.reply_text(...)` to send responses.

---

## 🗄️ Database & Schema Constraints
- **Telegram IDs**: Always map `telegram_user_id` columns to `BigInteger` (instead of standard `Integer`). Telegram user IDs are 64-bit numbers and will overflow 32-bit integer columns in databases like PostgreSQL.
- **Serverless PostgreSQL Connection Pooling**: When configuring `create_engine` for serverless databases like Neon, always set `pool_pre_ping=True` and `pool_recycle=280` to automatically test and recycle idle connections before queries are executed.

---

## 🎨 Style and Documentation Guidelines
- Preserve all existing docstrings, validators, and calibrations unless they are the direct target of the change.
- Make clean edits. When replacing code blocks, ensure whitespace and indentation match Python's PEP 8 standards.
- Always check that any modifications compile and run cleanly with `python app.py` before completing the task.
- Update `claude.md` and `agents.md` whenever adding new handlers, environment configs, database changes, or user commands.

