# 📋 AIRI 2.0 - План развития проекта

**Версия:** 3.0  
**Дата начала:** 18 февраля 2026  
**Длительность:** 8 недель  
**Команда:** 5 человек

---

## 👥 Команда и распределение ролей

| Роль | Участник | Основные задачи |
|------|----------|-----------------|
| **Главный по ИИ (Lead)** | Вы | Архитектура LLM, промпты, интеграции, code review |
| **Frontend разработчик** | 1 человек | PyQt UI, визуализация ответов ИИ |
| **Backend #1** | Универсал | История/контекст, база данных, API |
| **Backend #2** | Универсал + Аналитик | Тесты, мониторинг, аналитика логов |
| **Backend #3** | Универсал | Локальные модели, инфраструктура, Docker |

---

## 🎯 Приоритеты задач

```
1️⃣  Сложная структура истории/контекста
2️⃣  PyQt UI для отображения ответов ИИ
3️⃣  Доработка системного промпта
4️⃣  Переработка main.py
5️⃣  Тесты для всех компонентов
6️⃣  Локальные модели (Ollama / свой сервер)
```

---

## 📅 Дорожная карта (8 недель)

### Неделя 1-2: Фундамент

| Задача | Исполнитель | Описание | Критерий готовности |
|--------|-------------|----------|---------------------|
| **Проектирование новой истории** | Lead + Аналитик | Схема JSON/SQL, связи, индексы | Документ `HISTORY_SCHEMA.md` |
| **Реализация структуры истории** | Backend #1 | Класс `HistoryManager`, миграция | Работает загрузка/сохранение |
| **Системный промпт v2** | Lead | Улучшенные инструкции для ИИ | Тесты на 10+ сценариях |
| **Каркас PyQt приложения** | Frontend | Главное окно, логирование | Запускается окно |

**Результат:** Новая система истории, обновлённый промпт, запущенное UI окно

---

### Неделя 3-4: UI + Основная логика

| Задача | Исполнитель | Описание | Критерий готовности |
|--------|-------------|----------|---------------------|
| **Визуализация ответов ИИ** | Frontend | Отображение текста, tool_calls, результатов | Видно все этапы ответа |
| **Отображение истории** | Frontend + Backend #1 | Список диалогов, поиск, фильтры | Поиск по истории работает |
| **Переработка main.py** | Lead + Backend #2 | Рефакторинг, разделение на модули | Все тесты проходят |
| **Меню настроек UI** | Frontend | Выбор модели, спикера, STT/TTS | Настройки применяются |

**Результат:** Полноценное UI с историей и настройками, рефакторный main.py

---

### Неделя 5-6: Тесты + Качество

| Задача | Исполнитель | Описание | Критерий готовности |
|--------|-------------|----------|---------------------|
| **Unit-тесты STT/TTS** | Backend #2 | pytest для всех бэкендов | Покрытие >80% |
| **Тесты LLM интеграции** | Lead + Backend #2 | Моки для OpenRouter, тесты tool_calls | 20+ тест сценариев |
| **Интеграционные тесты** | Вся команда | End-to-end сценарии | 5+ полных сценариев |
| **CI/CD пайплайн** | Backend #3 | GitHub Actions для тестов | Тесты при каждом PR |

**Результат:** Полное тестовое покрытие, автоматическая проверка

---

### Неделя 7-8: Локальные модели + Финализация

| Задача | Исполнитель | Описание | Критерий готовности |
|--------|-------------|----------|---------------------|
| **Ollama интеграция** | Backend #3 | `ollama_handler.py`, настройки | Работает с локальной моделью |
| **Свой LLM сервер** | Backend #3 + Lead | FastAPI + модель на выбор | Развёрнуто локально |
| **Fallback система** | Lead | OpenRouter → Ollama → Local | Автоматическое переключение |
| **Финальное UI** | Frontend | Статус модели, индикаторы | Все статусы видны |
| **Документация** | Аналитик | README, инструкции, API doc | Полная документация |

