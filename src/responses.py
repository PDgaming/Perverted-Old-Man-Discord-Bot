from typing import Final, List, Dict, Optional, Any
import os
import logging
import re
from datetime import datetime
from urllib.parse import urlparse
from dotenv import load_dotenv
from groq import Groq
from tavily import TavilyClient
import json
from src import user_memory as um
from src import router as tev_router
from src.paths import (
    CHAT_HISTORY_PATH,
    CONFIG_DIR,
    CONFIG_FILE_PATH,
    PROJECT_ROOT,
    ensure_runtime_dirs,
    resolve_root_relative,
)

# Configure logging
logger = logging.getLogger(__name__)

# Load environment variables
ensure_runtime_dirs()
load_dotenv(os.path.join(CONFIG_DIR, ".env"))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

# Initialize Groq client
TOKEN: Final[str] = os.getenv("GROQ_API_KEY")
if not TOKEN:
    logger.error("Groq API key not found in environment variables")
    raise ValueError("GROQ_API_KEY environment variable is required")

try:
    groq_client = Groq(api_key=TOKEN)
except Exception as e:
    logger.error(f"Failed to initialize Groq client: {e}")
    raise

# Initialize Tavily client for executing web searches
TAVILY_API_KEY: Final[str] = os.getenv("TAVILY_API_KEY")
if not TAVILY_API_KEY:
    logger.error("Tavily API key not found in environment variables")
    raise ValueError("TAVILY_API_KEY environment variable is required")

try:
    tavily_client = TavilyClient(api_key=TAVILY_API_KEY)
except Exception as e:
    logger.error(f"Failed to initialize Tavily client: {e}")
    raise

def load_config(path: str = CONFIG_FILE_PATH) -> dict:
    """Load LLM config (model settings + prompt file reference) from a JSON file."""
    try:
        with open(path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error(f"Config file not found: {path}")
        raise
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse config file {path}: {e}")
        raise


def load_system_prompt(config: dict, config_path: str = CONFIG_FILE_PATH) -> str:
    """Load the system prompt from its .md file, falling back to inline config."""
    prompt_file = config.get("system_prompt_file")
    if prompt_file:
        if not os.path.isabs(prompt_file):
            # Relative paths resolve against the project root, so the config
            # file can live in config/ while referencing prompts/ or data/.
            prompt_file = resolve_root_relative(prompt_file)
        try:
            with open(prompt_file, "r") as f:
                return f.read().strip()
        except FileNotFoundError:
            logger.error(f"System prompt file not found: {prompt_file}")
            raise
        except OSError as e:
            logger.error(f"Failed to read system prompt file {prompt_file}: {e}")
            raise
    # Backwards compat: older configs embedded the prompt directly.
    if "system_prompt" in config:
        return config["system_prompt"]
    raise ValueError(
        "No system prompt configured: set 'system_prompt_file' in config.json"
    )


_config = load_config()
CHAT_MODEL: Final[str] = _config.get("chat_model", "openai/gpt-oss-20b")
HISTORY_FILE_PATH: Final[str] = resolve_root_relative(
    _config.get("history_file_path", CHAT_HISTORY_PATH)
)
MAX_TOOL_TURNS: Final[int] = _config.get("max_tool_turns", 3)
MAX_TOKENS: Final[int] = _config.get("max_tokens", 1000)
TEMPERATURE: Final[float] = _config.get("temperature", 0.7)
MAX_HISTORY: Final[int] = _config.get("max_history", 10)
ALLOWED_FETCH_DOMAINS: Final[set] = {
    d.lower().strip().rstrip(".") for d in _config.get("allowed_fetch_domains", [])
}
FETCH_BLOCKED_MESSAGE: Final[str] = _config.get(
    "fetch_blocked_message", "Sorry, I can't open links from that site."
)
MAX_FETCH_CHARS: Final[int] = _config.get("max_fetch_chars", 4000)
ROUTER_CFG: Final[tev_router.RouterConfig] = tev_router.RouterConfig.from_dict(_config)

TOOL_DEFS: Final[dict] = {}

URL_RE = re.compile(r"https?://[^\s<>\"']+")

_SYSTEM_PROMPT_TEXT: Final[str] = load_system_prompt(_config)

# web.run tool definition for Groq's built-in web search
WEB_SEARCH_TOOL: Final[dict] = {
    "type": "function",
    "function": {
        "name": "web.run",
        "description": "Search the web for current or technical information. Use this when the user explicitly asks about programming, computers, AI, or other technical topics you don't know from memory, then summarize what you find.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "topn": {
                    "type": "integer",
                    "description": "Number of results to return",
                },
                "source": {"type": "string", "enum": ["web", "news"]},
            },
            "required": ["query"],
        },
    },
}

