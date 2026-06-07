import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))

from app.data.vector_memory import vector_memory

def run_test():
    print("=== Тест VectorMemoryManager ===")
    
    # 1. Добавление в память
    res = vector_memory.save_memory("Пользователя зовут Миша", "user_profile")
    print(res)
    
    res = vector_memory.save_memory("Пользователь не любит лук", "preferences")
    print(res)
    
    res = vector_memory.save_memory("В Москве сейчас идет дождь", "general")
    print(res)
    
    print("\n--- Список всех фактов ---")
    memories = vector_memory.list_memories()
    for m in memories:
        print(f"[{m['id']}] {m['category']}: {m['content']}")
        
    print("\n--- Поиск (семантический) ---")
    results = vector_memory.search_memory("Как зовут юзера?")
    for r in results:
        print(f"[{r['distance']:.4f}] {r['content']}")
        
    print("\n--- Очистка тестовых данных ---")
    for m in memories:
        print(vector_memory.delete_memory(m['id']))
        
if __name__ == "__main__":
    run_test()
