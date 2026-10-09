from typing import Final, Optional
import os
import logging
from dotenv import load_dotenv
from discord import Intents, Client, Message, NotFound, TextChannel, app_commands
import discord
from discord.ext import commands
from src.paths import (
    BOT_LOG_PATH,
    CONFIG_DIR,
    CONFIG_FILE_PATH,
    PROJECT_ROOT,
    ensure_runtime_dirs,
)
from src.responses import get_response
import sys
import re
import json
import subprocess
import asyncio
import shlex
import psutil
import prctl

ensure_runtime_dirs()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(BOT_LOG_PATH)],
)
logger = logging.getLogger(__name__)
load_dotenv(os.path.join(CONFIG_DIR, ".env"))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
TOKEN: Final[str] = os.getenv("DISCORD_TOKEN")


def _get_channel_id(name: str) -> int:
    value = os.getenv(name)
    if not value:
        logger.error(f"{name} not found in environment variables")
        sys.exit(1)
    try:
        return int(value)
    except ValueError:
        logger.error(f"{name} must be a valid Discord channel ID (integer)")
        sys.exit(1)


PervertedOldMan_Channel: Final[int] = _get_channel_id("PERVERTED_OLD_MAN_CHANNEL_ID")
MinecraftServer_Channel: Final[int] = _get_channel_id("MINECRAFT_SERVER_CHANNEL_ID")


def _load_minecraft_config() -> dict:
    with open(CONFIG_FILE_PATH, "r") as f:
        return json.load(f)["minecraft"]


_MINECRAFT_CONFIG = _load_minecraft_config()

MC_SCRIPT_PATH: Final[str] = _MINECRAFT_CONFIG["script_path"]
MC_WORKING_DIR: Final[str] = _MINECRAFT_CONFIG["working_dir"]
MC_SCRIPT_NAME: Final[str] = _MINECRAFT_CONFIG["script_name"]
MC_TMUX_SESSION: Final[str] = _MINECRAFT_CONFIG["tmux_session"]
MC_PROCESS_MATCH: Final[str] = _MINECRAFT_CONFIG["process_match"].lower()
MC_TUNNEL_COMMAND: Final[str] = _MINECRAFT_CONFIG["tunnel_command"]
MC_TERMINAL_EMULATOR: Final[str] = _MINECRAFT_CONFIG["terminal_emulator"]
MC_TERMINAL_ARGS: Final[list] = _MINECRAFT_CONFIG["terminal_args"]

intents: Intents = Intents.default()
intents.message_content = True
intents.members = True  # Required so message.author / interaction.member carry role data
client: commands.Bot = commands.Bot(command_prefix="/", intents=intents)


def _extract_role_names(source) -> list[str] | None:
    """Return role names excluding @everyone, or None if roles are unavailable.

    Returns None (unknown) when the object has no usable ``roles`` attribute
    so callers can preserve previously stored roles instead of wiping them
    with ``[]``. An empty list means the member genuinely has no extra roles.
    """
    roles = getattr(source, "roles", None)
    if roles is None:
        return None
    try:
        names: list[str] = []
        for r in roles:
            is_default = getattr(r, "is_default", False)
            try:
                default = is_default() if callable(is_default) else bool(is_default)
            except Exception:
                default = False
            name = getattr(r, "name", None) or str(r)
            if default or name == "@everyone":
                continue
            names.append(name)
        return names
    except TypeError:
        return None

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
    import re

    # Normalize em/en dashes to plain ASCII so replies don't contain them.
    # Em dash (U+2014) acts as a clause break -> comma; en dash (U+2013)
    # acts as a join/range -> hyphen; non-breaking hyphen (U+2011) -> hyphen.
    response = re.sub(r"\s*—\s*", ", ", response)
    response = re.sub(r"\s*–\s*", "-", response)
    response = response.replace("‑", "-")

    # Split on '.' '?' '\n' as sentence boundaries, keeping the delimiter
    # This will split on either '.' or '?' followed by optional whitespace
    parts = re.split(r"([.?\n])", response)
    sentences = []
    current = ""
    for part in parts:
        if part in [".", "?", "\n"]:
            current += part
            if current.strip():
                sentences.append(current.strip())
            current = ""
        else:
            current += part
    if current.strip():
        sentences.append(current.strip())

    # Remove any empty strings
    sentences = [s for s in sentences if s]

    if not sentences:
        await channel.send("I'm sorry, I don't have a response for that.")
        return

    # Send each sentence as a separate message
    for sentence in sentences:
        await channel.send(sentence)


