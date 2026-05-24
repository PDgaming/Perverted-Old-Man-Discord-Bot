from typing import Final
import os
import logging
from dotenv import load_dotenv
from discord import Intents, Client, Message, NotFound, TextChannel, app_commands, TextStyle
import discord
from discord.ext import commands
from discord.ui import Modal, TextInput
from responses import get_response
import sys
import re
import subprocess
import asyncio
import shlex
import psutil
import prctl

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("bot.log")],
)
logger = logging.getLogger(__name__)
load_dotenv()
TOKEN: Final[str] = os.getenv("DISCORD_TOKEN")
PervertedOldMan_Channel: Final[int] = 1416359469654212658
MinecraftServer_Channel: Final[int] = 1340436119673770075

intents: Intents = Intents.default()
intents.message_content = True
client: commands.Bot = commands.Bot(command_prefix="/", intents=intents)

if not TOKEN:
    logger.error("Discord token not found in environment variables")
    sys.exit(1)


async def send_chunked_message(channel, response: str) -> None:
    """
    Sends a message to a Discord channel, splitting into multiple messages
    only if it exceeds Discord's 2000-character limit.

    Args:
        channel: The Discord channel to send the message to.
        response: The full response string from the AI.
    """
    if not response.strip():
        await channel.send("I'm sorry, I don't have a response for that.")
        return

    max_length = 2000
    if len(response) <= max_length:
        await channel.send(response)
        return

    for i in range(0, len(response), max_length):
        await channel.send(response[i : i + max_length])


async def send_message(
    message: Message,
    user_message: str,
    replied_to_message_content: str | None,
    replied_to_message_author: str | None,
    username: str,
) -> None:
    if not user_message:
        logger.warning(f"Empty message received from {username}")
        return

    is_private: bool = user_message.startswith("?")
    user_message = user_message[1:] if is_private else user_message

    try:
        response: str = get_response(
            user_message,
            username,
            replied_to_message_content,
            replied_to_message_author,
        )

        logger.info(f"send_message: sending response (len={len(response)}): '{response[:100]}...'")

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


async def start_minecraft_server():
    """
    Starts the Minecraft server using the start.sh script with multiple fallback options

    Returns:
        dict: Status information including success, method used, and any errors
    """
    script_path = "/mnt/Software/UnitedBlocks/UnitedBlocks.sh"

    try:
        if not os.path.exists(script_path):
            logger.error(f"Script {script_path} not found.")
            return {"success": False, "method": "none", "error": "Script not found."}

        if is_minecraft_server_running():
            return {
                "success": True,
                "method": "already_running",
                "message": "Minecraft server is already running.",
            }

        try:
            result = await start_with_terminal(script_path)
            if result["success"]:
                return {
                    "success": True,
                    "method": "start_with_terminal",
                    "message": "Starting Minecraft server...",
                }
            else:
                return {
                    "success": False,
                    "method": "start_with_terminal",
                    "error": result["error"],
                }
        except Exception as e:
            logger.error(f"Error starting Minecraft server: {e}")
            return {
                "success": False,
                "method": "start_with_terminal",
                "error": f"Error starting Minecraft server: {e}",
            }

    except Exception as e:
        logger.error(f"Error starting Minecraft server: {e}")
        return {
            "success": False,
            "method": "none",
            "error": f"Error starting Minecraft server: {e}",
        }


