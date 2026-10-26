import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
import asyncio
import time
from datetime import timedelta
from database import DB_PATH

PUNISHMENT_NAMES = {
    "nickname": "🏷️ เปลี่ยนชื่อประจาน",
    "mute": "🔇 ปิดไมค์ห้องคุย (Mute)",
    "timeout": "⏳ ปิดปากห้ามพิมพ์/พูด (Timeout)",
    "disconnect": "🦵 เตะออกห้องคุย (Disconnect)",
}


def format_punishment_detail(p_type: str, p_nick: str | None, p_mins: int) -> str:
    if p_type == "nickname":
        return f"🏷️ เปลี่ยนชื่อเล่นเป็น **`{p_nick}`**"
    elif p_type == "mute":
        base = f"🔇 ปิดไมค์ในห้องคุย (Server Mute) **`{p_mins} นาที`**"
    elif p_type == "timeout":
        base = f"⏳ Timeout ห้ามพิมพ์แชทและห้ามคุย **`{p_mins} นาที`**"
    elif p_type == "disconnect":
        base = "🦵 **เตะกระเด็นออกจากห้องพูดคุยทันที**"
    else:
        base = p_type

    if p_nick:
        base += f" + 🏷️ เปลี่ยนชื่อเป็น **`{p_nick}`**"
    return base


