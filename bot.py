import asyncio
import logging
import random
from datetime import datetime
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, CallbackQuery
from aiogram.filters import CommandStart
from aiogram.filters.callback_data import CallbackData
from aiogram.utils.keyboard import InlineKeyboardBuilder
import aiosqlite

# --- НАСТРОЙКИ ---
BOT_TOKEN = "8841473039:AAG6SeUMhtgQtkgPP1zTO3bbMx0z7aUT3vE"  # Вставьте ваш токен
DB_NAME = "battleship.db"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# --- CALLBACK DATA ДЛЯ КНОПОК ПОЛЯ ---
class BoardCB(CallbackData, prefix="b"):
    act: str  # "p" (place), "a" (attack), "i" (ignore)
    r: int
    c: int


# --- ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ---
queue_10 = []
queue_50 = []
active_games = {}

# --- КЛАВИАТУРЫ ---
main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="В бой на 10 монет"), KeyboardButton(text="В бой на 50 монет")],
        [KeyboardButton(text="Ежедневный бонус")],
        [KeyboardButton(text="Рейтинг"), KeyboardButton(text="Профиль")]
    ], resize_keyboard=True
)

rating_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="По монетам"), KeyboardButton(text="По победам")],
        [KeyboardButton(text="В главное меню")]
    ], resize_keyboard=True
)

placement_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🎲 Случайная расстановка"), KeyboardButton(text="🔄 Очистить")],
        [KeyboardButton(text="✅ Готово")],
        [KeyboardButton(text="Сдаться")]
    ], resize_keyboard=True
)

game_menu = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="Сдаться")]], resize_keyboard=True
)


# --- ЛОГИКА ПОЛЯ (8x8) И ПРАВИЛ ---
def get_empty_board():
    return [[0] * 8 for _ in range(8)]


def get_board_ikb(board, action="p", hide_ships=False):
    builder = InlineKeyboardBuilder()
    for r in range(8):
        for c in range(8):
            val = board[r][c]
            text = "🟦"  # Вода
            if val == 1:
                text = "🟦" if hide_ships else "🟩"  # Корабль
            elif val == 2:
                text = "⚪️"  # Промах
            elif val == 3:
                text = "💥"  # Попадание
            elif val == 4:
                text = "❌"  # Уничтожен

            builder.button(text=text, callback_data=BoardCB(act=action, r=r, c=c))
    builder.adjust(8)
    return builder.as_markup()


def generate_random_board():
    """Случайная расстановка (1шт-3кл, 3шт-2кл, 4шт-1кл)"""
    while True:
        board = get_empty_board()
        # Обновленный список кораблей
        ships = [3, 2, 2, 2, 1, 1, 1, 1]
        success = True

        for size in ships:
            placed = False
            for _ in range(100):
                r, c = random.randint(0, 7), random.randint(0, 7)
                horiz = random.choice([True, False])
                valid = True
                for i in range(size):
                    nr, nc = r + (0 if horiz else i), c + (i if horiz else 0)
                    if nr >= 8 or nc >= 8: valid = False; break
                    for dr in [-1, 0, 1]:
                        for dc in [-1, 0, 1]:
                            if 0 <= nr + dr < 8 and 0 <= nc + dc < 8 and board[nr + dr][nc + dc] == 1:
                                valid = False;
                                break
                    if not valid: break

                if valid:
                    for i in range(size):
                        nr, nc = r + (0 if horiz else i), c + (i if horiz else 0)
                        board[nr][nc] = 1
                    placed = True
                    break

            if not placed:
                success = False
                break

        if success:
            return board


def validate_board(board):
    """Проверка правильности расстановки"""
    visited = set()
    ships = []
    for r in range(8):
        for c in range(8):
            if board[r][c] == 1 and (r, c) not in visited:
                comp = []
                q = [(r, c)]
                visited.add((r, c))
                while q:
                    curr_r, curr_c = q.pop(0)
                    comp.append((curr_r, curr_c))
                    for dr in [-1, 0, 1]:
                        for dc in [-1, 0, 1]:
                            if dr == 0 and dc == 0: continue
                            nr, nc = curr_r + dr, curr_c + dc
                            if 0 <= nr < 8 and 0 <= nc < 8 and board[nr][nc] == 1:
                                if dr != 0 and dc != 0: return False, "Корабли не могут касаться по диагонали!"
                                if (nr, nc) not in visited:
                                    visited.add((nr, nc))
                                    q.append((nr, nc))
                rows, cols = set([p[0] for p in comp]), set([p[1] for p in comp])
                if len(rows) > 1 and len(cols) > 1: return False, "Корабли должны быть прямыми!"
                ships.append(len(comp))

    # Проверка обновленного списка кораблей
    if sorted(ships) == [1, 1, 1, 1, 2, 2, 2, 3]: return True, "Ок"
    return False, "Неверное количество кораблей!\nНужно:\n🟩🟩🟩 — 1 шт\n🟩🟩 — 3 шт\n🟩 — 4 шт"


