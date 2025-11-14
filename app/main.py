# Entry point for the FastAPI app
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import asyncio
import logging

from . import config, openai_agent, wati_client
from . import thread_store
from . import message_buffer
from . import whisper_client
from . import image_classifier, payment_proof_analyzer as payment_proof_tool
from . import security
from app.adapters.channel_detector import detect_channel
from app.adapters.manychat_fb_adapter import ManyChatFBAdapter
from app.adapters.manychat_ig_adapter import ManyChatIGAdapter

from datetime import datetime, timedelta
import threading
import time
import os
import tempfile
import httpx
# Agent context now handled inline in get_openai_response()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Caption cache for universal webhook (stores media captions and reply contexts)
# Structure: {file_path: {caption: str, reply_context_id: str, expires_at: float}}
caption_cache = {}
caption_cache_lock = threading.Lock()

# Message tracking for universal webhook safety net
# Tracks messages from /webhook/universal to detect WATI network hiccups
# Structure: {message_key: {wa_id: str, data: dict, timestamp: float, processed: bool}}
pending_messages = {}
pending_messages_lock = threading.Lock()

# Active safety net threads tracking to prevent thread explosion
# Structure: {message_key: thread_object}
active_safety_threads = {}
active_safety_threads_lock = threading.Lock()
 
# Serve static media for ManyChat via /pictures/ and /files/
try:
    from pathlib import Path
    resources_dir = Path(__file__).parent / "resources"
    pictures_dir = resources_dir / "pictures"
    if pictures_dir.exists():
        app.mount("/pictures", StaticFiles(directory=str(pictures_dir)), name="pictures")
        logger.info(f"[STATIC] Mounted /pictures -> {pictures_dir}")
    else:
        logger.warning(f"[STATIC] Pictures directory not found: {pictures_dir}")
    if resources_dir.exists():
        app.mount("/files", StaticFiles(directory=str(resources_dir)), name="files")
        logger.info(f"[STATIC] Mounted /files -> {resources_dir}")
        logger.warning(f"[STATIC] Resources directory not found: {resources_dir}")
except Exception as e:
    logger.exception(f"[STATIC] Failed to mount /pictures: {e}")


def normalize_file_path(file_path: str) -> str:
    """Normalize file path by extracting just the 'data/images/xxx.jpg' portion.
    
    Handles various formats:
    - 'data/images/xxx.jpg' -> 'data/images/xxx.jpg'
    - 'https://...?fileName=data/images/xxx.jpg' -> 'data/images/xxx.jpg'
    - 'https://.../data/images/xxx.jpg' -> 'data/images/xxx.jpg'
    """
    if not file_path:
        return file_path
    
    # Check if it's a URL with fileName parameter
    if 'fileName=' in file_path:
        # Extract the fileName parameter value
        import urllib.parse
        parsed = urllib.parse.urlparse(file_path)
        params = urllib.parse.parse_qs(parsed.query)
        if 'fileName' in params:
            return params['fileName'][0]
    
    # Check if it contains 'data/images/' or 'data/audios/' in the path
    if 'data/' in file_path:
        # Extract from 'data/' onwards
        data_index = file_path.find('data/')
        return file_path[data_index:]
    
    # Return as-is if no normalization needed
    return file_path


def store_caption_cache(file_path: str, caption: str = None, reply_context_id: str = None):
    """Store caption and reply context for a media file path with 300s TTL."""
    # Normalize the file path to ensure consistent cache keys
    normalized_path = normalize_file_path(file_path)
    expires_at = time.time() + 300  # 5 minutes
    with caption_cache_lock:
        caption_cache[normalized_path] = {
            "caption": caption,
            "reply_context_id": reply_context_id,
            "expires_at": expires_at
        }
    logger.info(f"[CAPTION_CACHE] Stored: {normalized_path} -> caption={caption!r}, reply_context={reply_context_id!r}")


def get_caption_from_cache(file_path: str) -> dict:
    """Retrieve caption and reply context from cache if not expired.
    
    Returns:
        dict with keys: caption, reply_context_id (both can be None if not found/expired)
    """
    # Normalize the file path to match cache keys
    normalized_path = normalize_file_path(file_path)
    with caption_cache_lock:
        entry = caption_cache.get(normalized_path)
        if entry and entry["expires_at"] > time.time():
            logger.info(f"[CAPTION_CACHE] Retrieved: {normalized_path} -> caption={entry['caption']!r}, reply_context={entry['reply_context_id']!r}")
            return {"caption": entry["caption"], "reply_context_id": entry["reply_context_id"]}
        elif entry:
            # Expired, remove from cache
            del caption_cache[normalized_path]
            logger.info(f"[CAPTION_CACHE] Expired entry removed: {normalized_path}")
        else:
            logger.info(f"[CAPTION_CACHE] No entry found for: {normalized_path}")
    return {"caption": None, "reply_context_id": None}


def cleanup_expired_cache():
    """Periodic cleanup of expired cache entries."""
    with caption_cache_lock:
        current_time = time.time()
        expired_keys = [k for k, v in caption_cache.items() if v["expires_at"] <= current_time]
        for key in expired_keys:
            del caption_cache[key]
        if expired_keys:
            logger.info(f"[CAPTION_CACHE] Cleaned up {len(expired_keys)} expired entries")


def generate_message_key(wa_id: str, message_text: str, message_type: str = None) -> str:
    """Generate unique key for message tracking.
    
    Args:
        wa_id: WhatsApp ID
        message_text: Message content or file path
        message_type: Message type (text, image, audio, etc.)
    
    Returns:
        Unique message key combining wa_id and content hash
    """
    import hashlib
    # Create hash from message content to identify unique messages
    content_hash = hashlib.md5(f"{message_text}:{message_type}".encode()).hexdigest()[:12]
    return f"{wa_id}:{content_hash}"


def mark_message_processed(wa_id: str, message_text: str, message_type: str = None):
    """Mark a message as processed by main webhook to prevent duplicate forwarding.
    
    Args:
        wa_id: WhatsApp ID
        message_text: Message content or file path
        message_type: Message type
    """
    message_key = generate_message_key(wa_id, message_text, message_type)
    with pending_messages_lock:
        if message_key in pending_messages:
            pending_messages[message_key]["processed"] = True
            logger.info(f"[SAFETY_NET] Marked message as processed: {message_key}")


def safety_net_task(wa_id: str, message_data: dict, message_key: str):
    """Background task that forwards message to processing if not received within 10 seconds.
    
    This handles WATI network hiccups where messages arrive at /webhook/universal
    but fail to reach the main /webhook endpoint.
    
    Args:
        wa_id: WhatsApp ID
        message_data: Original message data from universal webhook
        message_key: Unique message identifier
    """
    try:
        # Wait 10 seconds for message to arrive at main webhook
        time.sleep(10)
        
        with pending_messages_lock:
            entry = pending_messages.get(message_key)
            if not entry:
                logger.info(f"[SAFETY_NET] Message key not found (already cleaned): {message_key}")
                return
            
            if entry["processed"]:
                logger.info(f"[SAFETY_NET] Message already processed by main webhook: {message_key}")
                # Clean up processed message
                del pending_messages[message_key]
                return
            
            # Message was NOT processed - WATI network hiccup detected!
            logger.warning(f"[SAFETY_NET] WATI hiccup detected! Message not received at main webhook after 10s: wa_id={wa_id}, message_key={message_key}")
            
            # Extract message details
            message_type = message_data.get("type", "text")
            text_content = message_data.get("text", "")
            file_path = message_data.get("data")
            reply_context_id = message_data.get("replyContextId")
            
            # Determine content and type for buffering
            if message_type != "text" and file_path:
                # Media message
                buffer_type = "image" if message_type in ("image", "photo", "document") else "audio" if message_type in ("audio", "voice") else "text"
                content = file_path
                caption = text_content
            else:
                # Text message
                buffer_type = "text"
                content = text_content
                caption = None
            
            try:
                # Forward to message buffer for processing
                logger.info(f"[SAFETY_NET] Forwarding to message buffer: wa_id={wa_id}, type={buffer_type}, content={content[:50]}...")
                message_buffer.buffer_message(wa_id, buffer_type, content, caption, reply_context_id)
                
                # Get old timestamps before updating (for timer callback)
                old_webhook_timestamp = thread_store.get_last_webhook_timestamp(wa_id)
                old_last_updated = thread_store.get_last_updated_timestamp(wa_id)
                
                # Update timestamps (same as main webhook)
                thread_store.update_last_webhook_timestamp(wa_id)
                
                # CRITICAL: Start timer to process the buffered message
                now = datetime.utcnow()
                waid_last_message[wa_id] = now
                
                with timer_lock:
                    # Only start timer if one isn't already running
                    if wa_id not in waid_timers:
                        timer_start_time = now
                        t = threading.Thread(target=timer_callback, args=(wa_id, timer_start_time, old_webhook_timestamp, old_last_updated))
                        t.daemon = True
                        waid_timers[wa_id] = t
                        t.start()
                        logger.info(f"[SAFETY_NET] Started timer thread for {wa_id}")
                    else:
                        logger.info(f"[SAFETY_NET] Timer already running for {wa_id}, message buffered")
                
                # Mark as processed to prevent duplicate forwarding
                entry["processed"] = True
                logger.info(f"[SAFETY_NET] Successfully forwarded message to processing")
                
            except Exception as e:
                logger.exception(f"[SAFETY_NET] Failed to forward message: {e}")
    finally:
        # Remove thread from active tracking when done
        with active_safety_threads_lock:
            active_safety_threads.pop(message_key, None)
            logger.info(f"[SAFETY_NET] Removed tracking for message_key: {message_key}")
        
        # Clean up after 60 seconds
        time.sleep(50)  # Already waited 10, wait 50 more = 60 total
        with pending_messages_lock:
            if message_key in pending_messages:
                del pending_messages[message_key]
                logger.info(f"[SAFETY_NET] Cleaned up message entry: {message_key}")


