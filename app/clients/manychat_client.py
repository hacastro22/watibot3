"""ManyChat client implementation.

Async client functions for Facebook and Instagram operations via ManyChat.
Implements the error-handling pattern used in fbbot3: raise_for_status with
HTTPStatusError logging, returning None on error.

Notes:
- FB and IG use the same endpoint paths but different API keys.
- For IG media, include a content type hint where required by our plan.
"""
from __future__ import annotations

from typing import Any, Dict, Optional
import logging
import os
from pathlib import Path

import httpx

from app import config

logger = logging.getLogger(__name__)


# ----------------------- Instagram-specific -----------------------
async def send_ig_text_message(subscriber_id: str, text: str) -> Optional[Dict[str, Any]]:
    """Send text to Instagram subscriber via ManyChat (IG key)."""
    api_url = f"{config.MANYCHAT_API_URL}/fb/sending/sendContent"
    headers = {
        "Authorization": f"Bearer {config.MANYCHAT_INSTAGRAM_API_KEY}",
        "Content-Type": "application/json",
    }
    # ManyChat v2 payload with IG content type
    message_data: Dict[str, Any] = {
        "version": "v2",
        "content": {
            "type": "instagram",
            "messages": [
                {"type": "text", "text": text},
            ],
        },
    }
    payload: Dict[str, Any] = {
        "subscriber_id": subscriber_id,
        "data": message_data,
    }
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(api_url, json=payload, headers=headers)
            response.raise_for_status()
            logger.info(f"[IG] Text sent to {subscriber_id}: {response.json()}")
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"[IG] Error sending text to {subscriber_id}: {e.response.text}")
            return None


async def mark_ig_conversation_as_open(subscriber_id: str) -> Optional[Dict[str, Any]]:
    """Mark IG conversation as open using IG API key."""
    api_url = f"{config.MANYCHAT_API_URL}/fb/page/markConversationAsOpen"
    headers = {
        "Authorization": f"Bearer {config.MANYCHAT_INSTAGRAM_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"subscriber_id": subscriber_id}
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(api_url, json=payload, headers=headers)
            response.raise_for_status()
            logger.info(f"[IG] Conversation for {subscriber_id} marked as open.")
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"[IG] Error marking conversation as open for {subscriber_id}: {e.response.text}")
            return None


async def mark_ig_conversation_as_closed(subscriber_id: str) -> Optional[Dict[str, Any]]:
    """Mark IG conversation as closed using IG API key."""
    api_url = f"{config.MANYCHAT_API_URL}/fb/page/markConversationAsClosed"
    headers = {
        "Authorization": f"Bearer {config.MANYCHAT_INSTAGRAM_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"subscriber_id": subscriber_id}
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(api_url, json=payload, headers=headers)
            response.raise_for_status()
            logger.info(f"[IG] Conversation for {subscriber_id} marked as closed.")
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"[IG] Error marking conversation as closed for {subscriber_id}: {e.response.text}")
            return None


# ------------------------ Facebook-specific -----------------------
async def send_text_message(subscriber_id: str, text: str) -> Optional[Dict[str, Any]]:
    """Send text to Facebook subscriber via ManyChat (FB key)."""
    api_url = f"{config.MANYCHAT_API_URL}/fb/sending/sendContent"
    headers = {
        "Authorization": f"Bearer {config.MANYCHAT_API_KEY}",
        "Content-Type": "application/json",
    }
    # ManyChat v2 payload
    message_data: Dict[str, Any] = {
        "version": "v2",
        "content": {
            "messages": [
                {"type": "text", "text": text},
            ],
        },
    }
    payload: Dict[str, Any] = {
        "subscriber_id": subscriber_id,
        "data": message_data,
    }
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(api_url, json=payload, headers=headers)
            response.raise_for_status()
            logger.info(f"[FB] Text sent to {subscriber_id}: {response.json()}")
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"[FB] Error sending text to {subscriber_id}: {e.response.text}")
            return None


