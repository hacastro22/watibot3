"""ManyChat Instagram channel adapter scaffolding.

Maps ManyChat IG webhooks to the unified message model and defines the
outgoing API surface. Implementation details for sending are deferred
until the ManyChat client is finalized.
"""
from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import urlparse

from app.adapters.base_channel_adapter import ChannelAdapter
from app.models.unified_message import MessageType, UnifiedMessage
from app.clients import manychat_client


class ManyChatIGAdapter(ChannelAdapter):
    """Adapter for ManyChat Instagram channel."""

    def can_handle(self, raw: Dict[str, Any]) -> bool:
        if raw.get("platform") != "manychat":
            return False
        subscriber = raw.get("subscriber", {}) or {}
        return bool(subscriber.get("ig_id"))

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

        # IG voice notes often arrive as a plain text URL (lookaside.fbsbx.com/ig_messaging_cdn)
        if not attachments and text:
            url_candidate = text.strip()
            try:
                parsed = urlparse(url_candidate)
                host = (parsed.netloc or "").lower()
                path = parsed.path.lower()
                if host.endswith("fbsbx.com") and ("ig_messaging_cdn" in path or path.endswith(('.mp3', '.m4a', '.aac', '.amr', '.oga', '.ogg', '.wav', '.mp4'))):
                    media_url = url_candidate
                    msg_type = MessageType.AUDIO
                    text = ""
            except Exception:
                pass

        if not text and not media_url:
            return None

        return UnifiedMessage(
            channel="instagram",
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
        """Send a message via ManyChat IG using IG API key.

        If media is provided, expects keys: {"type": str, "file_path": str, "caption": Optional[str]}.
        """
        try:
            if media and media.get("type") and media.get("file_path"):
                media_type = str(media.get("type")).lower()
                file_path = str(media.get("file_path"))
                caption = str(media.get("caption")) if media.get("caption") else ""
                resp = await manychat_client.send_media_message(
                    subscriber_id=user_id,
                    file_path=file_path,
                    media_type=media_type,
                    channel="instagram",
                    caption=caption,
                )
                return resp is not None

            if not message:
                return False
            resp = await manychat_client.send_ig_text_message(user_id, message)
            return resp is not None
        except Exception:
            return False
