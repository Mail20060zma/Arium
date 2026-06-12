import sys
sys.stdout.reconfigure(encoding='utf-8')
import os
import json
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.utils.settings import Settings
from app.tools.instructions import build_tools_use
from app.LLM.universal_openai import UniversalOpenAIHandler

def format_messages_for_local_llm(messages):
    formatted = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        tool_calls = msg.get("tool_calls")
        
        if role == "tool":
            formatted.append({
                "role": "user",
                "content": f"[Результат выполнения {msg.get('name') or 'инструмента'}]: {content}"
            })
        else:
            formatted.append(msg)
    return formatted

def test_tool_loop():
    print("Testing tool loop history response...")
    
    settings = Settings("settings.json")
    api_key = settings.get_api_key_for_model()
    model_id = settings.get_model_id_for_model()
    base_url = settings.get_base_url_for_model()
    
    print(f"Model: {model_id}, Base URL: {base_url}")
    
    llm = UniversalOpenAIHandler(
        api_key=api_key,
        model=model_id,
        base_url=base_url,
        tool_handlers={}
    )
    
    # Replicate the exact messages
    messages = [
        {"role": "user", "content": "Привет дружище очень кратенько Расскажи что ты умеешь"},
        {
            "role": "assistant",
            "content": "Привет! Я Arium — твой интеллектуальный голосовой помощник. Я могу искать информацию в интернете, проверять погоду, управлять твоим экраном — кликать, масштабировать, набирать текст. Плюс, я умею запоминать и вспоминать детали из нашей беседы. Чем займемся?",
            "tool_calls": [
                {
                    "id": "local_tts_1781291619611",
                    "type": "function",
                    "function": {
                        "name": "text_to_audio",
                        "arguments": '{"text": "Привет Я Arium твой интеллектуальный голосовой помощник Я могу искать информацию в интернете проверять"}'
                    }
                }
            ]
        },
        {
            "role": "tool",
            "tool_call_id": "local_tts_1781291619611",
            "name": "text_to_audio",
            "content": "Привет Я Arium твой интеллектуальный голосовой помощник Я могу искать информацию в интернете проверять"
        },
        {"role": "user", "content": "Ок Хорошо Тогда можешь найти пожалуйста когда Стивен Хокинг изобрёл свой создал свою теорию по поводу чёрной дыры"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_53230",
                    "type": "function",
                    "function": {
                        "name": "search_web",
                        "arguments": '{"query": "когда Стивен Хокинг разработал теорию о черных дырах"}'
                    }
                }
            ]
        },
        {
            "role": "tool",
            "tool_call_id": "call_53230",
            "name": "search_web",
            "content": '{"query": "когда Стивен Хокинг разработал теорию о черных дырах", "results": [{"title": "Кто и когда...", "url": "https://...", "snippet": "В 1970 году Стивен Хокинг сформулировал..."}]}'
        }
    ]
    
    print("\nSending recursive-style messages to local LLM...")
    try:
        # Pass the real tool definitions
        tools_definitions = build_tools_use(settings.get("llm.enabled_tools", []))
        formatted_messages = format_messages_for_local_llm(messages)
        print("Formatted messages:", json.dumps(formatted_messages, indent=2, ensure_ascii=False))
        stream = llm.send_message_stream(formatted_messages, tools_definitions=tools_definitions)
        
        has_content = False
        for chunk in stream:
            print(f"Chunk: {chunk}")
            if chunk.get("type") == "content_delta" and chunk.get("content"):
                has_content = True
        
        print("\nStream finished.")
        if not has_content:
            print("WARNING: Model returned NO text content!")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_tool_loop()