def check_sunk_and_surround(board, r, c):
    q, visited = [(r, c)], set([(r, c)])
    ship_cells = []

    while q:
        cr, cc = q.pop(0)
        ship_cells.append((cr, cc))
        for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            nr, nc = cr + dr, cc + dc
            if 0 <= nr < 8 and 0 <= nc < 8 and (nr, nc) not in visited:
                if board[nr][nc] == 1: return False
                if board[nr][nc] in [3, 4]:
                    visited.add((nr, nc))
                    q.append((nr, nc))

    for sr, sc in ship_cells: board[sr][sc] = 4
    for sr, sc in ship_cells:
        for dr in [-1, 0, 1]:
            for dc in [-1, 0, 1]:
                nr, nc = sr + dr, sc + dc
                if 0 <= nr < 8 and 0 <= nc < 8 and board[nr][nc] == 0:
                    board[nr][nc] = 2
    return True


# --- ИГРОВАЯ МЕХАНИКА И МАТЧМЕЙКИНГ ---
async def create_game(p1, p2, bet):
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET coins = coins - ? WHERE user_id IN (?, ?)", (bet, p1, p2))
            await db.commit()

        session = {
            "p1": p1, "p2": p2, "bet": bet, "turn": p1, "state": "PLACEMENT",
            "board_p1": get_empty_board(), "board_p2": get_empty_board(),
            "ready_p1": False, "ready_p2": False,
            "msg_my_p1": None, "msg_en_p1": None,
            "msg_my_p2": None, "msg_en_p2": None,
            "timeout_task": None
        }
        active_games[p1] = session
        active_games[p2] = session

        # Обновленное сообщение с инструкцией перед расстановкой
        instructions = (
            "⚔️ <b>Игра найдена!</b>\n\n"
            "Расставьте корабли на вашем поле. Они не должны касаться друг друга (даже по углам).\n\n"
            "<b>Вам нужно разместить:</b>\n"
            "🟩🟩🟩 (3 клетки) — 1 шт\n"
            "🟩🟩 (2 клетки) — 3 шт\n"
            "🟩 (1 клетка) — 4 шт"
        )

        for pid in (p1, p2):
            await bot.send_message(pid, instructions, reply_markup=placement_menu, parse_mode="HTML")
            b_key = "board_p1" if pid == p1 else "board_p2"
            msg = await bot.send_message(pid, "Ваше поле:", reply_markup=get_board_ikb(session[b_key], "p"))

            if pid == p1:
                session["msg_my_p1"] = msg.message_id
            else:
                session["msg_my_p2"] = msg.message_id

        session["timeout_task"] = asyncio.create_task(afk_timeout(session))
    except Exception as e:
        logging.error(f"Ошибка при создании игры: {e}")


async def afk_timeout(session):
    await asyncio.sleep(600)
    loser = session["turn"]
    winner = session["p2"] if loser == session["p1"] else session["p1"]
    await end_game(winner, loser, session["bet"], "Тайм-аут! Игрок бездействовал 10 минут.")


def reset_timeout(session):
    if session["timeout_task"]: session["timeout_task"].cancel()
    session["timeout_task"] = asyncio.create_task(afk_timeout(session))


async def end_game(winner_id, loser_id, bet, reason):
    session = active_games.get(winner_id)
    if session and session.get("timeout_task"): session["timeout_task"].cancel()

    win_amount = bet * 2
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET coins = coins + ?, wins = wins + 1 WHERE user_id = ?",
                         (win_amount, winner_id))
        await db.execute("UPDATE users SET losses = losses + 1 WHERE user_id = ?", (loser_id,))
        await db.commit()

    if winner_id in active_games: del active_games[winner_id]
    if loser_id in active_games: del active_games[loser_id]

    try:
        await bot.send_message(winner_id, f"🏆 Вы победили! {reason}\nПриз: {win_amount} 💰", reply_markup=main_menu)
    except:
        pass
    try:
        await bot.send_message(loser_id, f"💀 Вы проиграли! {reason}", reply_markup=main_menu)
    except:
        pass


