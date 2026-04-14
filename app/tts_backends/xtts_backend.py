import os
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
        if self.model is None:
            self.load_model()
            
        # Compute or load conditioning latents
        gpt_cond_latent, speaker_embedding = self._get_conditioning_latents(speaker)
        
        chunks = self.model.inference_stream(
            text,
            self.language,
            gpt_cond_latent,
            speaker_embedding
        )

        for chunk in chunks:
            if abort_event.is_set():
                print("XTTS stream aborted.")
                break
                
            # XTTS inference_stream yields PyTorch tensors, convert to numpy
            wav_chunk = chunk.detach().cpu().numpy()
            yield wav_chunk
            
    @property
    def sample_rate(self) -> int:
        return self._sample_rate
