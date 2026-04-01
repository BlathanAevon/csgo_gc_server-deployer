#!/usr/bin/env python3
"""Automated CS:GO Legacy + csgo_gc server deployer for Linux VPS.

Run without arguments for an interactive guided setup.
"""

from __future__ import annotations

import configparser
import getpass
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

STEAMCMD_TAR_URL = "https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz"
CSGO_GC_URL = "https://github.com/mikkokko/csgo_gc/releases/download/continuous/csgo_gc-ubuntu-latest.zip"
STEAM_TOKEN_URL = "https://steamcommunity.com/dev/managegameservers"
STEAM_APP_ID = 4465480
METAMOD_STABLE_BRANCH = "1.12"
SOURCEMOD_STABLE_BRANCH = "1.12"

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
    rcon_port: int
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
    install_sourcemod_stack: bool
    metamod_branch: str
    sourcemod_branch: str
    plugin_source_path: Path | None
    plugin_install_mode: str
    plugin_audit_notes: tuple[str, ...]
    # Game mode and behavior
    game_mode: str
    game_type: int
    game_mode_id: int
    sv_skirmish_id: int
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
        f'rcon_port {cfg.rcon_port}\n'
        f'sv_password "{cfg.sv_password}"\n'
        f'\n'
        f'// Game mode\n'
        f'game_type {cfg.game_type}\n'
        f'game_mode {cfg.game_mode_id}\n'
        f'sv_skirmish_id {cfg.sv_skirmish_id}\n'
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
    parts = [
        "bash srcds_run",
        "-game csgo",
        "-console",
        "-usercon",
        "-tickrate",
        str(cfg.tickrate),
        "-port",
        str(cfg.port),
        "+net_public_adr",
        cfg.server_ip,
        "+sv_setsteamaccount",
        cfg.steam_token,
        "+sv_pure",
        "1",
        "+sv_lan",
        "0",
        "+rcon_port",
        str(cfg.rcon_port),
        "+game_type",
        str(cfg.game_type),
        "+game_mode",
        str(cfg.game_mode_id),
    ]
    if cfg.sv_skirmish_id:
        parts.extend(["+sv_skirmish_id", str(cfg.sv_skirmish_id)])
    parts.extend([
        "+map",
        cfg.map_name,
        "+exec",
        "server.cfg",
        "-steam",
        "-net_port_try",
        "1",
        "-maxplayers_override",
        str(cfg.max_players),
    ])
    if cfg.bot_quota == 0:
        parts.append("-nobots")
    return " ".join(parts)


def create_start_script(cfg: DeployConfig) -> str:
    return (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"cd {shlex.quote(str(cfg.install_dir))}\n"
        f"exec {start_command(cfg)}\n"
    )


def create_rcon_helper_script(cfg: DeployConfig) -> str:
    return (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "if [[ $# -eq 0 ]]; then\n"
        "  echo 'Usage: ./rcon.sh <command...>'\n"
        "  echo 'Example: ./rcon.sh status'\n"
        "  exit 1\n"
        "fi\n"
        "if ! command -v mcrcon >/dev/null 2>&1; then\n"
        "  echo 'mcrcon is not installed. Re-run the deployer to install it automatically.'\n"
        "  exit 1\n"
        "fi\n"
        f"exec mcrcon -H 127.0.0.1 -P {cfg.rcon_port} -p {shlex.quote(cfg.rcon_password)} \"$@\"\n"
    )


