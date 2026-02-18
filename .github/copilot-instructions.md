# Copilot Instructions for Airi Project

## Project Overview
**Airi** is a multi-backend speech processing system for Russian language, supporting both Speech-to-Text (STT) and Text-to-Speech (TTS) synthesis. Uses factory patterns and pluggable backends with OpenAI SDK (via OpenRouter) for LLM.

## Modern Architecture (Updated)

### Threading-Based Concurrent System
Three independent threads working simultaneously:
1. **Recording Thread** (`thread_record_voice`): Listens to microphone → Adds recognized text to `voice_input_queue`
2. **AI Processing Thread** (`thread_process_ai`): Consumes voice input → Sends to LLM → Stores response in `ai_response_queue` and history file
3. **Playback Thread** (`thread_play_response`): Consumes AI responses → Synthesizes TTS → Plays audio

Queue-based communication with `queue.Queue` for thread-safe inter-thread communication.

### New LLM Integration
- **File**: [app/openrouter_handler.py](app/openrouter_handler.py) - OpenRouter API handler using OpenAI SDK
- **Base URL**: `https://openrouter.ai/api/v1` with OpenAI client
- **Features**: Streaming response handling, tool calling with parallel execution, reasoning support
- **Callback pattern**: `save_callback` for progress tracking during streaming

### Common LLM Utilities
- **File**: [app/llm_common.py](app/llm_common.py) - Shared utilities for all LLM handlers
- **Key Classes**:
  - `StreamChunkBuffer` - Assembles streaming chunks into complete responses
  - `ToolExecutor` - Executes tool calls in parallel
  - `build_history_message()` - Formats responses for history storage

### Text-to-Speech System
- **File**: [app/tts.py](app/tts.py) - Silero TTS integration with threading
- **Classes**:
  - `TextToSpeech` - Synthesis engine (thread-safe with locks)
  - `AudioPlayer` - Plays WAV files or bytes in background thread
  - `TTSController` - High-level interface for speak/wait operations
- **Default Russian Voice**: "kseniya" (female)

### STT Backends
- **Vosk**: Direct PyAudio + Kaldi (16kHz mono, fastest)
- **Whisper**: Flexible audio input, handles resampling
- **Google**: Online API (no local model needed)
- Factory: [app/STT/__init__.py](app/STT/__init__.py) handles instantiation

### TTS Backends
- **Silero**: Primary backend with v5_ru model, Russian optimized
- Factory: [app/TTS/__init__.py](app/TTS/__init__.py)

## Critical Patterns & Implementation Details

### 1. Threading & Queues Best Practices
```python
# Thread-safe queue communication
voice_input_queue = queue.Queue(maxsize=10)
ai_response_queue = queue.Queue(maxsize=5)
state_lock = threading.Lock()

# Lock pattern for shared resources
with state_lock:
    app_state.processing = True

# Non-blocking puts with fallback
try:
    voice_input_queue.put_nowait(text)
except queue.Full:
    logger.warning("Queue full, skipping")
```

### 2. OpenRouter LLM Handler Pattern
```python
llm_handler = OpenRouterHandler(
    api_key=os.getenv("OPENROUTER_API_KEY"),
    model="openai/gpt-4-turbo",
    tool_handlers=get_tool_handlers(),
    reasoning_enabled=True
)

response = llm_handler.send_message(
    messages=messages_for_api,
    save_callback=lambda progress: logger.debug(f"Progress: {progress}%")
)
```

### 3. Tool Calling with Parallel Execution
- Tools defined in `get_tool_handlers()` as `{name: callable}` dict
- Handler automatically detects tool calls in streaming response
- Executes all tool calls **in parallel** using ThreadPoolExecutor
- Resends updated messages (with tool results) back to LLM
- Recursive until no more tool calls

### 4. History Management
- **File**: [app/data/chat_history.json](app/data/chat_history.json)
- **Format**: `{session_id, messages[], last_updated}`
- **Message types**: `user`, `assistant`, `error`, `tool`
- **Saved on every update**: Both voice input and AI responses written immediately
- **Load on startup**: History restored from file in AI processing thread

### 5. TTS Integration with Threading
```python
# Non-blocking speak with optional file saving
tts_controller.speak(
    text="Hello",
    use_file=False,  # Play from memory
    on_complete=callback_function
)

# Wait for playback to finish
tts_controller.wait_completion()
```

### 6. Error Handling in Streaming
- Network errors: Retry up to 3 times with exponential backoff
- Rate limits (429): Rotate API keys, wait for reset
- Tool execution errors: Caught individually, don't stop stream
- Unclosed tags: Auto-fix with `fix_unclosed_tags()` helper

### 7. Settings Management
- **File**: [app/utils/settings.py](app/utils/settings.py)
- Persistent JSON-based configuration
- Keys: `current_model`, `stt_provider`, `stt_model`, `tts_speaker`
- Auto-creates default structure if missing

## Development Workflow

### Running the Application
```bash
# Set API key first
set OPENROUTER_API_KEY=your_key_here

# Run main application
python main.py

# Or test STT alone (legacy mode)
python main.py whisper small
```

### Adding a New Tool Handler
1. Define function in [app/main.py](app/main.py) (or separate module)
2. Add to `get_tool_handlers()` dict
3. Function signature: `func_name(param: str) -> Dict[str, Any]`
4. Handler automatically serialized and sent to LLM

### Adding a New STT Backend
1. Create [app/STT/new_backend.py](app/STT/new_backend.py)
2. Implement `stream()` generator (yields recognized text)
3. Update factory in [app/STT/__init__.py](app/STT/__init__.py)
4. Auto-download logic in `_download_model()`

### Extending with Different LLM Providers
1. Create new file: `app/{provider}_handler.py`
2. Inherit common patterns from [app/llm_common.py](app/llm_common.py)
3. Implement `send_message()` method with same interface
4. Update [app/main.py](app/main.py) to instantiate new handler

## Dependencies

### Critical Packages
- **OpenAI SDK**: `openai` (used for OpenRouter integration)
- **Audio**: PyAudio, SpeechRecognition, soundfile
- **ML**: torch, torchvision (for Silero TTS)
- **Config**: omegaconf (Silero YAML parsing)
- **Utils**: requests (model downloads), pygame (audio playback)

### Model Files
- Silero TTS: `app/model/silero/v5_ru.pt` + `silero_models.yml`
- Vosk: `app/model/vosk/{model_name}/` (auto-downloaded on first use)
- Whisper: `app/model/whisper/{model}.pt` (auto-downloaded)

## Key Logging & Debugging

### Emoji Conventions (for UI consistency)
- 🎙️ Main application
- 🎤 Recording active
- 🔤 Text recognized
- 📝 Message stored
- 📤 Sending to LLM
- ✅ Success
- ❌ Error
- ⚠️ Warning
- 🔊 Audio playback
- 🤖 AI response

### Enable Debug Logging
```python
logger.setLevel(logging.DEBUG)  # In logging.basicConfig
```

### Check Microphone Availability
- List available devices: Vosk uses `sr.Microphone.list_microphone_names()`
- First check in main before starting threads

## Testing Notes
- **Microphone**: Must be available and working before launching
- **Large model downloads**: Expect 5-30 seconds on first run
- **Thread cleanup**: Ctrl+C stops `app_state.running`, all threads gracefully exit
- **Queue timeouts**: Set to 1.0s to avoid blocking main loop
- **TTS initialization**: Not critical; skips if torch/model load fails

