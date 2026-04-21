import sys
import os
from pathlib import Path

# Добавляем корень проекта в путь для импортов
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.utils.settings import Settings
from app.LLM.universal_openai import UniversalOpenAIHandler

def test_llm():
    print("🚀 Тест Universal OpenAI Handler\n")
    
    # Грузим настройки (чтобы достать API ключи и серверы)
    settings = Settings(str(Path(__file__).parent.parent / "app" / "utils" / "settings.json"))
    
    api_key = settings.get_api_key_for_model()
    model_id = settings.get_model_id_for_model()
    base_url = settings.get_base_url_for_model()
    
    print(f"🔧 Настройки модели:")
    print(f" - Модель: {model_id}")
    print(f" - Base URL: {base_url}")
    print(f" - API Key: {api_key}")
    
    # Инициализация обработчика
    llm = UniversalOpenAIHandler(
        api_key=api_key,
        model=model_id,
        base_url=base_url,
        tool_handlers={}  # Пока без тулзов
    )
    
    messages = [
        {"role": "system", "content": "Ты полезный ИИ-ассистент."},
        {"role": "user", "content": "расскажи анекдот про программистов."}
    ]
    
    print("\n📡 Отправка запроса к языковой модели...")
    print("🤖 Ответ ИИ: ", end="", flush=True)
    
    try:
        # Проверяем стриминг
        stream = llm.send_message_stream(messages)
        for chunk in stream:
            if "content" in chunk and chunk["content"]:
                print(chunk["content"], end="", flush=True)
        print("\n\n✅ Тест успешно завершен!")
    except Exception as e:
        print(f"\n\n❌ Ошибка при запросе к LLM: {str(e)}")

if __name__ == "__main__":
    test_llm()