async def send_image(subscriber_id: str, url: str, caption: str = "") -> Optional[Dict[str, Any]]:
    """Send image to Facebook subscriber via ManyChat (FB key)."""
    api_url = f"{config.MANYCHAT_API_URL}/fb/sending/sendContent"
    headers = {
        "Authorization": f"Bearer {config.MANYCHAT_API_KEY}",
        "Content-Type": "application/json",
    }
    # ManyChat v2 payload; include caption as a preceding text message if provided
    messages: list = []
    if caption:
        messages.append({"type": "text", "text": caption})
    messages.append({"type": "image", "url": url})
    message_data: Dict[str, Any] = {
        "version": "v2",
        "content": {"messages": messages},
    }
    payload = {"subscriber_id": subscriber_id, "data": message_data}
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(api_url, json=payload, headers=headers)
            response.raise_for_status()
            logger.info(f"[FB] Image sent to {subscriber_id}: {response.json()}")
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"[FB] Error sending image to {subscriber_id}: {e.response.text}")
            return None


async def send_file(subscriber_id: str, url: str) -> Optional[Dict[str, Any]]:
    """Send file to Facebook subscriber via ManyChat (FB key)."""
    api_url = f"{config.MANYCHAT_API_URL}/fb/sending/sendContent"
    headers = {
        "Authorization": f"Bearer {config.MANYCHAT_API_KEY}",
        "Content-Type": "application/json",
    }
    message_data: Dict[str, Any] = {
        "version": "v2",
        "content": {
            "messages": [
                {"type": "file", "url": url},
            ],
        },
    }
    payload = {"subscriber_id": subscriber_id, "data": message_data}
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(api_url, json=payload, headers=headers)
            response.raise_for_status()
            logger.info(f"[FB] File sent to {subscriber_id}: {response.json()}")
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"[FB] Error sending file to {subscriber_id}: {e.response.text}")
            return None


async def mark_conversation_as_open(subscriber_id: str) -> Optional[Dict[str, Any]]:
    """Mark FB conversation as open using FB API key."""
    api_url = f"{config.MANYCHAT_API_URL}/fb/page/markConversationAsOpen"
    headers = {
        "Authorization": f"Bearer {config.MANYCHAT_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"subscriber_id": subscriber_id}
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(api_url, json=payload, headers=headers)
            response.raise_for_status()
            logger.info(f"[FB] Conversation for {subscriber_id} marked as open.")
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"[FB] Error marking conversation as open for {subscriber_id}: {e.response.text}")
            return None


async def mark_conversation_as_closed(subscriber_id: str) -> Optional[Dict[str, Any]]:
    """Mark FB conversation as closed using FB API key."""
    api_url = f"{config.MANYCHAT_API_URL}/fb/page/markConversationAsClosed"
    headers = {
        "Authorization": f"Bearer {config.MANYCHAT_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"subscriber_id": subscriber_id}
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(api_url, json=payload, headers=headers)
            response.raise_for_status()
            logger.info(f"[FB] Conversation for {subscriber_id} marked as closed.")
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"[FB] Error marking conversation as closed for {subscriber_id}: {e.response.text}")
            return None


async def add_tag_to_subscriber(subscriber_id: str, tag_name: str) -> Optional[Dict[str, Any]]:
    """Add a tag to FB subscriber.

    Note: Endpoint path may vary by account setup. If it fails, logs error and returns None.
    """
    api_url = f"{config.MANYCHAT_API_URL}/fb/subscriber/tags/addTag"
    headers = {
        "Authorization": f"Bearer {config.MANYCHAT_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"subscriber_id": subscriber_id, "tag_name": tag_name}
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(api_url, json=payload, headers=headers)
            response.raise_for_status()
            logger.info(f"[FB] Tag '{tag_name}' added to {subscriber_id}: {response.json()}")
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"[FB] Error adding tag '{tag_name}' to {subscriber_id}: {e.response.text}")
            return None


