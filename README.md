# Airi - Многопоточная система обработки речи

> Полностью переработанная архитектура с использованием OpenAI SDK (OpenRouter), многопоточностью и параллельной обработкой

## 🚀 Что нового

### Основные изменения

✅ **OpenRouter с OpenAI SDK** - Используется официальный OpenAI SDK вместо raw HTTP запросов  
✅ **Многопоточная архитектура** - 3 независимых потока для записи, обработки и воспроизведения  
✅ **Параллельная обработка Tool Calls** - Инструменты выполняются одновременно, не последовательно  
✅ **Streaming с буферизацией** - Сборка чанков streaming ответа в полное сообщение  
✅ **Общие утилиты (llm_common)** - Переиспользуемые компоненты для будущих LLM обработчиков  
✅ **Text-to-Speech (TTS)** - Силеро синтез речи с воспроизведением в фоне  
✅ **Thread-safe очереди** - Синхронизация между потоками через queue.Queue  
✅ **Graceful shutdown** - Корректное завершение всех потоков при Ctrl+C  

---

## 📁 Новые файлы

| Файл | Описание |
|------|---------|
| [app/llm_common.py](app/llm_common.py) | Общие утилиты для всех LLM обработчиков |
| [app/openrouter_handler.py](app/openrouter_handler.py) | Обработчик OpenRouter с OpenAI SDK |
| [app/tts.py](app/tts.py) | Text-to-Speech (Silero) с воспроизведением |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Подробное описание архитектуры |
| [QUICKSTART.md](QUICKSTART.md) | Быстрый старт за 5 минут |
| [EXAMPLES.md](EXAMPLES.md) | 13 практических примеров использования |

---

## 🏗️ Архитектура

### Три независимых потока

```
┌─────────────────────────────────────┐
│  Поток 1: Запись голоса (Recording) │  → voice_input_queue
├─────────────────────────────────────┤
│   • Слушает микрофон                │
│   • STT (Vosk/Whisper)              │
│   • Распознает текст                │
└─────────────────────────────────────┘
             ↓
┌─────────────────────────────────────┐
│ Поток 2: Обработка ИИ (AI Processing)│  → ai_response_queue
├─────────────────────────────────────┤
│  • Читает из очереди                │
│  • Отправляет к OpenRouter          │
│  • Обработка Tool Calls (параллель) │
│  • Сохраняет в историю              │
└─────────────────────────────────────┘
             ↓
┌─────────────────────────────────────┐
│  Поток 3: Воспроизведение (Playback)│
├─────────────────────────────────────┤
│  • Синтезирует TTS (Silero)        │
│  • Воспроизводит через динамики    │
│  • Работает параллельно             │
└─────────────────────────────────────┘
```

### Очереди (Thread-Safe)

```python
voice_input_queue   # str → распознанный текст
ai_response_queue   # Dict → ответ ИИ
state_lock         # Lock → синхронизация доступа
```

---

## 🚄 Быстрый старт

### 1. Установка

```bash
pip install -r requirements.txt
```

### 2. API ключ

```bash
set OPENROUTER_API_KEY=sk_ваш_ключ
```

### 3. Запуск

```bash
python app/main.py
```

### 4. Использование

Просто говорите в микрофон! Приложение автоматически:
- Распознает речь
- Отправляет к ИИ
- Обрабатывает Tool Calls
- Синтезирует и воспроизводит ответ

**Остановка**: `Ctrl+C`

---

## 💻 Примеры кода

### Базовое использование ИИ

```python
from app.openrouter_handler import OpenRouterHandler

handler = OpenRouterHandler(api_key="sk_...")
response = handler.send_message([
    {"role": "user", "content": "Привет!"}
])
print(response["content"])
```

### Использование Tool Calling

```python
handler = OpenRouterHandler(
    api_key="sk_...",
    tool_handlers={
        "search_web": search_function,
        "get_weather": weather_function,
    }
)

# Tool calls обрабатываются автоматически параллельно!
response = handler.send_message(messages)
```

