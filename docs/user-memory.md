# User memory

This is William's long term memory for people. Code lives in `src/user_memory.py`, data in `data/user_memory.json`.

## Stored data

The file is a JSON object keyed by Discord user ID. Each entry has a username, display name, roles list, and a notes list.

```json
{
  "[discord user ID]": {
    "username": string,
    "display_name": string,
    "roles": string[],
    "notes": string[]
  }
}
```

The file is gitignored. It only exists on the machine running the bot.

## Functions

`upsert_user_profile` creates or refreshes a profile from Discord data on every message. Username, display name, and roles get updated. Notes are kept.

`find_user` matches by ID first, then by case-insensitive username or display name. Returns the store key or nothing.

`add_note` appends a note unless it is identical to the last one. Saves the file after each write.

`profile_to_context` turns a profile into a short text block for the model. It includes ID, names, roles, and the last 3 notes capped at 400 chars.

`load_user_memory` and `save_user_memory` handle file IO. Corrupt or missing files start empty and log an error.

## How the LLM uses it

On each chat call, the Discord layer passes user ID, username, roles, and display name. `src/responses.py` upserts the profile, renders it with `profile_to_context`, and injects it as a one-off system message. That message is not saved to chat history.

The system prompt tells the model to call `memory.remember` whenever someone shares personal details, and `memory.lookup` when someone asks about another user. So the flow is plain: chat reveals a fact, the model saves it, later chats include it in context.

Limits are small on purpose. 3 notes and 400 chars in context keep the prompt short. Full history stays in the JSON file either way.
