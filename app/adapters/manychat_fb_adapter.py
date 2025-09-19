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
from app.message_humanizer import humanize_response
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
                
                # Humanize caption if present
                if caption:
                    try:
                        humanized_caption = await humanize_response(caption)
                        logger.info(f"[FB] Caption humanized: {caption[:50]}... -> {humanized_caption[:50]}...")
                        caption = humanized_caption
                    except Exception as e:
                        logger.error(f"[FB] Caption humanization failed, using original: {e}")
                
                resp = await manychat_client.send_media_message(
                    subscriber_id=user_id,
                    file_path=file_path,
                    media_type=media_type,
                    channel="facebook",
                    caption=caption,
                )
                return resp is not None

            # Default to text
            if not message:
                return False
            
            # Humanize the message before sending
            try:
                humanized_message = await humanize_response(message)
                logger.info(f"[FB] Message humanized: {message[:50]}... -> {humanized_message[:50]}...")
            except Exception as e:
                logger.error(f"[FB] Humanization failed, using original message: {e}")
                humanized_message = message
            
            resp = await manychat_client.send_text_message(user_id, humanized_message)
            return resp is not None
        except Exception:
            # Let caller decide on retries
            return False
