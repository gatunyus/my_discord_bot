import discord
from discord import app_commands, ui
from discord.ext import commands, tasks
import aiosqlite
import re
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
from database import DB_PATH, get_guild_config, set_guild_config

TH_TZ = ZoneInfo("Asia/Bangkok")

IGNORED_ACTIVITIES = {
    "Custom Status",
    "Spotify",
    "Hang Status",
    "Chilling",
}

MUSIC_APP_NAMES = {
    "spotify",
    "youtube music",
    "apple music",
    "soundcloud",
    "tidal",
    "deezer",
    "cider",
    "joox",
    "musicbee",
    "foobar2000",
    "aimp",
    "winamp",
    "itunes",
    "amazon music",
    "qobuz",
    "last.fm",
}

IDLE_MUSIC_STATES = {
    "idle",
    "paused",
    "browsing",
    "not playing",
    "nothing playing",
    "home",
    "searching",
    "none",
}

# รายชื่อค่ายเกม/แพลตฟอร์ม เพื่อไม่ให้ตัดคำว่า Launcher/Client ทิ้งจนเหลือแต่ชื่อค่ายลอยๆ
PLATFORM_PREFIXES = {
    "riot",
    "epic",
    "epic games",
    "steam",
    "rockstar",
    "rockstar games",
    "ubisoft",
    "ea",
    "garena",
    "hoyoverse",
    "hoyoplay",
    "battle.net",
    "battlenet",
    "blizzard",
    "xbox",
    "gog",
    "curseforge",
    "netmarble",
    "netease",
    "purple",
    "stove",
}

# ชื่อมาตรฐานของเกมยอดฮิต (สำหรับบังคับตัวพิมพ์เล็ก-ใหญ่ให้เป๊ะเสมอ)
CANONICAL_TITLES = {
    "VALORANT",
    "Roblox",
    "Roblox Studio",
    "Minecraft",
    "Minecraft Dungeons",
    "Minecraft Legends",
    "Identity V",
    "osu!",
    "League of Legends",
    "Teamfight Tactics",
    "Counter-Strike 2",
    "FiveM",
    "Grand Theft Auto V",
    "PUBG: BATTLEGROUNDS",
    "Apex Legends",
    "Overwatch 2",
    "Genshin Impact",
    "Honkai: Star Rail",
    "Zenless Zone Zero",
    "Wuthering Waves",
    "Dota 2",
    "Rainbow Six Siege",
    "Dead by Daylight",
    "Call of Duty",
    "FC Online",
    "Marvel Rivals",
    "NARAKA: BLADEPOINT",
    "Delta Force",
    "Arena Breakout: Infinite",
    "Once Human",
    "Monster Hunter Wilds",
    "Monster Hunter: World",
    "Monster Hunter Rise",
    "Stardew Valley",
    "Fortnite",
    "Rocket League",
    "VRChat",
}


