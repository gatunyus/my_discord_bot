import os
import certifi
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

import discord
from discord import app_commands
from discord.ext import commands
import asyncio
from dotenv import load_dotenv
from database import init_db, set_guild_config
from cogs.activity_stats import build_guild_stats_view, get_day_night_emoji

load_dotenv()

COGS = [
    "cogs.hall_of_fame",
    "cogs.gemini_friend",
    "cogs.voice_magic",
    "cogs.events_reminders",
    "cogs.friend_court",
    "cogs.activity_stats",
]


class FriendSuperBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(command_prefix="!", intents=intents)
        self._initial_synced = False

    async def setup_hook(self):
        await init_db()
        for cog in COGS:
            await self.load_extension(cog)
        print("📦 โหลดโมดูลฟีเจอร์ทั้ง 6 หมวดเรียบร้อยแล้ว!")

    async def sync_to_guild(self, guild: discord.Guild) -> int:
        self.tree.clear_commands(guild=guild)
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        return len(synced)

    async def on_ready(self):
        if not self._initial_synced:
            self._initial_synced = True
            try:
                # ล้าง Global Commands เดิมออกเพื่อไม่ให้คำสั่งซ้ำ แล้วซิงค์แบบเจาะจงรายเซิร์ฟเวอร์ (Guild Sync)
                # ทำให้คำสั่ง / ใหม่ทั้งหมดเด้งขึ้นในเซิร์ฟเวอร์ทันทีภายใน 1 วินาที ไม่ต้องรอ Discord แคช 1 ชั่วโมง!
                if self.application_id:
                    await self.http.bulk_upsert_global_commands(self.application_id, payload=[])
                for guild in self.guilds:
                    count = await self.sync_to_guild(guild)
                    print(f"✅ ซิงค์คำสั่ง Slash Commands เข้าเซิร์ฟ '{guild.name}' ทันทีจำนวน {count} คำสั่งสำเร็จ!")
            except Exception as e:
                print(f"⚠️ ซิงค์คำสั่งไม่สำเร็จ: {e}")

        print(f"🚀 บอท '{self.user}' ออนไลน์พร้อมปั่นดิสกลุ่มเพื่อนแล้ว!")

    async def on_guild_join(self, guild: discord.Guild):
        try:
            count = await self.sync_to_guild(guild)
            print(f"✅ บอทเข้าเซิร์ฟใหม่ '{guild.name}' และซิงค์คำสั่งทันที {count} คำสั่ง!")
        except Exception as e:
            print(f"⚠️ ซิงค์คำสั่งเข้าเซิร์ฟใหม่ไม่สำเร็จ: {e}")


bot = FriendSuperBot()


@bot.command(name="sync")
async def manual_sync(ctx: commands.Context):
    """พิมพ์ !sync ในแชทเพื่อบังคับซิงค์คำสั่ง / ใหม่เข้าเซิร์ฟเวอร์ทันที"""
    if not ctx.guild:
        return
    count = await bot.sync_to_guild(ctx.guild)
    await ctx.reply(f"✅ บังคับซิงค์คำสั่ง `/` เข้าเซิร์ฟเวอร์นี้ใหม่เรียบร้อยแล้ว (**{count} คำสั่ง**)! ลองพิมพ์ `/` ดูได้เลย!")


