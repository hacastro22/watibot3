"""ManyChat Facebook channel adapter scaffolding.

Maps ManyChat FB webhooks to the unified message model and defines the
outgoing API surface. Implementation details for sending are deferred
until the ManyChat client is finalized.
"""
from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import urlparse

from app.adapters.base_channel_adapter import ChannelAdapter
from app.models.unified_message import MessageType, UnifiedMessage
from app.clients import manychat_client
from app import conversation_log
from app.utils.message_splitter import split_message, needs_splitting
import logging

logger = logging.getLogger(__name__)


class ManyChatFBAdapter(ChannelAdapter):
    """Adapter for ManyChat Facebook channel."""

    def can_handle(self, raw: Dict[str, Any]) -> bool:
        if raw.get("platform") != "manychat":
            return False
        subscriber = raw.get("subscriber", {}) or {}
        return not bool(subscriber.get("ig_id"))

    async def parse_incoming(self, raw: Dict[str, Any]) -> Optional[UnifiedMessage]:
        if not self.can_handle(raw):
            return None

        subscriber = raw.get("subscriber", {}) or {}
        user_id = str(subscriber.get("id", ""))
        if not user_id:
            return None

        message = raw.get("message", {}) or {}
        text = message.get("text") or ""

        attachments = message.get("attachments") or []
        media_url: Optional[str] = None
        msg_type = MessageType.TEXT

        if attachments:
            # Take first attachment only for initial scaffolding
            att = attachments[0] or {}
            att_type = (att.get("type") or "").lower()
            payload = att.get("payload", {}) or {}
            media_url = payload.get("url")
            if att_type == "image":
                msg_type = MessageType.IMAGE
            elif att_type == "audio":
                msg_type = MessageType.AUDIO
            elif att_type in ("file", "document"):
                msg_type = MessageType.DOCUMENT
            elif att_type == "video":
                msg_type = MessageType.VIDEO

        # Some FB voice notes arrive as a plain text URL (no attachments).
        # Detect common FB CDN audio clip links and classify as audio.
        if not attachments and text:
            url_candidate = text.strip()
            try:
                parsed = urlparse(url_candidate)
                host = (parsed.netloc or "").lower()
                path = parsed.path.lower()
                # Heuristics: FB CDN + audio-like extensions or 'audioclip' marker
                if host.endswith("fbsbx.com") and (
                    "audioclip" in path or path.endswith(('.mp3', '.m4a', '.aac', '.amr', '.oga', '.ogg', '.wav', '.mp4'))
                ):
                    media_url = url_candidate
                    msg_type = MessageType.AUDIO
                    text = ""
                # Heuristics: FB CDN image links (scontent / fbcdn) with image extensions
                elif (host.endswith("fbcdn.net") or host.startswith("scontent.")) and (
                    path.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif"))
                ):
                    media_url = url_candidate
                    msg_type = MessageType.IMAGE
                    text = ""
            except Exception:
                # Ignore parsing issues; keep original text classification
                pass

        if not text and not media_url:
            # Nothing actionable
            return None

        # Log customer message for context preservation
        if text:
            try:
                conversation_log.log_message(user_id, "user", text, "facebook")
            except Exception as e:
                logger.error(f"[FB] Failed to log customer message: {e}")

        return UnifiedMessage(
            channel="facebook",
            user_id=user_id,
            message_type=msg_type,
            content=text or "",
            media_url=media_url,
            reply_context_id=None,
            metadata={"subscriber": subscriber},
        )

    async def send_outgoing(
        self,
        user_id: str,
        message: str,
        media: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Send a message via ManyChat FB.

        Uses Facebook-specific API key and endpoints via manychat_client.
        If media is provided, expects keys: {"type": str, "file_path": str, "caption": Optional[str]}.
        """
        try:
            # Media branch
            if media and media.get("type") and media.get("file_path"):
                media_type = str(media.get("type")).lower()
                file_path = str(media.get("file_path"))
                caption = str(media.get("caption")) if media.get("caption") else ""
                
                # Send caption as-is

                resp = await manychat_client.send_media_message(
                    subscriber_id=user_id,
                    file_path=file_path,
                    media_type=media_type,
                    channel="facebook",
                    caption=caption,
                )
                return resp is not None

            # Default to text
            # Empty message is OK - it means the tool already sent the content (e.g., send_menu_pdf)
            if not message or message.strip() == "":
                logger.info(f"[FB] Empty message for {user_id} - assuming content already sent via tool")
                return True
            
            # Check if message needs splitting due to ManyChat's 2000-char limit
            if needs_splitting(message):
                chunks = split_message(message)
                logger.warning(f"[FB] Message exceeds 2000 chars ({len(message)} chars), splitting into {len(chunks)} parts")
                
                # Send each chunk sequentially
                for idx, chunk in enumerate(chunks, 1):
                    logger.info(f"[FB] Sending part {idx}/{len(chunks)} ({len(chunk)} chars)")
                    resp = await manychat_client.send_text_message(user_id, chunk)
                    if not resp:
                        logger.error(f"[FB] Failed to send part {idx}/{len(chunks)}")
                        return False
                    # Small delay between chunks to maintain order
                    if idx < len(chunks):
                        import asyncio
                        await asyncio.sleep(0.5)
                
                # Log the complete message (all chunks combined) for context preservation
                try:
                    conversation_log.log_message(user_id, "assistant", message, "facebook")
                except Exception as e:
                    logger.error(f"[FB] Failed to log assistant message: {e}")
                
                logger.info(f"[FB] Successfully sent all {len(chunks)} parts")
                return True
            else:
                # Message fits within limit, send normally
                # Log assistant response for context preservation
                try:
                    conversation_log.log_message(user_id, "assistant", message, "facebook")
                except Exception as e:
                    logger.error(f"[FB] Failed to log assistant message: {e}")
                
                resp = await manychat_client.send_text_message(user_id, message)
                return resp is not None
        except Exception as e:
            logger.exception(f"[FB] Error sending message to {user_id}: {e}")
            return False