async def send_message(
    message: Message,
    user_message: str,
    replied_to_message_content: str | None,
    replied_to_message_author: str | None,
    username: str,
    user_id: int | None = None,
    roles: list[str] | None = None,
    display_name: str | None = None,
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
            user_id=user_id,
            roles=roles,
            display_name=display_name,
        )

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
    script_path = MC_SCRIPT_PATH

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
        # Start tunnel in background, then run start script in foreground
        process = await asyncio.create_subprocess_exec(
            "tmux",
            "new-session",
            "-d",
            "-s",
            MC_TMUX_SESSION,
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
            MC_TMUX_SESSION,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await process.wait()

        # Run start script in the left pane (pane 0)
        process = await asyncio.create_subprocess_exec(
            "tmux",
            "send-keys",
            "-t",
            f"{MC_TMUX_SESSION}:0.0",
            f"cd {shlex.quote(MC_WORKING_DIR)} && ./{MC_SCRIPT_NAME}",
            "C-m",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await process.wait()

        # Run tunnel in the right pane (pane 1)
        process = await asyncio.create_subprocess_exec(
            "tmux",
            "send-keys",
            "-t",
            f"{MC_TMUX_SESSION}:0.1",
            MC_TUNNEL_COMMAND,
            "C-m",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await process.wait()

        # Attach to the tmux session in a new terminal window
        process = await asyncio.create_subprocess_exec(
            MC_TERMINAL_EMULATOR,
            *MC_TERMINAL_ARGS,
            f"tmux attach-session -t {MC_TMUX_SESSION}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Wait a moment for the terminal to open
        await asyncio.sleep(1)

        return {
            "success": True,
            "method": "tmux",
            "message": "Terminal window opened successfully.",
        }

    except Exception as e:
        return {
            "success": False,
            "method": "tmux",
            "error": f"Terminal method failed: {e}",
        }


def is_minecraft_server_running():
    """
    Checks if the Minecraft server is running by checking the process list
    and the tmux session.

    Returns:
        bool: True if the server is running, False otherwise
    """
    try:
        # Check if the tmux session exists
        try:
            tmux_check = subprocess.run(
                ["tmux", "has-session", "-t", MC_TMUX_SESSION],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if tmux_check.returncode == 0:
                logger.info("Minecraft server is running (tmux session found).")
                return True
        except (FileNotFoundError, OSError):
            # tmux is not installed or not available; fall through to process check
            pass

        # Check process list by name and cmdline (script name appears in
        # cmdline, not in the process name, e.g. "bash .../<script>.sh")
        for proc in psutil.process_iter(["name", "cmdline"]):
            try:
                name = (proc.info.get("name") or "").lower()
                cmdline_parts = proc.info.get("cmdline") or []
                cmdline = " ".join(cmdline_parts).lower()
                if MC_PROCESS_MATCH in name or MC_PROCESS_MATCH in cmdline:
                    logger.info("Minecraft server is running.")
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return False
    except Exception:
        logger.info("Minecraft server is not running.")
        return False


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
        channel: str = str(interaction.channel)

        # Defer the response if it might take longer than 3 seconds
        await interaction.response.defer(ephemeral=False)

        # Try to extract reply context if this slash command is used as a reply to a message
        replied_to_message_content: str | None = None
        replied_to_message_author: str | None = None

        # Try to get reply context if the interaction is a reply to a message
        # This is only possible if the interaction has a message reference (discord.py 2.3+)
        # Otherwise, try to parse reply context from the message text (e.g., quoting)
        try:
            # If the interaction was invoked as a reply to a message (e.g., right-click -> Apps -> grandpa)
            if hasattr(interaction, "channel") and hasattr(interaction, "data"):
                # Check for "message_reference" in interaction.data
                # This is not standard for slash commands, but some clients may provide it
                ref = getattr(interaction, "message", None)
                if ref and getattr(ref, "reference", None):
                    ref_msg_id = ref.reference.message_id
                    if ref_msg_id:
                        original_message = await interaction.channel.fetch_message(
                            ref_msg_id
                        )
                        replied_to_message_content = original_message.content
                        replied_to_message_author = str(original_message.author)
                        logger.info(
                            f"[{channel}] {username}: Replied to {replied_to_message_author}: {replied_to_message_content}"
                        )
        except Exception as e:
            logger.error(f"Error fetching replied message for slash command: {e}")

        # If the message starts with "!ignore", do not send it to the LLM
        if message.strip().startswith("!ignore"):
            logger.info(
                f"[{channel}] {username}: Message ignored due to '!ignore' prefix."
            )
            await interaction.followup.send("Message ignored due to '!ignore' prefix.")
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
        filler_pattern = (
            r"^\s*(um{1,}|uh+|hmm+|hm+|huh+|ok(ay)?|lol+|lmao+|brb|idk|\?+|\.{2,})\s*$"
        )
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
        # Prefer interaction.member (guild Member with roles); fall back to user.
        member_or_user = getattr(interaction, "member", None) or interaction.user
        member_roles = _extract_role_names(member_or_user)
        if member_roles is None:
            member_roles = _extract_role_names(interaction.user)
        response: str = get_response(
            message,
            username,
            replied_to_message_content,
            replied_to_message_author,
            user_id=interaction.user.id,
            roles=member_roles,
            display_name=getattr(interaction.user, "display_name", username),
        )

        # Send the user's message first
        await interaction.followup.send(f"**{username}:** {message}")

        # Send William's response using chunked messaging
        await send_chunked_message(interaction.channel, f"**William:** {response}")
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


@client.tree.command(name="start", description="Start the Minecraft Server")
async def start(interaction: discord.Interaction):
    """
    Slash command to start the Minecraft Server

    Args:
        interaction: The interaction object
    """
    if interaction.channel_id != MinecraftServer_Channel:
        await interaction.response.send_message(
            "This command can only be used in the #game-chat channel.", ephemeral=True
        )
        return

    if is_minecraft_server_running():
        await interaction.response.send_message(
            "✅ **Minecraft server is already running!**"
        )
        logger.info(
            f"{interaction.user} tried to start the Minecraft server, but it is already running."
        )
        return

    try:
        await interaction.response.defer()

        result = await start_minecraft_server()

        if isinstance(result, dict) and result["success"]:
            if result.get("method") == "already_running":
                await interaction.followup.send(
                    "✅ **Minecraft server is already running!**"
                )
                logger.info(
                    f"Minecraft server start requested by {interaction.user}, but it was already running."
                )
                return
            message = "🚀 **Minecraft server started successfully!**"
            if "message" in result:
                message += f"\n{result['message']}"
            await interaction.followup.send(message)
            logger.info(f"Minecraft server started by {interaction.user}.")
        else:
            error_msg = "❌ **Error starting Minecraft server...**"
            if isinstance(result, dict) and "error" in result:
                error_msg += f"\n{result['error']}"
                logger.error(f"Failed to start Minecraft server: {result.get('error')}")
            await interaction.followup.send(error_msg, ephemeral=True)
            logger.error("Failed to start Minecraft server.")

    except Exception as e:
        logger.error(f"Error executing command: {str(e)}")
        await interaction.followup.send(
            "❌ **Error starting Minecraft server...**", ephemeral=True
        )


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
    if message.channel.id != PervertedOldMan_Channel:
        return

    if message.author == client.user:
        return

    username: str = str(message.author)
    user_message: str = message.content
    channel: str = str(message.channel)

    replied_to_message_content: str | None = None
    replied_to_message_author: str | None = None

    if message.reference:
        try:
            original_message = await message.channel.fetch_message(
                message.reference.message_id
            )
            # Skip replies to other users to avoid invoking the model on
            # user-to-user conversations. Only replies to the bot itself
            # fall through to the model.
            if original_message.author != client.user:
                logger.info(
                    f"[{channel}] {username}: Ignored reply to {original_message.author}."
                )
                return

            replied_to_message_content = original_message.content
            replied_to_message_author = str(original_message.author)

            logger.info(
                f"[{channel}] {username}: Replied to {replied_to_message_author}: {replied_to_message_content}"
            )

        except NotFound:
            logger.warning(
                f"[{channel}] {username} replied to a message, but the original was not found."
            )
            return
        except Exception as e:
            logger.error(f"Error fetching replied message: {e}")
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
        user_id=message.author.id,
        roles=_extract_role_names(message.author),
        display_name=getattr(message.author, "display_name", username),
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