async def process_image_message(wa_id: str, file_path: str, caption: str = None, reply_context_id: str = None) -> str:
    """
    Downloads, classifies, and analyzes an image message.
    
    Args:
        wa_id: WhatsApp ID
        file_path: Path to the image file
        caption: Optional caption/text that came with the image
        reply_context_id: Optional reply context ID from universal webhook
    """
    logger.info(f"[PROCESS_IMAGE] Starting processing for image: {file_path} (caption: {caption!r}, reply_context: {reply_context_id!r})")
    tmpfile_path = None
    try:
        media_url = f"{config.WATI_API_URL}/api/v1/getMedia?fileName={file_path}"
        headers = {"Authorization": f"Bearer {config.WATI_API_KEY}"}

        async with httpx.AsyncClient() as client:
            response = await client.get(media_url, headers=headers, timeout=60)
            response.raise_for_status()

            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmpfile:
                tmpfile.write(response.content)
                tmpfile_path = tmpfile.name
        
        logger.info(f"[PROCESS_IMAGE] Image downloaded to temp file: {tmpfile_path} for {wa_id}")

        classification_result = await image_classifier.classify_image_with_context(
            image_path=tmpfile_path,
            conversation_context=None,  # Context is handled by the agent, not here
            wa_id=wa_id,
            caption=caption,
            reply_context_id=reply_context_id
        )
        
        classification = classification_result.get("classification", "unknown")
        confidence = classification_result.get("confidence", 0)
        direct_response = classification_result.get("direct_response")
        logger.info(f"[PROCESS_IMAGE] Classification for {wa_id}: {classification} (Confidence: {confidence})")

        # If general_inquiry was handled directly with Responses API, send response and skip further processing
        if direct_response:
            response_text = direct_response.get("response_text", "")
            if response_text:
                logger.info(f"[PROCESS_IMAGE] Sending direct response from general_inquiry handler: {len(response_text)} chars")
                # Send directly to WATI (must await async function)
                await wati_client.send_wati_message(wa_id, response_text)
                # Return special marker to skip get_openai_response
                return "__ALREADY_RESPONDED__"
            else:
                logger.warning(f"[PROCESS_IMAGE] direct_response exists but no response_text found")

        if classification == 'payment_proof' and confidence >= 0.8:
            analysis_result = await payment_proof_tool.analyze_payment_proof(tmpfile_path)
            return f"(User sent a payment proof. Analysis result: {analysis_result})"
        else:
            # Return caption with classification if provided
            if caption:
                return f"(User sent an image of type '{classification}' with caption: {caption})"
            else:
                return f"(User sent an image of type '{classification}')"

    except Exception as e:
        logger.exception(f"[PROCESS_IMAGE] Failed to download/process image from WATI: {file_path} for {wa_id}")
        return '(User sent an image, but an error occurred during processing)'
    finally:
        if tmpfile_path:
            try:
                os.remove(tmpfile_path)
            except Exception as e:
                logger.warning(f"[PROCESS_IMAGE] Could not delete temp file: {tmpfile_path}")

async def process_audio_message(file_path: str) -> str:
    """
    Downloads and transcribes an audio message.
    """
    logger.info(f"[PROCESS_AUDIO] Starting processing for audio: {file_path}")
    tmpfile_path = None
    try:
        media_url = f"{config.WATI_API_URL}/api/v1/getMedia?fileName={file_path}"
        headers = {"Authorization": f"Bearer {config.WATI_API_KEY}"}

        async with httpx.AsyncClient() as client:
            response = await client.get(media_url, headers=headers, timeout=60)
            response.raise_for_status()

            with tempfile.NamedTemporaryFile(delete=False, suffix=".opus") as tmpfile:
                tmpfile.write(response.content)
                tmpfile_path = tmpfile.name

        transcription = await whisper_client.transcribe_audio_opus(tmpfile_path)
        return f'(User sent a voice note: "{transcription}")'

    except Exception as e:
        logger.exception(f"[PROCESS_AUDIO] Failed to download/process audio from WATI: {file_path}")
        return '(User sent a voice note, but an error occurred during processing)'
    finally:
        if tmpfile_path:
            try:
                os.remove(tmpfile_path)
            except Exception as e:
                logger.warning(f"[PROCESS_AUDIO] Could not delete temp file: {tmpfile_path}")


async def process_manychat_audio_message(url: str) -> str:
    """
    Downloads and transcribes an audio message from a public ManyChat URL (e.g., Facebook CDN).

    Returns a formatted string with the transcription or a fallback description on error.
    """
    logger.info(f"[PROCESS_AUDIO_MC] Starting processing for audio URL: {url}")
    tmpfile_path = None
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=60)
            response.raise_for_status()

            # Some IG lookaside URLs can actually be images (e.g., screenshots sent as links).
            # If the content-type indicates an image, route to the image processor instead.
            content_type = response.headers.get("Content-Type", "").lower()
            if content_type.startswith("image/"):
                logger.info(f"[PROCESS_AUDIO_MC] Detected image content-type ({content_type}) for URL; routing to image handler")
                # Reuse the image processing path to keep behavior identical to WATI
                return await process_manychat_image_message(url)

            # Save as a generic container; ffmpeg detects the codec/container automatically
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmpfile:
                tmpfile.write(response.content)
                tmpfile_path = tmpfile.name

        # Reuse whisper client (ffmpeg -i works for mp4/m4a/aac/ogg/etc.)
        transcription = await whisper_client.transcribe_audio_opus(tmpfile_path)
        return f'(User sent a voice note: "{transcription}")'

    except Exception:
        logger.exception(f"[PROCESS_AUDIO_MC] Failed to download/process audio from URL: {url}")
        return '(User sent a voice note, but an error occurred during processing)'
    finally:
        if tmpfile_path:
            try:
                os.remove(tmpfile_path)
            except Exception:
                logger.warning(f"[PROCESS_AUDIO_MC] Could not delete temp file: {tmpfile_path}")


async def process_manychat_image_message(url: str) -> str:
    """
    Downloads, classifies, and analyzes an image from a public ManyChat URL.

    Mirrors WATI image processing but pulls the file directly from the given URL.
    """
    logger.info(f"[PROCESS_IMAGE_MC] Starting processing for image URL: {url}")
    tmpfile_path = None
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=60)
            response.raise_for_status()

            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmpfile:
                tmpfile.write(response.content)
                tmpfile_path = tmpfile.name

        classification_result = await image_classifier.classify_image_with_context(
            image_path=tmpfile_path,
            conversation_context=None,
            wa_id="manychat",  # Identifier only; not used by classifier
        )
        classification = classification_result.get("classification", "unknown")
        confidence = classification_result.get("confidence", 0)
        logger.info(f"[PROCESS_IMAGE_MC] Classification: {classification} (Confidence: {confidence})")

        if classification == 'payment_proof' and confidence >= 0.8:
            analysis_result = await payment_proof_tool.analyze_payment_proof(tmpfile_path)
            return f"(User sent a payment proof. Analysis result: {analysis_result})"
        else:
            return f"(User sent an image of type '{classification}')"

    except Exception:
        logger.exception(f"[PROCESS_IMAGE_MC] Failed to download/process image from URL: {url}")
        return '(User sent an image, but an error occurred during processing)'
    finally:
        if tmpfile_path:
            try:
                os.remove(tmpfile_path)
            except Exception:
                logger.warning(f"[PROCESS_IMAGE_MC] Could not delete temp file: {tmpfile_path}")


