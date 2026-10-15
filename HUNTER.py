import os
import re
import json
import asyncio
import logging
import sqlite3
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiohttp import web

BOT_TOKEN = "8604267580:AAGG638igKH16YmBd2iDhOSi1qAGdAUdKHs"

MAIN_CHAT_ID = -1003712655239
LOGS_THREAD_ID = 1004        # Топик "Квесты/казна"
MEMBERS_THREAD_ID = 134      # Ветка для добавления в состав

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "hunter_family.db")

RANKS = {
    1: "[1] ROOKIE",
    2: "[2] TRACKER",
    3: "[3] SCOUT",
    4: "[4] SHOOTER",
    5: "[5] CHASER",
    6: "[6] PREDATOR",
    7: "[7] SNIPER",
    8: "[8] VETERAN",
    9: "[9] MASTER",
    10: "[10] HEADHUNTER"
}

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS members (
    user_id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT DEFAULT 'без_юзернейма',
    nickname TEXT UNIQUE,
    rank INTEGER DEFAULT 1,
    quests_count INTEGER DEFAULT 0,
    total_bank INTEGER DEFAULT 0,
    joined_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")
conn.commit()


def normalize_nick(nickname: str) -> str:
    """Очищает ник от HTML тэгов, спецсимволов u1fc32 и форматирования"""
    if not nickname:
        return ""
    nickname = re.sub(r"<[^>]+>", "", nickname)
    nickname = re.sub(r"\\?u[0-9a-fA-F]{4}", "", nickname)
    nickname = re.sub(r"^\[[^\]]*\]\s*", "", nickname)
    nickname = re.sub(r"\[\d+\]$", "", nickname.strip())
    
    clean = nickname.strip().replace(" ", "_")
    return clean if len(clean) > 1 else ""


# ----------------------------------------------------------------------
# API ОБРАБОТЧИК ДЛЯ LUA-СКРИПТА
# ----------------------------------------------------------------------
async def handle_lua_event(request):
    try:
        raw_body = await request.read()
        try:
            text_body = raw_body.decode("utf-8")
        except UnicodeDecodeError:
            text_body = raw_body.decode("cp1251", errors="ignore")

        data = json.loads(text_body)
        print(f"\n📥 [LUA DATA]: {data}")

        event_type = data.get("type")
        raw_nick = data.get("nickname")
        value = data.get("value", 0)

        if not raw_nick:
            return web.json_response({"status": "error", "message": "No nickname"}, status=400)

        nickname = normalize_nick(raw_nick)

        if not nickname or nickname.lower() == "u1fc32":
            return web.json_response({"status": "error", "message": "Invalid nickname"}, status=400)

        cursor.execute("SELECT nickname FROM members WHERE LOWER(REPLACE(nickname, ' ', '_')) = LOWER(?)", (nickname,))
        user_in_db = cursor.fetchone()

        if not user_in_db:
            print(f"🆕 Игрок {nickname} отсутствует в базе. Автоматическое создание...")
            cursor.execute("INSERT INTO members (nickname) VALUES (?)", (nickname,))
            conn.commit()

        # 1. ВЫПОЛНЕНИЕ КВЕСТА
        if event_type == "quest":
            cursor.execute("""
                UPDATE members 
                SET quests_count = quests_count + 1 
                WHERE LOWER(REPLACE(nickname, ' ', '_')) = LOWER(?)
            """, (nickname,))
            conn.commit()

            msg_text = f"🎯 <b><code>{nickname}</code></b> выполнил семейное задание!"
            try:
                await bot.send_message(
                    chat_id=MAIN_CHAT_ID,
                    text=msg_text,
                    parse_mode="HTML",
                    message_thread_id=LOGS_THREAD_ID
                )
                print(f"✅ [TG OK] Quest sent: {nickname}")
            except Exception as e:
                print(f"❌ [QUEST ERROR]: {e}")

        # 2. ПОПОЛНЕНИЕ СКЛАДА / КАЗНЫ / МОНЕТ
        elif event_type in ["deposit", "type_deposit", "coins"]:
            cursor.execute("""
                UPDATE members 
                SET total_bank = total_bank + ? 
                WHERE LOWER(REPLACE(nickname, ' ', '_')) = LOWER(?)
            """, (value, nickname))
            conn.commit()

            formatted_val = f"{value:,}".replace(",", ".")
            if event_type == "coins":
                msg_text = f"🪙 <b><code>{nickname}</code></b> пополнил склад монетами: <b>{formatted_val} шт.</b>"
            else:
                msg_text = f"💰 <b><code>{nickname}</code></b> пополнил склад семьи на <b>${formatted_val}</b>"

            try:
                await bot.send_message(
                    chat_id=MAIN_CHAT_ID,
                    text=msg_text,
                    parse_mode="HTML",
                    message_thread_id=LOGS_THREAD_ID
                )
                print(f"✅ [TG OK] Deposit/Coins sent: {nickname} -> {value}")
            except Exception as e:
                print(f"❌ [DEPOSIT ERROR]: {e}")

        return web.json_response({"status": "ok"})
    except Exception as e:
        print(f"💥 Ошибка сервера: {e}")
        return web.json_response({"status": "error", "message": str(e)}, status=500)


async def start_web_server():
    app = web.Application()
    app.router.add_post("/lua_event", handle_lua_event)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 8080)
    await site.start()
    print("🌐 API для Lua-скрипта запущено на порту 8080!")


