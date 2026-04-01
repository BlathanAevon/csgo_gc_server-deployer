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
  - [Step 3.5 — Game mode & behavior](#step-35--game-mode--behavior)
  - [Step 4 — Security & extras](#step-4--security--extras)
  - [Dry-run vs. real deployment](#dry-run-vs-real-deployment)
  - [Confirmation summary](#confirmation-summary)
- [After Deployment — Starting Your Server](#after-deployment--starting-your-server)
- [Server Management — Connection & Administration](#server-management--connection--administration)
- [SourceMod Plugins](#sourcemod-plugins)
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

### Step 3.5 — Game mode & behavior

You are asked whether you want to customise game mode and server behavior:

**Game mode selection**
- `competitive` — classic competitive, applied as `game_type 0` and `game_mode 1`
- `casual` — classic casual, applied as `game_type 0` and `game_mode 0`
- `deathmatch` — deathmatch, applied as `game_type 1` and `game_mode 2`
- `arms_race` — arms race, applied as `game_type 1` and `game_mode 0`
- `demolition` — demolition, applied as `game_type 1` and `game_mode 1`
- `custom` — manual `game_type`, `game_mode`, and optional `sv_skirmish_id`

**Economy preset** (only if customizing)
- `conservative` — Low starting money ($1000–$8000 max), forces economic strategy
- `standard` — Balanced ($2400–$16000), Valve default
- `aggressive` — High starting money ($3500–$20000), beginner-friendly

**Match timing**
- Warmup time — seconds before match starts (0 to skip)
- Freeze time — seconds players remain frozen at round start
- Round time — minutes per round
- Buy time — seconds to purchase weapons each round

**Team dynamics**
- Dead player voice chat — allow dead players to communicate across teams?
- Auto-kick idle players — remove inactive players automatically?
- Punish team killers — damage team-killing players?
- Force dead spectator view — lock dead players to view their killer?

**Bot configuration**
- Bot count — number of bots (0–32)
- Bot difficulty — 0=easy, 1=normal, 2=hard, 3=expert
- Controllable bots — allow players to take control of bots?

**GOTV (server demo recording)**
- Enable GOTV — record all matches for replay and analysis
- GOTV max spectators — how many people can watch the live server feed
- GOTV delay — seconds of delay on the broadcast (privacy/anti-cheat)

**Logging**
- Log bans — record ban events to file?
- Echo logs to console — also print logs onscreen?
- Single log file — combine all logs or create per-session files?
- Sleep when empty — reduce server CPU load when no players connected?

Press **Enter** to skip and accept competitive defaults, or answer `y` to customize these settings.

The deployer now writes the numeric mode pair into `server.cfg` and also passes it on the launch command. That matters because CS:GO falls back to casual behavior unless the correct `game_type` / `game_mode` pair is applied.

---

### Step 4 — Security & extras

**RCON password**
The remote console password used to administrate the server. Leave blank to have the tool generate a cryptographically random password for you. The generated password is printed at the end of the wizard.

**RCON port**
The wizard configures a dedicated RCON port (default `27016`) so game traffic and admin control stay separate. This is more reliable than sharing the game port.

**Server join password**
Leave blank for a public server (no password required to join). Fill in if you want a private server.

**ufw firewall**
If you answer `y`, the tool will install `ufw` (if not already present) and open the following ports:

| Port | Purpose |
|---|---|
| 22 | SSH — keeps you connected to your VPS |
| 27015 (or your custom port) | CS:GO game traffic |
| 27016 (or your custom RCON port) | RCON administration (TCP) |
| 80, 443 | Web (only if you also answer `y` to the follow-up question) |

**Session manager**
`tmux` or `screen` — both keep your server running after you disconnect from SSH. `tmux` is recommended.

**Metamod:Source + SourceMod**
If you answer `y`, the deployer downloads the current official stable AlliedModders builds and extracts them into your server automatically.

**Plugin path**
If SourceMod is enabled, you can optionally provide a local path on the VPS to one of these:

| Artifact | Behavior |
|---|---|
| `.smx` | Installs the compiled plugin into `addons/sourcemod/plugins/` |
| `.sp` | Copies source into `addons/sourcemod/scripting/` and compiles it with `spcomp` |
| `.zip` / `.tar.gz` / `.tgz` | Extracts a packaged plugin bundle into the `csgo/` folder |
| Directory | Merges the directory contents into the `csgo/` folder |

If source or readable text files are present, the deployer performs a lightweight audit before confirmation and reports any obvious outbound URLs, HTTP/socket APIs, server-command calls, file-write calls, or SQL usage.

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

If you enabled SourceMod, verify the addon stack after startup from the SRCDS console:

```text
meta version
meta list
sm version
```

---

## SourceMod Plugins

The deployer can now install the common SourceMod stack without a separate FTP/manual extraction step:

1. It installs Metamod:Source first.
2. It installs SourceMod second.
3. It optionally deploys your plugin artifact.
4. It prints audit notes before the final confirmation if readable source/config text is available.

What the audit can and cannot tell you:

- If you provide `.sp` source or a package containing source/config text, the deployer scans for outbound URLs, HTTP/socket APIs, server-command calls, file-write calls, and SQL access.
- If you provide only a compiled `.smx`, the deployer cannot verify the safety claim from the binary alone. It can still install the plugin, but the audit remains incomplete.
- The audit is heuristic, not a proof of safety. It is meant to surface the most important behavior quickly before installation.

Best practice for community plugins:

- Prefer a packaged release that includes the original `.sp` source alongside the compiled `.smx`.
- Review any hardcoded URLs and confirm the domain belongs to the service you expect.
- Avoid plugins that rely on broad `ServerCommand` usage unless you understand the control path.
- Keep a copy of the original plugin package so you can diff future updates.

Typical verification after the server is online:

```text
meta version
meta list
sm plugins list
sm exts list
```

If a plugin should show your server in Discord or a control panel, also watch the server console on startup for load errors and the SourceMod logs under `/home/steam/csgo_server/csgo/addons/sourcemod/logs/`.

---

## Server Management — Connection & Administration

Once the server is running, you need to know how to connect, log in as admin, execute commands, and modify settings.

### Connecting as a Player

In your CS:GO client, press `~` (tilde) to open the console and enter:

```
connect <server_ip>:<port>
```

Replace `<server_ip>` with the public IP you entered and `<port>` with the game port (default `27015`).

**Example:**
```
connect 203.0.113.42:27015
```

If the server has a join password, CS:GO will prompt you for it.

---

### Admin Login — Using RCON

**Remote Console (RCON)** lets you execute server commands from your client or SSH terminal.

**Recommended client flow:**

1. From the CS:GO main menu, open the console (`~`)
2. Enter: `rcon_address SERVER_IP:SERVER_PORT`
3. Enter: `rcon_password "YOUR_ADMIN_PASSWORD"`
4. Test it with: `rcon status`

Use the dedicated RCON port from the wizard for `SERVER_PORT` (default `27016`), not the gameplay port.

This is more reliable than typing `rcon_password` after you are already in-game. Some client builds behave inconsistently once you are connected to the server.

**From SSH terminal:**

```bash
# Attach to the server console and issue commands locally:
su - steam -c 'tmux attach -t csgo'
```

Once attached, you can type server commands directly into the SRCDS console without using RCON at all. If you prefer remote admin tools, use a real Source RCON client instead of `nc`.

For frictionless SSH administration, the deployer also creates:

```bash
/home/steam/csgo_server/rcon.sh
```

Examples:

```bash
su - steam -c '/home/steam/csgo_server/rcon.sh status'
su - steam -c '/home/steam/csgo_server/rcon.sh changelevel de_dust2'
```

---

### Essential RCON Commands

| Command | Effect |
|---|---|
| `say TEXT` | Broadcast a message to all players |
| `status` | List all connected players and their scores |
| `changelevel MAP_NAME` | Change map immediately |
| `kick PLAYER_ID` | Remove a player (use `status` to find ID) |
| `ban PLAYER_ID TIME` | Ban a player (time in minutes; `0` = permanent) |
| `mp_autokick 1` | Enable auto-kick of idle players |
| `bot_add` | Add a bot |
| `bot_knifing_skill 100` | Make bots more aggressive |
| `quit` | Gracefully shut down the server |

---

### Game Mode Configuration

CS:GO dedicated servers do not become competitive just because `mp_freezetime`, `mp_roundtime`, and economy values look competitive. The server must receive the correct mode pair as well.

Core pairs:

| Preset | `game_type` | `game_mode` |
|---|---:|---:|
| Casual | `0` | `0` |
| Competitive | `0` | `1` |
| Arms Race | `1` | `0` |
| Demolition | `1` | `1` |
| Deathmatch | `1` | `2` |

The deployer applies these values in both the launch command and `server.cfg`.

Recommended presets:

**Competitive (default)**
```
game_type 0              // Classic game type
game_mode 1              // Competitive mode
mp_warmuptime 60          // Warm-up before match starts
mp_freezetime 15          // Players frozen at round start
mp_roundtime 1.92         // ~115 seconds per round
mp_startmoney 2400        // Starting economy
mp_maxmoney 16000         // Maximum team money
```

**Casual mode**
```
game_type 0
game_mode 0
mp_warmuptime 0           // No warm-up
mp_freezetime 3           // Very short freeze
mp_roundtime 3            // Longer rounds
mp_startmoney 5000        // Higher starting cash
mp_maxmoney 24000         // Higher economy
```

**Deathmatch**
```
game_type 1
game_mode 2               // Deathmatch mode
mp_startmoney 10000       // Very high cash
mp_buytime 9999           // Always allow weapon selection
mp_freezetime 0           // No freeze between spawns
```

To apply changes after deployment:

**Option A (in-game, temporary):**
```
rcon mp_freezetime 10
rcon mp_warmuptime 120
```

**Option B (persistent, via SSH):**
```bash
sudo -u steam nano /home/steam/csgo_server/csgo/server.cfg
# Edit settings, save (Ctrl+X, Y, Enter)
su - steam -c 'tmux attach -t csgo'
# Then type: quit
```

---

### Bot Configuration

Control bot behavior with these commands:

| Command | Effect |
|---|---|
| `bot_quota 10` | Set number of bots (0-32) |
| `bot_difficulty 1` | Skill level (0=easy, 1=normal, 2=hard, 3=expert) |
| `bot_add` | Add one bot immediately |
| `bot_remove` | Remove one bot |
| `bot_knifing_skill 100` | Make bots knife-aggressive (0-100) |
| `bot_buy WEAPON` | Force bots to buy specific weapon |

Example: **Spawn 10 hard bots for practice:**
```
rcon bot_quota 10
rcon bot_difficulty 2
```

---

### Monitoring & Logging

The server logs all actions to:

```
/home/steam/csgo_server/csgo/logs/
```

Common log files:

| File | Contains |
|---|---|
| `L*.log` | General server activity and kills/deaths |
| `bans.log` | Banned players (if `sv_logbans 1`) |
| `console.log` | Full console output |

View live logs during gameplay:

```bash
sudo -u steam tail -f /home/steam/csgo_server/csgo/logs/L*.log
```

---

### Stopping the Server

**Graceful stop (from rcon):**
```
rcon quit
```

This gives connected players a chance to disconnect cleanly. Allow ~10 seconds for the process to exit.

**Immediate stop (from SSH):**
```bash
sudo -u steam pkill -f srcds_run
```

Then detach from tmux: `Ctrl+B` then `D`

**Clean restart (stop + start):**
```bash
# Kill the old process
sudo -u steam pkill -f srcds_run

# Wait a moment
sleep 2

# Start fresh in a new tmux session
sudo -u steam tmux new-session -d -s csgo 'cd /home/steam/csgo_server && ./start_server.sh'
```

---

### Server Performance Tuning

After the server is running, you can optimize performance by tweaking `server.cfg`:

**For high-skill competitive servers:**
```
fps_max 300              // Allow server to run at max capacity
sv_mincmdrate 64         // Minimum updates per player
sv_maxcmdrate 128        // Maximum updates per player
net_splitpacketsize 1260 // Lower for unstable connections
sv_client_predict 0      // Disable prediction for LAN/LAN-like
```

**For casual servers with many players:**
```
fps_max 100              // Limit CPU use
sv_mincmdrate 30         // Lower server load
sv_maxcmdrate 64
sv_hibernate_when_empty 1  // Sleep during idle periods
```

Apply changes live with `rcon` or restart the server.

---



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
