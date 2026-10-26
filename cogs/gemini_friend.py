import discord
from discord import app_commands
from discord.ext import commands
import os
from google import genai
from google.genai import types


class GeminiFriendCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        api_key = os.getenv("GEMINI_API_KEY")
        self.client = genai.Client(api_key=api_key) if api_key and api_key != "ใส่_GEMINI_API_KEY_ตรงนี้" else None
        self.persona = os.getenv(
            "GROUP_PERSONA",
            "คุณคือบอทเพื่อนซี้ประจำดิสคอร์ดกลุ่มเพื่อนสนิท พูดจากวนๆ เป็นกันเอง มีอารมณ์ขัน ชอบแซวเพื่อนในกลุ่ม ตอบกระชับเข้าใจง่าย",
        )

    async def ask_gemini(self, prompt: str, system_instruction: str = None) -> str:
        if not self.client:
            return "⚠️ ยังไม่ได้ใส่ `GEMINI_API_KEY` ในไฟล์ `.env` เลยเพื่อน! ไปขอคีย์ฟรีที่ Google AI Studio มาใส่ก่อนนะ"
        try:
            response = await self.client.aio.models.generate_content(
                model="gemini-3.5-flash-lite",
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction or self.persona,
                    temperature=0.85,
                ),
            )
            return response.text or "คิดไม่ออกว่ะเพื่อน ลองถามใหม่ที"
        except Exception as e:
            return f"❌ สมอง AI รวนชั่วคราว: `{e}`"

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # เวลาเพื่อนแท็กชื่อบอท (@Bot) ในห้องไหนก็ได้ บอทจะตอบโต้เหมือนเพื่อนในกลุ่มทันที
        if message.author.bot or not self.bot.user:
            return

        if self.bot.user in message.mentions:
            async with message.channel.typing():
                clean_text = message.content.replace(f"<@{self.bot.user.id}>", "").strip()
                if not clean_text:
                    clean_text = "ทักทายเพื่อนแบบกวนๆ หน่อย"
                prompt = f"เพื่อนชื่อ '{message.author.display_name}' พูดกับคุณว่า: {clean_text}"
                reply = await self.ask_gemini(prompt)
                await message.reply(reply[:2000])

    @app_commands.command(name="ai", description="🧠 คุยกับ AI เพื่อนซี้ประจำกลุ่ม หรือให้ช่วยคิดอะไรกวนๆ")
    @app_commands.describe(question="สิ่งที่อยากถามหรือให้ AI ช่วย")
    async def ai_chat(self, interaction: discord.Interaction, question: str):
        await interaction.response.defer()
        prompt = f"เพื่อนชื่อ '{interaction.user.display_name}' ถามว่า: {question}"
        reply = await self.ask_gemini(prompt)
        await interaction.followup.send(f"**🗣️ {interaction.user.mention} ถาม:** {question}\n\n{reply[:1900]}")

    @app_commands.command(name="roast", description="🔥 สั่งให้ AI แซวหรือเผาเพื่อนในกลุ่มแบบฮาๆ")
    @app_commands.describe(member="เพื่อนที่อยากให้ AI แซว", topic="เรื่องที่จะให้แซว (เช่น ชอบเทตี้, นอนเช้า, เล่นเกมไก่)")
    async def roast_friend(self, interaction: discord.Interaction, member: discord.Member, topic: str = "นิสัยทั่วไปแบบกวนๆ"):
        await interaction.response.defer()
        prompt = (
            f"ช่วยแซวหรือเผาเพื่อนสนิทในกลุ่มที่ชื่อ '{member.display_name}' แบบขำๆ ฮาๆ ประสาเพื่อนสนิท "
            f"ในประเด็นเรื่อง: '{topic}' (ความยาว 2-3 ประโยคเด็ดๆ)"
        )
        reply = await self.ask_gemini(prompt)
        await interaction.followup.send(f"🔥 **ถึง {member.mention}:** {reply}")

    @app_commands.command(name="tldr", description="📝 สรุปแชทในห้องนี้ว่าเมื่อกี้เพื่อนคุยหรือเถียงเรื่องอะไรกัน!")
    @app_commands.describe(count="จำนวนข้อความย้อนหลังที่อยากให้สรุป (สูงสุด 150 ข้อความ)")
    async def summarize_chat(self, interaction: discord.Interaction, count: int = 50):
        await interaction.response.defer()
        count = max(10, min(count, 150))

        messages = []
        async for msg in interaction.channel.history(limit=count):
            if not msg.author.bot and msg.content:
                messages.append(f"{msg.author.display_name}: {msg.content}")

        if not messages:
            return await interaction.followup.send("❌ ไม่มีข้อความให้สรุปเลยเพื่อน!")

        messages.reverse()
        chat_log = "\n".join(messages)
        prompt = (
            f"นี่คือบทสนทนาย้อนหลังในกลุ่มเพื่อน:\n\n{chat_log}\n\n"
            f"ช่วยสรุปเป็นข้อๆ แบบฮาๆ เข้าใจง่ายหน่อยว่าพวกมันคุยหรือเถียงเรื่องอะไรกัน และใครเปิดประเด็นอะไรบ้าง"
        )
        reply = await self.ask_gemini(prompt)

        embed = discord.Embed(
            title=f"📝 สรุปดราม่า/แชทย้อนหลัง {len(messages)} ข้อความล่าสุด",
            description=reply[:4000],
            color=discord.Color.teal(),
        )
        embed.set_footer(text=f"ขอสรุปโดย {interaction.user.display_name}")
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(GeminiFriendCog(bot))