def create_stop_script(cfg: DeployConfig) -> str:
    session = "csgo"
    session_kill = _session_kill_command(cfg.session_tool, session)
    return (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"SESSION_TOOL={shlex.quote(cfg.session_tool)}\n"
        f"STEAM_USER={shlex.quote(cfg.steam_user)}\n"
        f"SESSION_KILL_CMD={shlex.quote(session_kill)}\n"
        "\n"
        "# Stop detached session first so it cannot respawn server children.\n"
        "su - \"$STEAM_USER\" -c \"$SESSION_KILL_CMD\" >/dev/null 2>&1 || true\n"
        "\n"
        "# Then terminate all known Source dedicated server processes for this user.\n"
        "PIDS=\"$(pgrep -u \"$STEAM_USER\" -f 'srcds_linux|srcds_run|csgo_gc|hl2_linux' || true)\"\n"
        "if [[ -n \"$PIDS\" ]]; then\n"
        "  kill $PIDS >/dev/null 2>&1 || true\n"
        "  for _ in {1..8}; do\n"
        "    sleep 1\n"
        "    REMAINING=\"$(pgrep -u \"$STEAM_USER\" -f 'srcds_linux|srcds_run|csgo_gc|hl2_linux' || true)\"\n"
        "    [[ -z \"$REMAINING\" ]] && break\n"
        "  done\n"
        "  REMAINING=\"$(pgrep -u \"$STEAM_USER\" -f 'srcds_linux|srcds_run|csgo_gc|hl2_linux' || true)\"\n"
        "  if [[ -n \"$REMAINING\" ]]; then\n"
        "    kill -9 $REMAINING >/dev/null 2>&1 || true\n"
        "  fi\n"
        "fi\n"
        "\n"
        "FINAL=\"$(pgrep -u \"$STEAM_USER\" -f 'srcds_linux|srcds_run|csgo_gc|hl2_linux' || true)\"\n"
        "if [[ -n \"$FINAL\" ]]; then\n"
        "  echo 'Some server-related processes are still running:'\n"
        "  ps -fp $FINAL || true\n"
        "  exit 1\n"
        "fi\n"
        "\n"
        "echo 'CS:GO server stop completed: no related processes remain.'\n"
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


def rcon_dependency_commands() -> Iterable[tuple[str, str | None]]:
    # Try distro package first; if unavailable, build mcrcon from source.
    yield "command -v mcrcon >/dev/null 2>&1 || apt-get install -y mcrcon || true", None
    yield "command -v mcrcon >/dev/null 2>&1 || apt-get install -y build-essential git ca-certificates", None
    yield (
        "command -v mcrcon >/dev/null 2>&1 || "
        "(tmpd=\"$(mktemp -d)\" && "
        "git clone --depth 1 https://github.com/Tiiffi/mcrcon \"$tmpd/mcrcon\" && "
        "make -C \"$tmpd/mcrcon\" && "
        "install -m 0755 \"$tmpd/mcrcon/mcrcon\" /usr/local/bin/mcrcon && "
        "rm -rf \"$tmpd\")"
    ), None


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


def addon_commands(cfg: DeployConfig) -> Iterable[tuple[str, str | None]]:
    game_dir = cfg.install_dir / "csgo"
    yield (
        f"cd {shlex.quote(str(game_dir))} && "
        f"mms_name=\"$(wget -qO- https://mms.alliedmods.net/mmsdrop/{shlex.quote(cfg.metamod_branch)}/mmsource-latest-linux)\" && "
        f"wget -N \"https://mms.alliedmods.net/mmsdrop/{cfg.metamod_branch}/$mms_name\" -O \"$mms_name\" && "
        "tar -xzf \"$mms_name\" && rm -f \"$mms_name\"",
        cfg.steam_user,
    )
    yield (
        f"cd {shlex.quote(str(game_dir))} && "
        f"sm_name=\"$(wget -qO- https://sm.alliedmods.net/smdrop/{shlex.quote(cfg.sourcemod_branch)}/sourcemod-latest-linux)\" && "
        f"wget -N \"https://sm.alliedmods.net/smdrop/{cfg.sourcemod_branch}/$sm_name\" -O \"$sm_name\" && "
        "tar -xzf \"$sm_name\" && rm -f \"$sm_name\"",
        cfg.steam_user,
    )


def deploy_plugin_artifact(cfg: DeployConfig) -> None:
    if cfg.plugin_source_path is None:
        return

    source_path = cfg.plugin_source_path
    sourcemod_dir = cfg.install_dir / "csgo" / "addons" / "sourcemod"
    staging_dir = cfg.install_dir / "_plugin_uploads"
    print(_c(f"  [PLUGIN] {source_path}", _DIM))
    if cfg.dry_run:
        if cfg.plugin_install_mode == "smx":
            print(_c(f"  [PLAN] Copy to {sourcemod_dir / 'plugins' / source_path.name}", _DIM))
        elif cfg.plugin_install_mode == "source":
            print(_c(f"  [PLAN] Compile {source_path.name} with spcomp and install resulting .smx", _DIM))
        else:
            print(_c(f"  [PLAN] Extract plugin package into {cfg.install_dir / 'csgo'}", _DIM))
        return

    staging_dir.mkdir(parents=True, exist_ok=True)
    staged_path = staging_dir / source_path.name
    if source_path.is_dir():
        if staged_path.exists():
            shutil.rmtree(staged_path)
        shutil.copytree(source_path, staged_path)
    else:
        shutil.copy2(source_path, staged_path)

    if cfg.plugin_install_mode == "smx":
        target = sourcemod_dir / "plugins" / source_path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(staged_path, target)
    elif cfg.plugin_install_mode == "source":
        scripting_dir = sourcemod_dir / "scripting"
        plugins_dir = sourcemod_dir / "plugins"
        scripting_dir.mkdir(parents=True, exist_ok=True)
        plugins_dir.mkdir(parents=True, exist_ok=True)
        target_source = scripting_dir / source_path.name
        shutil.copy2(staged_path, target_source)
        output_name = source_path.with_suffix(".smx").name
        run(
            f"cd {shlex.quote(str(scripting_dir))} && chmod +x ./spcomp && ./spcomp {shlex.quote(target_source.name)} -o../plugins/{shlex.quote(output_name)}",
            dry_run=False,
            as_user=cfg.steam_user,
        )
    elif cfg.plugin_install_mode == "directory":
        destination = cfg.install_dir / "csgo"
        for child in staged_path.iterdir():
            target = destination / child.name
            if child.is_dir():
                shutil.copytree(child, target, dirs_exist_ok=True)
            else:
                shutil.copy2(child, target)
    elif cfg.plugin_install_mode == "archive":
        destination = cfg.install_dir / "csgo"
        if staged_path.name.lower().endswith(".zip"):
            with zipfile.ZipFile(staged_path) as archive:
                _safe_extract_zip(archive, destination)
        else:
            with tarfile.open(staged_path) as archive:
                _safe_extract_tar(archive, destination)


def firewall_commands(cfg: DeployConfig) -> Iterable[tuple[str, str | None]]:
    yield "command -v ufw >/dev/null 2>&1 || apt-get install -y ufw", None
    yield "ufw allow 22", None
    yield f"ufw allow {cfg.port}/udp", None
    yield f"ufw allow {cfg.port}/tcp", None
    if cfg.rcon_port != cfg.port:
        yield f"ufw allow {cfg.rcon_port}/tcp", None
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


def _ask_float(label: str, default: float, lo: float, hi: float) -> float:
    while True:
        raw = _ask(label, default=str(default))
        try:
            val = float(raw)
            if lo <= val <= hi:
                return val
        except ValueError:
            pass
        print(_c(f"  ✗ Value must be a number between {lo} and {hi}.", _RED))


def _mode_launch_settings(mode_name: str) -> tuple[int, int, int]:
    presets = {
        "casual": (0, 0, 0),
        "competitive": (0, 1, 0),
        "arms_race": (1, 0, 0),
        "demolition": (1, 1, 0),
        "deathmatch": (1, 2, 0),
    }
    return presets.get(mode_name, presets["competitive"])


def _recommended_map_for_mode(mode_name: str) -> str | None:
    recommendations = {
        "casual": "de_dust2",
        "competitive": "de_dust2",
        "arms_race": "ar_shoots",
        "demolition": "de_lake",
        "deathmatch": "de_dust2",
    }
    return recommendations.get(mode_name)


def _ask_existing_path(label: str, default: str = "", hint: str = "", allow_blank: bool = True) -> Path | None:
    while True:
        raw = _ask(label, default=default, hint=hint)
        if not raw:
            return None if allow_blank else Path()
        path = Path(raw).expanduser()
        if path.exists():
            return path.resolve()
        print(_c("  ✗ Path does not exist on this machine.", _RED))


def _artifact_install_mode(path: Path) -> str:
    name = path.name.lower()
    if path.is_dir():
        return "directory"
    if name.endswith(".smx"):
        return "smx"
    if name.endswith(".sp"):
        return "source"
    if name.endswith(".zip") or name.endswith(".tar.gz") or name.endswith(".tgz"):
        return "archive"
    return "unknown"


def _scan_text_for_plugin_risks(label: str, text: str) -> list[str]:
    notes: list[str] = []
    url_hits = sorted(set(re.findall(r"https?://[^\s\"'<>]+", text, flags=re.IGNORECASE)))
    if url_hits:
        notes.append(f"{label}: outbound URLs found -> {', '.join(url_hits[:5])}")

    risk_patterns = {
        "network APIs": [r"\bHTTPClient\b", r"\bHTTPRequest\b", r"\bSteamWorks_CreateHTTPRequest\b", r"\bSocket(?:Create|Connect|Send)\b"],
        "server commands": [r"\bServerCommand\b", r"\bInsertServerCommand\b", r"\bFakeClientCommand(?:Ex)?\b"],
        "file writes": [r"\bOpenFile\s*\(", r"\bWriteFile(?:Line)?\b", r"\bDeleteFile\b", r"\bRenameFile\b"],
        "database access": [r"\bSQL_(?:Connect|TConnect|Query|FastQuery)\b"],
    }
    for category, patterns in risk_patterns.items():
        hits = sorted({match.group(0) for pattern in patterns for match in re.finditer(pattern, text)})
        if hits:
            notes.append(f"{label}: {category} -> {', '.join(hits[:6])}")
    return notes


def _audit_plugin_artifact(path: Path) -> tuple[str, tuple[str, ...]]:
    mode = _artifact_install_mode(path)
    if mode == "unknown":
        return mode, (f"Unsupported plugin artifact type: {path.name}",)
    if mode == "smx":
        return mode, (
            "Compiled .smx binary detected; source-level audit is not possible from the binary alone.",
            "Installation can proceed, but safety claims cannot be verified without the matching .sp source.",
        )

    notes: list[str] = []
    scanned_files = 0

    def scan_text_file(file_label: str, content: str) -> None:
        nonlocal scanned_files
        scanned_files += 1
        notes.extend(_scan_text_for_plugin_risks(file_label, content))

    if mode == "source":
        scan_text_file(path.name, path.read_text(encoding="utf-8", errors="ignore"))
    elif mode == "directory":
        for candidate in sorted(path.rglob("*")):
            if candidate.is_file() and candidate.suffix.lower() in {".sp", ".cfg", ".txt", ".ini", ".json", ".yml", ".yaml"}:
                scan_text_file(str(candidate.relative_to(path)), candidate.read_text(encoding="utf-8", errors="ignore"))
    elif mode == "archive":
        if path.name.lower().endswith(".zip"):
            with zipfile.ZipFile(path) as archive:
                for member in archive.infolist():
                    suffix = Path(member.filename).suffix.lower()
                    if member.is_dir() or suffix not in {".sp", ".cfg", ".txt", ".ini", ".json", ".yml", ".yaml"}:
                        continue
                    with archive.open(member) as fh:
                        scan_text_file(member.filename, fh.read().decode("utf-8", errors="ignore"))
        else:
            with tarfile.open(path) as archive:
                for member in archive.getmembers():
                    suffix = Path(member.name).suffix.lower()
                    if not member.isfile() or suffix not in {".sp", ".cfg", ".txt", ".ini", ".json", ".yml", ".yaml"}:
                        continue
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        continue
                    scan_text_file(member.name, extracted.read().decode("utf-8", errors="ignore"))

    if scanned_files == 0:
        notes.append("No readable source/config text files were found to audit inside the plugin artifact.")
    elif not notes:
        notes.append(f"Scanned {scanned_files} text file(s); no obvious network, command, file-write, or SQL indicators were found.")
    return mode, tuple(notes)


def _safe_extract_zip(archive: zipfile.ZipFile, destination: Path) -> None:
    destination = destination.resolve()
    for member in archive.infolist():
        target = (destination / member.filename).resolve()
        if not str(target).startswith(str(destination)):
            raise RuntimeError(f"Unsafe archive entry blocked: {member.filename}")
    archive.extractall(destination)


def _safe_extract_tar(archive: tarfile.TarFile, destination: Path) -> None:
    destination = destination.resolve()
    for member in archive.getmembers():
        target = (destination / member.name).resolve()
        if not str(target).startswith(str(destination)):
            raise RuntimeError(f"Unsafe archive entry blocked: {member.name}")
    archive.extractall(destination)


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
            choices=["competitive", "casual", "deathmatch", "arms_race", "demolition", "custom"],
            default=d("game_mode", "competitive"),
            hint="Uses real Source game_type/game_mode pairs. Pick custom if you need a manual pair or skirmish id.",
        )
        saved["game_mode"] = game_mode

        if game_mode == "custom":
            game_type = _ask_int("game_type", default=int(d("game_type", "0")), lo=0, hi=3)
            game_mode_id = _ask_int("game_mode", default=int(d("game_mode_id", "0")), lo=0, hi=2)
            sv_skirmish_id = _ask_int("sv_skirmish_id", default=int(d("sv_skirmish_id", "0")), lo=0, hi=99)
        else:
            game_type, game_mode_id, sv_skirmish_id = _mode_launch_settings(game_mode)
        saved["game_type"] = str(game_type)
        saved["game_mode_id"] = str(game_mode_id)
        saved["sv_skirmish_id"] = str(sv_skirmish_id)

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
        mp_roundtime = _ask_float("Round time (minutes)", default=float(d("mp_roundtime", "1.92")), lo=0.5, hi=10.0)
        mp_buytime = _ask_int("Buy time (seconds)", default=int(d("mp_buytime", "20")), lo=0, hi=120)
        saved["mp_warmuptime"] = str(mp_warmuptime)
        saved["mp_freezetime"] = str(mp_freezetime)
        saved["mp_roundtime"] = str(mp_roundtime)
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

        recommended_map = _recommended_map_for_mode(game_mode)
        if game_mode == "arms_race" and not map_name.startswith("ar_"):
            _info(f"Arms Race works best with ar_ maps. Switching map to {recommended_map}.")
            map_name = recommended_map or map_name
            saved["map_name"] = map_name
        elif game_mode == "demolition" and map_name == "de_dust2":
            _info(f"Demolition works best with a demolition map. Switching map to {recommended_map}.")
            map_name = recommended_map or map_name
            saved["map_name"] = map_name
    else:
        # Use defaults for all game mode settings
        game_mode = "competitive"
        game_type, game_mode_id, sv_skirmish_id = _mode_launch_settings(game_mode)
        mp_startmoney = int(d("mp_startmoney", "2400"))
        mp_maxmoney = int(d("mp_maxmoney", "16000"))
        mp_warmuptime = int(d("mp_warmuptime", "60"))
        mp_freezetime = int(d("mp_freezetime", "15"))
        mp_roundtime = float(d("mp_roundtime", "1.92"))
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

    rcon_port = _ask_port(
        "RCON port",
        default=int(d("rcon_port", "27016")),
    )
    saved["rcon_port"] = str(rcon_port)

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

    install_sourcemod_stack = _ask_bool(
        "Install Metamod:Source + SourceMod?",
        default=bool(int(d("install_sourcemod_stack", "0"))),
        hint="Recommended if you plan to run admin plugins, Discord bridge plugins, or SourceMod menus.",
    )
    saved["install_sourcemod_stack"] = "1" if install_sourcemod_stack else "0"

    metamod_branch = d("metamod_branch", METAMOD_STABLE_BRANCH)
    sourcemod_branch = d("sourcemod_branch", SOURCEMOD_STABLE_BRANCH)
    plugin_source_path: Path | None = None
    plugin_install_mode = "none"
    plugin_audit_notes: tuple[str, ...] = ()

    if install_sourcemod_stack:
        plugin_source_path = _ask_existing_path(
            "Plugin path (.smx, .sp, .zip, .tar.gz, or folder)",
            default=d("plugin_source_path", ""),
            hint="Optional local path on this VPS. Leave blank to install only Metamod + SourceMod.",
        )
        if plugin_source_path is not None:
            plugin_install_mode, plugin_audit_notes = _audit_plugin_artifact(plugin_source_path)
            saved["plugin_source_path"] = str(plugin_source_path)
            print()
            _header("Plugin audit preview")
            for note in plugin_audit_notes:
                _info(note)
        else:
            saved["plugin_source_path"] = ""
    else:
        saved["plugin_source_path"] = ""

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
        rcon_port=rcon_port,
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
        install_sourcemod_stack=install_sourcemod_stack,
        metamod_branch=metamod_branch,
        sourcemod_branch=sourcemod_branch,
        plugin_source_path=plugin_source_path,
        plugin_install_mode=plugin_install_mode,
        plugin_audit_notes=plugin_audit_notes,
        # Game mode and behavior
        game_mode=game_mode,
        game_type=game_type,
        game_mode_id=game_mode_id,
        sv_skirmish_id=sv_skirmish_id,
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
        ("Mode preset",     cfg.game_mode),
        ("Mode pair",       f"game_type {cfg.game_type} / game_mode {cfg.game_mode_id}"),
        ("Port",            str(cfg.port)),
        ("Map",             cfg.map_name),
        ("Max players",     str(cfg.max_players)),
        ("Tickrate",        str(cfg.tickrate)),
        ("SourceMod stack", "yes" if cfg.install_sourcemod_stack else "no"),
        ("Plugin artifact", str(cfg.plugin_source_path) if cfg.plugin_source_path else _c("(none)", _DIM)),
        ("RCON password",   _c("(set)", _DIM)),
        ("RCON port",       str(cfg.rcon_port)),
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
    if cfg.plugin_audit_notes:
        print()
        _info("Plugin audit notes:")
        for note in cfg.plugin_audit_notes:
            _info(f"- {note}")


