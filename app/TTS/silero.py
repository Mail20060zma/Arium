import zipfile
import torch
import re
import soundfile as sf
import threading
import os
import sys
from pathlib import Path
from typing import Optional, Union, Callable, Generator
import time
from omegaconf import OmegaConf
import requests
from tqdm import tqdm
import numpy as np

from .base import BaseTTS

class SileroTTS(BaseTTS):
    """
    Оптимизированный класс для синтеза речи с поддержкой текста, SSML и автоопределения режима
    """
    
    def __init__(self, 
                 language: str = 'ru', 
                 model_id: str = 'v5_ru',
                 speaker: str = "",
                 device: Optional[Union[str, torch.device]] = None):
        """
        Инициализация класса с автоматической загрузкой модели в память
        """
        self.language = language
        self.model_id = model_id
        self.speaker = speaker or ('kseniya' if language == 'ru' else 'en_0')
        self.device = self._get_device(device)
        self.model = None
        self._model_loaded = False
        self._lock = threading.Lock()
        
        # Загружаем модель при инициализации
        self.load_model()

    @property
    def sample_rate(self) -> int:
        """Возвращает частоту дискретизации сгенерированного аудио."""
        return 48000
    
    def _get_device(self, device: Optional[Union[str, torch.device]] = None) -> torch.device:
        """Автоматически определяет доступное устройство (CUDA если доступна)"""
        if device is not None:
            return torch.device(device) if isinstance(device, str) else device
        return torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    
    def _is_valid_model(self, path: Path) -> bool:
        """Проверка целостности модели: размер + сигнатура ZIP + попытка открыть архив"""
        if not path.exists():
            return False
        
        # Минимальный размер для валидной модели (>1 МБ)
        if path.stat().st_size < 1_000_000:
            return False
        
        try:
            # Проверка сигнатуры ZIP (PK\x03\x04)
            with open(path, 'rb') as f:
                header = f.read(4)
                if header[:2] != b'PK':
                    return False
            
            # Критически важная проверка: попытка открыть как ZIP
            with zipfile.ZipFile(path, 'r') as zf:
                # Быстрая проверка наличия центрального каталога
                if not zf.namelist():
                    return False
            return True
        except (zipfile.BadZipFile, RuntimeError, Exception):
            return False
    
    def load_model(self) -> None:
        """Загружает модель TTS в память"""
        with self._lock:
            if self._model_loaded:
                return
            
            start_time = time.time()
            print(f"🔄 Загрузка модели {self.model_id} для языка {self.language} на {self.device}...")
            
            try:
                # Получаем URL модели из официального YAML
                models_url = 'https://raw.githubusercontent.com/snakers4/silero-models/master/models.yml'
                models_path = Path(__file__).parent.parent / 'model' / "silero" / 'silero_models.yml'
                
                if not models_path.exists():
                    torch.hub.download_url_to_file(models_url, str(models_path), progress=False)
                
                models = OmegaConf.load(models_path)
                
                if self.language not in models.tts_models:
                    raise ValueError(f"Язык '{self.language}' не поддерживается")
                if self.model_id not in models.tts_models[self.language]:
                    avail = list(models.tts_models[self.language].keys())
                    raise ValueError(f"Модель '{self.model_id}' не найдена. Доступны: {avail}")
                
                model_url = models.tts_models[self.language][self.model_id].latest.package

                local_path = Path(__file__).parent.parent / 'model' / "silero" / f"{self.model_id}.pt"
                local_path.parent.mkdir(parents=True, exist_ok=True)
                
                # Загрузка модели (если отсутствует или повреждена)
                if not self._is_valid_model(local_path):
                    print(f"⚠️ Модель отсутствует или повреждена. Загрузка: {model_url}")
                    try:
                        r = requests.get(model_url, stream=True, timeout=30)
                        r.raise_for_status()
                        
                        total = int(r.headers.get('content-length', 0))
                        with open(local_path, 'wb') as f, tqdm(
                            total=total, unit='B', unit_scale=True, desc=local_path.name, ncols=80
                        ) as pbar:
                            for chunk in r.iter_content(chunk_size=8192):
                                if chunk:
                                    f.write(chunk)
                                    pbar.update(len(chunk))
                        
                        if not self._is_valid_model(local_path):
                            local_path.unlink()
                            raise RuntimeError("Скачан не бинарный файл (возможно, блокировка).")
                        print("✅ Модель загружена")
                    except Exception as e:
                        print(f"❌ Ошибка загрузки: {e}")
                        print(f"\n💡 ВАЖНО: Скачайте модель ВРУЧНУЮ через прокси:")
                        print(f"   → {model_url}")
                        print(f"   → Сохраните как '{local_path}' в эту папку")
                        print(f"   → Перезапустите скрипт")
                        sys.exit(1)
                
                importer = torch.package.PackageImporter(str(local_path))
                model_data = importer.load_pickle("tts_models", "model")
                
                # Если возвращается tuple, берем первый элемент (модель)
                if isinstance(model_data, tuple):
                    self.model = model_data[0]
                else:
                    self.model = model_data
                
                # Перевод на устройство
                if hasattr(self.model, 'to'):
                    self.model.to(self.device)
                
                if self.device.type == 'cuda':
                    torch.cuda.empty_cache()
                
                self._model_loaded = True
                elapsed = time.time() - start_time
                print(f"✅ Модель загружена за {elapsed:.2f} секунд")
                print(f"🎤 Доступные спикеры: {self.model.speakers}")
                
                # Автоисправление спикера
                if self.speaker not in self.model.speakers:
                    mapping = {'xenia': 'kseniya', 'ksenia': 'kseniya'}
                    if self.speaker in mapping and mapping[self.speaker] in self.model.speakers:
                        old = self.speaker
                        self.speaker = mapping[self.speaker]
                        print(f"⚠️ Спикер '{old}' заменён на '{self.speaker}'")
                    else:
                        self.speaker = self.model.speakers[0]
                        print(f"⚠️ Спикер не найден. Используется: {self.speaker}")
                
            except Exception as e:
                print(f"❌ Ошибка загрузки: {str(e)}")
                raise RuntimeError(f"Не удалось загрузить модель: {str(e)}")

    def synthesize_to_file(self, text: str, output_path: str, speaker: Optional[str] = None) -> str:
        """
        Синтезирует полный текст и сохраняет в WAV файл.
        Возвращает путь к сгенерированному файлу.
        """
        speaker_to_use = speaker if speaker else self.speaker
        return self.synthesize_auto_and_save(
            text, 
            output_file=str(output_path), 
            speaker=speaker_to_use, 
            sample_rate=self.sample_rate
        )

    def stream_audio(self, text: str, abort_event: threading.Event, speaker: Optional[str] = None) -> Generator[np.ndarray, None, None]:
        """
        Генерирует аудио потоково (чанками).
        Если abort_event.is_set() становится True, генерация должна быть немедленно прервана.
        Yields numpy arrays (float32).
        """
        if not self._model_loaded:
            raise RuntimeError("Модель не загружена")
            
        speaker_to_use = speaker if speaker else self.speaker
        
        # Если SSML разметка, не разбиваем текст на куски
        if '<speak' in text or '<prosody' in text or '<break' in text:
            if abort_event.is_set():
                return
            audio = self.synthesize_ssml(text, speaker=speaker_to_use, sample_rate=self.sample_rate)
            if not abort_event.is_set():
                yield audio.cpu().numpy()
        else:
            chunks = self._split_text(text)
            for chunk in chunks:
                if abort_event.is_set():
                    break
                
                try:
                    audio = self.model.apply_tts(
                        text=chunk,
                        speaker=speaker_to_use,
                        sample_rate=self.sample_rate
                    )
                except TypeError:
                    audio = self.model.apply_tts(
                        text=chunk,
                        speaker=speaker_to_use,
                        sample_rate=self.sample_rate,
                        put_accent=True,
                        put_yo=True
                    )
                
                if abort_event.is_set():
                    break
                    
                yield audio.cpu().numpy()
    
    def _split_text(self, text: str, max_length: int = 500) -> list:
        """Разбивает текст на части по предложениям"""
        if not text.strip():
            return []
        
        sentences = re.split(r'(?<=[.!?])\s+', text.strip())
        result, current = [], ""
        
        for sent in sentences:
            if not sent.strip(): continue
            if len(current) + len(sent) > max_length:
                if current: result.append(current.strip())
                current = sent.strip()
            else:
                current = f"{current} {sent.strip()}".strip() if current else sent.strip()
        
        if current: result.append(current.strip())
        return result
    
    def _synthesize_core(self, 
                        text: Optional[str] = None,
                        ssml_text: Optional[str] = None,
                        speaker: str = 'kseniya',
                        sample_rate: int = 48000,
                        **kwargs) -> torch.Tensor:
        """
        Ядро синтеза — общий метод для текста и SSML
        """
        if not self._model_loaded:
            raise RuntimeError("Модель не загружена")
        
        # Автоисправление спикера
        if speaker == 'xenia' and 'kseniya' in self.model.speakers:
            speaker = 'kseniya'
        
        # Для обычного текста — разбиваем на части
        if text is not None:
            chunks = self._split_text(text)
            audio_chunks = []
            
            print(f"🎯 Синтез текста. Разделено на {len(chunks)} частей")
            
            with torch.no_grad():
                for i, chunk in enumerate(chunks):
                    print(f"🔊 Часть {i+1}/{len(chunks)}: {len(chunk)} символов")
                    
                    # Попытка с полным набором параметров
                    try:
                        audio = self.model.apply_tts(
                            text=chunk,
                            speaker=speaker,
                            sample_rate=sample_rate,
                            **kwargs
                        )
                    except TypeError:
                        # Если параметры не поддерживаются — повторяем без них
                        print("⚠️ Некоторые параметры не поддерживаются, повтор без расширенных опций...")
                        audio = self.model.apply_tts(
                            text=chunk,
                            speaker=speaker,
                            sample_rate=sample_rate,
                            put_accent=kwargs.get('put_accent', True),
                            put_yo=kwargs.get('put_yo', True)
                        )
                    
                    audio_chunks.append(audio)
            
            return torch.cat(audio_chunks, dim=0)
        
        # Для SSML — передаём как есть (без разбивки!)
        elif ssml_text is not None:
            print(f"🎯 Синтез SSML разметки (без разбивки на части)...")
            
            with torch.no_grad():
                try:
                    audio = self.model.apply_tts(
                        ssml_text=ssml_text,
                        speaker=speaker,
                        sample_rate=sample_rate
                    )
                except Exception as e:
                    raise RuntimeError(f"Ошибка SSML синтеза: {str(e)}")
            
            return audio
        
        else:
            raise ValueError("Необходимо указать либо text, либо ssml_text")
    
    # ============ РЕЖИМ 1: Обычный текст ============
    def synthesize_text(self, 
                       text: str, 
                       speaker: str = 'kseniya', 
                       sample_rate: int = 48000,
                       put_accent: bool = True, 
                       put_yo: bool = True,
                       put_stress_homo: bool = True, 
                       put_yo_homo: bool = True) -> torch.Tensor:
        """
        Синтезирует аудио из обычного текста
        
        Args:
            text: Текст для синтеза
            speaker: Имя спикера ('kseniya', 'aidar', 'baya' и др.)
            sample_rate: Частота дискретизации (8000, 16000, 24000, 48000)
            put_accent: Расставлять ударения автоматически
            put_yo: Заменять 'е' на 'ё' где нужно
            put_stress_homo: Обрабатывать гомографы (может не поддерживаться)
            put_yo_homo: Обрабатывать ё в гомографах (может не поддерживаться)
        
        Returns:
            torch.Tensor: Аудиоданные
        """
        return self._synthesize_core(
            text=text,
            speaker=speaker,
            sample_rate=sample_rate,
            put_accent=put_accent,
            put_yo=put_yo,
            put_stress_homo=put_stress_homo,
            put_yo_homo=put_yo_homo
        )
    
    # ============ РЕЖИМ 2: SSML ============
    def synthesize_ssml(self, 
                       ssml_text: str, 
                       speaker: str = 'kseniya', 
                       sample_rate: int = 48000) -> torch.Tensor:
        """
        Синтезирует аудио из SSML разметки
        
        Поддерживаемые теги:
          - <prosody rate="slow/fast/x-slow/x-fast"> — скорость
          - <prosody pitch="high/low/x-high/x-low"> — тон
          - <break time="500ms/2s"> — пауза
          - <s> — граница предложения
          - <p> — граница абзаца
        
        Важно: параметры put_accent/put_yo ИГНОРИРУЮТСЯ — управление через разметку!
        
        Args:
            ssml_text: SSML разметка (обязательно с тегом <speak>)
            speaker: Имя спикера
            sample_rate: Частота дискретизации
        
        Returns:
            torch.Tensor: Аудиоданные
        """
        # Автоматически оборачиваем в <speak> если нужно
        if not ssml_text.strip().startswith('<speak'):
            ssml_text = f"<speak>{ssml_text}</speak>"
            print("ℹ️ SSML автоматически обёрнут в <speak> теги")
        
        return self._synthesize_core(
            ssml_text=ssml_text,
            speaker=speaker,
            sample_rate=sample_rate
        )
    
    # ============ РЕЖИМ 3: Автоопределение ============
    def synthesize_auto(self, 
                       input_text: str, 
                       speaker: str = 'kseniya', 
                       sample_rate: int = 48000,
                       **kwargs) -> torch.Tensor:
        """
        Автоматически определяет режим синтеза:
          - Если содержит <speak> или <prosody> → SSML режим
          - Иначе → обычный текст
        
        Удобно для динамических сценариев
        """
        if '<speak' in input_text or '<prosody' in input_text or '<break' in input_text:
            print("🔍 Обнаружена SSML разметка — используется режим SSML")
            return self.synthesize_ssml(ssml_text=input_text, speaker=speaker, sample_rate=sample_rate)
        else:
            print("🔍 Обычный текст — используется режим текста")
            return self.synthesize_text(
                text=input_text,
                speaker=speaker,
                sample_rate=sample_rate,
                **kwargs
            )
    
    # ============ ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ ============
    def save_audio(self, audio: torch.Tensor, filename: str, sample_rate: int = 48000) -> str:
        """Сохраняет аудио в файл WAV"""
        print(f"📁 Сохраняем аудио в {filename}")
        os.makedirs(os.path.dirname(os.path.abspath(filename)), exist_ok=True)
        filename_path = Path(filename)
        filename_path = filename_path if filename_path.suffix == '.wav' else filename_path.with_suffix('.wav')
        sf.write(str(filename_path), audio.cpu().numpy(), sample_rate)
        return os.path.abspath(filename_path)
    
    # ============ СИНХРОННЫЕ МЕТОДЫ СОХРАНЕНИЯ ============
    def synthesize_text_and_save(self, 
                                text: str, 
                                output_file: str,
                                speaker: str = 'kseniya', 
                                sample_rate: int = 48000,
                                **kwargs) -> str:
        """Синтез + сохранение для обычного текста"""
        audio = self.synthesize_text(text, speaker, sample_rate, **kwargs)
        return self.save_audio(audio, output_file, sample_rate)
    
    def synthesize_ssml_and_save(self, 
                                ssml_text: str, 
                                output_file: str,
                                speaker: str = 'kseniya', 
                                sample_rate: int = 48000) -> str:
        """Синтез + сохранение для SSML"""
        audio = self.synthesize_ssml(ssml_text, speaker, sample_rate)
        return self.save_audio(audio, output_file, sample_rate)
    
    def synthesize_auto_and_save(self, 
                                input_text: str, 
                                output_file: str,
                                speaker: str = 'kseniya', 
                                sample_rate: int = 48000,
                                **kwargs) -> str:
        """Синтез + сохранение с автоопределением режима"""
        audio = self.synthesize_auto(input_text, speaker, sample_rate, **kwargs)
        return self.save_audio(audio, output_file, sample_rate)
    
    # ============ АСИНХРОННЫЕ МЕТОДЫ (фоновая генерация) ============
    def _create_async_task(self, synthesize_func: Callable, callback: Optional[Callable] = None, **kwargs):
        """Вспомогательный метод для создания фоновых задач"""
        def task():
            try:
                result = synthesize_func(**kwargs)
                if callback:
                    callback(result, None)
            except Exception as e:
                if callback:
                    callback(None, e)
        
        thread = threading.Thread(target=task, daemon=True)
        thread.start()
        return thread
    
    def synthesize_text_async(self, 
                             text: str, 
                             output_file: str,
                             speaker: str = 'kseniya', 
                             sample_rate: int = 48000,
                             callback: Optional[Callable[[str, Optional[Exception]], None]] = None,
                             **kwargs) -> threading.Thread:
        """Фоновый синтез обычного текста"""
        return self._create_async_task(
            self.synthesize_text_and_save,
            callback,
            text=text,
            output_file=output_file,
            speaker=speaker,
            sample_rate=sample_rate,
            **kwargs
        )
    
    def synthesize_ssml_async(self, 
                             ssml_text: str, 
                             output_file: str,
                             speaker: str = 'kseniya', 
                             sample_rate: int = 48000,
                             callback: Optional[Callable[[str, Optional[Exception]], None]] = None) -> threading.Thread:
        """Фоновый синтез SSML"""
        return self._create_async_task(
            self.synthesize_ssml_and_save,
            callback,
            ssml_text=ssml_text,
            output_file=output_file,
            speaker=speaker,
            sample_rate=sample_rate
        )
    
    def synthesize_auto_async(self, 
                             input_text: str, 
                             output_file: str = "audio.wav",
                             speaker: str = 'kseniya', 
                             sample_rate: int = 48000,
                             callback: Optional[Callable[[str, Optional[Exception]], None]] = None,
                             **kwargs) -> threading.Thread:
        """Фоновый синтез с автоопределением режима"""
        return self._create_async_task(
            self.synthesize_auto_and_save,
            callback,
            input_text=input_text,
            output_file=output_file,
            speaker=speaker,
            sample_rate=sample_rate,
            **kwargs
        )


