#!/usr/bin/env python3
"""Automated CS:GO Legacy + csgo_gc server deployer for Linux VPS."""

from __future__ import annotations

import argparse
import getpass
import os
import secrets
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

STEAMCMD_TAR_URL = "https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz"
CSGO_GC_URL = "https://github.com/mikkokko/csgo_gc/releases/download/continuous/csgo_gc-ubuntu-latest.zip"
STEAM_TOKEN_URL = "https://steamcommunity.com/dev/managegameservers"
STEAM_APP_ID = 4465480


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
    assume_yes: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deploy CS:GO Legacy server with csgo_gc on Ubuntu/Debian VPS"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    deploy = subparsers.add_parser("deploy", help="Run guided deployment")
    deploy.add_argument("--server-ip", required=True, help="Public IPv4 of the VPS")
    deploy.add_argument("--steam-token", help="Steam game server token")
    deploy.add_argument("--hostname", default="My CS:GO_GC Legacy Server")
    deploy.add_argument("--rcon-password", help="RCON password (auto-generated if omitted)")
    deploy.add_argument("--sv-password", default="", help="Optional server join password")
    deploy.add_argument("--port", type=int, default=27015)
    deploy.add_argument("--map", dest="map_name", default="de_dust2")
    deploy.add_argument("--max-players", type=int, default=16)
    deploy.add_argument("--tickrate", type=int, default=128)
    deploy.add_argument("--steam-user", default="steam")
    deploy.add_argument("--steam-home", default="/home/steam")
    deploy.add_argument("--install-dir", default="/home/steam/csgo_server")
    deploy.add_argument("--session-tool", choices=["tmux", "screen"], default="tmux")
    deploy.add_argument(
        "--open-firewall",
        action="store_true",
        help="Open SSH/game ports with ufw and enable firewall",
    )
    deploy.add_argument(
        "--allow-web-ports",
        action="store_true",
        help="Also open ports 80 and 443 when --open-firewall is used",
    )
    deploy.add_argument(
        "--execute",
        action="store_true",
        help="Actually run commands (default is dry-run preview)",
    )
    deploy.add_argument("--yes", action="store_true", help="Skip confirmation prompt")

    subparsers.add_parser("token-help", help="Show Steam token instructions")
    subparsers.add_parser("plan", help="Show deployment plan and best practices")

    return parser.parse_args()


def run(cmd: str, dry_run: bool = False, as_user: str | None = None) -> None:
    if as_user:
        command = ["sudo", "-u", as_user, "bash", "-lc", cmd]
        printable = f"sudo -u {as_user} bash -lc {shlex.quote(cmd)}"
    else:
        command = ["bash", "-lc", cmd]
        printable = cmd

    print(f"[RUN] {printable}")
    if dry_run:
        return

    subprocess.run(command, check=True)


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


def require_linux() -> None:
    if sys.platform != "linux":
        raise RuntimeError("This tool supports Linux only.")


def require_root_for_execute(dry_run: bool) -> None:
    if dry_run:
        return
    if os.geteuid() != 0:
        raise RuntimeError("Run with sudo/root for execution mode.")


def disk_check(path: Path) -> None:
    total, used, free = shutil.disk_usage(path)
    free_gb = free / (1024**3)
    print(f"[INFO] Free disk space near {path}: {free_gb:.1f} GB")
    if free_gb < 50:
        print("[WARN] Less than 50 GB free. Server installation may fail or be unstable.")


def ensure_prompt(cfg: DeployConfig) -> None:
    print("\nPlanned deployment summary")
    print(f"- Steam user: {cfg.steam_user}")
    print(f"- Steam home: {cfg.steam_home}")
    print(f"- Install dir: {cfg.install_dir}")
    print(f"- Server IP: {cfg.server_ip}")
    print(f"- Port: {cfg.port}")
    print(f"- Start inside: {cfg.session_tool}")
    print(f"- Open firewall: {cfg.open_firewall}")
    print(f"- Dry run: {cfg.dry_run}")

    if cfg.assume_yes:
        return

    choice = input("Continue? [y/N]: ").strip().lower()
    if choice not in {"y", "yes"}:
        raise RuntimeError("Deployment aborted by user.")


