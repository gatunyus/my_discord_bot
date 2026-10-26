import discord
from discord import app_commands
from discord.ext import commands, tasks
import aiosqlite
import time
from database import DB_PATH


class EventsRemindersCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.check_reminders_loop.start()

    def cog_unload(self):
        self.check_reminders_loop.cancel()

    @tasks.loop(seconds=15)
    async def check_reminders_loop(self):
        now = time.time()
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT id, channel_id, user_id, message FROM reminders WHERE completed = 0 AND remind_at <= ?",
                (now,),
            ) as cur:
                due_list = await cur.fetchall()

            for r_id, channel_id, user_id, msg in due_list:
                await db.execute("UPDATE reminders SET completed = 1 WHERE id = ?", (r_id,))
                channel = self.bot.get_channel(channel_id)
                if channel:
                    try:
                        await channel.send(f"⏰ **แจ้งเตือนความจำ!** <@{user_id}> ฝากเตือนไว้ว่า:\n> 🔔 **{msg}**")
                    except Exception:
                        pass
            await db.commit()

    @check_reminders_loop.before_loop
    async def before_loops(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="remind", description="⏰ ตั้งเวลาเตือนความจำในกลุ่ม (เช่น เตือนไปเที่ยว, เตือนหารค่า Netflix, เตือนลงดัน)")
    @app_commands.describe(minutes="อีกกี่นาทีให้บอทแจ้งเตือน", message="เรื่องที่ต้องการให้เตือน")
    async def remind_cmd(self, interaction: discord.Interaction, minutes: int, message: str):
        if minutes <= 0:
            return await interaction.response.send_message("❌ จำนวนนาทีต้องมากกว่า 0 นะเพื่อน!", ephemeral=True)

        remind_at = time.time() + (minutes * 60)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO reminders (channel_id, user_id, message, remind_at) VALUES (?, ?, ?, ?)",
                (interaction.channel_id, interaction.user.id, message, remind_at),
            )
            await db.commit()

        await interaction.response.send_message(
            f"✅ รับทราบ! อีก **`{minutes}` นาที** เดี๋ยวบอทมาแท็กเตือนเรื่อง: **\"{message}\"**"
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(EventsRemindersCog(bot))
