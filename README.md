# Perverted Old Man Discord Bot

This is a Discord roleplay bot. It plays William Hartwell, a retired literature professor in his 60s who hangs out in chat and talks like a real person.

## What it does

William does four things.

He chats in a dedicated channel. You write something, he answers in short back and forth messages instead of one big wall of text.

He answers anywhere through a slash command. Type `/grandpa` plus your message in any channel and he replies there.

He remembers people. If you tell him your name, job, hobbies, or opinions, he saves that and brings it up later.

He can start a game server. `/start` command boots a Minecraft server.

## How it is put together

There are four parts. Each has its own doc if you want detail.

- [Discord bot](docs/discord-bot.md). Listens in one channel, handles `/grandpa`, filters spam and junk, splits long replies into chat-sized messages.
- [LLM](docs/llm.md). Talks to Groq, runs the web search and memory tools, keeps short term chat history.
- [User memory](docs/user-memory.md). A small JSON store that keeps what William knows about each person.
- [Game server](docs/server.md). The `/start` command that launches the Minecraft server.

Short term chat lives in `chat_history.json`. Long term facts about people live in `user_memory.json`. Both are local files, not synced anywhere.

## Quick pointer

This README is only the overview. Setup, config, and internals are in the docs above. Start with the Discord bot doc if you are trying to run this yourself, since that is where the intents live.