def write_text(path: Path, text: str, dry_run: bool) -> None:
    print(f"[WRITE] {path}")
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def create_start_script(cfg: DeployConfig) -> str:
    command = start_command(cfg)
    return (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"cd {shlex.quote(str(cfg.install_dir))}\n"
        f"exec {command}\n"
    )


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
        "+login anonymous +app_update 740 -beta csgo_legacy validate +quit",
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


def build_config(args: argparse.Namespace) -> DeployConfig:
    steam_token = args.steam_token or ""
    if args.command == "deploy" and args.execute and not steam_token:
        steam_token = getpass.getpass("Steam token (input hidden): ").strip()

    if args.command == "deploy" and not steam_token:
        steam_token = "YOUR_STEAM_TOKEN"

    rcon_password = args.rcon_password or secrets.token_urlsafe(16)

    return DeployConfig(
        server_ip=args.server_ip,
        steam_token=steam_token,
        hostname=args.hostname,
        rcon_password=rcon_password,
        sv_password=args.sv_password,
        port=args.port,
        map_name=args.map_name,
        max_players=args.max_players,
        tickrate=args.tickrate,
        steam_user=args.steam_user,
        steam_home=Path(args.steam_home),
        install_dir=Path(args.install_dir),
        open_firewall=args.open_firewall,
        allow_web_ports=args.allow_web_ports,
        session_tool=args.session_tool,
        dry_run=not args.execute,
        assume_yes=args.yes,
    )


def command_plan() -> None:
    print("Automation plan")
    print("1. Validate Linux/root requirements and free disk space.")
    print("2. Install required OS packages and i386 architecture support.")
    print("3. Create dedicated steam user if missing.")
    print("4. Install steamcmd and CS:GO legacy dedicated server.")
    print("5. Download and install csgo_gc release build.")
    print("6. Generate server.cfg and a reusable start_server.sh script.")
    print("7. Optionally configure ufw firewall.")
    print("8. Print final startup instructions for tmux/screen.")

    print("\nBest-practice guardrails")
    print("- Default dry-run mode previews every command before execution.")
    print("- Explicit --execute is required for real system changes.")
    print("- Sensitive token input can be hidden during prompt.")
    print("- Generated RCON password if user does not pass one.")
    print("- Dedicated non-root steam runtime user is enforced.")


def command_token_help() -> None:
    print("Steam token setup")
    print(f"- URL: {STEAM_TOKEN_URL}")
    print(f"- App ID to use: {STEAM_APP_ID}")
    print("- Create one token per server for blast-radius containment.")


def command_deploy(args: argparse.Namespace) -> None:
    require_linux()
    cfg = build_config(args)
    require_root_for_execute(cfg.dry_run)

    disk_check(cfg.install_dir.parent if cfg.install_dir.parent.exists() else Path("/"))
    ensure_prompt(cfg)

    for cmd, as_user in preinstall_commands(cfg):
        run(cmd, cfg.dry_run, as_user)

    for cmd, as_user in install_commands(cfg):
        run(cmd, cfg.dry_run, as_user)

    server_cfg_path = cfg.install_dir / "csgo" / "server.cfg"
    start_script_path = cfg.install_dir / "start_server.sh"

    write_text(server_cfg_path, render_server_cfg(cfg), cfg.dry_run)
    write_text(start_script_path, create_start_script(cfg), cfg.dry_run)

    run(f"chmod +x {shlex.quote(str(start_script_path))}", cfg.dry_run)
    run(
        f"chown -R {shlex.quote(cfg.steam_user)}:{shlex.quote(cfg.steam_user)} "
        f"{shlex.quote(str(cfg.install_dir))}",
        cfg.dry_run,
    )

    if cfg.open_firewall:
        for cmd, as_user in firewall_commands(cfg):
            run(cmd, cfg.dry_run, as_user)

    print("\nDeployment completed.")
    print("Start command:")
    print(start_command(cfg))
    print("\nRecommended startup:")
    print(f"sudo -u {cfg.steam_user} {cfg.session_tool}")
    print(f"sudo -u {cfg.steam_user} bash -lc 'cd {cfg.install_dir} && ./start_server.sh'")


if __name__ == "__main__":
    try:
        arguments = parse_args()
        if arguments.command == "plan":
            command_plan()
        elif arguments.command == "token-help":
            command_token_help()
        elif arguments.command == "deploy":
            command_deploy(arguments)
        else:
            raise RuntimeError(f"Unsupported command: {arguments.command}")
    except KeyboardInterrupt:
        print("Interrupted by user.")
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
