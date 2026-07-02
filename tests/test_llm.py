import sys
import os
from pathlib import Path

# Добавляем корень проекта в путь для импортов
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.utils.settings import Settings
from app.LLM.universal_openai import UniversalOpenAIHandler

def test_llm():
    print("[START] Тест Universal OpenAI Handler\n")
    
    # Грузим настройки (чтобы достать API ключи и серверы)
    settings = Settings(str(Path(__file__).parent.parent / "app" / "utils" / "settings.json"))
    
    api_key = settings.get_api_key_for_model()
    model_id = settings.get_model_id_for_model()
    base_url = settings.get_base_url_for_model()
    
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
        {"role": "user", "content": "расскажи анекдот  короткий и смешной"}
    ]
    
    print("\n[SEND] Отправка запроса к языковой модели...")
    print("[LLM] Ответ ИИ: ", end="", flush=True)
    
    has_error = False
    try:
        # Проверяем стриминг
        stream = llm.send_message_stream(messages)
        for chunk in stream:
            if chunk.get("finish_reason") == "error" or chunk.get("type") == "status" and chunk.get("finish_reason") == "error":
                print(f"\n[ERROR] Ошибка в потоке: {chunk.get('error') or chunk.get('content')}")
                has_error = True
            elif "content" in chunk and chunk["content"]:
                print(chunk["content"], end="", flush=True)
        
        if not has_error:
            print("\n\n[OK] Тест успешно завершен!")
        else:
            print("\n\n[FAIL] Тест завершился с ошибками соединения.")
    except Exception as e:
        print(f"\n\n[ERROR] Ошибка при запросе к LLM: {str(e)}")

if __name__ == "__main__":
    test_llm()


