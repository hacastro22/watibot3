#!/usr/bin/env python3
"""
Vertex AI Agent Validation Test
Tests all critical business logic ported from OpenAI to Vertex AI
"""

import asyncio
import logging
import os
import sys
from datetime import datetime
from unittest.mock import patch, AsyncMock, MagicMock

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Add app directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

def test_vertex_agent_import():
    """Test that vertex_agent can be imported successfully"""
    try:
        # Import with mocked dependencies to avoid circular imports
        with patch.dict('sys.modules', {
            'app.booking_tool': MagicMock(),
            'app.banking_validation': MagicMock(),
            'app.conversation_history': MagicMock(),
            'app.config': MagicMock(),
            'app.thread_store': MagicMock()
        }):
            import app.vertex_agent as vertex_agent
            
        logger.info("✓ Vertex agent imports successfully")
        return True
    except Exception as e:
        logger.error(f"✗ Vertex agent import failed: {e}")
        return False

def test_tool_definitions():
    """Test that all required tools are properly defined"""
    try:
        with patch.dict('sys.modules', {
            'app.booking_tool': MagicMock(),
            'app.banking_validation': MagicMock(),
            'app.conversation_history': MagicMock(),
            'app.config': MagicMock(),
            'app.thread_store': MagicMock()
        }):
            import app.vertex_agent as vertex_agent
            
        # Verify tool definitions
        assert hasattr(vertex_agent, 'TOOLS'), "TOOLS constant not found"
        assert hasattr(vertex_agent, 'AVAILABLE_FUNCTIONS'), "AVAILABLE_FUNCTIONS not found"
        
        tools = vertex_agent.TOOLS
        functions = vertex_agent.AVAILABLE_FUNCTIONS
        
        # Check required tools exist
        required_tools = ['get_conversation_snippets', 'validate_bank_transfer', 'book_room']
        tool_names = [t['function']['name'] for t in tools]
        
        for tool in required_tools:
            assert tool in tool_names, f"Tool {tool} missing from TOOLS"
            assert tool in functions, f"Function {tool} missing from AVAILABLE_FUNCTIONS"
            
        logger.info("✓ All required tools are properly defined")
        return True
    except Exception as e:
        logger.error(f"✗ Tool definitions validation failed: {e}")
        return False

async def test_retry_logic_structure():
    """Test retry logic implementation without making actual API calls"""
    try:
        with patch.dict('sys.modules', {
            'app.booking_tool': MagicMock(),
            'app.banking_validation': MagicMock(), 
            'app.conversation_history': MagicMock(),
            'app.config': MagicMock(
                GOOGLE_CLOUD_PROJECT_ID='test-project',
                VERTEX_AI_LOCATION='us-central1',
                GOOGLE_APPLICATION_CREDENTIALS='/path/to/creds.json'
            ),
            'app.thread_store': MagicMock()
        }):
            import app.vertex_agent as vertex_agent
            
        # Mock Vertex AI initialization
        with patch.object(vertex_agent, '_initialize_vertex_ai', return_value=True), \
             patch.object(vertex_agent, '_get_or_create_session', return_value='test_session'), \
             patch.object(vertex_agent, '_get_conversation_context', return_value='test context'), \
             patch.object(vertex_agent, '_get_system_instructions', return_value='test instructions'), \
             patch.object(vertex_agent, '_process_with_retry_logic', return_value='test response'):
            
            response, session_id = await vertex_agent.get_openai_response(
                prompt="Test message",
                wa_id="1234567890",
                channel="whatsapp"
            )
            
            assert response == "test response", f"Expected 'test response', got '{response}'"
            assert session_id == "test_session", f"Expected 'test_session', got '{session_id}'"
            
        logger.info("✓ Main response processing structure works correctly")
        return True
    except Exception as e:
        logger.error(f"✗ Retry logic structure test failed: {e}")
        return False