async def get_original_message_context(wa_id: str, reply_context_id: str) -> str:
    """
    Retrieves the original message being replied to and creates a contextual description
    based on message type (text, image, voice, document, video, etc.).
    
    Args:
        wa_id: WhatsApp ID to search messages for
        reply_context_id: The whatsappMessageId of the original message being replied to
        
    Returns:
        Formatted context string describing the original message, or empty string if not found/error
    """
    try:
        logger.info(f"[REPLY_CONTEXT] Retrieving original message: waId={wa_id}, messageId={reply_context_id}")
        
        # Call Wati API to get messages for this WhatsApp ID
        wati_url = f"{config.WATI_API_URL}/api/v1/getMessages/{wa_id}"
        headers = {"Authorization": f"Bearer {config.WATI_API_KEY}"}
        
        async with httpx.AsyncClient() as client:
            response = await client.get(wati_url, headers=headers, timeout=30)
            logger.info(f"[REPLY_CONTEXT] API response status: {response.status_code}")
            
            if response.status_code != 200:
                logger.warning(f"[REPLY_CONTEXT] Failed to retrieve messages: HTTP {response.status_code}")
                return ""
            
            messages = response.json()
            logger.info(f"[REPLY_CONTEXT] Retrieved {len(messages)} messages")
            
            # Find the message with matching whatsappMessageId
            original_message = None
            for msg in messages:
                if msg.get("whatsappMessageId") == reply_context_id:
                    original_message = msg
                    break
            
            if not original_message:
                logger.warning(f"[REPLY_CONTEXT] Original message not found with ID: {reply_context_id}")
                return ""
            
            # Format context based on message type
            message_type = original_message.get("type", "text")
            logger.info(f"[REPLY_CONTEXT] Found original message of type: {message_type}")
            
            if message_type == "text":
                text_content = original_message.get("text", "")
                return text_content if text_content else ""
                
            elif message_type == "image":
                # For Wati, check if there's a text field that might contain caption
                caption = original_message.get("text", "")
                if caption:
                    return f"[Image with caption: '{caption}']"
                else:
                    return "[Image (no caption)]"
                    
            elif message_type == "audio":
                # Wati might have additional data to distinguish voice vs audio
                data = original_message.get("data", {})
                # Check if it's a voice note (common pattern in WhatsApp APIs)
                is_voice = (
                    isinstance(data, dict) and (
                        data.get("voice") is True or
                        "voice" in str(data.get("filename", "")).lower() or
                        "ptt" in str(data).lower()  # Push-to-talk
                    )
                )
                if is_voice:
                    return "[Voice message]"
                else:
                    return "[Audio file]"
                    
            elif message_type == "document":
                # Extract filename from data or text field
                data = original_message.get("data", {})
                filename = ""
                if isinstance(data, dict):
                    filename = data.get("filename") or data.get("fileName") or ""
                
                if not filename:
                    # Try to get filename from text field as fallback
                    text_content = original_message.get("text", "")
                    if text_content and any(ext in text_content.lower() for ext in [".pdf", ".doc", ".txt", ".xls"]):
                        filename = text_content.split("/")[-1] if "/" in text_content else text_content
                
                if filename:
                    return f"[Document: {filename}]"
                else:
                    return "[Document]"
                    
            elif message_type == "video":
                # Check for caption in text field
                caption = original_message.get("text", "")
                if caption:
                    return f"[Video with caption: '{caption}']"
                else:
                    return "[Video (no caption)]"
                    
            elif message_type == "location":
                return "[Location shared]"
                
            elif message_type == "contact":
                return "[Contact shared]"
                
            elif message_type == "sticker":
                return "[Sticker]"
                
            else:
                # Unknown or new message type
                return f"[{message_type.title()} message]"
                
    except Exception as e:
        logger.exception(f"[REPLY_CONTEXT] Error retrieving original message: {e}")
        return ""
    
    return ""

# In-memory buffer for incoming messages
messages_buffer = {}

async def get_pre_live_history(wa_id: str, before_date: datetime) -> list:
    """
    Retrieves all conversation history before a specific go-live date.

    It fetches pages from the WATI API and stops as soon as it encounters a message
    on or after the go-live date, making it efficient.

    Args:
        wa_id: The WhatsApp ID of the user.
        before_date: The cutoff date. Only messages before this date will be returned.

    Returns:
        A list of all historical message objects, sorted chronologically.
    """
    all_pre_live_messages = []
    page_number = 1
    PAGE_SIZE = 100

    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            try:
                logger.info(f"[HISTORY_IMPORT] Fetching page {page_number} for {wa_id}...")
                wati_url = f"{config.WATI_API_URL}/api/v1/getMessages/{wa_id}?pageSize={PAGE_SIZE}&pageNumber={page_number}"
                headers = {"Authorization": f"Bearer {config.WATI_API_KEY}"}
                response = await client.get(wati_url, headers=headers)

                if response.status_code == 429:
                    logger.warning(f"[HISTORY_IMPORT] Rate limit hit. Waiting 30 seconds...")
                    await asyncio.sleep(30)
                    continue
                
                response.raise_for_status()
                response_data = response.json()
                # WATI API can return a "result" field with 0, which is not an error
                # but can be misinterpreted. We only care about the 'messages' part.
                messages_data = response_data.get("messages", {})
                if not isinstance(messages_data, dict):
                    logging.warning(f"[HISTORY_IMPORT] 'messages' field is not a dictionary for {wa_id}. Value: {messages_data}")
                    messages = []
                else:
                    messages = messages_data.get("items", [])

                if not messages:
                    logger.info(f"[HISTORY_IMPORT] No more messages found for {wa_id}. Stopping fetch.")
                    break

                all_pre_live_messages.extend(messages)
                page_number += 1

                # Stop fetching if we've gone back far enough to be sure we have all relevant history
                if page_number > 20: # Stop after 20 pages (2000 messages) as a safeguard
                    logger.warning(f"[HISTORY_IMPORT] Reached 20-page limit for {wa_id}. Stopping to avoid excessive fetching.")
                    break

                await asyncio.sleep(1)  # Be respectful to the API

            except httpx.HTTPStatusError as e:
                logger.error(f"[HISTORY_IMPORT] HTTP error fetching history: {e}")
                break
            except Exception as e:
                logger.error(f"[HISTORY_IMPORT] An unexpected error occurred: {e}")
                break

    # Filter one last time to be safe and return chronological
    final_list = []
    for m in all_pre_live_messages:
        try:
            timestamp_str = m.get('created', '')
            if not timestamp_str:
                continue

            # WATI timestamps can have variable-length fractional seconds, which older
            # Python versions' fromisoformat can't handle. It also doesn't like the 'Z'.
            # We need to normalize it to 6-digit microseconds.
            ts_str = m.get('created', '').replace('Z', '')
            if '+' in ts_str:
                ts_str = ts_str.split('+')[0]

            if '.' in ts_str:
                parts = ts_str.split('.')
                main_part = parts[0]
                frac_part = parts[1]
                # Pad with zeros to make it 6 digits (microseconds) and truncate if longer
                frac_part = frac_part.ljust(6, '0')[:6]
                ts_str = f"{main_part}.{frac_part}"
            
            message_date = datetime.fromisoformat(ts_str)

            if message_date < before_date:
                final_list.append(m)
        except (ValueError, TypeError) as e:
            logger.warning(f"[HISTORY_IMPORT] Could not parse date for message: {m.get('id', 'N/A')}. Timestamp: '{m.get('created', '')}'. Error: {e}")
            continue
    return final_list

# In-memory tracking for message buffering and timers
waid_timers = {}
waid_last_message = {}

# Thread-safe lock for timer management (prevents race conditions)
timer_lock = threading.Lock()

# Thread-safe lock for conversation access (prevents OpenAI conversation race conditions)
conversation_locks = {}
conversation_lock = threading.Lock()

# ManyChat tracking (separate from WATI)
mc_timers = {}
mc_last_message = {}
mc_timer_lock = threading.Lock()