**Результат:** Работа с локальными моделями, готовый релиз

---

## 📐 Детальная проработка задач

---

## 1️⃣ Сложная структура истории (Backend #1 + Аналитик)

### Текущая проблема

```json
// Сейчас - плоский список
{
  "messages": [
    {"role": "user", "content": "Привет"},
    {"role": "assistant", "content": "Привет!"}
  ]
}
```

### Новая структура

```json
{
  "session_id": "uuid",
  "created_at": "2026-02-18T10:00:00",
  "messages": [
    {
      "id": "msg_uuid",
      "role": "user|assistant|tool|system",
      "content": "текст",
      "timestamp": "ISO8601",
      
      // Для assistant
      "tool_calls": [
        {
          "id": "call_uuid",
          "name": "get_weather",
          "arguments": {"location": "Москва"},
          "status": "pending|completed|error",
          "result": {...},
          "execution_time_ms": 150
        }
      ],
      
      // Для tool
      "tool_call_id": "call_uuid",
      "result": {...},
      
      // Метаданные
      "metadata": {
        "model": "gpt-4",
        "tokens_used": 45,
        "latency_ms": 230,
        "interrupted": false,
        "tts_speaker": "kseniya",
        "audio_duration_sec": 3.5
      }
    }
  ],
  
  // Контекст сессии
  "context": {
    "user_preferences": {...},
    "active_tools": [...],
    "summary": "краткое содержание сессии"
  }
}
```

### Задачи

