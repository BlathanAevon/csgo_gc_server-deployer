#!/usr/bin/env python3
"""Automated CS:GO Legacy + csgo_gc server deployer for Linux VPS.

Run without arguments for an interactive guided setup.
"""

from __future__ import annotations

import configparser
import getpass
import os
import secrets
import shlex
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

STEAMCMD_TAR_URL = "https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz"
CSGO_GC_URL = "https://github.com/mikkokko/csgo_gc/releases/download/continuous/csgo_gc-ubuntu-latest.zip"
STEAM_TOKEN_URL = "https://steamcommunity.com/dev/managegameservers"
STEAM_APP_ID = 4465480

DEFAULTS_FILE = Path(__file__).with_name("defaults.ini")

_PUBLIC_IP_SERVICES = [
    "https://api.ipify.org",
    "https://ipv4.icanhazip.com",
    "https://checkip.amazonaws.com",
]


def _fetch_public_ip(timeout: int = 5) -> str:
    """Try each public-IP service in order; return the first clean result or ''."""
    for url in _PUBLIC_IP_SERVICES:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
                return resp.read().decode().strip()
        except Exception:  # noqa: BLE001
            continue
    return ""

# ──────────────────────────────────────────────────────────────────────────────
# Config dataclass
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class DeployConfig:
    server_ip: str
    steam_token: str
    hostname: str
    rcon_password: str
    sv_password: str
    port: int
    map_name: str
    max_players: int
    tickrate: int
    steam_user: str
    steam_home: Path
    install_dir: Path
    open_firewall: bool
    allow_web_ports: bool
    session_tool: str
    dry_run: bool
    # Game mode and behavior
    game_mode: str
    mp_warmuptime: int
    mp_freezetime: int
    mp_roundtime: float
    mp_buytime: int
    sv_deadtalk: int
    mp_startmoney: int
    mp_maxmoney: int
    mp_buy_anywhere: int
    mp_autokick: int
    mp_tkpunish: int
    mp_forcecamera: int
    # Bots
    bot_quota: int
    bot_difficulty: int
    bot_controllable: int
    # GOTV
    tv_enable: int
    tv_delaytime: int
    tv_maxclients: int
    # Logging
    sv_logbans: int
    sv_logecho: int
    sv_log_onefile: int
    sv_hibernate_when_empty: int


# ──────────────────────────────────────────────────────────────────────────────
# Defaults file helpers
# ──────────────────────────────────────────────────────────────────────────────

_DEFAULT_SECTION = "defaults"

def _load_defaults() -> dict[str, str]:
    """Return key→value defaults from defaults.ini [defaults] section."""
    cfg = configparser.ConfigParser()
    cfg.read(DEFAULTS_FILE)
    if cfg.has_section(_DEFAULT_SECTION):
        return dict(cfg[_DEFAULT_SECTION])
    return {}


def _save_defaults(values: dict[str, str]) -> None:
    """Persist answered values back to defaults.ini for next run."""
    cfg = configparser.ConfigParser()
    cfg.read(DEFAULTS_FILE)
    if not cfg.has_section(_DEFAULT_SECTION):
        cfg.add_section(_DEFAULT_SECTION)
    # Do not persist secrets
    _SKIP_SAVE = {"steam_token", "rcon_password", "sv_password"}
    for k, v in values.items():
        if k not in _SKIP_SAVE:
            cfg.set(_DEFAULT_SECTION, k, v)
    with DEFAULTS_FILE.open("w") as fh:
        cfg.write(fh)


# ──────────────────────────────────────────────────────────────────────────────
# Prompt helpers
# ──────────────────────────────────────────────────────────────────────────────

