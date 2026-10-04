# Web fetch plan

## Goal

Let William read a link a user pastes and summarize it, using Tavily Extract. Only domains listed in `config.json` are fetchable. All other domains get a fixed refusal, no LLM call.

Decisions already made:
- Any general URL type, gated by allowlist.
- Stay in character. Tech/AI/LLM deflection rule stays. Fetched tech pages still get deflected.
- Truncate page text to ~4000 chars, keep tool output out of `chat_history.json`.
- Allowlist match is base domain plus subdomains.
- Blocked domains bypass the LLM with a fixed reply.

## Current state

- `responses.py:69-87` defines `WEB_SEARCH_TOOL` (`web.run` -> `tavily_client.search`). No fetch path.
- `responses.py:207-214` is `execute_web_run`. `responses.py:246-255` is `execute_tool` router.
- `responses.py:258-368` is `chat_with_history`. It builds ephemeral `messages = list(chat_history)`, runs up to `MAX_TOOL_TURNS=3`, saves only user + final assistant to `chat_history.json`.
- `responses.py:315-350` loop bug: `if choice.message.content: break` ignores simultaneous `tool_calls`. Must handle tool calls first.
- `config.json` holds `chat_model=openai/gpt-oss-20b`, `max_tool_turns=3`, `max_tokens=1000`, `system_prompt` (3432 chars, includes tech deflect + memory instructions).
- `main.py:73-115` splits replies by sentence for Discord 2000 char limit.
- Tavily client in `.venv` supports: `extract(self, urls: Union[List[str], str], include_images=None, extract_depth='basic'|'advanced', format='markdown'|'text', timeout=30, include_favicon=None, include_usage=None, query=None, chunks_per_source=None) -> dict` with `results[].raw_content` and `failed_results[]`.

## Changes

### 1. Config

Add to `config.json` and `config.example.json`:

```json
{
  "allowed_fetch_domains": ["en.wikipedia.org"],
  "fetch_blocked_message": "Sorry, I can't open links from that site.",
  "max_fetch_chars": 4000,
  "max_tool_turns": 4
}
```

- `allowed_fetch_domains`: bare base domains, lowercase. Empty or missing means deny all.
- `fetch_blocked_message`: fixed user-facing refusal. No domain list leaked.
- `max_fetch_chars`: truncation cap before passing to model.
- Bump `max_tool_turns` 3 -> 4 to allow `search -> fetch -> answer` chains.

### 2. `responses.py` — allowlist helper

```python
ALLOWED_FETCH_DOMAINS: Final[set[str]] = {d.lower().strip().rstrip(".") for d in _config.get("allowed_fetch_domains", [])}
FETCH_BLOCKED_MESSAGE: Final[str] = _config.get("fetch_blocked_message", "Sorry, I can't open links from that site.")
MAX_FETCH_CHARS: Final[int] = _config.get("max_fetch_chars", 4000)

def is_fetch_domain_allowed(url: str) -> bool:
    # urlparse, require scheme http/https, require hostname
    # normalize host: lower, strip trailing dot
    # return True if host == allowed or host.endswith("." + allowed)
    # False for localhost, empty host, ftp/javascript/data, userinfo tricks fall out via hostname parsing
```

Notes:
- Compare against `hostname`, not netloc, so ports are ignored.
- `evilen.wikipedia.org` must fail when `en.wikipedia.org` is allowed (suffix check on dot boundary handles this; `wikipedia.org` allows `en.wikipedia.org`).
- Log blocked attempts with `logger.warning`.

### 3. `responses.py` — fetch tool

Tool definition next to `WEB_SEARCH_TOOL`:

```python
WEB_FETCH_TOOL = {
  "type": "function",
  "function": {
    "name": "web.fetch",
    "description": "Fetch full content of a specific URL the user pasted. Use when user_message contains an http/https link. Only works for allowed domains, otherwise blocked.",
    "parameters": {
      "type": "object",
      "properties": {
        "url": {"type": "string", "description": "Full http/https URL to fetch"},
        "query": {"type": "string", "description": "Optional focus topic for reranking"}
      },
      "required": ["url"]
    }
  }
}
```

Executor:

