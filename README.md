# RelayForge
**By Ragequits** — Python game-server ↔ Discord bridge for Pterodactyl.

RelayForge connects Pterodactyl game console output to Discord, sends Discord messages back to the game, and reports server problems.

## Features
- Factorio chat, player join/leave, `!players`, research notifications with optional Factorio helper mod.
- Generic game adapter with custom chat regex and outgoing command template.
- `!help`, `!ping`, `!status`, `!uptime` (HH:MM); role-restricted `!pause`, `!resume`, `!announce`.
- Auto-resolved Discord user/role mentions for Factorio chat.
- Pterodactyl resource monitoring, error burst alerts, role pings and cooldowns.
- `commands.json` dynamically reloads aliases, disabled commands and custom replies.
- Experimental known-pattern chat detection, not a neural network.

## Quick start
1. Install Python 3.11 or newer.
2. `python -m pip install -r requirements.txt`
3. Copy `.env.example` to `.env`; fill in your Pterodactyl panel, game server ID, Client API key, Discord bot token and channel ID. Never commit real secrets.
4. Enable **Message Content Intent** and **Server Members Intent** in the Discord Developer Portal.
5. `python main.py`

For Pterodactyl, upload the source to a separate Python server and set startup to install requirements then run `python3 -u main.py`. Install the dependencies in a writable location if your container does not allow global pip installs.

## Configuration
- `GAME_TYPE=factorio` (default) or `generic`.
- Generic: `GAME_CHAT_REGEX` must capture player (group 1) and message (group 2).
- `GAME_SEND_COMMAND`: generic console command template using `{user}` and `{message}`; keep blank for receive-only.
- `CHAT_AUTO_DETECT=true`: try known formats after 3 sample chat lines.
- `ALERT_CPU_THRESHOLD=0`: disable CPU alerts; other values trigger after consecutive checks.
- Blank `ALERT_DISCORD_CHANNEL_ID`: use regular chat channel.
- `DISCORD_COMMAND_ROLE_ID`: optional authorized admin role.

## Testing
```bash
python -m unittest discover -s tests -v
python -m compileall -q relayforge main.py
```

## Limitations
- The first Python release needs live integration testing.
- Generic automatic detection does not infer outgoing commands.
- Factorio research events require a separate compatible helper mod.
- Container uptime is available only when Pterodactyl includes an uptime field.
- Do not run two bridges against the same game/Discord channel; duplicated messages may result.
- Before commercial redistribution, review dependencies, permissions, IP ownership, licensing, and naming clearance.

Copyright © Ragequits. No open-source license has been assigned yet.
