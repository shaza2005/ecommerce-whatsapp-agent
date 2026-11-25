
"""WhatsApp Bot implementation for the E-Commerce Assistant using neonize."""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

AGENT_DIR = os.path.join(SCRIPT_DIR, "ecommerce-whatsapp-agent")
if os.path.isdir(AGENT_DIR) and AGENT_DIR not in sys.path:
    sys.path.insert(0, AGENT_DIR)

load_dotenv()
load_dotenv(os.path.join(SCRIPT_DIR, ".env"))
if os.path.isdir(AGENT_DIR):
    load_dotenv(os.path.join(AGENT_DIR, ".env"))

try:
    import pylibmagic  # type: ignore[import-untyped]
except ImportError:
    pass

import segno
from neonize.client import NewClient
from neonize.events import ConnectedEv, MessageEv, PairStatusEv, QREv, event as stop_event
from neonize.proto.Neonize_pb2 import JID

import agent

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("whatsapp_bot")

SESSION_FILE = os.getenv("WHATSAPP_SESSION_FILE", os.path.join(SCRIPT_DIR, "session.db"))
logger.info("Using session file: %s", SESSION_FILE)

client = NewClient(SESSION_FILE)

MAX_WORKERS = int(os.getenv("BOT_MAX_WORKERS", "10"))
executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="agent-worker")


def _get_friendly_error(text: str) -> str:
    has_arabic = any("\u0600" <= c <= "\u06ff" for c in text)
    if not has_arabic and any(c.isalpha() for c in text):
        return "Sorry, an unexpected error occurred while processing your request. Please try again later."
    return "عذراً، حدث خطأ غير متوقع أثناء معالجة رسالتك. يرجى المحاولة مرة أخرى لاحقاً."


def _process_and_reply(client: NewClient, chat: JID, phone: str, text: str) -> None:
    logger.info("Received message from phone=%s (message length=%d): %s", phone, len(text), text)
    try:
        reply = agent.process_message(phone, text)
        logger.info("Generated agent reply for phone=%s (reply length=%d)", phone, len(reply))
        client.send_message(to=chat, message=reply)
    except Exception as exc:
        logger.exception("Error processing message for phone=%s: %s", phone, exc)
        friendly_error = _get_friendly_error(text)
        try:
            client.send_message(to=chat, message=friendly_error)
            logger.info(
                "Sent friendly error reply to phone=%s (reply length=%d)",
                phone,
                len(friendly_error),
            )
        except Exception as send_err:
            logger.exception(
                "Failed to send friendly error reply to phone=%s: %s",
                phone,
                send_err,
            )


def on_qr(client: NewClient, data_qr: bytes) -> None:
    logger.info(
        "WhatsApp authentication QR code generated. Scan with WhatsApp (Linked Devices -> Link a Device):"
    )
    try:
        qr = segno.make_qr(data_qr)
        qr.terminal(compact=True)
    except Exception as exc:
        logger.error("Failed to render QR code in terminal: %s", exc)


client.qr(on_qr)


def on_qr_event(client: NewClient, message: QREv) -> None:
    codes = getattr(message, "Codes", [])
    if codes:
        logger.info("WhatsApp authentication QR code received from event:")
        for code in codes:
            try:
                segno.make_qr(code).terminal(compact=True)
            except Exception as exc:
                logger.error("Failed to render QR code from event: %s", exc)


client.event(QREv)(on_qr_event)


def on_connected(client: NewClient, message: ConnectedEv) -> None:
    status = getattr(message, "status", "connected")
    logger.info("Connected to WhatsApp servers successfully! (status: %s)", status)


client.event(ConnectedEv)(on_connected)


def on_pair_status(client: NewClient, message: PairStatusEv) -> None:
    status = getattr(message, "Status", 0)
    if status == 2 or getattr(PairStatusEv, "SUCCESS", 2) == status:
        logger.info(
            "WhatsApp pairing successful! ID: %s, Platform: %s, BusinessName: %s",
            getattr(message, "ID", ""),
            getattr(message, "Platform", ""),
            getattr(message, "BusinessName", ""),
        )
    else:
        logger.info(
            "WhatsApp pair status event: Status=%s, ID=%s, Error=%s",
            status,
            getattr(message, "ID", ""),
            getattr(message, "Error", ""),
        )


client.event(PairStatusEv)(on_pair_status)


def on_message(client: NewClient, message: MessageEv) -> None:
    info = message.Info
    source = info.MessageSource

    # Ignore messages sent by the linked account.
    if source.IsFromMe:
        return

    # Ignore group chats.
    if source.IsGroup:
        return
    if source.Chat.Server == "g.us" or source.Sender.Server == "g.us":
        return

    # Ignore status, broadcasts, and newsletters.
    if source.Chat.Server in ("broadcast", "status", "newsletter"):
        return
    if source.Chat.User == "status" or source.Sender.User == "status":
        return
    if source.BroadcastListOwner.User or len(source.BroadcastRecipients) > 0:
        return
    if getattr(info, "Category", "") == "peer":
        return

    # Read text messages only.
    raw_text = ""
    msg_proto = message.Message
    if msg_proto.conversation:
        raw_text = msg_proto.conversation
    elif msg_proto.extendedTextMessage and msg_proto.extendedTextMessage.text:
        raw_text = msg_proto.extendedTextMessage.text

    text = raw_text.strip()
    if not text:
        return

    # Extract sender phone number.
    sender_user = source.Sender.User or source.Chat.User
    phone = "".join(filter(str.isdigit, sender_user))

    if not phone:
        logger.warning(
            "Could not extract digits from sender User '%s', skipping",
            sender_user,
        )
        return


    chat = source.Chat

    # Process only messages from authorized senders.
    executor.submit(_process_and_reply, client, chat, phone, text)


client.event(MessageEv)(on_message)


def main() -> None:
    logger.info("Starting WhatsApp E-Commerce Bot...")

    shutdown_event = threading.Event()

    def signal_handler(signum: int, frame: Any) -> None:
        if not shutdown_event.is_set():
            shutdown_event.set()
            logger.info(
                "Shutdown signal received (%d). Stopping WhatsApp bot...",
                signum,
            )
            stop_event.set()
            try:
                client.stop()
            except Exception:
                pass

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        client.connect()
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt caught, initiating shutdown...")
    except Exception as exc:
        logger.exception("Client connection terminated with error: %s", exc)
    finally:
        logger.info("Shutting down worker threads...")
        executor.shutdown(wait=True, cancel_futures=True)
        try:
            client.disconnect()
        except Exception:
            pass
        logger.info("WhatsApp bot shutdown cleanly. Goodbye!")


if __name__ == "__main__":
    main()