# Initialize thread store DB and message buffer DB on startup
@app.on_event("startup")
def startup_event():
    # CRITICAL: Environment validation - Prevent wrong venv usage
    import sys
    import playwright
    
    expected_venv = "/home/robin/watibot4/venv"
    actual_python = sys.executable
    playwright_path = playwright.__file__
    
    # Check if running with correct Python executable
    if expected_venv not in actual_python:
        logger.error("="*80)
        logger.error("🚨 CRITICAL ENVIRONMENT MISMATCH DETECTED!")
        logger.error(f"❌ Expected: Python from {expected_venv}")
        logger.error(f"❌ Actual:   {actual_python}")
        logger.error(f"❌ Playwright from: {playwright_path}")
        logger.error("="*80)
        logger.error("🛑 STOPPING SERVICE - Must be started with correct environment!")
        logger.error("✅ Correct command: cd /home/robin/watibot4 && source venv/bin/activate && uvicorn app.main:app --host 0.0.0.0 --port 8006")
        logger.error("="*80)
        raise RuntimeError("Environment mismatch detected - service cannot start with wrong virtual environment")
    
    logger.info(f"✅ Environment validation passed - Python: {actual_python}")
    logger.info(f"✅ Playwright location: {playwright_path}")
    
    thread_store.init_db()
    message_buffer.init_message_buffer_db()
    
    # Clean up stale locks from crashed workers
    logger.info("[STARTUP] Cleaning up stale processing locks...")
    stale_count = message_buffer.cleanup_stale_locks(max_age_minutes=10)
    if stale_count > 0:
        logger.warning(f"[STARTUP] Cleaned up {stale_count} stale processing locks")
    
    # Clean up old buffered messages (older than 5 minutes)
    # These are from previous service runs where timers were killed mid-flight
    logger.info("[STARTUP] Cleaning up old buffered messages...")
    cleaned_count = message_buffer.cleanup_old_buffered_messages(max_age_minutes=5)
    if cleaned_count > 0:
        logger.info(f"[STARTUP] Cleaned up {cleaned_count} old buffered messages")
    
    logger.info("[STARTUP] Initialization complete")

