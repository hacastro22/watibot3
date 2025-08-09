"""Adapters package entry.

Provides a factory to obtain adapters by channel string.
"""
from typing import Dict

from app.adapters.manychat_fb_adapter import ManyChatFBAdapter
from app.adapters.manychat_ig_adapter import ManyChatIGAdapter
from app.adapters.wati_adapter import WatiAdapter
from app.adapters.base_channel_adapter import ChannelAdapter


_ADAPTERS: Dict[str, ChannelAdapter] = {
    "wati": WatiAdapter(),
    "facebook": ManyChatFBAdapter(),
    "instagram": ManyChatIGAdapter(),
}


def get_adapter_for_channel(channel: str) -> ChannelAdapter:
    """Return a singleton adapter instance for the given channel."""
    adapter = _ADAPTERS.get(channel)
    if adapter is None:
        raise ValueError(f"No adapter for channel: {channel}")
    return adapter