@bot.tree.command(
    name="upgrade_server",
    description="✨ เสกหรืออัปเดตห้องฟีเจอร์ทั้งหมด (Statistics, Hall of Fame, ห้องคุยอัตโนมัติ) ในคลิกเดียว!",
)
@app_commands.checks.has_permissions(administrator=True)
async def upgrade_server(interaction: discord.Interaction):
    await interaction.response.defer()
    guild = interaction.guild

    # ลบยศวันเกิดเก่า (ถ้ายกเลิกระบบวันเกิดแล้วเคยสร้างค้างไว้)
    old_bday_role = discord.utils.get(guild.roles, name="🎂 เจ้าของวันเกิดวันนี้")
    if old_bday_role:
        try:
            await old_bday_role.delete(reason="ยกเลิกระบบวันเกิด")
        except Exception:
            pass

    # ลบห้องเก่าที่ไม่ใช้แล้ว ถ้าเคยสร้างไว้
    for old_name in (
        "🚨・เรียกรวมพล-ตี้เกม",
        "🎰・คาสิโน-ปั่นเหรียญ",
        "⚖️・ศาลเตี้ย-และสถิติ",
        "⚖️・ศาลเตี้ยประจำกลุ่ม",
    ):
        old_ch = discord.utils.get(guild.text_channels, name=old_name)
        if old_ch:
            try:
                await old_ch.delete(reason="ปรับโครงสร้างห้องตาม Layout ล่าสุด")
            except Exception:
                pass

    # 1. หมวดหมู่ Statistics (☀️ Statistics / 🌙 Statistics) -> 📊・stats และ 🌟・hall-of-fame
    dn_emoji = get_day_night_emoji()
    stats_cat_name = f"{dn_emoji} Statistics"
    stats_category = None
    for cat in guild.categories:
        if "Statistics" in cat.name or "สถิติ" in cat.name:
            stats_category = cat
            break
    if not stats_category:
        stats_category = await guild.create_category(stats_cat_name)
    elif stats_category.name != stats_cat_name:
        try:
            await stats_category.edit(name=stats_cat_name)
        except Exception:
            pass

    stats_ch_name = "📊・stats"
    stats_channel = None
    for ch in guild.text_channels:
        if ch.name in ("📊・stats", "stats") or "・stats" in ch.name or "สถิติชาวแก๊ง" in ch.name:
            stats_channel = ch
            break

    if not stats_channel:
        stats_channel = await guild.create_text_channel(
            stats_ch_name,
            category=stats_category,
            topic="กระดานสถิติชาวแก๊ง: ใครเล่นเกมไหนบ้าง อัปเดตอัตโนมัติทุก 30 นาที!",
        )
    else:
        try:
            await stats_channel.edit(name=stats_ch_name, category=stats_category)
        except Exception:
            pass

    hof_channel = discord.utils.get(guild.text_channels, name="🌟・hall-of-fame")
    if not hof_channel:
        hof_channel = await guild.create_text_channel(
            "🌟・hall-of-fame",
            category=stats_category,
            topic="รวมประวัติศาสตร์หน้ามืดและข้อความสุดปั่น (กด ⭐ หรือ 💀 ให้ครบ 3 คนเพื่อส่งข้อความเข้าห้องนี้!)",
        )

    # 2. หมวดหมู่ ✨ Command -> ➕ สร้างห้องคุย
    cmd_category = None
    for cat in guild.categories:
        if cat.name == "✨ Command" or "โซนพิเศษชาวแก๊ง" in cat.name:
            cmd_category = cat
            break
    if not cmd_category:
        cmd_category = await guild.create_category("✨ Command")

    jtc_channel = discord.utils.get(guild.voice_channels, name="➕ สร้างห้องคุย")
    if not jtc_channel:
        jtc_channel = await guild.create_voice_channel("➕ สร้างห้องคุย", category=cmd_category)

    board_view = await build_guild_stats_view(guild)
    sent_board = await stats_channel.send(view=board_view)
    stats_msg_id = sent_board.id

    await set_guild_config(
        guild.id,
        starboard_channel_id=hof_channel.id,
        jtc_channel_id=jtc_channel.id,
        stats_channel_id=stats_channel.id,
        stats_message_id=stats_msg_id,
    )

    summary = discord.Embed(
        title="🎉 อัปเดตโครงสร้างเซิร์ฟเวอร์สำเร็จ 100%!",
        description=(
            f"1️⃣ หมวด **{stats_category.name}** -> {stats_channel.mention} และ {hof_channel.mention}\n"
            f"2️⃣ หมวด **{cmd_category.name}** -> {jtc_channel.mention} (สร้างห้องคุยอัตโนมัติ)\n"
            f"3️⃣ **คำสั่งศาลเตี้ย (`/sue`, `/punish`, `/pardon`, `/criminal_record`)** — เรียกใช้ได้จากทุกห้องแชท\n"
            f"4️⃣ **คำสั่งเสียง (`/play_sound`, `/soundboard`, `/set_entrance_sound`)** — พร้อมใช้งาน!"
        ),
        color=discord.Color.green(),
    )
    await interaction.followup.send(embed=summary)


if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token or token == "ใส่_TOKEN_ของบอทตรงนี้":
        print("❌ กรุณาก๊อปปี้ไฟล์ .env.example เป็น .env แล้วใส่ DISCORD_TOKEN ก่อนรันบอทนะครับ!")
    else:
        bot.run(token)
