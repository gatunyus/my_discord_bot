import discord
from discord import app_commands
from discord.ext import commands
import os
import shutil
import asyncio
from database import (
    get_guild_config,
    get_entrance_sound,
    set_entrance_sound,
    get_entrance_volume,
    set_entrance_volume,
)

SOUNDS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sounds")
os.makedirs(SOUNDS_DIR, exist_ok=True)
AUDIO_EXTS = (".mp3", ".wav", ".ogg", ".m4a", ".flac", ".opus")

# โหลดไลบรารีเสียง Opus รองรับทั้ง Cloud Linux (Ubuntu/Debian/Docker) และ macOS อัตโนมัติ
if not discord.opus.is_loaded():
    for opus_path in (
        "libopus.so.0",
        "libopus.so",
        "/usr/lib/x86_64-linux-gnu/libopus.so.0",
        "/usr/lib/aarch64-linux-gnu/libopus.so.0",
        "/opt/homebrew/lib/libopus.dylib",
        "/opt/homebrew/lib/libopus.0.dylib",
        "/usr/local/lib/libopus.dylib",
        "libopus.0.dylib",
        "opus",
    ):
        try:
            discord.opus.load_opus(opus_path)
            print(f"🔊 โหลดระบบเสียง Opus สำเร็จจาก: {opus_path}")
            break
        except Exception:
            continue

FFMPEG_PATH = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"


def list_sound_files() -> list[str]:
    if not os.path.exists(SOUNDS_DIR):
        return []
    return sorted(
        f
        for f in os.listdir(SOUNDS_DIR)
        if not f.startswith(".") and f.lower().endswith(AUDIO_EXTS)
    )