def _confirm_or_abort(cfg: DeployConfig) -> bool:
    _print_summary(cfg)
    print()
    return _ask_bool("Proceed?", default=False)


def _session_start_command(session_tool: str, session_name: str, command: str) -> str:
    if session_tool == "screen":
        return f"screen -dmS {shlex.quote(session_name)} {shlex.quote(command)}"
    return f"tmux new-session -d -s {shlex.quote(session_name)} {shlex.quote(command)}"


def _session_attach_command(session_tool: str, session_name: str) -> str:
    if session_tool == "screen":
        return f"screen -r {shlex.quote(session_name)}"
    return f"tmux attach -t {shlex.quote(session_name)}"


def _session_kill_command(session_tool: str, session_name: str) -> str:
    if session_tool == "screen":
        return f"screen -S {shlex.quote(session_name)} -X quit 2>/dev/null; true"
    return f"tmux kill-session -t {shlex.quote(session_name)} 2>/dev/null; true"


# ──────────────────────────────────────────────────────────────────────────────
# Post-deploy server launch
# ──────────────────────────────────────────────────────────────────────────────

def _offer_launch(cfg: DeployConfig) -> bool:
    """Offer to start the server immediately in a detached session."""
    print()
    launch = _ask_bool(
        "Start the server now?",
        default=True,
        hint=f"Launches as '{cfg.steam_user}' in a detached {cfg.session_tool} session named 'csgo'.",
    )
    if not launch:
        return False

    session = "csgo"
    start_script = str(cfg.install_dir / "start_server.sh")

    # Kill any stale same-named session silently
    subprocess.run(["su", "-", cfg.steam_user, "-c", _session_kill_command(cfg.session_tool, session)], check=False)
    result = subprocess.run(
        ["su", "-", cfg.steam_user, "-c", _session_start_command(cfg.session_tool, session, start_script)],
        check=False,
    )
    if result.returncode != 0:
        print(_c(f"  [WARN] Could not start {cfg.session_tool} session automatically. Start the server manually using the command below.", _YELLOW))
        return False

    print()
    print(_c(f"  Server is running in {cfg.session_tool} session '{session}'.", _GREEN, _BOLD))
    _info(f"Attach to the console:  su - {cfg.steam_user} -c '{_session_attach_command(cfg.session_tool, session)}'")
    if cfg.session_tool == "tmux":
        _info("Detach without stopping: Ctrl+B then D")
    else:
        _info("Detach without stopping: Ctrl+A then D")
    print()
    _header("Connection & admin quick reference")
    print(f"     connect {cfg.server_ip}:{cfg.port}")
    if cfg.sv_password:
        _info(f"Join password: {cfg.sv_password}")
    _info("If RCON does not bind while already in-game, use the main-menu console flow below:")
    print(f"     rcon_address {cfg.server_ip}:{cfg.rcon_port}")
    print(f"     rcon_password \"{cfg.rcon_password}\"")
    print("     rcon status")
    return True


