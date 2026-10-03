# Discord bot

This is the Discord layer. Code lives in `main.py`.

## Setup

The bot runs with `commands.Bot(command_prefix="/")` and the `message_content` intent. That intent has to be enabled in the Discord Developer Portal too, or the bot sees nothing.

It reads `DISCORD_TOKEN` from `.env` and exits if it is missing. Slash commands sync on `on_ready`, so `/grandpa` and `/start` show up after a restart.

Logs go to stdout and `bot.log`.

## How William listens

`on_message` only cares about one channel, `PervertedOldMan_Channel` in `main.py`. Everything else is ignored. Messages from the bot itself are ignored.

If a message is a reply, the bot fetches the original message and passes its text and author to the LLM. That is how William follows reply threads. Deleted originals just log a warning.

## Slash commands

`/grandpa` takes a message string and works from any channel. It defers the reply, runs the same LLM path as normal chat, echoes the user's line, then sends William's answer. Errors get a "dropped my glasses" fallback.

`/start` is documented separately in [server](server.md). It only runs in the game chat channel.

## Message sending

`send_chunked_message` splits William's reply on periods, question marks, and newlines, then sends each piece as its own Discord message. That is why he talks in bursts instead of paragraphs. Empty replies get an "I don't have a response for that" fallback.

Starting a channel message with `?` sends the reply by DM instead of in channel. The `?` itself is stripped before the LLM sees it.

## Filters

A few inputs never reach the model.

`!ignore` at the start skips the LLM entirely. In the channel it stays silent. Through `/grandpa` it replies that the message was ignored.

Messages with both `gf` (or `girlfriend`, fuzzy matched) and `prodeh` are blocked with a short refusal.

Filler gets skipped. Things like "umm", "uh", "hmm", "ok", "lol", "lmao", "brb", "idk", "?", "...". In channel these are silently ignored. Through `/grandpa` they get a short notice.

## What gets passed to the LLM

Every call sends the message text, username, Discord user ID, roles, display name, plus reply context if there is any. User ID and roles feed the memory store, so William can tell people apart even when usernames change.