_RESET = "\033[0m"
_BOLD  = "\033[1m"
_CYAN  = "\033[36m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED   = "\033[31m"
_DIM   = "\033[2m"


def _c(text: str, *codes: str) -> str:
    """Wrap text in ANSI codes, falling back to plain on non-TTY."""
    if not sys.stdout.isatty():
        return text
    return "".join(codes) + text + _RESET


def _header(text: str) -> None:
    print()
    print(_c(f"  {text}", _BOLD, _CYAN))
    print(_c("  " + "─" * len(text), _DIM))


def _info(text: str) -> None:
    print(_c(f"  {text}", _DIM))


def _ask(
    label: str,
    default: str = "",
    hint: str = "",
    required: bool = False,
    secret: bool = False,
) -> str:
    """
    Prompt the user for a single value.

    Shows:  label [default]: hint
    Pressing Enter accepts the default.
    """
    default_display = _c(f"[{default}]", _DIM) if default else ""
    hint_display = _c(f"  {hint}", _DIM) if hint else ""
    if hint_display:
        print(hint_display)

    prompt = f"  {_c(label, _BOLD)} {default_display}: "

    while True:
        try:
            raw = getpass.getpass(prompt) if secret else input(prompt)
        except EOFError:
            raw = ""

        value = raw.strip() or default
        if required and not value:
            print(_c("  ✗ This field is required.", _RED))
            continue
        return value


def _ask_bool(label: str, default: bool = False, hint: str = "") -> bool:
    """Prompt for a yes/no answer."""
    default_str = "Y/n" if default else "y/N"
    if hint:
        print(_c(f"  {hint}", _DIM))
    prompt = f"  {_c(label, _BOLD)} [{default_str}]: "
    try:
        raw = input(prompt).strip().lower()
    except EOFError:
        raw = ""
    if not raw:
        return default
    return raw in {"y", "yes"}


def _ask_choice(label: str, choices: list[str], default: str, hint: str = "") -> str:
    """Prompt for a value from a fixed set of choices."""
    choices_display = "/".join(
        _c(c, _BOLD) if c == default else c for c in choices
    )
    if hint:
        print(_c(f"  {hint}", _DIM))
    prompt = f"  {_c(label, _BOLD)} [{choices_display}]: "
    while True:
        try:
            raw = input(prompt).strip().lower()
        except EOFError:
            raw = ""
        value = raw or default
        if value in choices:
            return value
        print(_c(f"  ✗ Choose one of: {', '.join(choices)}", _RED))


# ──────────────────────────────────────────────────────────────────────────────
# System / execution helpers
# ──────────────────────────────────────────────────────────────────────────────

def run(cmd: str, dry_run: bool = False, as_user: str | None = None) -> None:
    if as_user:
        command = ["su", "-", as_user, "-c", cmd]
        printable = f"su - {as_user} -c {shlex.quote(cmd)}"
    else:
        command = ["bash", "-lc", cmd]
        printable = cmd

    print(_c(f"  [RUN] {printable}", _DIM))
    if dry_run:
        return
    subprocess.run(command, check=True)


def write_text(path: Path, text: str, dry_run: bool) -> None:
    print(_c(f"  [WRITE] {path}", _DIM))
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def require_root_for_execute(dry_run: bool) -> None:
    if dry_run:
        return
    if os.geteuid() != 0:
        raise RuntimeError(
            "Real execution requires root. Re-run with: sudo python3 csgo_gc_deployer.py"
        )


def disk_check(path: Path) -> None:
    _, _, free = shutil.disk_usage(path)
    free_gb = free / (1024 ** 3)
    if free_gb < 50:
        print(_c(f"  [WARN] Only {free_gb:.1f} GB free near {path}. 50 GB recommended.", _YELLOW))
    else:
        print(_c(f"  [OK]   {free_gb:.1f} GB free near {path}.", _GREEN))


# ──────────────────────────────────────────────────────────────────────────────
# Config rendering helpers
# ──────────────────────────────────────────────────────────────────────────────

def render_server_cfg(cfg: DeployConfig) -> str:
    return (
        f'// Basic server identity\n'
        f'hostname "{cfg.hostname}"\n'
        f'rcon_password "{cfg.rcon_password}"\n'
        f'sv_password "{cfg.sv_password}"\n'
        f'\n'
        f'// Networking\n'
        f'sv_lan 0\n'
        f'sv_region 0\n'
        f'\n'
        f'// Match Timing\n'
        f'mp_warmuptime {cfg.mp_warmuptime}\n'
        f'mp_freezetime {cfg.mp_freezetime}\n'
        f'mp_roundtime {cfg.mp_roundtime}\n'
        f'mp_buytime {cfg.mp_buytime}\n'
        f'\n'
        f'// Communication\n'
        f'sv_deadtalk {cfg.sv_deadtalk}\n'
        f'\n'
        f'// Economy\n'
        f'mp_startmoney {cfg.mp_startmoney}\n'
        f'mp_maxmoney {cfg.mp_maxmoney}\n'
        f'mp_buy_anywhere {cfg.mp_buy_anywhere}\n'
        f'\n'
        f'// Team Dynamics\n'
        f'mp_autokick {cfg.mp_autokick}\n'
        f'mp_tkpunish {cfg.mp_tkpunish}\n'
        f'mp_forcecamera {cfg.mp_forcecamera}\n'
        f'\n'
        f'// Bots\n'
        f'bot_quota {cfg.bot_quota}\n'
        f'bot_difficulty {cfg.bot_difficulty}\n'
        f'bot_controllable {cfg.bot_controllable}\n'
        f'\n'
        f'// GOTV (Game Observation TV) - for spectators\n'
        f'tv_enable {cfg.tv_enable}\n'
        f'tv_delaytime {cfg.tv_delaytime}\n'
        f'tv_maxclients {cfg.tv_maxclients}\n'
        f'\n'
        f'// Logging\n'
        f'sv_logbans {cfg.sv_logbans}\n'
        f'sv_logecho {cfg.sv_logecho}\n'
        f'sv_log_onefile {cfg.sv_log_onefile}\n'
        f'sv_hibernate_when_empty {cfg.sv_hibernate_when_empty}\n'
    )


def start_command(cfg: DeployConfig) -> str:
    return (
        "bash srcds_run -game csgo -console -usercon -insecure "
        f"-tickrate {cfg.tickrate} +port {cfg.port} +map {cfg.map_name} "
        f"-hostip {cfg.server_ip} +ip {cfg.server_ip} "
        f"+sv_setsteamaccount {cfg.steam_token} "
        f"-maxplayers_override {cfg.max_players}"
    )


def create_start_script(cfg: DeployConfig) -> str:
    return (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"cd {shlex.quote(str(cfg.install_dir))}\n"
        f"exec {start_command(cfg)}\n"
    )


# ──────────────────────────────────────────────────────────────────────────────
# Command iterables (unchanged deployment logic)
# ──────────────────────────────────────────────────────────────────────────────

def preinstall_commands(cfg: DeployConfig) -> Iterable[tuple[str, str | None]]:
    yield "dpkg --add-architecture i386", None
    yield "apt-get update", None
    yield (
        "apt-get install -y "
        "lib32gcc-s1 lib32stdc++6 lib32z1 screen tmux tar debsig-verify wget unzip",
        None,
    )
    yield (
        f"id -u {shlex.quote(cfg.steam_user)} >/dev/null 2>&1 || "
        f"useradd -m {shlex.quote(cfg.steam_user)}",
        None,
    )


def install_commands(cfg: DeployConfig) -> Iterable[tuple[str, str | None]]:
    steamcmd_dir = cfg.steam_home / "steamcmd"
    yield f"mkdir -p {shlex.quote(str(steamcmd_dir))}", cfg.steam_user
    yield (
        f"cd {shlex.quote(str(steamcmd_dir))} && "
        f"wget -N {STEAMCMD_TAR_URL} && "
        "tar -xzf steamcmd_linux.tar.gz",
        cfg.steam_user,
    )
    yield (
        "~/steamcmd/steamcmd.sh "
        f"+force_install_dir {shlex.quote(str(cfg.install_dir))} "
        "+login anonymous +app_update 740 validate +quit",
        cfg.steam_user,
    )
    yield (
        f"cd {shlex.quote(str(cfg.install_dir))} && "
        f"wget -N {CSGO_GC_URL} -O csgo_gc-ubuntu-latest.zip && "
        "unzip -o csgo_gc-ubuntu-latest.zip",
        cfg.steam_user,
    )
    # Remove the stale bundled libgcc_s / libstdc++ that CS:GO ships.
    # They conflict with the system's newer 32-bit versions on Ubuntu 22+ and
    # cause the 'GCC_7.0.0 not found' crash loop at startup.
    yield (
        f"rm -f "
        f"{shlex.quote(str(cfg.install_dir / 'bin' / 'libgcc_s.so.1'))} "
        f"{shlex.quote(str(cfg.install_dir / 'bin' / 'libstdc++.so.6'))}",
        cfg.steam_user,
    )


def firewall_commands(cfg: DeployConfig) -> Iterable[tuple[str, str | None]]:
    yield "command -v ufw >/dev/null 2>&1 || apt-get install -y ufw", None
    yield "ufw allow 22", None
    yield f"ufw allow {cfg.port}", None
    if cfg.allow_web_ports:
        yield "ufw allow 80", None
        yield "ufw allow 443", None
    yield "ufw --force enable", None


# ──────────────────────────────────────────────────────────────────────────────
# Interactive wizard
# ──────────────────────────────────────────────────────────────────────────────

def _validate_ip(value: str) -> bool:
    """Basic IPv4 sanity check."""
    parts = value.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in parts)
    except ValueError:
        return False