# ----------------------------------------------------------------------
# АВТОМАТИЧЕСКОЕ ДОБАВЛЕНИЕ ИЗ ВЕТКИ 134 (З ТАЙМЕРОМ ВИДАЛЕННЯ ЧЕРЕЗ 10 СЕК)
# ----------------------------------------------------------------------
@dp.message(F.message_thread_id == MEMBERS_THREAD_ID)
async def auto_add_from_thread(message: types.Message):
    if not message.text or message.text.startswith("/"):
        return

    nickname = normalize_nick(message.text)
    if not nickname:
        return

    username = message.from_user.username or "без_юзернейма"
    username = username.replace("@", "")

    cursor.execute("SELECT nickname FROM members WHERE LOWER(REPLACE(nickname, ' ', '_')) = LOWER(?)", (nickname,))
    row = cursor.fetchone()

    if row:
        cursor.execute("UPDATE members SET username = ? WHERE LOWER(REPLACE(nickname, ' ', '_')) = LOWER(?)", (username, nickname))
        conn.commit()
        sent_msg = await message.reply(f"✅ Обновлена привязка Telegram для участника <b><code>{row[0]}</code></b> (@{username})", parse_mode="HTML")
    else:
        cursor.execute("INSERT INTO members (nickname, username) VALUES (?, ?)", (nickname, username))
        conn.commit()
        sent_msg = await message.reply(f"🎉 <b><code>{nickname}</code></b> успешно занесён в состав семьи HUNTER!", parse_mode="HTML")

    # ТАЙМЕР НА УДАЛЕНИЕ ЧЕРЕЗ 10 СЕКУНД
    await asyncio.sleep(10)
    try:
        await sent_msg.delete()
        await message.delete()  # Видаляє також оригінальне повідомлення гравця, щоб гілка була чистою
    except Exception as e:
        pass


# ----------------------------------------------------------------------
# КОМАНДИ БОТА
# ----------------------------------------------------------------------

@dp.message(Command("getid"))
async def get_id(message: types.Message):
    await message.reply(
        f"🆔 <b>chat_id:</b> <code>{message.chat.id}</code>\n"
        f"🧵 <b>thread_id:</b> <code>{message.message_thread_id}</code>",
        parse_mode="HTML"
    )


@dp.message(Command("ping"))
async def ping(message: types.Message):
    await message.reply("🏓 Pong! Бот живой.", parse_mode="HTML")


