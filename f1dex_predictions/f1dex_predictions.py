
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from functools import cmp_to_key
import re
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import discord
from discord.ext import tasks
from redbot.core import Config, commands

EMBED_RED = 0xD72638
EMBED_DARK = 0x1F2937
EMBED_GREEN = 0x2ECC71
EMBED_GOLD = 0xF1C40F

TRUE_TOKENS = {"y", "yes", "true", "1", "on"}
FALSE_TOKENS = {"n", "no", "false", "0", "off"}

TEAM_ALIASES = {
    "mclaren": "McLaren",
    "papaya": "McLaren",
    "mcl": "McLaren",
    "ferrari": "Ferrari",
    "scuderia": "Ferrari",
    "fer": "Ferrari",
    "redbull": "Red Bull",
    "red bull": "Red Bull",
    "rbr": "Red Bull",
    "mercedes": "Mercedes",
    "merc": "Mercedes",
    "amg": "Mercedes",
    "astonmartin": "Aston Martin",
    "aston martin": "Aston Martin",
    "amr": "Aston Martin",
    "alpine": "Alpine",
    "alp": "Alpine",
    "haas": "Haas",
    "has": "Haas",
    "williams": "Williams",
    "wil": "Williams",
    "sauber": "Sauber",
    "kick": "Sauber",
    "audi": "Audi",
    "racingbulls": "Racing Bulls",
    "racing bulls": "Racing Bulls",
    "vcarb": "Racing Bulls",
    "rbf1": "Racing Bulls",
    "rb": "Racing Bulls",
}

OPENF1_BASE_URL = "https://api.openf1.org/v1"

BOLD_PREFIX_TOKENS = (
    "bold:",
    "hot take:",
    "hot-take:",
    "prediction:",
    "call:",
    "my bold:",
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        out = datetime.fromisoformat(text)
    except ValueError:
        return None
    if out.tzinfo is None:
        out = out.replace(tzinfo=timezone.utc)
    return out.astimezone(timezone.utc)


def normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower()) if value else ""


def parse_yes_no(value: str) -> Optional[bool]:
    token = normalize_text(value)
    if token in TRUE_TOKENS:
        return True
    if token in FALSE_TOKENS:
        return False
    return None


def same_name(a: Optional[str], b: Optional[str]) -> bool:
    if not a or not b:
        return False
    return normalize_text(a) == normalize_text(b)


class CorePredictionModal(discord.ui.Modal):
    def __init__(self, cog: "F1DexPredictions"):
        super().__init__(title="Core Prediction")
        self.cog = cog
        self.p1 = discord.ui.TextInput(label="P1 Driver", max_length=64, required=True)
        self.p2 = discord.ui.TextInput(label="P2 Driver", max_length=64, required=True)
        self.p3 = discord.ui.TextInput(label="P3 Driver", max_length=64, required=True)
        self.pole = discord.ui.TextInput(label="Pole Position", max_length=64, required=True)
        self.safety_car = discord.ui.TextInput(label="Safety Car (Y/N)", max_length=5, required=True)
        for item in (self.p1, self.p2, self.p3, self.pole, self.safety_car):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_core_submit(
            interaction,
            {
                "p1": self.p1.value.strip(),
                "p2": self.p2.value.strip(),
                "p3": self.p3.value.strip(),
                "pole": self.pole.value.strip(),
                "safety_car": self.safety_car.value.strip(),
            },
        )