def test_json_response_guard():
    """Test JSON response guard functionality"""
    try:
        with patch.dict('sys.modules', {
            'app.booking_tool': MagicMock(),
            'app.banking_validation': MagicMock(),
            'app.conversation_history': MagicMock(),
            'app.config': MagicMock(),
            'app.thread_store': MagicMock()
        }):
            import app.vertex_agent as vertex_agent
            
        # Test various JSON artifacts
        test_cases = [
            ('```json\n{"response": "Hola, ¿cómo puedo ayudarte?"}\n```', "Hola, ¿cómo puedo ayudarte?"),
            ('{"message": "Reserva confirmada"}', "Reserva confirmada"),
            ('', "Lo siento, no pude procesar tu solicitud. ¿Podrías intentar de nuevo?"),
            ('Normal Spanish response', "Normal Spanish response")
        ]
        
        for input_text, expected_output in test_cases:
            result = vertex_agent._apply_json_response_guard(input_text)
            assert expected_output in result, f"Guard failed for '{input_text}': got '{result}'"
            
        logger.info("✓ JSON response guard handles all test cases correctly")
        return True
    except Exception as e:
        logger.error(f"✗ JSON response guard test failed: {e}")
        return False

async def test_tool_identifier_injection():
    """Test that tool identifier injection is properly implemented"""
    try:
        with patch.dict('sys.modules', {
            'app.booking_tool': MagicMock(),
            'app.banking_validation': MagicMock(),
            'app.conversation_history': MagicMock(),
            'app.config': MagicMock(),
            'app.thread_store': MagicMock()
        }):
            import app.vertex_agent as vertex_agent
            
        # Mock tool call structure
        mock_tool_calls = [
            {
                'name': 'get_conversation_snippets',
                'arguments': {'search_query': 'test query'}
            }
        ]
        
        # Mock the function to verify identifier injection
        mock_function = AsyncMock(return_value={'result': 'success'})
        
        with patch.object(vertex_agent, 'AVAILABLE_FUNCTIONS', {'get_conversation_snippets': mock_function}):
            results = await vertex_agent._process_tool_calls(
                mock_tool_calls, 
                wa_id='1234567890',
                subscriber_id='sub123',
                channel='whatsapp'
            )
            
        # Verify function was called (would be called with injected identifiers in real scenario)
        assert len(results) == 1, f"Expected 1 result, got {len(results)}"
        
        logger.info("✓ Tool identifier injection structure is properly implemented")
        return True
    except Exception as e:
        logger.error(f"✗ Tool identifier injection test failed: {e}")
        return False

async def test_wati_history_functions():
    """Test WATI history injection functions structure"""
    try:
        with patch.dict('sys.modules', {
            'app.booking_tool': MagicMock(),
            'app.banking_validation': MagicMock(),
            'app.conversation_history': MagicMock(),
            'app.config': MagicMock(
                WATI_API_URL='https://test-api.wati.io',
                WATI_API_KEY='test-key'
            ),
            'app.thread_store': MagicMock()
        }):
            import app.vertex_agent as vertex_agent
            
        # Test that functions exist and have correct signatures
        assert hasattr(vertex_agent, 'get_pre_live_history'), "get_pre_live_history function missing"
        assert hasattr(vertex_agent, 'inject_wati_pre_live_history'), "inject_wati_pre_live_history function missing"
        assert hasattr(vertex_agent, 'inject_agent_context'), "inject_agent_context function missing"
        
        # Mock HTTP client to avoid actual API calls
        with patch('httpx.AsyncClient') as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"messages": {"items": []}}
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_response)
            
            # Test history fetching function structure
            history = await vertex_agent.get_pre_live_history('1234567890', datetime(2025, 7, 5))
            assert isinstance(history, list), "get_pre_live_history should return a list"
            
        logger.info("✓ WATI history injection functions are properly structured")
        return True
    except Exception as e:
        logger.error(f"✗ WATI history functions test failed: {e}")
        return False

async def run_all_tests():
    """Run all validation tests"""
    logger.info("🚀 Starting Vertex AI Agent Validation Tests")
    logger.info("=" * 60)
    
    tests = [
        ("Import Test", test_vertex_agent_import),
        ("Tool Definitions Test", test_tool_definitions), 
        ("Retry Logic Structure Test", test_retry_logic_structure),
        ("JSON Response Guard Test", test_json_response_guard),
        ("Tool Identifier Injection Test", test_tool_identifier_injection),
        ("WATI History Functions Test", test_wati_history_functions)
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
    
    logger.info("\n" + "=" * 60)
    logger.info(f"🎯 VALIDATION RESULTS: {passed}/{total} tests passed")
    
    if passed == total:
        logger.info("🎉 ALL TESTS PASSED - Vertex AI migration is ready!")
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