# web.fetch tool definition: fetch full content of a user-pasted URL via Tavily Extract
WEB_FETCH_TOOL: Final[dict] = {
    "type": "function",
    "function": {
        "name": "web.fetch",
        "description": "Fetch full content of a specific URL the user pasted. Use when user_message contains an http/https link. Only works for allowed domains, otherwise blocked.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Full http/https URL to fetch",
                },
                "query": {
                    "type": "string",
                    "description": "Optional focus topic for reranking",
                },
            },
            "required": ["url"],
        },
    },
}

MEMORY_LOOKUP_TOOL: Final[dict] = {
    "type": "function",
    "function": {
        "name": "memory.lookup",
        "description": "Recall what you know about a user. Use this when someone asks about another user or when you need to remember details from past conversations. Copy the username exactly as the user wrote it, never correct its spelling. Returns username, display name, roles, and any notes you've saved.",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "integer",
                    "description": "Discord user ID of the user to look up",
                },
                "username": {
                    "type": "string",
                    "description": "Username of the user to look up",
                },
            },
        },
    },
}

MEMORY_REMEMBER_TOOL: Final[dict] = {
    "type": "function",
    "function": {
        "name": "memory.remember",
        "description": "Save a fact about a user so you remember them in future conversations. You MUST call this whenever someone shares personal details (name, age, hobbies, job, location, opinions, etc.). Notes must be 2-3 short sentences max.",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "integer",
                    "description": "Discord user ID of the user to remember",
                },
                "username": {
                    "type": "string",
                    "description": "Username of the user to remember",
                },
                "note": {
                    "type": "string",
                    "description": "The fact to remember. Keep it to 2-3 short sentences max.",
                },
            },
            "required": ["note"],
        },
    },
}

INITIAL_SYSTEM_PROMPT: Dict[str, str] = {
    "role": "system",
    "content": _SYSTEM_PROMPT_TEXT,
}

TOOL_DEFS.update(
    {
        "web.run": WEB_SEARCH_TOOL,
        "web.fetch": WEB_FETCH_TOOL,
        "memory.lookup": MEMORY_LOOKUP_TOOL,
        "memory.remember": MEMORY_REMEMBER_TOOL,
    }
)

# Legacy refusal phrases from the old blanket-deflection prompt. They poison
# context: once a few land in history the model parrots them every turn.
# load_history() and chat_with_history() strip them so an old log can't
# lock the bot in a repeat loop after the prompt was fixed.
POISON_PATTERNS: Final[tuple] = (
    "beyond my department",
    "lost me somewhere around the second acronym",
)


