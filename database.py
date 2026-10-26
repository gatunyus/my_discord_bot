import aiosqlite
import os
import shutil

_LOCAL_SEED_DB = os.path.join(os.path.dirname(__file__), "bot_data.db")
DB_PATH = os.getenv("DB_PATH", _LOCAL_SEED_DB)
if os.path.dirname(DB_PATH):
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# ถ้าย้ายขึ้น Cloud (เช่น DB_PATH=/data/bot_data.db) ครั้งแรกแล้วไฟล์ใน Volume ยังไม่มี ให้ยกข้อมูลเดิมไปตั้งต้นให้อัตโนมัติ
if DB_PATH != _LOCAL_SEED_DB and not os.path.exists(DB_PATH) and os.path.exists(_LOCAL_SEED_DB):
    try:
        shutil.copy2(_LOCAL_SEED_DB, DB_PATH)
    except Exception:
        pass


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # 1. ตารางเก็บเงิน/แต้มของเพื่อน และเสียงเปิดตัว (Entrance Sound + ความดัง)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                coins INTEGER DEFAULT 500,
                birthday TEXT DEFAULT NULL,
                entrance_sound TEXT DEFAULT NULL,
                entrance_volume INTEGER DEFAULT 50
            )
        """)
        try:
            await db.execute("ALTER TABLE users ADD COLUMN entrance_volume INTEGER DEFAULT 50")
        except Exception:
            pass

        # 2. ตารางเก็บประวัติข้อความที่ขึ้น Hall of Fame (Starboard) ไปแล้ว จะได้ไม่ส่งซ้ำ
        await db.execute("""
            CREATE TABLE IF NOT EXISTS starboard_entries (
                message_id INTEGER PRIMARY KEY,
                star_message_id INTEGER
            )
        """)

        # 3. ตารางเก็บคำคมระดับตำนานของเพื่อน (/quote)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS quotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER,
                author_id INTEGER,
                author_name TEXT,
                content TEXT,
                added_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 4. ตารางเก็บการแจ้งเตือน (Reminders)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER,
                user_id INTEGER,
                message TEXT,
                remind_at REAL,
                completed INTEGER DEFAULT 0
            )
        """)

        # 5. ตารางตั้งค่าห้องประจำเซิร์ฟเวอร์
        await db.execute("""
            CREATE TABLE IF NOT EXISTS guild_config (
                guild_id INTEGER PRIMARY KEY,
                starboard_channel_id INTEGER DEFAULT NULL,
                jtc_channel_id INTEGER DEFAULT NULL,
                birthday_channel_id INTEGER DEFAULT NULL,
                birthday_role_id INTEGER DEFAULT NULL,
                court_channel_id INTEGER DEFAULT NULL,
                stats_channel_id INTEGER DEFAULT NULL,
                stats_message_id INTEGER DEFAULT NULL
            )
        """)
        for col in ("court_channel_id", "stats_channel_id", "stats_message_id"):
            try:
                await db.execute(f"ALTER TABLE guild_config ADD COLUMN {col} INTEGER DEFAULT NULL")
            except Exception:
                pass

        # 6. ตารางบันทึกคดีศาลเตี้ยประจำกลุ่ม (/sue)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS lawsuits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER,
                plaintiff_id INTEGER,
                defendant_id INTEGER,
                charge TEXT,
                punishment_type TEXT DEFAULT 'nickname',
                punishment_nick TEXT,
                punish_minutes INTEGER DEFAULT 3,
                verdict TEXT DEFAULT 'PENDING',
                guilty_votes INTEGER DEFAULT 0,
                innocent_votes INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        for col_sql in (
            "ALTER TABLE lawsuits ADD COLUMN punishment_type TEXT DEFAULT 'nickname'",
            "ALTER TABLE lawsuits ADD COLUMN punish_minutes INTEGER DEFAULT 3",
        ):
            try:
                await db.execute(col_sql)
            except Exception:
                pass

        # 7. ตารางเก็บสถิติการเล่นเกมของแต่ละคน (วินาที)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS game_stats (
                guild_id INTEGER,
                user_id INTEGER,
                game_name TEXT,
                seconds_played INTEGER DEFAULT 0,
                PRIMARY KEY (guild_id, user_id, game_name)
            )
        """)

        # 8. ตารางเก็บสถิติการสิงอยู่ในห้องพูดคุย (Voice) และจำนวนแชท
        await db.execute("""
            CREATE TABLE IF NOT EXISTS member_activity (
                guild_id INTEGER,
                user_id INTEGER,
                voice_seconds INTEGER DEFAULT 0,
                message_count INTEGER DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            )
        """)

        # 9. ตารางบันทึกสถานะการอวยพรวันเกิด (ป้องกันการแท็กซ้ำเมื่อรีสตาร์ทบอทในวันเดียวกัน)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS birthday_celebrations (
                guild_id INTEGER,
                user_id INTEGER,
                celebrated_date TEXT,
                PRIMARY KEY (guild_id, user_id, celebrated_date)
            )
        """)

        # 10. ตารางเก็บสถิติการฟังเพลง (Spotify / YouTube Music / Apple Music ฯลฯ)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS music_stats (
                guild_id INTEGER,
                user_id INTEGER,
                artist_name TEXT,
                song_title TEXT,
                seconds_listened INTEGER DEFAULT 0,
                PRIMARY KEY (guild_id, user_id, artist_name, song_title)
            )
        """)
        await db.commit()


async def get_coins(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT coins FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row is None:
                await db.execute("INSERT INTO users (user_id, coins) VALUES (?, 500)", (user_id,))
                await db.commit()
                return 500
            return row[0]


async def add_coins(user_id: int, amount: int) -> int:
    current = await get_coins(user_id)
    new_balance = max(0, current + amount)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET coins = ? WHERE user_id = ?", (new_balance, user_id))
        await db.commit()
    return new_balance


async def set_birthday(user_id: int, date_str: str):
    await get_coins(user_id)  # Ensure user exists
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET birthday = ? WHERE user_id = ?", (date_str, user_id))
        await db.commit()


async def get_all_birthdays():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, birthday FROM users WHERE birthday IS NOT NULL") as cursor:
            return await cursor.fetchall()


async def set_entrance_sound(user_id: int, sound_filename: str):
    await get_coins(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET entrance_sound = ? WHERE user_id = ?", (sound_filename, user_id))
        await db.commit()


async def get_entrance_sound(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT entrance_sound FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def set_entrance_volume(user_id: int, volume: int):
    await get_coins(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET entrance_volume = ? WHERE user_id = ?", (volume, user_id))
        await db.commit()


async def get_entrance_volume(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT entrance_volume FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if (row and row[0] is not None) else 50


async def set_guild_config(guild_id: int, **kwargs):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT guild_id FROM guild_config WHERE guild_id = ?", (guild_id,)) as cur:
            if not await cur.fetchone():
                await db.execute("INSERT INTO guild_config (guild_id) VALUES (?)", (guild_id,))
        for key, value in kwargs.items():
            await db.execute(f"UPDATE guild_config SET {key} = ? WHERE guild_id = ?", (value, guild_id))
        await db.commit()


async def get_guild_config(guild_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM guild_config WHERE guild_id = ?", (guild_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else {}
