"""Unified message model and enums for channel-agnostic processing.

This module defines the `MessageType` enum and the `UnifiedMessage` dataclass
used by channel adapters to normalize incoming payloads for the core pipeline.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional


class MessageType(Enum):
    """Supported message types across channels."""
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    DOCUMENT = "document"
    VIDEO = "video"


@dataclass
class UnifiedMessage:
    """Normalized message container used by the core pipeline.

    Attributes:
        channel: Logical channel identifier (e.g., 'wati', 'facebook', 'instagram').
        user_id: Unique user identifier for the channel (e.g., waId or subscriber.id).
        message_type: One of MessageType values describing the content.
        content: Primary text content (empty string for pure media messages).
        media_url: Optional public URL for media (images, audio, documents, videos).
        reply_context_id: Optional ID to correlate replies/threading if available.
        metadata: Channel-specific metadata preserved for downstream needs.
    """
    channel: str
    user_id: str
    message_type: MessageType
    content: str
    media_url: Optional[str] = None
    reply_context_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
