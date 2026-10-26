import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
import os
import io
from PIL import Image, ImageDraw, ImageFont
from database import DB_PATH, get_guild_config

STAR_EMOJIS = {"⭐", "💀", "😂", "🔥", "🗿"}


def create_quote_image(text: str, author_name: str, avatar_bytes: bytes = None) -> io.BytesIO:
    """สร้างรูปภาพคำคมขาวดำสไตล์คนดัง"""
    width, height = 800, 420
    img = Image.new("RGB", (width, height), color=(18, 18, 22))
    draw = ImageDraw.Draw(img)

    # วาดกรอบตกแต่ง
    draw.rectangle([15, 15, width - 15, height - 15], outline=(90, 90, 100), width=2)

    # ถ้ามีรูปโปรไฟล์เพื่อน เอามาแปะฝั่งซ้ายแบบขาวดำเท่ๆ
    text_x = 60
    if avatar_bytes:
        try:
            avatar = Image.open(io.BytesIO(avatar_bytes)).convert("L").convert("RGB")
            avatar = avatar.resize((260, 260))
            img.paste(avatar, (50, 80))
            text_x = 340
        except Exception:
            pass

    # โหลดฟอนต์ (เช็คโฟลเดอร์ fonts/ ในโปรเจกต์ก่อน แล้วค่อยเช็คฟอนต์ระบบ macOS / Windows / Linux)
    local_fonts_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fonts")
    local_font_files = []
    if os.path.isdir(local_fonts_dir):
        local_font_files = [
            os.path.join(local_fonts_dir, f)
            for f in sorted(os.listdir(local_fonts_dir))
            if f.lower().endswith((".ttf", ".ttc", ".otf"))
        ]

    font_paths = local_font_files + [
        "/System/Library/Fonts/Thonburi.ttc",
        "/System/Library/Fonts/Supplemental/Tahoma.ttf",
        "C:/Windows/Fonts/tahoma.ttf",
        "/usr/share/fonts/truetype/tlwg/Garuda.ttf",
        "/usr/share/fonts/truetype/tlwg/Loma.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansThai-Regular.ttf",
    ]
    quote_font = ImageFont.load_default()
    author_font = ImageFont.load_default()
    for path in font_paths:
        if os.path.exists(path):
            try:
                quote_font = ImageFont.truetype(path, 28)
                author_font = ImageFont.truetype(path, 22)
                break
            except Exception:
                continue

    # ตัดบรรทัดข้อความอัตโนมัติ
    max_chars_per_line = 26 if text_x > 100 else 42
    words = text.split(" ")
    lines = []
    current_line = ""
    for word in words:
        if len(current_line + " " + word) <= max_chars_per_line:
            current_line = (current_line + " " + word).strip()
        else:
            if current_line:
                lines.append(current_line)
            current_line = word
    if current_line:
        lines.append(current_line)

    # กรณีภาษาไทยพิมพ์ติดกันไม่เว้นวรรค
    final_lines = []
    for line in lines:
        while len(line) > max_chars_per_line:
            final_lines.append(line[:max_chars_per_line])
            line = line[max_chars_per_line:]
        final_lines.append(line)

    wrapped_text = '"\n'.join([final_lines[0]]) if len(final_lines) == 1 else "\n".join(final_lines[:6])
    full_quote = f'"{wrapped_text}"'

    draw.text((text_x, 110), full_quote, fill=(240, 240, 245), font=quote_font)
    draw.text((text_x, 310), f"— {author_name}", fill=(170, 175, 190), font=author_font)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


class HallOfFameCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.threshold = int(os.getenv("STARBOARD_THRESHOLD", "3"))

        # เพิ่มเมนูคลิกขวาที่ข้อความเพื่อนเพื่อสร้างรูปคำคมทันที!
        self.quote_ctx_menu = app_commands.ContextMenu(
            name="📸 สร้างรูปคำคมจากข้อความนี้",
            callback=self.context_quote_message,
        )
        self.bot.tree.add_command(self.quote_ctx_menu)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if str(payload.emoji) not in STAR_EMOJIS or not payload.guild_id:
            return

        channel = self.bot.get_channel(payload.channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            return

        try:
            message = await channel.fetch_message(payload.message_id)
        except Exception:
            return

        # นับจำนวนรีแอคชันในกลุ่มอีโมจิตำนาน
        total_stars = 0
        for reaction in message.reactions:
            if str(reaction.emoji) in STAR_EMOJIS:
                total_stars = max(total_stars, reaction.count)

        if total_stars < self.threshold:
            return

        # เช็คว่าเคยส่งเข้า Hall of Fame ไปหรือยัง
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT star_message_id FROM starboard_entries WHERE message_id = ?", (message.id,)
            ) as cur:
                if await cur.fetchone():
                    return

        config = await get_guild_config(payload.guild_id)
        star_channel_id = config.get("starboard_channel_id")
        star_channel = self.bot.get_channel(star_channel_id) if star_channel_id else None

        # ถ้ายังไม่ได้ตั้งค่า ให้หาห้องชื่อ hall-of-fame อัตโนมัติ
        if not star_channel:
            guild = self.bot.get_guild(payload.guild_id)
            star_channel = discord.utils.get(guild.text_channels, name="🌟・hall-of-fame") or discord.utils.get(
                guild.text_channels, name="hall-of-fame"
            )

        if not star_channel:
            return

        embed = discord.Embed(
            description=message.content or "*[รูปภาพ / ไฟล์แนบระดับตำนาน]*",
            color=discord.Color.gold(),
            timestamp=message.created_at,
        )
        embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
        embed.add_field(name="ต้นทาง", value=f"[กดเพื่อวาร์ปไปดูข้อความจริง]({message.jump_url})", inline=False)
        if message.attachments:
            embed.set_image(url=message.attachments[0].url)
        embed.set_footer(text=f"{payload.emoji} {total_stars} รีแอคชัน | ห้อง #{channel.name}")

        sent_msg = await star_channel.send(content=f"🌟 **ประวัติศาสตร์หน้าใหม่ได้ถือกำเนิดขึ้น!**", embed=embed)

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO starboard_entries (message_id, star_message_id) VALUES (?, ?)",
                (message.id, sent_msg.id),
            )
            await db.commit()

    async def context_quote_message(self, interaction: discord.Interaction, message: discord.Message):
        await interaction.response.defer()
        if not message.content:
            return await interaction.followup.send("❌ ข้อความนี้ไม่มีตัวหนังสือให้ทำคำคมเลยเพื่อน!", ephemeral=True)

        # บันทึกลงคลังคำคมกลุ่มด้วย
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO quotes (guild_id, author_id, author_name, content, added_by) VALUES (?, ?, ?, ?, ?)",
                (interaction.guild_id, message.author.id, message.author.display_name, message.content, interaction.user.id),
            )
            await db.commit()

        avatar_bytes = await message.author.display_avatar.read()
        img_buf = create_quote_image(message.content, message.author.display_name, avatar_bytes)
        file = discord.File(fp=img_buf, filename="quote.png")
        await interaction.followup.send(
            content=f"📸 บันทึกคำคมของ **{message.author.mention}** ลงคลังประวัติศาสตร์เรียบร้อย!", file=file
        )

    @app_commands.command(name="quote_add", description="📸 สร้างรูปคำคมให้เพื่อนและบันทึกลงคลังประวัติศาสตร์กลุ่ม")
    @app_commands.describe(member="เพื่อนเจ้าของคำพูด", text="คำพูดสุดปั่นของเพื่อน")
    async def quote_add(self, interaction: discord.Interaction, member: discord.Member, text: str):
        await interaction.response.defer()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO quotes (guild_id, author_id, author_name, content, added_by) VALUES (?, ?, ?, ?, ?)",
                (interaction.guild_id, member.id, member.display_name, text, interaction.user.id),
            )
            await db.commit()

        avatar_bytes = await member.display_avatar.read()
        img_buf = create_quote_image(text, member.display_name, avatar_bytes)
        file = discord.File(fp=img_buf, filename="quote.png")
        await interaction.followup.send(content=f"✨ คำคมระดับตำนานโดย {member.mention}", file=file)

    @app_commands.command(name="quote_random", description="🎲 สุ่มคำคมในอดีตของเพื่อนในกลุ่มขึ้นมาประจาน")
    async def quote_random(self, interaction: discord.Interaction):
        await interaction.response.defer()
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT author_id, author_name, content, created_at FROM quotes WHERE guild_id = ? ORDER BY RANDOM() LIMIT 1",
                (interaction.guild_id,),
            ) as cur:
                row = await cur.fetchone()

        if not row:
            return await interaction.followup.send(
                "ยังไม่มีคำคมในคลังเลย! ลองใช้ `/quote_add` หรือคลิกขวาที่ข้อความเพื่อนแล้วกด `Apps -> สร้างรูปคำคม` ดูสิ!",
                ephemeral=True,
            )

        author_id, author_name, content, created_at = row

        # ดึงรูปโปรไฟล์ของเพื่อนเจ้าของคำคมมาทำภาพขาวดำเหมือนตอนสร้าง Quote
        avatar_bytes = None
        member = interaction.guild.get_member(author_id) if interaction.guild else None
        if not member and interaction.guild:
            try:
                member = await interaction.guild.fetch_member(author_id)
            except Exception:
                member = None
        if not member:
            try:
                member = await self.bot.fetch_user(author_id)
            except Exception:
                member = None

        if member and member.display_avatar:
            try:
                avatar_bytes = await member.display_avatar.read()
            except Exception:
                avatar_bytes = None

        img_buf = create_quote_image(content, author_name, avatar_bytes)
        file = discord.File(fp=img_buf, filename="quote_random.png")
        await interaction.followup.send(
            content=f"🎲 **สุ่มคำคมระดับตำนานประจำกลุ่ม!** — กล่าวโดย <@{author_id}> *(เมื่อ {created_at[:10]})*",
            file=file,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(HallOfFameCog(bot))