# ============ ПРИМЕРЫ ИСПОЛЬЗОВАНИЯ ============
if False:
    print("="*60)
    print("🚀 Инициализация Silero TTS (CUDA автоматически)")
    print("="*60)
    
    # Инициализация (модель загружается сразу)
    tts = SileroTTS(language='ru', model_id='v5_ru', speaker='xenia')  # xenia → kseniya автоматически
    
    # === РЕЖИМ 1: Обычный текст ===
    print("\n" + "="*60)
    print("🔤 РЕЖИМ 1: Обычный текст")
    print("="*60)
    text = "Меня зовут Лева Королев. Я из готов. И я уже готов открыть все ваши замки любой сложности!"
    result1 = tts.synthesize_text_and_save(
        text=text,
        output_file="text_mode.wav",
        speaker='kseniya',
        sample_rate=48000,
        put_accent=True,
        put_yo=True
    )
    print(f"✅ Сохранено: {result1}")
    
    # === РЕЖИМ 2: SSML ===
    print("\n" + "="*60)
    print("🎨 РЕЖИМ 2: SSML разметка")
    print("="*60)
    ssml = """
    <speak>
        Привет! <break time="500ms"/>
        <prosody rate="slow">Я говорю медленно</prosody>,
        <prosody rate="fast">а теперь быстро</prosody>.
        <break time="1s"/>
        <prosody pitch="x-high">Высокий тон</prosody>,
        <prosody pitch="x-low">низкий тон</prosody>.
    </speak>
    """
    result2 = tts.synthesize_ssml_and_save(
        ssml_text=ssml,
        output_file="ssml_mode.wav",
        speaker='kseniya',
        sample_rate=48000
    )
    print(f"✅ Сохранено: {result2}")
    
    # === РЕЖИМ 3: Автоопределение ===
    print("\n" + "="*60)
    print("🤖 РЕЖИМ 3: Автоопределение режима")
    print("="*60)
    mixed_input = "Обычный текст без разметки"
    result3 = tts.synthesize_auto_and_save(
        input_text=mixed_input,
        output_file="auto_mode.wav",
        speaker='kseniya'
    )
    print(f"✅ Сохранено: {result3}")
    
    # === ФОНОВАЯ ГЕНЕРАЦИЯ ===
    print("\n" + "="*60)
    print("🔄 ФОНОВАЯ ГЕНЕРАЦИЯ (без блокировки основного потока)")
    print("="*60)
    
    def callback(path, err):
        if err:
            print(f"❌ Ошибка в фоновом потоке: {err}")
        else:
            print(f"✅ Фоновая генерация завершена: {path}")
    
    # Запускаем фоновую генерацию SSML
    thread = tts.synthesize_ssml_async(
        ssml_text="<speak>Это фоновая генерация SSML!</speak>",
        output_file="async_ssml.wav",
        callback=callback
    )
    
    # Основной поток продолжает работу
    print("💡 Основной поток не заблокирован — можно выполнять другие задачи...")
    time.sleep(1)
    print("🔄 Основной поток завершил свою работу")
    
    # Дожидаемся завершения фонового потока для демонстрации
    thread.join(timeout=5.0)
    print("\n🎉 Все задачи выполнены!")

