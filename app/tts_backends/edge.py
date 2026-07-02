import sys
import asyncio
import logging

# Apply Windows event loop policy fix if needed to prevent SSL handshake issues
if sys.platform == 'win32':
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

import aiohttp
from .base import BaseTTS
import edge_tts
import soundfile as sf
import io
import numpy as np
import threading
from typing import Optional, Generator

logger = logging.getLogger(__name__)

class EdgeTTS(BaseTTS):
    """
    Edge TTS Text-to-Speech backend.
    """
    def __init__(self, settings):
        self.settings = settings
        self.voice = settings.get('tts_speaker', 'ru-RU-SvetlanaNeural')
        self._sample_rate = 24000
        
        logger.info(f"EdgeTTS: Initialized with voice={self.voice}")
        
    def load_model(self):
        pass  # API backend, no model loading required
        
    def synthesize_to_file(self, text: str, output_path: str, speaker: Optional[str] = None) -> str:
        voice_to_use = speaker if speaker else self.voice
        logger.info(f"EdgeTTS: Synthesizing to {output_path} (voice={voice_to_use})")
        
        async def _synth():
            communicate = edge_tts.Communicate(text, voice_to_use)
            await communicate.save(output_path)
            
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
        loop.run_until_complete(_synth())
        return output_path
        
    def stream_audio(self, text: str, abort_event: threading.Event, speaker: Optional[str] = None) -> Generator[np.ndarray, None, None]:
        voice_to_use = speaker if speaker else self.voice
        logger.info(f"EdgeTTS: Streaming speech (voice={voice_to_use})")
        
        async def _fetch():
            communicate = edge_tts.Communicate(text, voice_to_use)
            audio_data = b""
            async for chunk in communicate.stream():
                if abort_event.is_set():
                    break
                if chunk["type"] == "audio":
                    audio_data += chunk["data"]
            return audio_data

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
        audio_bytes = loop.run_until_complete(_fetch())
        
        if abort_event.is_set() or not audio_bytes:
            return
            
        try:
            data, samplerate = sf.read(io.BytesIO(audio_bytes), dtype='float32')
            self._sample_rate = samplerate
        except Exception as e:
            logger.error(f"EdgeTTS: Error decoding MP3 stream: {e}", exc_info=True)
            return
            
        # Yield in small chunks (1024 samples) to support instant abort
        chunk_size = 1024
        for i in range(0, len(data), chunk_size):
            if abort_event.is_set():
                break
            yield data[i:i + chunk_size]

    @property
    def sample_rate(self) -> int:
        return self._sample_rate
