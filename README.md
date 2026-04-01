# csgo_gc_server-deployer

An interactive wizard that fully automates the deployment of a **CS:GO Legacy** dedicated server with the **csgo_gc** library on any Ubuntu or Debian VPS.

No flags, no manual commands — just answer the prompts and the tool does the rest.

---

## Table of Contents

- [Requirements](#requirements)
- [Before You Start — Steam Token](#before-you-start--steam-token)
- [Quick Start](#quick-start)
- [Wizard Walkthrough](#wizard-walkthrough)
  - [Step 1 — Server identity](#step-1--server-identity)
  - [Step 2 — Steam token](#step-2--steam-token)
  - [Step 3 — Advanced settings](#step-3--advanced-settings)
  - [Step 4 — Security & extras](#step-4--security--extras)
  - [Dry-run vs. real deployment](#dry-run-vs-real-deployment)
  - [Confirmation summary](#confirmation-summary)
- [After Deployment — Starting Your Server](#after-deployment--starting-your-server)
- [Customizing Defaults](#customizing-defaults)
- [Security Notes](#security-notes)
- [Troubleshooting](#troubleshooting)

---

## Requirements

| Requirement | Details |
|---|---|
| OS | Ubuntu or Debian (any modern LTS) |
| Python | 3.10 or newer (`python3 --version`) |
| Disk space | ~50 GB free for the server files |
| Network | Outbound internet access on the VPS |
| Privileges | `sudo` or root access for real deployment |

Python 3.10+ is pre-installed on all modern Ubuntu/Debian VPS images. No third-party packages are required.

---

## Before You Start — Steam Token

The server will not authenticate with Steam without a **Game Server Login Token (GSLT)**. You must obtain one before running the deployer in execute mode.

1. Log in to your Steam account in a browser.
2. Go to: https://steamcommunity.com/dev/managegameservers
3. In the **App ID** field enter: `4465480`
4. Fill in a memo (e.g. `my-csgo-server-1`) — this is optional but recommended.
5. Click **Create** and copy the generated token.

> **Important:** Use one unique token per server instance. If a token is compromised, it only affects the single server it was issued for.

---

## Quick Start

Clone or download this repository onto your VPS, then run:

```bash
sudo python3 csgo_gc_deployer.py
```

The wizard starts immediately. Answer each prompt — press **Enter** to accept the value shown in brackets.

> `sudo` is only needed when you choose real deployment at the end of the wizard. For a dry-run preview you can run without it.

---

## Wizard Walkthrough

The wizard is split into four short steps. Each answer is validated before moving on, and your choices are saved to `defaults.ini` so repeat runs require minimal re-entry.

### Step 1 — Server identity

**Server public IP**
The tool automatically detects the public IP of the machine it is running on.
The detected address is shown as the default — press Enter to accept, or type a different IP.

**Server name**
The server browser display name (e.g. `My awesome CS:GO server`).

---

### Step 2 — Steam token

Your GSLT token obtained from the Steam page above.
Input is hidden while you type (like a password field).

Leave blank if you only want a **dry-run preview** — the wizard continues and shows you all commands without executing any of them.

---

### Step 3 — Advanced settings

You are asked whether you want to customise:

| Setting | Default |
|---|---|
| Server port | `27015` |
| Starting map | `de_dust2` |
| Max players | `16` |
| Tickrate | `128` |

Press **Enter** (accepting `n`) to skip this step entirely and use the recommended defaults. Only answer `y` if you have a specific reason to change these values.

---

### Step 4 — Security & extras

**RCON password**
The remote console password used to administrate the server. Leave blank to have the tool generate a cryptographically random password for you. The generated password is printed at the end of the wizard.

**Server join password**
Leave blank for a public server (no password required to join). Fill in if you want a private server.

**ufw firewall**
If you answer `y`, the tool will install `ufw` (if not already present) and open the following ports:

| Port | Purpose |
|---|---|
| 22 | SSH — keeps you connected to your VPS |
| 27015 (or your custom port) | CS:GO game traffic |
| 80, 443 | Web (only if you also answer `y` to the follow-up question) |

**Session manager**
`tmux` or `screen` — both keep your server running after you disconnect from SSH. `tmux` is recommended.

---

### Dry-run vs. real deployment

At the end of the wizard you are asked:

```
  Dry-run only? (preview commands without executing) [Y/n]:
```

| Choice | What happens |
|---|---|
| **Enter / Y** (default) | Every command is printed but nothing is executed. Safe to run as a regular user. |
| **n** | Commands are executed for real. The script must be run with `sudo`. |

Always do a dry-run first on a new machine to verify the plan matches your expectations.

---

### Confirmation summary

Before any real work begins you are shown a full summary of every setting:

```
  Deployment summary
  ─────────────────────────────────────────
  Dry run        YES — no real changes
  Server IP      203.0.113.42
  Hostname       My CS:GO_GC Legacy Server
  Port           27015
  ...
  Proceed? [y/N]:
```

Answering **n** restarts the wizard from the beginning so you can correct any mistakes — the program never exits unless you confirm or press Ctrl+C.

---

## After Deployment — Starting Your Server

At the end of a successful run the tool prints exact commands to start your server. The general pattern is:

**1. Open a persistent session so the server survives SSH disconnection:**

```bash
sudo -u steam tmux
```

**2. Launch the server inside that session:**

```bash
sudo -u steam bash -lc 'cd /home/steam/csgo_server && ./start_server.sh'
```

`start_server.sh` is generated automatically with all the correct parameters baked in. You can edit it later if needed.

**To detach from tmux** (leave server running in background): `Ctrl+B` then `D`

**To re-attach:** `sudo -u steam tmux attach`

---

## Customizing Defaults

Non-secret values are saved to `defaults.ini` automatically after each successful wizard run. You can also edit it directly before running the deployer:

```ini
[defaults]
hostname        = My CS:GO_GC Legacy Server
port            = 27015
map_name        = de_dust2
max_players     = 16
tickrate        = 128
session_tool    = tmux
open_firewall   = 0
allow_web_ports = 0

steam_user  = steam
steam_home  = /home/steam
install_dir = /home/steam/csgo_server
```

> Secrets (`steam_token`, `rcon_password`, `sv_password`) are **never** written to `defaults.ini`.

The `steam_user`, `steam_home`, and `install_dir` values are not asked during the wizard — change them here if your setup uses non-standard paths.

---

## Security Notes

- **Run as a dedicated user.** The deployer creates a `steam` system user and installs all server files under that account. The server process never runs as root.
- **Tokens are single-use per server.** If a GSLT is leaked, only the server it belongs to is at risk.
- **Passwords are not stored.** The `rcon_password` and `sv_password` are written only to `/home/steam/csgo_server/csgo/server.cfg`, which is owned by the `steam` user.
- **Dry-run first.** The default mode never touches the system, so you can verify the planned commands before committing.
- **Firewall.** If you enable ufw, SSH port 22 is opened first — before the firewall is enabled — so you cannot accidentally lock yourself out.

---

## Troubleshooting

**`Error: Real execution requires root`**
Re-run with `sudo`: `sudo python3 csgo_gc_deployer.py`

**`[WARN] Only X GB free`**
The CS:GO server requires roughly 50 GB. Free up disk space or attach additional storage before proceeding.

**`Could not detect public IP`**
The machine has no outbound internet access or all IP-detection services are blocked. Enter the IP manually when prompted.

**SteamCMD hangs or fails**
This is usually a temporary Steam network issue. Re-run the deployer — SteamCMD picks up where it left off because the `validate` flag is idempotent.

**Server does not appear in the browser**
- Confirm the GSLT token is valid and not already used on another running server.
- Check that port 27015 (UDP+TCP) is reachable from outside the VPS (`sudo ufw status`).
- Ensure the correct public IP was entered during setup.