# --- ОБРАБОТКА CALLBACK КНОПОК ---
@dp.callback_query(BoardCB.filter())
async def process_board_click(callback: CallbackQuery, callback_data: BoardCB):
    user_id = callback.from_user.id
    if user_id not in active_games: return await callback.answer("Игра не найдена или уже завершена!", show_alert=True)

    session = active_games[user_id]
    is_p1 = (user_id == session["p1"])
    my_board_key = "board_p1" if is_p1 else "board_p2"
    en_board_key = "board_p2" if is_p1 else "board_p1"

    act, r, c = callback_data.act, callback_data.r, callback_data.c

    if act == "i": return await callback.answer()

    if session["state"] == "PLACEMENT":
        if act != "p": return await callback.answer()
        if (is_p1 and session["ready_p1"]) or (not is_p1 and session["ready_p2"]):
            return await callback.answer("Вы уже подтвердили готовность!", show_alert=True)

        session[my_board_key][r][c] = 1 if session[my_board_key][r][c] == 0 else 0
        await callback.message.edit_reply_markup(reply_markup=get_board_ikb(session[my_board_key], "p"))
        return await callback.answer()

    if session["state"] == "PLAYING":
        if act != "a": return await callback.answer()
        if session["turn"] != user_id:
            return await callback.answer("Сейчас ход соперника!", show_alert=True)

        target_board = session[en_board_key]
        if target_board[r][c] in [2, 3, 4]:
            return await callback.answer("Вы уже стреляли сюда!")

        reset_timeout(session)
        en_id = session["p2"] if is_p1 else session["p1"]

        if target_board[r][c] == 0:
            target_board[r][c] = 2
            session["turn"] = en_id
            await callback.answer("Мимо! 💦")
            await bot.send_message(user_id, "Мимо! Ход переходит к сопернику ⏱")
            await bot.send_message(en_id, "Соперник промахнулся. Ваш ход! 🎯")
        else:
            target_board[r][c] = 3
            if check_sunk_and_surround(target_board, r, c):
                await callback.answer("Убил! 💥💀")
                await bot.send_message(user_id, "Корабль уничтожен! Ваш ход 🎯")
            else:
                await callback.answer("Ранил! 💥")
                await bot.send_message(user_id, "Ранил! Вы стреляете еще раз 🎯")

        await bot.edit_message_reply_markup(chat_id=user_id, message_id=session["msg_en_p1" if is_p1 else "msg_en_p2"],
                                            reply_markup=get_board_ikb(target_board, "a", True))
        await bot.edit_message_reply_markup(chat_id=en_id, message_id=session["msg_my_p2" if is_p1 else "msg_my_p1"],
                                            reply_markup=get_board_ikb(target_board, "i", False))

        if not any(1 in row for row in target_board):
            await end_game(user_id, en_id, session["bet"], "Все корабли противника потоплены!")


# --- ОБРАБОТЧИКИ ТЕКСТА РАССТАНОВКИ ---
@dp.message(F.text.in_({"🎲 Случайная расстановка", "🔄 Очистить", "✅ Готово"}))
async def placement_actions(message: Message):
    user_id = message.from_user.id
    if user_id not in active_games: return
    session = active_games[user_id]
    if session["state"] != "PLACEMENT": return

    is_p1 = (user_id == session["p1"])
    b_key = "board_p1" if is_p1 else "board_p2"
    msg_id = session["msg_my_p1" if is_p1 else "msg_my_p2"]

    if message.text == "🎲 Случайная расстановка":
        session[b_key] = generate_random_board()
        await bot.edit_message_reply_markup(chat_id=user_id, message_id=msg_id,
                                            reply_markup=get_board_ikb(session[b_key], "p"))
        await message.answer("Случайная расстановка применена!")

    elif message.text == "🔄 Очистить":
        session[b_key] = get_empty_board()
        await bot.edit_message_reply_markup(chat_id=user_id, message_id=msg_id,
                                            reply_markup=get_board_ikb(session[b_key], "p"))
        await message.answer("Поле очищено.")

    elif message.text == "✅ Готово":
        is_valid, err_msg = validate_board(session[b_key])
        if not is_valid: return await message.answer(f"❌ Ошибка:\n{err_msg}")

        if is_p1:
            session["ready_p1"] = True
        else:
            session["ready_p2"] = True
        await message.answer("✅ Готовность подтверждена! Ожидаем соперника...")

        if session["ready_p1"] and session["ready_p2"]:
            session["state"] = "PLAYING"
            for p in (session["p1"], session["p2"]):
                p_is_p1 = (p == session["p1"])
                my_board = session["board_p1" if p_is_p1 else "board_p2"]
                en_board = session["board_p2" if p_is_p1 else "board_p1"]

                await bot.send_message(p, "🔥 БОЙ НАЧАЛСЯ!", reply_markup=game_menu)
                msg1 = await bot.send_message(p, "Ваше поле:", reply_markup=get_board_ikb(my_board, "i", False))
                msg2 = await bot.send_message(p, "Поле противника (Стреляйте сюда):",
                                              reply_markup=get_board_ikb(en_board, "a", True))

                if p_is_p1:
                    session["msg_my_p1"] = msg1.message_id
                    session["msg_en_p1"] = msg2.message_id
                else:
                    session["msg_my_p2"] = msg1.message_id
                    session["msg_en_p2"] = msg2.message_id

            await bot.send_message(session["turn"], "🎯 Вы ходите первым!")


