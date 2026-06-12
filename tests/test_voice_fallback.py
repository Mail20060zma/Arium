import sys
from unittest.mock import MagicMock

# Mock out heavy and system-dependent modules before any other imports
sys.modules['sounddevice'] = MagicMock()
sys.modules['app.tts_backends'] = MagicMock()
sys.modules['app.STT'] = MagicMock()

sys.stdout.reconfigure(encoding='utf-8')
import os
import json
import threading
from unittest.mock import patch, ANY
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.engine.core import AriumEngine
from app.utils.settings import Settings

def test_voice_fallback():
    print("Running voice fallback tests...")

    # We patch _init_components to avoid loading silero-vad, Whisper, and silero TTS
    with patch.object(AriumEngine, '_init_components') as mock_init:
        # Create an engine instance
        engine = AriumEngine("settings.json")
        
        # Manually initialize the minimal variables needed for _generate_ai_response
        engine.settings = MagicMock(spec=Settings)
        # Mock settings calls
        engine.settings.get.side_effect = lambda key, default=None: {
            'memory.context_window_size': 25,
            'memory.include_reasoning_in_context': False,
            'memory.reasoning_max_chars': 4000,
            'llm.enabled_tools': ['text_to_audio', 'search_web'],
            'llm.tool_only_voice_output': True,
        }.get(key, default)
        engine.settings.get_model_id_for_model.return_value = "dummy-model"
        
        engine.running = True
        engine.cancellation_token = False
        engine.abort_playback_event = threading.Event()
        engine._turn_state_lock = threading.Lock()
        engine._turn_tts_tool_results = []
        engine.current_turn_tool_results = []
        engine.enabled_tools = ['text_to_audio', 'search_web']
        engine.tool_only_voice_output = True
        engine.system_prompt = "System prompt"
        engine.user_system_prompt = "User prompt"
        engine.tools_definitions = [{"name": "search_web"}, {"name": "text_to_audio"}]
        
        # Mocks for methods
        engine.ui_notify = MagicMock()
        engine._reset_spoken_words_state = MagicMock()
        engine._cancellation_callback = MagicMock()
        engine._wait_for_tts_completion = MagicMock()
        engine._tool_text_to_audio = MagicMock(return_value={"text_requested": "dummy"})
        
        # Mock HistoryManager
        engine.history_manager = MagicMock()
        engine.history_manager.get_context_window.return_value = [
            {"role": "user", "content": "Привет"}
        ]
        # Track message IDs
        msg_counter = 0
        def append_msg(role, content, **kwargs):
            nonlocal msg_counter
            msg_counter += 1
            print(f"History append: role={role}, content={content}, kwargs={kwargs}")
            return f"msg_{msg_counter}"
        engine.history_manager.append_message.side_effect = append_msg
        
        # Mock LLM
        engine.llm = MagicMock()

        # --- CASE 1: Model returns plain text (no tool calls at all) ---
        print("\n--- CASE 1: Model returns plain text ---")
        engine._turn_tts_tool_results = []
        engine.last_ai_message_id = None
        
        # Mock the stream output
        engine.llm.send_message_stream.return_value = [
            {"type": "content_delta", "content": "Привет! Как дела?"},
            {"type": "status", "finish_reason": "stop"}
        ]
        
        # Run response generation
        engine._generate_ai_response()
        
        # Assertions for Case 1
        # 1. _tool_text_to_audio should be called with the text
        engine._tool_text_to_audio.assert_called_with("Привет! Как дела?")
        # 2. History manager should have appended the tool call and tool result
        engine.history_manager.append_message.assert_any_call(
            "assistant",
            None,
            tool_calls=[{
                "id": ANY,
                "type": "function",
                "function": {
                    "name": "text_to_audio",
                    "arguments": ANY
                }
            }]
        )
        print("CASE 1 passed successfully!")

        # Reset mocks
        engine._tool_text_to_audio.reset_mock()
        engine.history_manager.reset_mock()
        engine.history_manager.append_message.side_effect = append_msg

        # --- CASE 2: Model returns plain text AFTER a tool call (recursive loop) ---
        print("\n--- CASE 2: Model returns plain text after tool call (recursive loop) ---")
        engine._turn_tts_tool_results = []
        engine.last_ai_message_id = None
        
        # Mock the stream output simulating a recursive loop tool result first, then final text
        # Since _generate_ai_response processes the stream chunk-by-chunk,
        # we yield the tool results first (which the engine appends to history),
        # and then the content_delta for the final answer.
        engine.llm.send_message_stream.return_value = [
            {
                "type": "assistant_tool_calls",
                "tool_calls": [
                    {
                        "id": "call_search",
                        "type": "function",
                        "function": {"name": "search_web", "arguments": '{"query": "Hawking"}'}
                    }
                ]
            },
            {
                "type": "tool_result",
                "tool_call_id": "call_search",
                "tool_name": "search_web",
                "result": {"results": "Hawking born 1942"},
                "success": True
            },
            {"type": "content_delta", "content": "Стивен Хокинг родился в 1942 году."},
            {"type": "status", "finish_reason": "stop"}
        ]
        
        # Run response generation
        engine._generate_ai_response()
        
        # Assertions for Case 2
        # 1. _tool_text_to_audio should be called with the final answer text
        engine._tool_text_to_audio.assert_called_with("Стивен Хокинг родился в 1942 году.")
        print("CASE 2 passed successfully!")

        # Reset mocks
        engine._tool_text_to_audio.reset_mock()
        engine.history_manager.reset_mock()
        engine.history_manager.append_message.side_effect = append_msg

        # --- CASE 3: Model returns a JSON tool call block in raw text ---
        print("\n--- CASE 3: Model returns JSON tool call in raw text ---")
        engine._turn_tts_tool_results = []
        engine.last_ai_message_id = None
        
        # Mock the stream output returning JSON text containing a tool call
        engine.llm.send_message_stream.return_value = [
            {"type": "content_delta", "content": '{"tool_name": "text_to_audio", "parameters": {"text": "Привет! Конечно, запомнил. Пользователя зовут Михаил."}}'},
            {"type": "status", "finish_reason": "stop"}
        ]
        
        # Run response generation
        engine._generate_ai_response()
        
        # Assertions for Case 3
        # 1. _tool_text_to_audio should be called with only the extracted text
        engine._tool_text_to_audio.assert_called_with("Привет! Конечно, запомнил. Пользователя зовут Михаил.")
        print("CASE 3 passed successfully!")

if __name__ == "__main__":
    test_voice_fallback()

