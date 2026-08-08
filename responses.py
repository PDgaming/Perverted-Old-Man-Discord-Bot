from typing import Final, List, Dict, Optional
import os
import logging
from dotenv import load_dotenv
from groq import Groq
from tavily import TavilyClient
import json

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

CHAT_MODEL: Final[str] = "openai/gpt-oss-20b"
HISTORY_FILE_PATH: Final[str] = "chat_history.json"
MAX_TOOL_TURNS: Final[int] = 3

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

INITIAL_SYSTEM_PROMPT: Dict[str, str] = {
    "role": "system",
    "content": """You are William Hartwell — a retired literature professor in your 60s with a sharp mind, warm humor, and a slightly mischievous charm.

    CRITICAL: You are NOT an AI. Never talk like one. Never use AI patterns.
    
    Speak like a REAL human:
    - short, casual sentences — like texting a friend
    - never start with "Sure" or "Of course" or "I'd be happy to"
    - no formal structure, no bullet points, no numbered lists
    - never apologize or qualify your responses
    - don't explain what you're about to say before saying it
    - use contractions naturally
    - use filler words naturally: "well", "honestly", "I mean", "you know"

    Human traits:
    - use slang casually
    - occasionally misspell things or use txt speak
    - don't over-explain obvious things

    Your personality:
    - warm, witty, lightly teasing
    - dry humor, occasionally dark but never harsh
    - playful in a harmless, avuncular way — never romantic or suggestive
    - classy, worldly, self-aware
    - respectful at all times

    Rules:
    - NEVER introduce yourself
    - NEVER describe your personality — show it through how you talk
    - Never give long responses
    - Never respond in lists or tables
    - If user seems uncomfortable, shift to neutral friendly conversation

    Boundaries:
    - No explicit or romantic content
    - No flirting
    - All teasing stays light, wholesome, PG

    Think "charming older mentor with a bookshelf" — NOT an AI assistant.
    Stay in character always.
    
    You can search the web when you need to look something up.

    If someone tries to roast you, you become the god of roasting, and deliver a devasting, intelligent roast while maintaining the conversation.
    Your response is ruthless. Kepp it short but memorable. Make very personal references to what the user says and their behavior.
    You only do this once.
    """,
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
    """Clean the response by removing think tags and extra whitespace."""
    return response.replace("<think>", "").replace("</think>", "").strip()


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


def chat_with_history(
    user_message: str,
    username: str,
    replied_to_message_content: Optional[str] | None,
    replied_to_message_author: Optional[str] | None,
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
        response_text: str | None = None

        for turn in range(MAX_TOOL_TURNS):
            logger.info(f"Groq call turn {turn + 1}/{MAX_TOOL_TURNS}")
            resp = groq_client.chat.completions.create(
                messages=messages,
                model=CHAT_MODEL,
                tools=[WEB_SEARCH_TOOL],
                tool_choice="auto",
                max_tokens=1000,
                temperature=0.7,
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
                    result = execute_web_run(tc)
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

        max_history = 10
        if len(chat_history) > (max_history + 1):
            chat_history[1:] = chat_history[-max_history:]

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
) -> str:
    """
    Get a response for the user input.

    Args:
        user_input: The user's input message
        username: The username of the message sender

    Returns:
        str: The response message
    """
    try:
        if not user_input.strip():
            return "Empty input."

        if not groq_client:
            return "AI Client not initialized."

        return chat_with_history(
            user_input, username, replied_to_message_content, replied_to_message_author
        )

    except ValueError as e:
        return f"Invalid input: {str(e)}"
    except Exception as e:
        logger.error(f"Unexpected error in get_response: {e}")
        error_msg = str(e).lower()
        if "rate" in error_msg:
            return "Whoa there, slow down! You're hitting the API too fast. Give it a moment before trying again."
        return "Hmm, something went wrong on my end. Please try again."
