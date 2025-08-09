"""Abstract base interface for channel adapters.

Adapters normalize provider-specific payloads to the unified message model
and provide a uniform API for sending responses back through that channel.

Note: Keep implementations channel-specific in concrete adapters.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from app.models.unified_message import UnifiedMessage


class ChannelAdapter(ABC):
    """Base adapter contract for all channels."""

    @abstractmethod
    async def parse_incoming(self, raw: Dict[str, Any]) -> Optional[UnifiedMessage]:
        """Parse channel-specific webhook payload into a `UnifiedMessage`.

        Return None to ignore payloads that are not actionable (e.g., delivery receipts).
        """
        raise NotImplementedError

    @abstractmethod
    async def send_outgoing(
        self,
        user_id: str,
        message: str,
        media: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Send a message back to the user via the channel.

        Args:
            user_id: Channel-specific user id (e.g., waId, subscriber.id).
            message: Text content to send. May be empty for pure media sends.
            media: Optional media payload (e.g., {"type": "image", "url": "..."}).

        Returns:
            True if accepted by the channel, False otherwise.
        """
        raise NotImplementedError

    @abstractmethod
    def can_handle(self, raw: Dict[str, Any]) -> bool:
        """Quick predicate to check if this adapter can handle the payload."""
        raise NotImplementedError
