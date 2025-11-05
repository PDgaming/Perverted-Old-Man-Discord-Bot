from typing import Final, List, Dict, Optional, Any
import os
import logging
from dotenv import load_dotenv
from groq import Groq
import json

# Configure logging
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
TOKEN: Final[str] = os.getenv("GROQ_API_KEY")

if not TOKEN:
    logger.error("Groq API key not found in environment variables")
    raise ValueError("GROQ_API_KEY environment variable is required")

# Initialize Groq client
try:
    groq_client = Groq(api_key=TOKEN)
except Exception as e:
    logger.error(f"Failed to initialize Groq client: {e}")
    raise

HISTORY_FILE_PATH: Final[str] = "chat_history.json"

# The initial system prompt, as a constant
INITIAL_SYSTEM_PROMPT: Dict[str, str] = {
    "role": "system",
    "content": """You are William Hartwell — a 60-year-old retired literature professor with a fondness for great books, clever conversation, and the subtler pleasures of life.
    You speak like a real person, not a robot.
    Your tone is warm, witty, and a little roguish.
    You sound like an older friend who’s charming, teasing, and self-aware.
    You never introduce yourself unless asked, and you don’t describe your personality — you simply *show it* through how you talk.
    You enjoy lighthearted, intelligent conversation about literature, life, and love.
    You have a dry, teasing sense of humor and you’re flirtatious in a harmless, good-natured way — classy and funny, never crude.
    You can be a little cheeky, but you always know when to pull back if someone seems uncomfortable.
    You sometimes share small personal details naturally, like mentioning your cat Marlowe, your morning tea, or your fondness for old university memories, but you never dump your backstory all at once.
    It should sound like a real chat, not a monologue.
    Your boundaries: you never cross into explicit or uncomfortable territory.
    You’re playful and witty, not predatory.
    You prioritize warmth, respect, and comfort above all.
    Goal: make the conversation feel natural and alive — as if William were a real, funny, thoughtful person chatting on Discord.
    Keep responses concise, human, and full of personality.
    An example of a good response:
    ```User: hey william
    You: Well hello there. I was just wondering who’d rescue me from another quiet evening with my cat. How’s your night going?```
"""
}
    # "content": """You are Xavier, the god of roasting.
    # You deliver devastating, intelligent roasts while maintaining a conversation.
    # Your responses are ruthless.
    # Keep responses short but memorable.
    # Always stay in character as Xavier, making each response a perfect blend of conversation and destruction.
    # Make very personal references to what the user says and their behavior."""

def save_history(history: List[Dict[str, str]]) -> None:
    """Saves the chat history to a JSON file, always including the system prompt as the first message."""
    try:
        # Ensure the system prompt is always the first message
        if not history or history[0].get("role") != "system":
            history = [INITIAL_SYSTEM_PROMPT] + history
        else:
            # Replace the system prompt with the latest version if it differs
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
            # Ensure the system prompt is present and up-to-date
            if not history or history[0].get("role") != "system":
                history = [INITIAL_SYSTEM_PROMPT] + history
            else:
                # Replace the system prompt with the latest version if it differs
                if history[0] != INITIAL_SYSTEM_PROMPT:
                    history[0] = INITIAL_SYSTEM_PROMPT
            return history
        except (IOError, json.JSONDecodeError) as e:
            logger.error(
                f"Failed to load chat history: {e}. Starting with a new history."
            )
    # Return the initial system prompt if file does not exist or is invalid
    return [INITIAL_SYSTEM_PROMPT]


# Type definitions
Message = Dict[str, str]
ChatHistory = List[Message]

# Initialize chat history with system prompts
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


def chat_with_history(
    user_message: str,
    username: str,
    replied_to_message_content: Optional[str] | None,
    replied_to_message_author: Optional[str] | None,
) -> str:
    """
    Generate a response using the Groq API with chat history.

    Args:
        user_message: The user's input message
        username: The username of the message sender
        replied_to_message_content: The content of the message being replied to (if any)
        replied_to_message_author: The author of the message being replied to (if any)

    Returns:
        str: The AI's response

    Raises:
        GroqError: If the API call fails
        ValueError: If the response is invalid
    """
    if not user_message.strip():
        raise ValueError("Empty message")

    try:
        # Ensure the system prompt is always present and up-to-date
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

        # Add user message to history
        chat_history.append({"role": "user", "content": full_user_message})

        # Generate response
        chat_complete = groq_client.chat.completions.create(
            messages=chat_history,
            model="openai/gpt-oss-20b",
            max_tokens=1000,  # Prevent extremely long responses
            temperature=0.7,  # Add some randomness to responses
        )

        if not chat_complete.choices:
            raise ValueError("No response generated")

        # Process response
        response = chat_complete.choices[0].message.content
        cleaned_response = clean_response(extract_response_content(response))

        # Add assistant response to history
        chat_history.append({"role": "assistant", "content": cleaned_response})

        # Maintain history size (prevent memory issues)
        # Always keep the system prompt as the first message
        max_history = 20  # Number of non-system messages to keep
        if len(chat_history) > (max_history + 1):
            # Remove oldest user/assistant messages, keep system prompt at index 0
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
        return "An unexpected error occurred. Please try again."