def timer_callback(wa_id, timer_start_time=None, previous_webhook_timestamp=None, previous_last_updated=None):
    time.sleep(5)  # Add a 5-second delay to mitigate race condition with WATI DB
    # Wait 60 seconds to gather all messages
    threading.Event().wait(60)
    
    # Calculate exact buffer window based on when the timer started
    if timer_start_time:
        # Get all messages since the timer started (plus a small safety buffer)
        time_elapsed = (datetime.utcnow() - timer_start_time).total_seconds()
        buffer_window = int(time_elapsed) + 5  # Add 5 second safety buffer
        logger.info(f"[BUFFER] Timer for {wa_id} started at {timer_start_time}, using buffer window of {buffer_window}s")
    else:
        # Fallback to old logic
        buffer_window = 75  # 65 + 10 safety buffer
        logger.info(f"[BUFFER] No timer start time for {wa_id}, using fallback buffer window of {buffer_window}s")
    
    # Gather all messages buffered in the calculated window
    buffered_messages = message_buffer.get_and_clear_buffered_messages(wa_id, since_seconds=buffer_window)
    if not buffered_messages:
        logger.info(f"[BUFFER] No messages to process for {wa_id}")
        with timer_lock:
            waid_timers.pop(wa_id, None)
        return

    processed_messages = []
    # Use a new event loop for async operations within this thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    for message in buffered_messages:
        msg_type = message.get('type')
        content = message.get('content')
        reply_context_id = message.get('reply_context_id')
        user_message = ''

        if msg_type == 'text':
            user_message = content
            
            # Enhance text message with reply context if available
            if reply_context_id:
                logger.info(f"[REPLY_CONTEXT] Text message has reply context: {reply_context_id}")
                try:
                    original_context = loop.run_until_complete(get_original_message_context(wa_id, reply_context_id))
                    if original_context:
                        user_message = f"(Customer is replying to: \"{original_context}\") {content}"
                        logger.info(f"[REPLY_CONTEXT] Enhanced text with context: {user_message[:200]}...")
                    else:
                        logger.info(f"[REPLY_CONTEXT] No context found for ID: {reply_context_id}")
                except Exception as e:
                    logger.warning(f"[REPLY_CONTEXT] Failed to retrieve context: {e}")
                    # Continue with original message
        
        elif msg_type == 'image':
            file_path = content
            caption = message.get('caption')
            # Check caption cache for additional metadata (caption may have been updated, and get reply_context)
            cache_data = get_caption_from_cache(file_path)
            if not caption and cache_data.get("caption"):
                caption = cache_data["caption"]
            reply_context_id = cache_data.get("reply_context_id")
            logger.info(f"[TIMER_CALLBACK] Processing buffered image: {file_path} (caption: {caption!r}, reply_context: {reply_context_id!r})")
            try:
                user_message = loop.run_until_complete(process_image_message(wa_id, file_path, caption, reply_context_id))
            except Exception as e:
                logger.exception(f"[TIMER_CALLBACK] Error processing image {file_path} for {wa_id}")
                user_message = "(User sent an image, but an error occurred during processing)"

        elif msg_type == 'audio':
            file_path = content
            logger.info(f"[TIMER_CALLBACK] Processing buffered audio: {file_path}")
            try:
                user_message = loop.run_until_complete(process_audio_message(file_path))
            except Exception as e:
                logger.exception(f"[TIMER_CALLBACK] Error processing audio {file_path} for {wa_id}")
                user_message = "(User sent a voice note, but an error occurred during processing)"

        if user_message:
            processed_messages.append(user_message)
    
    loop.close()

    if not processed_messages:
        logger.warning(f"[BUFFER] No processable message content found for {wa_id}, but customer sent messages - providing fallback response")
        # CRITICAL: Never abandon a customer - always provide some response
        # Check what types of messages were buffered to provide appropriate fallback
        message_types = [msg.get('type') for msg in buffered_messages]
        if 'image' in message_types:
            processed_messages.append("(User sent an image, but processing failed - please acknowledge and ask for clarification)")
        elif 'audio' in message_types:
            processed_messages.append("(User sent a voice message, but processing failed - please acknowledge and ask for clarification)")
        else:
            processed_messages.append("(User sent a message, but processing failed - please acknowledge and ask for clarification)")
        logger.info(f"[BUFFER] Applied fallback response for {wa_id}: {processed_messages[0]}")

    # Check if image was already responded to directly
    if processed_messages and processed_messages[0] == "__ALREADY_RESPONDED__":
        logger.info(f"[BUFFER] Image was already responded to directly, skipping get_openai_response for {wa_id}")
        # CRITICAL: Must clean up timer before returning to prevent zombie timer
        with timer_lock:
            waid_timers.pop(wa_id, None)
            
            # Check if new messages arrived while we were processing
            if message_buffer.has_buffered_messages(wa_id):
                logger.warning(f"[BUFFER] Orphaned messages detected for {wa_id} after direct image response - starting immediate processing")
                # Don't pass previous timestamps for orphaned messages (same conversation)
                t = threading.Thread(target=timer_callback, args=(wa_id, timer_start_time, None, None))
                t.daemon = True
                waid_timers[wa_id] = t
                t.start()
                logger.info(f"[TIMER_THREAD] Started new timer for orphaned messages (reusing original start time: {timer_start_time})")
            else:
                logger.info(f"[BUFFER] Timer cleanup complete for {wa_id} after direct image response, no orphaned messages")
                # CRITICAL: Release processing lock so other workers can handle next message from this customer
                message_buffer.release_processing_lock(wa_id)
        return
    
    prompt = "\n".join(processed_messages)
    logger.info(f"[BUFFER] Sending combined prompt for {wa_id}: {prompt!r}")
    try:
        thread_info = thread_store.get_thread_id(wa_id)
        thread_id = thread_info['thread_id'] if thread_info else None

        # --- ONE-TIME HISTORY IMPORT (SEQUENTIAL) --- #
        # DISABLED: History now handled by agent_context_injector.py
        if False and (not thread_info or not thread_info.get('history_imported')):
            logger.info(f"[HISTORY_IMPORT] History not imported for {wa_id}. Starting SEQUENTIAL import...")
            
            # Define the go-live date (naive, for direct comparison)
            GO_LIVE_DATE = datetime(2025, 7, 5)
            MESSAGE_LIMIT = 200

            history_import_success = False
            try:
                history_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(history_loop)
                # Fetch all messages before the go-live date
                all_pre_live_history = history_loop.run_until_complete(
                    get_pre_live_history(wa_id, before_date=GO_LIVE_DATE)
                )
                history_loop.close()

                if all_pre_live_history:
                    # Take the last MESSAGE_LIMIT messages (the most recent ones)
                    limited_history = all_pre_live_history[-MESSAGE_LIMIT:]
                    logger.info(f"[HISTORY_IMPORT] Found {len(all_pre_live_history)} total pre-live messages. Capping at {len(limited_history)}.")

                    # Format history into a single message
                    formatted_history = f"Este es el historial de conversación más reciente ({len(limited_history)} mensajes antes del 5 de Julio, 2025) para darte contexto:\n\n---"
                    for msg in limited_history:
                        try:
                            # Fix timestamp format - normalize microseconds to 6 digits
                            timestamp_str = msg.get('created', '')
                            if '.' in timestamp_str and not timestamp_str.endswith('Z'):
                                # Handle microseconds - pad to 6 digits if needed
                                parts = timestamp_str.split('.')
                                if len(parts) == 2:
                                    microseconds = parts[1].ljust(6, '0')[:6]  # Pad or truncate to 6 digits
                                    timestamp_str = f"{parts[0]}.{microseconds}"
                            
                            ts = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                            formatted_ts = ts.strftime('%Y-%m-%d %H:%M')
                            sender = "Usuario" if msg.get('eventType') == 'message' else "Asistente"
                            text = msg.get('text', '[Mensaje sin texto]')
                            formatted_history += f"\n[{formatted_ts}] {sender}: {text}"
                        except Exception as e:
                            logger.warning(f"[HISTORY_IMPORT] Could not format message: {msg}. Error: {e}")
                    formatted_history += "\n---"
                    
                    # Add the formatted history to the thread - WAIT FOR COMPLETION
                    if thread_id:
                        logger.info(f"[HISTORY_IMPORT] Injecting {len(limited_history)} pre-go-live messages into thread {thread_id} for {wa_id}")
                        injection_loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(injection_loop)
                        success = injection_loop.run_until_complete(openai_agent.add_message_to_thread(thread_id, formatted_history))
                        injection_loop.close()
                        if success:
                            thread_store.set_history_imported(wa_id)
                            history_import_success = True
                            logger.info(f"[HISTORY_IMPORT] Successfully imported and marked history for {wa_id}.")
                        else:
                            logger.error(f"[HISTORY_IMPORT] Failed to inject history for {wa_id}. Will not proceed with message processing.")
                            return  # Exit early if history import fails
                    else:
                        # No thread_id (new conversation with Responses API) - mark as imported since agent context injection handles history
                        logger.info(f"[HISTORY_IMPORT] No thread_id for {wa_id} (Responses API mode). Marking history as imported - agent context injection will handle context.")
                        thread_store.set_history_imported(wa_id)
                        history_import_success = True
                else:
                    logger.info(f"[HISTORY_IMPORT] No pre-live messages found for {wa_id}. Marking as imported.")
                    thread_store.set_history_imported(wa_id)
                    history_import_success = True
                    
            except Exception as e:
                logger.error(f"[HISTORY_IMPORT] Unexpected error during history import for {wa_id}: {e}")
                return  # Exit early if history import fails completely
            
            if not history_import_success:
                logger.error(f"[HISTORY_IMPORT] History import failed for {wa_id}. Aborting message processing to avoid race condition.")
                return
                
            logger.info(f"[HISTORY_IMPORT] History import completed successfully for {wa_id}. Now running agent context injection...")
            
            logger.info(f"[HISTORY_IMPORT] All context preparation completed for {wa_id}. Proceeding with message processing.")
        else:
            logger.info(f"[AGENT_CONTEXT] History already imported for {wa_id}. Agent context will be handled inline.")
        # --- END OF SEQUENTIAL HISTORY IMPORT --- #

        # CRITICAL FIX: Check for missed messages using PREVIOUS webhook timestamp
        # The timestamp was already updated in webhook handler, so we use the old one we saved
        missed_messages_prompt = ""
        if previous_webhook_timestamp:
            try:
                # Parse the timestamp string (datetime already imported at top of file)
                last_webhook_time = datetime.strptime(previous_webhook_timestamp, '%Y-%m-%d %H:%M:%S')
                current_time = datetime.utcnow()
                time_diff = (current_time - last_webhook_time).total_seconds()
                
                logger.info(f"[MISSED_MESSAGES_CHECK] {wa_id}: Previous webhook at {previous_webhook_timestamp}, time_diff={time_diff:.1f}s")
                
                # If more than 5 minutes, check for missed messages
                if time_diff > 300:
                    from agent_context_injector import get_missed_customer_agent_messages_for_developer_input
                    # Pass the PREVIOUS last_updated timestamp (before current response) as cutoff
                    missed_messages_prompt = get_missed_customer_agent_messages_for_developer_input(wa_id, previous_last_updated)
                    if missed_messages_prompt:
                        logger.info(f"[MISSED_MESSAGES_CHECK] Found {len(missed_messages_prompt)} chars of missed messages for {wa_id}")
                        # Prepend missed messages to prompt
                        prompt = missed_messages_prompt + "\n\n" + prompt
                    else:
                        logger.info(f"[MISSED_MESSAGES_CHECK] No missed messages found for {wa_id}")
                else:
                    logger.info(f"[MISSED_MESSAGES_CHECK] Less than 5 minutes since previous message for {wa_id}")
            except Exception as e:
                logger.exception(f"[MISSED_MESSAGES_CHECK] Error checking missed messages for {wa_id}: {e}")
        else:
            logger.info(f"[MISSED_MESSAGES_CHECK] No previous webhook timestamp for {wa_id} (first message)")
        
        # Implement exponential backoff retry logic with 15-minute timeout and escalation
        start_time = time.time()
        max_duration_seconds = 900  # 15 minutes
        attempt = 0
        base_delay = 10  # Initial delay in seconds
        max_delay = 120  # Cap individual delays at 2 minutes
        ai_response = None
        new_thread_id = None
        
        while (time.time() - start_time) < max_duration_seconds:
            attempt += 1
            elapsed = time.time() - start_time
            
            try:
                logger.info(f"[BUFFER] Attempt {attempt} to get OpenAI response for {wa_id} (elapsed: {elapsed:.1f}s)")
                aio_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(aio_loop)
                ai_response, new_thread_id = aio_loop.run_until_complete(openai_agent.get_openai_response(prompt, thread_id, wa_id))
                aio_loop.close()
                logger.info(f"[BUFFER] OpenAI response for {wa_id}: {ai_response!r}")
                
                if not thread_id or (new_thread_id and new_thread_id != thread_id):
                    thread_store.set_thread_id(wa_id, new_thread_id)
                
                # Send response via WATI (also with retry logic)
                send_success = False
                for send_attempt in range(1, 4):  # 3 attempts for sending
                    try:
                        logger.info(f"[BUFFER] Attempt {send_attempt}/3 to send WATI message to {wa_id}")
                        asyncio.run(wati_client.send_wati_message(wa_id, ai_response))
                        send_success = True
                        break
                    except Exception as send_error:
                        logger.warning(f"[BUFFER] WATI send attempt {send_attempt} failed for {wa_id}: {send_error}")
                        if send_attempt < 3:
                            time.sleep(5)
                        else:
                            logger.error(f"[BUFFER] Failed to send WATI message after 3 attempts for {wa_id}")
                            raise
                
                if send_success:
                    logger.info(f"[BUFFER] Successfully processed and sent response to {wa_id} after {attempt} attempts")
                    break  # Success! Exit retry loop
                    
            except Exception as e:
                logger.exception(f"[BUFFER] Attempt {attempt} failed for {wa_id} after {elapsed:.1f}s: {e}")
                
                # Calculate exponential backoff delay
                delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                remaining_time = max_duration_seconds - elapsed
                
                if remaining_time > delay:
                    logger.info(f"[BUFFER] Retrying in {delay} seconds for {wa_id} (time remaining: {remaining_time:.1f}s)...")
                    time.sleep(delay)
                else:
                    logger.warning(f"[BUFFER] Not enough time for another retry. Breaking retry loop for {wa_id}")
                    break
        
        # If we've exhausted 15 minutes without success, escalate
        final_elapsed = time.time() - start_time
        if not ai_response and final_elapsed >= max_duration_seconds:
            logger.critical(f"[BUFFER] ESCALATION: Failed to process message for {wa_id} after {final_elapsed:.1f}s. Marking as PENDING.")
            
            try:
                # Use the existing WATI API to mark conversation as PENDING for human review
                asyncio.run(wati_client.update_chat_status(wa_id, "PENDING"))
                logger.info(f"[BUFFER] Successfully marked conversation as PENDING for {wa_id}")
                
                # Send polite escalation message to customer
                escalation_message = (
                    "Su mensaje ha sido recibido y está siendo procesado por nuestro equipo. "
                    "Le responderemos a la brevedad posible. Gracias por su paciencia."
                )
                asyncio.run(wati_client.send_wati_message(wa_id, escalation_message))
                logger.info(f"[BUFFER] Sent escalation notification to {wa_id}")
                    
            except Exception as escalation_error:
                logger.critical(f"[BUFFER] Failed to escalate conversation for {wa_id}: {escalation_error}")
                # Even if escalation fails, try to notify customer
                try:
                    emergency_message = (
                        "Estamos experimentando dificultades técnicas. Por favor, "
                        "contacte directamente con nuestras oficinas para asistencia inmediata."
                    )
                    asyncio.run(wati_client.send_wati_message(wa_id, emergency_message))
                except:
                    logger.critical(f"[BUFFER] Complete failure - unable to notify {wa_id}")
                        
    except Exception as e:
        logger.critical(f"[BUFFER] Unexpected error in buffer processing for {wa_id}: {e}")
    
    # CRITICAL FIX: Check for orphaned messages before cleaning up timer
    # Messages that arrived after we called get_and_clear_buffered_messages() 
    # but before processing completed need to be handled
    with timer_lock:
        waid_timers.pop(wa_id, None)
        
        # Check if new messages arrived while we were processing
        if message_buffer.has_buffered_messages(wa_id):
            logger.warning(f"[BUFFER] Orphaned messages detected for {wa_id} - starting immediate processing")
            # CRITICAL: Use the ORIGINAL timer_start_time so the buffer window 
            # includes messages that were buffered during the previous processing
            # This prevents orphaned messages from being outside the time window
            # Don't pass previous timestamps for orphaned messages (same conversation)
            t = threading.Thread(target=timer_callback, args=(wa_id, timer_start_time, None, None))
            t.daemon = True
            waid_timers[wa_id] = t
            t.start()
            logger.info(f"[TIMER_THREAD] Started new timer for orphaned messages (reusing original start time: {timer_start_time})")
        else:
            logger.info(f"[BUFFER] Timer cleanup complete for {wa_id}, no orphaned messages")
            # CRITICAL: Release processing lock so other workers can handle next message from this customer
            message_buffer.release_processing_lock(wa_id)


