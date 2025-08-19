import httpx
import os
import tempfile
import subprocess
import logging
import asyncio
from google.cloud import speech
from google.oauth2 import service_account

# Import config for feature flag
from . import config

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_WHISPER_URL = "https://api.openai.com/v1/audio/transcriptions"
logger = logging.getLogger(__name__)

def _initialize_speech_client():
    """Initialize Google Cloud Speech-to-Text client"""
    try:
        if config.GOOGLE_APPLICATION_CREDENTIALS:
            credentials = service_account.Credentials.from_service_account_file(
                config.GOOGLE_APPLICATION_CREDENTIALS
            )
            client = speech.SpeechClient(credentials=credentials)
        else:
            client = speech.SpeechClient()  # Use default credentials
        
        logger.info(f"[SPEECH_CLIENT] Google Cloud Speech-to-Text initialized")
        return client
    except Exception as e:
        logger.error(f"[SPEECH_CLIENT] Failed to initialize Speech client: {e}")
        return None

async def transcribe_audio_opus(file_path: str, language: str = "es") -> str:
    """Transcribe an opus audio file using OpenAI Whisper or Google Cloud Speech-to-Text based on USE_VERTEX_AI flag. Converts to wav before upload. Returns transcribed text or raises Exception."""
    # Convert opus to wav using ffmpeg (required for both OpenAI and Google Cloud)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as wav_tmpfile:
        wav_path = wav_tmpfile.name
    try:
        ffmpeg_cmd = [
            "/usr/bin/ffmpeg", "-y", "-i", file_path, "-ar", "16000", "-ac", "1", wav_path
        ]
        result = subprocess.run(ffmpeg_cmd, capture_output=True)
        if result.returncode != 0:
            logger.error(f"[SPEECH_CLIENT] ffmpeg conversion failed: {result.stderr.decode(errors='ignore')}")
            raise Exception("Failed to convert opus to wav for speech transcription.")
        
        # Route to appropriate transcription service based on feature flag
        if config.USE_VERTEX_AI:
            return await _transcribe_with_google_speech(wav_path, language)
        else:
            return await _transcribe_with_openai_whisper(wav_path, language)
    finally:
        # Clean up temporary file
        if os.path.exists(wav_path):
            os.unlink(wav_path)

async def _transcribe_with_openai_whisper(wav_path: str, language: str = "es") -> str:
    """Transcribe using OpenAI Whisper API"""
    try:
        logger.info(f"[SPEECH_CLIENT] Using OpenAI Whisper for transcription")
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
    except Exception as e:
        logger.error(f"[SPEECH_CLIENT] OpenAI Whisper transcription failed: {e}")
        raise Exception(f"OpenAI Whisper transcription failed: {str(e)}")

async def _transcribe_with_google_speech(wav_path: str, language: str = "es") -> str:
    """Transcribe using Google Cloud Speech-to-Text API"""
    try:
        logger.info(f"[SPEECH_CLIENT] Using Google Cloud Speech-to-Text for transcription")
        
        # Initialize Speech client
        client = _initialize_speech_client()
        if not client:
            raise Exception("Failed to initialize Google Cloud Speech client")
        
        # Read audio file
        with open(wav_path, "rb") as audio_file:
            audio_content = audio_file.read()
        
        # Configure audio and recognition settings
        audio = speech.RecognitionAudio(content=audio_content)
        
        # Map language codes (es -> es-ES for Spanish)
        if language == "es":
            language_code = "es-ES"
        elif language == "en":
            language_code = "en-US"
        else:
            language_code = language
        
        config_speech = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=16000,
            language_code=language_code,
            audio_channel_count=1,
            enable_automatic_punctuation=True,
            use_enhanced=True,  # Use enhanced model for better accuracy
            model='latest_long',  # Use latest long model for voice messages
        )
        
        # Perform transcription with timeout and retry logic
        max_retries = 3
        for attempt in range(max_retries):
            try:
                logger.info(f"[SPEECH_CLIENT] Attempting transcription (attempt {attempt + 1}/{max_retries})")
                
                # Run transcription in thread pool since it's blocking
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None, 
                    lambda: client.recognize(config=config_speech, audio=audio)
                )
                
                # Extract transcription text
                if response.results:
                    transcript = ""
                    for result in response.results:
                        if result.alternatives:
                            transcript += result.alternatives[0].transcript
                    
                    if transcript.strip():
                        logger.info(f"[SPEECH_CLIENT] Google Cloud transcription successful: '{transcript[:50]}...'")
                        return transcript.strip()
                    else:
                        raise Exception("Empty transcription result")
                else:
                    raise Exception("No transcription results returned")
                    
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    logger.warning(f"[SPEECH_CLIENT] Attempt {attempt + 1} failed: {e}. Retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    raise
        
        raise Exception("Max transcription retries exceeded")
        
    except Exception as e:
        logger.error(f"[SPEECH_CLIENT] Google Cloud Speech transcription failed: {e}")
        raise Exception(f"Google Cloud Speech transcription failed: {str(e)}")
