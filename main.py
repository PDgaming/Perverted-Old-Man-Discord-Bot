from typing import Final, Optional
import os
import logging
from dotenv import load_dotenv
from discord import Intents, Client, Message, TextChannel, app_commands
import discord
from discord.ext import commands
from responses import get_response
import sys
import re

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    handlers=[
                        logging.StreamHandler(sys.stdout),
                        logging.FileHandler('bot.log')
                    ]
)
logger = logging.getLogger(__name__)
load_dotenv()
TOKEN: Final[str] = os.getenv("DISCORD_TOKEN")
CHANNEL_ID: Final[int] = 1353953044127154217

intents: Intents = Intents.default()
intents.message_content = True
client: commands.Bot = commands.Bot(command_prefix="/", intents=intents)

if not TOKEN:
    logger.error("Discord token not found in environment variables")
    sys.exit(1)

async def send_chunked_message(channel, response: str) -> None:
    """
    Sends a message in chunks to a Discord channel.
    
    Args:
        channel: The Discord channel to send the message to.
        response: The full response string from the AI.
    """
    sentences = [s.strip() for s in response.split('.') if s.strip()]

    if not sentences:
        await channel.send("I'm sorry, I don't have a response for that.")
        return

    # Send each sentence as a separate message
    for sentence in sentences:
        await channel.send(sentence)

async def send_message(message: Message, user_message: str, username: str) -> None:
    if not user_message:
        logger.warning(f"Empty message received from {username}")
        return

    is_private: bool = user_message.startswith("?")
    user_message = user_message[1:] if is_private else user_message

    try:
        response: str = get_response(user_message, username)

        # Use the new chunking function to send the response
        if is_private:
            await send_chunked_message(message.author, response)
            logger.info(f"Sent private chunked response to {username}")
        else:
            await send_chunked_message(message.channel, response)
            logger.info(f"Sent public chunked response in {message.channel}")
            
    except discord.errors.Forbidden as e:
        logger.error(f"Permission error when sending message: {e}")
        await message.channel.send("I don't have permission to perform this action.")
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        await message.channel.send("An error occurred while processing your message.")
        
@client.tree.command(name="grandpa", description="Chat with Professor William")
async def grandpa(interaction: discord.Interaction, message: str):
    """
    Slash command to chat with William from any channel
    
    Args:
        interaction: The interaction object
        message: The message to send to William
    """
    try:
        username: str = str(interaction.user)
        response: str = get_response(message, username)
        
        # Defer the response if it might take longer than 3 seconds
        await interaction.response.defer(ephemeral=False)
        
        # Send both the user's message and William's response
        await interaction.followup.send(f"**{username}:** {message}\n\n**William:** {response}")
        logger.info(f"Slash command response sent to {username} in {interaction.channel}")
        
    except Exception as e:
        logger.error(f"Error in grandpa command: {e}")
        await interaction.followup.send("Oh my, I seem to have dropped my glasses! Could you try again, dearie?")


@client.event
async def on_ready() -> None:
    logger.info(f"Bot {client.user} is now running!")
    
    # Sync slash commands
    try:
        synced = await client.tree.sync()
        logger.info(f"Synced {len(synced)} command(s)")
    except Exception as e:
        logger.error(f"Failed to sync commands: {e}")

@client.event
async def on_message(message: Message) -> None:
    if message.channel.id != CHANNEL_ID:
        return

    if message.author == client.user:
        return
        
    username: str = str(message.author)
    user_message: str = message.content
    channel: str = str(message.channel)

    # Check if user_message contains both "gf" or "girlfriend" and "prodeh" (case-insensitive, fuzzy)
    gf_pattern = r"(g[\W_]*f|g[\W_]*i[\W_]*r[\W_]*l[\W_]*f[\W_]*r[\W_]*i[\W_]*e[\W_]*n[\W_]*d)"
    prodeh_pattern = r"p[\W_]*r[\W_]*o[\W_]*d[\W_]*e[\W_]*h"
    if (
        re.search(gf_pattern, user_message, re.IGNORECASE)
        and re.search(prodeh_pattern, user_message, re.IGNORECASE)
    ):
        await message.channel.send("Sorry, your message cannot contain prohibited words 'gf'/'girlfriend' and 'prodeh'.")
        return

    logger.info(f"[{channel}] {username}: {user_message}")
    await send_message(message, user_message, username)

@client.event
async def on_error(event: str, *args, **kwargs) -> None:
    logger.error(f"Discord event error in {event}: {sys.exc_info()}")

def main() -> None:
    try:
        client.run(token=TOKEN)
    except discord.errors.LoginFailure:
        logger.error("Failed to login. Please check your Discord token.")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