def manychat_timer_callback(conversation_id: str, channel: str, user_id: str, timer_start_time=None):
    """Aggregates buffered ManyChat messages and sends AI response via appropriate adapter.

    conversation_id is formatted as "{channel}:{user_id}" to avoid collisions with WATI keys.
    """
    # Wait 60 seconds to gather messages similar to WATI behavior
    threading.Event().wait(60)

    # Calculate buffer window similar to WATI logic
    if timer_start_time:
        time_elapsed = (datetime.utcnow() - timer_start_time).total_seconds()
        buffer_window = int(time_elapsed) + 5  # Add 5 second safety buffer
        logging.info(f"[MC_BUFFER] Timer for {conversation_id} started at {timer_start_time}, using buffer window of {buffer_window}s")
    else:
        buffer_window = 75  # Fallback
        logging.info(f"[MC_BUFFER] No timer start time for {conversation_id}, using fallback buffer window of {buffer_window}s")

    buffered_messages = message_buffer.get_and_clear_buffered_messages(conversation_id, since_seconds=buffer_window)
    if not buffered_messages:
        logging.info(f"[MC_BUFFER] No messages to process for {conversation_id}")
        with mc_timer_lock:
            mc_timers.pop(conversation_id, None)
        return

    # Convert all buffered messages into plain text lines for the AI prompt
    # For ManyChat, mirror WATI behavior: process audio (transcribe) during timer.
    lines = []
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        for m in buffered_messages:
            mtype = (m.get('type') or 'text').lower()
            content = m.get('content') or ''
            if mtype == 'text':
                lines.append(content)
            elif mtype == 'image' and content:
                try:
                    analyzed = loop.run_until_complete(process_manychat_image_message(content))
                    lines.append(analyzed)
                except Exception:
                    logger.exception(f"[MC_BUFFER] Error processing image for {conversation_id}")
                    lines.append("(User sent an image, but an error occurred during processing)")
            elif mtype == 'audio' and content:
                try:
                    transcribed = loop.run_until_complete(process_manychat_audio_message(content))
                    lines.append(transcribed)
                except Exception:
                    logger.exception(f"[MC_BUFFER] Error transcribing audio for {conversation_id}")
                    lines.append("(User sent a voice note, but an error occurred during processing)")
            else:
                # Include URL/path if present to give the AI some context for other media types
                if content:
                    lines.append(f"(User sent a {mtype}: {content})")
                else:
                    lines.append(f"(User sent a {mtype})")
    finally:
        try:
            loop.close()
        except Exception:
            pass

    prompt = "\n".join(lines)
    logging.info(f"[MC_BUFFER] Sending combined prompt for {conversation_id}: {prompt!r}")

    try:
        # Maintain independent threads per conversation_id
        thread_info = thread_store.get_thread_id(conversation_id)
        thread_id = thread_info['thread_id'] if thread_info else None

        # Call OpenAI agent to get response (no phone_number for ManyChat)
        aio_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(aio_loop)
        ai_response, new_thread_id = aio_loop.run_until_complete(
            openai_agent.get_openai_response(
                prompt,
                thread_id,
                None,  # phone_number not used for ManyChat
                subscriber_id=user_id,
                channel=channel,
            )
        )
        aio_loop.close()

        if not thread_id or (new_thread_id and new_thread_id != thread_id):
            thread_store.set_thread_id(conversation_id, new_thread_id)

        # Choose adapter per channel
        adapter = ManyChatFBAdapter() if channel == 'facebook' else ManyChatIGAdapter()

        # Try to send the AI response up to 3 times
        send_success = False
        for attempt in range(1, 4):
            try:
                send_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(send_loop)
                ok = send_loop.run_until_complete(adapter.send_outgoing(user_id, ai_response))
                send_loop.close()
                if ok:
                    send_success = True
                    break
                else:
                    raise Exception("send_outgoing returned False")
            except Exception as send_err:
                logging.warning(f"[MC_BUFFER] Send attempt {attempt} failed for {conversation_id}: {send_err}")
                if attempt < 3:
                    time.sleep(5)

        if send_success:
            logging.info(f"[MC_BUFFER] Successfully processed and sent response to {conversation_id}")
        else:
            logging.error(f"[MC_BUFFER] Failed to send response for {conversation_id} after retries")
    except Exception as e:
        logging.exception(f"[MC_BUFFER] Unexpected error in buffer processing for {conversation_id}: {e}")
    finally:
        # CRITICAL FIX: Check for orphaned messages before cleaning up timer
        with mc_timer_lock:
            mc_timers.pop(conversation_id, None)
            
            # Check if new messages arrived while we were processing
            if message_buffer.has_buffered_messages(conversation_id):
                logging.warning(f"[MC_BUFFER] Orphaned messages detected for {conversation_id} - starting immediate processing")
                # CRITICAL: Use the ORIGINAL timer_start_time so the buffer window 
                # includes messages that were buffered during the previous processing
                t = threading.Thread(target=manychat_timer_callback, args=(conversation_id, channel, user_id, timer_start_time))
                t.daemon = True
                mc_timers[conversation_id] = t
                t.start()
                logging.info(f"[MC_TIMER] Started new timer for orphaned messages (reusing original start time: {timer_start_time})")
            else:
                logging.info(f"[MC_BUFFER] Timer cleanup complete for {conversation_id}, no orphaned messages")

default_message = {
    "message": "WATI-OpenAI integration service running. Configure webhook endpoint next."
}

# Periodic cleanup task for caption cache
def periodic_cache_cleanup():
    """Background thread that cleans up expired cache entries every 60 seconds."""
    while True:
        try:
            time.sleep(60)  # Run every minute
            cleanup_expired_cache()
        except Exception as e:
            logger.exception(f"[CAPTION_CACHE] Error in periodic cleanup: {e}")

# Start the periodic cleanup thread
cache_cleanup_thread = threading.Thread(target=periodic_cache_cleanup, daemon=True)
cache_cleanup_thread.start()
logger.info("[CAPTION_CACHE] Started periodic cleanup thread")

@app.get("/")
def root():
    return default_message

@app.post("/manychat/webhook")
async def manychat_webhook(request: Request):
    """Unified ManyChat webhook handler for Facebook and Instagram with passkey authentication.

    - Validates passkey authentication first
    - Detects channel via `detect_channel`.
    - Parses payload via channel-specific adapter into UnifiedMessage.
    - Buffers messages by conversation_id and triggers timer processing.
    """
    body = await request.body()
    logger.info(f"[MC] RAW REQUEST BODY: {body!r}")

    # Best-effort JSON parse (with fallback to form)
    try:
        data = await request.json()
    except Exception:
        try:
            form = await request.form()
            data = dict(form)
        except Exception:
            logger.error(f"[MC] Could not parse request body: {body!r}")
            return {"status": "error", "detail": "Could not parse body as JSON or form data"}

    # SECURITY: Validate passkey authentication - will trigger fail2ban on failure
    security.validate_webhook_auth(request, data)

    # Detect the channel
    try:
        channel = detect_channel(data)
    except Exception:
        logger.warning("[MC] Unknown channel for payload, ignoring")
        return {"status": "ignored"}

    if channel not in ("facebook", "instagram"):
        # Let WATI or other handlers process
        return {"status": "ignored"}

    # Pick adapter and parse
    adapter = ManyChatFBAdapter() if channel == "facebook" else ManyChatIGAdapter()
    unified_msg = await adapter.parse_incoming(data)
    if not unified_msg:
        return {"status": "ignored"}

    conversation_id = f"{unified_msg.channel}:{unified_msg.user_id}"

    # Buffer raw message content first; processing occurs in the timer callback
    msg_type = unified_msg.message_type.value
    if msg_type == 'text':
        content = unified_msg.content
        caption = None
    else:
        # For media (images, audio, etc.), content is the media_url, caption is the text
        content = unified_msg.media_url or ''
        caption = unified_msg.content if unified_msg.content else None
    message_buffer.buffer_message(conversation_id, msg_type, content, caption)
    
    # Track webhook message timestamp for missed message detection
    thread_store.update_last_webhook_timestamp(conversation_id)

    # Start a timer for this conversation if not running
    now = datetime.utcnow()
    with mc_timer_lock:
        if conversation_id not in mc_timers:
            timer_start_time = now
            t = threading.Thread(target=manychat_timer_callback, args=(conversation_id, unified_msg.channel, unified_msg.user_id, timer_start_time))
            t.daemon = True
            mc_timers[conversation_id] = t
            t.start()
            logger.info(f"[MC_TIMER] Started timer for {conversation_id} at {timer_start_time}")
        else:
            logger.info(f"[MC_TIMER] Timer already running for {conversation_id}, message buffered")

    return {"status": "ok", "detail": "Message buffered"}

