import httpx
import os
import tempfile
import subprocess
import logging

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_WHISPER_URL = "https://api.openai.com/v1/audio/transcriptions"
logger = logging.getLogger(__name__)

async def transcribe_audio_opus(file_path: str, language: str = "es") -> str:
    """Transcribe an opus audio file using OpenAI Whisper API. Converts to wav before upload. Returns transcribed text or raises Exception."""
    # Convert opus to wav using ffmpeg
    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as wav_tmpfile:
        wav_path = wav_tmpfile.name
    try:
        ffmpeg_cmd = [
            "/usr/bin/ffmpeg", "-y", "-i", file_path, "-ar", "16000", "-ac", "1", wav_path
        ]
        result = subprocess.run(ffmpeg_cmd, capture_output=True)
        if result.returncode != 0:
            logger.error(f"[WHISPER] ffmpeg conversion failed: {result.stderr.decode(errors='ignore')}")
            raise Exception("Failed to convert opus to wav for Whisper upload.")
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
        }
        files = {
            "file": (os.path.basename(wav_path), open(wav_path, "rb"), "audio/wav"),
        }
        data = {
            "model": "whisper-1",
            "response_format": "text",
            "language": language,
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(OPENAI_WHISPER_URL, headers=headers, data=data, files=files, timeout=60)
            if response.status_code != 200:
                logger.error(f"[WHISPER] Whisper API error {response.status_code}: {response.text}")
                response.raise_for_status()
            return response.text.strip()
    finally:
        try:
            os.remove(wav_path)
        except Exception as e:
            logger.warning(f"[WHISPER] Could not delete temp wav file: {wav_path}")