def normalize_game_name(raw_name: str) -> str:
    """รวมชื่อเกมเดียวกันที่มาจากคนละ Launcher / Tracker / ตัวพิมพ์เล็ก-ใหญ่ ให้เป็นชื่อเดียวด้วย Python"""
    if not raw_name:
        return ""

    # 1. ลบสัญลักษณ์เครื่องหมายการค้าและช่องว่างเกิน
    cleaned = re.sub(r"[®™©\u200b]", "", raw_name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return ""

    # เช็ค Launcher พิเศษของเกมก่อนตัดคำ (เช่น Lunar Client / Bloxstrap)
    low_raw = cleaned.casefold()
    if low_raw in {
        "bloxstrap",
        "fishtrap",
        "voidstrap",
    }:
        return "Roblox"
    if low_raw in {
        "lunar client",
        "lunar",
        "badlion client",
        "badlion",
        "feather client",
        "feather",
        "prism launcher",
        "multimc",
        "modrinth",
        "modrinth app",
        "tlauncher",
        "sklauncher",
        "atlauncher",
        "gdlauncher",
        "labymod",
        "hmcl",
        "pcl2",
    }:
        return "Minecraft"

    # 2. ตัดคำต่อท้ายพวก Tracker App / Overlay / (64-bit) / DirectX / Launcher ของตัวเกมออกอัตโนมัติ
    for _ in range(2):
        # ตัดวงเล็บต่อท้าย เช่น (64-bit), (DX12), (Steam), (PC), [Global]
        cleaned = re.sub(
            r"\s*[\(\[](?:64-?bit|32-?bit|x64|x86|dx\s*\d+|directx\s*\d+|vulkan|opengl|steam|epic(?:\s+games)?|gog|riot|pc|mobile|beta|playtest|global|sea|asia|th|en|kr|jp|cn|tw|client|launcher|app)[\)\]]$",
            "",
            cleaned,
            flags=re.IGNORECASE,
        ).strip()

        # ตัดขีดต่อท้าย เช่น - DirectX 12, - Vulkan, - 64-bit
        cleaned = re.sub(
            r"\s+[-–—]\s*(?:directx\s*\d+|dx\s*\d+|vulkan|opengl|64-?bit|32-?bit|steam|epic)$",
            "",
            cleaned,
            flags=re.IGNORECASE,
        ).strip()

        # ตัดคำต่อท้ายสายแอปช่วยเล่น เช่น "Valorant Tracker App", "Apex Legends Tracker", "... Overlay"
        stripped_tracker = re.sub(
            r"\s+(?:stat\s+|stats\s+|live\s+|in-game\s+)?(?:tracker|companion|overlay|helper|assistant)(?:\s+app)?$",
            "",
            cleaned,
            flags=re.IGNORECASE,
        ).strip()
        if len(stripped_tracker) >= 2:
            cleaned = stripped_tracker

        # ตัดคำต่อท้าย Launcher / Client / Player เฉพาะกรณีที่ข้างหน้าเป็นชื่อเกม (ไม่ใช่ชื่อค่าย เช่น Riot Client)
        m_launcher = re.match(
            r"^(.+?)\s+(?:game\s+)?(?:launcher|client|player)$",
            cleaned,
            flags=re.IGNORECASE,
        )
        if m_launcher:
            base = m_launcher.group(1).strip()
            if len(base) >= 2 and base.casefold() not in PLATFORM_PREFIXES:
                cleaned = base

    low = cleaned.casefold()
    compact = re.sub(r"[^a-z0-9ก-๙]+", "", low)

    # 3. จับคู่เกมยอดฮิตที่มีหลาย Launcher / ชื่อหลายแบบ ให้รวมเป็นชื่อมาตรฐานเดียวกัน
    if "valorant" in low:
        return "VALORANT"
    if "roblox studio" in low:
        return "Roblox Studio"
    if low == "roblox" or low.startswith("roblox ") or compact == "roblox":
        return "Roblox"
    if "minecraft dungeons" in low:
        return "Minecraft Dungeons"
    if "minecraft legends" in low:
        return "Minecraft Legends"
    if "minecraft" in low:
        return "Minecraft"
    if compact == "identityv" or low.startswith("identity v"):
        return "Identity V"
    if low.startswith("osu!") or compact in {"osu", "osulazer"}:
        return "osu!"
    if "teamfight tactics" in low or compact == "tft":
        return "Teamfight Tactics"
    if "league of legends" in low or compact in {"leagueoflegends", "leagueclient"}:
        return "League of Legends"
    if low.startswith("counter-strike") or compact in {"cs2", "csgo", "counterstrike2"}:
        return "Counter-Strike 2"
    if low.startswith("fivem"):
        return "FiveM"
    if low.startswith("grand theft auto v") or compact in {"gtav", "gta5", "gtaonline"}:
        return "Grand Theft Auto V"
    if low.startswith("pubg") or "playerunknown's battlegrounds" in low:
        return "PUBG: BATTLEGROUNDS"
    if low.startswith("apex legends"):
        return "Apex Legends"
    if low.startswith("overwatch"):
        return "Overwatch 2"
    if "genshin impact" in low or compact == "genshinimpact" or cleaned in {"原神", "원신"}:
        return "Genshin Impact"
    if "star rail" in low or compact == "honkaistarrail" or cleaned == "崩坏：星穹铁道":
        return "Honkai: Star Rail"
    if "zenless zone zero" in low or compact == "zenlesszonezero":
        return "Zenless Zone Zero"
    if "wuthering waves" in low or compact == "wutheringwaves":
        return "Wuthering Waves"
    if low.startswith("dota 2") or compact == "dota2":
        return "Dota 2"
    if "rainbow six" in low and "siege" in low:
        return "Rainbow Six Siege"
    if low.startswith("dead by daylight"):
        return "Dead by Daylight"
    if low in {"call of duty", "call of duty hq", "cod"} or "warzone" in low:
        return "Call of Duty"
    if "fc online" in low or "fifa online 4" in low:
        return "FC Online"
    if low.startswith("marvel rivals"):
        return "Marvel Rivals"
    if "naraka" in low and "bladepoint" in low:
        return "NARAKA: BLADEPOINT"
    if low.startswith("delta force"):
        return "Delta Force"
    if low.startswith("arena breakout"):
        return "Arena Breakout: Infinite"
    if low.startswith("once human"):
        return "Once Human"
    if low.startswith("monster hunter wilds"):
        return "Monster Hunter Wilds"
    if "monster hunter: world" in low or "monster hunter world" in low:
        return "Monster Hunter: World"
    if low.startswith("monster hunter rise"):
        return "Monster Hunter Rise"
    if low.startswith("stardew valley") or compact == "stardewvalley":
        return "Stardew Valley"
    if low.startswith("fortnite"):
        return "Fortnite"
    if low.startswith("rocket league"):
        return "Rocket League"
    if low.startswith("vrchat"):
        return "VRChat"

    return cleaned


def make_game_key(game_name: str) -> str:
    """คีย์กลางสำหรับรวมเกมเดียวกันที่ต่างแค่ตัวพิมพ์ใหญ่-เล็ก หรือเว้นวรรค"""
    norm = normalize_game_name(game_name)
    key = re.sub(r"[\s\-_.:!']+", "", norm).casefold()
    return key or norm.casefold()


def _display_name_score(name: str, seconds: int) -> tuple[int, int, int]:
    """ให้คะแนนชื่อที่ใช้แสดงผล (เลือกชื่อมาตรฐาน หรือชื่อที่มีตัวพิมพ์ใหญ่-เล็กสวยงามก่อนตัวพิมพ์ใหญ่ล้วน)"""
    if name in CANONICAL_TITLES:
        return (3, 1, seconds)
    is_mixed_case = not name.isupper() and not name.islower()
    has_space = 1 if " " in name else 0
    return (2 if is_mixed_case else 1, has_space, seconds)


def aggregate_guild_game_stats(rows: list[tuple[int, str, int]]):
    """
    รับข้อมูลดิบ (user_id, game_name, seconds_played) ทั้งเซิร์ฟเวอร์มากรองและรวมยอดด้วย Python
    คืนค่า:
      - top_gamers: [(user_id, total_sec, [(display_game, sec), ...]), ...]
      - top_games:  [(display_game, total_sec, [(user_id, sec), ...]), ...]
    """
    user_game_sec: dict[tuple[int, str], int] = defaultdict(int)
    key_name_sec: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for user_id, raw_name, sec in rows:
        if not raw_name or raw_name in IGNORED_ACTIVITIES or sec <= 0:
            continue
        norm_name = normalize_game_name(raw_name)
        if not norm_name or norm_name in IGNORED_ACTIVITIES:
            continue
        g_key = make_game_key(norm_name)
        user_game_sec[(user_id, g_key)] += sec
        key_name_sec[g_key][norm_name] += sec

    # เลือกชื่อแสดงผลที่เหมาะสมที่สุดของแต่ละ g_key
    key_to_display: dict[str, str] = {}
    for g_key, candidates in key_name_sec.items():
        best_name = max(candidates.items(), key=lambda kv: _display_name_score(kv[0], kv[1]))[0]
        key_to_display[g_key] = best_name

    # รวมยอดรายบุคคล และรายเกม
    per_user_games: dict[int, list[tuple[str, int]]] = defaultdict(list)
    per_game_players: dict[str, list[tuple[int, int]]] = defaultdict(list)

    for (user_id, g_key), total_s in user_game_sec.items():
        per_user_games[user_id].append((key_to_display[g_key], total_s))
        per_game_players[g_key].append((user_id, total_s))

    top_gamers = []
    for user_id, g_list in per_user_games.items():
        g_list.sort(key=lambda x: x[1], reverse=True)
        total_u = sum(x[1] for x in g_list)
        top_gamers.append((user_id, total_u, g_list))
    top_gamers.sort(key=lambda x: x[1], reverse=True)

    top_games = []
    for g_key, p_list in per_game_players.items():
        p_list.sort(key=lambda x: x[1], reverse=True)
        total_g = sum(x[1] for x in p_list)
        top_games.append((key_to_display[g_key], total_g, p_list))
    top_games.sort(key=lambda x: x[1], reverse=True)

    return top_gamers, top_games


def clean_artist_name(raw_artist: str) -> str:
    """จัดระเบียบชื่อศิลปินจาก Spotify / YouTube Music / Apple Music ให้รวมเป็นชื่อเดียวกัน"""
    if not raw_artist:
        return "ไม่ระบุศิลปิน"
    cleaned = re.sub(r"[\u200b]", "", raw_artist).strip()
    cleaned = re.sub(r"^(?:by|ศิลปิน:?)\s+", "", cleaned, flags=re.IGNORECASE).strip()
    # แยกกรณีมีหลายศิลปินคั่นด้วย '; ' (Spotify) หรือ ' • ' (YouTube Music แถมชื่ออัลบั้ม)
    if "; " in cleaned:
        cleaned = cleaned.split("; ")[0].strip()
    if " • " in cleaned:
        cleaned = cleaned.split(" • ")[0].strip()
    # ตัดคำว่า " - Topic" ของช่องศิลปินอัตโนมัติจาก YouTube Music
    cleaned = re.sub(r"\s*-\s*topic$", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:60] if cleaned else "ไม่ระบุศิลปิน"


def clean_song_title(raw_title: str) -> str:
    """ตัดคำต่อท้าย (Official MV / Official Audio / Lyrics) เพื่อรวมเพลงเดียวกันจากหลายแอป"""
    if not raw_title:
        return ""
    cleaned = re.sub(r"[\u200b]", "", raw_title).strip()
    cleaned = re.sub(
        r"\s*[\(\[](?:official\s*(?:music\s*)?(?:video|mv|audio|visualizer)|mv|m/v|lyric(?:s)?(?:\s*video)?|audio)[\)\]]",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:80]


def extract_music_track(act: discord.BaseActivity) -> tuple[str, str] | None:
    """ดึงข้อมูล (artist_name, song_title) จาก Spotify หรือแอปฟังเพลงอื่นๆ บน Discord"""
    if isinstance(act, discord.Spotify):
        song = clean_song_title(act.title or "")
        if not song:
            return None
        raw_artist = act.artists[0] if getattr(act, "artists", None) and act.artists[0] else (act.artist or "")
        artist = clean_artist_name(raw_artist)
        return (artist, song)

    act_name = (getattr(act, "name", None) or "").strip()
    act_name_low = act_name.casefold()
    act_type = getattr(act, "type", None)

    if act_type != discord.ActivityType.listening and act_name_low not in MUSIC_APP_NAMES:
        return None

    details = (getattr(act, "details", None) or "").strip()
    state = (getattr(act, "state", None) or "").strip()

    if details.casefold() in IDLE_MUSIC_STATES or state.casefold() in {"paused", "หยุดชั่วคราว"}:
        return None

    if details:
        song = clean_song_title(details)
        artist = clean_artist_name(state) if state and state.casefold() not in IDLE_MUSIC_STATES else "ไม่ระบุศิลปิน"
        return (artist, song) if song else None

    if act_type == discord.ActivityType.listening and act_name and act_name_low not in MUSIC_APP_NAMES:
        if act_name_low in IDLE_MUSIC_STATES:
            return None
        song = clean_song_title(act_name)
        artist = clean_artist_name(state) if state else "ไม่ระบุศิลปิน"
        return (artist, song) if song else None

    return None


def _make_text_key(text: str) -> str:
    key = re.sub(r"[\s\-_.:!'\"()\[\]]+", "", text).casefold()
    return key or text.casefold()


def aggregate_guild_music_stats(rows: list[tuple[int, str, str, int]]):
    """
    รวมสถิติการฟังเพลงด้วย Python จากข้อมูล (user_id, artist_name, song_title, seconds_listened)
    คืนค่า:
      - top_listeners: [(user_id, total_sec, [(artist, sec), ...], [(song, artist, sec), ...]), ...]
      - top_artists:   [(artist, total_sec, [(user_id, sec), ...]), ...]
      - top_songs:     [(song, artist, total_sec, [(user_id, sec), ...]), ...]
    """
    user_total: dict[int, int] = defaultdict(int)
    user_artist_sec: dict[tuple[int, str], int] = defaultdict(int)
    user_song_sec: dict[tuple[int, str, str], int] = defaultdict(int)

    artist_names: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    song_names: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for user_id, raw_artist, raw_song, sec in rows:
        if sec <= 0 or not raw_song:
            continue
        artist = clean_artist_name(raw_artist)
        song = clean_song_title(raw_song)
        if not song:
            continue

        a_key = _make_text_key(artist)
        s_key = _make_text_key(song)

        user_total[user_id] += sec
        user_artist_sec[(user_id, a_key)] += sec
        user_song_sec[(user_id, a_key, s_key)] += sec
        artist_names[a_key][artist] += sec
        song_names[(a_key, s_key)][song] += sec

    artist_display = {
        ak: max(cands.items(), key=lambda kv: _display_name_score(kv[0], kv[1]))[0]
        for ak, cands in artist_names.items()
    }
    song_display = {
        sk: max(cands.items(), key=lambda kv: _display_name_score(kv[0], kv[1]))[0]
        for sk, cands in song_names.items()
    }

    per_user_artists: dict[int, list[tuple[str, int]]] = defaultdict(list)
    per_artist_users: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for (uid, a_key), sec in user_artist_sec.items():
        per_user_artists[uid].append((artist_display[a_key], sec))
        per_artist_users[a_key].append((uid, sec))

    per_user_songs: dict[int, list[tuple[str, str, int]]] = defaultdict(list)
    per_song_users: dict[tuple[str, str], list[tuple[int, int]]] = defaultdict(list)
    for (uid, a_key, s_key), sec in user_song_sec.items():
        per_user_songs[uid].append((song_display[(a_key, s_key)], artist_display[a_key], sec))
        per_song_users[(a_key, s_key)].append((uid, sec))

    top_listeners = []
    for uid, t_sec in user_total.items():
        u_artists = sorted(per_user_artists[uid], key=lambda x: x[1], reverse=True)
        u_songs = sorted(per_user_songs[uid], key=lambda x: x[2], reverse=True)
        top_listeners.append((uid, t_sec, u_artists, u_songs))
    top_listeners.sort(key=lambda x: x[1], reverse=True)

    top_artists = []
    for a_key, u_list in per_artist_users.items():
        u_list.sort(key=lambda x: x[1], reverse=True)
        top_artists.append((artist_display[a_key], sum(x[1] for x in u_list), u_list))
    top_artists.sort(key=lambda x: x[1], reverse=True)

    top_songs = []
    for (a_key, s_key), u_list in per_song_users.items():
        u_list.sort(key=lambda x: x[1], reverse=True)
        top_songs.append((song_display[(a_key, s_key)], artist_display[a_key], sum(x[1] for x in u_list), u_list))
    top_songs.sort(key=lambda x: x[2], reverse=True)

    return top_listeners, top_artists, top_songs


def get_day_night_emoji() -> str:
    """ตอนเช้า/กลางวัน (06:00 - 17:59 เวลาไทย) ใช้ ☀️ ส่วนตอนกลางคืน (18:00 - 05:59) ใช้ 🌙"""
    hour = datetime.now(TH_TZ).hour
    return "☀️" if 6 <= hour < 18 else "🌙"


def get_rank_badge(rank: int) -> str:
    if rank == 1:
        return "🥇"
    if rank == 2:
        return "🥈"
    if rank == 3:
        return "🥉"
    return f"`#{rank}`"


def format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} วินาที"
    minutes = seconds // 60
    hours = minutes // 60
    rem_mins = minutes % 60
    if hours > 0:
        return f"{hours:,} ชม. {rem_mins} นาที"
    return f"{minutes} นาที"