@app.post("/webhook/universal")
async def universal_webhook(request: Request):
    """Universal WATI webhook for caching media captions and reply contexts.
    
    This endpoint receives ALL messages from WATI but only caches metadata for:
    - Media messages (type != "text"): Stores caption and reply context
    - Messages with reply context (replyContextId != null): Stores for context tracking
    
    The cached data is retrieved by the main webhook when processing images.
    Does NOT trigger bot processing - only stores metadata.
    """
    try:
        data = await request.json()
        
        # Extract key fields from WATI universal webhook payload
        message_type = data.get("type")  # "text", "image", "audio", etc.
        file_path = data.get("data")  # Media file path (e.g., "data/images/xxx.jpg")
        caption_text = data.get("text")  # Caption for media or message text
        reply_context_id = data.get("replyContextId")  # Reply context ID
        wa_id = data.get("waId")
        owner = data.get("owner", False)  # true = sent by bot, false = from customer
        operator_name = data.get("operatorName")  # Operator name if message is assigned to agent
        
        logger.info(f"[UNIVERSAL_WEBHOOK] Received: type={message_type}, file_path={file_path}, caption={caption_text!r}, reply_context={reply_context_id!r}, waId={wa_id}, owner={owner}, operatorName={operator_name}")
        
        # FILTER: Only process incoming customer messages (not bot's own messages)
        if owner:
            logger.info(f"[UNIVERSAL_WEBHOOK] Ignoring outgoing message (owner=true)")
            return {"status": "ignored", "reason": "outgoing_message"}
        
        # SAFETY NET: Track messages meant for bot to detect WATI network hiccups
        # If operatorName is None, this message should reach the main /webhook endpoint
        # Start a background task to forward if it doesn't arrive within 10 seconds
        if operator_name is None and wa_id and caption_text:
            message_key = generate_message_key(wa_id, caption_text or file_path or "", message_type)
            
            # Check if safety net thread already running for this message
            with active_safety_threads_lock:
                if message_key in active_safety_threads:
                    logger.info(f"[SAFETY_NET] Thread already running for message_key: {message_key}, skipping")
                    return {"status": "ignored", "reason": "safety_thread_already_active"}
            
            with pending_messages_lock:
                # Store message for tracking
                pending_messages[message_key] = {
                    "wa_id": wa_id,
                    "data": data.copy(),
                    "timestamp": time.time(),
                    "processed": False
                }
            
            # Start background safety net task (only if not already running)
            safety_thread = threading.Thread(
                target=safety_net_task,
                args=(wa_id, data.copy(), message_key),
                daemon=True
            )
            
            # Track the thread before starting it
            with active_safety_threads_lock:
                active_safety_threads[message_key] = safety_thread
            
            safety_thread.start()
            logger.info(f"[SAFETY_NET] Tracking message for bot: wa_id={wa_id}, message_key={message_key} (total active: {len(active_safety_threads)})")
        
        # FILTER: Cache media messages OR messages with reply context
        should_cache = False
        cache_reason = []
        
        if message_type and message_type != "text" and file_path:
            # Media message: cache caption and reply context
            should_cache = True
            cache_reason.append("media_message")
        
        if reply_context_id:
            # Message with reply context: cache it
            should_cache = True
            cache_reason.append("has_reply_context")
        
        if should_cache and file_path:
            # Store in cache with 300s TTL (normalization happens inside)
            normalized = normalize_file_path(file_path)
            store_caption_cache(file_path, caption_text, reply_context_id)
            logger.info(f"[UNIVERSAL_WEBHOOK] Cached metadata: original={file_path}, normalized={normalized}, reasons={cache_reason}")
            return {"status": "cached", "reason": ",".join(cache_reason)}
        else:
            logger.info(f"[UNIVERSAL_WEBHOOK] Not caching: type={message_type}, has_file_path={bool(file_path)}")
            return {"status": "ignored", "reason": "no_cache_needed"}
    
    except Exception as e:
        logger.exception(f"[UNIVERSAL_WEBHOOK] Error processing webhook")
        # Always return 200 to prevent WATI retries
        return {"status": "error", "detail": str(e)}