# --- МЕНЮ И БД ---
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY, username TEXT, coins INTEGER DEFAULT 100, 
                wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0, last_bonus TIMESTAMP)''')
        await db.commit()


async def get_user(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cur:
            return await cur.fetchone()


@dp.message(CommandStart())
async def start_cmd(message: Message):
    user = await get_user(message.from_user.id)
    if not user:
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("INSERT INTO users (user_id, username) VALUES (?, ?)",
                             (message.from_user.id, message.from_user.first_name))
            await db.commit()
        await message.answer("Добро пожаловать в Морской Бой! Вам начислено 100 монет.", reply_markup=main_menu)
    else:
        await message.answer("Главное меню ⚓️", reply_markup=main_menu)


@dp.message(F.text == "Ежедневный бонус")
async def get_bonus(message: Message):
    user = await get_user(message.from_user.id)
    now = datetime.now()
    if user[5] and now.date() <= datetime.fromisoformat(user[5]).date():
        return await message.answer("Бонус уже получен! Приходите завтра 🎁")
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET coins = coins + 50, last_bonus = ? WHERE user_id = ?",
                         (now.isoformat(), message.from_user.id))
        await db.commit()
    await message.answer("🎁 Вы получили 50 монет!")


@dp.message(F.text == "Профиль")
async def show_profile(message: Message):
    u = await get_user(message.from_user.id)
    if u: await message.answer(f"👤 <b>{u[1]}</b>\n💰 Монеты: {u[2]}\n🏆 Победы: {u[3]}\n💀 Поражения: {u[4]}",
                               parse_mode="HTML")


@dp.message(F.text == "Рейтинг")
async def show_rating(m: Message): await m.answer("Выберите:", reply_markup=rating_menu)


@dp.message(F.text.in_({"По монетам", "По победам"}))
async def show_top(m: Message):
    s = "coins" if m.text == "По монетам" else "wins"
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(f"SELECT username, {s} FROM users ORDER BY {s} DESC LIMIT 10") as cur:
            top = await cur.fetchall()
    text = "💰 ТОП Монет:\n" if s == "coins" else "🏆 ТОП Побед:\n"
    for i, (usr, val) in enumerate(top, 1): text += f"{i}. {usr} — {val}\n"
    await m.answer(text)


@dp.message(F.text.in_({"В бой на 10 монет", "В бой на 50 монет"}))
async def search_game(m: Message):
    uid = m.from_user.id
    if uid in active_games: return await m.answer("Вы уже в игре!")
    bet = 10 if "10" in m.text else 50
    user = await get_user(uid)
    if user[2] < bet: return await m.answer("Недостаточно монет!")

    q = queue_10 if bet == 10 else queue_50
    if uid in q: return await m.answer("Вы уже ищете игру.")

    if q:
        op_id = q.pop(0)
        await create_game(uid, op_id, bet)
    else:
        q.append(uid)
        await m.answer(f"🔍 Поиск игры ({bet} монет)...",
                       reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Отменить поиск")]],
                                                        resize_keyboard=True))


@dp.message(F.text == "Отменить поиск")
async def cancel_search(m: Message):
    uid = m.from_user.id
    if uid in queue_10: queue_10.remove(uid)
    if uid in queue_50: queue_50.remove(uid)
    await m.answer("Поиск отменен.", reply_markup=main_menu)


@dp.message(F.text == "Сдаться")
async def surrender(m: Message):
    uid = m.from_user.id
    if uid not in active_games: return await m.answer("Вы не в игре.", reply_markup=main_menu)
    session = active_games[uid]
    winner = session["p2"] if uid == session["p1"] else session["p1"]
    await end_game(winner, uid, session["bet"], "Соперник сдался.")


@dp.message(F.text == "В главное меню")
async def back_main(m: Message): await m.answer("Главное меню.", reply_markup=main_menu)


# --- ЗАПУСК БОТА ---
async def main():
    await init_db()
    logging.basicConfig(level=logging.INFO)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