async def start_with_terminal(script_path):
    """
    Starts the Minecraft server in a new terminal window
    """
    try:
        # Use gnome-terminal with proper command structure
        # Start playit in background, then run start.sh in foreground
        process = await asyncio.create_subprocess_exec(
            "tmux",
            "new-session",
            "-d",
            "-s",
            "unitedblocks",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await process.wait()

        # Split the window horizontally (creates right pane)
        process = await asyncio.create_subprocess_exec(
            "tmux",
            "split-window",
            "-h",
            "-t",
            "unitedblocks",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await process.wait()

        # Run start.sh in the left pane (pane 0)
        process = await asyncio.create_subprocess_exec(
            "tmux",
            "send-keys",
            "-t",
            "unitedblocks:0.0",
            "cd /mnt/Software/UnitedBlocks && ./UnitedBlocks.sh",
            "C-m",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await process.wait()

        # Run playit in the right pane (pane 1)
        process = await asyncio.create_subprocess_exec(
            "tmux",
            "send-keys",
            "-t",
            "unitedblocks:0.1",
            "playit",
            "C-m",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await process.wait()

        # Try to attach to the tmux session in a new terminal window
        # This is non-fatal — the server is already running in tmux
        try:
            process = await asyncio.create_subprocess_exec(
                "konsole",
                "--separate",
                "--hide-menubar",
                "-e",
                "tmux attach-session -t unitedblocks",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.sleep(1)
        except FileNotFoundError:
            logger.info("konsole not available, server running in tmux headless mode")
        except Exception as e:
            logger.warning(f"Could not open terminal window: {e}")

        return {
            "success": True,
            "method": "tmux",
            "message": "Minecraft server is starting in tmux session.",
        }

    except Exception as e:
        return {
            "success": False,
            "method": "tmux",
            "error": f"Terminal method failed: {e}",
        }


def is_minecraft_server_running():
    """
    Checks if the Minecraft server is running by checking the process list.

    Returns:
        bool: True if the server is running, False otherwise
    """
    try:
        for proc in psutil.process_iter(["name"]):
            try:
                if "UnitedBlocks.sh".lower() in proc.info["name"].lower():
                    logger.info("Minecraft server is running.")
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return False
    except Exception:
        logger.info("Minecraft server is not running.")
        return False


class GrandpaReplyModal(Modal, title="Reply to Message"):
    reply = TextInput(
        label="What do you want to say?",
        style=TextStyle.paragraph,
        placeholder="Type your reply to this message...",
        max_length=2000,
        required=True,
    )

    def __init__(self, target_message: discord.Message):
        super().__init__(title="Reply to Message")
        self.target_message = target_message

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            await interaction.response.defer(ephemeral=False)

            username: str = str(interaction.user)
            channel: str = str(interaction.channel)
            user_message: str = self.reply.value
            replied_to_message_content: str = self.target_message.content
            replied_to_message_author: str = str(self.target_message.author)

            if user_message.strip().startswith("!ignore"):
                logger.info(f"[{channel}] {username}: Message ignored via context menu ('!ignore' prefix).")
                await interaction.followup.send("Message ignored due to '!ignore' prefix.")
                return

            gf_pattern = r"(g[\W_]*f|g[\W_]*i[\W_]*r[\W_]*l[\W_]*f[\W_]*r[\W_]*i[\W_]*e[\W_]*n[\W_]*d)"
            prodeh_pattern = r"p[\W_]*r[\W_]*o[\W_]*d[\W_]*e[\W_]*h"
            if re.search(gf_pattern, user_message, re.IGNORECASE) and re.search(
                prodeh_pattern, user_message, re.IGNORECASE
            ):
                await interaction.followup.send(
                    "Sorry, your message cannot contain prohibited words 'gf'/'girlfriend' and 'prodeh'."
                )
                return

            filler_pattern = r"^\s*(um{1,}|uh+|hmm+|hm+|huh+|ok(ay)?|lol+|lmao+|brb|idk|\?+|\.{2,})\s*$"
            if re.match(filler_pattern, user_message, re.IGNORECASE):
                logger.info(f"[{channel}] {username}: Context menu message ignored (filler).")
                await interaction.followup.send("Message ignored due to filler/short content.")
                return

            logger.info(f"[{channel}] {username} (replying to {replied_to_message_author}): {user_message}")

            response: str = get_response(
                user_message,
                username,
                replied_to_message_content,
                replied_to_message_author,
            )

            combined = (
                f"**{username}** (replying to **{replied_to_message_author}**): {user_message}\n\n"
                f"**William:** {response}"
            )
            max_length = 2000
            if len(combined) <= max_length:
                await interaction.followup.send(combined)
            else:
                for i in range(0, len(combined), max_length):
                    await interaction.followup.send(combined[i : i + max_length])

            logger.info(f"Context menu reply sent to {username} in {interaction.channel}")

        except discord.errors.Forbidden as e:
            logger.error(f"Permission error in context menu reply: {e}")
            await interaction.followup.send("I don't have permission to perform this action.")
        except Exception as e:
            logger.error(f"Error in context menu reply: {e}")
            await interaction.followup.send(
                "Oh my, I seem to have dropped my glasses! Could you try again, dearie?"
            )


def setup_commands(tree: app_commands.CommandTree):
    @tree.command(name="grandpa", description="Chat with Professor William")
    async def grandpa(interaction: discord.Interaction, message: str):
        """
        Slash command to chat with William from any channel

        Args:
            interaction: The interaction object
            message: The message to send to William
        """
        try:
            username: str = str(interaction.user)
            channel: str = str(interaction.channel)

            # Defer the response if it might take longer than 3 seconds
            await interaction.response.defer(ephemeral=False)

            # Use the "Ask Grandpa" message context menu (right-click -> Apps) for reply-to-message
            replied_to_message_content: str | None = None
            replied_to_message_author: str | None = None

            # If the message starts with "!ignore", do not send it to the LLM
            if message.strip().startswith("!ignore"):
                logger.info(
                    f"[{channel}] {username}: Message ignored due to '!ignore' prefix."
                )
                await interaction.followup.send(
                    "Message ignored due to '!ignore' prefix."
                )
                return

            # Check if user_message contains both "gf" or "girlfriend" and "prodeh" (case-insensitive, fuzzy)
            gf_pattern = r"(g[\W_]*f|g[\W_]*i[\W_]*r[\W_]*l[\W_]*f[\W_]*r[\W_]*i[\W_]*e[\W_]*n[\W_]*d)"
            prodeh_pattern = r"p[\W_]*r[\W_]*o[\W_]*d[\W_]*e[\W_]*h"
            if re.search(gf_pattern, message, re.IGNORECASE) and re.search(
                prodeh_pattern, message, re.IGNORECASE
            ):
                await interaction.followup.send(
                    "Sorry, your message cannot contain prohibited words 'gf'/'girlfriend' and 'prodeh'."
                )
                return

            # Detect "umm" and similar small filler messages and ignore them
            # Examples: "umm", "uh", "hmm", "hm", "huh", "ok", "okay", "lol", "lmao", "brb", "idk", "?", "..."
            filler_pattern = r"^\s*(um{1,}|uh+|hmm+|hm+|huh+|ok(ay)?|lol+|lmao+|brb|idk|\?+|\.{2,})\s*$"
            if re.match(filler_pattern, message, re.IGNORECASE):
                logger.info(
                    f"[{channel}] {username}: Message ignored due to filler/short content ('{message.strip()}')."
                )
                await interaction.followup.send(
                    "Message ignored due to filler/short content."
                )
                return

            logger.info(f"[{channel}] {username}: {message}")

            # Get response with reply context (now possibly filled)
            response: str = get_response(
                message, username, replied_to_message_content, replied_to_message_author
            )

            # Send both the user's message and William's response in a single followup
            combined = f"**{username}:** {message}\n\n**William:** {response}"
            max_length = 2000
            if len(combined) <= max_length:
                await interaction.followup.send(combined)
            else:
                for i in range(0, len(combined), max_length):
                    await interaction.followup.send(combined[i : i + max_length])
            logger.info(
                f"Slash command response sent to {username} in {interaction.channel}"
            )

        except discord.errors.Forbidden as e:
            logger.error(f"Permission error in grandpa command: {e}")
            await interaction.followup.send(
                "I don't have permission to perform this action."
            )
        except Exception as e:
            logger.error(f"Error in grandpa command: {e}")
            await interaction.followup.send(
                "Oh my, I seem to have dropped my glasses! Could you try again, dearie?"
            )

    @tree.command(name="start", description="Start the Minecraft Server")
    async def start(interaction: discord.Interaction):
        """
        Slash command to start the Minecraft Server

        Args:
            interaction: The interaction object
        """
        if interaction.channel_id != MinecraftServer_Channel:
            await interaction.response.send_message(
                "This command can only be used in the #game-chat channel.",
                ephemeral=True,
            )
            return

        try:
            await interaction.response.defer()

            result = await start_minecraft_server()

            if isinstance(result, dict) and result["success"]:
                message = "🚀 **Minecraft server started successfully!**"
                if "message" in result:
                    message += f"\n{result['message']}"
                await interaction.followup.send(message)
                logger.info(f"Minecraft server started by {interaction.user}.")
            else:
                error_msg = "❌ **Error starting Minecraft server...**"
                if isinstance(result, dict) and "error" in result:
                    error_msg += f"\n{result['error']}"
                    logger.error(
                        f"Failed to start Minecraft server: {result.get('error')}"
                    )
                await interaction.followup.send(error_msg, ephemeral=True)
                logger.error("Failed to start Minecraft server.")

        except Exception as e:
            logger.error(f"Error executing command: {str(e)}")
            await interaction.followup.send(
                "❌ **Error starting Minecraft server...**", ephemeral=True
            )

    @tree.context_menu(name="Ask Grandpa")
    async def ask_grandpa(interaction: discord.Interaction, message: discord.Message):
        modal = GrandpaReplyModal(message)
        await interaction.response.send_modal(modal)


@client.event
async def on_ready() -> None:
    logger.info(f"Bot {client.user} is now running!")

    # Set up commands
    setup_commands(client.tree)

    # Sync slash commands
    try:
        synced = await client.tree.sync()
        logger.info(f"Synced {len(synced)} command(s)")
    except Exception as e:
        logger.error(f"Failed to sync commands: {e}")


@client.event
async def on_message(message: Message) -> None:
    msg_id = message.id
    logger.info(f"on_message fired for msg_id={msg_id}, author={message.author}, content='{message.content[:50]}'")
    if message.author == client.user:
        logger.info(f"on_message: ignoring own message {msg_id}")
        return

    username: str = str(message.author)
    user_message: str = message.content
    channel: str = str(message.channel)

    replied_to_message_content: str | None = None
    replied_to_message_author: str | None = None

    is_reply_to_bot = False
    if message.reference:
        try:
            original_message = await message.channel.fetch_message(
                message.reference.message_id
            )
            replied_to_message_content = original_message.content
            replied_to_message_author = str(original_message.author)
            is_reply_to_bot = original_message.author == client.user

            logger.info(
                f"[{channel}] {username}: Replied to {replied_to_message_author}: {replied_to_message_content}"
            )

        except NotFound:
            logger.warning(
                f"[{channel}] {username} replied to a message, but the original was not found."
            )
        except Exception as e:
            logger.error(f"Error fetching replied message: {e}")

    is_bot_mentioned = client.user.mentioned_in(message)

    if not is_bot_mentioned and message.channel.id != PervertedOldMan_Channel and not is_reply_to_bot:
        logger.info(f"on_message: ignoring msg {msg_id} — wrong channel and not reply to bot")
        return

    if is_bot_mentioned:
        logger.info(
            f"[{channel}] {username}: Bot mentioned in msg {msg_id}, bypassing channel filter."
        )

    # In PervertedOldMan_Channel, ignore replies to other users unless "grandpa" is in the message
    if not is_bot_mentioned and (
        message.channel.id == PervertedOldMan_Channel
        and message.reference
        and not is_reply_to_bot
    ):
        if "grandpa" not in user_message.lower():
            logger.info(
                f"[{channel}] {username}: Ignored reply to another user (no 'grandpa' keyword)."
            )
            return

    # If the message starts with "!ignore", do not send it to the LLM
    if user_message.strip().startswith("!ignore"):
        logger.info(f"[{channel}] {username}: Message ignored due to '!ignore' prefix.")
        return

    # Check if user_message contains both "gf" or "girlfriend" and "prodeh" (case-insensitive, fuzzy)
    gf_pattern = (
        r"(g[\W_]*f|g[\W_]*i[\W_]*r[\W_]*l[\W_]*f[\W_]*r[\W_]*i[\W_]*e[\W_]*n[\W_]*d)"
    )
    prodeh_pattern = r"p[\W_]*r[\W_]*o[\W_]*d[\W_]*e[\W_]*h"
    if re.search(gf_pattern, user_message, re.IGNORECASE) and re.search(
        prodeh_pattern, user_message, re.IGNORECASE
    ):
        await message.channel.send(
            "Sorry, your message cannot contain prohibited words 'gf'/'girlfriend' and 'prodeh'."
        )
        return

    # Detect "umm" and similar small filler messages and ignore them
    # Examples: "umm", "uh", "hmm", "hm", "huh", "ok", "okay", "lol", "lmao", "brb", "idk", "?", "..."
    filler_pattern = (
        r"^\s*(um{1,}|uh+|hmm+|hm+|huh+|ok(ay)?|lol+|lmao+|brb|idk|\?+|\.{2,})\s*$"
    )
    if re.match(filler_pattern, user_message, re.IGNORECASE):
        logger.info(
            f"[{channel}] {username}: Message ignored due to filler/short content ('{user_message.strip()}')."
        )
        return

    logger.info(f"[{channel}] {username}: {user_message}")
    await send_message(
        message,
        user_message,
        replied_to_message_content,
        replied_to_message_author,
        username,
    )


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
