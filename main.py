from typing import Final, Optional
import os
import logging
from dotenv import load_dotenv
from discord import Intents, Client, Message, TextChannel
from responses import get_response
import discord
import sys

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
CHANNEL_ID: Final[int] = 1353714545230741538

intents: Intents = Intents.default()
intents.message_content = True
client: Client = Client(intents=intents)

if not TOKEN:
    logger.error("Discord token not found in environment variables")
    sys.exit(1)

intents: Intents = Intents.default()
intents.message_content = True
client: Client = Client(intents=intents)

async def send_message(message: Message, user_message: str, username: str) -> None:
    if not user_message:
        logger.warning(f"Empty message received from {username}")
        return

    is_private: bool = user_message.startswith("?")
    user_message = user_message[1:] if is_private else user_message

    try:
        response: str = get_response(user_message, username)
        if is_private:
            await message.author.send(response)
            logger.info(f"Sent private response to {username}")
        else:
            await message.channel.send(response)
            logger.info(f"Sent public response in {message.channel}")
    except discord.errors.Forbidden as e:
        logger.error(f"Permission error when sending message: {e}")
        await message.channel.send("I don't have permission to perform this action.")
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        await message.channel.send("An error occurred while processing your message.")


@client.event
async def on_ready() -> None:
    logger.info(f"Bot {client.user} is now running!")

    channel: Optional[TextChannel] = client.get_channel(CHANNEL_ID)
    if channel:
        try:
            await channel.send("Hello guys!")
            logger.info(f"Sent startup message in channel {CHANNEL_ID}")
        except Exception as e:
            logger.error(f"Failed to send startup message: {e}")
    else:
        logger.error(f"Could not find channel with ID {CHANNEL_ID}")

@client.event
async def on_message(message: Message) -> None:
    if message.channel.id != CHANNEL_ID:
        return

    if message.author == client.user:
        return
        
    username: str = str(message.author)
    user_message: str = message.content
    channel: str = str(message.channel)

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