@app.post("/webhook")
async def wati_webhook(request: Request):
    """WATI webhook handler with passkey authentication for watibot4.
    
    - Validates passkey authentication first  
    - Processes WATI WhatsApp messages
    - Buffers messages and triggers AI processing
    """
    # Clean up old threads before processing
    # thread_store.delete_old_threads()  # Commented out to preserve conversation history indefinitely
    body = await request.body()
    logger.info(f"RAW REQUEST BODY: {body!r}")
    logger.info(f"HEADERS: {dict(request.headers)}")
    logger.info(f"QUERY PARAMS: {dict(request.query_params)}")

    try:
        # Special case: if WATI sends 'null', try to extract phone number from headers or query params
        if body.strip() == b'null':
            phone_number = request.query_params.get('waId') or request.query_params.get('phone')
            if not phone_number:
                phone_number = request.headers.get('waid') or request.headers.get('phone')
            if not phone_number:
                phone_number = "test"
            message = "hola"
            # Create minimal data structure for authentication check
            data = {"waId": phone_number, "text": message}
            logger.info(f"[DEBUG] Using phone_number='{phone_number}' for null payload")
        else:
            try:
                data = await request.json()
            except Exception as e_json:
                try:
                    form = await request.form()
                    data = dict(form)
                except Exception as e_form:
                    logger.error(f"Could not parse request body: {body!r}")
                    logger.exception(e_form)
                    data = None

            if not data:
                logger.error("Could not parse body as JSON or form data")
                return {"status": "error", "detail": "Could not parse body as JSON or form data"}

        # EARLY LOGGING: Log message details BEFORE security check for debugging
        # This ensures we have a record even if authentication fails
        early_phone = data.get("waId") or data.get("phone") or "unknown"
        early_msg_type = data.get("type") or "unknown"
        early_text = (data.get("text") or data.get("userMessage") or "")[:100]
        logger.info(f"[WEBHOOK_RECEIVED] waId={early_phone}, type={early_msg_type}, text_preview={early_text!r}")

        # SECURITY: Validate passkey authentication - will trigger fail2ban on failure
        security.validate_webhook_auth(request, data)

        phone_number = data.get("waId") or data.get("phone")
        
        # WATI webhook structure: 'text' = caption, 'data' = media file path, 'type' = message type
        message_type_wati = data.get("type")  # "text", "image", "audio", etc.
        text_content = data.get("text")  # Caption for media or text for text messages
        media_path = data.get("data")  # File path for media messages
        
        # Fallback: Custom webhook format uses 'userMessage' and 'filePath'
        if not text_content and not media_path:
            message = data.get("message") or data.get("userMessage")
            file_path_custom = data.get("filePath")
        else:
            # Standard WATI webhook: determine message based on type
            if message_type_wati and message_type_wati != "text" and media_path:
                # Media message: use media path as message, text as caption
                message = media_path
            else:
                # Text message: use text content
                message = text_content
            file_path_custom = None
        
        reply_context_id = data.get("replyContextId")
        logger.info(f"PARSED DATA: {data}")
        # Truncate and escape message for single-line logging
        msg_preview = (message[:100] + '...') if len(message) > 100 else message
        msg_preview = msg_preview.replace('\n', ' ').replace('\r', '')
        logger.info(f"[DEBUG] Parsed waId: {phone_number}, message: {msg_preview!r}, replyContextId: {reply_context_id}, caption: {text_content if message != text_content else 'N/A'}")

        # SAFETY NET: Mark message as processed to prevent duplicate forwarding from universal webhook
        if phone_number and message:
            # Debug: Generate the same message_key to verify it matches universal webhook
            import hashlib
            test_key = f"{phone_number}:{hashlib.md5(f'{message}:{message_type_wati}'.encode()).hexdigest()[:12]}"
            logger.info(f"[SAFETY_NET_DEBUG] Attempting to mark processed: wa_id={phone_number}, message={message[:50]!r}, type={message_type_wati}, key={test_key}")
            mark_message_processed(phone_number, message, message_type_wati)

        if not phone_number:
            logger.error("Missing phone number in payload")
            return {"status": "error", "detail": "Missing phone number in payload"}
        if not message:
            logger.error("Missing message in payload")
            return {"status": "error", "detail": "Missing message in payload"}

        # NOTE: Agent context now handled inline in get_openai_response() via enhanced developer input

        # --- Reply Context Enhancement ---
        # REMOVED: This was blocking webhook 200 OK response by calling external WATI API
        # Reply context lookup now happens in timer_callback instead (non-blocking)
        # Store reply_context_id for later processing
        if reply_context_id:
            logger.info(f"[REPLY_CONTEXT] Reply context detected: {reply_context_id} (will be processed async)")

        # --- Message Buffering Logic ---
        # --- Message Buffering Logic (Resilient Version) ---
        message_type = None
        content = None
        caption = None

        # 1. Primary Check: Standard WATI webhook (type, data, text fields)
        if message_type_wati and media_path:
            # Standard WATI: media message with file path
            if message_type_wati in ('image', 'photo', 'document'):
                message_type = 'image'
                content = media_path
                caption = text_content  # text field contains caption
                logger.info(f"[WEBHOOK] Detected image via WATI standard webhook: {content} (caption: {caption!r})")
            elif message_type_wati in ('audio', 'voice'):
                message_type = 'audio'
                content = media_path
                logger.info(f"[WEBHOOK] Detected audio via WATI standard webhook: {content}")
        
        # 2. Fallback Check: Custom payload with dataType and filePath
        if not message_type:
            data_type = data.get('dataType', '').lower()
            file_path = data.get('filePath')

            if data_type in ('image', 'photo', 'document') and file_path:
                message_type = 'image'
                content = file_path
                caption = message if message and message != file_path else None
                logger.info(f"[WEBHOOK] Detected image/document via dataType: {content} (caption: {caption!r})")
            elif data_type == 'audio' and file_path:
                message_type = 'audio'
                content = file_path
                logger.info(f"[WEBHOOK] Detected audio via dataType: {content}")

        # 3. Legacy Check: File path embedded in message text
        if not message_type and isinstance(message, str):
            msg_strip = message.strip().lower()
            if 'data/images/' in msg_strip or any(msg_strip.endswith(ext) for ext in ('.jpg', '.jpeg', '.png', '.pdf')):
                message_type = 'image'
                content = message.strip()
                logger.info(f"[WEBHOOK] Detected image via message content: {content}")
            elif 'data/audios/' in msg_strip or msg_strip.endswith('.opus'):
                message_type = 'audio'
                content = message.strip()
                logger.info(f"[WEBHOOK] Detected audio via message content: {content}")

        # 3. Final Determination: If not media, it's text or should be ignored
        if not message_type:
            if message:
                message_type = 'text'
                content = message
                logger.info(f"[WEBHOOK] Detected text message for buffering.")
            else:
                logger.warning(f"[WEBHOOK] Ignoring message with no processable content. Payload: {data}")
                return {"status": "ok", "detail": "Message type ignored"}

        # 4. Caption Cache Lookup: For images, check universal webhook cache
        cached_reply_context_id = None
        if message_type == 'image' and content:
            cache_data = get_caption_from_cache(content)
            # Use cached caption if we don't have one from custom webhook
            if not caption and cache_data.get("caption"):
                caption = cache_data["caption"]
                logger.info(f"[WEBHOOK] Retrieved caption from cache: {caption!r}")
            # Always use cached reply context if available
            if cache_data.get("reply_context_id"):
                cached_reply_context_id = cache_data["reply_context_id"]
                logger.info(f"[WEBHOOK] Retrieved reply_context from cache: {cached_reply_context_id!r}")

        # Buffer the message with its type, content, optional caption, and reply context
        # Note: We store cached_reply_context_id separately to pass to image processor
        message_buffer.buffer_message(phone_number, message_type, content, caption, reply_context_id)
        logger.info(f"[WEBHOOK] Message buffered successfully for {phone_number}")
        
        # CRITICAL: Wrap timer creation in try/except to prevent orphaned messages
        # If ANY exception occurs after buffering, we must handle cleanup
        try:
            # CRITICAL: Get the OLD timestamps BEFORE updating them
            # This allows the timer to check if 5+ minutes passed since the PREVIOUS message
            # AND to filter missed messages from after the PREVIOUS assistant response
            old_webhook_timestamp = thread_store.get_last_webhook_timestamp(phone_number)
            old_last_updated = thread_store.get_last_updated_timestamp(phone_number)
            
            # Now update to current timestamp for next message
            thread_store.update_last_webhook_timestamp(phone_number)
            
            now = datetime.utcnow()
            waid_last_message[phone_number] = now
            
            # CRITICAL: Try to acquire processing lock from database
            # This ensures only ONE worker processes this customer at a time (linear conversation)
            # while allowing multiple workers to handle different customers concurrently
            lock_acquired = message_buffer.try_acquire_processing_lock(phone_number)
            
            if lock_acquired:
                # We got the lock - start timer for this customer
                with timer_lock:
                    timer_start_time = now
                    t = threading.Thread(target=timer_callback, args=(phone_number, timer_start_time, old_webhook_timestamp, old_last_updated))
                    t.daemon = True
                    # Add to local worker's dict
                    waid_timers[phone_number] = t
                    t.start()
                    logger.info(f"[TIMER_THREAD] Acquired lock and started timer for {phone_number} at {timer_start_time}")
            else:
                # Another worker is already processing this customer - just buffer the message
                logger.info(f"[TIMER_THREAD] Another worker is processing {phone_number}, message buffered (will be included in that batch)")
                    
        except Exception as timer_error:
            # CRITICAL: If timer creation fails, message is orphaned in buffer
            # Log the error prominently so it can be investigated
            logger.critical(f"[WEBHOOK] CRITICAL: Timer creation failed for {phone_number} after buffering message! Message is orphaned and will be processed on next startup. Error: {timer_error}")
            logger.exception(timer_error)
            # Note: We intentionally do NOT delete the buffered message here
            # The orphan detection on startup will process it
            # This ensures messages are never lost, only delayed
            
        # Immediately return a batching notice to WATI
        return {"ai_response": "Gathering questions for the assistant"}
    except Exception as e:
        logger.exception("Error in webhook handler")
        return {"status": "error", "detail": str(e)}

@app.get("/debug/check-orphaned-messages")
async def check_orphaned_messages():
    """Debug endpoint to manually check for and process orphaned messages.
    
    This is useful for debugging message drop issues without restarting the service.
    Returns a list of wa_ids with orphaned messages and triggers processing for them.
    """
    try:
        logger.info("[DEBUG] Manual orphan check triggered via /debug/check-orphaned-messages")
        orphaned_wa_ids = message_buffer.get_all_wa_ids_with_buffered_messages()
        
        if not orphaned_wa_ids:
            logger.info("[DEBUG] No orphaned messages found")
            return {
                "status": "ok",
                "orphaned_count": 0,
                "orphaned_wa_ids": [],
                "message": "No orphaned messages detected"
            }
        
        logger.warning(f"[DEBUG] Found {len(orphaned_wa_ids)} wa_ids with orphaned messages: {orphaned_wa_ids}")
        
        # Trigger processing for each orphaned wa_id
        processed = []
        for wa_id in orphaned_wa_ids:
            # Check if timer is already running for this wa_id
            with timer_lock:
                if wa_id not in waid_timers:
                    logger.info(f"[DEBUG] Starting immediate processing for orphaned messages: {wa_id}")
                    now = datetime.utcnow()
                    t = threading.Thread(target=timer_callback, args=(wa_id, now, None, None))
                    t.daemon = True
                    waid_timers[wa_id] = t
                    t.start()
                    processed.append(wa_id)
                else:
                    logger.info(f"[DEBUG] Timer already running for {wa_id}, skipping")
        
        return {
            "status": "ok",
            "orphaned_count": len(orphaned_wa_ids),
            "orphaned_wa_ids": orphaned_wa_ids,
            "processed": processed,
            "message": f"Found and triggered processing for {len(processed)} orphaned conversations"
        }
        
    except Exception as e:
        logger.exception("[DEBUG] Error checking orphaned messages")
        return {
            "status": "error",
            "error": str(e)
        }