class SoundboardView(discord.ui.View):
    """แผงปุ่มกด Soundboard ให้เพื่อนกดเล่นเสียงเอฟเฟกต์ในห้องคุยได้ทันที"""

    def __init__(self, cog: "VoiceMagicCog", files: list[str], volume: int = 50):
        super().__init__(timeout=3600)
        self.cog = cog
        self.volume = volume

        # แสดงปุ่มเสียงสูงสุด 20 ไฟล์แรก (เหลือที่ให้ปุ่มหยุดเสียง)
        for idx, fname in enumerate(files[:20]):
            label = os.path.splitext(fname)[0][:75]
            btn = discord.ui.Button(
                label=f"🔊 {label}",
                style=discord.ButtonStyle.primary,
                row=idx // 5,
            )
            btn.callback = self._make_play_callback(fname)
            self.add_item(btn)

        stop_btn = discord.ui.Button(
            label="⏹️ หยุดเสียง / ออกห้อง",
            style=discord.ButtonStyle.danger,
            row=min(4, (len(files[:20]) // 5) + 1) if len(files[:20]) < 20 else 4,
        )
        stop_btn.callback = self._stop_callback
        if len(self.children) < 25:
            self.add_item(stop_btn)

    def _make_play_callback(self, filename: str):
        async def _callback(interaction: discord.Interaction):
            if not interaction.user.voice or not interaction.user.voice.channel:
                return await interaction.response.send_message(
                    "❌ แกต้องเข้าไปอยู่ในห้องพูดคุย (Voice Channel) ก่อนถึงจะกดปุ่มเปิดเสียงได้!",
                    ephemeral=True,
                )
            sound_path = os.path.join(SOUNDS_DIR, filename)
            if not os.path.exists(sound_path):
                return await interaction.response.send_message(
                    f"❌ ไม่พบไฟล์เสียง `{filename}` แล้ว!", ephemeral=True
                )

            await interaction.response.defer(ephemeral=True)
            ok, err = await self.cog.play_audio_clip(
                interaction.guild,
                interaction.user.voice.channel,
                sound_path,
                self.volume,
            )
            if ok:
                await interaction.followup.send(
                    f"🔊 เปิดเสียง **`{filename}`** (`{self.volume}%`) ในห้อง **{interaction.user.voice.channel.name}**!",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(f"❌ เล่นเสียงไม่สำเร็จ: `{err}`", ephemeral=True)

        return _callback

    async def _stop_callback(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client if interaction.guild else None
        if not vc:
            return await interaction.response.send_message("🔇 บอทไม่ได้อยู่ในห้องพูดคุยแล้ว!", ephemeral=True)
        self.cog.play_tokens[interaction.guild.id] = self.cog.play_tokens.get(interaction.guild.id, 0) + 1
        if vc.is_playing():
            vc.stop()
        await vc.disconnect()
        await interaction.response.send_message("⏹️ หยุดเสียงและนำบอทออกจากห้องเรียบร้อย!", ephemeral=True)


class VoiceMagicCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.temp_channels: set[int] = set()
        self.play_tokens: dict[int, int] = {}
        self._vc_locks: dict[int, asyncio.Lock] = {}

    def _get_lock(self, guild_id: int) -> asyncio.Lock:
        if guild_id not in self._vc_locks:
            self._vc_locks[guild_id] = asyncio.Lock()
        return self._vc_locks[guild_id]

    async def play_audio_clip(
        self,
        guild: discord.Guild,
        channel: discord.VoiceChannel,
        sound_path: str,
        volume_pct: int = 50,
    ) -> tuple[bool, str | None]:
        """สั่งให้บอทเข้าห้อง (หรือสลับเพลงทันทีถ้าอยู่ในห้องแล้ว) เล่นไฟล์เสียง และออกอัตโนมัติเมื่อเล่นจบ"""
        async with self._get_lock(guild.id):
            token = self.play_tokens.get(guild.id, 0) + 1
            self.play_tokens[guild.id] = token

            try:
                vc = guild.voice_client
                if vc and vc.is_connected():
                    if vc.channel.id != channel.id:
                        await vc.move_to(channel)
                        await asyncio.sleep(0.25)
                else:
                    if vc:
                        try:
                            await vc.disconnect(force=True)
                        except Exception:
                            pass
                    vc = await channel.connect()
                    await asyncio.sleep(0.3)

                if vc.is_playing():
                    vc.stop()

                raw_audio = discord.FFmpegPCMAudio(sound_path, executable=FFMPEG_PATH)
                vol_float = max(0.01, min(volume_pct / 100.0, 2.0))
                audio_source = discord.PCMVolumeTransformer(raw_audio, volume=vol_float)
                vc.play(audio_source)
            except Exception as e:
                print(f"⚠️ เล่นเสียงไม่สำเร็จ: {e}")
                if guild.voice_client:
                    try:
                        await guild.voice_client.disconnect(force=True)
                    except Exception:
                        pass
                return False, str(e)

        async def _auto_disconnect_watcher():
            while True:
                if self.play_tokens.get(guild.id) != token:
                    return
                current_vc = guild.voice_client
                if not current_vc or not current_vc.is_connected() or not current_vc.is_playing():
                    break
                await asyncio.sleep(0.5)

            # รอเผื่อมีเพื่อนกดปุ่มเล่นเสียงต่อเนื่องอีก 2 วินาที ถ้าไม่มีค่อยออกจากห้อง
            await asyncio.sleep(2.0)
            if self.play_tokens.get(guild.id) == token and guild.voice_client:
                try:
                    await guild.voice_client.disconnect()
                except Exception:
                    pass

        asyncio.create_task(_auto_disconnect_watcher())
        return True, None

    async def sound_file_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        files = list_sound_files()
        matched = [f for f in files if current.lower() in f.lower()]
        return [app_commands.Choice(name=f, value=f) for f in matched[:25]]

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ):
        if member.bot:
            return

        guild = member.guild
        config = await get_guild_config(guild.id)
        jtc_id = config.get("jtc_channel_id")

        # 1. เช็คระบบลบห้องชั่วคราวเมื่อเพื่อนออกกันหมดแล้ว
        if before.channel and before.channel.id in self.temp_channels:
            human_members = [m for m in before.channel.members if not m.bot]
            if len(human_members) == 0:
                self.temp_channels.discard(before.channel.id)
                try:
                    await before.channel.delete(reason="ห้องคุยชั่วคราวว่างแล้ว")
                except Exception:
                    pass

        # 2. ระบบ Join-to-Create (กดเข้าห้อง ➕ สร้างห้องคุย แล้วสร้างห้องแยกให้ทันที)
        if after.channel:
            is_jtc = (jtc_id and after.channel.id == jtc_id) or ("สร้างห้องคุย" in after.channel.name)
            if is_jtc:
                category = after.channel.category
                overwrites = {
                    member: discord.PermissionOverwrite(manage_channels=True, move_members=True, connect=True)
                }
                new_vc = await guild.create_voice_channel(
                    name=f"🔊 ห้องของ {member.display_name}",
                    category=category,
                    overwrites=overwrites,
                )
                self.temp_channels.add(new_vc.id)
                try:
                    await member.move_to(new_vc)
                except Exception:
                    pass
                return

            # 3. ระบบเสียงเปิดตัวประจำตัว (Entrance Sound) เมื่อเพื่อนกดเข้าห้อง Voice!
            if before.channel != after.channel:
                sound_file = await get_entrance_sound(member.id)
                if sound_file:
                    sound_path = os.path.join(SOUNDS_DIR, sound_file)
                    if os.path.exists(sound_path) and not guild.voice_client:
                        volume_pct = await get_entrance_volume(member.id)
                        await asyncio.sleep(0.8)
                        if member.voice and member.voice.channel:
                            await self.play_audio_clip(guild, member.voice.channel, sound_path, volume_pct)

    @app_commands.command(
        name="play_sound",
        description="🔊 สั่งบอทเข้าห้องคุยไปเปิดไฟล์เสียงเอฟเฟกต์ทันที (ไม่ต้องตั้งเป็นเสียงเปิดตัว)",
    )
    @app_commands.describe(
        filename="เลือกไฟล์เสียงจากโฟลเดอร์ sounds/",
        volume="ความดังเสียง 1-200% (ค่าเริ่มต้น 50%)",
        channel="เลือกห้องคุยเป้าหมายที่จะส่งบอทไปปั่น (เว้นว่างเพื่อเปิดในห้องที่คุณอยู่)",
    )
    @app_commands.autocomplete(filename=sound_file_autocomplete)
    async def play_sound_cmd(
        self,
        interaction: discord.Interaction,
        filename: str,
        volume: int = 50,
        channel: discord.VoiceChannel = None,
    ):
        target_vc = channel or (interaction.user.voice.channel if interaction.user.voice else None)
        if not target_vc:
            return await interaction.response.send_message(
                "❌ แกต้องอยู่ในห้องพูดคุยก่อน หรือเลือกห้องเป้าหมายในช่อง `channel` เพื่อส่งบอทไปเปิดเสียง!",
                ephemeral=True,
            )

        files = list_sound_files()
        if filename not in files:
            available = ", ".join(files) if files else "ยังไม่มีไฟล์ในโฟลเดอร์ sounds/"
            return await interaction.response.send_message(
                f"❌ ไม่พบไฟล์ `{filename}` ในโฟลเดอร์ `sounds/`\n📂 ไฟล์ที่มีตอนนี้: `{available}`",
                ephemeral=True,
            )

        await interaction.response.defer()
        vol = max(1, min(volume, 200))
        sound_path = os.path.join(SOUNDS_DIR, filename)
        ok, err = await self.play_audio_clip(interaction.guild, target_vc, sound_path, vol)

        if ok:
            await interaction.followup.send(
                f"🔊 กำลังเปิดเสียง **`{filename}`** (ความดัง `{vol}%`) ในห้อง **{target_vc.name}**!"
            )
        else:
            await interaction.followup.send(f"❌ เล่นเสียงไม่สำเร็จ: `{err}`", ephemeral=True)

    @app_commands.command(
        name="soundboard",
        description="🎛️ เปิดแผงปุ่มกด Soundboard รวมเสียงมีมทั้งหมดในโฟลเดอร์ sounds/ กดเล่นได้รัวๆ!",
    )
    @app_commands.describe(volume="ความดังเริ่มต้นของปุ่มกด Soundboard (1-200%, ค่าเริ่มต้น 50%)")
    async def soundboard_cmd(self, interaction: discord.Interaction, volume: int = 50):
        files = list_sound_files()
        if not files:
            return await interaction.response.send_message(
                "❌ ยังไม่มีไฟล์เสียงในโฟลเดอร์ `sounds/` เลย! เอาไฟล์ `.mp3` ไปวางใน `sounds/` ก่อนนะ",
                ephemeral=True,
            )

        vol = max(1, min(volume, 200))
        embed = discord.Embed(
            title="🎛️ แผงควบคุม Soundboard ประจำแก๊ง",
            description=(
                f"กดปุ่มด้านล่างเพื่อสั่งบอทเปิดเสียงเอฟเฟกต์ในห้องคุยที่คุณอยู่ทันที! *(ความดัง `{vol}%`)*\n"
                f"📂 มีเสียงพร้อมเล่นทั้งหมด **{len(files)} ไฟล์**"
            ),
            color=discord.Color.purple(),
        )
        view = SoundboardView(self, files, volume=vol)
        await interaction.response.send_message(embed=embed, view=view)

    @app_commands.command(
        name="stop_sound",
        description="⏹️ สั่งหยุดเสียงและให้บอทออกจากห้องพูดคุยทันที",
    )
    async def stop_sound_cmd(self, interaction: discord.Interaction):
        vc = interaction.guild.voice_client if interaction.guild else None
        if not vc:
            return await interaction.response.send_message("🔇 ตอนนี้บอทไม่ได้เปิดเสียงอยู่ในห้องไหนเลย!", ephemeral=True)

        self.play_tokens[interaction.guild.id] = self.play_tokens.get(interaction.guild.id, 0) + 1
        if vc.is_playing():
            vc.stop()
        await vc.disconnect()
        await interaction.response.send_message("⏹️ หยุดเล่นเสียงและนำบอทออกจากห้องพูดคุยเรียบร้อยแล้ว!")

    @app_commands.command(name="vc_rename", description="✏️ เปลี่ยนชื่อห้องพูดคุยชั่วคราวของคุณแบบกวนๆ")
    @app_commands.describe(new_name="ชื่อห้องใหม่ที่ต้องการตั้ง")
    async def vc_rename(self, interaction: discord.Interaction, new_name: str):
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.response.send_message("❌ แกต้องอยู่ในห้องพูดคุยก่อนนะ!", ephemeral=True)

        vc = interaction.user.voice.channel
        if vc.id not in self.temp_channels:
            return await interaction.response.send_message("❌ เปลี่ยนชื่อได้เฉพาะห้องที่สร้างจากระบบ `➕ สร้างห้องคุย` เท่านั้นนะ!", ephemeral=True)

        await vc.edit(name=f"🔊 {new_name}")
        await interaction.response.send_message(f"✅ เปลี่ยนชื่อห้องเป็น **🔊 {new_name}** เรียบร้อย!")

    @app_commands.command(
        name="set_entrance_sound",
        description="🎺 ตั้งค่าไฟล์เสียงเปิดตัวและความดังเวลาเข้าห้องคุย (ใส่ไฟล์ในโฟลเดอร์ sounds/)",
    )
    @app_commands.describe(
        filename="ชื่อไฟล์เสียงในโฟลเดอร์ sounds เช่น spiderman.mp3 (เว้นว่างเพื่อปิดเสียง)",
        volume="ระดับความดัง 1-200% (ค่าเริ่มต้น 50% เพื่อไม่ให้หนวกหูเพื่อน)",
    )
    @app_commands.autocomplete(filename=sound_file_autocomplete)
    async def set_sound_cmd(self, interaction: discord.Interaction, filename: str = None, volume: int = None):
        if not filename:
            await set_entrance_sound(interaction.user.id, None)
            return await interaction.response.send_message("🔇 **ปิดเสียงเปิดตัวประจำตัวเรียบร้อยแล้ว!**", ephemeral=True)

        files = list_sound_files()
        if filename not in files:
            available = ", ".join(files) if files else "ยังไม่มีไฟล์ในโฟลเดอร์ sounds/"
            return await interaction.response.send_message(
                f"❌ ไม่พบไฟล์ `{filename}` ในโฟลเดอร์ `sounds/`\n📂 ไฟล์ที่มีตอนนี้: `{available}`",
                ephemeral=True,
            )

        await set_entrance_sound(interaction.user.id, filename)
        if volume is not None:
            vol = max(1, min(volume, 200))
            await set_entrance_volume(interaction.user.id, vol)
        else:
            vol = await get_entrance_volume(interaction.user.id)

        await interaction.response.send_message(
            f"🎺 ตั้งเสียงเปิดตัวเป็น **`{filename}`** (ความดัง **`{vol}%`**) เรียบร้อย! ลองเข้าห้องคุยดูได้เลย!"
        )

    @app_commands.command(
        name="remove_entrance_sound",
        description="🔇 ลบ/ปิดเสียงเปิดตัวประจำตัวของคุณ (หรือใช้ลบไฟล์เสียงออกจากระบบ)",
    )
    @app_commands.describe(
        delete_file="ใส่ชื่อไฟล์ในโฟลเดอร์ sounds/ หากต้องการลบไฟล์นั้นทิ้งออกจากเครื่อง (เว้นว่างถ้าแค่ปิดเสียงตัวเอง)"
    )
    async def remove_sound_cmd(self, interaction: discord.Interaction, delete_file: str = None):
        if delete_file:
            if not interaction.user.guild_permissions.administrator:
                return await interaction.response.send_message("❌ เฉพาะแอดมินเท่านั้นที่ลบไฟล์เสียงออกจากโฟลเดอร์ได้!", ephemeral=True)
            safe_name = os.path.basename(delete_file)
            target_path = os.path.join(SOUNDS_DIR, safe_name)
            if not os.path.exists(target_path):
                return await interaction.response.send_message(f"❌ ไม่พบไฟล์ `{safe_name}` ในโฟลเดอร์ `sounds/`!", ephemeral=True)
            os.remove(target_path)
            return await interaction.response.send_message(f"🗑️ ลบไฟล์เสียง **`{safe_name}`** ออกจากระบบเรียบร้อยแล้ว!")

        await set_entrance_sound(interaction.user.id, None)
        await interaction.response.send_message("🔇 **ลบ/ปิดเสียงเปิดตัวประจำตัวของคุณเรียบร้อยแล้ว!** ตอนเข้าห้องจะไม่มีเสียงเพลงขึ้นแล้วครับ")

    @app_commands.command(
        name="entrance_volume",
        description="🔉 ปรับลด/เพิ่มความดังของเสียงเปิดตัวเวลาเข้าห้องคุย (1% - 200%)",
    )
    @app_commands.describe(
        volume="ระดับความดังเป็นเปอร์เซ็นต์ (เช่น 20 = เบาๆ, 50 = พอดี, 100 = ดังปกติ)",
        member="ปรับความดังเสียงเปิดตัวของเพื่อนคนอื่น (ถ้าเสียงเพื่อนดังเกินไป)",
    )
    async def entrance_volume_cmd(self, interaction: discord.Interaction, volume: int, member: discord.Member = None):
        vol = max(1, min(volume, 200))
        target = member or interaction.user
        await set_entrance_volume(target.id, vol)
        await interaction.response.send_message(
            f"🔉 ปรับความดังเสียงเปิดตัวของ **{target.display_name}** เป็น **`{vol}%`** เรียบร้อยแล้ว!"
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceMagicCog(bot))