def _ask_ip(label: str, default: str = "", hint: str = "") -> str:
    while True:
        value = _ask(label, default=default, hint=hint, required=True)
        if _validate_ip(value):
            return value
        print(_c("  ✗ Enter a valid IPv4 address (e.g. 203.0.113.10).", _RED))


def _ask_port(label: str, default: int) -> int:
    while True:
        raw = _ask(label, default=str(default))
        try:
            port = int(raw)
            if 1 <= port <= 65535:
                return port
        except ValueError:
            pass
        print(_c("  ✗ Port must be an integer between 1 and 65535.", _RED))


def _ask_int(label: str, default: int, lo: int, hi: int) -> int:
    while True:
        raw = _ask(label, default=str(default))
        try:
            val = int(raw)
            if lo <= val <= hi:
                return val
        except ValueError:
            pass
        print(_c(f"  ✗ Value must be an integer between {lo} and {hi}.", _RED))


def wizard() -> DeployConfig:
    """Interactive step-by-step configuration wizard."""
    defaults = _load_defaults()
    saved: dict[str, str] = {}

    def d(key: str, fallback: str) -> str:
        return defaults.get(key, fallback)

    # ── Banner ─────────────────────────────────────────────────────────────
    print()
    print(_c(" ╔══════════════════════════════════════════════════╗", _CYAN))
    print(_c(" ║   CS:GO Legacy + csgo_gc  —  Server Deployer     ║", _CYAN, _BOLD))
    print(_c(" ╚══════════════════════════════════════════════════╝", _CYAN))
    print()
    _info("Press Enter to accept a default shown in [brackets].")
    _info("Defaults are remembered between runs in defaults.ini.")

    # ── Step 1: Essential ─────────────────────────────────────────────────
    _header("Step 1 / 4  —  Server identity")

    _detected_ip = d("server_ip", "")
    if not _detected_ip:
        _info("Detecting public IP of this machine…")
        _detected_ip = _fetch_public_ip()
        if _detected_ip:
            _info(f"Detected: {_detected_ip}")
        else:
            _info("Could not detect public IP. Enter it manually.")

    server_ip = _ask_ip(
        "Server public IP",
        default=_detected_ip,
        hint="The VPS IPv4 that players will connect to.",
    )
    saved["server_ip"] = server_ip

    hostname = _ask(
        "Server name",
        default=d("hostname", "My CS:GO_GC Legacy Server"),
        hint="Displayed in the server browser.",
    )
    saved["hostname"] = hostname

    # ── Step 2: Steam token ───────────────────────────────────────────────
    _header("Step 2 / 4  —  Steam token")
    _info(f"Get yours at: {STEAM_TOKEN_URL}")
    _info(f"App ID to use: {STEAM_APP_ID}")

    steam_token = _ask(
        "Steam game server token",
        default="",
        hint="Input is hidden. Required for execute mode; leave blank to dry-run only.",
        secret=True,
    )

    # ── Step 3: Advanced settings (skippable) ─────────────────────────────
    _header("Step 3 / 4  —  Advanced settings")
    customize = _ask_bool(
        "Customize port / start map / max players / tickrate?",
        default=False,
        hint="Skip this to use recommended defaults.",
    )

    if customize:
        port     = _ask_port("Server port",   default=int(d("port", "27015")))
        map_name = _ask("Default map",        default=d("map_name", "de_dust2"))
        max_players = _ask_int("Max players", default=int(d("max_players", "16")), lo=1, hi=64)
        tickrate    = _ask_int("Tickrate",    default=int(d("tickrate", "128")), lo=33, hi=128)
    else:
        port        = int(d("port", "27015"))
        map_name    = d("map_name", "de_dust2")
        max_players = int(d("max_players", "16"))
        tickrate    = int(d("tickrate", "128"))

    saved.update(
        port=str(port),
        map_name=map_name,
        max_players=str(max_players),
        tickrate=str(tickrate),
    )

    # ── Step 3.5: Game Mode & Behavior (skippable) ──────────────────────────
    _header("Step 3.5 / 4  —  Game mode & behavior")
    configure_gamemode = _ask_bool(
        "Customize game mode and behavior?",
        default=False,
        hint="Skip this to use competitive defaults.",
    )

    if configure_gamemode:
        game_mode = _ask_choice(
            "Game mode",
            choices=["competitive", "casual", "deathmatch", "arms_race", "flying_scoutsman"],
            default=d("game_mode", "competitive"),
            hint="Determines game mechanics, economy, and team dynamics.",
        )
        saved["game_mode"] = game_mode

        # Economy presets
        economy_choice = _ask_choice(
            "Economy preset",
            choices=["conservative", "standard", "aggressive"],
            default="standard",
            hint="conservative: Low start money; standard: $2400; aggressive: High starting cash.",
        )
        if economy_choice == "conservative":
            mp_startmoney, mp_maxmoney = 1000, 8000
        elif economy_choice == "aggressive":
            mp_startmoney, mp_maxmoney = 3500, 20000
        else:
            mp_startmoney, mp_maxmoney = 2400, 16000
        saved["mp_startmoney"] = str(mp_startmoney)
        saved["mp_maxmoney"] = str(mp_maxmoney)

        # Match timing
        mp_warmuptime = _ask_int("Warmup time (seconds)", default=int(d("mp_warmuptime", "60")), lo=0, hi=600)
        mp_freezetime = _ask_int("Freeze time (seconds)", default=int(d("mp_freezetime", "15")), lo=0, hi=60)
        mp_roundtime = _ask_int("Round time (minutes)", default=int(d("mp_roundtime", "1")), lo=1, hi=5)
        mp_buytime = _ask_int("Buy time (seconds)", default=int(d("mp_buytime", "20")), lo=0, hi=120)
        saved["mp_warmuptime"] = str(mp_warmuptime)
        saved["mp_freezetime"] = str(mp_freezetime)
        saved["mp_roundtime"] = str(mp_roundtime * 60)  # Convert to seconds (displayed as minutes)
        saved["mp_buytime"] = str(mp_buytime)

        # Team dynamics
        sv_deadtalk = _ask_bool("Dead players can talk to all?", default=bool(int(d("sv_deadtalk", "0"))))
        mp_autokick = _ask_bool("Auto-kick idle players?", default=bool(int(d("mp_autokick", "1"))))
        mp_tkpunish = _ask_bool("Punish team killers?", default=bool(int(d("mp_tkpunish", "0"))))
        mp_forcecamera = _ask_bool("Force dead players spectate killer?", default=bool(int(d("mp_forcecamera", "0"))))
        saved["sv_deadtalk"] = "1" if sv_deadtalk else "0"
        saved["mp_autokick"] = "1" if mp_autokick else "0"
        saved["mp_tkpunish"] = "1" if mp_tkpunish else "0"
        saved["mp_forcecamera"] = "1" if mp_forcecamera else "0"

        # Bot configuration
        bot_quota = _ask_int("Bot count", default=int(d("bot_quota", "10")), lo=0, hi=32)
        bot_difficulty = _ask_choice(
            "Bot difficulty",
            choices=["0", "1", "2", "3"],
            default=d("bot_difficulty", "1"),
            hint="0=easy, 1=normal, 2=hard, 3=expert",
        )
        bot_controllable = _ask_bool("Allow player control of bots?", default=bool(int(d("bot_controllable", "1"))))
        saved["bot_quota"] = str(bot_quota)
        saved["bot_difficulty"] = str(bot_difficulty)
        saved["bot_controllable"] = "1" if bot_controllable else "0"

        # GOTV
        tv_enable = _ask_bool("Enable GOTV (demo recording)?", default=bool(int(d("tv_enable", "1"))))
        tv_maxclients = _ask_int("GOTV max spectators", default=int(d("tv_maxclients", "0")), lo=0, hi=128) if tv_enable else 0
        tv_delaytime = _ask_int("GOTV delay (seconds)", default=int(d("tv_delaytime", "30")), lo=0, hi=120) if tv_enable else 30
        saved["tv_enable"] = "1" if tv_enable else "0"
        saved["tv_maxclients"] = str(tv_maxclients)
        saved["tv_delaytime"] = str(tv_delaytime)

        # Logging
        sv_logbans = _ask_bool("Log bans?", default=bool(int(d("sv_logbans", "1"))))
        sv_logecho = _ask_bool("Echo logs to console?", default=bool(int(d("sv_logecho", "1"))))
        sv_log_onefile = _ask_bool("Log to single file?", default=bool(int(d("sv_log_onefile", "1"))))
        sv_hibernate_when_empty = _ask_bool("Sleep server when empty?", default=bool(int(d("sv_hibernate_when_empty", "0"))))
        saved["sv_logbans"] = "1" if sv_logbans else "0"
        saved["sv_logecho"] = "1" if sv_logecho else "0"
        saved["sv_log_onefile"] = "1" if sv_log_onefile else "0"
        saved["sv_hibernate_when_empty"] = "1" if sv_hibernate_when_empty else "0"
    else:
        # Use defaults for all game mode settings
        game_mode = d("game_mode", "competitive")
        mp_startmoney = int(d("mp_startmoney", "2400"))
        mp_maxmoney = int(d("mp_maxmoney", "16000"))
        mp_warmuptime = int(d("mp_warmuptime", "60"))
        mp_freezetime = int(d("mp_freezetime", "15"))
        mp_roundtime = int(d("mp_roundtime", "115"))  # ~1.92 minutes in seconds
        mp_buytime = int(d("mp_buytime", "20"))
        sv_deadtalk = int(d("sv_deadtalk", "0"))
        mp_autokick = int(d("mp_autokick", "1"))
        mp_tkpunish = int(d("mp_tkpunish", "0"))
        mp_forcecamera = int(d("mp_forcecamera", "0"))
        bot_quota = int(d("bot_quota", "10"))
        bot_difficulty = int(d("bot_difficulty", "1"))
        bot_controllable = int(d("bot_controllable", "1"))
        tv_enable = int(d("tv_enable", "1"))
        tv_maxclients = int(d("tv_maxclients", "0"))
        tv_delaytime = int(d("tv_delaytime", "30"))
        sv_logbans = int(d("sv_logbans", "1"))
        sv_logecho = int(d("sv_logecho", "1"))
        sv_log_onefile = int(d("sv_log_onefile", "1"))
        sv_hibernate_when_empty = int(d("sv_hibernate_when_empty", "0"))

    # ── Step 4: Security & extras ─────────────────────────────────────────
    _header("Step 4 / 4  —  Security & extras")

    rcon_raw = _ask(
        "admin password (RCON)",
        default="",
        hint="Leave blank to auto-generate a secure password.",
        secret=True,
    )
    rcon_password = rcon_raw or secrets.token_urlsafe(16)

    sv_password = _ask(
        "Server join password",
        default="",
        hint="Leave blank for a public (no password) server.",
        secret=True,
    )

    open_firewall = _ask_bool(
        "Configure ufw firewall?",
        default=bool(int(d("open_firewall", "0"))),
        hint="Opens SSH (22) and game port with ufw.",
    )
    saved["open_firewall"] = "1" if open_firewall else "0"

    allow_web_ports = False
    if open_firewall:
        allow_web_ports = _ask_bool(
            "Also open web ports 80 and 443?",
            default=bool(int(d("allow_web_ports", "0"))),
        )
        saved["allow_web_ports"] = "1" if allow_web_ports else "0"

    session_tool = _ask_choice(
        "Session manager",
        choices=["tmux", "screen"],
        default=d("session_tool", "tmux"),
        hint="Used in the startup instructions printed at the end.",
    )
    saved["session_tool"] = session_tool

    steam_user = d("steam_user", "steam")
    steam_home = Path(d("steam_home", "/home/steam"))
    install_dir = Path(d("install_dir", "/home/steam/csgo_server"))

    # ── Dry-run choice ─────────────────────────────────────────────────────
    print()
    dry_run = _ask_bool(
        "Dry-run only? (preview commands without executing)",
        default=True,
        hint="Choose 'n' to actually deploy. Requires running as root.",
    )

    _save_defaults(saved)

    return DeployConfig(
        server_ip=server_ip,
        steam_token=steam_token or "YOUR_STEAM_TOKEN",
        hostname=hostname,
        rcon_password=rcon_password,
        sv_password=sv_password,
        port=port,
        map_name=map_name,
        max_players=max_players,
        tickrate=tickrate,
        steam_user=steam_user,
        steam_home=steam_home,
        install_dir=install_dir,
        open_firewall=open_firewall,
        allow_web_ports=allow_web_ports,
        session_tool=session_tool,
        dry_run=dry_run,
        # Game mode and behavior
        game_mode=game_mode,
        mp_warmuptime=mp_warmuptime,
        mp_freezetime=mp_freezetime,
        mp_roundtime=mp_roundtime,
        mp_buytime=mp_buytime,
        sv_deadtalk=sv_deadtalk,
        mp_startmoney=mp_startmoney,
        mp_maxmoney=mp_maxmoney,
        mp_buy_anywhere=int(d("mp_buy_anywhere", "0")),
        mp_autokick=mp_autokick,
        mp_tkpunish=mp_tkpunish,
        mp_forcecamera=mp_forcecamera,
        # Bots
        bot_quota=bot_quota,
        bot_difficulty=bot_difficulty,
        bot_controllable=bot_controllable,
        # GOTV
        tv_enable=tv_enable,
        tv_delaytime=tv_delaytime,
        tv_maxclients=tv_maxclients,
        # Logging
        sv_logbans=sv_logbans,
        sv_logecho=sv_logecho,
        sv_log_onefile=sv_log_onefile,
        sv_hibernate_when_empty=sv_hibernate_when_empty,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Deployment summary + confirmation
# ──────────────────────────────────────────────────────────────────────────────

def _print_summary(cfg: DeployConfig) -> None:
    _header("Deployment summary")
    rows = [
        ("Dry run",         "YES — no real changes" if cfg.dry_run else _c("NO — WILL CHANGE SYSTEM", _RED, _BOLD)),
        ("Server IP",       cfg.server_ip),
        ("Hostname",        cfg.hostname),
        ("Port",            str(cfg.port)),
        ("Map",             cfg.map_name),
        ("Max players",     str(cfg.max_players)),
        ("Tickrate",        str(cfg.tickrate)),
        ("RCON password",   _c("(set)", _DIM)),
        ("Join password",   _c("(set)", _DIM) if cfg.sv_password else _c("(none — public)", _DIM)),
        ("Steam user",      cfg.steam_user),
        ("Install dir",     str(cfg.install_dir)),
        ("Open firewall",   "yes" if cfg.open_firewall else "no"),
        ("Web ports",       "yes" if cfg.allow_web_ports else "no"),
        ("Session tool",    cfg.session_tool),
    ]
    width = max(len(r[0]) for r in rows)
    for label, value in rows:
        print(f"  {_c(label.ljust(width), _BOLD)}  {value}")


def _confirm_or_abort(cfg: DeployConfig) -> bool:
    _print_summary(cfg)
    print()
    return _ask_bool("Proceed?", default=False)


# ──────────────────────────────────────────────────────────────────────────────
# Post-deploy server launch
# ──────────────────────────────────────────────────────────────────────────────

def _offer_launch(cfg: DeployConfig) -> None:
    """Offer to start the server immediately in a detached tmux session."""
    print()
    launch = _ask_bool(
        "Start the server now?",
        default=True,
        hint=f"Launches as '{cfg.steam_user}' in a detached tmux session named 'csgo'.",
    )
    if not launch:
        return

    session = "csgo"
    start_script = str(cfg.install_dir / "start_server.sh")

    # Kill any stale same-named session silently
    subprocess.run(
        ["su", "-", cfg.steam_user, "-c",
         f"tmux kill-session -t {session} 2>/dev/null; true"],
        check=False,
    )
    result = subprocess.run(
        ["su", "-", cfg.steam_user, "-c",
         f"tmux new-session -d -s {shlex.quote(session)} {shlex.quote(start_script)}"],
        check=False,
    )
    if result.returncode != 0:
        print(_c("  [WARN] Could not start tmux session automatically. Start the server manually using the command below.", _YELLOW))
        return

    print()
    print(_c(f"  Server is running in tmux session '{session}'.", _GREEN, _BOLD))
    _info(f"Attach to the console:  su - {cfg.steam_user} -c 'tmux attach -t {session}'")
    _info("Detach without stopping: Ctrl+B then D")


# ──────────────────────────────────────────────────────────────────────────────
# Main deploy routine
# ──────────────────────────────────────────────────────────────────────────────

def deploy(cfg: DeployConfig) -> bool:
    """Run deployment. Returns False if the user declined the confirmation."""
    if sys.platform != "linux":
        raise RuntimeError("This tool supports Linux only.")

    require_root_for_execute(cfg.dry_run)

    check_path = cfg.install_dir.parent if cfg.install_dir.parent.exists() else Path("/")
    disk_check(check_path)

    if not _confirm_or_abort(cfg):
        return False

    _header("Phase 1  —  System packages & steam user")
    for cmd, as_user in preinstall_commands(cfg):
        run(cmd, cfg.dry_run, as_user)

    _header("Phase 2  —  SteamCMD + CS:GO server + csgo_gc")
    for cmd, as_user in install_commands(cfg):
        run(cmd, cfg.dry_run, as_user)

    _header("Phase 3  —  Config files")
    server_cfg_path  = cfg.install_dir / "csgo" / "server.cfg"
    start_script_path = cfg.install_dir / "start_server.sh"

    write_text(server_cfg_path,  render_server_cfg(cfg), cfg.dry_run)
    write_text(start_script_path, create_start_script(cfg), cfg.dry_run)

    run(f"chmod +x {shlex.quote(str(start_script_path))}", cfg.dry_run)
    run(
        f"chown -R {shlex.quote(cfg.steam_user)}:{shlex.quote(cfg.steam_user)} "
        f"{shlex.quote(str(cfg.install_dir))}",
        cfg.dry_run,
    )

    if cfg.open_firewall:
        _header("Phase 4  —  Firewall")
        for cmd, as_user in firewall_commands(cfg):
            run(cmd, cfg.dry_run, as_user)

    # ── Final instructions ─────────────────────────────────────────────────
    print()
    print(_c(" ✔  Done!", _GREEN, _BOLD))

    if cfg.dry_run:
        print()
        print(_c("  (Dry-run — no system changes were made)", _YELLOW))
    else:
        _offer_launch(cfg)

    _header("Manual start reference")
    _info("Start the server in a detached tmux session:")
    print(f"     su - {cfg.steam_user} -c 'tmux new-session -d -s csgo {cfg.install_dir}/start_server.sh'")
    _info("Attach to the running session:")
    print(f"     su - {cfg.steam_user} -c 'tmux attach -t csgo'")
    _info("Detach without stopping the server: Ctrl+B then D")
    print()
    _info("Full start command:")
    print(f"     {start_command(cfg)}")
    return True


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        while True:
            cfg = wizard()
            if deploy(cfg):
                break
            print()
            print(_c("  Starting over — press Ctrl+C at any time to quit.", _YELLOW))
    except KeyboardInterrupt:
        print()
        print(_c("  Interrupted.", _YELLOW))
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001
        print(_c(f"\n  Error: {exc}", _RED), file=sys.stderr)
        sys.exit(1)
