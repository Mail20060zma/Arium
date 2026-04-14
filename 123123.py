import logging
import tiktoken
import time
import json
from datetime import datetime
from openai import OpenAI
from concurrent.futures import ThreadPoolExecutor, as_completed

# Настройка логирования
log_filename = "123log.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_filename, encoding='utf-8'),
        logging.StreamHandler()  # Также вывод в консоль
    ]
)
logger = logging.getLogger(__name__)

# Логируем начало сессии
logger.info("=" * 60)
logger.info(f"Сессия чата начата: {datetime.now()}")
logger.info("=" * 60)


# ==================== ФУНКЦИИ ДЛЯ ИНСТРУМЕНТОВ ====================

def get_current_time():
    """Получить текущее время и дату"""
    now = datetime.now()
    return {
        "time": now.strftime("%H:%M:%S"),
        "date": now.strftime("%d.%m.%Y"),
        "full": now.strftime("%d.%m.%Y %H:%M:%S"),
        "day_of_week": ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"][now.weekday()]
    }


def calculate_math(expression):
    """Простой калькулятор для математических выражений"""
    try:
        # Безопасная оценка математического выражения
        result = eval(expression, {"__builtins__": {}}, {"__import__": __import__})
        return {"result": result, "expression": expression, "success": True}
    except Exception as e:
        return {"error": str(e), "expression": expression, "success": False}


def wait_time(seconds):
    """Функция ожидания/таймер - ждёт указанное количество секунд"""
    try:
        seconds = int(seconds)
        if seconds < 0:
            return {"error": "Время не может быть отрицательным", "success": False}
        
        logger.info(f"⏳ Ожидание {seconds} секунд...")
        time.sleep(seconds)
        
        return {
            "waited_seconds": seconds,
            "message": f"Ожидал {seconds} секунд",
            "success": True
        }
    except (ValueError, TypeError):
        return {"error": "Параметр должен быть числом (секунды)", "success": False}


# Словарь инструментов
TOOLS_FUNCTIONS = {
    "get_current_time": get_current_time,
    "calculate_math": calculate_math,
    "wait_time": wait_time
}

# Определения инструментов для API
TOOLS_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "Получить текущее время и дату. Вызывай эту функцию когда пользователь спрашивает о времени, дате или дне недели.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_math",
            "description": "Выполнить математическое вычисление. Используй для решения математических задач, вычисления выражений.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "Математическое выражение для вычисления (например: '2 + 2 * 3' или 'pow(2, 8)')"
                    }
                },
                "required": ["expression"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "wait_time",
            "description": "Функция ожидания/таймер. Ждёт указанное количество секунд. Используй когда нужно подождать перед выполнением следующего действия.",
            "parameters": {
                "type": "object",
                "properties": {
                    "seconds": {
                        "type": "integer",
                        "description": "Количество секунд для ожидания (должно быть положительным числом)"
                    }
                },
                "required": ["seconds"]
            }
        }
    }
]


# ==================== КЛАСС ЧАТБОТА (БЕЗ STREAMING) ====================