### Синтез и воспроизведение речи

```python
from app.tts import TTSController

tts = TTSController()
tts.initialize()
tts.speak("Привет, это синтезированная речь")
tts.wait_completion()
```

### Работа с историей

```python
from app.main import load_history_from_file, save_history_to_file

messages = load_history_from_file(Path("app/data/chat_history.json"))
print(f"Загружено {len(messages)} сообщений")
```

**Больше примеров**: [EXAMPLES.md](EXAMPLES.md)

---

## 🔧 Конфигурация

### settings.json

```json
{
  "current_model": "openai/gpt-4-turbo",
  "stt_provider": "whisper",
  "stt_model": "small",
  "tts_speaker": "kseniya"
}
```

### Доступные модели

| Модель | Скорость | Качество | Стоимость |
|--------|----------|----------|----------|
| openai/gpt-4-turbo | Медленно | Отличное | Высокая |
| openai/gpt-4 | Медленно | Отличное | Высокая |
| openai/gpt-3.5-turbo | Быстро | Хорошее | Низкая |
| qwen2.5-7b | Быстро | Хорошее | Низкая |
| deepseek-chat | Средне | Хорошее | Низкая |

### STT бэкенды

```bash
python app/main.py vosk vosk-model-small-ru-0.22    # Быстро, локально
python app/main.py whisper small                     # Точнее
python app/main.py google                            # Онлайн
```

### TTS голоса

```python
tts.initialize(speaker='kseniya')      # Женский (default)
tts.initialize(speaker='baya')         # Женский (альт)
tts.initialize(speaker='en_0')         # Английский мужской
```

---

## 📊 Структура проекта

```
app/
  ├── main.py                     # Главное приложение (с потоками)
  ├── llm_common.py              # Общие утилиты для LLM
  ├── openrouter_handler.py      # Обработчик OpenRouter
  ├── tts.py                     # Text-to-Speech (Silero)
  ├── STT/
  │   ├── __init__.py            # Factory для STT
  │   ├── vosk_backend.py
  │   ├── whisper_backend.py
  │   └── google_backend.py
  ├── TTS/
  │   ├── __init__.py
  │   └── silero.py
  ├── utils/
  │   └── settings.py            # Управление настройками
  ├── data/
  │   └── chat_history.json      # История диалога
  ├── model/                      # Кэш моделей
  │   ├── whisper/
  │   ├── vosk/
  │   └── silero/
  └── config/
      └── logs.log               # Логи приложения

.github/
  └── copilot-instructions.md    # Инструкции для AI

ARCHITECTURE.md                  # Подробное описание архитектуры
QUICKSTART.md                    # Быстрый старт
EXAMPLES.md                      # 13 примеров использования
```

---

## 🎯 Ключевые особенности

### 1. Многопоточность

Все три компонента работают **параллельно**:
- Можно говорить, пока ИИ обрабатывает
- Можно говорить, пока воспроизводится ответ
- Запись продолжается во время всех операций

### 2. Tool Calling

Tool calls выполняются **параллельно** в ThreadPoolExecutor:
```python
# Все 3 функции выполняются одновременно, не по очереди
search_web(query)
get_weather(location)
get_time()
```

### 3. Streaming

Ответ обрабатывается по частям (чанкам):
- Прогресс видн сразу
- Не нужно ждать полного ответа
- Можно начать воспроизведение раньше

### 4. История

Автоматически сохраняется после каждого обновления:
- Сообщения пользователя
- Ответы ИИ
- Reasoning процесс
- Tool calls и результаты
- Timestamps и metadata

### 5. Graceful Shutdown

Нажимаете `Ctrl+C`:
- Устанавливается флаг `app_state.running = False`
- Все потоки корректно завершают работу
- Нет зависших процессов

---

## 🔄 Поток выполнения

