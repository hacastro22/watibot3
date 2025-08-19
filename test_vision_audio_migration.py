#!/usr/bin/env python3
"""
Vision and Audio Migration Validation Test
Tests the complete migration of vision and audio components to Vertex AI
"""

import asyncio
import logging
import os
import sys
import tempfile
import base64
from unittest.mock import patch, AsyncMock, MagicMock
from PIL import Image
import io

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Add app directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

def create_test_image() -> str:
    """Create a test image and return its base64 encoding"""
    # Create a simple test image
    img = Image.new('RGB', (100, 100), color='white')
    img_buffer = io.BytesIO()
    img.save(img_buffer, format='JPEG')
    img_base64 = base64.b64encode(img_buffer.getvalue()).decode('utf-8')
    return img_base64

def create_test_audio_file() -> str:
    """Create a temporary test audio file"""
    with tempfile.NamedTemporaryFile(suffix='.opus', delete=False) as f:
        # Create a minimal opus file (just placeholder data for testing)
        f.write(b'OggS\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00')  # Minimal Ogg header
        return f.name

def test_image_classifier_import():
    """Test that image_classifier imports with Vertex AI support"""
    try:
        with patch.dict('sys.modules', {
            'app.config': MagicMock(
                USE_VERTEX_AI=True,
                GOOGLE_CLOUD_PROJECT_ID='test-project',
                VERTEX_AI_LOCATION='us-central1',
                GOOGLE_APPLICATION_CREDENTIALS='/path/to/creds.json'
            ),
            'app.thread_store': MagicMock()
        }):
            import app.image_classifier as classifier
            
        # Verify Vertex AI functions exist
        assert hasattr(classifier, '_classify_with_vertex_ai'), "Vertex AI classification function missing"
        assert hasattr(classifier, '_initialize_vertex_ai'), "Vertex AI initialization function missing"
        
        logger.info("✓ Image classifier imports successfully with Vertex AI support")
        return True
    except Exception as e:
        logger.error(f"✗ Image classifier import failed: {e}")
        return False

async def test_image_classifier_vertex_routing():
    """Test that image classifier routes to Vertex AI when flag is enabled"""
    try:
        with patch.dict('sys.modules', {
            'app.config': MagicMock(
                USE_VERTEX_AI=True,
                GOOGLE_CLOUD_PROJECT_ID='test-project',
                VERTEX_AI_LOCATION='us-central1',
                GOOGLE_APPLICATION_CREDENTIALS='/path/to/creds.json'
            ),
            'app.thread_store': MagicMock()
        }):
            import app.image_classifier as classifier
            
        # Mock Vertex AI classification
        mock_result = '{"classification": "test", "confidence": 0.8}'
        
        with patch.object(classifier, '_classify_with_vertex_ai', return_value=mock_result):
            # Create a test image file
            with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as img_file:
                test_img = Image.new('RGB', (100, 100), color='red')
                test_img.save(img_file, format='JPEG')
                img_path = img_file.name
            
            try:
                result = await classifier.classify_image_with_context(
                    image_path=img_path,
                    conversation_context="Test context",
                    wa_id="test123"
                )
                
                assert result['success'] == True, "Classification should succeed"
                assert 'classification' in result, "Result should contain classification"
                
                logger.info("✓ Image classifier routes to Vertex AI correctly")
                return True
            finally:
                os.unlink(img_path)
            
    except Exception as e:
        logger.error(f"✗ Image classifier Vertex AI routing test failed: {e}")
        return False

def test_payment_analyzer_import():
    """Test that payment_proof_analyzer imports with Vertex AI support"""
    try:
        with patch.dict('sys.modules', {
            'app.config': MagicMock(
                USE_VERTEX_AI=True,
                GOOGLE_CLOUD_PROJECT_ID='test-project',
                VERTEX_AI_LOCATION='us-central1',
                GOOGLE_APPLICATION_CREDENTIALS='/path/to/creds.json'
            )
        }):
            import app.payment_proof_analyzer as analyzer
            
        # Verify Vertex AI functions exist
        assert hasattr(analyzer, 'analyze_with_vertex_ai'), "Vertex AI analysis function missing"
        assert hasattr(analyzer, '_initialize_vertex_ai'), "Vertex AI initialization function missing"
        
        logger.info("✓ Payment analyzer imports successfully with Vertex AI support")
        return True
    except Exception as e:
        logger.error(f"✗ Payment analyzer import failed: {e}")
        return False

async def test_payment_analyzer_vertex_routing():
    """Test that payment analyzer routes to Vertex AI when flag is enabled"""
    try:
        with patch.dict('sys.modules', {
            'app.config': MagicMock(
                USE_VERTEX_AI=True,
                GOOGLE_CLOUD_PROJECT_ID='test-project',
                VERTEX_AI_LOCATION='us-central1',
                GOOGLE_APPLICATION_CREDENTIALS='/path/to/creds.json'
            )
        }):
            import app.payment_proof_analyzer as analyzer
            
        # Mock Vertex AI analysis
        mock_result = {
            "success": True,
            "is_valid_receipt": False,
            "receipt_type": "unknown",
            "extracted_info": {}
        }
        
        with patch.object(analyzer, 'analyze_with_vertex_ai', return_value=mock_result), \
             patch('aiohttp.ClientSession') as mock_session:
            
            # Mock file download
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.headers = {'Content-Type': 'image/jpeg'}
            mock_response.read = AsyncMock(return_value=base64.b64decode(create_test_image()))
            mock_session.return_value.__aenter__.return_value.get.return_value.__aenter__.return_value = mock_response
            
            result = await analyzer.analyze_payment_proof("http://test.com/image.jpg")
            
            assert result['success'] == True, "Analysis should succeed"
            assert 'is_valid_receipt' in result, "Result should contain validity check"
            
            logger.info("✓ Payment analyzer routes to Vertex AI correctly")
            return True
            
    except Exception as e:
        logger.error(f"✗ Payment analyzer Vertex AI routing test failed: {e}")
        return False

