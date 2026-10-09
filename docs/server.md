# Game server

This covers the `/start` command that boots the Minecraft server. Code lives in `src/main.py`.

## What it does

Someone types `/start` in the game chat channel and the bot launches the UnitedBlocks server on the host machine. If the server is already up, it says so and does nothing.

The command only works in one channel. That ID is `MinecraftServer_Channel` in `src/main.py` (the `#game-chat` channel). Anywhere else gets an ephemeral "only in #game-chat" reply.

## How launch works

All server settings live in the `minecraft` section of `config/config.json` (`script_path`, `working_dir`, `script_name`, `tmux_session`, `process_match`, `tunnel_command`, `terminal_emulator`, `terminal_args`). `src/main.py` loads them at startup with no hardcoded fallbacks.

`start_minecraft_server` checks the script exists at `script_path`, then checks if the server is already running. If not, it calls `start_with_terminal`.

`start_with_terminal` uses tmux. It creates a detached session called `tmux_session`, splits the window horizontally, sends `cd <working_dir> && ./<script_name>` to the left pane and `<tunnel_command>` to the right pane, then opens a `<terminal_emulator>` window attached to that session.

This is tied to one Linux box. It assumes tmux, the configured terminal emulator, and tunnel command are installed, and that the server files sit at the configured path. To run elsewhere, edit `config/config.json`.

## How it knows the server is running

`is_minecraft_server_running` does two checks.

First it runs `tmux has-session -t <tmux_session>`. If that session exists, the server counts as running.

If tmux is missing or has no such session, it walks the process list with `psutil` and looks for `process_match` in the process name or full command line. The command line check matters because the script shows up as `bash .../<script_name>`, not as a process named `process_match`.

Anything unexpected just returns false.

## Responses

- Already running: "Minecraft server is already running!"
- Fresh start: "Minecraft server started successfully!" plus a short status line.
- Failure: "Error starting Minecraft server..." with the error text if there is one. Failures go to `logs/bot.log` too.

## Files and deps

Needs `psutil` for the process scan. Also needs the system tools tmux plus the configured `terminal_emulator` and `tunnel_command`, plus the `prctl` import at the top of `src/main.py`.