class AdvancedPredictionModal(discord.ui.Modal):
    def __init__(self, cog: "F1DexPredictions"):
        super().__init__(title="Advanced Prediction")
        self.cog = cog
        self.flop_driver = discord.ui.TextInput(label="Biggest Flop Driver", max_length=64, required=True)
        self.flop_team = discord.ui.TextInput(label="Biggest Flop Team", max_length=64, required=True)
        self.surprise_driver = discord.ui.TextInput(label="Good Surprise Driver", max_length=64, required=True)
        self.surprise_team = discord.ui.TextInput(label="Good Surprise Team", max_length=64, required=True)
        self.bold_text = discord.ui.TextInput(
            label="Bold Prediction",
            max_length=200,
            required=True,
            style=discord.TextStyle.long,
            placeholder="Example: McLaren double points, Norris podium from P10+",
        )
        for item in (
            self.flop_driver,
            self.flop_team,
            self.surprise_driver,
            self.surprise_team,
            self.bold_text,
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_advanced_submit(
            interaction,
            {
                "flop_driver": self.flop_driver.value.strip(),
                "flop_team": self.flop_team.value.strip(),
                "surprise_driver": self.surprise_driver.value.strip(),
                "surprise_team": self.surprise_team.value.strip(),
                "bold_text": self.bold_text.value.strip(),
            },
        )


class QOTWModal(discord.ui.Modal):
    def __init__(self, cog: "F1DexPredictions", answer_type: str):
        super().__init__(title="Question of the Weekend")
        self.cog = cog
        placeholder = "Driver or Team"
        if answer_type == "boolean":
            placeholder = "Y or N"
        self.answer = discord.ui.TextInput(label="Your Answer", max_length=120, required=True, placeholder=placeholder)
        self.add_item(self.answer)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_qotw_submit(interaction, {"answer": self.answer.value.strip()})


class SprintPredictionModal(discord.ui.Modal):
    def __init__(self, cog: "F1DexPredictions"):
        super().__init__(title="Sprint Prediction")
        self.cog = cog
        self.p1 = discord.ui.TextInput(label="Sprint P1 Driver", max_length=64, required=True)
        self.p2 = discord.ui.TextInput(label="Sprint P2 Driver", max_length=64, required=True)
        self.p3 = discord.ui.TextInput(label="Sprint P3 Driver", max_length=64, required=True)
        self.pole = discord.ui.TextInput(label="Sprint Pole", max_length=64, required=True)
        self.safety_car = discord.ui.TextInput(label="Sprint Safety Car (Y/N)", max_length=5, required=True)
        for item in (self.p1, self.p2, self.p3, self.pole, self.safety_car):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_sprint_submit(
            interaction,
            {
                "p1": self.p1.value.strip(),
                "p2": self.p2.value.strip(),
                "p3": self.p3.value.strip(),
                "pole": self.pole.value.strip(),
                "safety_car": self.safety_car.value.strip(),
            },
        )


class MainPredictionView(discord.ui.View):
    def __init__(self, cog: "F1DexPredictions"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Submit Core", style=discord.ButtonStyle.danger, custom_id="f1dexpred:core")
    async def core(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.open_core_modal(interaction)

    @discord.ui.button(label="Submit Advanced", style=discord.ButtonStyle.primary, custom_id="f1dexpred:adv")
    async def adv(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.open_advanced_modal(interaction)

    @discord.ui.button(label="Submit QOTW", style=discord.ButtonStyle.secondary, custom_id="f1dexpred:qotw")
    async def qotw(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.open_qotw_modal(interaction)

    @discord.ui.button(label="View My Prediction", style=discord.ButtonStyle.success, custom_id="f1dexpred:mine")
    async def mine(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.show_prediction_for_interaction(interaction)


class SprintPredictionView(discord.ui.View):
    def __init__(self, cog: "F1DexPredictions"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Submit Sprint", style=discord.ButtonStyle.danger, custom_id="f1dexpred:sprint")
    async def sprint(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.open_sprint_modal(interaction)

    @discord.ui.button(label="View My Prediction", style=discord.ButtonStyle.success, custom_id="f1dexpred:mine2")
    async def mine(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.show_prediction_for_interaction(interaction)


class F1DexPredictions(commands.Cog):
    """F1dex prediction championship with button-based submissions."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=3198227041, force_registration=True)
        self.config.register_guild(
            main_channel_id=None,
            log_channel_id=None,
            leaderboard_channel_id=None,
            master_role_id=None,
            active_round_id=None,
            rounds={},
            queued_round=None,
        )
        self.main_view = MainPredictionView(self)
        self.sprint_view = SprintPredictionView(self)
        self._write_lock = asyncio.Lock()
        self._http_session: Optional[aiohttp.ClientSession] = None

    async def cog_load(self) -> None:
        self.bot.add_view(self.main_view)
        self.bot.add_view(self.sprint_view)
        if self._http_session is None or self._http_session.closed:
            timeout = aiohttp.ClientTimeout(total=20)
            self._http_session = aiohttp.ClientSession(timeout=timeout)
        if not self.round_scheduler.is_running():
            self.round_scheduler.start()

    def cog_unload(self) -> None:
        if self.round_scheduler.is_running():
            self.round_scheduler.cancel()
        if self._http_session and not self._http_session.closed:
            self.bot.loop.create_task(self._http_session.close())

    @tasks.loop(minutes=1)
    async def round_scheduler(self) -> None:
        for guild in self.bot.guilds:
            try:
                await self._run_scheduler_for_guild(guild)
            except Exception:
                continue

    @round_scheduler.before_loop
    async def _before_scheduler(self) -> None:
        if hasattr(self.bot, "wait_until_red_ready"):
            await self.bot.wait_until_red_ready()
        else:
            await self.bot.wait_until_ready()

    async def _run_scheduler_for_guild(self, guild: discord.Guild) -> None:
        now = utcnow()
        guild_conf = self.config.guild(guild)
        rounds = await guild_conf.rounds()
        active_round_id = await guild_conf.active_round_id()
        queued_round = await guild_conf.queued_round()
        changed = False

        if active_round_id and active_round_id in rounds:
            round_data = rounds[active_round_id]
            lock_at = parse_iso(round_data.get("lock_at"))
            if round_data.get("is_open") and lock_at and now >= lock_at:
                round_data["is_open"] = False
                round_data["locked_at"] = to_iso(now)
                await self._refresh_round_messages(guild, round_data)
                changed = True

            top10_at = parse_iso(round_data.get("top10_post_at"))
            if top10_at and not round_data.get("top10_posted", False) and now >= top10_at:
                await self._post_top10(guild, round_data)
                round_data["top10_posted"] = True
                changed = True

            race_end = parse_iso(round_data.get("race_end"))
            openf1_data = round_data.get("openf1", {})
            if race_end and openf1_data.get("meeting_key"):
                synced_at = parse_iso(openf1_data.get("synced_at"))
                last_attempt = parse_iso(openf1_data.get("last_sync_attempt"))
                sync_due = race_end + timedelta(minutes=20)
                retry_due = (last_attempt is None) or (now - last_attempt >= timedelta(minutes=30))
                if now >= sync_due and synced_at is None and retry_due:
                    try:
                        await self._openf1_sync_round_results(round_data)
                        changed = True
                    except Exception:
                        openf1_data["last_sync_attempt"] = to_iso(now)
                        changed = True

            rounds[active_round_id] = round_data

        if queued_round:
            open_at = parse_iso(queued_round.get("open_at"))
            if open_at and now >= open_at:
                rounds[queued_round["round_id"]] = queued_round
                await guild_conf.rounds.set(rounds)
                await guild_conf.active_round_id.set(queued_round["round_id"])
                await guild_conf.queued_round.set(None)
                await self._post_or_update_round_embeds(guild, queued_round)
                return

        if changed:
            await guild_conf.rounds.set(rounds)

    def _new_round(self, round_id: str, name: str, open_at: datetime, lock_at: datetime, race_end: datetime, sprint_enabled: bool) -> Dict[str, Any]:
        return {
            "round_id": round_id,
            "name": name,
            "open_at": to_iso(open_at),
            "lock_at": to_iso(lock_at),
            "race_end": to_iso(race_end),
            "top10_post_at": to_iso(race_end + timedelta(hours=47, minutes=45)),
            "top10_posted": False,
            "is_open": open_at <= utcnow() < lock_at,
            "locked_at": None,
            "sprint_enabled": sprint_enabled,
            "main_message_id": None,
            "sprint_message_id": None,
            "submissions": {},
            "scores": {},
            "bold_overrides": {},
            "qotw": {"prompt": None, "answer_type": None, "correct_answer": None},
            "openf1": {
                "meeting_key": None,
                "fp1_session_key": None,
                "qualifying_session_key": None,
                "race_session_key": None,
                "sprint_qualifying_session_key": None,
                "sprint_session_key": None,
                "synced_at": None,
                "last_sync_attempt": None,
            },
            "official": {
                "core": {"p1": None, "p2": None, "p3": None, "pole": None, "safety_car": None},
                "sprint": {"p1": None, "p2": None, "p3": None, "pole": None, "safety_car": None},
                "flop": {"driver": None, "team": None},
                "surprise": {"driver": None, "team": None},
                "weekend_data": {"drivers": {}, "safety_car": None, "red_flag": None},
            },
        }

    async def _reply(self, interaction: discord.Interaction, content: Optional[str] = None, embed: Optional[discord.Embed] = None) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(content=content, embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(content=content, embed=embed, ephemeral=True)

    def _format_dt(self, dt: Optional[datetime]) -> str:
        if not dt:
            return "Unknown"
        return discord.utils.format_dt(dt, style="F")

    def _resolve_text_channel(self, guild: discord.Guild, channel_id: Optional[int]) -> Optional[discord.TextChannel]:
        if channel_id is None:
            return None
        channel = guild.get_channel(channel_id)
        return channel if isinstance(channel, discord.TextChannel) else None

    def _round_is_open(self, round_data: Dict[str, Any]) -> bool:
        if not round_data.get("is_open"):
            return False
        lock_at = parse_iso(round_data.get("lock_at"))
        if lock_at and utcnow() >= lock_at:
            return False
        return True

    async def _get_active_round(self, guild: discord.Guild) -> Tuple[Optional[str], Optional[Dict[str, Any]], Dict[str, Any]]:
        guild_conf = self.config.guild(guild)
        active_round_id = await guild_conf.active_round_id()
        rounds = await guild_conf.rounds()
        if not active_round_id:
            return None, None, rounds
        return active_round_id, rounds.get(active_round_id), rounds

    async def _save_round(self, guild: discord.Guild, round_id: str, round_data: Dict[str, Any], rounds: Dict[str, Any]) -> None:
        rounds[round_id] = round_data
        await self.config.guild(guild).rounds.set(rounds)

    async def _get_http_session(self) -> aiohttp.ClientSession:
        if self._http_session is None or self._http_session.closed:
            timeout = aiohttp.ClientTimeout(total=20)
            self._http_session = aiohttp.ClientSession(timeout=timeout)
        return self._http_session

    async def _openf1_get(self, endpoint: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        session = await self._get_http_session()
        url = f"{OPENF1_BASE_URL}/{endpoint.lstrip('/')}"
        last_error: Optional[Exception] = None

        for attempt in range(3):
            try:
                async with session.get(url, params=params) as response:
                    if response.status == 429:
                        await asyncio.sleep(1.0 + attempt)
                        continue
                    if response.status >= 400:
                        body = await response.text()
                        raise RuntimeError(
                            f"OpenF1 request failed ({response.status}) for {endpoint}: {body[:180]}"
                        )
                    payload = await response.json(content_type=None)
                    if isinstance(payload, list):
                        return payload
                    raise RuntimeError(f"OpenF1 returned non-list payload for {endpoint}.")
            except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(0.6 + (attempt * 0.6))
                    continue

        raise RuntimeError(f"OpenF1 request failed for {endpoint}: {last_error}")

    async def _openf1_get_meeting(self, meeting_key: int) -> Optional[Dict[str, Any]]:
        meetings = await self._openf1_get("meetings", {"meeting_key": meeting_key})
        return meetings[0] if meetings else None

    async def _openf1_get_sessions_for_meeting(self, meeting_key: int) -> List[Dict[str, Any]]:
        sessions = await self._openf1_get("sessions", {"meeting_key": meeting_key})
        sessions.sort(key=lambda row: row.get("date_start", ""))
        return sessions

    def _find_session(self, sessions: List[Dict[str, Any]], names: Tuple[str, ...]) -> Optional[Dict[str, Any]]:
        lowered_names = {item.lower() for item in names}
        for session in sessions:
            if str(session.get("session_name", "")).lower() in lowered_names:
                return session
        return None

    def _find_first_practice(self, sessions: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        fp1 = self._find_session(sessions, ("Practice 1",))
        if fp1:
            return fp1
        for session in sessions:
            if str(session.get("session_type", "")).lower() == "practice":
                return session
        return None

    async def _openf1_get_race_weekends(self, year: int) -> List[Dict[str, Any]]:
        meetings = await self._openf1_get("meetings", {"year": year})
        meeting_map = {int(row["meeting_key"]): row for row in meetings if row.get("meeting_key") is not None}
        race_sessions = await self._openf1_get("sessions", {"year": year, "session_name": "Race"})
        race_sessions.sort(key=lambda row: row.get("date_start", ""))

        weekends: List[Dict[str, Any]] = []
        for race in race_sessions:
            meeting_key = race.get("meeting_key")
            if meeting_key is None:
                continue
            meeting_key = int(meeting_key)
            meeting = meeting_map.get(meeting_key, {})
            weekends.append(
                {
                    "meeting_key": meeting_key,
                    "meeting_name": meeting.get("meeting_name") or race.get("country_name") or f"Meeting {meeting_key}",
                    "country_name": meeting.get("country_name") or race.get("country_name"),
                    "location": meeting.get("location") or race.get("location"),
                    "race_date_start": race.get("date_start"),
                    "race_date_end": race.get("date_end"),
                    "race_session_key": race.get("session_key"),
                }
            )
        return weekends

    async def _openf1_build_round(
        self,
        meeting_key: int,
        round_id: Optional[str] = None,
        name: Optional[str] = None,
        open_at: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        meeting = await self._openf1_get_meeting(meeting_key)
        if not meeting:
            raise RuntimeError(f"No OpenF1 meeting found for meeting_key={meeting_key}.")

        sessions = await self._openf1_get_sessions_for_meeting(meeting_key)
        fp1 = self._find_first_practice(sessions)
        race = self._find_session(sessions, ("Race",))
        qualifying = self._find_session(sessions, ("Qualifying",))
        sprint_quali = self._find_session(sessions, ("Sprint Qualifying",))
        sprint = self._find_session(sessions, ("Sprint",))

        if not fp1 or not race:
            raise RuntimeError("OpenF1 meeting is missing Practice 1 or Race session.")

        lock_at = parse_iso(fp1.get("date_start"))
        race_end = parse_iso(race.get("date_end")) or parse_iso(race.get("date_start"))
        if lock_at is None or race_end is None:
            raise RuntimeError("OpenF1 session dates are invalid for this meeting.")

        round_open_at = open_at or utcnow()
        sprint_enabled = sprint is not None
        round_name = name or meeting.get("meeting_name") or f"Meeting {meeting_key}"
        season_year = int(meeting.get("year") or utcnow().year)
        round_key = round_id or f"{season_year}-mk{meeting_key}"

        round_data = self._new_round(
            round_key,
            round_name,
            round_open_at,
            lock_at,
            race_end,
            sprint_enabled,
        )
        round_data.setdefault("openf1", {})
        round_data["openf1"].update(
            {
                "meeting_key": meeting_key,
                "fp1_session_key": fp1.get("session_key"),
                "qualifying_session_key": qualifying.get("session_key") if qualifying else None,
                "race_session_key": race.get("session_key"),
                "sprint_qualifying_session_key": sprint_quali.get("session_key") if sprint_quali else None,
                "sprint_session_key": sprint.get("session_key") if sprint else None,
                "synced_at": None,
                "last_sync_attempt": None,
            }
        )
        return round_data

    def _openf1_driver_map(self, drivers: List[Dict[str, Any]], preferred_session_key: Optional[int]) -> Dict[int, Dict[str, Any]]:
        out: Dict[int, Dict[str, Any]] = {}
        for row in drivers:
            number = row.get("driver_number")
            if number is None:
                continue
            number = int(number)
            chosen = out.get(number)
            if not chosen:
                out[number] = row
                continue
            if preferred_session_key and row.get("session_key") == preferred_session_key:
                out[number] = row
        return out

    def _detect_safety_events(self, race_control_rows: List[Dict[str, Any]]) -> Tuple[bool, bool]:
        safety_car = False
        red_flag = False
        for row in race_control_rows:
            message = str(row.get("message") or "").upper()
            if (
                "SAFETY CAR DEPLOYED" in message
                or "VIRTUAL SAFETY CAR DEPLOYED" in message
                or "VSC DEPLOYED" in message
            ):
                safety_car = True
            if "RED FLAG" in message:
                red_flag = True
            if safety_car and red_flag:
                break
        return safety_car, red_flag

    def _extract_fastest_lap_driver(
        self, laps: List[Dict[str, Any]], driver_map: Dict[int, Dict[str, Any]]
    ) -> Optional[str]:
        best_driver: Optional[int] = None
        best_lap = float("inf")
        for row in laps:
            duration = row.get("lap_duration")
            number = row.get("driver_number")
            if duration is None or number is None:
                continue
            try:
                duration_f = float(duration)
            except (TypeError, ValueError):
                continue
            if duration_f <= 0:
                continue
            if duration_f < best_lap:
                best_lap = duration_f
                best_driver = int(number)
        if best_driver is None:
            return None
        info = driver_map.get(best_driver, {})
        return (
            info.get("full_name")
            or info.get("broadcast_name")
            or info.get("name_acronym")
            or f"Driver {best_driver}"
        )

    async def _openf1_sync_round_results(self, round_data: Dict[str, Any]) -> Dict[str, Any]:
        openf1_data = round_data.setdefault("openf1", {})
        meeting_key = openf1_data.get("meeting_key")
        if meeting_key is None:
            raise RuntimeError("Active round has no OpenF1 meeting key.")
        meeting_key = int(meeting_key)
        openf1_data["last_sync_attempt"] = to_iso(utcnow())

        sessions = await self._openf1_get_sessions_for_meeting(meeting_key)
        fp1 = self._find_first_practice(sessions)
        race = self._find_session(sessions, ("Race",))
        qualifying = self._find_session(sessions, ("Qualifying",))
        sprint = self._find_session(sessions, ("Sprint",))
        sprint_quali = self._find_session(sessions, ("Sprint Qualifying",))

        if not race:
            raise RuntimeError("No race session found for this meeting in OpenF1.")

        race_session_key = int(race["session_key"])
        qualifying_session_key = int(qualifying["session_key"]) if qualifying else None
        sprint_session_key = int(sprint["session_key"]) if sprint else None
        sprint_quali_session_key = int(sprint_quali["session_key"]) if sprint_quali else None

        openf1_data.update(
            {
                "fp1_session_key": int(fp1["session_key"]) if fp1 else None,
                "qualifying_session_key": qualifying_session_key,
                "race_session_key": race_session_key,
                "sprint_session_key": sprint_session_key,
                "sprint_qualifying_session_key": sprint_quali_session_key,
            }
        )

        drivers = await self._openf1_get("drivers", {"meeting_key": meeting_key})
        driver_map = self._openf1_driver_map(drivers, race_session_key)
        race_results = await self._openf1_get("session_result", {"session_key": race_session_key})
        race_results.sort(key=lambda row: int(row.get("position") or 999))

        qualifying_positions: Dict[int, int] = {}
        if qualifying_session_key:
            qualifying_results = await self._openf1_get("session_result", {"session_key": qualifying_session_key})
            for row in qualifying_results:
                number = row.get("driver_number")
                position = row.get("position")
                if number is None or position is None:
                    continue
                qualifying_positions[int(number)] = int(position)

        race_control = await self._openf1_get("race_control", {"session_key": race_session_key})
        safety_car, red_flag = self._detect_safety_events(race_control)

        laps = await self._openf1_get("laps", {"session_key": race_session_key})
        fastest_lap_driver = self._extract_fastest_lap_driver(laps, driver_map)

        weekend_drivers: Dict[str, Dict[str, Any]] = {}
        ordered_names: List[str] = []
        for row in race_results:
            number = row.get("driver_number")
            if number is None:
                continue
            number = int(number)
            info = driver_map.get(number, {})
            name = (
                info.get("full_name")
                or info.get("broadcast_name")
                or info.get("name_acronym")
                or f"Driver {number}"
            )
            team = info.get("team_name") or "Unknown Team"

            position = row.get("position")
            finish = int(position) if position is not None else 20
            grid = qualifying_positions.get(number, finish)
            is_dns = bool(row.get("dns"))
            is_dsq = bool(row.get("dsq"))
            is_dnf = bool(row.get("dnf"))
            status = "finished"
            if is_dns:
                status = "dns"
            elif is_dsq:
                status = "dsq"
            elif is_dnf:
                status = "dnf"

            points = float(row.get("points") or 0.0)
            weekend_drivers[normalize_text(name)] = {
                "name": name,
                "team": team,
                "grid": grid,
                "finish": finish,
                "status": status,
                "dnf": is_dns or is_dsq or is_dnf,
                "points": points,
                "driver_number": number,
            }
            ordered_names.append(name)

        official = round_data.setdefault("official", {})
        core = official.setdefault("core", {})
        core["p1"] = ordered_names[0] if len(ordered_names) >= 1 else None
        core["p2"] = ordered_names[1] if len(ordered_names) >= 2 else None
        core["p3"] = ordered_names[2] if len(ordered_names) >= 3 else None
        core["pole"] = None
        if qualifying_positions:
            pole_driver_number = next((n for n, pos in qualifying_positions.items() if pos == 1), None)
            if pole_driver_number is not None:
                info = driver_map.get(pole_driver_number, {})
                core["pole"] = (
                    info.get("full_name")
                    or info.get("broadcast_name")
                    or info.get("name_acronym")
                    or f"Driver {pole_driver_number}"
                )
        if core["pole"] is None:
            core["pole"] = core["p1"]
        core["safety_car"] = safety_car

        weekend = official.setdefault("weekend_data", {})
        weekend["drivers"] = weekend_drivers
        weekend["safety_car"] = safety_car
        weekend["red_flag"] = red_flag
        weekend["fastest_lap_driver"] = fastest_lap_driver

        if sprint_session_key:
            round_data["sprint_enabled"] = True
            sprint_results = await self._openf1_get("session_result", {"session_key": sprint_session_key})
            sprint_results.sort(key=lambda row: int(row.get("position") or 999))
            sprint_names: List[str] = []
            for row in sprint_results:
                number = row.get("driver_number")
                if number is None:
                    continue
                info = driver_map.get(int(number), {})
                sprint_names.append(
                    info.get("full_name")
                    or info.get("broadcast_name")
                    or info.get("name_acronym")
                    or f"Driver {number}"
                )
            sprint_official = official.setdefault("sprint", {})
            sprint_official["p1"] = sprint_names[0] if len(sprint_names) >= 1 else None
            sprint_official["p2"] = sprint_names[1] if len(sprint_names) >= 2 else None
            sprint_official["p3"] = sprint_names[2] if len(sprint_names) >= 3 else None

            sprint_official["pole"] = sprint_official["p1"]
            if sprint_quali_session_key:
                sprint_quali_results = await self._openf1_get(
                    "session_result", {"session_key": sprint_quali_session_key}
                )
                pole_number = next(
                    (
                        int(row["driver_number"])
                        for row in sprint_quali_results
                        if row.get("position") == 1 and row.get("driver_number") is not None
                    ),
                    None,
                )
                if pole_number is not None:
                    info = driver_map.get(pole_number, {})
                    sprint_official["pole"] = (
                        info.get("full_name")
                        or info.get("broadcast_name")
                        or info.get("name_acronym")
                        or f"Driver {pole_number}"
                    )

            sprint_race_control = await self._openf1_get("race_control", {"session_key": sprint_session_key})
            sprint_sc, _ = self._detect_safety_events(sprint_race_control)
            sprint_official["safety_car"] = sprint_sc
        else:
            round_data["sprint_enabled"] = False

        self._compute_flop_surprise(round_data)
        openf1_data["synced_at"] = to_iso(utcnow())

        return {
            "meeting_key": meeting_key,
            "drivers_loaded": len(weekend_drivers),
            "race_session_key": race_session_key,
            "qualifying_session_key": qualifying_session_key,
            "sprint_session_key": sprint_session_key,
            "safety_car": safety_car,
            "red_flag": red_flag,
            "fastest_lap_driver": fastest_lap_driver,
        }

    def _build_main_embed(self, round_data: Dict[str, Any], is_open: bool) -> discord.Embed:
        embed = discord.Embed(
            title=f"F1dex Predictions Championship - {round_data.get('name', 'Weekend')}",
            description="Submit with buttons below. Each section is one-time and partial is allowed.",
            color=EMBED_RED if is_open else EMBED_DARK,
        )
        embed.add_field(name="Status", value="OPEN" if is_open else "LOCKED", inline=True)
        embed.add_field(name="Opens", value=self._format_dt(parse_iso(round_data.get("open_at"))), inline=True)
        embed.add_field(name="Locks At FP1", value=self._format_dt(parse_iso(round_data.get("lock_at"))), inline=False)
        embed.add_field(name="Race End", value=self._format_dt(parse_iso(round_data.get("race_end"))), inline=True)
        embed.add_field(
            name="Scoring",
            value="Core 5 | Flop D 1 | Flop T 1 | Surprise D 1 | Surprise T 1 | Bold 1/2 | QOTW 1",
            inline=False,
        )
        embed.set_footer(text=f"Round ID: {round_data.get('round_id')}")
        return embed

    def _build_sprint_embed(self, round_data: Dict[str, Any], is_open: bool) -> discord.Embed:
        embed = discord.Embed(
            title=f"Sprint Segment - {round_data.get('name', 'Weekend')}",
            description="Sprint section has separate prediction and 0.5 point per field.",
            color=EMBED_RED if is_open else EMBED_DARK,
        )
        embed.add_field(name="Fields", value="P1, P2, P3, Sprint Pole, Sprint Safety Car", inline=False)
        embed.add_field(name="No Sprint Flop/Surprise", value="Sprint uses only sprint core fields.", inline=False)
        embed.set_footer(text=f"Round ID: {round_data.get('round_id')}")
        return embed

    async def _post_or_update_round_embeds(self, guild: discord.Guild, round_data: Dict[str, Any]) -> Tuple[bool, str]:
        guild_conf = self.config.guild(guild)
        channel = self._resolve_text_channel(guild, await guild_conf.main_channel_id())
        if not channel:
            return False, "Main channel is not configured."
        is_open = self._round_is_open(round_data)

        main_msg = None
        if round_data.get("main_message_id"):
            try:
                main_msg = await channel.fetch_message(round_data["main_message_id"])
                await main_msg.edit(embed=self._build_main_embed(round_data, is_open), view=self.main_view if is_open else None)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                main_msg = None
        if main_msg is None:
            main_msg = await channel.send(embed=self._build_main_embed(round_data, is_open), view=self.main_view if is_open else None)
            round_data["main_message_id"] = main_msg.id

        if round_data.get("sprint_enabled"):
            sprint_msg = None
            if round_data.get("sprint_message_id"):
                try:
                    sprint_msg = await channel.fetch_message(round_data["sprint_message_id"])
                    await sprint_msg.edit(embed=self._build_sprint_embed(round_data, is_open), view=self.sprint_view if is_open else None)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    sprint_msg = None
            if sprint_msg is None:
                sprint_msg = await channel.send(embed=self._build_sprint_embed(round_data, is_open), view=self.sprint_view if is_open else None)
                round_data["sprint_message_id"] = sprint_msg.id

        rounds = await guild_conf.rounds()
        rounds[round_data["round_id"]] = round_data
        await guild_conf.rounds.set(rounds)
        return True, "ok"

    async def _refresh_round_messages(self, guild: discord.Guild, round_data: Dict[str, Any]) -> None:
        try:
            await self._post_or_update_round_embeds(guild, round_data)
        except Exception:
            pass

    async def _ensure_round_open(self, interaction: discord.Interaction) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        if interaction.guild is None:
            await self._reply(interaction, "This action is only available in servers.")
            return None, None, None
        round_id, round_data, rounds = await self._get_active_round(interaction.guild)
        if not round_id or not round_data:
            await self._reply(interaction, "No active prediction round is configured.")
            return None, None, None
        if not self._round_is_open(round_data):
            if round_data.get("is_open"):
                round_data["is_open"] = False
                round_data["locked_at"] = to_iso(utcnow())
                await self._save_round(interaction.guild, round_id, round_data, rounds)
                await self._refresh_round_messages(interaction.guild, round_data)
            await self._reply(interaction, "Predictions are closed for this round.")
            return None, None, None
        return round_id, round_data, rounds

    def _yn(self, value: Any) -> str:
        if value is True:
            return "Y"
        if value is False:
            return "N"
        return "?"

    def _build_submission_log_embed(
        self,
        user: discord.abc.User,
        round_data: Dict[str, Any],
        submission: Dict[str, Any],
        updated_section: str,
    ) -> discord.Embed:
        embed = discord.Embed(
            title="F1dex Prediction Log",
            description=f"Single submission log entry (auto-edited on updates).",
            color=EMBED_DARK,
            timestamp=utcnow(),
        )
        embed.add_field(name="User", value=user.mention, inline=True)
        embed.add_field(name="Round", value=round_data.get("name", "Unknown"), inline=True)
        embed.add_field(name="Updated Section", value=updated_section.title(), inline=True)

        sections = ["core", "advanced", "qotw"]
        if round_data.get("sprint_enabled"):
            sections.append("sprint")
        completed = sum(1 for key in sections if submission.get(key))
        embed.add_field(name="Progress", value=f"{completed}/{len(sections)} sections submitted", inline=False)

        core = submission.get("core")
        if core:
            core_value = (
                f"P1: {core.get('p1')}\n"
                f"P2: {core.get('p2')}\n"
                f"P3: {core.get('p3')}\n"
                f"Pole: {core.get('pole')}\n"
                f"Safety Car: {self._yn(core.get('safety_car'))}"
            )
        else:
            core_value = "Not submitted."
        embed.add_field(name="Core", value=core_value, inline=False)

        advanced = submission.get("advanced")
        if advanced:
            detection = advanced.get("bold_detection", {})
            advanced_value = (
                f"Flop Driver: {advanced.get('flop_driver')}\n"
                f"Flop Team: {advanced.get('flop_team')}\n"
                f"Surprise Driver: {advanced.get('surprise_driver')}\n"
                f"Surprise Team: {advanced.get('surprise_team')}\n"
                f"Bold: {advanced.get('bold_text')}\n"
                f"Detected: {detection.get('label', 'Unknown')} ({int(float(detection.get('probability', 1.0)) * 100)}%)"
            )
        else:
            advanced_value = "Not submitted."
        embed.add_field(name="Advanced", value=advanced_value, inline=False)

        qotw = submission.get("qotw")
        qotw_prompt = round_data.get("qotw", {}).get("prompt") or "QOTW"
        if qotw:
            qotw_answer = qotw.get("answer")
            if isinstance(qotw_answer, bool):
                qotw_answer = self._yn(qotw_answer)
            qotw_value = str(qotw_answer)
        else:
            qotw_value = "Not submitted."
        embed.add_field(name=qotw_prompt, value=qotw_value, inline=False)

        if round_data.get("sprint_enabled"):
            sprint = submission.get("sprint")
            if sprint:
                sprint_value = (
                    f"P1: {sprint.get('p1')}\n"
                    f"P2: {sprint.get('p2')}\n"
                    f"P3: {sprint.get('p3')}\n"
                    f"Pole: {sprint.get('pole')}\n"
                    f"Safety Car: {self._yn(sprint.get('safety_car'))}"
                )
            else:
                sprint_value = "Not submitted."
            embed.add_field(name="Sprint", value=sprint_value, inline=False)

        embed.set_footer(text=f"ID: {round_data.get('round_id')}:{user.id}")
        return embed

    async def _upsert_submission_log(
        self,
        guild: discord.Guild,
        round_id: str,
        round_data: Dict[str, Any],
        rounds: Dict[str, Any],
        user: discord.abc.User,
        updated_section: str,
    ) -> None:
        channel = self._resolve_text_channel(guild, await self.config.guild(guild).log_channel_id())
        if not channel:
            return

        submission = round_data.get("submissions", {}).get(str(user.id))
        if not submission:
            return

        embed = self._build_submission_log_embed(user, round_data, submission, updated_section)
        message_id = submission.get("log_message_id")

        if message_id:
            try:
                msg = await channel.fetch_message(int(message_id))
                await msg.edit(embed=embed)
                submission["log_last_section"] = updated_section
                submission["log_last_updated_at"] = to_iso(utcnow())
                await self._save_round(guild, round_id, round_data, rounds)
                return
            except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError):
                pass

        try:
            msg = await channel.send(embed=embed)
        except discord.HTTPException:
            return

        submission["log_message_id"] = msg.id
        submission["log_last_section"] = updated_section
        submission["log_last_updated_at"] = to_iso(utcnow())
        await self._save_round(guild, round_id, round_data, rounds)

    def _build_prediction_embed(self, user: discord.abc.User, round_data: Dict[str, Any], submission: Dict[str, Any]) -> discord.Embed:
        embed = discord.Embed(title=f"Your Prediction - {round_data.get('name')}", color=EMBED_GOLD)
        embed.set_author(name=str(user))
        core = submission.get("core")
        adv = submission.get("advanced")
        qotw = submission.get("qotw")
        sprint = submission.get("sprint")

        embed.add_field(
            name="Core",
            value=(
                f"P1: {core.get('p1')}\nP2: {core.get('p2')}\nP3: {core.get('p3')}\n"
                f"Pole: {core.get('pole')}\nSafety Car: {'Y' if core.get('safety_car') else 'N'}"
            )
            if core
            else "Not submitted.",
            inline=False,
        )
        embed.add_field(
            name="Advanced",
            value=(
                f"Flop Driver: {adv.get('flop_driver')}\nFlop Team: {adv.get('flop_team')}\n"
                f"Surprise Driver: {adv.get('surprise_driver')}\nSurprise Team: {adv.get('surprise_team')}\n"
                f"Bold: {adv.get('bold_text')}"
            )
            if adv
            else "Not submitted.",
            inline=False,
        )
        qotw_label = round_data.get("qotw", {}).get("prompt") or "QOTW"
        embed.add_field(name=qotw_label, value=qotw.get("answer") if qotw else "Not submitted.", inline=False)
        if round_data.get("sprint_enabled"):
            embed.add_field(
                name="Sprint",
                value=(
                    f"P1: {sprint.get('p1')}\nP2: {sprint.get('p2')}\nP3: {sprint.get('p3')}\n"
                    f"Pole: {sprint.get('pole')}\nSafety Car: {'Y' if sprint.get('safety_car') else 'N'}"
                )
                if sprint
                else "Not submitted.",
                inline=False,
            )
        return embed

    async def show_prediction_for_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await self._reply(interaction, "Server-only action.")
            return
        round_id, round_data, _ = await self._get_active_round(interaction.guild)
        if not round_id or not round_data:
            await self._reply(interaction, "No active round configured.")
            return
        submission = round_data.get("submissions", {}).get(str(interaction.user.id))
        if not submission:
            await self._reply(interaction, "You have no submission for this round yet.")
            return
        await self._reply(interaction, embed=self._build_prediction_embed(interaction.user, round_data, submission))

    def _advanced_is_retryable(self, advanced: Optional[Dict[str, Any]], round_data: Dict[str, Any]) -> bool:
        if not advanced:
            return False
        detection = advanced.get("bold_detection")
        if detection and detection.get("type") == "unknown":
            return True
        if not detection:
            reparsed = self._detect_bold_prediction(advanced.get("bold_text", ""), round_data)
            if reparsed.get("type") == "unknown":
                return True
        return False

    async def open_core_modal(self, interaction: discord.Interaction) -> None:
        round_id, round_data, _ = await self._ensure_round_open(interaction)
        if not round_id or not round_data:
            return
        if round_data.get("submissions", {}).get(str(interaction.user.id), {}).get("core"):
            await self._reply(interaction, "Core is already submitted and locked.")
            return
        await interaction.response.send_modal(CorePredictionModal(self))

    async def open_advanced_modal(self, interaction: discord.Interaction) -> None:
        round_id, round_data, _ = await self._ensure_round_open(interaction)
        if not round_id or not round_data:
            return
        existing_advanced = round_data.get("submissions", {}).get(str(interaction.user.id), {}).get("advanced")
        if existing_advanced and not self._advanced_is_retryable(existing_advanced, round_data):
            await self._reply(interaction, "Advanced is already submitted and locked.")
            return
        await interaction.response.send_modal(AdvancedPredictionModal(self))

    async def open_qotw_modal(self, interaction: discord.Interaction) -> None:
        round_id, round_data, _ = await self._ensure_round_open(interaction)
        if not round_id or not round_data:
            return
        qotw = round_data.get("qotw", {})
        if not qotw.get("prompt") or not qotw.get("answer_type"):
            await self._reply(interaction, "QOTW is not configured yet.")
            return
        if round_data.get("submissions", {}).get(str(interaction.user.id), {}).get("qotw"):
            await self._reply(interaction, "QOTW is already submitted and locked.")
            return
        await interaction.response.send_modal(QOTWModal(self, qotw["answer_type"]))

    async def open_sprint_modal(self, interaction: discord.Interaction) -> None:
        round_id, round_data, _ = await self._ensure_round_open(interaction)
        if not round_id or not round_data:
            return
        if not round_data.get("sprint_enabled"):
            await self._reply(interaction, "Sprint section is disabled this round.")
            return
        if round_data.get("submissions", {}).get(str(interaction.user.id), {}).get("sprint"):
            await self._reply(interaction, "Sprint is already submitted and locked.")
            return
        await interaction.response.send_modal(SprintPredictionModal(self))

    async def handle_core_submit(self, interaction: discord.Interaction, payload: Dict[str, str]) -> None:
        sc = parse_yes_no(payload.get("safety_car", ""))
        if sc is None:
            await self._reply(interaction, "Safety Car must be Y or N.")
            return
        async with self._write_lock:
            round_id, round_data, rounds = await self._ensure_round_open(interaction)
            if not round_id or not round_data or rounds is None:
                return
            user_id = str(interaction.user.id)
            sub = round_data.setdefault("submissions", {}).setdefault(user_id, {})
            if sub.get("core"):
                await self._reply(interaction, "Core is already submitted and locked.")
                return
            core = {
                "p1": payload["p1"],
                "p2": payload["p2"],
                "p3": payload["p3"],
                "pole": payload["pole"],
                "safety_car": sc,
                "submitted_at": to_iso(utcnow()),
            }
            sub["core"] = core
            await self._save_round(interaction.guild, round_id, round_data, rounds)
            await self._upsert_submission_log(
                interaction.guild, round_id, round_data, rounds, interaction.user, "core"
            )
        await self._reply(interaction, "Core submitted and locked.")

    async def handle_advanced_submit(self, interaction: discord.Interaction, payload: Dict[str, str]) -> None:
        async with self._write_lock:
            round_id, round_data, rounds = await self._ensure_round_open(interaction)
            if not round_id or not round_data or rounds is None:
                return
            user_id = str(interaction.user.id)
            sub = round_data.setdefault("submissions", {}).setdefault(user_id, {})
            had_retryable_advanced = bool(
                sub.get("advanced") and self._advanced_is_retryable(sub.get("advanced"), round_data)
            )
            if sub.get("advanced") and not had_retryable_advanced:
                await self._reply(interaction, "Advanced is already submitted and locked.")
                return
            detect = self._detect_bold_prediction(payload["bold_text"], round_data)
            if detect.get("type") == "unknown":
                await self._reply(
                    interaction,
                    (
                        "Your bold prediction was not recognized, so this section was not saved.\n"
                        "Please rewrite it with clearer keywords and submit again. "
                        "Use `[p]f1pred boldhelp` for examples."
                    ),
                )
                return
            adv = {
                "flop_driver": payload["flop_driver"],
                "flop_team": payload["flop_team"],
                "surprise_driver": payload["surprise_driver"],
                "surprise_team": payload["surprise_team"],
                "bold_text": payload["bold_text"],
                "bold_detection": detect,
                "submitted_at": to_iso(utcnow()),
            }
            sub["advanced"] = adv
            await self._save_round(interaction.guild, round_id, round_data, rounds)
            await self._upsert_submission_log(
                interaction.guild, round_id, round_data, rounds, interaction.user, "advanced"
            )
        p = detect.get("probability", 1.0)
        extra = "Not bold enough (>30%)." if p > 0.30 else f"Detected: {detect.get('label')} ({int(p * 100)}%)."
        prefix = "Advanced resubmitted and locked. " if had_retryable_advanced else "Advanced submitted and locked. "
        await self._reply(interaction, f"{prefix}{extra}")

    async def handle_qotw_submit(self, interaction: discord.Interaction, payload: Dict[str, str]) -> None:
        async with self._write_lock:
            round_id, round_data, rounds = await self._ensure_round_open(interaction)
            if not round_id or not round_data or rounds is None:
                return
            qotw = round_data.get("qotw", {})
            qtype = qotw.get("answer_type")
            if not qtype:
                await self._reply(interaction, "QOTW is not configured.")
                return
            answer: Any = payload["answer"].strip()
            if qtype == "boolean":
                parsed = parse_yes_no(answer)
                if parsed is None:
                    await self._reply(interaction, "QOTW expects Y or N.")
                    return
                answer = parsed
            user_id = str(interaction.user.id)
            sub = round_data.setdefault("submissions", {}).setdefault(user_id, {})
            if sub.get("qotw"):
                await self._reply(interaction, "QOTW is already submitted and locked.")
                return
            qsub = {"answer": answer, "submitted_at": to_iso(utcnow())}
            sub["qotw"] = qsub
            await self._save_round(interaction.guild, round_id, round_data, rounds)
            await self._upsert_submission_log(
                interaction.guild, round_id, round_data, rounds, interaction.user, "qotw"
            )
        await self._reply(interaction, "QOTW submitted and locked.")

    async def handle_sprint_submit(self, interaction: discord.Interaction, payload: Dict[str, str]) -> None:
        sc = parse_yes_no(payload.get("safety_car", ""))
        if sc is None:
            await self._reply(interaction, "Sprint Safety Car must be Y or N.")
            return
        async with self._write_lock:
            round_id, round_data, rounds = await self._ensure_round_open(interaction)
            if not round_id or not round_data or rounds is None:
                return
            if not round_data.get("sprint_enabled"):
                await self._reply(interaction, "Sprint section is disabled for this round.")
                return
            user_id = str(interaction.user.id)
            sub = round_data.setdefault("submissions", {}).setdefault(user_id, {})
            if sub.get("sprint"):
                await self._reply(interaction, "Sprint is already submitted and locked.")
                return
            sprint = {
                "p1": payload["p1"],
                "p2": payload["p2"],
                "p3": payload["p3"],
                "pole": payload["pole"],
                "safety_car": sc,
                "submitted_at": to_iso(utcnow()),
            }
            sub["sprint"] = sprint
            await self._save_round(interaction.guild, round_id, round_data, rounds)
            await self._upsert_submission_log(
                interaction.guild, round_id, round_data, rounds, interaction.user, "sprint"
            )
        await self._reply(interaction, "Sprint submitted and locked.")

    def _collect_driver_candidates(self, round_data: Dict[str, Any]) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for rec in round_data.get("official", {}).get("weekend_data", {}).get("drivers", {}).values():
            if rec.get("name"):
                out[normalize_text(rec["name"])] = rec["name"]
        for sub in round_data.get("submissions", {}).values():
            core = sub.get("core", {})
            for key in ("p1", "p2", "p3", "pole"):
                if core.get(key):
                    out[normalize_text(core[key])] = core[key]
            adv = sub.get("advanced", {})
            for key in ("flop_driver", "surprise_driver"):
                if adv.get(key):
                    out[normalize_text(adv[key])] = adv[key]
        return out

    def _collect_team_candidates(self, round_data: Dict[str, Any]) -> Dict[str, str]:
        out: Dict[str, str] = {normalize_text(k): v for k, v in TEAM_ALIASES.items()}
        for rec in round_data.get("official", {}).get("weekend_data", {}).get("drivers", {}).values():
            if rec.get("team"):
                out[normalize_text(rec["team"])] = rec["team"]
        for sub in round_data.get("submissions", {}).values():
            adv = sub.get("advanced", {})
            for key in ("flop_team", "surprise_team"):
                if adv.get(key):
                    out[normalize_text(adv[key])] = adv[key]
        return out

    def _detect_target(self, text: str, candidates: Dict[str, str]) -> Optional[str]:
        compact = normalize_text(text)
        matched: List[Tuple[int, str]] = []
        for key, value in candidates.items():
            if len(key) >= 3 and key in compact:
                matched.append((len(key), value))
        if not matched:
            return None
        matched.sort(key=lambda x: x[0], reverse=True)
        return matched[0][1]

    def _strip_bold_prefix(self, text: str) -> str:
        stripped = text.strip()
        lowered = stripped.lower()
        for token in BOLD_PREFIX_TOKENS:
            if lowered.startswith(token):
                return stripped[len(token) :].strip()
        return stripped

    def _extract_grid_floor(self, text: str) -> Optional[int]:
        lowered = text.lower()
        for pattern in (r"p\s*(\d{1,2})\s*\+", r"position\s*(\d{1,2})\s*\+", r"from\s*p?\s*(\d{1,2})\s*\+"):
            match = re.search(pattern, lowered)
            if not match:
                continue
            try:
                value = int(match.group(1))
            except ValueError:
                continue
            if 1 <= value <= 25:
                return value
        return None

    def _extract_dnf_count(self, text: str) -> Optional[int]:
        lowered = text.lower()
        match = re.search(r"(?:at least|min(?:imum)?|>=?)\s*(\d{1,2})\s*dnf", lowered)
        if match:
            return int(match.group(1))
        match = re.search(r"(\d{1,2})\s*dnf", lowered)
        if match:
            return int(match.group(1))
        if "triple dnf" in lowered:
            return 3
        return None

    def _detect_bold_prediction(self, text: str, round_data: Dict[str, Any]) -> Dict[str, Any]:
        cleaned = self._strip_bold_prefix(text)
        lower = cleaned.lower()
        compact = normalize_text(cleaned)
        driver = self._detect_target(cleaned, self._collect_driver_candidates(round_data))
        team = self._detect_target(cleaned, self._collect_team_candidates(round_data))
        grid_floor = self._extract_grid_floor(cleaned)
        dnf_count = self._extract_dnf_count(cleaned)

        if "pole to win" in lower or "convert pole" in lower or ("from pole" in lower and "win" in lower):
            return {"type": "pole_to_win", "target": None, "probability": 0.24, "label": "Pole to Win"}

        if "double podium" in lower or "both on podium" in lower:
            return {"type": "team_double_podium", "target": team, "probability": 0.07, "label": "Team Double Podium"}

        if "team podium" in lower or "constructor podium" in lower:
            return {"type": "team_podium_any", "target": team, "probability": 0.20, "label": "Team Podium"}

        if "double q3" in lower or "both in q3" in lower:
            return {"type": "team_double_q3", "target": team, "probability": 0.12, "label": "Team Double Q3"}

        if "both out in q1" in lower or "double q1 exit" in lower:
            return {"type": "team_double_q1_exit", "target": team, "probability": 0.10, "label": "Team Double Q1 Exit"}

        if "no dnf" in lower or "all finish" in lower or "everyone finishes" in lower:
            return {"type": "no_dnf", "target": None, "probability": 0.14, "label": "No DNF"}

        if dnf_count and dnf_count >= 3:
            probability = 0.12 if dnf_count == 3 else 0.08
            return {"type": "at_least_n_dnf", "target": None, "n": dnf_count, "probability": probability, "label": f"At Least {dnf_count} DNF"}

        if "double dnf" in lower or ("both" in lower and "dnf" in lower):
            return {"type": "double_dnf", "target": team, "probability": 0.08, "label": "Double DNF"}

        if "double points" in lower or ("both" in lower and "points" in lower):
            return {"type": "double_points", "target": team, "probability": 0.22, "label": "Double Points"}

        if "fastest lap" in lower:
            return {"type": "fastest_lap", "target": driver, "probability": 0.16, "label": "Fastest Lap"}

        if "red flag" in lower or "redflag" in compact:
            return {"type": "red_flag", "target": None, "probability": 0.18, "label": "Red Flag"}

        if "no safety car" in lower or "nosafetycar" in compact:
            return {"type": "no_safety_car", "target": None, "probability": 0.28, "label": "No Safety Car"}

        if "safety car" in lower or "safetycar" in compact or "vsc" in lower:
            return {"type": "safety_car", "target": None, "probability": 0.72, "label": "Safety Car"}

        if "q3" in lower:
            if team and ("both" in lower or "team" in lower or "constructor" in lower):
                return {"type": "team_double_q3", "target": team, "probability": 0.12, "label": "Team Double Q3"}
            return {"type": "q3_driver", "target": driver, "probability": 0.21, "label": "Q3 Appearance"}

        if ("q1" in lower and ("out" in lower or "eliminated" in lower)) or "q1 exit" in lower:
            if team and ("both" in lower or "team" in lower):
                return {"type": "team_double_q1_exit", "target": team, "probability": 0.10, "label": "Team Double Q1 Exit"}
            return {"type": "q1_exit_driver", "target": driver, "probability": 0.18, "label": "Q1 Exit"}

        if "podium" in lower and grid_floor:
            probability = 0.09 if grid_floor >= 10 else 0.15
            return {"type": "podium_from_grid", "target": driver, "grid_floor": grid_floor, "probability": probability, "label": f"Podium from P{grid_floor}+"}

        if "podium" in lower:
            return {"type": "podium", "target": driver, "probability": 0.18, "label": "Podium"}

        if "winner" in lower or " wins" in lower or " win " in f" {lower} ":
            return {"type": "win", "target": driver, "probability": 0.09, "label": "Race Winner"}

        if "top 5" in lower or "top5" in compact:
            return {"type": "top5", "target": driver, "probability": 0.27, "label": "Top 5"}

        if ("top 10" in lower or "top10" in compact) and grid_floor:
            probability = 0.14 if grid_floor >= 15 else 0.24
            return {"type": "top10_from_grid", "target": driver, "grid_floor": grid_floor, "probability": probability, "label": f"Top 10 from P{grid_floor}+"}

        if "no points" in lower or "pointless" in lower:
            if team and ("team" in lower or "constructor" in lower or "both" in lower):
                return {"type": "team_no_points", "target": team, "probability": 0.20, "label": "Team No Points"}
            return {"type": "no_points", "target": driver, "probability": 0.24, "label": "No Points"}

        if "points" in lower and grid_floor and grid_floor >= 12:
            probability = 0.14 if grid_floor >= 15 else 0.24
            return {"type": "points_from_grid", "target": driver, "grid_floor": grid_floor, "probability": probability, "label": f"Points from P{grid_floor}+"}

        if "points" in lower and "double" not in lower and "both" not in lower:
            if team and ("team" in lower or "constructor" in lower):
                return {"type": "team_points", "target": team, "probability": 0.29, "label": "Team Points"}
            return {"type": "points_finish", "target": driver, "probability": 0.25, "label": "Driver Points"}

        if "dnf" in lower:
            if team and ("team" in lower or "both" in lower):
                return {"type": "team_dnf_any", "target": team, "probability": 0.26, "label": "Team DNF"}
            return {"type": "driver_dnf", "target": driver, "probability": 0.24, "label": "Driver DNF"}

        return {"type": "unknown", "target": None, "probability": 1.0, "label": "Unparsed"}

    def _get_driver_record(self, round_data: Dict[str, Any], target: Optional[str]) -> Optional[Dict[str, Any]]:
        if not target:
            return None
        drivers = round_data.get("official", {}).get("weekend_data", {}).get("drivers", {})
        key = normalize_text(target)
        if key in drivers:
            return drivers[key]
        for dkey, record in drivers.items():
            if key and (key in dkey or dkey in key):
                return record
        return None

    def _get_team_records(self, round_data: Dict[str, Any], target: Optional[str]) -> List[Dict[str, Any]]:
        if not target:
            return []
        key = normalize_text(target)
        out = []
        for rec in round_data.get("official", {}).get("weekend_data", {}).get("drivers", {}).values():
            t = normalize_text(rec.get("team", ""))
            if key and (key == t or key in t or t in key):
                out.append(rec)
        return out

    def _group_records_by_team(self, round_data: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for record in round_data.get("official", {}).get("weekend_data", {}).get("drivers", {}).values():
            team_key = normalize_text(record.get("team", ""))
            if not team_key:
                continue
            grouped.setdefault(team_key, []).append(record)
        return grouped

    def _is_points_finish(self, record: Dict[str, Any]) -> bool:
        return int(record.get("finish", 99)) <= 10 and normalize_text(record.get("status", "")) not in {
            "dnf",
            "dns",
            "disq",
            "dsq",
        }

    def _race_safety_car(self, round_data: Dict[str, Any]) -> Optional[bool]:
        core_sc = round_data.get("official", {}).get("core", {}).get("safety_car")
        if isinstance(core_sc, bool):
            return core_sc
        wk_sc = round_data.get("official", {}).get("weekend_data", {}).get("safety_car")
        return wk_sc if isinstance(wk_sc, bool) else None

    def _evaluate_bold(self, rule: Dict[str, Any], round_data: Dict[str, Any]) -> bool:
        rtype = rule.get("type")
        target = rule.get("target")
        weekend = round_data.get("official", {}).get("weekend_data", {})

        def team_records() -> List[Dict[str, Any]]:
            if target:
                return self._get_team_records(round_data, target)
            return []

        def all_teams() -> Dict[str, List[Dict[str, Any]]]:
            return self._group_records_by_team(round_data)

        if rtype == "pole_to_win":
            pole = round_data.get("official", {}).get("core", {}).get("pole")
            winner = round_data.get("official", {}).get("core", {}).get("p1")
            return bool(pole and winner and same_name(pole, winner))

        if rtype == "team_double_podium":
            if target:
                return sum(1 for rec in team_records() if int(rec.get("finish", 99)) <= 3) >= 2
            return any(
                sum(1 for rec in members if int(rec.get("finish", 99)) <= 3) >= 2
                for members in all_teams().values()
            )

        if rtype == "team_podium_any":
            if target:
                return any(int(rec.get("finish", 99)) <= 3 for rec in team_records())
            return any(
                any(int(rec.get("finish", 99)) <= 3 for rec in members)
                for members in all_teams().values()
            )

        if rtype == "team_double_q3":
            if target:
                return sum(1 for rec in team_records() if int(rec.get("grid", 99)) <= 10) >= 2
            return any(
                sum(1 for rec in members if int(rec.get("grid", 99)) <= 10) >= 2
                for members in all_teams().values()
            )

        if rtype == "team_double_q1_exit":
            if target:
                return sum(1 for rec in team_records() if int(rec.get("grid", 99)) > 15) >= 2
            return any(
                sum(1 for rec in members if int(rec.get("grid", 99)) > 15) >= 2
                for members in all_teams().values()
            )

        if rtype == "no_dnf":
            drivers = weekend.get("drivers", {})
            return bool(drivers) and all(not rec.get("dnf", False) for rec in drivers.values())

        if rtype == "at_least_n_dnf":
            threshold = int(rule.get("n") or 3)
            drivers = weekend.get("drivers", {})
            return sum(1 for rec in drivers.values() if rec.get("dnf", False)) >= threshold

        if rtype == "double_dnf":
            if target:
                return sum(1 for rec in team_records() if rec.get("dnf")) >= 2
            return any(
                sum(1 for rec in members if rec.get("dnf")) >= 2
                for members in all_teams().values()
            )
        if rtype == "double_points":
            if target:
                return sum(1 for rec in team_records() if self._is_points_finish(rec)) >= 2
            return any(
                sum(1 for rec in members if self._is_points_finish(rec)) >= 2
                for members in all_teams().values()
            )
        if rtype == "fastest_lap":
            fastest = weekend.get("fastest_lap_driver")
            return bool(target and fastest and same_name(str(target), str(fastest)))
        if rtype == "podium":
            rec = self._get_driver_record(round_data, target)
            return bool(rec and int(rec.get("finish", 99)) <= 3)
        if rtype == "podium_from_p10":
            rec = self._get_driver_record(round_data, target)
            return bool(rec and int(rec.get("grid", 0)) >= 10 and int(rec.get("finish", 99)) <= 3)
        if rtype == "podium_from_grid":
            rec = self._get_driver_record(round_data, target)
            floor = int(rule.get("grid_floor") or 10)
            return bool(rec and int(rec.get("grid", 0)) >= floor and int(rec.get("finish", 99)) <= 3)
        if rtype == "win":
            rec = self._get_driver_record(round_data, target)
            return bool(rec and int(rec.get("finish", 99)) == 1)
        if rtype == "red_flag":
            return bool(weekend.get("red_flag", False))
        if rtype == "no_safety_car":
            return self._race_safety_car(round_data) is False
        if rtype == "safety_car":
            return self._race_safety_car(round_data) is True
        if rtype == "top5":
            rec = self._get_driver_record(round_data, target)
            return bool(rec and int(rec.get("finish", 99)) <= 5)
        if rtype == "top10_from_grid":
            rec = self._get_driver_record(round_data, target)
            floor = int(rule.get("grid_floor") or 15)
            return bool(rec and int(rec.get("grid", 0)) >= floor and int(rec.get("finish", 99)) <= 10)
        if rtype == "q3_driver":
            rec = self._get_driver_record(round_data, target)
            return bool(rec and int(rec.get("grid", 99)) <= 10)
        if rtype == "q1_exit_driver":
            rec = self._get_driver_record(round_data, target)
            return bool(rec and int(rec.get("grid", 0)) > 15)
        if rtype == "points_finish":
            rec = self._get_driver_record(round_data, target)
            return bool(rec and self._is_points_finish(rec))
        if rtype == "points_from_grid":
            rec = self._get_driver_record(round_data, target)
            floor = int(rule.get("grid_floor") or 15)
            return bool(rec and int(rec.get("grid", 0)) >= floor and self._is_points_finish(rec))
        if rtype == "no_points":
            rec = self._get_driver_record(round_data, target)
            return bool(rec and not self._is_points_finish(rec))
        if rtype == "team_points":
            if target:
                return any(self._is_points_finish(rec) for rec in team_records())
            return any(
                any(self._is_points_finish(rec) for rec in members)
                for members in all_teams().values()
            )
        if rtype == "team_no_points":
            if target:
                records = team_records()
                return bool(records) and all(not self._is_points_finish(rec) for rec in records)
            return any(
                members and all(not self._is_points_finish(rec) for rec in members)
                for members in all_teams().values()
            )
        if rtype == "driver_dnf":
            rec = self._get_driver_record(round_data, target)
            return bool(rec and rec.get("dnf"))
        if rtype == "team_dnf_any":
            if target:
                return any(rec.get("dnf") for rec in team_records())
            return any(
                any(rec.get("dnf") for rec in members)
                for members in all_teams().values()
            )
        return False

    def _score_bold(self, user_id: str, advanced: Dict[str, Any], round_data: Dict[str, Any]) -> Tuple[float, str]:
        overrides = round_data.get("bold_overrides", {})
        if user_id in overrides:
            try:
                return float(overrides[user_id]), "Manual override"
            except (TypeError, ValueError):
                pass
        rule = advanced.get("bold_detection") or self._detect_bold_prediction(advanced.get("bold_text", ""), round_data)
        refreshed = self._detect_bold_prediction(advanced.get("bold_text", ""), round_data)
        if rule.get("type") == "unknown" and refreshed.get("type") != "unknown":
            rule = refreshed
        elif not rule.get("target") and refreshed.get("target"):
            rule["target"] = refreshed.get("target")
        p = float(rule.get("probability", 1.0))
        if p > 0.30:
            return 0.0, "Not bold enough"
        if rule.get("type") == "unknown":
            return 0.0, "Unparsed"
        if not self._evaluate_bold(rule, round_data):
            return 0.0, "Incorrect"
        return (2.0, "Very Bold Hit") if p <= 0.10 else (1.0, "Bold Hit")

    def _score_round(self, round_data: Dict[str, Any]) -> Dict[str, Any]:
        scores: Dict[str, Any] = {}
        subs = round_data.get("submissions", {})
        official = round_data.get("official", {})
        core_ans = official.get("core", {})
        sprint_ans = official.get("sprint", {})
        flop_ans = official.get("flop", {})
        surprise_ans = official.get("surprise", {})
        qotw = round_data.get("qotw", {})

        for user_id, sub in subs.items():
            total = 0.0
            bd: Dict[str, Any] = {}
            core = sub.get("core")
            if core:
                for key in ("p1", "p2", "p3", "pole"):
                    ok = bool(core_ans.get(key) and same_name(core.get(key), core_ans.get(key)))
                    bd[f"core_{key}"] = 1 if ok else 0
                    total += 1 if ok else 0
                if isinstance(core_ans.get("safety_car"), bool):
                    ok = core.get("safety_car") is core_ans.get("safety_car")
                    bd["core_safety_car"] = 1 if ok else 0
                    total += 1 if ok else 0

            adv = sub.get("advanced")
            if adv:
                for key, ans_key in (
                    ("flop_driver", "driver"),
                    ("flop_team", "team"),
                    ("surprise_driver", "driver"),
                    ("surprise_team", "team"),
                ):
                    answers = flop_ans if key.startswith("flop") else surprise_ans
                    ok = bool(answers.get(ans_key) and same_name(adv.get(key), answers.get(ans_key)))
                    bd[key] = 1 if ok else 0
                    total += 1 if ok else 0
                bold_points, bold_note = self._score_bold(user_id, adv, round_data)
                bd["bold"] = bold_points
                bd["bold_note"] = bold_note
                total += bold_points
                if bold_points >= 2:
                    bd["very_bold_hit"] = 1

            qsub = sub.get("qotw")
            if qsub and qotw.get("correct_answer") is not None and qotw.get("answer_type"):
                if qotw["answer_type"] == "boolean":
                    ok = qsub.get("answer") is qotw.get("correct_answer")
                else:
                    ok = same_name(str(qsub.get("answer")), str(qotw.get("correct_answer")))
                bd["qotw"] = 1 if ok else 0
                total += 1 if ok else 0

            sprint = sub.get("sprint")
            if sprint:
                for key in ("p1", "p2", "p3", "pole"):
                    ok = bool(sprint_ans.get(key) and same_name(sprint.get(key), sprint_ans.get(key)))
                    bd[f"sprint_{key}"] = 0.5 if ok else 0.0
                    total += 0.5 if ok else 0.0
                if isinstance(sprint_ans.get("safety_car"), bool):
                    ok = sprint.get("safety_car") is sprint_ans.get("safety_car")
                    bd["sprint_safety_car"] = 0.5 if ok else 0.0
                    total += 0.5 if ok else 0.0

            scores[user_id] = {"total": round(total, 2), "breakdown": bd, "scored_at": to_iso(utcnow())}
        return scores

    def _compute_flop_surprise(self, round_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        drivers = round_data.get("official", {}).get("weekend_data", {}).get("drivers", {})
        if not drivers:
            return None
        driver_idx: Dict[str, Dict[str, Any]] = {}
        teams: Dict[str, Dict[str, Any]] = {}
        for dkey, rec in drivers.items():
            grid = int(rec.get("grid", 20))
            finish = int(rec.get("finish", 20))
            status = normalize_text(rec.get("status", "finished"))
            dnf = bool(rec.get("dnf", False))
            dns = status == "dns"
            penalty = 10 if dns else 6 if dnf else 0
            flop = (finish - grid) + penalty
            surprise = (grid - finish)
            if grid >= 11 and finish <= 3:
                surprise += 2
            if grid >= 16 and finish <= 10:
                surprise += 1
            driver_idx[dkey] = {"name": rec.get("name"), "team": rec.get("team"), "flop": flop, "surprise": surprise, "dnf": dnf}
            tkey = normalize_text(rec.get("team", ""))
            pool = teams.setdefault(tkey, {"name": rec.get("team"), "flops": [], "surprises": [], "dnf_count": 0})
            pool["flops"].append(flop)
            pool["surprises"].append(surprise)
            if dnf:
                pool["dnf_count"] += 1

        flop_driver = max(driver_idx.values(), key=lambda x: x["flop"])
        surprise_driver = max(driver_idx.values(), key=lambda x: x["surprise"])
        team_index: Dict[str, Dict[str, Any]] = {}
        for tkey, data in teams.items():
            avg_flop = sum(data["flops"]) / len(data["flops"])
            avg_surprise = sum(data["surprises"]) / len(data["surprises"])
            team_index[tkey] = {"name": data["name"], "flop": avg_flop + (4 * data["dnf_count"]), "surprise": avg_surprise}
        flop_team = max(team_index.values(), key=lambda x: x["flop"])
        surprise_team = max(team_index.values(), key=lambda x: x["surprise"])

        official = round_data.setdefault("official", {})
        official.setdefault("flop", {})
        official.setdefault("surprise", {})
        official["flop"]["driver"] = flop_driver["name"]
        official["flop"]["team"] = flop_team["name"]
        official["surprise"]["driver"] = surprise_driver["name"]
        official["surprise"]["team"] = surprise_team["name"]
        return {"flop_driver": flop_driver, "flop_team": flop_team, "surprise_driver": surprise_driver, "surprise_team": surprise_team}

    async def _apply_prediction_master_role(self, guild: discord.Guild, round_data: Dict[str, Any]) -> None:
        role_id = await self.config.guild(guild).master_role_id()
        if not role_id:
            return
        role = guild.get_role(role_id)
        if not role:
            return
        scores = round_data.get("scores", {})
        if not scores:
            return
        max_score = max(float(v.get("total", 0.0)) for v in scores.values())
        winners = {int(uid) for uid, data in scores.items() if float(data.get("total", 0.0)) == max_score}
        for member in list(role.members):
            if member.id not in winners:
                try:
                    await member.remove_roles(role, reason="New prediction winners")
                except (discord.Forbidden, discord.HTTPException):
                    pass
        for uid in winners:
            member = guild.get_member(uid)
            if member and role not in member.roles:
                try:
                    await member.add_roles(role, reason="Prediction winner")
                except (discord.Forbidden, discord.HTTPException):
                    pass

    async def _collect_season_stats(self, guild: discord.Guild) -> Dict[str, Any]:
        rounds = await self.config.guild(guild).rounds()
        totals: Dict[str, float] = {}
        weekend_scores: Dict[str, Dict[str, float]] = {}
        weekend_wins: Dict[str, int] = {}
        bold_points: Dict[str, float] = {}
        very_bold_hits: Dict[str, int] = {}
        for rid, rdata in rounds.items():
            scores = rdata.get("scores", {})
            if not scores:
                continue
            max_score = max(float(v.get("total", 0.0)) for v in scores.values())
            winners = [uid for uid, v in scores.items() if float(v.get("total", 0.0)) == max_score]
            for uid, v in scores.items():
                pts = float(v.get("total", 0.0))
                totals[uid] = totals.get(uid, 0.0) + pts
                weekend_scores.setdefault(uid, {})[rid] = pts
                bpts = float(v.get("breakdown", {}).get("bold", 0.0))
                bold_points[uid] = bold_points.get(uid, 0.0) + bpts
                if bpts >= 2:
                    very_bold_hits[uid] = very_bold_hits.get(uid, 0) + 1
            for uid in winners:
                weekend_wins[uid] = weekend_wins.get(uid, 0) + 1
        return {
            "totals": totals,
            "weekend_scores": weekend_scores,
            "weekend_wins": weekend_wins,
            "bold_points": bold_points,
            "very_bold_hits": very_bold_hits,
        }

    def _head_to_head(self, user_a: str, user_b: str, weekend_scores: Dict[str, Dict[str, float]]) -> Tuple[int, int]:
        a = b = 0
        for rid, score_a in weekend_scores.get(user_a, {}).items():
            if rid not in weekend_scores.get(user_b, {}):
                continue
            score_b = weekend_scores[user_b][rid]
            if score_a > score_b:
                a += 1
            elif score_b > score_a:
                b += 1
        return a, b

    def _compare_total_rank(self, user_a: str, user_b: str, stats: Dict[str, Any]) -> int:
        ta, tb = stats["totals"].get(user_a, 0.0), stats["totals"].get(user_b, 0.0)
        if ta != tb:
            return -1 if ta > tb else 1
        h2h_a, h2h_b = self._head_to_head(user_a, user_b, stats["weekend_scores"])
        if h2h_a != h2h_b:
            return -1 if h2h_a > h2h_b else 1
        wa, wb = stats["weekend_wins"].get(user_a, 0), stats["weekend_wins"].get(user_b, 0)
        if wa != wb:
            return -1 if wa > wb else 1
        va, vb = stats["very_bold_hits"].get(user_a, 0), stats["very_bold_hits"].get(user_b, 0)
        if va != vb:
            return -1 if va > vb else 1
        return -1 if int(user_a) < int(user_b) else 1 if int(user_a) > int(user_b) else 0

    async def _post_top10(self, guild: discord.Guild, round_data: Dict[str, Any]) -> None:
        board_id = await self.config.guild(guild).leaderboard_channel_id()
        channel = self._resolve_text_channel(guild, board_id) if board_id else self._resolve_text_channel(guild, await self.config.guild(guild).main_channel_id())
        if not channel:
            return
        stats = await self._collect_season_stats(guild)
        if not stats["totals"]:
            return
        ranked = list(stats["totals"].keys())
        ranked.sort(key=cmp_to_key(lambda a, b: self._compare_total_rank(a, b, stats)))
        embed = discord.Embed(title="F1dex Championship - Top 10", description=f"Posted before next round opens ({round_data.get('name')})", color=EMBED_GOLD, timestamp=utcnow())
        lines = []
        for i, uid in enumerate(ranked[:10], start=1):
            member = guild.get_member(int(uid))
            user_obj = self.bot.get_user(int(uid))
            name = member.mention if member else user_obj.mention if user_obj else f"<@{uid}>"
            lines.append(f"{i}. {name} - {stats['totals'][uid]:.2f} pts")
        embed.add_field(name="Standings", value="\n".join(lines), inline=False)
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

    def _parse_pipe_fields(self, payload: str, count: int) -> Optional[List[str]]:
        parts = [p.strip() for p in payload.split("|")]
        return parts if len(parts) == count else None

    @commands.group(name="f1pred", invoke_without_command=True)
    @commands.guild_only()
    async def f1pred(self, ctx: commands.Context) -> None:
        """F1dex predictions commands."""
        await ctx.send_help()

    @f1pred.command(name="status")
    @commands.guild_only()
    async def f1pred_status(self, ctx: commands.Context) -> None:
        round_id, round_data, _ = await self._get_active_round(ctx.guild)
        queue = await self.config.guild(ctx.guild).queued_round()
        if not round_id or not round_data:
            await ctx.send("No active round configured.")
            return
        embed = discord.Embed(title="F1dex Status", color=EMBED_GOLD)
        embed.add_field(name="Round", value=round_data.get("name", round_id), inline=True)
        embed.add_field(name="ID", value=round_id, inline=True)
        embed.add_field(name="Status", value="OPEN" if self._round_is_open(round_data) else "LOCKED", inline=True)
        embed.add_field(name="Open", value=self._format_dt(parse_iso(round_data.get("open_at"))), inline=False)
        embed.add_field(name="Lock", value=self._format_dt(parse_iso(round_data.get("lock_at"))), inline=True)
        embed.add_field(name="Race End", value=self._format_dt(parse_iso(round_data.get("race_end"))), inline=True)
        openf1_data = round_data.get("openf1", {})
        if openf1_data.get("meeting_key"):
            embed.add_field(name="OpenF1 Meeting", value=str(openf1_data.get("meeting_key")), inline=True)
            embed.add_field(
                name="Last Sync",
                value=self._format_dt(parse_iso(openf1_data.get("synced_at"))),
                inline=True,
            )
        if queue:
            embed.add_field(name="Queued", value=f"{queue.get('name')} ({queue.get('round_id')})", inline=False)
        await ctx.send(embed=embed)

    @f1pred.command(name="me")
    @commands.guild_only()
    async def f1pred_me(self, ctx: commands.Context, round_id: Optional[str] = None) -> None:
        rounds = await self.config.guild(ctx.guild).rounds()
        if round_id is None:
            round_id = await self.config.guild(ctx.guild).active_round_id()
        if not round_id or round_id not in rounds:
            await ctx.send("Round not found.")
            return
        sub = rounds[round_id].get("submissions", {}).get(str(ctx.author.id))
        if not sub:
            await ctx.send("You have no submission for this round.")
            return
        await ctx.send(embed=self._build_prediction_embed(ctx.author, rounds[round_id], sub))

    @f1pred.command(name="leaderboard")
    @commands.guild_only()
    async def f1pred_leaderboard(self, ctx: commands.Context, board_type: str = "total", top: int = 10) -> None:
        board_type = board_type.lower().strip()
        top = max(1, min(top, 25))
        if board_type in {"total", "season"}:
            stats = await self._collect_season_stats(ctx.guild)
            if not stats["totals"]:
                await ctx.send("No scored rounds yet.")
                return
            ranked = list(stats["totals"].keys())
            ranked.sort(key=cmp_to_key(lambda a, b: self._compare_total_rank(a, b, stats)))
            embed = discord.Embed(title="F1dex Championship Leaderboard", color=EMBED_GOLD)
            embed.description = "\n".join(
                f"{i}. {(ctx.guild.get_member(int(uid)).mention if ctx.guild.get_member(int(uid)) else f'<@{uid}>')} - {stats['totals'][uid]:.2f} pts"
                for i, uid in enumerate(ranked[:top], start=1)
            )
            await ctx.send(embed=embed)
            return
        if board_type in {"weekend", "thisweek", "current"}:
            round_id, round_data, _ = await self._get_active_round(ctx.guild)
            if not round_id or not round_data:
                await ctx.send("No active round configured.")
                return
            scores = round_data.get("scores", {})
            if not scores:
                await ctx.send("Current round has not been scored yet.")
                return
            sorted_scores = sorted(scores.items(), key=lambda pair: float(pair[1].get("total", 0.0)), reverse=True)
            embed = discord.Embed(title=f"Weekend Leaderboard - {round_data.get('name')}", color=EMBED_GREEN)
            embed.description = "\n".join(
                f"{i}. {(ctx.guild.get_member(int(uid)).mention if ctx.guild.get_member(int(uid)) else f'<@{uid}>')} - {float(data.get('total', 0.0)):.2f} pts"
                for i, (uid, data) in enumerate(sorted_scores[:top], start=1)
            )
            await ctx.send(embed=embed)
            return
        if board_type in {"bold", "risk"}:
            stats = await self._collect_season_stats(ctx.guild)
            if not stats["bold_points"]:
                await ctx.send("No bold scores yet.")
                return
            sorted_bold = sorted(stats["bold_points"].items(), key=lambda pair: pair[1], reverse=True)
            embed = discord.Embed(title="Bold Leaderboard", color=EMBED_RED)
            embed.description = "\n".join(
                f"{i}. {(ctx.guild.get_member(int(uid)).mention if ctx.guild.get_member(int(uid)) else f'<@{uid}>')} - {pts:.2f} bold pts | Very Bold: {stats['very_bold_hits'].get(uid, 0)}"
                for i, (uid, pts) in enumerate(sorted_bold[:top], start=1)
            )
            await ctx.send(embed=embed)
            return
        await ctx.send("Leaderboard type must be one of: total, weekend, bold.")

    @f1pred.command(name="boldhelp")
    @commands.guild_only()
    async def f1pred_boldhelp(self, ctx: commands.Context) -> None:
        """Show supported bold parser keywords and prefixes."""
        embed = discord.Embed(title="Bold Parser Help", color=EMBED_RED)
        embed.add_field(
            name="Accepted Prefixes",
            value="`bold:` `hot take:` `prediction:` `call:`",
            inline=False,
        )
        embed.add_field(
            name="Examples",
            value=(
                "`bold: team double points`\n"
                "`hot take: norris podium from p10+`\n"
                "`prediction: at least 3 dnf`\n"
                "`call: ferrari double q3`\n"
                "`bold: verstappen fastest lap`\n"
                "`bold: no safety car`\n"
                "`bold: pole to win`\n"
                "`bold: points from p15+`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Scoring",
            value="<=10% baseline = 2 points, <=30% baseline = 1 point, >30% = 0",
            inline=False,
        )
        await ctx.send(embed=embed)

    @f1pred.group(name="admin", invoke_without_command=True)
    @commands.guild_only()
    @commands.is_owner()
    async def f1pred_admin(self, ctx: commands.Context) -> None:
        await ctx.send_help()

    @f1pred_admin.group(name="openf1", invoke_without_command=True)
    async def f1pred_admin_openf1(self, ctx: commands.Context) -> None:
        """OpenF1 integration commands."""
        await ctx.send_help()

    @f1pred_admin_openf1.command(name="races", aliases=["calendar", "schedule"])
    async def f1pred_admin_openf1_races(
        self, ctx: commands.Context, year: int, limit: int = 30
    ) -> None:
        """List race weekends for a season from OpenF1."""
        limit = max(1, min(limit, 40))
        try:
            weekends = await self._openf1_get_race_weekends(year)
        except Exception as exc:
            await ctx.send(f"OpenF1 error: {exc}")
            return

        if not weekends:
            await ctx.send(f"No race weekends found for {year} on OpenF1.")
            return

        embed = discord.Embed(title=f"OpenF1 Race Weekends {year}", color=EMBED_GOLD)
        lines = []
        for index, weekend in enumerate(weekends[:limit], start=1):
            start = parse_iso(weekend.get("race_date_start"))
            start_text = self._format_dt(start) if start else "Unknown date"
            lines.append(
                f"{index}. `{weekend['meeting_key']}` - {weekend['meeting_name']} ({weekend.get('location')}) - {start_text}"
            )
        embed.description = "\n".join(lines)
        await ctx.send(embed=embed)

    @f1pred_admin_openf1.command(name="create", aliases=["startround"])
    async def f1pred_admin_openf1_create(
        self,
        ctx: commands.Context,
        meeting_key: int,
        round_id: Optional[str] = None,
        *,
        name: Optional[str] = None,
    ) -> None:
        """Create and activate a round from OpenF1 meeting_key."""
        try:
            round_data = await self._openf1_build_round(
                meeting_key=meeting_key,
                round_id=round_id,
                name=name,
            )
        except Exception as exc:
            await ctx.send(f"OpenF1 error: {exc}")
            return

        conf = self.config.guild(ctx.guild)
        rounds = await conf.rounds()
        rounds[round_data["round_id"]] = round_data
        await conf.rounds.set(rounds)
        await conf.active_round_id.set(round_data["round_id"])

        posted, _ = await self._post_or_update_round_embeds(ctx.guild, round_data)
        if posted:
            await ctx.send(
                f"Round `{round_data['name']}` created from OpenF1, activated, and posted."
            )
        else:
            await ctx.send(
                f"Round `{round_data['name']}` created from OpenF1 and activated. Set main channel then post."
            )

    @f1pred_admin_openf1.command(name="createbyround", aliases=["round"])
    async def f1pred_admin_openf1_createbyround(
        self,
        ctx: commands.Context,
        year: int,
        round_number: int,
        round_id: Optional[str] = None,
        *,
        name: Optional[str] = None,
    ) -> None:
        """Create and activate round by season round number (race order)."""
        if round_number < 1:
            await ctx.send("round_number must be 1 or greater.")
            return
        try:
            weekends = await self._openf1_get_race_weekends(year)
        except Exception as exc:
            await ctx.send(f"OpenF1 error: {exc}")
            return
        if round_number > len(weekends):
            await ctx.send(f"Only {len(weekends)} race weekends found for {year}.")
            return
        meeting_key = int(weekends[round_number - 1]["meeting_key"])
        try:
            round_data = await self._openf1_build_round(
                meeting_key=meeting_key,
                round_id=round_id,
                name=name,
            )
        except Exception as exc:
            await ctx.send(f"OpenF1 error: {exc}")
            return

        conf = self.config.guild(ctx.guild)
        rounds = await conf.rounds()
        rounds[round_data["round_id"]] = round_data
        await conf.rounds.set(rounds)
        await conf.active_round_id.set(round_data["round_id"])

        posted, _ = await self._post_or_update_round_embeds(ctx.guild, round_data)
        if posted:
            await ctx.send(
                f"Round `{round_data['name']}` (R{round_number}) created from OpenF1, activated, and posted."
            )
        else:
            await ctx.send(
                f"Round `{round_data['name']}` (R{round_number}) created from OpenF1 and activated."
            )

    @f1pred_admin_openf1.command(name="queue", aliases=["queueround"])
    async def f1pred_admin_openf1_queue(
        self,
        ctx: commands.Context,
        meeting_key: int,
        round_id: Optional[str] = None,
        *,
        name: Optional[str] = None,
    ) -> None:
        """Queue next round from OpenF1. Opens at active race_end + 48h."""
        _, active, _ = await self._get_active_round(ctx.guild)
        if active and parse_iso(active.get("race_end")):
            open_at = parse_iso(active["race_end"]) + timedelta(hours=48)
        else:
            open_at = utcnow()
        try:
            queued = await self._openf1_build_round(
                meeting_key=meeting_key,
                round_id=round_id,
                name=name,
                open_at=open_at,
            )
        except Exception as exc:
            await ctx.send(f"OpenF1 error: {exc}")
            return
        await self.config.guild(ctx.guild).queued_round.set(queued)
        await ctx.send(
            f"Queued `{queued['name']}` from OpenF1. Opens at {self._format_dt(open_at)}."
        )

    @f1pred_admin_openf1.command(name="bind", aliases=["setmeeting"])
    async def f1pred_admin_openf1_bind(
        self, ctx: commands.Context, meeting_key: int, update_times: bool = False
    ) -> None:
        """Bind active round to an OpenF1 meeting key. Optionally update lock/race times."""
        round_id, round_data, rounds = await self._get_active_round(ctx.guild)
        if not round_id or not round_data:
            await ctx.send("No active round configured.")
            return

        try:
            template = await self._openf1_build_round(
                meeting_key=meeting_key,
                round_id=round_id,
                name=round_data.get("name"),
                open_at=parse_iso(round_data.get("open_at")) or utcnow(),
            )
        except Exception as exc:
            await ctx.send(f"OpenF1 error: {exc}")
            return

        round_data.setdefault("openf1", {})
        round_data["openf1"].update(template.get("openf1", {}))
        round_data["sprint_enabled"] = bool(template.get("sprint_enabled", False))
        if update_times:
            round_data["lock_at"] = template.get("lock_at")
            round_data["race_end"] = template.get("race_end")
            race_end = parse_iso(round_data.get("race_end"))
            if race_end:
                round_data["top10_post_at"] = to_iso(race_end + timedelta(hours=47, minutes=45))
            round_data["is_open"] = self._round_is_open(round_data)

        await self._save_round(ctx.guild, round_id, round_data, rounds)
        await self._refresh_round_messages(ctx.guild, round_data)
        await ctx.send(
            f"Bound active round to OpenF1 meeting `{meeting_key}`."
            + (" Times updated from OpenF1." if update_times else "")
        )

    @f1pred_admin_openf1.command(name="sync", aliases=["pull", "syncresults"])
    async def f1pred_admin_openf1_sync(self, ctx: commands.Context) -> None:
        """Sync active round results from OpenF1 (core/sprint/safety/flags/flop/surprise)."""
        round_id, round_data, rounds = await self._get_active_round(ctx.guild)
        if not round_id or not round_data:
            await ctx.send("No active round configured.")
            return
        if not round_data.get("openf1", {}).get("meeting_key"):
            await ctx.send(
                "Active round has no OpenF1 meeting key. Create with `f1pred admin openf1 create`."
            )
            return

        try:
            summary = await self._openf1_sync_round_results(round_data)
        except Exception as exc:
            await ctx.send(f"OpenF1 sync failed: {exc}")
            return

        await self._save_round(ctx.guild, round_id, round_data, rounds)
        embed = discord.Embed(title="OpenF1 Sync Complete", color=EMBED_GREEN)
        embed.add_field(name="Round", value=round_data.get("name"), inline=True)
        embed.add_field(name="Meeting Key", value=str(summary["meeting_key"]), inline=True)
        embed.add_field(name="Drivers", value=str(summary["drivers_loaded"]), inline=True)
        embed.add_field(
            name="Core",
            value=(
                f"P1: {round_data.get('official', {}).get('core', {}).get('p1')}\n"
                f"P2: {round_data.get('official', {}).get('core', {}).get('p2')}\n"
                f"P3: {round_data.get('official', {}).get('core', {}).get('p3')}\n"
                f"Pole: {round_data.get('official', {}).get('core', {}).get('pole')}\n"
                f"SC: {'Y' if round_data.get('official', {}).get('core', {}).get('safety_car') else 'N'}"
            ),
            inline=False,
        )
        if round_data.get("sprint_enabled"):
            sprint = round_data.get("official", {}).get("sprint", {})
            embed.add_field(
                name="Sprint",
                value=(
                    f"P1: {sprint.get('p1')}\nP2: {sprint.get('p2')}\nP3: {sprint.get('p3')}\n"
                    f"Pole: {sprint.get('pole')}\nSC: {'Y' if sprint.get('safety_car') else 'N'}"
                ),
                inline=False,
            )
        embed.add_field(
            name="Flags",
            value=(
                f"Safety Car: {'Y' if summary['safety_car'] else 'N'}\n"
                f"Red Flag: {'Y' if summary['red_flag'] else 'N'}"
            ),
            inline=True,
        )
        embed.add_field(
            name="Fastest Lap",
            value=summary.get("fastest_lap_driver") or "Unknown",
            inline=True,
        )
        await ctx.send(embed=embed)

    @f1pred_admin.command(name="setmain")
    async def f1pred_admin_setmain(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
        await self.config.guild(ctx.guild).main_channel_id.set(channel.id)
        await ctx.send(f"Main channel set to {channel.mention}.")

    @f1pred_admin.command(name="setlog")
    async def f1pred_admin_setlog(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
        await self.config.guild(ctx.guild).log_channel_id.set(channel.id)
        await ctx.send(f"Log channel set to {channel.mention}.")

    @f1pred_admin.command(name="setboard")
    async def f1pred_admin_setboard(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
        await self.config.guild(ctx.guild).leaderboard_channel_id.set(channel.id)
        await ctx.send(f"Leaderboard channel set to {channel.mention}.")

    @f1pred_admin.command(name="setmasterrole")
    async def f1pred_admin_setmasterrole(self, ctx: commands.Context, role: discord.Role) -> None:
        await self.config.guild(ctx.guild).master_role_id.set(role.id)
        await ctx.send(f"Master role set to {role.mention}.")

    @f1pred_admin.command(name="create")
    async def f1pred_admin_create(self, ctx: commands.Context, round_id: str, lock_at_utc: str, race_end_utc: str, sprint_enabled: bool = False, *, name: Optional[str] = None) -> None:
        lock_at = parse_iso(lock_at_utc)
        race_end = parse_iso(race_end_utc)
        if not lock_at or not race_end:
            await ctx.send("Invalid datetime format. Use ISO with timezone.")
            return
        round_name = name or round_id.replace("-", " ").title()
        rdata = self._new_round(round_id, round_name, utcnow(), lock_at, race_end, sprint_enabled)
        conf = self.config.guild(ctx.guild)
        rounds = await conf.rounds()
        rounds[round_id] = rdata
        await conf.rounds.set(rounds)
        await conf.active_round_id.set(round_id)
        posted, _ = await self._post_or_update_round_embeds(ctx.guild, rdata)
        if posted:
            await ctx.send(f"Round `{round_name}` created, activated, and posted.")
        else:
            await ctx.send(f"Round `{round_name}` created and activated. Run `f1pred admin post` after setting main channel.")

    @f1pred_admin.command(name="queue")
    async def f1pred_admin_queue(self, ctx: commands.Context, round_id: str, lock_at_utc: str, race_end_utc: str, sprint_enabled: bool = False, *, name: Optional[str] = None) -> None:
        lock_at = parse_iso(lock_at_utc)
        race_end = parse_iso(race_end_utc)
        if not lock_at or not race_end:
            await ctx.send("Invalid datetime format. Use ISO with timezone.")
            return
        _, active, _ = await self._get_active_round(ctx.guild)
        if active and parse_iso(active.get("race_end")):
            open_at = parse_iso(active.get("race_end")) + timedelta(hours=48)
        else:
            open_at = utcnow()
        round_name = name or round_id.replace("-", " ").title()
        queued = self._new_round(round_id, round_name, open_at, lock_at, race_end, sprint_enabled)
        await self.config.guild(ctx.guild).queued_round.set(queued)
        await ctx.send(f"Queued `{round_name}`. Opens at {self._format_dt(open_at)}.")

    @f1pred_admin.command(name="post")
    async def f1pred_admin_post(self, ctx: commands.Context) -> None:
        rid, rdata, _ = await self._get_active_round(ctx.guild)
        if not rid or not rdata:
            await ctx.send("No active round configured.")
            return
        ok, msg = await self._post_or_update_round_embeds(ctx.guild, rdata)
        await ctx.send("Round message posted/updated." if ok else msg)

    @f1pred_admin.command(name="locknow")
    async def f1pred_admin_locknow(self, ctx: commands.Context) -> None:
        rid, rdata, rounds = await self._get_active_round(ctx.guild)
        if not rid or not rdata:
            await ctx.send("No active round configured.")
            return
        rdata["is_open"] = False
        rdata["locked_at"] = to_iso(utcnow())
        await self._save_round(ctx.guild, rid, rdata, rounds)
        await self._refresh_round_messages(ctx.guild, rdata)
        await ctx.send("Round locked.")

    @f1pred_admin.command(name="unlock")
    async def f1pred_admin_unlock(self, ctx: commands.Context) -> None:
        rid, rdata, rounds = await self._get_active_round(ctx.guild)
        if not rid or not rdata:
            await ctx.send("No active round configured.")
            return
        rdata["is_open"] = True
        rdata["locked_at"] = None
        await self._save_round(ctx.guild, rid, rdata, rounds)
        await self._refresh_round_messages(ctx.guild, rdata)
        await ctx.send("Round unlocked.")

    @f1pred_admin.command(name="setqotw")
    async def f1pred_admin_setqotw(self, ctx: commands.Context, answer_type: str, *, prompt: str) -> None:
        answer_type = answer_type.lower().strip()
        if answer_type not in {"driver", "team", "boolean"}:
            await ctx.send("answer_type must be driver, team, or boolean.")
            return
        rid, rdata, rounds = await self._get_active_round(ctx.guild)
        if not rid or not rdata:
            await ctx.send("No active round configured.")
            return
        rdata.setdefault("qotw", {})
        rdata["qotw"].update({"prompt": prompt, "answer_type": answer_type, "correct_answer": None})
        await self._save_round(ctx.guild, rid, rdata, rounds)
        await ctx.send("QOTW configured.")

    @f1pred_admin.command(name="setqotwanswer")
    async def f1pred_admin_setqotwanswer(self, ctx: commands.Context, *, answer: str) -> None:
        rid, rdata, rounds = await self._get_active_round(ctx.guild)
        if not rid or not rdata:
            await ctx.send("No active round configured.")
            return
        qotw = rdata.setdefault("qotw", {})
        qtype = qotw.get("answer_type")
        if not qtype:
            await ctx.send("QOTW is not configured.")
            return
        out: Any = answer.strip()
        if qtype == "boolean":
            parsed = parse_yes_no(answer)
            if parsed is None:
                await ctx.send("Boolean QOTW answer must be Y or N.")
                return
            out = parsed
        qotw["correct_answer"] = out
        await self._save_round(ctx.guild, rid, rdata, rounds)
        await ctx.send("QOTW correct answer saved.")

    @f1pred_admin.command(name="setcore")
    async def f1pred_admin_setcore(self, ctx: commands.Context, *, payload: str) -> None:
        fields = self._parse_pipe_fields(payload, 5)
        if not fields:
            await ctx.send("Use format: p1|p2|p3|pole|safetycar(Y/N)")
            return
        sc = parse_yes_no(fields[4])
        if sc is None:
            await ctx.send("Safety car must be Y or N.")
            return
        rid, rdata, rounds = await self._get_active_round(ctx.guild)
        if not rid or not rdata:
            await ctx.send("No active round configured.")
            return
        core = rdata.setdefault("official", {}).setdefault("core", {})
        core.update({"p1": fields[0], "p2": fields[1], "p3": fields[2], "pole": fields[3], "safety_car": sc})
        await self._save_round(ctx.guild, rid, rdata, rounds)
        await ctx.send("Official core answers saved.")

    @f1pred_admin.command(name="setsprint")
    async def f1pred_admin_setsprint(self, ctx: commands.Context, *, payload: str) -> None:
        fields = self._parse_pipe_fields(payload, 5)
        if not fields:
            await ctx.send("Use format: p1|p2|p3|pole|safetycar(Y/N)")
            return
        sc = parse_yes_no(fields[4])
        if sc is None:
            await ctx.send("Safety car must be Y or N.")
            return
        rid, rdata, rounds = await self._get_active_round(ctx.guild)
        if not rid or not rdata:
            await ctx.send("No active round configured.")
            return
        sprint = rdata.setdefault("official", {}).setdefault("sprint", {})
        sprint.update({"p1": fields[0], "p2": fields[1], "p3": fields[2], "pole": fields[3], "safety_car": sc})
        await self._save_round(ctx.guild, rid, rdata, rounds)
        await ctx.send("Official sprint answers saved.")

    @f1pred_admin.command(name="setflop")
    async def f1pred_admin_setflop(self, ctx: commands.Context, *, payload: str) -> None:
        fields = self._parse_pipe_fields(payload, 2)
        if not fields:
            await ctx.send("Use format: driver|team")
            return
        rid, rdata, rounds = await self._get_active_round(ctx.guild)
        if not rid or not rdata:
            await ctx.send("No active round configured.")
            return
        flop = rdata.setdefault("official", {}).setdefault("flop", {})
        flop.update({"driver": fields[0], "team": fields[1]})
        await self._save_round(ctx.guild, rid, rdata, rounds)
        await ctx.send("Official flop answers saved.")

    @f1pred_admin.command(name="setsurprise")
    async def f1pred_admin_setsurprise(self, ctx: commands.Context, *, payload: str) -> None:
        fields = self._parse_pipe_fields(payload, 2)
        if not fields:
            await ctx.send("Use format: driver|team")
            return
        rid, rdata, rounds = await self._get_active_round(ctx.guild)
        if not rid or not rdata:
            await ctx.send("No active round configured.")
            return
        surprise = rdata.setdefault("official", {}).setdefault("surprise", {})
        surprise.update({"driver": fields[0], "team": fields[1]})
        await self._save_round(ctx.guild, rid, rdata, rounds)
        await ctx.send("Official surprise answers saved.")

    @f1pred_admin.command(name="setflags")
    async def f1pred_admin_setflags(self, ctx: commands.Context, safety_car: str, red_flag: str) -> None:
        sc = parse_yes_no(safety_car)
        rf = parse_yes_no(red_flag)
        if sc is None or rf is None:
            await ctx.send("Both flags must be Y or N.")
            return
        rid, rdata, rounds = await self._get_active_round(ctx.guild)
        if not rid or not rdata:
            await ctx.send("No active round configured.")
            return
        wk = rdata.setdefault("official", {}).setdefault("weekend_data", {})
        wk.update({"safety_car": sc, "red_flag": rf})
        await self._save_round(ctx.guild, rid, rdata, rounds)
        await ctx.send("Weekend flags updated.")

    @f1pred_admin.command(name="setdriver")
    async def f1pred_admin_setdriver(self, ctx: commands.Context, *, payload: str) -> None:
        fields = self._parse_pipe_fields(payload, 5)
        if not fields:
            await ctx.send("Use format: driver|team|grid|finish|status")
            return
        try:
            grid = int(fields[2])
            finish = int(fields[3])
        except ValueError:
            await ctx.send("Grid and finish must be integers.")
            return
        status = fields[4].strip().lower()
        dnf = normalize_text(status) in {"dnf", "dns", "disq", "dsq", "retired"}
        rid, rdata, rounds = await self._get_active_round(ctx.guild)
        if not rid or not rdata:
            await ctx.send("No active round configured.")
            return
        wk = rdata.setdefault("official", {}).setdefault("weekend_data", {})
        drivers = wk.setdefault("drivers", {})
        drivers[normalize_text(fields[0])] = {
            "name": fields[0],
            "team": fields[1],
            "grid": grid,
            "finish": finish,
            "status": status,
            "dnf": dnf,
        }
        await self._save_round(ctx.guild, rid, rdata, rounds)
        await ctx.send(f"Saved weekend data for `{fields[0]}`.")

    @f1pred_admin.command(name="clearweekend")
    async def f1pred_admin_clearweekend(self, ctx: commands.Context) -> None:
        rid, rdata, rounds = await self._get_active_round(ctx.guild)
        if not rid or not rdata:
            await ctx.send("No active round configured.")
            return
        rdata.setdefault("official", {}).setdefault("weekend_data", {})["drivers"] = {}
        await self._save_round(ctx.guild, rid, rdata, rounds)
        await ctx.send("Weekend data cleared.")

    @f1pred_admin.command(name="computeouts")
    async def f1pred_admin_computeouts(self, ctx: commands.Context) -> None:
        rid, rdata, rounds = await self._get_active_round(ctx.guild)
        if not rid or not rdata:
            await ctx.send("No active round configured.")
            return
        computed = self._compute_flop_surprise(rdata)
        if not computed:
            await ctx.send("No weekend driver data found.")
            return
        await self._save_round(ctx.guild, rid, rdata, rounds)
        embed = discord.Embed(title="Computed Flop & Surprise", color=EMBED_GREEN)
        embed.add_field(name="Flop Driver", value=f"{computed['flop_driver']['name']} ({computed['flop_driver']['flop']:.2f})", inline=False)
        embed.add_field(name="Flop Team", value=f"{computed['flop_team']['name']} ({computed['flop_team']['flop']:.2f})", inline=False)
        embed.add_field(name="Surprise Driver", value=f"{computed['surprise_driver']['name']} ({computed['surprise_driver']['surprise']:.2f})", inline=False)
        embed.add_field(name="Surprise Team", value=f"{computed['surprise_team']['name']} ({computed['surprise_team']['surprise']:.2f})", inline=False)
        await ctx.send(embed=embed)

    @f1pred_admin.command(name="boldoverride")
    async def f1pred_admin_boldoverride(self, ctx: commands.Context, user: discord.Member, points: float) -> None:
        if points not in {0.0, 1.0, 2.0}:
            await ctx.send("Points must be exactly 0, 1, or 2.")
            return
        rid, rdata, rounds = await self._get_active_round(ctx.guild)
        if not rid or not rdata:
            await ctx.send("No active round configured.")
            return
        rdata.setdefault("bold_overrides", {})[str(user.id)] = points
        await self._save_round(ctx.guild, rid, rdata, rounds)
        await ctx.send(f"Bold override for {user.mention}: {points:.1f}")

    @f1pred_admin.command(name="score")
    async def f1pred_admin_score(self, ctx: commands.Context) -> None:
        rid, rdata, rounds = await self._get_active_round(ctx.guild)
        if not rid or not rdata:
            await ctx.send("No active round configured.")
            return
        scores = self._score_round(rdata)
        rdata["scores"] = scores
        rdata["scored_at"] = to_iso(utcnow())
        await self._save_round(ctx.guild, rid, rdata, rounds)
        await self._apply_prediction_master_role(ctx.guild, rdata)
        if not scores:
            await ctx.send("Round scored. No submissions found.")
            return
        sorted_scores = sorted(scores.items(), key=lambda pair: float(pair[1].get("total", 0.0)), reverse=True)
        embed = discord.Embed(title=f"Scored - {rdata.get('name')}", color=EMBED_GREEN, timestamp=utcnow())
        embed.description = "\n".join(
            f"{i}. {(ctx.guild.get_member(int(uid)).mention if ctx.guild.get_member(int(uid)) else f'<@{uid}>')} - {float(data.get('total', 0.0)):.2f} pts"
            for i, (uid, data) in enumerate(sorted_scores[:10], start=1)
        )
        await ctx.send(embed=embed)
        board = self._resolve_text_channel(ctx.guild, await self.config.guild(ctx.guild).leaderboard_channel_id())
        if board:
            try:
                await board.send(embed=embed)
            except discord.HTTPException:
                pass