```python
def execute_web_fetch(tool_call) -> str:
    # 1. parse args, validate url present
    # 2. is_fetch_domain_allowed(url) else return json.dumps({"error": "blocked", "message": FETCH_BLOCKED_MESSAGE})
    # 3. tavily_client.extract(urls=url, extract_depth="basic", format="markdown", timeout=30, query=query?)
    # 4. check results[0].raw_content, failed_results
    # 5. truncate to MAX_FETCH_CHARS + "[truncated]" marker
    # 6. return json.dumps({"url": url, "content": truncated}) or {"error": ...}
    # 7. catch exceptions -> json.dumps({"error": str(e)})
```

Use `basic` depth and `markdown` format. Cost is 1 credit per 5 successful extractions.

### 4. Pre-scan bypass (saves Groq turn + Tavily credit)

At top of `chat_with_history`, before first Groq call:

```python
URL_RE = re.compile(r"https?://[^\s<>\"']+")
for url in URL_RE.findall(user_message):
    if not is_fetch_domain_allowed(url):
        logger.warning(f"fetch blocked: {url} from {username}")
        chat_history.append({"role": "user", "content": full_user_message})
        chat_history.append({"role": "assistant", "content": FETCH_BLOCKED_MESSAGE})
        # trim + save_history as normal, return FETCH_BLOCKED_MESSAGE
```

Also keep in-tool re-check for model-hallucinated URLs.

Blocked path:
- No Groq call, no Tavily call.
- Saves only user + fixed reply (2 short entries).
- Never lists allowed domains.

### 5. Tool loop wiring

- Route `web.fetch` in `execute_tool`.
- Include in call: `tools=[WEB_SEARCH_TOOL, WEB_FETCH_TOOL, MEMORY_LOOKUP_TOOL, MEMORY_REMEMBER_TOOL]`.
- Fix loop order: process `tool_calls` first even if `content` is also present. Only break when content present and no tool calls.
- System prompt addition (one line in `config.json`): `If user message contains a URL, call web.fetch for it; use web.run for general queries. Tech deflection still applies to final answer.`

### 6. History and output limits

- No change needed for non-saving: tool messages already live only in local `messages`, not `chat_history`. Keep that.
- Truncated fetch text never touches `chat_history.json` or `user_memory.json`.
- Final summary still capped by `MAX_TOKENS=1000` and split by `send_chunked_message` in `main.py`.

## Security

- Scheme whitelist: http/https only.
- Hostname suffix match, case-insensitive.
- Tavily follows redirects. We validate the requested URL, not the final redirect target. Acceptable for this bot. If stricter needed later, validate `results[0].url` against allowlist too and drop on mismatch.
- Do not echo allowed list to users.
- Rate path is same as existing `web.run`. No new secrets. Uses existing `TAVILY_API_KEY`.

## Docs

Update `docs/llm.md`:
- Describe `web.fetch`, allowlist location, blocked bypass, truncation, cost.

## Verification

1. Unit (`.venv/bin/python`):
   - `is_fetch_domain_allowed("https://en.wikipedia.org/wiki/X")` -> True
   - `"https://sub.en.wikipedia.org/x"` with base `wikipedia.org` -> True
   - `"https://evilen.wikipedia.org/x"` -> False
   - `"HTTPS://EN.WIKIPEDIA.ORG:443/x"` -> True
   - `"ftp://en.wikipedia.org/x"`, `"javascript:alert(1)"`, `"http://localhost/x"` -> False
   - `execute_web_fetch` allowed URL returns truncated content <= `MAX_FETCH_CHARS`
   - blocked URL returns blocked JSON without network call
2. History check: `chat_history.json` growth after blocked fetch is 2 short entries. After allowed fetch, no raw page text in file (`grep raw_content` empty).
3. Live Discord: paste allowed article link -> short William-style summary. Paste non-listed link -> exact `fetch_blocked_message`. Paste AI model link (e.g. HuggingFace) -> deflect in character if not allowed, or deflect as tech even if allowed.
4. Logs: `bot.log` shows `web.fetch` turn and `fetch blocked` warnings.

## Open item

Seed value for `allowed_fetch_domains`. Proposal: start with `["en.wikipedia.org"]`, expand on request.

## Out of scope

- No crawl/map, no multi-URL batch, no image/favicon extraction.
- No redirect-target revalidation (noted above).
- No change to `web.run` permissions or memory tools.
