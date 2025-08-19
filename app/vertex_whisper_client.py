"""
Vertex AI Speech-to-Text Client - Migration target for OpenAI Whisper
Implements equivalent functionality using Google Cloud Speech-to-Text API
"""

import os
import tempfile
import subprocess
import logging
from google.cloud import speech
import vertexai

from . import config

logger = logging.getLogger(__name__)

def _initialize_speech_client():
    """Initialize Google Cloud Speech-to-Text client"""
    try:
        if config.GOOGLE_APPLICATION_CREDENTIALS:
            os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = config.GOOGLE_APPLICATION_CREDENTIALS
        
        client = speech.SpeechClient()
        logger.info("[VERTEX_SPEECH] Initialized Google Cloud Speech-to-Text client")
        return client
    except Exception as e:
        logger.exception(f"[VERTEX_SPEECH] Failed to initialize Speech client: {e}")
        return None

async def transcribe_audio_opus(file_path: str, language: str = "es") -> str:
    """
    Transcribe an opus audio file using Google Cloud Speech-to-Text API.
    Converts to wav before upload. Returns transcribed text or raises Exception.
    
    Args:
        file_path: Path to the opus audio file
        language: Language code (default: "es" for Spanish)
        
    Returns:
        str: Transcribed text
        
    Raises:
        Exception: If transcription fails
    """
    try:
        logger.info(f"[VERTEX_SPEECH] Starting audio transcription for file: {file_path}")
        
        # Initialize Speech client
        client = _initialize_speech_client()
        if not client:
            raise Exception("Failed to initialize Google Cloud Speech-to-Text client")
        
        # Convert opus to wav using ffmpeg (same as OpenAI Whisper)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as wav_tmpfile:
            wav_path = wav_tmpfile.name
            
        try:
            ffmpeg_cmd = [
                "/usr/bin/ffmpeg", "-y", "-i", file_path, 
                "-ar", "16000",  # 16kHz sample rate (required by Speech-to-Text)
                "-ac", "1",      # Mono audio
                wav_path
            ]
            result = subprocess.run(ffmpeg_cmd, capture_output=True)
            if result.returncode != 0:
                logger.error(f"[VERTEX_SPEECH] ffmpeg conversion failed: {result.stderr.decode(errors='ignore')}")
                raise Exception("Failed to convert opus to wav for Speech-to-Text upload.")
            
            # Read the converted audio file
            with open(wav_path, "rb") as audio_file:
                audio_content = audio_file.read()
            
            # Configure recognition settings
            audio = speech.RecognitionAudio(content=audio_content)
            
            # Map language codes (Speech-to-Text uses different format)
            language_map = {
                "es": "es-ES",  # Spanish (Spain) - adjust as needed
                "en": "en-US",  # English (US)
                "pt": "pt-BR",  # Portuguese (Brazil)
            }
            speech_language = language_map.get(language, f"{language}-{language.upper()}")
            
            config_speech = speech.RecognitionConfig(
                encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=16000,
                language_code=speech_language,
                # Enable automatic punctuation for better readability
                enable_automatic_punctuation=True,
                # Use enhanced model for better accuracy
                use_enhanced=True,
                # Enable profanity filter
                profanity_filter=True,
            )
            
            # Perform the transcription
            logger.info(f"[VERTEX_SPEECH] Calling Google Speech-to-Text API with language: {speech_language}")
            response = client.recognize(config=config_speech, audio=audio)
            
            # Extract transcribed text
            transcribed_text = ""
            for result in response.results:
                # Use the most confident alternative
                if result.alternatives:
                    transcribed_text += result.alternatives[0].transcript + " "
            
            transcribed_text = transcribed_text.strip()
            
            if not transcribed_text:
                logger.warning("[VERTEX_SPEECH] No transcription results returned")
                raise Exception("No speech detected in audio file")
            
            logger.info(f"[VERTEX_SPEECH] Transcription successful: {transcribed_text[:100]}...")
            return transcribed_text
            
        finally:
            # Clean up temporary wav file
            try:
                os.remove(wav_path)
            except Exception as e:
                logger.warning(f"[VERTEX_SPEECH] Could not delete temp wav file: {wav_path}")
                
    except Exception as e:
        logger.exception(f"[VERTEX_SPEECH] Error during audio transcription: {str(e)}")
        raise Exception(f"Speech-to-Text transcription failed: {str(e)}")

# For compatibility with existing code
async def transcribe_audio(file_path: str, language: str = "es") -> str:
    """
    Alternative function name for compatibility.
    Delegates to transcribe_audio_opus.
    """
    return await transcribe_audio_opus(file_path, language)