@dp.message(Command("help"))
async def show_help(message: types.Message):
    text = (
        "📜 <b>Команды бота HUNTER:</b>\n\n"
        "👥 /members — Весь состав семьи\n"
        "📊 /stats <code>[Ник]</code> — Личная карточка игрока\n"
        "🏆 /top — Топ-5 участников по деньгам и квестам\n"
        "🧹 /cleandups — Очистить базу от всех дубликатов\n"
        "🆔 /getid — Показать ID чата и топика\n"
        "🏓 /ping — Проверка что бот работает\n\n"
        "<b>Админ-команды:</b>\n"
        "👑 /setrank <code>[Ник] [1-10]</code> — Изменить ранг участнику\n"
        "💵 /addbank <code>[Ник] [Сумма]</code> — Добавить денег участнику\n"
        "📉 /delbank <code>[Ник] [Сумма]</code> — Списать деньги у участника\n"
        "🎯 /addquest <code>[Ник] [Кол-во]</code> — Добавить квесты участнику\n"
        "❌ /delquest <code>[Ник] [Кол-во]</code> — Списать квесты у участника\n"
        "🗑️ /del <code>[Ник]</code> или /delmember — Удалить участника из базы"
    )
    await message.reply(text, parse_mode="HTML")


@dp.message(Command("cleandups"))
async def clean_duplicates(message: types.Message):
    cursor.execute("SELECT user_id, nickname, rank, quests_count, total_bank, username FROM members")
    rows = cursor.fetchall()

    merged = {}
    for user_id, nick, rank, quests, bank, uname in rows:
        clean_key = normalize_nick(nick).lower()
        clean_name = normalize_nick(nick)

        if not clean_key or clean_key == "u1fc32":
            continue

        if clean_key not in merged:
            merged[clean_key] = {
                "nickname": clean_name,
                "rank": rank,
                "quests": quests,
                "bank": bank,
                "username": uname
            }
        else:
            merged[clean_key]["rank"] = max(merged[clean_key]["rank"], rank)
            merged[clean_key]["quests"] += quests
            merged[clean_key]["bank"] += bank
            if uname and uname != "без_юзернейма":
                merged[clean_key]["username"] = uname

    cursor.execute("DELETE FROM members")
    for data in merged.values():
        cursor.execute(
            "INSERT INTO members (nickname, rank, quests_count, total_bank, username) VALUES (?, ?, ?, ?, ?)",
            (data["nickname"], data["rank"], data["quests"], data["bank"], data["username"])
        )
    conn.commit()
    await message.reply("🧹 <b>База вычищена! Дубликаты объединены и лишние записи удалены.</b>", parse_mode="HTML")


@dp.message(Command("members"))
async def show_members(message: types.Message):
    cursor.execute("SELECT username, nickname, rank, quests_count, total_bank FROM members")
    rows = cursor.fetchall()

    if not rows:
        await message.reply("📂 <b>База данных семьи пуста.</b>", parse_mode="HTML")
        return

    grouped = {}
    for uname, nick, rank_num, quests, bank in rows:
        clean_nick = normalize_nick(nick)
        key = clean_nick.lower()
        if not key or key == "u1fc32":
            continue

        if key not in grouped:
            grouped[key] = {
                "username": uname,
                "nickname": clean_nick,
                "rank": rank_num,
                "quests": quests,
                "bank": bank
            }
        else:
            grouped[key]["rank"] = max(grouped[key]["rank"], rank_num)
            grouped[key]["quests"] += quests
            grouped[key]["bank"] += bank
            if uname and uname != "без_юзернейма":
                grouped[key]["username"] = uname

    sorted_members = sorted(grouped.values(), key=lambda x: (x["rank"], x["bank"]), reverse=True)

    total_count = len(sorted_members)
    text = f"⚔️ <b>СОСТАВ СЕМЬИ HUNTER ({total_count} чел.)</b>\n"
    text += "─────────────────────────\n"

    for idx, item in enumerate(sorted_members, start=1):
        rank_num = item["rank"] if item["rank"] in RANKS else 1
        rank_title = RANKS[rank_num]
        bank_str = f"{item['bank']:,}".replace(",", ".")
        username = item["username"]

        if username and username != "без_юзернейма":
            clean_username = username.replace("@", "")
            tg_info = f"(@{clean_username})"
        else:
            tg_info = ""

        text += f"<code>{idx:02d}.</code> 🎖️ <b>{rank_title}</b> | <b><code>{item['nickname']}</code></b> {tg_info}\n"
        text += f"      └ 🎯 Квесты: <code>{item['quests']}</code> | 💵 Внесено: <code>${bank_str}</code>\n"

    text += "─────────────────────────\n"
    text += "🔥 <i>HUNTER DOMINATION!</i>"

    await message.reply(text, parse_mode="HTML", disable_web_page_preview=True)


