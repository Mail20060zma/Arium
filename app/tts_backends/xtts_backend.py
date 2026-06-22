import os
import queue
import threading
import torch
import torchaudio
import numpy as np
from typing import Generator, Optional
import soundfile as sf
from huggingface_hub import snapshot_download

from TTS.tts.configs.xtts_config import XttsConfig
from TTS.tts.models.xtts import Xtts
from .base import BaseTTS

class XTTSBackend(BaseTTS):
    """
    XTTS (v2) text-to-speech implementation.
    Automatically downloads the model from Hugging Face if not present.
    Uses 'app/model/xtts/reference.wav' as the default speaker reference file.
    """
    
    def __init__(self, model_dir: str = "app/model/xtts", language: str = "ru", device: Optional[str] = None):
        self.model_dir = model_dir
        self.language = language
        self.reference_wav = os.path.join(model_dir, "reference.wav")
        self.device = device if device else ("cuda" if torch.cuda.is_available() else "cpu")
        self.model: Optional[Xtts] = None
        self._sample_rate = 24000
        
        # Автоматическая загрузка модели при инициализации как в Silero
        self.load_model()
        
    def load_model(self):
        if self.model is not None:
            return
        print("Loading XTTS model...")
        
        # Download if config.json doesn't exist
        config_path = os.path.join(self.model_dir, "config.json")
        if not os.path.exists(config_path):
            print("Downloading XTTS v2 model from HuggingFace...")
            snapshot_download(
                repo_id="coqui/XTTS-v2",
                local_dir=self.model_dir,
                local_dir_use_symlinks=False
            )
            
        # Download a default reference wav if missing
        if not os.path.exists(self.reference_wav):
            import urllib.request
            print("Downloading default reference.wav...")
            try:
                urllib.request.urlretrieve(
                    "https://huggingface.co/coqui/XTTS-v2/resolve/main/samples/en_sample.wav",
                    self.reference_wav
                )
            except Exception as e:
                print(f"Failed to download default reference wav: {e}")
                
        config = XttsConfig()
        config.load_json(config_path)
        
        self.model = Xtts.init_from_config(config)

        self.model.load_checkpoint(
            config,
            checkpoint_dir=self.model_dir,
            vocab_path=os.path.join(self.model_dir, "vocab.json"),
            use_deepspeed=False
        )
        self.model.to(self.device)

        # === Агрессивная оптимизация для CUDA ===
        if self.device == "cuda":
            # 1. TF32 — ускоряет матричные операции ~2x
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True

            # 2. Flash Attention — ускоряет механизм внимания в GPT
            try:
                torch.backends.cuda.enable_flash_sdp(True)
                torch.backends.cuda.enable_mem_efficient_sdp(True)
                print("✅ XTTS: Flash SDP включён")
            except Exception:
                pass

            # 3. torch.compile — оптимизирует граф вычислений 
            try:
                if hasattr(self.model, 'gpt'):
                    self.model.gpt = torch.compile(
                        self.model.gpt,
                        mode='reduce-overhead',
                        fullgraph=False
                    )
                    print("✅ XTTS: torch.compile применён к GPT")
            except Exception as e:
                print(f"⚠️ torch.compile не применён: {e}")

        print("XTTS model loaded successfully.")

    def _get_conditioning_latents(self, speaker: Optional[str]):
        """Helper to compute or load cached conditioning latents for a speaker."""
        voices_dir = os.path.join("app", "model", "xtts", "voices")
        os.makedirs(voices_dir, exist_ok=True)
        
        if speaker and not speaker.endswith(".wav") and not os.path.isabs(speaker):
            speaker_name = speaker
            wav_path = os.path.join(voices_dir, f"{speaker_name}.wav")
            pth_path = os.path.join(voices_dir, f"{speaker_name}.pth")
        else:
            wav_path = speaker if speaker and os.path.exists(speaker) else self.reference_wav
            speaker_name = os.path.splitext(os.path.basename(wav_path))[0]
            pth_path = os.path.join(voices_dir, f"{speaker_name}.pth")

        if os.path.exists(pth_path):
            return torch.load(pth_path)

        if not os.path.exists(wav_path):
            raise FileNotFoundError(f"Reference WAV missing or speaker not found: {wav_path}")

        print(f"Computing conditioning latents for {speaker_name}...")
        
        # === ПАТЧ ДЛЯ WINDOWS (БЕЗ FFMPEG) ===
        # Модуль torchaudio.load() часто падает на Windows из-за отсутствия ffmpeg/libtorchcodec.
        # Мы перехватываем функцию load_audio в модуле XTTS и заменяем её на версию с soundfile.
        try:
            import TTS.tts.models.xtts as xtts_module
            import soundfile as sf
            
            def patched_load_audio(audiopath, sampling_rate):
                data, lsr = sf.read(audiopath, dtype='float32')
                audio = torch.FloatTensor(data)
                if len(audio.shape) > 1: # Stereo to mono
                    audio = torch.mean(audio, dim=1, keepdim=True)
                else:
                    audio = audio.unsqueeze(0)
                
                if lsr != sampling_rate:
                    audio = torchaudio.functional.resample(audio, lsr, sampling_rate)
                
                audio.clip_(-1, 1)
                return audio

            xtts_module.load_audio = patched_load_audio
        except Exception as e:
            print(f"⚠️ Не удалось применить патч load_audio: {e}")

        gpt_cond_latent, speaker_embedding = self.model.get_conditioning_latents(audio_path=[wav_path])
        
        torch.save((gpt_cond_latent, speaker_embedding), pth_path)
        return gpt_cond_latent, speaker_embedding

    def synthesize_to_file(self, text: str, output_path: str, speaker: Optional[str] = None) -> str:
        if self.model is None:
            self.load_model()
            
        print(f"XTTS Synthesizing to {output_path}")
        
        # Calculate conditioning latents
        gpt_cond_latent, speaker_embedding = self._get_conditioning_latents(speaker)
        
        # Generate audio
        out = self.model.inference(
            text,
            self.language,
            gpt_cond_latent,
            speaker_embedding,
            temperature=0.7,
        )
        
        audio_tensor = torch.tensor(out["wav"]).unsqueeze(0)
        torchaudio.save(output_path, audio_tensor, self._sample_rate)
        
        return output_path

    def stream_audio(self, text: str, abort_event: threading.Event, speaker: Optional[str] = None) -> Generator[np.ndarray, None, None]:
        """
        Стриминг аудио с PREFETCH: генерация следующего чанка происходит
        в фоновом потоке, пока текущий чанк воспроизводится.
        Это резко снижает задержку первого звука.
        """
        if self.model is None:
            self.load_model()

        # Compute or load conditioning latents
        gpt_cond_latent, speaker_embedding = self._get_conditioning_latents(speaker)

        # Очередь буферизации: хранит до 5 сгенерированных чанков вперёд
        prefetch_queue: queue.Queue = queue.Queue(maxsize=5)
        generation_error = None

        def _generate_in_background():
            """Фоновый поток: генерирует чанки и кладёт в очередь."""
            nonlocal generation_error
            try:
                with torch.inference_mode():
                    chunks = self.model.inference_stream(
                        text,
                        self.language,
                        gpt_cond_latent,
                        speaker_embedding,
                        overlap_wav_len=512,
                        stream_chunk_size=16
                    )
                    for chunk in chunks:
                        if abort_event.is_set():
                            break
                        prefetch_queue.put(chunk, timeout=10.0)
                    prefetch_queue.put(None)  # Сигнал: генерация завершена
            except Exception as e:
                generation_error = e
                try:
                    prefetch_queue.put(None)
                except queue.Full:
                    pass

        # Запускаем генерацию в фоне
        gen_thread = threading.Thread(target=_generate_in_background, daemon=True)
        gen_thread.start()

        # Основной поток: отдаёт чанки из очереди
        while not abort_event.is_set():
            try:
                chunk = prefetch_queue.get(timeout=0.5)
            except queue.Empty:
                if not gen_thread.is_alive():
                    break
                continue

            if chunk is None:
                if generation_error:
                    raise generation_error
                break

            if isinstance(chunk, Exception):
                raise chunk

            if isinstance(chunk, torch.Tensor):
                wav_chunk = chunk.detach().cpu().float().numpy()
            else:
                wav_chunk = np.array(chunk, dtype=np.float32)
            yield wav_chunk
            
    @property
    def sample_rate(self) -> int:
        return self._sample_rate