class TrialVoteView(discord.ui.View):
    def __init__(
        self,
        cog: "FriendCourtCog",
        guild: discord.Guild,
        channel: discord.abc.Messageable,
        case_id: int,
        plaintiff: discord.Member,
        defendant: discord.Member,
        charge: str,
        punishment_type: str,
        punishment_nick: str | None,
        punish_minutes: int,
        talk_seconds: int,
        ai_comment: str,
    ):
        super().__init__(timeout=max(3600, talk_seconds + 600))
        self.cog = cog
        self.bot = cog.bot
        self.guild = guild
        self.channel = channel
        self.case_id = case_id
        self.plaintiff = plaintiff
        self.defendant = defendant
        self.charge = charge
        self.punishment_type = punishment_type
        self.punishment_nick = punishment_nick
        self.punish_minutes = punish_minutes
        self.ai_comment = ai_comment

        now = time.time()
        self.started_at = now
        self.end_time = now + talk_seconds
        # บังคับให้มีเวลาคุยเถียงกันอย่างน้อยครึ่งหนึ่งของเวลาที่ตั้งไว้ ก่อนจะกดทุบค้อนก่อนเวลาได้
        self.min_talk_until = now + max(15, int(talk_seconds * 0.5))

        self.guilty_voters: dict[int, discord.Member] = {}
        self.innocent_voters: dict[int, discord.Member] = {}
        self.defense_statement: str | None = None
        self.witness_notes: list[tuple[str, str]] = []
        self.extensions_used = 0
        self.closed = False
        self.message: discord.Message | None = None
        self.timer_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    def sync_button_labels(self):
        self.vote_guilty.label = f"💀 มีความผิดจริง ({len(self.guilty_voters)})"
        self.vote_innocent.label = f"😇 บริสุทธิ์ ({len(self.innocent_voters)})"

    def build_embed(self, verdict_text: str = None) -> discord.Embed:
        color = discord.Color.dark_red() if not self.closed else discord.Color.gold()
        punish_desc = format_punishment_detail(
            self.punishment_type, self.punishment_nick, self.punish_minutes
        )

        time_line = (
            f"⏳ **เวลาคุยไต่สวน & โหวต:** ตัดสินคดีอัตโนมัติ <t:{int(self.end_time)}:R> *(เวลา <t:{int(self.end_time)}:T>)*"
            if not self.closed
            else "🔨 **สถานะคดี:** สิ้นสุดการไต่สวนและอ่านคำพิพากษาแล้ว"
        )

        embed = discord.Embed(
            title=f"⚖️ ศาลเตี้ยประจำกลุ่ม — คดีหมายเลข #{self.case_id}",
            description=(
                f"👨‍⚖️ **โจทก์ผู้ฟ้อง:** {self.plaintiff.mention}\n"
                f"⛓️ **จำเลย:** {self.defendant.mention}\n"
                f"📜 **ข้อหา:** **`{self.charge}`**\n"
                f"💥 **บทลงโทษหากแพ้คดี:** {punish_desc}\n\n"
                f"{time_line}"
            ),
            color=color,
        )

        if self.ai_comment:
            embed.add_field(
                name="🤖 ผู้พิพากษา AI กล่าวเปิดศาล",
                value=f"*{self.ai_comment}*",
                inline=False,
            )

        if self.defense_statement:
            embed.add_field(
                name=f"🗣️ คำให้การแก้ต่างของจำเลย ({self.defendant.display_name})",
                value=f"> \"{self.defense_statement}\"",
                inline=False,
            )
        elif not self.closed:
            embed.add_field(
                name="🗣️ คำให้การแก้ต่างของจำเลย",
                value="*กำลังรอจำเลยพูดแก้ตัวในห้องคุย หรือกดปุ่ม `🗣️ จำเลยแก้ต่าง` ด้านล่าง...*",
                inline=False,
            )

        if self.witness_notes:
            w_lines = [f"• **{name}:** \"{note}\"" for name, note in self.witness_notes[-4:]]
            embed.add_field(
                name="🕵️ พยานและหลักฐานเพิ่มเติมจากชาวแก๊ง",
                value="\n".join(w_lines)[:1024],
                inline=False,
            )

        guilty_names = ", ".join([m.display_name for m in self.guilty_voters.values()]) or "ยังไม่มีคนโหวต"
        innocent_names = ", ".join([m.display_name for m in self.innocent_voters.values()]) or "ยังไม่มีคนโหวต"

        embed.add_field(
            name=f"💀 มีความผิดจริง! ({len(self.guilty_voters)} เสียง)",
            value=guilty_names,
            inline=True,
        )
        embed.add_field(
            name=f"😇 บริสุทธิ์ / โดนใส่ร้าย ({len(self.innocent_voters)} เสียง)",
            value=innocent_names,
            inline=True,
        )

        if verdict_text:
            embed.add_field(name="🔨 คำพิพากษาสูงสุด", value=verdict_text, inline=False)
        else:
            embed.set_footer(
                text="💬 เปิดโอกาสให้เถียงกันก่อนตัดสิน! ต่อให้เป็นแอดมินหรือเจ้าของดิสก็โดนลงโทษได้ครบทุกคน!"
            )
        return embed

    async def run_trial_timer(self):
        try:
            while not self.closed:
                remaining = self.end_time - time.time()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(remaining, 2.0))

            if not self.closed:
                await self.finalize_verdict(interaction=None)
        except asyncio.CancelledError:
            pass

    @discord.ui.button(label="💀 มีความผิดจริง (0)", style=discord.ButtonStyle.danger, row=0)
    async def vote_guilty(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.closed:
            return await interaction.response.send_message("❌ คดีนี้ตัดสินจบไปแล้ว!", ephemeral=True)
        uid = interaction.user.id
        if uid in self.guilty_voters:
            self.guilty_voters.pop(uid, None)
        else:
            self.innocent_voters.pop(uid, None)
            self.guilty_voters[uid] = interaction.user

        self.sync_button_labels()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="😇 บริสุทธิ์ (0)", style=discord.ButtonStyle.success, row=0)
    async def vote_innocent(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.closed:
            return await interaction.response.send_message("❌ คดีนี้ตัดสินจบไปแล้ว!", ephemeral=True)
        uid = interaction.user.id
        if uid in self.innocent_voters:
            self.innocent_voters.pop(uid, None)
        else:
            self.guilty_voters.pop(uid, None)
            self.innocent_voters[uid] = interaction.user

        self.sync_button_labels()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="🗣️ จำเลยแก้ต่าง", style=discord.ButtonStyle.primary, row=1)
    async def btn_defend(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.closed:
            return await interaction.response.send_message("❌ คดีนี้ตัดสินจบไปแล้ว!", ephemeral=True)
        if interaction.user.id != self.defendant.id:
            return await interaction.response.send_message(
                f"❌ ปุ่มนี้เฉพาะจำเลย ({self.defendant.display_name}) เท่านั้นที่กดแก้ต่างได้! ถ้าอยากแฉเพิ่มกดปุ่ม `🕵️ พยานแฉเพิ่ม` เลย!",
                ephemeral=True,
            )
        await interaction.response.send_modal(DefenseModal(self))

    @discord.ui.button(label="🕵️ พยานแฉเพิ่ม", style=discord.ButtonStyle.secondary, row=1)
    async def btn_witness(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.closed:
            return await interaction.response.send_message("❌ คดีนี้ตัดสินจบไปแล้ว!", ephemeral=True)
        await interaction.response.send_modal(WitnessModal(self))

    @discord.ui.button(label="⏱️ ต่อเวลาคุย +1 นาที", style=discord.ButtonStyle.secondary, row=1)
    async def btn_extend(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.closed:
            return await interaction.response.send_message("❌ คดีนี้ตัดสินจบไปแล้ว!", ephemeral=True)
        if interaction.user.id not in (self.plaintiff.id, self.defendant.id):
            return await interaction.response.send_message(
                "❌ เฉพาะโจทก์หรือจำเลยเท่านั้นที่ขอต่อเวลาเถียงกันได้!", ephemeral=True
            )
        if self.extensions_used >= 2:
            return await interaction.response.send_message("❌ คดีนี้ต่อเวลาครบโควต้า 2 ครั้งแล้ว!", ephemeral=True)

        self.extensions_used += 1
        self.end_time += 60
        if self.extensions_used >= 2:
            button.disabled = True

        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="🔨 ทุบค้อนตัดสิน", style=discord.ButtonStyle.secondary, row=1)
    async def btn_verdict(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.closed:
            return await interaction.response.send_message("❌ คดีนี้ตัดสินจบไปแล้ว!", ephemeral=True)

        if interaction.user.id != self.plaintiff.id:
            return await interaction.response.send_message(
                "❌ เฉพาะโจทก์ผู้ยื่นฟ้องเท่านั้นที่กดทุบค้อนปิดคดีได้ (หรือรอให้หมดเวลาเพื่อตัดสินอัตโนมัติ)!",
                ephemeral=True,
            )

        now = time.time()
        if now < self.min_talk_until:
            return await interaction.response.send_message(
                f"⏳ **ใจเย็นผู้พิพากษา! ให้เวลาคุยและแก้ต่างกันก่อน!**\n"
                f"จะกดทุบค้อนก่อนเวลาได้ <t:{int(self.min_talk_until)}:R> หรือรอให้ศาลตัดสินอัตโนมัติ <t:{int(self.end_time)}:R> ครับ",
                ephemeral=True,
            )

        if len(self.guilty_voters) == 0 and len(self.innocent_voters) == 0:
            return await interaction.response.send_message(
                "❌ ยังไม่มีลูกขุนมาโหวตสักคนเลย รอเพื่อนกดโหวตก่อน!", ephemeral=True
            )

        await self.finalize_verdict(interaction=interaction)

    async def execute_punishments(self) -> list[str]:
        """ดำเนินการลงโทษจำเลยแบบทะลุสิทธิ์ Admin / เจ้าของเซิร์ฟเวอร์!"""
        return await self.cog.apply_punishment(
            guild=self.guild,
            target_id=self.defendant.id,
            punishment_type=self.punishment_type,
            punishment_nick=self.punishment_nick,
            punish_minutes=self.punish_minutes,
            reason_str=f"แพ้คดีศาลเตี้ย #{self.case_id}: {self.charge}",
        )

    async def finalize_verdict(self, interaction: discord.Interaction | None):
        async with self._lock:
            if self.closed:
                return
            self.closed = True

            if self.timer_task and not self.timer_task.done():
                current = asyncio.current_task()
                if self.timer_task is not current:
                    self.timer_task.cancel()

            for child in self.children:
                child.disabled = True
            self.sync_button_labels()

            g_count = len(self.guilty_voters)
            i_count = len(self.innocent_voters)

            if g_count == 0 and i_count == 0:
                verdict_code = "DISMISSED"
                verdict_text = (
                    f"⚪ **หมดเวลา! ไม่มีลูกขุนมาโหวตเลย (0 ต่อ 0)**\n"
                    f"ศาลขอจำหน่ายคดีทิ้ง {self.defendant.mention} รอดตัวไปแบบงงๆ!"
                )
            elif g_count > i_count:
                verdict_code = "GUILTY"
                punish_logs = await self.execute_punishments()
                punish_block = "\n".join(punish_logs)
                verdict_text = (
                    f"🔴 **ศาลตัดสินว่า: มีความผิดจริง! (โหวต {g_count} ต่อ {i_count})**\n"
                    f"{punish_block}"
                )
            elif i_count > g_count:
                verdict_code = "INNOCENT"
                verdict_text = (
                    f"🟢 **ศาลตัดสินว่า: บริสุทธิ์! (โหวต {i_count} ต่อ {g_count})**\n"
                    f"🎉 {self.defendant.mention} พ้นมลทิน! ส่วนโจทก์ {self.plaintiff.mention} หน้าแตกยับ!"
                )
            else:
                verdict_code = "TIE"
                verdict_text = (
                    f"🟡 **คะแนนโหวตเสมอกัน ({g_count} ต่อ {i_count})!**\n"
                    f"ศาลยกประโยชน์แห่งความสงสัยให้จำเลย {self.defendant.mention} รอดตัวไปหวุดหวิด!"
                )

            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "UPDATE lawsuits SET verdict = ?, guilty_votes = ?, innocent_votes = ? WHERE id = ?",
                    (verdict_code, g_count, i_count, self.case_id),
                )
                await db.commit()

            final_embed = self.build_embed(verdict_text)

            if interaction is not None:
                await interaction.response.edit_message(embed=final_embed, view=self)
                await interaction.followup.send(f"🔨 **ทุบค้อนปิดคดีหมายเลข #{self.case_id}!**\n{verdict_text}")
            else:
                if self.message:
                    try:
                        await self.message.edit(embed=final_embed, view=self)
                    except Exception:
                        pass
                try:
                    await self.channel.send(
                        f"⏰🔨 **หมดเวลาไต่สวน! ศาลขออ่านคำพิพากษาคดีหมายเลข #{self.case_id}!**\n{verdict_text}"
                    )
                except Exception:
                    pass


class DefenseModal(discord.ui.Modal, title="🗣️ ยื่นคำให้การแก้ต่างต่อศาลเตี้ย"):
    statement = discord.ui.TextInput(
        label="คำแก้ต่างของคุณ (พิมพ์แก้ตัวให้เพื่อนเห็นใจ)",
        style=discord.TextStyle.paragraph,
        placeholder="เช่น ผมไม่ได้ตั้งใจเทนัดครับ แค่หลับเพลินไป 14 ชั่วโมงเอง...",
        required=True,
        max_length=400,
    )

    def __init__(self, trial_view: TrialVoteView):
        super().__init__()
        self.trial_view = trial_view

    async def on_submit(self, interaction: discord.Interaction):
        self.trial_view.defense_statement = self.statement.value
        await interaction.response.edit_message(
            embed=self.trial_view.build_embed(), view=self.trial_view
        )


class WitnessModal(discord.ui.Modal, title="🕵️ ยื่นคำพยาน / หลักฐานแฉเพิ่มเติม"):
    evidence = discord.ui.TextInput(
        label="คำให้การพยาน หรือ หลักฐานเสริม",
        style=discord.TextStyle.paragraph,
        placeholder="เช่น ผมเห็นคาตาเลยครับว่ามันบอกไปนอนแต่แอบกดแรงค์ตอนตี 2!",
        required=True,
        max_length=250,
    )

    def __init__(self, trial_view: TrialVoteView):
        super().__init__()
        self.trial_view = trial_view

    async def on_submit(self, interaction: discord.Interaction):
        self.trial_view.witness_notes.append((interaction.user.display_name, self.evidence.value))
        await interaction.response.edit_message(
            embed=self.trial_view.build_embed(), view=self.trial_view
        )


class FriendCourtCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # ระบบคุมขังระดับปราบแอดมิน / เจ้าของเซิร์ฟเวอร์
        self.active_mutes: dict[tuple[int, int], float] = {}       # (guild_id, user_id) -> expire_timestamp
        self.pending_mutes: dict[tuple[int, int], int] = {}        # (guild_id, user_id) -> duration_sec (กรณีอยู่นอกห้องตอนตัดสิน)
        self.pending_unmutes: set[tuple[int, int]] = set()         # (guild_id, user_id)
        self.enforced_timeouts: dict[tuple[int, int], float] = {}  # (guild_id, user_id) -> expire_timestamp (ลบแชทแอดมินอัตโนมัติ)
        self.shame_nicks: dict[tuple[int, int], tuple[str, float]] = {}  # (guild_id, user_id) -> (nick, expire_timestamp)
        self.original_nicks: dict[tuple[int, int], str | None] = {}      # (guild_id, user_id) -> ชื่อเล่นเดิมก่อนโดนลงโทษ
        self.warn_cooldown: dict[tuple[int, int], float] = {}
        self.remute_cooldown: dict[tuple[int, int], float] = {}
        self.webhook_cache: dict[int, discord.Webhook] = {}

    async def apply_punishment(
        self,
        guild: discord.Guild,
        target_id: int,
        punishment_type: str,
        punishment_nick: str | None,
        punish_minutes: int,
        reason_str: str,
    ) -> list[str]:
        """ฟังก์ชันกลางสำหรับลงโทษเพื่อน (ใช้ร่วมกันทั้ง /sue และ /punish แบบทะลุสิทธิ์แอดมิน)"""
        results: list[str] = []
        member = guild.get_member(target_id)
        if not member:
            try:
                member = await guild.fetch_member(target_id)
            except Exception:
                return ["⚠️ ไม่พบตัวผู้ถูกลงโทษในเซิร์ฟเวอร์แล้ว!"]

        duration_sec = punish_minutes * 60
        expire_ts = time.time() + duration_sec
        key = (guild.id, member.id)

        # 1. ลงโทษหลักตามประเภทที่เลือก
        if punishment_type == "mute":
            self.pending_unmutes.discard(key)
            self.active_mutes[key] = expire_ts
            self.schedule_unmute(guild.id, member.id, duration_sec, expire_ts)
            if member.voice and member.voice.channel:
                try:
                    await member.edit(mute=True, reason=reason_str)
                    results.append(
                        f"🔇 **ปิดไมค์ล็อกตาย (Anti-Admin Mute)** {member.mention} เป็นเวลา **`{punish_minutes} นาที`**! *(พ้นโทษ <t:{int(expire_ts)}:R>)*"
                    )
                except discord.Forbidden:
                    results.append("⚠️ บอทไม่มีสิทธิ์ Mute Members (โปรดเช็คสิทธิ์ Administrator ของบอท)")
            else:
                self.pending_mutes[key] = duration_sec
                results.append(
                    f"🔇 เป้าหมายไม่ได้อยู่ในห้องคุยตอนนี้ — **ออกหมายจับล็อกไมค์ไว้แล้ว!** ทันทีที่ {member.mention} เข้าห้องคุย จะโดนปิดไมค์ **`{punish_minutes} นาที`** ทันที!"
                )

        elif punishment_type == "timeout":
            self.pending_unmutes.discard(key)
            native_ok = False
            try:
                await member.timeout(timedelta(minutes=punish_minutes), reason=reason_str)
                native_ok = True
                results.append(
                    f"⏳ **Timeout ปิดปาก** {member.mention} ห้ามพิมพ์แชทและห้ามคุยเป็นเวลา **`{punish_minutes} นาที`** สำเร็จ!"
                )
            except discord.Forbidden:
                native_ok = False

            if not native_ok:
                self.enforced_timeouts[key] = expire_ts
                self.active_mutes[key] = expire_ts
                self.schedule_unmute(guild.id, member.id, duration_sec, expire_ts)

                if member.voice and member.voice.channel:
                    try:
                        await member.edit(mute=True, reason=reason_str)
                    except Exception:
                        pass
                else:
                    self.pending_mutes[key] = duration_sec

                results.append(
                    f"⛓️ **เปิดระบบขังเดี่ยวปราบแอดมิน (Super Timeout)!** ใส่ {member.mention} เป็นเวลา **`{punish_minutes} นาที`** (พ้นโทษ <t:{int(expire_ts)}:R>)\n"
                    f"┗ 🚫 พิมพ์แชทมาปุ๊บบอทลบข้อความทิ้งทันที + 🔇 ล็อกปิดไมค์ในห้องคุยห้ามปลดเอง!"
                )

        elif punishment_type == "disconnect":
            if member.voice and member.voice.channel:
                vc_name = member.voice.channel.name
                try:
                    await member.move_to(None, reason=reason_str)
                    results.append(f"🦵 **ถีบส่ง {member.mention} กระเด็นออกจากห้อง `{vc_name}`** เรียบร้อย!")
                except discord.Forbidden:
                    results.append("⚠️ บอทไม่มีสิทธิ์ Move Members (โปรดเช็คสิทธิ์ Administrator ของบอท)")
            else:
                results.append(f"🏃 {member.mention} ไม่ได้อยู่ในห้องพูดคุยตอนนี้!")

        # 2. ลงโทษเปลี่ยนชื่อเล่น (หรือสวมหน้ากากชื่อประจานถ้าเป้าหมายเป็นเจ้าของดิส / ยศสูงกว่าบอท)
        if punishment_nick:
            target_nick = punishment_nick[:32]
            if key not in self.original_nicks:
                self.original_nicks[key] = member.nick
            try:
                await member.edit(nick=target_nick, reason=reason_str)
                results.append(
                    f"🏷️ **บังคับเปลี่ยนชื่อเล่น** ของ {member.mention} เป็น **`{target_nick}`** สำเร็จ!"
                )
            except discord.Forbidden:
                shame_mins = max(10, punish_minutes)
                shame_expire = time.time() + (shame_mins * 60)
                self.shame_nicks[key] = (target_nick, shame_expire)
                results.append(
                    f"🎭 **สวมหน้ากากฉายานักโทษปราบแอดมิน!** เวลา {member.mention} พิมพ์แชทตลอด **`{shame_mins} นาที`** นี้ บอทจะแปลงชื่อผู้ส่งเป็น **`{target_nick}`** ให้อัตโนมัติ!"
                )

        return results

    def schedule_unmute(self, guild_id: int, user_id: int, delay_seconds: int, expected_expire: float):
        async def _unmute_job():
            await asyncio.sleep(delay_seconds)
            key = (guild_id, user_id)
            # ถ้ามีการตัดสินคดีใหม่ทับเวลาเดิม ให้รออันใหม่แทน
            if self.active_mutes.get(key, 0) > expected_expire + 1:
                return
            self.active_mutes.pop(key, None)
            self.enforced_timeouts.pop(key, None)

            guild = self.bot.get_guild(guild_id)
            if not guild:
                return
            member = guild.get_member(user_id)
            if member and member.voice and member.voice.channel:
                try:
                    await member.edit(mute=False, reason="ครบกำหนดโทษปิดไมค์จากศาลเตี้ยแล้ว")
                except Exception:
                    pass
            else:
                self.pending_unmutes.add(key)

        asyncio.create_task(_unmute_job())

    async def _get_or_create_webhook(self, channel: discord.TextChannel) -> discord.Webhook | None:
        cached = self.webhook_cache.get(channel.id)
        if cached:
            return cached
        try:
            webhooks = await channel.webhooks()
            for wh in webhooks:
                if wh.user and self.bot.user and wh.user.id == self.bot.user.id:
                    self.webhook_cache[channel.id] = wh
                    return wh
            wh = await channel.create_webhook(name="FriendCourtMask")
            self.webhook_cache[channel.id] = wh
            return wh
        except Exception:
            return None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        now = time.time()
        key = (message.guild.id, message.author.id)

        # 1. เช็คโทษ Super Timeout (ปราบแอดมิน/เจ้าของดิส): พิมพ์อะไรมา ลบทิ้งทันที!
        timeout_until = self.enforced_timeouts.get(key)
        if timeout_until:
            if now < timeout_until:
                try:
                    await message.delete()
                except Exception:
                    pass
                last_warn = self.warn_cooldown.get(key, 0)
                if now - last_warn >= 6.0:
                    self.warn_cooldown[key] = now
                    try:
                        await message.channel.send(
                            f"⛓️ {message.author.mention} **แกกำลังติดโทษ Timeout จากศาลเตี้ย!** ต่อให้มียศแอดมินก็ห้ามพิมพ์แชท *(พ้นโทษ <t:{int(timeout_until)}:R>)*",
                            delete_after=5.0,
                        )
                    except Exception:
                        pass
                return
            else:
                self.enforced_timeouts.pop(key, None)

        # 2. เช็คโทษสวมหน้ากากฉายาประจาน (สำหรับเจ้าของดิส 👑 หรือแอดมินที่ยศสูงกว่าบอท)
        shame_data = self.shame_nicks.get(key)
        if shame_data:
            shame_nick, shame_until = shame_data
            if now < shame_until:
                if (
                    isinstance(message.channel, discord.TextChannel)
                    and message.content
                    and not message.attachments
                ):
                    wh = await self._get_or_create_webhook(message.channel)
                    if wh:
                        try:
                            await message.delete()
                            await wh.send(
                                content=message.content,
                                username=shame_nick[:32],
                                avatar_url=message.author.display_avatar.url,
                                allowed_mentions=discord.AllowedMentions.none(),
                            )
                        except Exception:
                            pass
            else:
                self.shame_nicks.pop(key, None)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ):
        if member.bot or not after.channel:
            return

        now = time.time()
        key = (member.guild.id, member.id)

        # 1. ถ้ามีหมายจับปิดไมค์ค้างไว้ (ตอนตัดสินไม่ได้อยู่ในห้องคุย) -> ล็อกคอปิดไมค์ทันทีที่เข้าห้อง!
        if key in self.pending_mutes:
            duration = self.pending_mutes.pop(key)
            expire_ts = now + duration
            self.active_mutes[key] = expire_ts
            try:
                await member.edit(mute=True, reason="รับโทษปิดไมค์ตามหมายจับศาลเตี้ย")
                self.schedule_unmute(member.guild.id, member.id, duration, expire_ts)
            except Exception:
                pass
            return

        # 2. ป้องกันแอดมินใช้อำนาจมืดกดปลด Server Mute ให้ตัวเองก่อนครบเวลา!
        mute_until = self.active_mutes.get(key)
        if mute_until:
            if now < mute_until:
                if not after.mute:
                    last_rm = self.remute_cooldown.get(key, 0)
                    if now - last_rm >= 0.8:
                        self.remute_cooldown[key] = now
                        try:
                            await member.edit(mute=True, reason="ห้ามแอดมินแอบปลดไมค์เองก่อนครบโทษศาลเตี้ย!")
                        except Exception:
                            pass
                return
            else:
                self.active_mutes.pop(key, None)

        # 3. ถ้าครบเวลาโทษไปแล้วตอนเจ้าตัวอยู่นอกห้อง -> ปลด Server Mute คืนให้ทันทีที่เข้าห้อง
        if key in self.pending_unmutes:
            self.pending_unmutes.discard(key)
            if after.mute:
                try:
                    await member.edit(mute=False, reason="ครบกำหนดโทษศาลเตี้ยแล้ว")
                except Exception:
                    pass

    @app_commands.command(
        name="sue",
        description="⚖️ ฟ้องศาลเตี้ยเอาผิดเพื่อน! เลือกบทลงโทษได้ครบ (แอดมินก็โดนได้!) และมีเวลาคุยก่อนตัดสิน",
    )
    @app_commands.describe(
        defendant="เพื่อนตัวตึงที่ต้องการฟ้องร้อง (ฟ้องแอดมินหรือเจ้าของดิสก็ได้!)",
        charge="ข้อหา / ความผิดที่ก่อไว้ (เช่น บอกจะมาใน 5 นาทีแต่หายไป 2 ชม., โยนเกม)",
        punishment="เลือกบทลงโทษหากจำเลยแพ้คดี",
        punishment_nick="ตั้งชื่อประจานใหม่ (ถ้าเลือกโทษอื่นแล้วกรอกช่องนี้ด้วย จะเปลี่ยนชื่อแถมให้ด้วย!)",
        talk_time="เวลาเปิดศาลให้คุยเถียงกันและโหวตก่อนตัดสิน (ค่าเริ่มต้น 2 นาที)",
        punish_minutes="ระยะเวลากี่นาที สำหรับโทษ Mute หรือ Timeout (1-60 นาที, ค่าเริ่มต้น 3 นาที)",
    )
    @app_commands.choices(
        punishment=[
            app_commands.Choice(name="🏷️ เปลี่ยนชื่อเล่นประจาน (Nickname)", value="nickname"),
            app_commands.Choice(name="🔇 ปิดไมค์ในห้องพูดคุย (Server Mute)", value="mute"),
            app_commands.Choice(name="⏳ ปิดปากห้ามพิมพ์และห้ามคุย (Timeout)", value="timeout"),
            app_commands.Choice(name="🦵 เตะกระเด็นออกจากห้องพูดคุย (Disconnect)", value="disconnect"),
        ],
        talk_time=[
            app_commands.Choice(name="⏱️ 30 วินาที (คดีด่วนทันใจ)", value=30),
            app_commands.Choice(name="⏱️ 1 นาที (คุยสั้นๆ)", value=60),
            app_commands.Choice(name="⏱️ 2 นาที (มาตรฐาน - แนะนำ)", value=120),
            app_commands.Choice(name="⏱️ 3 นาที (เปิดศึกโต้วาที)", value=180),
            app_commands.Choice(name="⏱️ 5 นาที (ดราม่าระดับชาติ)", value=300),
        ],
    )
    async def sue_friend(
        self,
        interaction: discord.Interaction,
        defendant: discord.Member,
        charge: str,
        punishment: str = "nickname",
        punishment_nick: str = None,
        talk_time: int = 120,
        punish_minutes: int = 3,
    ):
        if defendant.bot:
            return await interaction.response.send_message("❌ ฟ้องบอทไม่ได้เว้ยเพื่อน ฟ้องคนด้วยกันสิ!", ephemeral=True)
        if defendant.id == interaction.user.id:
            return await interaction.response.send_message("❌ จะฟ้องตัวเองทำไมล่ะเนี่ย!", ephemeral=True)

        await interaction.response.defer()

        punish_minutes = max(1, min(punish_minutes, 60))

        clean_nick = punishment_nick.strip()[:32] if punishment_nick and punishment_nick.strip() else None
        if punishment == "nickname" and not clean_nick:
            clean_nick = f"[นักโทษ] {defendant.display_name}"[:32]

        punish_summary = format_punishment_detail(punishment, clean_nick, punish_minutes)

        ai_comment = "เปิดบัลลังก์ไต่สวน! ให้เวลาโจทก์กับจำเลยซัดกันให้เต็มที่ก่อนลูกขุนลงมติ!"
        gemini_cog = self.bot.get_cog("GeminiFriendCog")
        if gemini_cog and gemini_cog.client:
            prompt = (
                f"ในศาลเตี้ยกลุ่มเพื่อน โจทก์ชื่อ '{interaction.user.display_name}' ได้ยื่นฟ้องจำเลยชื่อ '{defendant.display_name}' "
                f"ในข้อหา: '{charge}' หากแพ้จะโดนโทษ: '{punish_summary}' "
                f"จงรับบทเป็นผู้พิพากษาศาลเตี้ยปากแจ๋ว พูดเปิดคดีแซวทั้งคู่และบอกให้เริ่มเถียงกันได้เลย (สั้นๆ 2 ประโยคเด็ดๆ)"
            )
            ai_comment = await gemini_cog.ask_gemini(prompt)

        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                """
                INSERT INTO lawsuits (
                    guild_id, plaintiff_id, defendant_id, charge,
                    punishment_type, punishment_nick, punish_minutes, verdict
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING')
                """,
                (
                    interaction.guild_id,
                    interaction.user.id,
                    defendant.id,
                    charge,
                    punishment,
                    clean_nick,
                    punish_minutes,
                ),
            )
            case_id = cursor.lastrowid
            await db.commit()

        view = TrialVoteView(
            cog=self,
            guild=interaction.guild,
            channel=interaction.channel,
            case_id=case_id,
            plaintiff=interaction.user,
            defendant=defendant,
            charge=charge,
            punishment_type=punishment,
            punishment_nick=clean_nick,
            punish_minutes=punish_minutes,
            talk_seconds=talk_time,
            ai_comment=ai_comment,
        )

        sent_msg = await interaction.followup.send(
            content=(
                f"🚨 **เปิดบัลลังก์ศาลเตี้ย คดี #{case_id}!** {defendant.mention} คุณถูกฟ้องโดย {interaction.user.mention}!\n"
                f"🎙️ **มีเวลาให้คุยเถียงกันและลงคะแนนโหวตก่อนศาลตัดสิน!**"
            ),
            embed=view.build_embed(),
            view=view,
            wait=True,
        )
        view.message = sent_msg
        view.timer_task = asyncio.create_task(view.run_trial_timer())

    @app_commands.command(
        name="punish",
        description="⚡ (เฉพาะ Admin) สั่งลงโทษเพื่อนทันทีโดยไม่ต้องเปิดโหวตศาลเตี้ย! (ใช้ /pardon ปลดโทษได้)",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        target="เพื่อนเป้าหมายที่ต้องการสั่งลงโทษทันที",
        punishment="เลือกรูปแบบบทลงโทษ",
        reason="เหตุผลหรือข้อหาที่สั่งลงโทษ (ไม่บังคับกรอก)",
        punishment_nick="ตั้งชื่อประจานใหม่ (กรอกเพิ่มคู่กับโทษอื่นเพื่อเปลี่ยนชื่อแถมได้!)",
        punish_minutes="ระยะเวลากี่นาที สำหรับโทษ Mute หรือ Timeout (1-60 นาที, ค่าเริ่มต้น 3 นาที)",
    )
    @app_commands.choices(
        punishment=[
            app_commands.Choice(name="🏷️ เปลี่ยนชื่อเล่นประจาน (Nickname)", value="nickname"),
            app_commands.Choice(name="🔇 ปิดไมค์ในห้องพูดคุย (Server Mute)", value="mute"),
            app_commands.Choice(name="⏳ ปิดปากห้ามพิมพ์และห้ามคุย (Timeout)", value="timeout"),
            app_commands.Choice(name="🦵 เตะกระเด็นออกจากห้องพูดคุย (Disconnect)", value="disconnect"),
        ]
    )
    async def punish_direct(
        self,
        interaction: discord.Interaction,
        target: discord.Member,
        punishment: str,
        reason: str = "คำสั่งลงทัณฑ์สายฟ้าฟาดจากแอดมิน",
        punishment_nick: str = None,
        punish_minutes: int = 3,
    ):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(
                "❌ คำสั่ง `/punish` สงวนไว้สำหรับ **Admin** เท่านั้น! ถ้าอยากลงโทษเพื่อนใช้ `/sue` เปิดโหวตแทนนะ!",
                ephemeral=True,
            )
        if target.bot:
            return await interaction.response.send_message("❌ สั่งลงโทษบอทไม่ได้นะเพื่อน!", ephemeral=True)

        await interaction.response.defer()
        punish_minutes = max(1, min(punish_minutes, 60))

        clean_nick = punishment_nick.strip()[:32] if punishment_nick and punishment_nick.strip() else None
        if punishment == "nickname" and not clean_nick:
            clean_nick = f"[นักโทษ] {target.display_name}"[:32]

        logs = await self.apply_punishment(
            guild=interaction.guild,
            target_id=target.id,
            punishment_type=punishment,
            punishment_nick=clean_nick,
            punish_minutes=punish_minutes,
            reason_str=f"แอดมิน {interaction.user.display_name} สั่งลงโทษด่วน: {reason}",
        )

        embed = discord.Embed(
            title="⚡ คำสั่งลงทัณฑ์สายฟ้าฟาด! (ไม่ต้องผ่านศาล)",
            description=(
                f"👮 **ผู้สั่งลงโทษ:** {interaction.user.mention}\n"
                f"⛓️ **ผู้รับโทษ:** {target.mention}\n"
                f"📜 **ข้อหา/เหตุผล:** `{reason}`\n\n"
                + "\n".join(logs)
            ),
            color=discord.Color.red(),
        )
        embed.set_footer(text="💡 ใช้คำสั่ง /pardon เพื่อปลดบทลงโทษทั้งหมดได้ตลอดเวลา")
        await interaction.followup.send(embed=embed)

    @punish_direct.error
    async def punish_direct_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ คำสั่ง `/punish` สงวนไว้สำหรับ **Admin** เท่านั้น! ถ้าอยากลงโทษเพื่อนใช้ `/sue` เปิดโหวตแทนนะ!",
                    ephemeral=True,
                )

    @app_commands.command(
        name="pardon",
        description="🕊️ อภัยโทษปลดแบน/ล็อกไมค์/คืนชื่อเดิมให้เพื่อน (ใช้ได้กับทั้งโทษจาก /sue และ /punish)",
    )
    @app_commands.describe(member="เพื่อนที่ต้องการปลดบทลงโทษทั้งหมดให้")
    async def pardon_friend(self, interaction: discord.Interaction, member: discord.Member):
        if member.id == interaction.user.id:
            return await interaction.response.send_message(
                "❌ **นักโทษห้ามกดอภัยโทษให้ตัวเองเว้ย!** ต้องให้เพื่อนคนอื่นในกลุ่มเป็นคนกดปลดให้เท่านั้น!",
                ephemeral=True,
            )

        await interaction.response.defer()
        key = (interaction.guild_id, member.id)
        self.active_mutes.pop(key, None)
        self.pending_mutes.pop(key, None)
        self.enforced_timeouts.pop(key, None)
        self.shame_nicks.pop(key, None)

        # คืนชื่อเล่นเดิมก่อนโดนลงโทษ (ถ้าเคยถูกบอทเปลี่ยนชื่อไว้)
        if key in self.original_nicks:
            prev_nick = self.original_nicks.pop(key)
            try:
                await member.edit(nick=prev_nick, reason=f"ได้รับอภัยโทษคืนชื่อเดิมโดย {interaction.user.display_name}")
            except Exception:
                pass

        try:
            await member.timeout(None, reason=f"ได้รับอภัยโทษโดย {interaction.user.display_name}")
        except Exception:
            pass

        if member.voice and member.voice.channel and member.voice.mute:
            try:
                await member.edit(mute=False, reason=f"ได้รับอภัยโทษโดย {interaction.user.display_name}")
            except Exception:
                pass
        else:
            self.pending_unmutes.add(key)

        await interaction.followup.send(
            f"🕊️ **พระราชทานอภัยโทษ!** {interaction.user.mention} ได้ล้างบทลงโทษทั้งหมด (ปลดแบน / ปลดไมค์ / คืนชื่อเดิม) ให้ {member.mention} เรียบร้อยแล้ว!"
        )

    @app_commands.command(
        name="criminal_record",
        description="🚓 เช็คประวัติอาชญากรรมและคดีความในศาลเตี้ยของเพื่อน (หรือดูอันดับอาชญากรประจำกลุ่ม)",
    )
    @app_commands.describe(member="เพื่อนที่ต้องการเช็คประวัติ (เว้นว่างเพื่อดูอันดับอาชญากรทั้งกลุ่ม)")
    async def criminal_record(self, interaction: discord.Interaction, member: discord.Member = None):
        async with aiosqlite.connect(DB_PATH) as db:
            if member:
                async with db.execute(
                    """
                    SELECT id, plaintiff_id, charge, punishment_type, verdict, guilty_votes, innocent_votes
                    FROM lawsuits
                    WHERE guild_id = ? AND defendant_id = ?
                    ORDER BY id DESC LIMIT 10
                    """,
                    (interaction.guild_id, member.id),
                ) as cur:
                    rows = await cur.fetchall()

                async with db.execute(
                    "SELECT COUNT(*) FROM lawsuits WHERE guild_id = ? AND defendant_id = ? AND verdict = 'GUILTY'",
                    (interaction.guild_id, member.id),
                ) as cur:
                    guilty_total = (await cur.fetchone())[0]

                embed = discord.Embed(
                    title=f"🚓 แฟ้มประวัติอาชญากรรม: {member.display_name}",
                    description=f"โดนฟ้องทั้งหมด **{len(rows)} คดี** | ศาลตัดสินว่าผิดจริง **{guilty_total} คดี**",
                    color=discord.Color.orange(),
                )
                embed.set_thumbnail(url=member.display_avatar.url)

                if not rows:
                    embed.add_field(name="✨ ประวัติขาวสะอาด", value="คนนี้ยังไม่เคยโดนฟ้องขึ้นศาลเตี้ยเลย!", inline=False)
                else:
                    for c_id, p_id, charge, p_type, verdict, g_v, i_v in rows[:6]:
                        status_icon = (
                            "💀 ผิดจริง"
                            if verdict == "GUILTY"
                            else ("😇 รอด" if verdict in ("INNOCENT", "TIE", "DISMISSED") else "⏳ รอตัดสิน")
                        )
                        p_label = PUNISHMENT_NAMES.get(p_type or "nickname", "🏷️ เปลี่ยนชื่อ")
                        embed.add_field(
                            name=f"คดี #{c_id} — {status_icon} ({g_v} vs {i_v}) | โทษ: {p_label}",
                            value=f"**ข้อหา:** {charge} *(ฟ้องโดย <@{p_id}>)*",
                            inline=False,
                        )
                await interaction.response.send_message(embed=embed)
            else:
                async with db.execute(
                    """
                    SELECT defendant_id,
                           COUNT(*) as total_cases,
                           SUM(CASE WHEN verdict = 'GUILTY' THEN 1 ELSE 0 END) as guilty_cases
                    FROM lawsuits
                    WHERE guild_id = ?
                    GROUP BY defendant_id
                    ORDER BY guilty_cases DESC, total_cases DESC
                    LIMIT 10
                    """,
                    (interaction.guild_id,),
                ) as cur:
                    rows = await cur.fetchall()

                if not rows:
                    return await interaction.response.send_message(
                        "ยังไม่มีใครเคยโดนฟ้องในเซิร์ฟเวอร์นี้เลย! ประเดิมฟ้องเพื่อนคนแรกด้วย `/sue` ได้เลย!",
                        ephemeral=True,
                    )

                lines = []
                for idx, (def_id, total_c, guilty_c) in enumerate(rows, start=1):
                    medal = "🥇" if idx == 1 else ("🥈" if idx == 2 else ("🥉" if idx == 3 else f"`#{idx}`"))
                    lines.append(
                        f"{medal} <@{def_id}> — แพ้คดี **{guilty_c} ครั้ง** *(ถูกฟ้องทั้งหมด {total_c} คดี)*"
                    )

                embed = discord.Embed(
                    title="🏴‍☠️ ทำเนียบอาชญากรตัวตึงประจำกลุ่ม (Most Wanted)",
                    description="\n".join(lines),
                    color=discord.Color.red(),
                )
                await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(FriendCourtCog(bot))