# ---------------------- Shared/Channel-dependent ------------------
async def send_media_message(
    subscriber_id: str,
    file_path: str,
    media_type: str,
    channel: str,
    caption: str = "",
) -> Optional[Dict[str, Any]]:
    """Send media using a public URL derived from file_path.

    - If file_path is already a URL, use it.
    - Otherwise, compute a public URL under PUBLIC_MEDIA_BASE_URL.
    - media_type: one of 'image', 'audio', 'video', 'file'/'document'.
    - channel: 'facebook' | 'instagram'
    """
    if file_path.startswith("http://") or file_path.startswith("https://"):
        public_url = file_path
    else:
        # Try under resources/pictures first; if outside, fallback to resources via /files/
        resources_root = Path(__file__).resolve().parents[1] / "resources"
        pictures_root = resources_root / "pictures"
        base_url = (config.PUBLIC_MEDIA_BASE_URL or "").rstrip("/")

        public_url = None
        try:
            # Attempt relative path to pictures
            rel_pictures = os.path.relpath(file_path, pictures_root)
            clean_rel_pictures = str(rel_pictures).replace(os.sep, "/")
            # If the relative path escapes the pictures folder, it will start with '..'
            if clean_rel_pictures.startswith(".."):
                raise ValueError("File not under pictures")
            # Avoid duplicating 'pictures/' in the final URL
            if clean_rel_pictures.startswith("pictures/"):
                public_url = f"{base_url}/{clean_rel_pictures}"
            else:
                public_url = f"{base_url}/pictures/{clean_rel_pictures}"
        except Exception:
            # Fallback: relative to resources root, serve via /files/
            rel_resources = os.path.relpath(file_path, resources_root)
            clean_rel_resources = str(rel_resources).replace(os.sep, "/")
            public_url = f"{base_url}/files/{clean_rel_resources}"

        # Debug log to verify the exact media URL being sent
        logger.info(f"[ManyChat] public URL for media: {public_url}")

    att_type = media_type.lower()
    if att_type == "document":
        att_type = "file"

    api_url = f"{config.MANYCHAT_API_URL}/fb/sending/sendContent"

    if channel == "instagram":
        headers = {
            "Authorization": f"Bearer {config.MANYCHAT_INSTAGRAM_API_KEY}",
            "Content-Type": "application/json",
        }
        # Instagram does not support generic file/PDF attachments. Fallback to text + link.
        messages: list = []
        if caption:
            messages.append({"type": "text", "text": caption})
        if att_type == "file":
            logger.info("[IG] PDF/file not supported as attachment on Instagram. Sending link as text instead.")
            messages.append({"type": "text", "text": public_url})
        else:
            messages.append({"type": att_type, "url": public_url})
        message_data: Dict[str, Any] = {
            "version": "v2",
            "content": {
                "type": "instagram",
                "messages": messages,
            },
        }
        payload = {"subscriber_id": subscriber_id, "data": message_data}
    else:
        headers = {
            "Authorization": f"Bearer {config.MANYCHAT_API_KEY}",
            "Content-Type": "application/json",
        }
        messages = []
        if caption:
            messages.append({"type": "text", "text": caption})
        messages.append({"type": att_type, "url": public_url})
        message_data = {
            "version": "v2",
            "content": {"messages": messages},
        }
        payload = {"subscriber_id": subscriber_id, "data": message_data}
        # Mirror fbbot3 behavior: add message_tag for Facebook media
        payload["message_tag"] = "POST_PURCHASE_UPDATE"

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(api_url, json=payload, headers=headers)
            response.raise_for_status()
            logger.info(f"[{channel.upper()}] Media sent to {subscriber_id}: {response.json()}")
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"[{channel.upper()}] Error sending media to {subscriber_id}: {e.response.text}")
            return None
