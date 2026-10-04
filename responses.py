from typing import Final, List, Dict, Optional
import os
import logging
from dotenv import load_dotenv
from groq import Groq
from tavily import TavilyClient
import json
import user_memory as um

# Configure logging
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

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

CONFIG_FILE_PATH: Final[str] = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "config.json"
)


def load_config(path: str = CONFIG_FILE_PATH) -> dict:
    """Load LLM config (model settings + system prompt) from a JSON file."""
    try:
        with open(path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error(f"Config file not found: {path}")
        raise
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse config file {path}: {e}")
        raise


_config = load_config()
CHAT_MODEL: Final[str] = _config.get("chat_model", "openai/gpt-oss-20b")
HISTORY_FILE_PATH: Final[str] = _config.get("history_file_path", "chat_history.json")
MAX_TOOL_TURNS: Final[int] = _config.get("max_tool_turns", 3)
MAX_TOKENS: Final[int] = _config.get("max_tokens", 1000)
TEMPERATURE: Final[float] = _config.get("temperature", 0.7)
MAX_HISTORY: Final[int] = _config.get("max_history", 10)

_SYSTEM_PROMPT_TEXT: Final[str] = _config["system_prompt"]

# web.run tool definition for Groq's built-in web search
WEB_SEARCH_TOOL: Final[dict] = {
    "type": "function",
    "function": {
        "name": "web.run",
        "description": "Search the web for current information",
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

MEMORY_LOOKUP_TOOL: Final[dict] = {
    "type": "function",
    "function": {
        "name": "memory.lookup",
        "description": "Recall what you know about a user. Use this when someone asks about another user or when you need to remember details from past conversations. Returns username, display name, roles, and any notes you've saved.",
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
            if not history or history[0].get("role") != "system":
                history = [INITIAL_SYSTEM_PROMPT] + history
            else:
                if history[0] != INITIAL_SYSTEM_PROMPT:
                    history[0] = INITIAL_SYSTEM_PROMPT
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
    import re

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


def execute_memory_lookup(tool_call, current_user_key=None) -> str:
    """Execute a memory.lookup tool call."""
    args = json.loads(tool_call.function.arguments)
    key = um.find_user(args.get("user_id"), args.get("username"))
    if key is None:
        key = current_user_key
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
        if not chat_history or chat_history[0].get("role") != "system":
            chat_history.insert(0, INITIAL_SYSTEM_PROMPT)
        elif chat_history[0] != INITIAL_SYSTEM_PROMPT:
            chat_history[0] = INITIAL_SYSTEM_PROMPT

        if replied_to_message_content and replied_to_message_author:
            full_user_message = (
                f"The user '{username}' replied to a message by '{replied_to_message_author}'.\n"
                f"Original message: '{replied_to_message_content}'\n"
                f"User's reply: '{user_message}'"
            )
        else:
            full_user_message = f"{username}>{user_message}"

        chat_history.append({"role": "user", "content": full_user_message})

        # Build a mutable messages list from chat history for the tool loop
        messages = list(chat_history)

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

        for turn in range(MAX_TOOL_TURNS):
            logger.info(f"Groq call turn {turn + 1}/{MAX_TOOL_TURNS}")
            resp = groq_client.chat.completions.create(
                messages=messages,
                model=CHAT_MODEL,
                tools=[WEB_SEARCH_TOOL, MEMORY_LOOKUP_TOOL, MEMORY_REMEMBER_TOOL],
                tool_choice="auto",
                max_tokens=MAX_TOKENS,
                temperature=TEMPERATURE,
            )

            choice = resp.choices[0]

            if choice.message.content:
                response_text = choice.message.content
                logger.info(f"Got text response on turn {turn + 1}")
                break

            if choice.message.tool_calls:
                for tc in choice.message.tool_calls:
                    logger.info(
                        f"Tool call: {tc.function.name} args={tc.function.arguments}"
                    )
                    result = execute_tool(tc, current_user_key)
                    messages.append(choice.message)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result,
                        }
                    )
                continue

            # Neither content nor tool_calls — unexpected
            raise ValueError("Model returned neither content nor tool calls")

        if not response_text:
            raise ValueError("No response generated after max tool turns")

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