def _run_local_rcon(cfg: DeployConfig, command: str) -> subprocess.CompletedProcess[str]:
    rcon_helper = cfg.install_dir / "rcon.sh"
    return subprocess.run(
        ["su", "-", cfg.steam_user, "-c", f"{shlex.quote(str(rcon_helper))} {shlex.quote(command)}"],
        check=False,
        text=True,
        capture_output=True,
    )


def _post_start_self_test(cfg: DeployConfig, attempts: int = 20, delay_sec: float = 1.0) -> None:
    """Validate that RCON works and sv_lan is forced to 0 after startup."""
    _header("Post-start self-test")
    _info("Validating local RCON authentication and LAN mode...")

    last_status_output = ""
    for attempt in range(1, attempts + 1):
        status = _run_local_rcon(cfg, "status")
        status_out = (status.stdout + status.stderr).strip()
        last_status_output = status_out or f"(exit code {status.returncode})"
        if status.returncode == 0:
            break
        if attempt < attempts:
            _info(f"RCON not ready yet (attempt {attempt}/{attempts}); retrying...")
            time.sleep(delay_sec)
    else:
        raise RuntimeError(
            "Post-start self-test failed: local RCON authentication did not succeed. "
            f"Last output: {last_status_output}"
        )

    sv_lan = _run_local_rcon(cfg, "sv_lan")
    sv_lan_out = (sv_lan.stdout + sv_lan.stderr).strip()
    if sv_lan.returncode != 0:
        raise RuntimeError(
            "Post-start self-test failed: could not query sv_lan via local RCON. "
            f"Output: {sv_lan_out or '(empty)'}"
        )
    if re.search(r"\bsv_lan\b.*\b0\b", sv_lan_out) is None and sv_lan_out.strip() != "0":
        raise RuntimeError(
            "Post-start self-test failed: sv_lan is not 0 (server appears LAN-restricted). "
            f"Output: {sv_lan_out}"
        )

    print(_c("  [OK] RCON auth check passed.", _GREEN))
    print(_c("  [OK] sv_lan check passed (0).", _GREEN))


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

    _header("Phase 1.5  —  RCON tool dependency")
    for cmd, as_user in rcon_dependency_commands():
        run(cmd, cfg.dry_run, as_user)

    _header("Phase 2  —  SteamCMD + CS:GO server + csgo_gc")
    for cmd, as_user in install_commands(cfg):
        run(cmd, cfg.dry_run, as_user)

    if cfg.install_sourcemod_stack:
        _header("Phase 2.5  —  Metamod:Source + SourceMod")
        for cmd, as_user in addon_commands(cfg):
            run(cmd, cfg.dry_run, as_user)

    _header("Phase 3  —  Config files")
    server_cfg_path  = cfg.install_dir / "csgo" / "cfg" / "server.cfg"
    start_script_path = cfg.install_dir / "start_server.sh"
    rcon_script_path = cfg.install_dir / "rcon.sh"
    stop_script_path = cfg.install_dir / "stop_server.sh"

    write_text(server_cfg_path,  render_server_cfg(cfg), cfg.dry_run)
    write_text(start_script_path, create_start_script(cfg), cfg.dry_run)
    write_text(rcon_script_path, create_rcon_helper_script(cfg), cfg.dry_run)
    write_text(stop_script_path, create_stop_script(cfg), cfg.dry_run)

    run(f"chmod +x {shlex.quote(str(start_script_path))}", cfg.dry_run)
    run(f"chmod +x {shlex.quote(str(rcon_script_path))}", cfg.dry_run)
    run(f"chmod +x {shlex.quote(str(stop_script_path))}", cfg.dry_run)
    run(
        f"chown -R {shlex.quote(cfg.steam_user)}:{shlex.quote(cfg.steam_user)} "
        f"{shlex.quote(str(cfg.install_dir))}",
        cfg.dry_run,
    )

    if cfg.install_sourcemod_stack and cfg.plugin_source_path is not None:
        _header("Phase 3.5  —  Plugin deployment")
        deploy_plugin_artifact(cfg)
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
        started = _offer_launch(cfg)
        if started:
            _post_start_self_test(cfg)

    _header("Manual start reference")
    _info(f"Start the server in a detached {cfg.session_tool} session:")
    print(f"     su - {cfg.steam_user} -c '{_session_start_command(cfg.session_tool, 'csgo', str(cfg.install_dir / 'start_server.sh'))}'")
    _info("Attach to the running session:")
    print(f"     su - {cfg.steam_user} -c '{_session_attach_command(cfg.session_tool, 'csgo')}'")
    _info("Stop the server completely (session + all related processes):")
    print(f"     su - {cfg.steam_user} -c '{cfg.install_dir}/stop_server.sh'")
    if cfg.session_tool == "tmux":
        _info("Detach without stopping the server: Ctrl+B then D")
    else:
        _info("Detach without stopping the server: Ctrl+A then D")
    print()
    _info("Full start command:")
    print(f"     {start_command(cfg)}")
    print()
    _header("Admin quick reference")
    print(f"     connect {cfg.server_ip}:{cfg.port}")
    if cfg.sv_password:
        _info(f"Join password: {cfg.sv_password}")
    print(f"     rcon_address {cfg.server_ip}:{cfg.rcon_port}")
    print(f"     rcon_password \"{cfg.rcon_password}\"")
    print("     rcon status")
    print(f"     su - {cfg.steam_user} -c '{cfg.install_dir}/rcon.sh status'")
    print(f"     su - {cfg.steam_user} -c '{cfg.install_dir}/rcon.sh changelevel de_dust2'")
    print(f"     su - {cfg.steam_user} -c '{cfg.install_dir}/rcon.sh sv_lan'")
    print(f"     su - {cfg.steam_user} -c '{cfg.install_dir}/rcon.sh rcon_password'")
    if cfg.install_sourcemod_stack:
        print()
        _header("Addon verification")
        print("     meta version")
        print("     meta list")
        print("     sm version")
        print("     sm plugins list")
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
