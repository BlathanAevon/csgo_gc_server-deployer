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
        command = ["sudo", "-u", as_user, "bash", "-lc", cmd]
        printable = f"sudo -u {as_user} bash -lc {shlex.quote(cmd)}"
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
        f'hostname "{cfg.hostname}"\n'
        f'rcon_password "{cfg.rcon_password}"\n'
        f'sv_password "{cfg.sv_password}"\n'
    )


def start_command(cfg: DeployConfig) -> str:
    return (
        "./srcds_run -game csgo -console -usercon -insecure "
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
    print()
    _header("How to start your server")
    _info(f"1. Open a {cfg.session_tool} session:")
    print(f"     sudo -u {cfg.steam_user} {cfg.session_tool}")
    _info("2. Launch the server:")
    print(f"     sudo -u {cfg.steam_user} bash -lc 'cd {cfg.install_dir} && ./start_server.sh'")
    print()
    _info("Full start command:")
    print(f"     {start_command(cfg)}")
    if cfg.dry_run:
        print()
        print(_c("  (Dry-run — no system changes were made)", _YELLOW))
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