@dp.message(Command("stats", "profile"))
async def show_stats(message: types.Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply("⚠️ <b>Использование:</b> <code>/stats [Ник]</code>", parse_mode="HTML")
        return

    nickname = normalize_nick(args[1])
    cursor.execute("SELECT rank, quests_count, total_bank, joined_date, nickname FROM members")
    rows = cursor.fetchall()

    target_rows = [r for r in rows if normalize_nick(r[4]).lower() == nickname.lower()]

    if not target_rows:
        await message.reply(f"❌ Игрок <b><code>{nickname}</code></b> не найден в базе данных семьи!", parse_mode="HTML")
        return

    rank_num = max(r[0] for r in target_rows)
    quests = sum(r[1] for r in target_rows)
    bank = sum(r[2] for r in target_rows)
    joined = target_rows[0][3]

    rank_title = RANKS.get(rank_num, "[1] ROOKIE")
    bank_str = f"{bank:,}".replace(",", ".")

    text = (
        f"👤 <b>КАРТОЧКА УЧАСТНИКА: <code>{nickname}</code></b>\n"
        "─────────────────────────\n"
        f"🎖️ <b>Ранг:</b> {rank_title}\n"
        f"🎯 <b>Выполнено квестов:</b> <code>{quests}</code>\n"
        f"💵 <b>Внесено денег:</b> <code>${bank_str}</code>\n"
        f"📅 <b>В базе с:</b> <code>{joined}</code>\n"
        "─────────────────────────"
    )
    await message.reply(text, parse_mode="HTML")


@dp.message(Command("top"))
async def show_top(message: types.Message):
    cursor.execute("SELECT nickname, total_bank, quests_count FROM members")
    rows = cursor.fetchall()

    merged = {}
    for nick, bank, quests in rows:
        clean = normalize_nick(nick)
        key = clean.lower()
        if not key or key == "u1fc32":
            continue
        if key not in merged:
            merged[key] = {"nick": clean, "bank": bank, "quests": quests}
        else:
            merged[key]["bank"] += bank
            merged[key]["quests"] += quests

    top_bank = sorted(merged.values(), key=lambda x: x["bank"], reverse=True)[:5]
    top_quests = sorted(merged.values(), key=lambda x: x["quests"], reverse=True)[:5]

    text = "🏆 <b>ТОП-5 УЧАСТНИКОВ СЕМЬИ HUNTER</b> 🏆\n\n"

    text += "💵 <b>ПО ВНЕСЁННЫМ ДЕНЬГАМ:</b>\n"
    for idx, item in enumerate(top_bank, start=1):
        bank_str = f"{item['bank']:,}".replace(",", ".")
        text += f"{idx}. <b><code>{item['nick']}</code></b> — <code>${bank_str}</code>\n"

    text += "\n🎯 <b>ПО КВЕСТАМ:</b>\n"
    for idx, item in enumerate(top_quests, start=1):
        text += f"{idx}. <b><code>{item['nick']}</code></b> — <code>{item['quests']}</code> квестов\n"

    await message.reply(text, parse_mode="HTML")


@dp.message(Command("del", "delmember"))
async def del_member(message: types.Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply("⚠️ <b>Использование:</b> <code>/del [Ник]</code>", parse_mode="HTML")
        return

    nickname = normalize_nick(args[1])
    cursor.execute("SELECT user_id, nickname FROM members")
    rows = cursor.fetchall()
    
    deleted_count = 0
    for uid, nick in rows:
        if normalize_nick(nick).lower() == nickname.lower():
            cursor.execute("DELETE FROM members WHERE user_id = ?", (uid,))
            deleted_count += 1
            
    conn.commit()

    if deleted_count > 0:
        await message.reply(f"🗑 Игрок <b><code>{nickname}</code></b> (удалено записей: {deleted_count}) удален из базы данных!", parse_mode="HTML")
    else:
        await message.reply(f"❌ Игрок <b><code>{nickname}</code></b> не найден!", parse_mode="HTML")


@dp.message(Command("setrank"))
async def set_rank(message: types.Message):
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.reply("⚠️ <b>Использование:</b> <code>/setrank [Ник] [1-10]</code>", parse_mode="HTML")
        return

    nickname = normalize_nick(args[1])
    try:
        new_rank = int(args[2])
        if new_rank not in RANKS:
            raise ValueError()
    except ValueError:
        await message.reply("⚠️ Ранг должен быть числом от 1 до 10!", parse_mode="HTML")
        return

    cursor.execute("""
        UPDATE members 
        SET rank = ? 
        WHERE LOWER(REPLACE(nickname, ' ', '_')) = LOWER(?) 
           OR LOWER(REPLACE(nickname, ' ', '_')) LIKE LOWER(?)
    """, (new_rank, nickname, f"%{nickname}%"))
    conn.commit()

    await message.reply(f"✅ Игроку <b><code>{nickname}</code></b> установлен ранг <b>{RANKS[new_rank]}</b>!", parse_mode="HTML")


@dp.message(Command("addbank"))
async def add_bank(message: types.Message):
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.reply("⚠️ <b>Использование:</b> <code>/addbank [Ник] [Сумма]</code>", parse_mode="HTML")
        return

    nickname = normalize_nick(args[1])
    try:
        amount = int(args[2])
    except ValueError:
        await message.reply("⚠️ Сумма должна быть числом!", parse_mode="HTML")
        return

    cursor.execute("""
        UPDATE members 
        SET total_bank = total_bank + ? 
        WHERE LOWER(REPLACE(nickname, ' ', '_')) = LOWER(?)
    """, (amount, nickname))
    conn.commit()

    formatted_val = f"{amount:,}".replace(",", ".")
    await message.reply(f"✅ Баланс игрока <b><code>{nickname}</code></b> пополнен на <b>${formatted_val}</b>!", parse_mode="HTML")


@dp.message(Command("delbank"))
async def del_bank(message: types.Message):
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.reply("⚠️ <b>Использование:</b> <code>/delbank [Ник] [Сумма]</code>", parse_mode="HTML")
        return

    nickname = normalize_nick(args[1])
    try:
        amount = int(args[2])
    except ValueError:
        await message.reply("⚠️ Сумма должна быть числом!", parse_mode="HTML")
        return

    cursor.execute("""
        UPDATE members 
        SET total_bank = MAX(0, total_bank - ?) 
        WHERE LOWER(REPLACE(nickname, ' ', '_')) = LOWER(?)
    """, (amount, nickname))
    conn.commit()

    formatted_val = f"{amount:,}".replace(",", ".")
    await message.reply(f"📉 С баланса игрока <b><code>{nickname}</code></b> списано <b>${formatted_val}</b>!", parse_mode="HTML")


@dp.message(Command("addquest"))
async def add_quest(message: types.Message):
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.reply("⚠️ <b>Использование:</b> <code>/addquest [Ник] [Кол-во]</code>", parse_mode="HTML")
        return

    nickname = normalize_nick(args[1])
    try:
        count = int(args[2])
    except ValueError:
        await message.reply("⚠️ Количество должно быть числом!", parse_mode="HTML")
        return

    cursor.execute("""
        UPDATE members 
        SET quests_count = quests_count + ? 
        WHERE LOWER(REPLACE(nickname, ' ', '_')) = LOWER(?)
    """, (count, nickname))
    conn.commit()

    await message.reply(f"✅ Игроку <b><code>{nickname}</code></b> изменено кол-во квестов на <b>{count}</b>!", parse_mode="HTML")


@dp.message(Command("delquest"))
async def del_quest(message: types.Message):
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.reply("⚠️ <b>Использование:</b> <code>/delquest [Ник] [Кол-во]</code>", parse_mode="HTML")
        return

    nickname = normalize_nick(args[1])
    try:
        count = int(args[2])
    except ValueError:
        await message.reply("⚠️ Количество должно быть числом!", parse_mode="HTML")
        return

    cursor.execute("""
        UPDATE members 
        SET quests_count = MAX(0, quests_count - ?) 
        WHERE LOWER(REPLACE(nickname, ' ', '_')) = LOWER(?)
    """, (count, nickname))
    conn.commit()

    await message.reply(f"❌ У игрока <b><code>{nickname}</code></b> списано <b>{count}</b> квест(ов)!", parse_mode="HTML")


async def main():
    logging.basicConfig(level=logging.INFO)
    print("🚀 Бот HUNTER успешно запущен!")
    await start_web_server()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
