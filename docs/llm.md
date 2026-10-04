# LLM

This is how William thinks. Code lives in `responses.py`.

## Backend

William talks through Groq. The model and prompt live in `config.json` (`chat_model`, `system_prompt`), loaded by `responses.py` via `load_config`. Calls use the configured temperature and token cap.

You need `GROQ_API_KEY` in `.env` for any of this to work. Without it the module raises on import.

## Persona

The system prompt defines the persona of the agent.

## Tools

The model gets four tools on every call.

`web.run` searches the web. The definition is in `responses.py`, but the actual search runs through Tavily in `execute_web_run`. Needs `TAVILY_API_KEY`. Query plus an optional result count, capped at 10.

`web.fetch` reads a specific URL the user pasted. Runs through Tavily Extract in `execute_web_fetch` (`basic` depth, `markdown` format, 30s timeout). Content is truncated to `max_fetch_chars` (4000) with a `[truncated]` marker, and tool output stays in the ephemeral call messages, never in `chat_history.json`. Only domains in `allowed_fetch_domains` (`config.json`) pass `is_fetch_domain_allowed` (base domain plus subdomains, case-insensitive, http/https only). A blocked URL is caught by a pre-scan in `chat_with_history` and returns `fetch_blocked_message` directly with no Groq or Tavily call. The in-tool check is defense in depth for model-hallucinated URLs. Basic extraction costs 1 credit per 5 successful URLs.

`memory.lookup` recalls a stored user profile. Takes a Discord user ID or username and returns what William saved about that person.

`memory.remember` saves a fact about a user. The prompt tells the model to call this whenever someone shares personal details. Notes are supposed to stay at 2 to 3 short sentences.

The tool loop in `chat_with_history` runs up to `MAX_TOOL_TURNS` (4). Tool calls are handled first even when the model also returns text on the same turn. Each turn it either runs tool calls, appends results as tool messages, and tries again, or gets text back and stops. If it gets neither text nor tool calls, it raises. If it never gets text after 4 turns, it raises. If `user_message` has a URL, the model should call `web.fetch`; otherwise `web.run`. Tech deflection still applies to the final answer.

## History

Short term memory is `chat_history.json`. It holds the system prompt plus the last 10 exchanges. Older turns get trimmed in `chat_with_history`.

On load, the code checks the first message. If it is missing or stale, it inserts the current `INITIAL_SYSTEM_PROMPT` (built from `config.json`). So editing the prompt in `config.json` applies on next restart.

The current speaker's stored profile is injected as a one-off system message right before the latest user message. It is not appended to the saved history, so it does not bloat the file.

Replies that mention another message are flattened into text first. Something like "user X replied to author Y, original message, user's reply". The model never sees Discord reply objects directly.

## Cleaning and errors

Model output goes through `extract_response_content` and `clean_response`, which strip `<think>` blocks and whitespace.

`get_response` is the entry point the Discord layer calls. Empty input returns "Empty input." Rate limit errors get a "slow down" reply. Anything else returns a generic "something went wrong" message. Errors are logged in both cases.

## Settings worth knowing

All in `config.json`:

- `chat_model = "openai/gpt-oss-20b"`
- `history_file_path = "chat_history.json"`
- `max_tool_turns = 4`
- `allowed_fetch_domains = ["en.wikipedia.org"]` (empty means deny all)
- `fetch_blocked_message` is the fixed refusal for non-listed domains
- `max_fetch_chars = 4000` caps Extract output passed to the model
- History window (`max_history`) is 10 turns
- `GEMINI_API_KEY` appears in `.env.example` but nothing in `responses.py` uses it