- [ ] Спроектировать схему (Lead + Аналитик)
- [ ] Реализовать `HistoryManager` класс (Backend #1)
- [ ] Миграция старых данных (Backend #1)
- [ ] Индексы для быстрого поиска (Backend #1)
- [ ] Методы: `add_message()`, `get_session()`, `search()`, `get_context()` (Backend #1)

### Файлы для создания/изменения

```
app/
├── core/
│   └── history_manager.py      # НОВЫЙ: Класс управления историей
├── data/
│   └── chat_history.json       # Изменится структура
└── utils/
    └── history_schema.md       # НОВЫЙ: Документация схемы
```

---

## 2️⃣ PyQt UI (Frontend)

### Структура приложения

```
ui/
├── main_window.py          # Главное окно
├── widgets/
│   ├── chat_display.py     # Отображение диалога
│   ├── message_bubble.py   # Пузырь сообщения
│   ├── tool_call_view.py   # Визуализация tool_calls
│   ├── history_panel.py    # Панель истории
│   ├── settings_dialog.py  # Настройки
│   └── status_bar.py       # Статус модели/подключения
├── styles/
│   └── theme.qss           # Стили (тёмная/светлая тема)
└── resources/
    └── icons/              # Иконки
```

### Компоненты

| Компонент | Описание | Сложность |
|-----------|----------|-----------|
| `ChatDisplay` | Лента сообщений с авто-скроллом | 🟡 Средняя |
| `MessageBubble` | Стильные пузыри (user/assistant/tool) | 🟢 Низкая |
| `ToolCallView` | Отображение вызванных функций с результатами | 🟡 Средняя |
| `HistoryPanel` | Список сессий с поиском | 🟡 Средняя |
| `SettingsDialog` | Выбор модели, спикера, STT/TTS | 🟢 Низкая |
| `StatusBar` | Индикаторы: модель, токены, задержка | 🟢 Низкая |

### Задачи

- [ ] Главное окно с layout (Frontend)
- [ ] ChatDisplay + MessageBubble (Frontend)
- [ ] ToolCallView (Frontend + Lead)
- [ ] HistoryPanel (Frontend + Backend #1)
- [ ] SettingsDialog (Frontend)
- [ ] Темы оформления (Frontend)

### Файлы для создания

```
app/
└── ui/
    ├── __init__.py
    ├── main_window.py
    ├── app.py
    ├── widgets/
    │   ├── __init__.py
    │   ├── chat_display.py
    │   ├── message_bubble.py
    │   ├── tool_call_view.py
    │   ├── history_panel.py
    │   ├── settings_dialog.py
    │   └── status_bar.py
    ├── styles/
    │   └── theme.qss
    └── resources/
        └── icons/
```

---

## 3️⃣ Доработка системного промпта (Lead)

### Текущий промпт

~150 строк, общая структура

### Новый промпт должен включать

```python
SYSTEM_PROMPT_V2 = """
Вы - Arium, умный голосовой помощник.

# Личность
- Дружелюбный, полезный, профессиональный
- Краткий (2-4 предложения обычно)
- Говорите по-русски

# Доступные инструменты
{tools_json}  # Динамическая вставка

# Правила использования инструментов
1. Вызывайте ТОЛЬКО если необходимо
2. text_to_audio - ЕДИНСТВЕННЫЙ способ озвучки
3. Если инструмент не нужен - просто ответьте текстом

# Формат ответов
- Для фактов: кратко + источник
- Для инструкций: пошагово
- Для кода: с объяснением

# Контекст
{context_summary}  # Динамическая вставка

# Ограничения
- Не придумывайте факты
- Если не знаете - скажите честно
- Не вызывайте инструменты без необходимости
"""
```

### Задачи

- [ ] Анализ текущих ответов ИИ (Аналитик)
- [ ] Проектирование новой структуры (Lead)
- [ ] Реализация шаблонизатора (Backend #2)
- [ ] Тесты на 20+ сценариях (Lead + Backend #2)
- [ ] A/B тестирование версий (вся команда)

### Файлы для изменения

```
app/
└── tools/
    └── instructions.py       # Изменится SYSTEM_PROMPT
```

---

## 4️⃣ Переработка main.py (Lead + Backend #2)

### Текущая проблема

711 строк в одном файле, глобальное состояние

### Новая структура

```
app/
├── core/
│   ├── application.py    # Главный класс AiriApp
│   ├── state.py          # AppState (thread-safe)
│   └── config.py         # Конфигурация
│
├── threads/
│   ├── recorder.py       # thread_record_voice
│   ├── processor.py      # thread_process_ai
│   └── player.py         # thread_play_response
│
├── services/
│   ├── history.py        # HistoryManager
│   ├── llm_router.py     # Роутинг между LLM
│   └── tool_registry.py  # Реестр инструментов
│
├── ui/                   # PyQt компоненты
├── stt/                  # STT бэкенды
├── tts/                  # TTS бэкенды
└── llm/                  # LLM хендлеры

main.py  # 50 строк: AiriApp().run()
```

### Задачи

- [ ] Спроектировать архитектуру (Lead)
- [ ] Выделить потоки в отдельные классы (Backend #2)
- [ ] Создать `AiriApp` класс (Lead)
- [ ] Dependency Injection (Backend #2)
- [ ] Миграция текущего кода (вся команда)
- [ ] Тесты на рефактор (Backend #2)

### Файлы для создания/изменения

```
app/
├── core/
│   ├── __init__.py
│   ├── application.py      # НОВЫЙ: AiriApp класс
│   ├── state.py            # НОВЫЙ: AppState
│   └── config.py           # НОВЫЙ: Конфигурация
├── threads/
│   ├── __init__.py
│   ├── recorder.py         # НОВЫЙ: из main.py
│   ├── processor.py        # НОВЫЙ: из main.py
│   └── player.py           # НОВЫЙ: из main.py
├── services/
│   ├── __init__.py
│   ├── history.py          # НОВЫЙ: HistoryManager
│   ├── llm_router.py       # НОВЫЙ: Роутинг
│   └── tool_registry.py    # НОВЫЙ: Реестр
└── main.py                 # Изменится: ~50 строк
```

---

## 5️⃣ Тесты (Backend #2 + вся команда)

### Структура тестов

```
tests/
├── unit/
│   ├── test_stt.py
│   ├── test_tts.py
│   ├── test_tool_executor.py
│   ├── test_history_manager.py
│   └── test_llm_handler.py
│
├── integration/
│   ├── test_recording_thread.py
│   ├── test_ai_processing.py
│   └── test_playback_thread.py
│
├── e2e/
│   ├── test_greeting_scenario.py
│   ├── test_weather_query_scenario.py
│   └── test_tool_calling_scenario.py
│
└── conftest.py  # Фикстуры pytest
```

### Задачи

- [ ] Настроить pytest (Backend #2)
- [ ] Unit-тесты STT/TTS (Backend #2)
- [ ] Unit-тесты LLM/Tools (Lead + Backend #2)
- [ ] Integration тесты потоков (Backend #2)
- [ ] E2E сценарии (вся команда)
- [ ] CI/CD пайплайн (Backend #3)

### Цель

- Покрытие >80%
- Запуск тестов <5 минут

### Файлы для создания

```
tests/
├── __init__.py
├── conftest.py
├── unit/
│   ├── __init__.py
│   ├── test_stt.py
│   ├── test_tts.py
│   ├── test_tool_executor.py
│   ├── test_history_manager.py
│   └── test_llm_handler.py
├── integration/
│   ├── __init__.py
│   ├── test_recording_thread.py
│   ├── test_ai_processing.py
│   └── test_playback_thread.py
└── e2e/
    ├── __init__.py
    ├── test_greeting_scenario.py
    ├── test_weather_query_scenario.py
    └── test_tool_calling_scenario.py
```

---

## 6️⃣ Локальные модели (Backend #3 + Lead)

### Варианты

| Способ | Описание | Сложность |
|--------|----------|-----------|
| **Ollama** | Готовое решение, много моделей | 🟢 Низкая |
| **LM Studio** | GUI + сервер | 🟢 Низкая |
| **Свой сервер** | FastAPI + transformers | 🟡 Средняя |
| **vLLM** | Production-ready сервер | 🔴 Высокая |

### Рекомендуемый стек

```
# Для разработки
Ollama (qwen2.5:7b, llama3.2:3b)

# Для продакшена
Свой сервер на FastAPI + vLLM
```

### Интеграция

```python
# app/llm/ollama_handler.py
class OllamaHandler:
    def __init__(self, model: str, base_url: str = "http://localhost:11434"):
        self.client = AsyncClient(host=base_url)
        self.model = model
    
    async def send_message(self, messages: List[Dict]) -> Dict:
        response = await self.client.chat(
            model=self.model,
            messages=messages,
            stream=False
        )
        return {
            "content": response.message.content,
            "tool_calls": self._parse_tool_calls(response)
        }

# app/llm/router.py
class LLMRouter:
    def __init__(self):
        self.handlers = {
            "openrouter": OpenRouterHandler(...),
            "ollama": OllamaHandler(...),
            "custom": CustomHandler(...)
        }
    
    def get_handler(self) -> BaseHandler:
        # Приоритет: OpenRouter → Ollama → Custom
        if self._is_openrouter_available():
            return self.handlers["openrouter"]
        elif self._is_ollama_available():
            return self.handlers["ollama"]
        else:
            return self.handlers["custom"]
```

### Задачи

- [ ] Ollama handler (Backend #3)
- [ ] Custom server handler (Backend #3 + Lead)
- [ ] LLM Router с fallback (Lead)
- [ ] Настройки для локальных моделей (Frontend)
- [ ] Документация по развёртыванию (Backend #3)

### Файлы для создания

```
app/
└── llm/
    ├── ollama_handler.py     # НОВЫЙ: Ollama интеграция
    ├── custom_handler.py     # НОВЫЙ: Свой сервер
    └── router.py             # НОВЫЙ: Роутинг между LLM
```

---

## 📊 Гант (визуализация)

```
Неделя:  1  2  3  4  5  6  7  8
         │  │  │  │  │  │  │  │
История  ██████
UI          ████████████
Промпт  ████
Main         ████████
Тесты            ████████████
Локальные              ████████
```

---

## 🎯 Еженедельные вехи

| Неделя | Цель | Демонстрация |
|--------|------|--------------|
| **1** | Схема истории, промпт v2 черновик | Документация |
| **2** | HistoryManager готов, промпт v2 тесты | Загрузка/сохранение |
| **3** | UI каркас, визуализация ответов | Запускается окно |
| **4** | UI история + настройки, main рефактор 50% | Работающее UI |
| **5** | Unit-тесты 50%, main готов | pytest запускается |
| **6** | Все тесты готовы, CI/CD | Зелёные галочки в GH Actions |
| **7** | Ollama работает, fallback | Локальная модель отвечает |
| **8** | Финализация, документация, релиз | **v3.0 готов!** |

---

## 🔄 Ежедневный процесс

### Standup (15 минут, утром)

```
Каждый отвечает на 3 вопроса:
1. Что сделал вчера?
2. Что сделаю сегодня?
3. Какие есть блокираторы?
```

### Code Review

- **Lead** ревьюит все PR
- **Минимум 1 аппрув** перед мержем
- **Ветвление:** `feature/название` → `develop` → `main`

### Спринты

- **Длительность:** 2 недели
- **Планирование:** Начало спринта
- **Ретроспектива:** Конец спринта

---

## 📈 Метрики успеха

| Метрика | Сейчас | Цель (8 недель) |
|---------|--------|-----------------|
| Строк кода в main.py | 711 | <100 |
| Покрытие тестами | 0% | >80% |
| Время запуска | ~10 сек | <5 сек |
| Поддержка моделей | 1 (OpenRouter) | 3+ (OpenRouter, Ollama, Custom) |
| UI | Отсутствует | Полноценное PyQt |
| Структура истории | Плоский JSON | Реляционная + контекст |

---

## 🚀 Следующие шаги

1. **Создать GitHub проект** с доской (Kanban)
2. **Раздать задачи** по исполнителям
3. **Настроить репозиторий** (branches, PR template)
4. **Провести kickoff встречу** (30 минут)
5. **Начать Неделю 1** с проектирования истории

---

## 📁 Список файлов для создания

### Неделя 1-2

```
□ app/core/history_manager.py
□ app/utils/history_schema.md
□ app/ui/main_window.py
□ app/ui/app.py
□ app/tools/instructions.py (изменения)
```

### Неделя 3-4

```
□ app/ui/widgets/chat_display.py
□ app/ui/widgets/message_bubble.py
□ app/ui/widgets/tool_call_view.py
□ app/ui/widgets/history_panel.py
□ app/ui/widgets/settings_dialog.py
□ app/ui/widgets/status_bar.py
□ app/ui/styles/theme.qss
□ app/core/application.py
□ app/core/state.py
□ app/core/config.py
□ app/threads/recorder.py
□ app/threads/processor.py
□ app/threads/player.py
□ app/services/llm_router.py
□ app/services/tool_registry.py
□ app/main.py (рефактор)
```

### Неделя 5-6

```
□ tests/conftest.py
□ tests/unit/test_stt.py
□ tests/unit/test_tts.py
□ tests/unit/test_tool_executor.py
□ tests/unit/test_history_manager.py
□ tests/unit/test_llm_handler.py
□ tests/integration/test_recording_thread.py
□ tests/integration/test_ai_processing.py
□ tests/integration/test_playback_thread.py
□ tests/e2e/test_greeting_scenario.py
□ tests/e2e/test_weather_query_scenario.py
□ tests/e2e/test_tool_calling_scenario.py
□ .github/workflows/tests.yml
```

### Неделя 7-8

```
□ app/llm/ollama_handler.py
□ app/llm/custom_handler.py
□ app/llm/router.py
□ docs/deployment.md
□ docs/local_models.md
```

---

## ✅ Чек-лист готовности к старту

- [ ] GitHub проект создан
- [ ] Задачи распределены
- [ ] Branch protection настроен
- [ ] PR template создан
- [ ] Kickoff встреча проведена
- [ ] Неделя 1 началась

---

**Дата создания документа:** 18 февраля 2026  
**Автор:** AI Assistant  
**Статус:** Готов к выполнению