class ChatBot:
    """Класс для управления чатом с ИИ (БЕЗ STREAMING)"""
    
    def __init__(self, api_key="", base_url="https://g4f.space/api/groq/models", model="meta-llama/llama-4-scout-17b-16e-instruct"):
        """Инициализация чатбота"""
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.history = []
        
        # Инициализация счетчика токенов
        try:
            self.encoding = tiktoken.get_encoding("cl100k_base")
        except:
            self.encoding = None
            logger.warning("Не удалось инициализировать tiktoken. Подсчет токенов недоступен.")
        
        # Статистика токенов
        self.token_stats = {
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_tokens": 0,
            "messages_count": 0,
            "generation_speeds": []
        }
        
        logger.info(f"🤖 Чатбот инициализирован. Модель: {self.model} (БЕЗ STREAMING)")
    
    def count_tokens(self, text):
        """Подсчет токенов в тексте"""
        if self.encoding is None:
            return 0
        try:
            return len(self.encoding.encode(text))
        except:
            return 0
    
    def update_token_stats(self, user_msg, assistant_msg, generation_time):
        """Обновление статистики токенов"""
        if self.encoding is None:
            return
        
        input_tokens = self.count_tokens(user_msg)
        output_tokens = self.count_tokens(assistant_msg)
        
        generation_speed = output_tokens / generation_time if generation_time > 0 else 0
        
        self.token_stats["total_input_tokens"] += input_tokens
        self.token_stats["total_output_tokens"] += output_tokens
        self.token_stats["total_tokens"] += input_tokens + output_tokens
        self.token_stats["messages_count"] += 1
        self.token_stats["generation_speeds"].append(generation_speed)
        
        logger.info(f"📊 Токены запроса: {input_tokens} | Токены ответа: {output_tokens}")
        logger.info(f"⚡ Скорость генерации: {generation_speed:.2f} токен/сек | Время: {generation_time:.2f}сек")
        logger.info(f"📊 Всего использовано: {self.token_stats['total_tokens']} токенов")
    
    def execute_tool(self, tool_call, tool_name):
        """Выполнение одного инструмента"""
        tool_args_str = tool_call.function.arguments
        
        logger.info(f"🔧 Вызов инструмента: {tool_name}")
        logger.info(f"🔧 Параметры: {tool_args_str}")
        
        if tool_name in TOOLS_FUNCTIONS:
            try:
                if isinstance(tool_args_str, str):
                    args_dict = json.loads(tool_args_str) if tool_args_str.strip() else {}
                else:
                    args_dict = tool_args_str
                
                func = TOOLS_FUNCTIONS[tool_name]
                result = func(**args_dict) if args_dict else func()
                
                logger.info(f"✅ Результат {tool_name}: {result}")
                
                return {
                    "tool_call_id": tool_call.id,
                    "tool_name": tool_name,
                    "result": result,
                    "success": True
                }
            except Exception as e:
                logger.error(f"❌ Ошибка выполнения {tool_name}: {e}")
                return {
                    "tool_call_id": tool_call.id,
                    "tool_name": tool_name,
                    "result": {"error": str(e)},
                    "success": False
                }
        else:
            logger.warning(f"⚠️ Неизвестный инструмент: {tool_name}")
            return {
                "tool_call_id": tool_call.id,
                "tool_name": tool_name,
                "result": {"error": f"Неизвестный инструмент: {tool_name}"},
                "success": False
            }
    
    def process_tool_calls(self, response):
        """Обработка и выполнение вызовов инструментов ПАРАЛЛЕЛЬНО"""
        if not hasattr(response, 'tool_calls') or not response.tool_calls:
            return None
        
        logger.info(f"⚡ Обнаружено {len(response.tool_calls)} вызовов инструментов")
        logger.info("⚡ Запуск параллельной обработки функций...")
        
        tool_results = []
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(self.execute_tool, tool_call, tool_call.function.name): tool_call 
                for tool_call in response.tool_calls
            }
            
            for future in as_completed(futures):
                try:
                    result = future.result()
                    tool_results.append(result)
                    logger.info(f"✨ Завершена обработка функции: {result['tool_name']}")
                    print(f"✨ ✅ {result['tool_name']}: {result['result']}")
                except Exception as e:
                    logger.error(f"❌ Ошибка в ThreadPoolExecutor: {e}")
        
        logger.info(f"✨ Все {len(tool_results)} функций обработаны!")
        return tool_results
    
    def send_message(self, user_message):
        """Отправка сообщения и получение ответа (БЕЗ STREAMING)"""
        
        # Добавляем пользовательское сообщение в историю
        self.history.append({"role": "user", "content": user_message})
        logger.info(f"Пользователь: {user_message}")
        
        print(f"\nТы: {user_message}")
        print("Ассистент: ", end="", flush=True)
        
        try:
            # Запрос БЕЗ streaming
            logger.info(f"Отправка запроса к модели: {self.model}")
            
            start_time = time.time()
            
            response = self.client.chat.completions.create(
                model=self.model,
                messages=self.history,
                tools=TOOLS_DEFINITIONS,
                timeout=60
            )
            
            generation_time = time.time() - start_time
            
            # Получаем полный ответ
            assistant_message = response.choices[0].message
            full_response = assistant_message.content if assistant_message.content else ""
            
            print(full_response)
            logger.info(f"Ассистент: {full_response}")
            
            # Обновляем статистику токенов
            self.update_token_stats(user_message, full_response, generation_time)
            
            # Если есть tool_calls, обрабатываем их рекурсивно
            if hasattr(assistant_message, 'tool_calls') and assistant_message.tool_calls:
                logger.info(f"🔍 Обнаружено {len(assistant_message.tool_calls)} вызовов инструментов")
                print(f"\n🔧 ИИ запросил выполнение {len(assistant_message.tool_calls)} функций!\n")
                
                tool_results = self.process_tool_calls(assistant_message)
                
                if tool_results:
                    # Рекурсивная обработка с tool_calls
                    return self._process_tool_results(full_response, tool_results)
            
            # Если нет tool_calls, просто добавляем ответ и завершаем
            self.history.append({"role": "assistant", "content": full_response})
            return full_response
            
        except Exception as e:
            error_msg = f"Ошибка: {str(e)}"
            print(f"\n{error_msg}\n")
            logger.error(error_msg, exc_info=True)
            self.history.append({"role": "assistant", "content": f"[Ошибка: {str(e)}]"})
            return ""
    
    def _process_tool_results(self, assistant_response, tool_results):
        """Обработка результатов tool_calls и отправка повторного запроса (РЕКУРСИВНАЯ, БЕЗ STREAMING)"""
        
        print(f"\n{'='*50}")
        print(f"📋 РЕЗУЛЬТАТЫ ФУНКЦИЙ ({len(tool_results)} выполнено):")
        print(f"{'='*50}")
        
        # Добавляем ответ ассистента
        self.history.append({
            "role": "assistant",
            "content": assistant_response
        })
        
        # Добавляем результаты инструментов в историю
        for tool_result in tool_results:
            result_text = f"Результат инструмента {tool_result['tool_name']}: {tool_result['result']}"
            self.history.append({
                "role": "user",
                "content": result_text
            })
            
            status = "✅" if tool_result.get('success') else "❌"
            print(f"{status} {tool_result['tool_name']}: {tool_result['result']}")
        
        print(f"{'='*50}\n")
        
        # Отправляем повторный запрос
        logger.info(f"📤 Отправка повторного запроса с результатами {len(tool_results)} функций")
        
        try:
            start_time = time.time()
            
            response = self.client.chat.completions.create(
                model=self.model,
                messages=self.history,
                tools=TOOLS_DEFINITIONS,
                timeout=60
            )
            
            generation_time = time.time() - start_time
            
            # Получаем обновленный ответ
            assistant_message = response.choices[0].message
            final_response = assistant_message.content if assistant_message.content else ""
            
            print(final_response)
            
            logger.info(f"Ассистент: {final_response}")
            self.update_token_stats("", final_response, generation_time)
            
            # Проверяем на дополнительные tool_calls (рекурсия)
            logger.info("🔄 Проверка на дополнительные вызовы функций...")
            
            # Рекурсивная обработка новых tool_calls
            if hasattr(assistant_message, 'tool_calls') and assistant_message.tool_calls:
                logger.info(f"🔄 Обнаружено {len(assistant_message.tool_calls)} дополнительных вызовов функций (рекурсия)")
                print(f"\n🔧 ИИ запросил выполнение ещё {len(assistant_message.tool_calls)} функций!\n")
                
                # Добавляем финальный ответ в историю
                self.history.append({"role": "assistant", "content": final_response})
                
                # Обрабатываем новые tool calls рекурсивно
                new_tool_results = self.process_tool_calls(assistant_message)
                
                if new_tool_results:
                    # Рекурсивный вызов
                    return self._process_tool_results(final_response, new_tool_results)
            
            # Если нет новых tool_calls, добавляем финальный ответ и завершаем
            self.history.append({"role": "assistant", "content": final_response})
            return final_response
            
        except Exception as e:
            error_msg = f"Ошибка при повторном запросе: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return ""
    
    def get_history(self):
        """Получить историю диалога"""
        return self.history
    
    def get_token_stats(self):
        """Получить статистику токенов"""
        return self.token_stats
    
    def clear_history(self):
        """Очистить историю диалога"""
        self.history = []
        logger.info("📝 История диалога очищена")