class RefreshStatsButton(ui.DynamicItem[ui.Button], template=r"stats_refresh_btn"):
    """ปุ่มกดรีเฟรชบอร์ดสถิติแบบ Persistent รองรับทั้งข้อความเก่าและ Layout ใหม่"""

    def __init__(self) -> None:
        super().__init__(
            ui.Button(
                label="🔄 รีเฟรชสถิติ",
                style=discord.ButtonStyle.primary,
                custom_id="stats_refresh_btn",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button, match):
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        board_view = await build_guild_stats_view(interaction.guild)
        if interaction.message and interaction.message.flags.components_v2:
            await interaction.response.edit_message(view=board_view)
        else:
            # กรณีกดจากบอร์ดรูปแบบ Embed เดิม ให้เปลี่ยนเป็นบอร์ดแบบ Block ใหม่ทันที
            await interaction.response.defer()
            new_msg = await interaction.channel.send(view=board_view)
            config = await get_guild_config(interaction.guild_id)
            if config.get("stats_channel_id") == interaction.channel_id:
                await set_guild_config(interaction.guild_id, stats_message_id=new_msg.id)
            try:
                await interaction.message.delete()
            except Exception:
                pass


class MyStatsButton(ui.DynamicItem[ui.Button], template=r"stats_me_btn"):
    """ปุ่มกดดูสถิติส่วนตัวของตัวเองแบบซ่อนข้อความ (Ephemeral)"""

    def __init__(self) -> None:
        super().__init__(
            ui.Button(
                label="👤 สถิติส่วนตัวของฉัน",
                style=discord.ButtonStyle.secondary,
                custom_id="stats_me_btn",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button, match):
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        cog: ActivityStatsCog = interaction.client.get_cog("ActivityStatsCog")
        if cog:
            member_view = await cog.build_member_stats_view(interaction.guild_id, interaction.user)
            await interaction.response.send_message(view=member_view, ephemeral=True)


async def build_guild_stats_view(guild: discord.Guild) -> ui.LayoutView:
    """สร้างกระดานสถิติแบบแบ่งเป็น Block การ์ดแยกหมวดหมู่สวยงาม (Components V2)"""
    guild_id = guild.id
    dn_emoji = get_day_night_emoji()
    now_ts = int(datetime.now().timestamp())

    async with aiosqlite.connect(DB_PATH) as db:
        # ดึงสถิติเกมทั้งหมดในเซิร์ฟเวอร์มากรองและรวม Launcher/Tracker ซ้ำด้วย Python
        async with db.execute(
            """
            SELECT user_id, game_name, seconds_played
            FROM game_stats
            WHERE guild_id = ?
            """,
            (guild_id,),
        ) as cur:
            raw_game_rows = await cur.fetchall()

        top_gamers, top_games = aggregate_guild_game_stats(raw_game_rows)

        # 1. รายบุคคล: สมาชิกแต่ละคนเล่นเกมไหนบ้าง (สูงสุด 8 อันดับแรก)
        member_blocks = []
        for i, (u_id, t_sec, user_games) in enumerate(top_gamers[:8], start=1):
            games_str = "  •  ".join([f"**{gn}** `{format_duration(s)}`" for gn, s in user_games[:4]])
            badge = get_rank_badge(i)
            member_blocks.append(
                f"> {badge} <@{u_id}>  •  รวม `{format_duration(t_sec)}`\n"
                f"> 🕹️ {games_str}"
            )

        # 2. หมวดเกมยอดฮิต: แต่ละเกมมีใครเล่นบ้าง (สูงสุด 6 เกมแรก)
        game_blocks = []
        for g_name, total_sec, players in top_games[:6]:
            players_str = "  •  ".join([f"<@{uid}> `{format_duration(sec)}`" for uid, sec in players[:5]]) or "-"
            game_blocks.append(
                f"> 🎮 **{g_name}**  •  รวม `{format_duration(total_sec)}`\n"
                f"> 👥 {players_str}"
            )

        # 3. ดึงสถิติการฟังเพลงทั้งหมดในเซิร์ฟเวอร์มากรองด้วย Python
        raw_music_rows = []
        try:
            async with db.execute(
                """
                SELECT user_id, artist_name, song_title, seconds_listened
                FROM music_stats
                WHERE guild_id = ?
                """,
                (guild_id,),
            ) as cur:
                raw_music_rows = await cur.fetchall()
        except Exception:
            raw_music_rows = []

        top_listeners, _top_artists, top_songs = aggregate_guild_music_stats(raw_music_rows)

        music_listener_blocks = []
        for i, (u_id, m_sec, u_artists, _u_songs) in enumerate(top_listeners[:5], start=1):
            artists_str = "  •  ".join([f"**{an}** `{format_duration(s)}`" for an, s in u_artists[:2]])
            badge = get_rank_badge(i)
            music_listener_blocks.append(
                f"> {badge} <@{u_id}>  •  รวม `{format_duration(m_sec)}`\n"
                f"> 🎵 {artists_str}"
            )

        song_lines = []
        for song_t, artist_t, s_total, s_players in top_songs[:3]:
            listeners_str = ", ".join([f"<@{uid}>" for uid, _ in s_players[:3]])
            song_lines.append(
                f"> 🎶 **{song_t}** — *{artist_t}*  •  `{format_duration(s_total)}` ({listeners_str})"
            )

        # 4. คนสิงห้องพูดคุยนานสุด
        async with db.execute(
            """
            SELECT user_id, voice_seconds
            FROM member_activity
            WHERE guild_id = ? AND voice_seconds > 0
            ORDER BY voice_seconds DESC
            LIMIT 5
            """,
            (guild_id,),
        ) as cur:
            top_voice = await cur.fetchall()

        # 5. คนพิมพ์แชทเยอะสุด
        async with db.execute(
            """
            SELECT user_id, message_count
            FROM member_activity
            WHERE guild_id = ? AND message_count > 0
            ORDER BY message_count DESC
            LIMIT 5
            """,
            (guild_id,),
        ) as cur:
            top_chat = await cur.fetchall()

    view = ui.LayoutView(timeout=None)

    # หัวข้อกระดานด้านบนแบบคลีน (ไม่มีคำอธิบายรกๆ)
    view.add_item(ui.TextDisplay(f"## {dn_emoji} กระดานสถิติชาวแก๊ง"))

    # ── BLOCK 1: สมาชิกแต่ละคนเล่นเกมไหนบ้าง ──
    member_text = (
        "\n\n".join(member_blocks)[:700]
        if member_blocks
        else "-# กำลังเริ่มเก็บสถิติเวลาเล่นเกม..."
    )
    view.add_item(
        ui.Container(
            ui.TextDisplay("### 👤 สมาชิกแต่ละคนเล่นเกมไหนบ้าง"),
            ui.Separator(spacing=discord.SeparatorSpacing.small),
            ui.TextDisplay(member_text),
            accent_colour=discord.Color.from_rgb(245, 158, 11),  # Amber Gold
        )
    )

    # ── BLOCK 2: หมวดเกมยอดฮิต ──
    games_text = (
        "\n\n".join(game_blocks)[:700]
        if game_blocks
        else "-# ยังไม่มีข้อมูลหมวดเกมยอดฮิต"
    )
    view.add_item(
        ui.Container(
            ui.TextDisplay("### 🔥 หมวดเกมยอดฮิต"),
            ui.Separator(spacing=discord.SeparatorSpacing.small),
            ui.TextDisplay(games_text),
            accent_colour=discord.Color.from_rgb(249, 115, 22),  # Coral Orange
        )
    )

    # ── BLOCK 3: สายฟังเพลง & เพลงฮิตประจำกลุ่ม ──
    if music_listener_blocks:
        music_parts = ["\n\n".join(music_listener_blocks)]
        if song_lines:
            music_parts.append("**💿 เพลงฮิตประจำกลุ่ม**\n" + "\n".join(song_lines))
        music_text = "\n\n".join(music_parts)[:700]
    else:
        music_text = "-# ยังไม่มีสถิติการฟังเพลง (เชื่อม Spotify หรือแอปเพลงใน Discord เพื่อเก็บสถิติได้เลย!)"

    view.add_item(
        ui.Container(
            ui.TextDisplay("### 🎧 สายฟังเพลง & ศิลปินยอดฮิต"),
            ui.Separator(spacing=discord.SeparatorSpacing.small),
            ui.TextDisplay(music_text),
            accent_colour=discord.Color.from_rgb(236, 72, 153),  # Rose Pink
        )
    )

    # ── BLOCK 4: สิงสถิตห้องพูดคุยนานสุด ──
    if top_voice:
        voice_text = "\n".join(
            [f"{get_rank_badge(i)} <@{u_id}> — `{format_duration(v_sec)}`" for i, (u_id, v_sec) in enumerate(top_voice, 1)]
        )[:500]
    else:
        voice_text = "-# ยังไม่มีสถิติคนเข้าห้องพูดคุย"

    view.add_item(
        ui.Container(
            ui.TextDisplay("### 🔊 สิงสถิตห้องพูดคุยนานสุด"),
            ui.Separator(spacing=discord.SeparatorSpacing.small),
            ui.TextDisplay(voice_text),
            accent_colour=discord.Color.from_rgb(16, 185, 129),  # Emerald
        )
    )

    # ── BLOCK 5: นักแชทนิ้วทองคำ ──
    if top_chat:
        chat_text = "\n".join(
            [f"{get_rank_badge(i)} <@{u_id}> — `{m_cnt:,} ข้อความ`" for i, (u_id, m_cnt) in enumerate(top_chat, 1)]
        )[:500]
    else:
        chat_text = "-# ยังไม่มีสถิติข้อความแชท"

    view.add_item(
        ui.Container(
            ui.TextDisplay("### 💬 นักแชทนิ้วทองคำ"),
            ui.Separator(spacing=discord.SeparatorSpacing.small),
            ui.TextDisplay(chat_text),
            accent_colour=discord.Color.from_rgb(88, 101, 242),  # Blurple
        )
    )

    # เวลาอัปเดตล่าสุดด้านล่างสุด + ปุ่มกด
    view.add_item(ui.TextDisplay(f"-# {dn_emoji} อัปเดตล่าสุด <t:{now_ts}:t> (<t:{now_ts}:R>)"))
    view.add_item(ui.ActionRow(RefreshStatsButton(), MyStatsButton()))

    return view


class ActivityStatsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.track_activity_loop.start()
        self.update_stats_board_loop.start()

    def cog_unload(self):
        self.track_activity_loop.cancel()
        self.update_stats_board_loop.cancel()

    @tasks.loop(seconds=60)
    async def track_activity_loop(self):
        """เก็บสถิติเงียบๆ ในฐานข้อมูลทุก 1 นาทีว่าใครกำลังเล่นเกมอะไร หรือสิงอยู่ในห้องพูดคุยนานแค่ไหน"""
        async with aiosqlite.connect(DB_PATH) as db:
            for guild in self.bot.guilds:
                for member in guild.members:
                    if member.bot:
                        continue

                    # 1. เช็คว่าอยู่ในห้องพูดคุย (Voice Channel) และไม่ได้อยู่ห้อง AFK
                    if member.voice and member.voice.channel and member.voice.channel != guild.afk_channel:
                        await db.execute(
                            """
                            INSERT INTO member_activity (guild_id, user_id, voice_seconds, message_count)
                            VALUES (?, ?, 60, 0)
                            ON CONFLICT(guild_id, user_id)
                            DO UPDATE SET voice_seconds = voice_seconds + 60
                            """,
                            (guild.id, member.id),
                        )

                    # 2. เช็คการฟังเพลง (Spotify / YouTube Music / Apple Music ฯลฯ) — นับสูงสุด 60 วินาทีต่อนาที
                    recorded_music = False
                    for act in member.activities:
                        track_info = extract_music_track(act)
                        if track_info and not recorded_music:
                            recorded_music = True
                            artist_name, song_title = track_info
                            await db.execute(
                                """
                                INSERT INTO music_stats (guild_id, user_id, artist_name, song_title, seconds_listened)
                                VALUES (?, ?, ?, ?, 60)
                                ON CONFLICT(guild_id, user_id, artist_name, song_title)
                                DO UPDATE SET seconds_listened = seconds_listened + 60
                                """,
                                (guild.id, member.id, artist_name, song_title),
                            )

                    # 3. เช็คกิจกรรม/เกมที่กำลังเปิดเล่นอยู่ (Playing / Competing) พร้อมกรองรวม Launcher ด้วย Python
                    recorded_games = set()
                    for act in member.activities:
                        if act.type in (discord.ActivityType.playing, discord.ActivityType.competing):
                            raw_name = (act.name or "").strip()
                            if (
                                not raw_name
                                or raw_name in IGNORED_ACTIVITIES
                                or raw_name.casefold() in MUSIC_APP_NAMES
                            ):
                                continue
                            game_name = normalize_game_name(raw_name)
                            if (
                                not game_name
                                or game_name in IGNORED_ACTIVITIES
                                or game_name.casefold() in MUSIC_APP_NAMES
                            ):
                                continue
                            game_key = make_game_key(game_name)
                            # ป้องกันการบวกเวลาเบิ้ลกรณีเปิดทั้งตัวเกมและแอป Tracker/Launcher พร้อมกันในนาทีเดียว
                            if game_key not in recorded_games:
                                recorded_games.add(game_key)
                                await db.execute(
                                    """
                                    INSERT INTO game_stats (guild_id, user_id, game_name, seconds_played)
                                    VALUES (?, ?, ?, 60)
                                    ON CONFLICT(guild_id, user_id, game_name)
                                    DO UPDATE SET seconds_played = seconds_played + 60
                                    """,
                                    (guild.id, member.id, game_name),
                                )
            await db.commit()

    @tasks.loop(minutes=30)
    async def update_stats_board_loop(self):
        """อัปเดตบอร์ดสถิติและเปลี่ยนอีโมจิ ☀️/🌙 หน้าหมวด Statistics ทุกๆ 30 นาที"""
        for guild in self.bot.guilds:
            await self.refresh_guild_stats_channel(guild)

    async def refresh_guild_stats_channel(self, guild: discord.Guild):
        config = await get_guild_config(guild.id)
        stats_ch_id = config.get("stats_channel_id")
        stats_msg_id = config.get("stats_message_id")
        dn_emoji = get_day_night_emoji()

        channel = guild.get_channel(stats_ch_id) if stats_ch_id else None
        if not channel:
            for ch in guild.text_channels:
                if ch.name in ("📊・stats", "stats") or "・stats" in ch.name or "สถิติชาวแก๊ง" in ch.name:
                    channel = ch
                    await set_guild_config(guild.id, stats_channel_id=channel.id)
                    break

        if not channel:
            return

        # ถ้าชื่อห้องยังเป็นชื่อเก่า (📊・สถิติชาวแก๊ง) ให้เปลี่ยนเป็น 📊・stats ตาม Layout ปัจจุบัน
        if "สถิติชาวแก๊ง" in channel.name and channel.name != "📊・stats":
            try:
                await channel.edit(name="📊・stats")
            except Exception:
                pass

        # เปลี่ยนอีโมจิ ☀️ / 🌙 เฉพาะหน้าหมวด Statistics ตาม Layout ปัจจุบัน (☀️ Statistics / 🌙 Statistics)
        if channel.category and ("Statistics" in channel.category.name or "สถิติ" in channel.category.name):
            target_cat_name = f"{dn_emoji} Statistics"
            if channel.category.name != target_cat_name:
                try:
                    await channel.category.edit(name=target_cat_name)
                except Exception:
                    pass

        board_view = await build_guild_stats_view(guild)

        if stats_msg_id:
            try:
                msg = await channel.fetch_message(stats_msg_id)
                if msg.flags.components_v2:
                    await msg.edit(view=board_view)
                    return
                else:
                    # อัปเกรดจากข้อความ Embed รูปแบบเก่าเป็น Layout แบบ Block ใหม่
                    try:
                        await msg.delete()
                    except Exception:
                        pass
            except Exception:
                pass

        # ส่งบอร์ดใหม่และบันทึก ID ไว้สำหรับอัปเดตรอบถัดไป
        try:
            new_msg = await channel.send(view=board_view)
            await set_guild_config(guild.id, stats_channel_id=channel.id, stats_message_id=new_msg.id)
        except Exception:
            pass

    @track_activity_loop.before_loop
    @update_stats_board_loop.before_loop
    async def before_loops(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                INSERT INTO member_activity (guild_id, user_id, voice_seconds, message_count)
                VALUES (?, ?, 0, 1)
                ON CONFLICT(guild_id, user_id)
                DO UPDATE SET message_count = message_count + 1
                """,
                (message.guild.id, message.author.id),
            )
            await db.commit()

    async def build_member_stats_view(self, guild_id: int, member: discord.Member) -> ui.LayoutView:
        dn_emoji = get_day_night_emoji()
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT voice_seconds, message_count FROM member_activity WHERE guild_id = ? AND user_id = ?",
                (guild_id, member.id),
            ) as cur:
                act_row = await cur.fetchone()
            voice_sec = act_row[0] if act_row else 0
            msg_count = act_row[1] if act_row else 0

            async with db.execute(
                """
                SELECT game_name, seconds_played
                FROM game_stats
                WHERE guild_id = ? AND user_id = ?
                """,
                (guild_id, member.id),
            ) as cur:
                raw_games = await cur.fetchall()

            raw_music = []
            try:
                async with db.execute(
                    """
                    SELECT artist_name, song_title, seconds_listened
                    FROM music_stats
                    WHERE guild_id = ? AND user_id = ?
                    """,
                    (guild_id, member.id),
                ) as cur:
                    raw_music = await cur.fetchall()
            except Exception:
                raw_music = []

        gamers_agg, _ = aggregate_guild_game_stats([(member.id, g, s) for g, s in raw_games])
        if gamers_agg:
            _, total_game_sec, games = gamers_agg[0]
            games = games[:8]
        else:
            total_game_sec = 0
            games = []

        music_agg, _, _ = aggregate_guild_music_stats([(member.id, a, t, s) for a, t, s in raw_music])
        if music_agg:
            _, total_music_sec, top_u_artists, top_u_songs = music_agg[0]
        else:
            total_music_sec = 0
            top_u_artists = []
            top_u_songs = []

        view = ui.LayoutView(timeout=300)

        summary_text = (
            f"### {dn_emoji} สถิติของ {member.display_name}\n"
            f"🎮 **เวลาเล่นเกมรวม:** `{format_duration(total_game_sec)}`\n"
            f"🎧 **เวลาฟังเพลงรวม:** `{format_duration(total_music_sec)}`\n"
            f"🔊 **เวลาสิงห้องพูดคุย:** `{format_duration(voice_sec)}`\n"
            f"💬 **ข้อความที่พิมพ์:** `{msg_count:,} ข้อความ`"
        )
        view.add_item(
            ui.Container(
                ui.Section(
                    ui.TextDisplay(summary_text),
                    accessory=ui.Thumbnail(member.display_avatar.url),
                ),
                accent_colour=discord.Color.blurple(),
            )
        )

        if games:
            game_lines = [
                f"{get_rank_badge(idx)} **{g_name}** — `{format_duration(sec)}`"
                for idx, (g_name, sec) in enumerate(games, start=1)
            ]
            games_body = "\n".join(game_lines)[:800]
        else:
            games_body = "-# ยังไม่มีข้อมูลการเล่นเกมที่บันทึกไว้ (ต้องเปิดแสดงสถานะ Activity ใน Discord ตอนเล่นเกมนะ!)"

        view.add_item(
            ui.Container(
                ui.TextDisplay("### 🕹️ เกมที่เล่นทั้งหมด"),
                ui.Separator(spacing=discord.SeparatorSpacing.small),
                ui.TextDisplay(games_body),
                accent_colour=discord.Color.from_rgb(245, 158, 11),
            )
        )

        if top_u_artists or top_u_songs:
            m_parts = []
            if top_u_artists:
                a_lines = [
                    f"{get_rank_badge(idx)} **{a_name}** — `{format_duration(sec)}`"
                    for idx, (a_name, sec) in enumerate(top_u_artists[:5], start=1)
                ]
                m_parts.append("**🎤 ศิลปินที่ฟังบ่อยสุด**\n" + "\n".join(a_lines))
            if top_u_songs:
                s_lines = [
                    f"{get_rank_badge(idx)} **{s_name}** (*{a_name}*) — `{format_duration(sec)}`"
                    for idx, (s_name, a_name, sec) in enumerate(top_u_songs[:5], start=1)
                ]
                m_parts.append("**💿 เพลงที่เปิดซ้ำบ่อยสุด**\n" + "\n".join(s_lines))
            music_body = "\n\n".join(m_parts)[:800]
        else:
            music_body = "-# ยังไม่มีข้อมูลการฟังเพลง (เชื่อม Spotify หรือแอปฟังเพลงเข้ากับ Discord เพื่อเก็บสถิติได้เลย!)"

        view.add_item(
            ui.Container(
                ui.TextDisplay("### 🎧 ศิลปินและเพลงโปรด"),
                ui.Separator(spacing=discord.SeparatorSpacing.small),
                ui.TextDisplay(music_body),
                accent_colour=discord.Color.from_rgb(236, 72, 153),
            )
        )
        return view

    @app_commands.command(
        name="stats",
        description="📊 ดูสถิติของเพื่อนหรือทั้งกลุ่ม: ใครเล่นเกมไหนบ้าง สิงห้องคุยนานสุด และพิมพ์แชทเยอะสุด!",
    )
    @app_commands.describe(member="เลือกเพื่อนเพื่อเจาะลึกสถิติรายบุคคล (เว้นว่างเพื่อดูอันดับรวมทั้งกลุ่ม)")
    async def stats_cmd(self, interaction: discord.Interaction, member: discord.Member = None):
        await interaction.response.defer()
        if member:
            member_view = await self.build_member_stats_view(interaction.guild_id, member)
            await interaction.followup.send(view=member_view)
        else:
            board_view = await build_guild_stats_view(interaction.guild)
            await interaction.followup.send(view=board_view)

    @app_commands.command(
        name="game_rank",
        description="🎮 ดูอันดับว่าในเกมที่ระบุ (หรือเกมที่กำลังเล่นกัน) ใครในกลุ่มเล่นเยอะสุด!",
    )
    @app_commands.describe(game_name="ชื่อเกมที่ต้องการเช็คอันดับ (เช่น Valorant, Dota 2, Roblox)")
    async def game_rank(self, interaction: discord.Interaction, game_name: str):
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                """
                SELECT user_id, game_name, seconds_played
                FROM game_stats
                WHERE guild_id = ?
                """,
                (interaction.guild_id,),
            ) as cur:
                raw_rows = await cur.fetchall()

        _, top_games = aggregate_guild_game_stats(raw_rows)
        query_norm = normalize_game_name(game_name.strip())
        query_key = make_game_key(query_norm)
        raw_low = game_name.strip().casefold()

        matched = None
        # 1. เช็คตรงชื่อมาตรฐานเป๊ะก่อน (เช่น พิมพ์ valorant หรือ bloxstrap ก็จะเจอ VALORANT / Roblox ทันที)
        for g_title, total_sec, p_list in top_games:
            if make_game_key(g_title) == query_key:
                matched = (g_title, total_sec, p_list)
                break

        # 2. ถ้าไม่เจอแบบตรงเป๊ะ ให้ค้นหาแบบบางส่วนของชื่อเกม
        if not matched:
            for g_title, total_sec, p_list in top_games:
                g_key = make_game_key(g_title)
                if (
                    (query_norm and query_norm.casefold() in g_title.casefold())
                    or (raw_low and raw_low in g_title.casefold())
                    or (query_key and query_key in g_key)
                ):
                    matched = (g_title, total_sec, p_list)
                    break

        if not matched:
            return await interaction.response.send_message(
                f"❌ ยังไม่มีสถิติคนเล่นเกมที่ชื่อคล้าย **`{game_name}`** เลยเพื่อน!", ephemeral=True
            )

        matched_title, _total_g, players = matched
        lines = [
            f"{get_rank_badge(idx)} <@{uid}> — **`{format_duration(sec)}`**"
            for idx, (uid, sec) in enumerate(players[:10], start=1)
        ]

        embed = discord.Embed(
            title=f"🏆 อันดับคนเล่นเกม: {matched_title}",
            description="\n".join(lines),
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="music_rank",
        description="🎧 ดูอันดับสายฟังเพลง ศิลปินยอดฮิต หรือเช็คว่าใครฟังศิลปิน/เพลงไหนเยอะสุด!",
    )
    @app_commands.describe(query="พิมพ์ชื่อศิลปินหรือชื่อเพลงเพื่อค้นหา (เว้นว่างเพื่อดูอันดับรวมทั้งกลุ่ม)")
    async def music_rank(self, interaction: discord.Interaction, query: str = None):
        async with aiosqlite.connect(DB_PATH) as db:
            try:
                async with db.execute(
                    """
                    SELECT user_id, artist_name, song_title, seconds_listened
                    FROM music_stats
                    WHERE guild_id = ?
                    """,
                    (interaction.guild_id,),
                ) as cur:
                    raw_rows = await cur.fetchall()
            except Exception:
                raw_rows = []

        if not raw_rows:
            return await interaction.response.send_message(
                "🎧 ยังไม่มีสถิติการฟังเพลงในเซิร์ฟเวอร์นี้เลย! ลองเชื่อม Spotify เข้ากับ Discord แล้วเปิดเพลงฟังดูนะ!",
                ephemeral=True,
            )

        top_listeners, top_artists, top_songs = aggregate_guild_music_stats(raw_rows)

        if not query or not query.strip():
            embed = discord.Embed(
                title="🎧 ชาร์ตเพลงและนักฟังเพลงประจำกลุ่ม",
                color=discord.Color.from_rgb(236, 72, 153),
            )
            listener_lines = [
                f"{get_rank_badge(i)} <@{uid}> — **`{format_duration(sec)}`** *(ชอบฟัง {u_art[0][0]})*"
                if u_art
                else f"{get_rank_badge(i)} <@{uid}> — **`{format_duration(sec)}`**"
                for i, (uid, sec, u_art, _) in enumerate(top_listeners[:6], start=1)
            ]
            embed.add_field(
                name="👑 อันดับคนฟังเพลงนานสุด",
                value="\n".join(listener_lines) or "-",
                inline=False,
            )

            artist_lines = [
                f"{get_rank_badge(i)} **{art}** — รวม `{format_duration(sec)}`"
                for i, (art, sec, _) in enumerate(top_artists[:5], start=1)
            ]
            if artist_lines:
                embed.add_field(
                    name="🎤 ศิลปินยอดฮิตประจำกลุ่ม",
                    value="\n".join(artist_lines),
                    inline=False,
                )

            song_lines = [
                f"{get_rank_badge(i)} **{s_title}** (*{art}*) — รวม `{format_duration(sec)}`"
                for i, (s_title, art, sec, _) in enumerate(top_songs[:5], start=1)
            ]
            if song_lines:
                embed.add_field(
                    name="💿 เพลงฮิตติดชาร์ตชาวแก๊ง",
                    value="\n".join(song_lines),
                    inline=False,
                )
            return await interaction.response.send_message(embed=embed)

        q_clean = query.strip()
        q_key = _make_text_key(q_clean)
        q_low = q_clean.casefold()

        # เช็คศิลปินก่อน แล้วค่อยเช็คชื่อเพลง
        for art_name, total_sec, u_list in top_artists:
            if q_low in art_name.casefold() or q_key in _make_text_key(art_name):
                lines = [
                    f"{get_rank_badge(i)} <@{uid}> — **`{format_duration(sec)}`**"
                    for i, (uid, sec) in enumerate(u_list[:10], start=1)
                ]
                embed = discord.Embed(
                    title=f"🎤 แฟนพันธุ์แท้ศิลปิน: {art_name}",
                    description=f"เวลาฟังรวมทั้งกลุ่ม: **`{format_duration(total_sec)}`**\n\n" + "\n".join(lines),
                    color=discord.Color.from_rgb(236, 72, 153),
                )
                return await interaction.response.send_message(embed=embed)

        for s_title, art_name, total_sec, u_list in top_songs:
            if q_low in s_title.casefold() or q_key in _make_text_key(s_title):
                lines = [
                    f"{get_rank_badge(i)} <@{uid}> — **`{format_duration(sec)}`**"
                    for i, (uid, sec) in enumerate(u_list[:10], start=1)
                ]
                embed = discord.Embed(
                    title=f"💿 อันดับคนฟังเพลง: {s_title} — {art_name}",
                    description=f"เวลาฟังรวมทั้งกลุ่ม: **`{format_duration(total_sec)}`**\n\n" + "\n".join(lines),
                    color=discord.Color.from_rgb(236, 72, 153),
                )
                return await interaction.response.send_message(embed=embed)

        await interaction.response.send_message(
            f"❌ ยังไม่มีสถิติคนฟังศิลปินหรือเพลงที่ชื่อคล้าย **`{q_clean}`** เลยเพื่อน!",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    bot.add_dynamic_items(RefreshStatsButton, MyStatsButton)
    await bot.add_cog(ActivityStatsCog(bot))
