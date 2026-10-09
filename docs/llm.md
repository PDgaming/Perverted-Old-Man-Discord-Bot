# LLM

This is how William thinks. Code lives in `src/responses.py`.

## Backend

William talks through Groq. The model lives in `config/config.json` (`chat_model`), the persona lives in `prompts/system.md` (referenced by `system_prompt_file` in `config/config.json`), loaded by `src/responses.py` via `load_config` + `load_system_prompt`. Calls use the configured temperature and token cap.

You need `GROQ_API_KEY` in `config/.env` for any of this to work. Without it the module raises on import.

## Persona

The system prompt defines the persona of the agent.

## Tools

Every message first passes through the Clef router (`src/router.py`, model
`clef-flash` via Cloudflare Workers AI). Clef answers two questions in one
batched call:
`route` (choice: needs_search / needs_fetch / needs_lookup / none) and `should_remember`
(noul: does the speaker state a personal fact). `decide_route` maps the answers to a
forced tool set. The Groq call then gets only those tools plus an ephemeral system nudge
("you MUST call ..."), with `tool_choice="auto"`. `tool_choice="required"`, named-function
choices, and `"none"` are deliberately not used: Groq hard-errors (400) whenever the model
disagrees (calling on "none", answering directly on "required"). A Clef "plain chat" verdict
is advisory only: all tools stay available and the model has final say. Anything Clef can't decide (HTTP error, timeout, low confidence, missing credentials) falls
back to all four tools with `auto`, which is the pre-router behavior. Router knobs live under
the `router` key in `config/config.json` (`enabled`, `model`, `timeout_s` default 8,
`route_confidence_min`, `remember_fire`, `remember_skip`). Needs `WORKERS_AI_API_KEY` plus
`WORKERS_AI_ACCOUNT_ID` (or `CLOUDFLARE_ACCOUNT_ID`, or bare `ACCOUNT_ID`) in `config/.env` (endpoint
`https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/@cf/cloudflare/clef-flash`).
Expect well under a second added latency per message; slow calls fall back automatically.

Set `router.backend` to `"tev"` (or pass `--router tev` to `start.sh`) to use the old local
Tev model (`tev_model`, default `tev1:0.8b`) via the Ollama daemon (`OLLAMA_HOST`, default
localhost:11434, `keep_alive` still applies). Needs the `ollama` package plus
`ollama pull tev1:0.8b`. Default backend is `"clef"`.

`start.sh` passes flags through to `src/main.py`. `--no-llm` runs the bot with no Groq
calls: every reply is `offline_message` from `config/config.json`. The Clef/Tev router
still runs (decisions are logged), but web tools never execute since there is no LLM turn
to call them. History is still saved locally. Useful for testing Discord plumbing
without spending Groq calls. `--router clef|tev` overrides the config file backend.

The model gets four tools on every call.

`web.run` searches the web. The definition is in `src/responses.py`, but the actual search runs through Tavily in `execute_web_run`. Needs `TAVILY_API_KEY`. Query plus an optional result count, capped at 10.

`web.fetch` reads a specific URL the user pasted. Runs through Tavily Extract in `execute_web_fetch` (`basic` depth, `markdown` format, 30s timeout). Content is truncated to `max_fetch_chars` (4000) with a `[truncated]` marker, and tool output stays in the ephemeral call messages, never in `data/chat_history.json`. Only domains in `allowed_fetch_domains` (`config/config.json`) pass `is_fetch_domain_allowed` (base domain plus subdomains, case-insensitive, http/https only). A blocked URL is caught by a pre-scan in `chat_with_history` and returns `fetch_blocked_message` directly with no Groq or Tavily call. The in-tool check is defense in depth for model-hallucinated URLs. Basic extraction costs 1 credit per 5 successful URLs.

`memory.lookup` recalls a stored user profile. Takes a Discord user ID or username and returns what William saved about that person. Usernames must be copied exactly, never spell-corrected. A named user with no match returns a miss (plus a did-you-mean retry hint), never another user's profile; the current speaker is the fallback only when no user was named at all.

`memory.remember` saves a fact about a user. The prompt tells the model to call this whenever someone shares personal details. Notes are supposed to stay at 2 to 3 short sentences. Single-writer rule: only the LLM ever writes notes (Clef only classifies), at most one `memory.remember` call per turn, and `add_note` skips exact duplicates.

The tool loop in `chat_with_history` runs up to `MAX_TOOL_TURNS` (4). Tool calls are handled first even when the model also returns text on the same turn. Each turn it either runs tool calls, appends results as tool messages, and tries again, or gets text back and stops. If it gets neither text nor tool calls, it raises. If it never gets text after 4 turns, it raises. If `user_message` has a URL, the model should call `web.fetch`; otherwise `web.run`, including for technical questions the user explicitly asks about. The model summarizes what the tools return instead of refusing.

## History

Short term memory is `data/chat_history.json`. It holds the system prompt plus the last 10 exchanges. Older turns get trimmed in `chat_with_history`.

On load, the code checks the first message. If it is missing or stale, it inserts the current `INITIAL_SYSTEM_PROMPT` (built from `prompts/system.md`). So editing the prompt in `prompts/system.md` applies on next restart.

The current speaker's stored profile is injected as a one-off system message right before the latest user message. It is not appended to the saved history, so it does not bloat the file. A second ephemeral system message injects the current local date and time, so time/date questions are answered directly with no tool calls.

Replies that mention another message are flattened into text first. Something like "user X replied to author Y, original message, user's reply". The model never sees Discord reply objects directly.

## Cleaning and errors

Model output goes through `extract_response_content` and `clean_response`, which strip `<think>` blocks and whitespace.

`get_response` is the entry point the Discord layer calls. Empty input returns "Empty input." Rate limit errors get a "slow down" reply. Anything else returns a generic "something went wrong" message. Errors are logged in both cases.

## Settings worth knowing

All in `config/config.json`:

- `chat_model = "openai/gpt-oss-20b"`
- `history_file_path = "chat_history.json"`
- `max_tool_turns = 4`
- `allowed_fetch_domains = ["en.wikipedia.org"]` (empty means deny all)
- `fetch_blocked_message` is the fixed refusal for non-listed domains
- `max_fetch_chars = 4000` caps Extract output passed to the model
- History window (`max_history`) is 10 turns
- `GEMINI_API_KEY` appears in `config/.env.example` but nothing in `src/responses.py` uses it