def main():
    """Главная функция для интерактивного диалога"""
    print("Чат с ассистентом БЕЗ STREAMING (введите 'выход' для завершения)")
    print("=" * 50)
    logger.info("Чат начинается (БЕЗ STREAMING). Команды: 'выход', 'exit', 'quit'")
    
    # Инициализируем чатбота
    chatbot = ChatBot(api_key="", base_url="https://g4f.space/api/groq/models")
    
    while True:
        user_input = input("Ты: ").strip()
        
        if user_input.lower() in ['выход', 'exit', 'quit']:
            print("До встречи!")
            logger.info("Сессия завершена пользователем")
            break
        
        if not user_input:
            continue
        
        chatbot.send_message(user_input)
    
    # Выводим полную историю
    history = chatbot.get_history()
    token_stats = chatbot.get_token_stats()
    
    print("\n" + "=" * 50)
    print("История диалога:")
    print("=" * 50)
    logger.info("Вывод полной истории диалога")
    
    for msg in history:
        print(f"\n{msg['role'].upper()}:")
        print(msg['content'])
    
    # Выводим статистику использования токенов
    print("\n" + "=" * 50)
    print("📊 СТАТИСТИКА ИСПОЛЬЗОВАНИЯ ТОКЕНОВ:")
    print("=" * 50)
    print(f"Всего сообщений: {token_stats['messages_count']}")
    print(f"Входные токены (вопросы): {token_stats['total_input_tokens']}")
    print(f"Выходные токены (ответы): {token_stats['total_output_tokens']}")
    print(f"ВСЕГО ТОКЕНОВ: {token_stats['total_tokens']}")
    
    # Выводим информацию о скорости генерации
    if token_stats['generation_speeds']:
        avg_speed = sum(token_stats['generation_speeds']) / len(token_stats['generation_speeds'])
        min_speed = min(token_stats['generation_speeds'])
        max_speed = max(token_stats['generation_speeds'])
        
        print("\n" + "=" * 50)
        print("⚡ СТАТИСТИКА СКОРОСТИ ГЕНЕРАЦИИ:")
        print("=" * 50)
        print(f"Средняя скорость: {avg_speed:.2f} токен/сек")
        print(f"Минимальная скорость: {min_speed:.2f} токен/сек")
        print(f"Максимальная скорость: {max_speed:.2f} токен/сек")
        
        logger.info("=" * 60)
        logger.info("⚡ ИТОГОВАЯ СТАТИСТИКА СКОРОСТИ ГЕНЕРАЦИИ:")
        logger.info(f"Средняя скорость: {avg_speed:.2f} токен/сек")
        logger.info(f"Минимальная скорость: {min_speed:.2f} токен/сек")
        logger.info(f"Максимальная скорость: {max_speed:.2f} токен/сек")
    
    print("=" * 50)
    
    logger.info("=" * 60)
    logger.info("📊 ИТОГОВАЯ СТАТИСТИКА ТОКЕНОВ:")
    logger.info(f"Всего сообщений: {token_stats['messages_count']}")
    logger.info(f"Входные токены (вопросы): {token_stats['total_input_tokens']}")
    logger.info(f"Выходные токены (ответы): {token_stats['total_output_tokens']}")
    logger.info(f"ВСЕГО ТОКЕНОВ: {token_stats['total_tokens']}")
    logger.info("=" * 60)
    logger.info(f"Сессия завершена: {datetime.now()}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