```
1. Пользователь говорит в микрофон
   ↓
2. Recording Thread распознает текст
   ↓
3. Добавляет в voice_input_queue
   ↓
4. AI Processing Thread берет текст
   ↓
5. Добавляет в историю, сохраняет
   ↓
6. Отправляет к OpenRouter
   ↓
7. Streaming ответ собирается в StreamChunkBuffer
   ↓
8. Обнаружены Tool Calls → выполняются параллельно
   ↓
9. Результаты Tool Calls отправляются обратно к ИИ
   ↓
10. Получен финальный ответ
    ↓
11. Добавлено в историю, сохранено
    ↓
12. Добавлено в ai_response_queue
    ↓
13. Playback Thread берет ответ
    ↓
14. Синтезирует TTS
    ↓
15. Воспроизводит звук
    ↓
16. Возвращается к шагу 1
```

---

## 📝 Логирование

Все события логируются в `app/config/logs.log`:

```bash
tail -f app/config/logs.log
```

Уровни:
- **DEBUG** - Детальная отладочная информация
- **INFO** - Основные события (хорошее для мониторинга)
- **WARNING** - Предупреждения
- **ERROR** - Ошибки

Emoji для быстрого сканирования:
```
🎙️ Главное приложение
🎤 Запись активна
🔤 Текст распознан
📝 Сообщение сохранено
📤 Отправка к ИИ
✅ Успех
❌ Ошибка
⚠️ Предупреждение
🔊 Воспроизведение
🤖 Ответ ИИ
```

---

## 🐛 Решение проблем

### "Микрофон не найден"
```bash
python app/main.py  # Выведет список доступных
```

### "OPENROUTER_API_KEY не установлен"
```bash
echo %OPENROUTER_API_KEY%  # Проверить переменную
```

### "Модели скачиваются долго" (первый запуск)
- Это нормально, требуется 5-30 секунд
- Модели кэшируются в `app/model/`

### Нет звука из TTS
- Проверьте громкость ОС
- Убедитесь, что динамики включены
- Приложение продолжит работу даже если TTS не инициализирован

---

## 📚 Документация

| Документ | Для кого |
|----------|----------|
| [QUICKSTART.md](QUICKSTART.md) | Новые пользователи (5 минут) |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Разработчики (понимание системы) |
| [EXAMPLES.md](EXAMPLES.md) | Программисты (13 примеров кода) |
| [.github/copilot-instructions.md](.github/copilot-instructions.md) | AI агенты (для разработки) |

---

## 🚀 Следующие шаги

1. **Запустить приложение** → [QUICKSTART.md](QUICKSTART.md)
2. **Изучить архитектуру** → [ARCHITECTURE.md](ARCHITECTURE.md)
3. **Посмотреть примеры** → [EXAMPLES.md](EXAMPLES.md)
4. **Добавить инструменты** → Редактировать `get_tool_handlers()`
5. **Поддержка других LLM** → Создать класс наподобие `OpenRouterHandler`

---

## ✨ Основные преимущества

✅ **Параллельная работа** - Все компоненты работают одновременно  
✅ **Быстрая реакция** - Tool calls выполняются параллельно  
✅ **Стабильность** - Thread-safe очереди, graceful shutdown  
✅ **Расширяемость** - Легко добавить новые бэкенды и провайдеров  
✅ **Отладка** - Подробное логирование всех событий  
✅ **Документация** - Четыре файла с примерами и инструкциями  

---

## 📦 Зависимости

```
openai>=1.3.0           # OpenRouter via OpenAI SDK
torch>=2.4.1            # Silero TTS
vosk==0.3.45            # STT
openai-whisper>=20250625 # STT альтернатива
pygame>=2.5.0           # Воспроизведение звука
SpeechRecognition>=3.14 # STT
```

---

**Версия**: 2.0 (переработанная архитектура)  
**Дата**: Январь 2026  
**Язык**: Python 3.8+  

🎉 **Готово к использованию!**
