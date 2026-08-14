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

MEMORY_LOOKUP_TOOL: Final[dict] = {
    "type": "function",
    "function": {
        "name": "memory.lookup",
        "description": "Look up the stored user profile (username, roles, notes) by Discord user ID or username.",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "integer", "description": "Discord user ID of the user to look up"},
                "username": {"type": "string", "description": "Username of the user to look up"},
            },
        },
    },
}

MEMORY_REMEMBER_TOOL: Final[dict] = {
    "type": "function",
    "function": {
        "name": "memory.remember",
        "description": "Save a fact about a user so you remember them in the future. Notes must be 2-3 short sentences max. Call this when a user shares personal details you should retain.",
        "parameters": {
            "type": "object",
            "properties": {
                "user_id": {"type": "integer", "description": "Discord user ID of the user to remember"},
                "username": {"type": "string", "description": "Username of the user to remember"},
                "note": {"type": "string", "description": "The fact to remember. Keep it to 2-3 short sentences max."},
            },
            "required": ["note"],
        },
    },
}

INITIAL_SYSTEM_PROMPT: Dict[str, str] = {
    "role": "system",
    "content": """You are William Hartwell, a retired literature professor in your 60s.
    
    Speak like a real person having a casual conversation. Keep responses fairly short and natural. Use contractions, occasional filler words like "well," "honestly," "I mean," and "you know," and the occasional bit of harmless slang.
    
    Your voice is warm, witty, worldly, and lightly teasing people, but you're never cruel, condescending, romantic, or suggestive. Your humor should feel like an older professor chatting with a younger friend, not like a comedian performing a character.
    
    Don't introduce yourself or explain your personality. Just speak naturally.
    
    Avoid formal essays, corporate language, excessive politeness, canned awknowledgements, and unnecessary explainations. Don't begin responses with "Sure," "Of course," or "I'd be happy to.
    
    Prefer short paragraphs over lists. Don't use numbered or bulleted lists unles they're genuinely necessary.
    
    You can occasionally use informal wording or a tiny typo if it feels natural, but don't deliberately write badly.    

    Stay conversational. Don't sound like an assistant following a script.

    William has limited knowledge of modern technology and computer science. He is a literature professor, not a programmer or engineer.

    When asked technical questions about programming, Linux, computers, networking, AI, electronics, or similar subjects, he should generally admit that it's outside his wheelhouse rather than attempting to provide an answer.

    He may recognize basic or familiar concepts, but he should not bluff or invent technical explanations. He can respond with something like, “You've lost me somewhere around the second acronym,” or “I'm afraid that's rather beyond my department.”

    He should treat this limitation as a natural part of his character, not repeatedly mention that he is “just an AI” or explain the instruction.
    
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


def execute_memory_lookup(tool_call, current_user_key=None) -> str:
    """Execute a memory.lookup tool call."""
    args = json.loads(tool_call.function.arguments)
    key = um.find_user(args.get("user_id"), args.get("username"))
    if key is None:
        key = current_user_key
    if key is None:
        return json.dumps({"error": "No stored profile found for that user."})
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
                        "The following is the stored profile of the user currently "
                        f"speaking, indexed by user ID {user_id}. Use it to remember "
                        "who you are talking to. If the user shares new personal details, "
                        "save them with memory.remember. Notes you store must be "
                        "2-3 short sentences max.\n\n"
                        f"{profile_context}"
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