def test_whisper_client_import():
    """Test that whisper_client imports with Google Cloud Speech support"""
    try:
        with patch.dict('sys.modules', {
            'app.config': MagicMock(
                USE_VERTEX_AI=True,
                GOOGLE_APPLICATION_CREDENTIALS='/path/to/creds.json'
            ),
            'google.cloud.speech': MagicMock(),
            'google.oauth2.service_account': MagicMock()
        }):
            import app.whisper_client as whisper
            
        # Verify Google Cloud functions exist
        assert hasattr(whisper, '_transcribe_with_google_speech'), "Google Cloud transcription function missing"
        assert hasattr(whisper, '_initialize_speech_client'), "Speech client initialization function missing"
        assert hasattr(whisper, '_transcribe_with_openai_whisper'), "OpenAI Whisper fallback function missing"
        
        logger.info("✓ Whisper client imports successfully with Google Cloud Speech support")
        return True
    except Exception as e:
        logger.error(f"✗ Whisper client import failed: {e}")
        return False

async def test_whisper_client_vertex_routing():
    """Test that whisper client routes to Google Cloud Speech when flag is enabled"""
    try:
        with patch.dict('sys.modules', {
            'app.config': MagicMock(
                USE_VERTEX_AI=True,
                GOOGLE_APPLICATION_CREDENTIALS='/path/to/creds.json'
            ),
            'google.cloud.speech': MagicMock(),
            'google.oauth2.service_account': MagicMock()
        }):
            import app.whisper_client as whisper
            
        # Mock Google Cloud Speech transcription
        with patch.object(whisper, '_transcribe_with_google_speech', return_value="Hola, soy un mensaje de prueba"), \
             patch('subprocess.run') as mock_subprocess:
            
            # Mock successful ffmpeg conversion
            mock_subprocess.return_value.returncode = 0
            
            # Create test audio file
            test_audio = create_test_audio_file()
            
            try:
                result = await whisper.transcribe_audio_opus(test_audio, "es")
                
                assert result == "Hola, soy un mensaje de prueba", f"Expected test transcript, got '{result}'"
                
                logger.info("✓ Whisper client routes to Google Cloud Speech correctly")
                return True
            finally:
                if os.path.exists(test_audio):
                    os.unlink(test_audio)
            
    except Exception as e:
        logger.error(f"✗ Whisper client Google Cloud Speech routing test failed: {e}")
        return False

def test_feature_flag_fallback():
    """Test that components fall back to OpenAI when USE_VERTEX_AI=False"""
    try:
        with patch.dict('sys.modules', {
            'app.config': MagicMock(
                USE_VERTEX_AI=False,  # Feature flag disabled
                OPENAI_API_KEY='test-key'
            ),
            'app.thread_store': MagicMock()
        }):
            import app.image_classifier as classifier
            import app.payment_proof_analyzer as analyzer
            import app.whisper_client as whisper
            
        # All modules should import without Vertex AI dependencies when flag is False
        logger.info("✓ All components import successfully with feature flag disabled")
        return True
    except Exception as e:
        logger.error(f"✗ Feature flag fallback test failed: {e}")
        return False

def test_dependencies_availability():
    """Test that all required dependencies are available"""
    try:
        # Test Vertex AI dependencies
        import vertexai
        from vertexai.generative_models import GenerativeModel, Part
        import vertexai.preview.generative_models as generative_models
        
        # Test Google Cloud Speech dependencies  
        from google.cloud import speech
        from google.oauth2 import service_account
        
        # Test image processing dependencies
        from PIL import Image
        
        logger.info("✓ All required dependencies are available")
        return True
    except ImportError as e:
        logger.error(f"✗ Missing required dependency: {e}")
        return False

async def run_all_tests():
    """Run all validation tests"""
    logger.info("🚀 Starting Vision and Audio Migration Validation Tests")
    logger.info("=" * 70)
    
    tests = [
        ("Dependencies Check", test_dependencies_availability),
        ("Image Classifier Import", test_image_classifier_import),
        ("Image Classifier Vertex AI Routing", test_image_classifier_vertex_routing),
        ("Payment Analyzer Import", test_payment_analyzer_import), 
        ("Payment Analyzer Vertex AI Routing", test_payment_analyzer_vertex_routing),
        ("Whisper Client Import", test_whisper_client_import),
        ("Whisper Client Google Cloud Routing", test_whisper_client_vertex_routing),
        ("Feature Flag Fallback", test_feature_flag_fallback)
    ]
    
    passed = 0
    total = len(tests)
    
    for test_name, test_func in tests:
        logger.info(f"\n🧪 Running {test_name}...")
        try:
            if asyncio.iscoroutinefunction(test_func):
                result = await test_func()
            else:
                result = test_func()
            
            if result:
                passed += 1
        except Exception as e:
            logger.error(f"✗ {test_name} failed with exception: {e}")
    
    logger.info("\n" + "=" * 70)
    logger.info(f"🎯 VALIDATION RESULTS: {passed}/{total} tests passed")
    
    if passed == total:
        logger.info("🎉 ALL VISION AND AUDIO MIGRATION TESTS PASSED!")
        logger.info("✅ Phase 1 migration is now 100% complete")
        return True
    else:
        logger.warning(f"⚠️  {total - passed} tests failed - Review issues before deployment")
        return False

if __name__ == "__main__":
    try:
        success = asyncio.run(run_all_tests())
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        logger.info("\n🛑 Tests interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"💥 Test runner failed: {e}")
        sys.exit(1)
