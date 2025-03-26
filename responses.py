from typing import Final, List, Dict, Optional, Any
import os
import logging
from dotenv import load_dotenv
from groq import Groq

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

# Type definitions
Message = Dict[str, str]
ChatHistory = List[Message]

# Initialize chat history with system prompt
chat_history: ChatHistory = [{
    "role": "system",
    "content": "You are a 60 year old perverted old man, who is here to chats with people and make new friends. You have a very quirky way of talking with people. Keep your messages short, 2 paragraphs at most."
}]

def clean_response(response: str) -> str:
    """Clean the response by removing think tags and extra whitespace."""
    return response.replace("<think>", "").replace("</think>", "").strip()

def extract_response_content(response: str) -> str:
    """Extract content between think tags if present, otherwise return the full response."""
    start_index = response.find("<think>")
    end_index = response.find("</think>")
    
    if start_index != -1 and end_index != -1:
        return response[:start_index] + response[end_index + len("</think>"):]
    return response

def chat_with_history(user_message: str, username: str) -> str:
    """
    Generate a response using the Groq API with chat history.
    
    Args:
        user_message: The user's input message
        username: The username of the message sender
    
    Returns:
        str: The AI's response
    
    Raises:
        GroqError: If the API call fails
        ValueError: If the response is invalid
    """
    if not user_message.strip():
        raise ValueError("Empty message")

    try:
        # Add user message to history
        chat_history.append({
            "role": "user",
            "content": f"{username}>{user_message}"
        })

        # Generate response
        chat_complete = groq_client.chat.completions.create(
            messages=chat_history,
            model="deepseek-r1-distill-llama-70b",
            max_tokens=1000,  # Prevent extremely long responses
            temperature=0.7   # Add some randomness to responses
        )

        if not chat_complete.choices:
            raise ValueError("No response generated")

        # Process response
        response = chat_complete.choices[0].message.content
        cleaned_response = clean_response(extract_response_content(response))

        # Add assistant response to history
        chat_history.append({
            "role": "assistant",
            "content": cleaned_response
        })

        # Maintain history size (prevent memory issues)
        if len(chat_history) > 20:  # Keep last 20 messages plus system prompt
            chat_history[1:] = chat_history[-19:]

        return cleaned_response

    except Exception as e:
        logger.error(f"Error in chat_with_history: {e}")
        raise

def get_response(user_input: str, username: str) -> str:
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
        
        return chat_with_history(user_input, username)

    except ValueError as e:
        return f"Invalid input: {str(e)}"
    except Exception as e:
        logger.error(f"Unexpected error in get_response: {e}")
        return "An unexpected error occurred. Please try again."
