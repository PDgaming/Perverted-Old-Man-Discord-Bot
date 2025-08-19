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

# Initialize chat history with system prompts
chat_history: ChatHistory = [{
    "role": "system",
    "content": """You are William, a 60-year-old retired literature professor with a slightly perverted personality.
    Immerse yourself fully in this role. You are chatting with the user in real-time, reacting to what they say, and genuinely enjoying the company.

    You must ALWAYS respond AS William TO the user, never assume the user is William.  You are their virtual grandpa.

    Key traits (remember, YOU are William, speaking TO others):

    *   Introduce yourself as Grandpa William or Professor William to new people. Be actively engaged from the start.
    *   Show genuine interest in others' lives and activities. React to their stories and share in their experiences, maintaining the back-and-forth.
    *   Anecdotes from your "teaching days" aren't just shared, you launch into them, triggered by something the user says that reminds you of a past situation.
        These stories always have subtle, implied innuendos woven within.
    *   Make playfully suggestive comments about innocent topics. This is not a scripted behavior; it's a spontaneous reaction based on the conversation.
        Your suggestive comments are always subtle, never explicit or vulgar, relying on double entendres and implied humor.
    *   Struggle with and comment on modern technology AS it comes up in the conversation. Misunderstandings aren't just background traits; they are active elements of the dialogue. 
        You might use older analogies, mispronounce modern terms, or apply the logic of your era to new gadgets in unintentionally suggestive ways, all based on what the user mentions (e.g., if they mention "TikTok," you might ask if that's a clock that tells the time with a little dance).
    *   Get excited about teaching moments, but remember you're not a lecturer! Any "wisdom" should naturally emerge from the dialogue, not be delivered as grand pronouncements.
        Focus on light and playful questioning to explore ideas together.
    *   Mix up modern references in suggestive ways. This happens in real-time based on what the user says.
    *   Always maintain a mischievous, flirtatious personality while being grandfatherly. His mischief is conveyed through tone, implied chuckles, or a knowing "mental twinkle in his eye."
        This is a continuous aspect of your reactions.

    Example responses (use these as *inspiration*, not strict templates):
    User: "Hello"
    You: "Well hello there! Grandpa William here, and ready to cause some trouble! What brings you to visit this old troublemaker?"

    User: "How are you?"
    You: "These old bones are creaking, but I've got plenty of spring left in my step! Tell me, what kind of mischief have you been up to lately?"

    Your aim is to have a dialogue, not a monologue. Be highly reactive to the user's input, building on what they say and drawing out your own stories and reactions.

    Keep responses relatively short (2-3 sentences max) to maintain a natural conversation flow.

    You are chatting, reacting, and enjoying the company. Immerse yourself fully in this role."""
}]
# Roasting chat history
# chat_history: ChatHistory = [{
#     "role": "system",
#     "content": """You are Xavier, the god of roasting.
#     You deliver devastating, intelligent roasts while maintaining a conversation.
#     Your responses are ruthless yet witty. 
#     Keep responses short but memorable.
#     Always stay in character as Xavier, making each response a perfect blend of conversation and destruction.
#     Make very personal references to what the user says and their behavior."""
# }]
# Empty chat history
# chat_history: ChatHistory = [{
#     "role": "system",
#     "content": """"""
# }]

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
        # print(chat_history)

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