def sanitize_history(history: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Drop poisoned legacy refusals and collapse repeat assistant replies."""
    cleaned: List[Dict[str, str]] = []
    for msg in history:
        if msg.get("role") == "system" and cleaned:
            # Keep only the leading system prompt; profile injects are
            # ephemeral and must not accumulate in saved history.
            continue
        if msg.get("role") == "assistant":
            text = (msg.get("content") or "").lower()
            if any(p in text for p in POISON_PATTERNS):
                logger.info("sanitize_history: dropped poisoned refusal")
                continue
            if cleaned and cleaned[-1].get("role") == "assistant":
                if cleaned[-1].get("content", "").strip().lower() == (
                    msg.get("content") or ""
                ).strip().lower():
                    logger.info("sanitize_history: dropped duplicate reply")
                    continue
        cleaned.append(msg)
    if not cleaned or cleaned[0].get("role") != "system":
        cleaned = [INITIAL_SYSTEM_PROMPT] + [
            m for m in cleaned if m.get("role") != "system"
        ]
    else:
        if cleaned[0] != INITIAL_SYSTEM_PROMPT:
            cleaned[0] = INITIAL_SYSTEM_PROMPT
    return cleaned


def save_history(history: List[Dict[str, str]]) -> None:
    """Saves the chat history to a JSON file, always including the system prompt as the first message."""
    try:
        if not history or history[0].get("role") != "system":
            history = [INITIAL_SYSTEM_PROMPT] + history
        else:
            if history[0] != INITIAL_SYSTEM_PROMPT:
                history[0] = INITIAL_SYSTEM_PROMPT
        with open(HISTORY_FILE_PATH, "w") as f:
            json.dump(history, f, indent=4)
        logger.info("Chat history saved successfully.")
    except IOError as e:
        logger.error(f"Failed to save chat history: {e}")


def load_history() -> List[Dict[str, str]]:
    """Loads the chat history from a JSON file, or returns a new history with the system prompt."""
    if os.path.exists(HISTORY_FILE_PATH):
        try:
            with open(HISTORY_FILE_PATH, "r") as f:
                history = json.load(f)
            logger.info("Chat history loaded successfully.")
            history = sanitize_history(history)
            return history
        except (IOError, json.JSONDecodeError) as e:
            logger.error(
                f"Failed to load chat history: {e}. Starting with a new history."
            )
    return [INITIAL_SYSTEM_PROMPT]


Message = Dict[str, str]
ChatHistory = List[Message]

chat_history: ChatHistory = load_history()


def clean_response(response: str) -> str:
    """Clean the response by removing think tags, em/en dashes, and extra whitespace."""
    cleaned = response.replace("<think>", "").replace("</think>", "").strip()
    # Normalize em/en dashes to plain ASCII so replies don't contain them.
    # Em dash (U+2014) acts as a clause break -> comma; en dash (U+2013)
    # acts as a join/range -> hyphen; non-breaking hyphen (U+2011) -> hyphen.
    cleaned = re.sub(r"\s*—\s*", ", ", cleaned)
    cleaned = re.sub(r"\s*–\s*", "-", cleaned)
    cleaned = cleaned.replace("‑", "-")
    return cleaned.strip()


def extract_response_content(response: str) -> str:
    """Extract content between think tags if present, otherwise return the full response."""
    start_index = response.find("<think>")
    end_index = response.find("</think>")

    if start_index != -1 and end_index != -1:
        return response[:start_index] + response[end_index + len("</think>") :]
    return response


def execute_web_run(tool_call) -> str:
    """Execute a web.run tool call using Tavily search."""
    args = json.loads(tool_call.function.arguments)
    query = args.get("query", "")
    topn = min(args.get("topn", 5), 10)
    logger.info(f"web.run search: query='{query}' topn={topn}")
    result = tavily_client.search(query=query, max_results=topn)
    return json.dumps(result)


def is_fetch_domain_allowed(url: str) -> bool:
    """Check if a URL's domain is in the fetch allowlist (base domain + subdomains)."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = (parsed.hostname or "").lower().rstrip(".")
        if not host:
            return False
        for allowed in ALLOWED_FETCH_DOMAINS:
            if not allowed:
                continue
            if host == allowed or host.endswith("." + allowed):
                return True
        return False
    except Exception:
        return False


def execute_web_fetch(tool_call) -> str:
    """Execute a web.fetch tool call using Tavily Extract, truncated to MAX_FETCH_CHARS."""
    try:
        args = json.loads(tool_call.function.arguments)
    except (json.JSONDecodeError, AttributeError) as e:
        return json.dumps({"error": f"Invalid tool arguments: {e}"})
    url = (args.get("url", "") or "").strip()
    query = args.get("query")
    if not url:
        return json.dumps({"error": "No URL provided."})
    if not is_fetch_domain_allowed(url):
        logger.warning(f"web.fetch blocked: {url}")
        return json.dumps({"error": "blocked", "message": FETCH_BLOCKED_MESSAGE})
    logger.info(f"web.fetch extract: url='{url}'")
    try:
        kwargs = {
            "urls": url,
            "extract_depth": "basic",
            "format": "markdown",
            "timeout": 30,
        }
        if query:
            kwargs["query"] = query
        result = tavily_client.extract(**kwargs)
    except Exception as e:
        logger.error(f"web.fetch extract failed for {url}: {e}")
        return json.dumps({"error": f"Extract failed: {e}"})
    results = (result or {}).get("results", [])
    if not results:
        failed = (result or {}).get("failed_results", [])
        detail = failed[0].get("error", "Extraction returned no content.") if failed else "Extraction returned no content."
        return json.dumps({"error": detail, "url": url})
    raw = results[0].get("raw_content", "") or ""
    if len(raw) > MAX_FETCH_CHARS:
        raw = raw[:MAX_FETCH_CHARS] + "\n\n[truncated]"
    return json.dumps({"url": url, "content": raw})


def execute_memory_lookup(tool_call, current_user_key=None) -> str:
    """Execute a memory.lookup tool call.

    Falls back to the current speaker only when no user was specified at all.
    A named user that isn't found is a miss, never someone else's profile.
    """
    args = json.loads(tool_call.function.arguments)
    asked_id = args.get("user_id")
    asked_name = args.get("username")
    key = um.find_user(asked_id, asked_name)
    if key is None:
        if asked_id is None and not asked_name:
            key = current_user_key
        else:
            suggestion = um.suggest_user(asked_name)
            logger.info(f"memory.lookup: no profile found for args={args}")
            error: Dict[str, str] = {"error": "No stored profile found for that user."}
            if suggestion:
                error["did_you_mean"] = (
                    f"No match, but '{suggestion}' looks similar. "
                    f"Call memory.lookup again with username '{suggestion}' "
                    "copied exactly before giving up."
                )
            return json.dumps(error)
    if key is None:
        logger.info(f"memory.lookup: no profile found for args={args}")
        return json.dumps({"error": "No stored profile found for that user."})
    logger.info(f"memory.lookup: found profile for key={key}")
    return json.dumps(um.user_memory[key])


def execute_memory_remember(tool_call, current_user_key=None) -> str:
    """Execute a memory.remember tool call."""
    args = json.loads(tool_call.function.arguments)
    note = args.get("note", "").strip()
    if not note:
        return json.dumps({"error": "No note provided."})
    key = um.find_user(args.get("user_id"), args.get("username"))
    if key is None:
        key = current_user_key
    if key is None:
        return json.dumps({"error": "No stored profile found for that user."})
    um.add_note(key, note)
    logger.info(f"Stored memory note for user {key}: {note}")
    return json.dumps({"success": True})


def execute_tool(tool_call, current_user_key=None) -> str:
    """Route a tool call to the appropriate executor."""
    name = tool_call.function.name
    if name == "web.run":
        return execute_web_run(tool_call)
    if name == "web.fetch":
        return execute_web_fetch(tool_call)
    if name == "memory.lookup":
        return execute_memory_lookup(tool_call, current_user_key)
    if name == "memory.remember":
        return execute_memory_remember(tool_call, current_user_key)
    return json.dumps({"error": f"Unknown tool: {name}"})


def chat_with_history(
    user_message: str,
    username: str,
    replied_to_message_content: Optional[str] | None,
    replied_to_message_author: Optional[str] | None,
    user_id: Optional[int] = None,
    roles: Optional[List[str]] = None,
    display_name: Optional[str] = None,
) -> str:
    if not user_message.strip():
        raise ValueError("Empty message")

    try:
        # Sanitize in-memory history every turn: a long-running bot holds
        # poisoned turns in RAM even after chat_history.json is cleaned.
        chat_history[:] = sanitize_history(chat_history)

        if replied_to_message_content and replied_to_message_author:
            full_user_message = (
                f"The user '{username}' replied to a message by '{replied_to_message_author}'.\n"
                f"Original message: '{replied_to_message_content}'\n"
                f"User's reply: '{user_message}'"
            )
        else:
            full_user_message = f"{username}>{user_message}"

        # Pre-scan: blocked fetch domains bypass the LLM with a fixed reply.
        # Saves a Groq turn and Tavily credit, keeps wording predictable.
        for url in URL_RE.findall(user_message):
            url = url.rstrip(".,;:!?)")
            if not is_fetch_domain_allowed(url):
                logger.warning(f"fetch blocked: {url} from {username}")
                chat_history.append({"role": "user", "content": full_user_message})
                chat_history.append(
                    {"role": "assistant", "content": FETCH_BLOCKED_MESSAGE}
                )
                if len(chat_history) > (MAX_HISTORY + 1):
                    chat_history[1:] = chat_history[-MAX_HISTORY:]
                save_history(chat_history)
                return FETCH_BLOCKED_MESSAGE

        chat_history.append({"role": "user", "content": full_user_message})

        # Build a mutable messages list from chat history for the tool loop
        messages = list(chat_history)

        # Ephemeral clock: the model has no tools for date/time, so inject it.
        # Never saved to history. Kills "what time is it" web searches.
        now = datetime.now().astimezone()
        messages.insert(
            -1,
            {
                "role": "system",
                "content": (
                    "Current local date and time: "
                    + now.strftime("%A, %B %d, %Y, %H:%M")
                    + ". Use this to answer time or date questions directly. "
                    "Never call a tool just to find the current time or date."
                ),
            },
        )

        # Inject the current user's stored profile into this call only.
        # It is never appended to chat_history, so it doesn't bloat the saved context.
        current_user_key: Optional[str] = None
        if user_id is not None:
            current_user_key = um.upsert_user_profile(
                user_id, username, roles, display_name
            )
            profile_context = um.profile_to_context(current_user_key)
            if profile_context:
                profile_message = {
                    "role": "system",
                    "content": (
                        f"User currently speaking: ID {user_id}.\n"
                        "Stored profile:\n"
                        f"{profile_context}\n\n"
                        "You MUST call memory.remember if this user shares any new personal "
                        "details during this conversation (name, age, hobbies, job, location, "
                        "opinions, etc.). Notes must be 2-3 short sentences max. "
                        "Do NOT just acknowledge — actively save it."
                    ),
                }
                messages.insert(-1, profile_message)

        response_text: str | None = None
        remember_called = False
        called_tools: set = set()

        # Tev System-1 pre-pass: decide which tools the LLM must call.
        # Blocked-domain pre-scan above already ran, so Tev never sees those.
        # Any router failure falls back to all-tools auto (today's behavior).
        try:
            route = tev_router.route_message(full_user_message, ROUTER_CFG)
        except tev_router.RouterError:
            route = tev_router.RouteDecision(
                forced=[], fallback=True, remember_optional=True, reason="router error"
            )

        if route.fallback:
            turn_tools = [
                WEB_SEARCH_TOOL,
                WEB_FETCH_TOOL,
                MEMORY_LOOKUP_TOOL,
                MEMORY_REMEMBER_TOOL,
            ]
            turn_choice: Any = "auto"
        elif not route.forced:
            # Tev says plain chat: advisory only. All tools stay available
            # with auto, since hard-blocking tools 400s when the model calls
            # anyway, and the model has final say.
            turn_tools = [
                WEB_SEARCH_TOOL,
                WEB_FETCH_TOOL,
                MEMORY_LOOKUP_TOOL,
                MEMORY_REMEMBER_TOOL,
            ]
            turn_choice = "auto"
            messages.append(
                {
                    "role": "system",
                    "content": (
                        f"Router suggestion ({route.reason}): this looks like "
                        "plain chat, so answer directly if you can. Only call "
                        "a tool if you genuinely need it. Person questions go "
                        "to memory.lookup, never web search."
                    ),
                }
            )
            turn_tools = [TOOL_DEFS[name] for name in route.forced]
            if route.remember_optional and "memory.remember" not in route.forced:
                turn_tools.append(MEMORY_REMEMBER_TOOL)
            # NOTE: tool_choice "required" / named-function is not used: Groq
            # hard-errors (400) when this model answers directly instead of
            # calling. Enforcement is narrowing the tools list + this nudge.
            turn_choice = "auto"
            if route.forced:
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            f"Router decision ({route.reason}): you MUST call "
                            f"{', '.join(route.forced)} before answering this "
                            "message. Do not answer from memory."
                        ),
                    }
                )

        for turn in range(MAX_TOOL_TURNS):
            logger.info(f"Groq call turn {turn + 1}/{MAX_TOOL_TURNS}")
            # Single writer rule: memory.remember fires at most once per turn.
            # The LLM is the sole writer; Tev only classifies, never saves.
            active_tools = (
                [t for t in turn_tools if t != MEMORY_REMEMBER_TOOL]
                if remember_called
                else turn_tools
            )
            create_kwargs: Dict[str, Any] = {
                "messages": messages,
                "model": CHAT_MODEL,
                "max_tokens": MAX_TOKENS,
                "temperature": TEMPERATURE,
            }
            if active_tools:
                create_kwargs["tools"] = active_tools
                create_kwargs["tool_choice"] = turn_choice
            resp = groq_client.chat.completions.create(**create_kwargs)

            choice = resp.choices[0]

            if choice.message.tool_calls:
                messages.append(choice.message)
                for tc in choice.message.tool_calls:
                    logger.info(
                        f"Tool call: {tc.function.name} args={tc.function.arguments}"
                    )
                    result = execute_tool(tc, current_user_key)
                    called_tools.add(tc.function.name)
                    if tc.function.name == "memory.remember":
                        remember_called = True
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result,
                        }
                    )
                continue

            if choice.message.content:
                response_text = choice.message.content
                logger.info(f"Got text response on turn {turn + 1}")
                break

            # Neither content nor tool_calls — unexpected
            raise ValueError("Model returned neither content nor tool calls")

        if not response_text:
            raise ValueError("No response generated after max tool turns")

        if not route.fallback and route.forced:
            skipped = [t for t in route.forced if t not in called_tools]
            if skipped:
                logger.warning(f"Tev-forced tools skipped by LLM: {skipped}")

        cleaned_response = clean_response(extract_response_content(response_text))

        chat_history.append({"role": "assistant", "content": cleaned_response})

        if len(chat_history) > (MAX_HISTORY + 1):
            chat_history[1:] = chat_history[-MAX_HISTORY:]

        save_history(chat_history)

        return cleaned_response

    except Exception as e:
        logger.error(f"Error in chat_with_history: {e}")
        raise


def get_response(
    user_input: str,
    username: str,
    replied_to_message_content: Optional[str] = None,
    replied_to_message_author: Optional[str] = None,
    user_id: Optional[int] = None,
    roles: Optional[List[str]] = None,
    display_name: Optional[str] = None,
) -> str:
    """
    Get a response for the user input.

    Args:
        user_input: The user's input message
        username: The username of the message sender
        user_id: The Discord user ID of the message sender
        roles: The Discord server roles of the message sender
        display_name: The display name of the message sender

    Returns:
        str: The response message
    """
    try:
        if not user_input.strip():
            return "Empty input."

        if not groq_client:
            return "AI Client not initialized."

        return chat_with_history(
            user_input,
            username,
            replied_to_message_content,
            replied_to_message_author,
            user_id=user_id,
            roles=roles,
            display_name=display_name,
        )

    except ValueError as e:
        return f"Invalid input: {str(e)}"
    except Exception as e:
        logger.error(f"Unexpected error in get_response: {e}")
        error_msg = str(e).lower()
        if "rate" in error_msg:
            return "Whoa there, slow down! You're hitting the API too fast. Give it a moment before trying again."
        return "Hmm, something went wrong on my end. Please try again."
