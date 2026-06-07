# utils/settings.py
import json
import os
from pathlib import Path
from typing import Any, Optional, Dict, Union


class Settings:
    """
    Менеджер настроек приложения с хранением в JSON-файле.
    """
    
    def __init__(self, filepath: str = 'settings.json', create_if_missing: bool = True):
        import copy
        self._filepath = Path(__file__).parent / filepath
        self._settings: Dict[str, Any] = {}
        self._default_settings = self._get_default_structure()
        
        if create_if_missing and not self._filepath.exists():
            self._settings = copy.deepcopy(self._default_settings)
            self._save()
            print(f"✓ Создан файл настроек: {self._filepath.absolute()}")
        else:
            self._load()
    
    @staticmethod
    def _get_default_structure() -> Dict[str, Any]:
        """Возвращает структуру дефолтных настроек."""
        return {
            "model": {
            },
            "current_model": "qwen2.5-coder-7b",
            "stt_provider": "whisper",
            "stt_model": "tiny",
            "tts_backend": "silero",
            "tts_silero_model": "v5_ru",
            "tts_speaker": "kseniya",
            "tts_xtts_model": "v2.0.2",
            "tts_xtts_speaker": "",
            "tts_streaming": True,
            "system_device": "cuda" if __import__("torch").cuda.is_available() else "cpu",

            "llm": {
                "tool_only_voice_output": True,
                "enabled_tools": ["text_to_audio"],
                "user_system_prompt": ""
            },
            
            # === AI Engine v2 ===
            "memory": {
                "context_window_size": 25,
                "history_file": "app/data/chat_history.json",
                "include_reasoning_in_context": True,
                "reasoning_max_chars": 4000
            },
            "controls": {
                "ptt_mode": "push_to_talk",
                "ptt_keys": "right alt",
                "ptt_pre_roll_seconds": 1.0,
                "ptt_post_roll_seconds": 1.0,
                "ptt_chunk_seconds": 0.1,
                "ptt_ring_buffer_seconds": 6.0,
                "ptt_sample_rate": 16000
            }
        }
    
    def _load(self) -> None:
        """Загрузка настроек из файла с валидацией структуры."""
        import copy
        try:
            if not self._filepath.exists():
                self._settings = copy.deepcopy(self._default_settings)
                self._save()
                return
            
            with open(self._filepath, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
            
            # Валидация и мерж с дефолтной структурой (deep merge)
            self._settings = self._merge_with_defaults(loaded, self._default_settings)

            def _has_nested_key(data: dict, keys: tuple[str, ...]) -> bool:
                current = data
                for key in keys:
                    if not isinstance(current, dict) or key not in current:
                        return False
                    current = current[key]
                return True

            needs_save = False
            if isinstance(loaded, dict):
                if not _has_nested_key(loaded, ("llm", "user_system_prompt")):
                    needs_save = True
            else:
                needs_save = True

            if needs_save:
                self._save()
            
        except json.JSONDecodeError as e:
            print(f"⚠ Ошибка парсинга {self._filepath}: {e}")
            print("   Используются дефолтные настройки.")
            self._settings = copy.deepcopy(self._default_settings)
            self._save()
        except Exception as e:
            print(f"⚠ Ошибка загрузки настроек: {e}")
            self._settings = copy.deepcopy(self._default_settings)
            
    def _merge_with_defaults(self, loaded: Dict, defaults: Dict) -> Dict:
        """Рекурсивно мержит загруженные настройки с дефолтной структурой, сохраняя любые новые поля."""
        import copy
        result = copy.deepcopy(defaults)
        
        def _deep_update(target: dict, source: dict):
            for k, v in source.items():
                if isinstance(v, dict):
                    if k not in target or not isinstance(target[k], dict):
                        target[k] = {}
                    _deep_update(target[k], v)
                else:
                    target[k] = copy.deepcopy(v)

        # Мержим пользовательские настройки поверх дефолтных
        _deep_update(result, loaded)
        
        # Защита от несуществующей текущей модели
        if result.get("current_model") not in result.get("model", {}):
            if result.get("model"):
                result["current_model"] = next(iter(result.get("model", {})))
                print(f"  ⚠ current_model не найден, установлен: {result['current_model']}")
            else:
                result["model"] = copy.deepcopy(self._default_settings["model"])
                result["current_model"] = self._default_settings["current_model"]
                print("  ⚠ Список моделей пуст, восстановлены дефолтные значения")
        
        return result
    
    def _save(self) -> bool:
        """Сохранение настроек в файл."""
        try:
            self._filepath.parent.mkdir(parents=True, exist_ok=True)
            
            with open(self._filepath, 'w', encoding='utf-8') as f:
                json.dump(self._settings, f, ensure_ascii=False, indent=2)
            
            return True
        except Exception as e:
            print(f"✗ Ошибка сохранения настроек: {e}")
            return False
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        Получение значения параметра через точечную нотацию.
        
        Примеры:
            settings.get('tts_speaker')
            settings.get('model.qwen2.5-coder-7b.api_key')
            settings.get('current_model')
        """
        keys = key.split('.')
        value = self._settings
        
        try:
            for k in keys:
                if isinstance(value, dict):
                    value = value.get(k, default if k == keys[-1] else {})
                else:
                    return default if k == keys[-1] else {}
            return value if value is not None else default
        except (KeyError, TypeError):
            return default
    
    def set(self, key: str, value: Any, auto_save: bool = True) -> bool:
        """
        Изменение параметра через точечную нотацию.
        
        Примеры:
            settings.set('tts_speaker', 'baya')
            settings.set('model.qwen2.5-coder-7b.api_key', 'sk-...')
        """
        keys = key.split('.')
        target = self._settings
        
        try:
            for k in keys[:-1]:
                if isinstance(target, dict):
                    if k not in target:
                        target[k] = {}
                    target = target[k]
                else:
                    return False
            
            last_key = keys[-1]
            if isinstance(target, dict):
                target[last_key] = value
            else:
                return False
            
            # Валидация: если изменили current_model, проверяем существование
            if key == "current_model" and value not in self._settings.get("model", {}):
                print(f"⚠ Предупреждение: модель '{value}' не найдена в списке моделей")
            
            if auto_save:
                return self._save()
            return True
            
        except Exception as e:
            print(f"✗ Ошибка установки параметра '{key}': {e}")
            return False
    
    # === Методы для работы с моделями ===
    
    def add_model(self, model_key: str, model_config: Dict[str, Any], set_as_current: bool = False, auto_save: bool = True) -> bool:
        """
        Добавление новой модели в словарь.
        
        Args:
            model_key: Уникальный ключ модели (например: 'gpt-4o')
            model_config: Словарь с параметрами модели
            set_as_current: Сделать ли модель текущей после добавления
            auto_save: Сохранять ли изменения сразу
            
        Пример:
            settings.add_model('gpt-4o', {
                'model': 'gpt-4o',
                'api_key': 'sk-...',
                'provider': 'openai',
                'model_id': 'gpt-4o',
                'model_name': 'GPT-4o'
            })
        """
        # Заполняем обязательные поля значениями по умолчанию при необходимости
        required_fields = {
            'model': model_key,
            'api_key': '',
            'provider': 'unknown',
            'model_id': model_key,
            'model_name': model_key
        }
        
        for field, default_value in required_fields.items():
            if field not in model_config:
                model_config[field] = default_value
        
        # Добавляем/обновляем модель
        if "model" not in self._settings or not isinstance(self._settings["model"], dict):
            self._settings["model"] = {}
        
        self._settings["model"][model_key] = model_config
        
        if set_as_current:
            self._settings["current_model"] = model_key
        
        return self._save() if auto_save else True
    
    def remove_model(self, model_key: str, auto_save: bool = True) -> bool:
        """
        Удаление модели из словаря.
        
        Если удаляемая модель является текущей — автоматически выбирается первая доступная.
        """
        if model_key not in self._settings.get("model", {}):
            return False
        
        del self._settings["model"][model_key]
        
        # Если удалили текущую модель — выбираем новую
        if self._settings.get("current_model") == model_key:
            if self._settings["model"]:
                self._settings["current_model"] = next(iter(self._settings["model"]))
                print(f"⚠ Модель '{model_key}' была текущей. Новая текущая модель: {self._settings['current_model']}")
            else:
                # Восстанавливаем дефолтную модель
                default_key = next(iter(self._default_settings["model"]))
                self.add_model(default_key, self._default_settings["model"][default_key], set_as_current=True, auto_save=False)
                print(f"⚠ Список моделей опустел. Восстановлена дефолтная модель: {default_key}")
        
        return self._save() if auto_save else True
    
    def set_current_model(self, model_key: str, auto_save: bool = True) -> bool:
        """
        Установка текущей модели.
        
        Возвращает False, если модель с указанным ключом не существует.
        """
        if model_key not in self._settings.get("model", {}):
            print(f"✗ Модель '{model_key}' не найдена в списке доступных моделей")
            return False
        
        self._settings["current_model"] = model_key
        return self._save() if auto_save else True
    
    def get_current_model(self) -> str:
        """Получение ключа текущей модели."""
        return self._settings.get("current_model", "")
    
    def get_current_model_config(self) -> Dict[str, Any]:
        """Получение конфигурации текущей модели."""
        current_key = self._settings.get("current_model", "")
        return self._settings.get("model", {}).get(current_key, {})
    
    def get_models(self) -> Dict[str, Dict[str, Any]]:
        """Получение словаря всех моделей."""
        return self._settings.get("model", {}).copy()
    
    def get_model(self, model_key: str) -> Optional[Dict[str, Any]]:
        """Получение конфигурации конкретной модели по ключу."""
        return self._settings.get("model", {}).get(model_key)
    
    # === Прочие методы ===
    
    def reload(self) -> None:
        """Перезагрузка настроек из файла."""
        self._load()
    
    def save(self) -> bool:
        """Принудительное сохранение настроек."""
        return self._save()
    
    def get_api_key_for_model(self, model_key: Optional[str] = None) -> str:
        """Получает API ключ для модели."""
        model = model_key or self.get('current_model')
        api_key = self.get('model', {}).get(model, {}).get('api_key', '')
        
        # Если не найдено, пробуем из переменной окружения
        if not api_key:
            import os
            api_key = os.getenv('OPENROUTER_API_KEY', os.getenv('OPENAI_API_KEY', ''))
        
        return api_key
    
    def get_base_url_for_model(self, model_key: Optional[str] = None) -> str:
        """Получает base_url для кастомных/локальных серверов."""
        model = model_key or self.get('current_model')
        base_url = self.get('model', {}).get(model, {}).get('base_url', '')
        return base_url
    
    def get_model_id_for_model(self, model_key: Optional[str] = None) -> str:
        """Получает model_id для модели."""
        model = model_key or self.get('current_model')
        return self.get(f'model.{model}.model_id', model)
    
    @property
    def all(self) -> Dict[str, Any]:
        """Получение копии всех настроек."""
        return self._settings.copy()
    
    def __repr__(self) -> str:
        current = self._settings.get('current_model', 'N/A')
        models_count = len(self._settings.get('model', {}))
        return f"Settings(filepath='{self._filepath}', current='{current}', models={models_count})"



# Пример использования и тестирование
# if __name__ == "__main__":
#     # Создание менеджера (файл создастся автоматически)
#     settings = Settings('settings.json')
    
#     print("Текущие настройки:")
#     print(json.dumps(settings.all, ensure_ascii=False, indent=2))
    
#     print("\n" + "="*50)
#     print("Примеры использования:")
#     print("="*50)
    
#     # Получение текущей модели
#     current = settings.get_current_model()
#     print(f"Текущая модель: {current}")
#     print(f"Конфигурация текущей модели: {settings.get_current_model_config()}")
    
#     # Получение параметров через точечную нотацию
#     print(f"TTS спикер: {settings.get('tts_speaker')}")
#     print(f"STT провайдер: {settings.get('stt_provider')}")
#     print(f"API ключ текущей модели: {settings.get(f'model.{current}.api_key')}")
    
#     # Изменение параметра
#     settings.set('tts_speaker', 'baya')
#     print(f"\nПосле изменения: TTS спикер = {settings.get('tts_speaker')}")
    
#     # Добавление новой модели
#     print("\nДобавление новой модели 'gpt-4o'...")
#     settings.add_model('gpt-4o', {
#         'model': 'gpt-4o',
#         'api_key': 'sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx',
#         'provider': 'openai',
#         'model_id': 'gpt-4o-2024-08',
#         'model_name': 'GPT-4o (OpenAI)'
#     })
#     print(f"Всего моделей: {len(settings.get_models())}")
    
#     # Установка текущей модели
#     print("\nУстановка 'gpt-4o' как текущей модели...")
#     settings.set_current_model('gpt-4o')
#     print(f"Текущая модель: {settings.get_current_model()}")
    
#     # Удаление модели
#     print("\nУдаление модели 'qwen2.5-coder-7b'...")
#     settings.remove_model('qw