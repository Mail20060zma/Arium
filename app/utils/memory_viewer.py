import sys
import os
from pathlib import Path

# Добавляем корень проекта (Arium) в sys.path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.data.vector_memory import vector_memory

def print_separator():
    print("-" * 60)

def show_memories(limit=50):
    print("\n📚 Текущие записи в векторной памяти:")
    print_separator()
    memories = vector_memory.list_memories(limit=limit)
    if not memories:
        print("Память пуста.")
    else:
        for m in memories:
            print(f"ID:       {m['id']}")
            print(f"Category: {m['category']}")
            print(f"Content:  {m['content']}")
            print_separator()

def search_memory():
    print("\n🔍 Поиск в памяти")
    query = input("Введите запрос: ").strip()
    if not query:
        return
    results = vector_memory.search_memory(query, n_results=5)
    print_separator()
    if not results:
        print("Ничего не найдено.")
    else:
        for r in results:
            dist = r.get('distance', 0)
            print(f"[{dist:.4f}] ID: {r['id']} | {r['category']} | {r['content']}")
    print_separator()

def add_memory():
    print("\n➕ Добавление памяти")
    content = input("Введите факт (что нужно запомнить): ").strip()
    if not content:
        return
    category = input("Введите категорию (по умолчанию 'general'): ").strip()
    if not category:
        category = "general"
        
    res = vector_memory.save_memory(content, category)
    print(f"Результат: {res}")

def update_memory():
    print("\n✏️ Редактирование памяти")
    mem_id = input("Введите ID записи (начинается с mem_): ").strip()
    if not mem_id:
        return
    content = input("Введите новый текст: ").strip()
    if not content:
        return
    category = input("Введите новую категорию (по умолчанию 'general'): ").strip()
    if not category:
        category = "general"
        
    res = vector_memory.update_memory(mem_id, content, category)
    print(f"Результат: {res}")

def delete_memory():
    print("\n🗑️ Удаление памяти")
    mem_id = input("Введите ID записи (начинается с mem_): ").strip()
    if not mem_id:
        return
        
    res = vector_memory.delete_memory(mem_id)
    print(f"Результат: {res}")

def main():
    if not vector_memory.is_active:
        print("❌ Ошибка: Векторная память отключена или не смогла инициализироваться.")
        return

    while True:
        print("\n🧠 Arium Memory Manager")
        print("=" * 60)
        print("1. Показать все записи (последние 50)")
        print("2. Поиск по памяти (семантический)")
        print("3. Добавить новую запись")
        print("4. Редактировать запись")
        print("5. Удалить запись")
        print("0. Выход")
        print("=" * 60)
        
        choice = input("Выберите действие: ").strip()
        
        if choice == '1':
            show_memories()
        elif choice == '2':
            search_memory()
        elif choice == '3':
            add_memory()
        elif choice == '4':
            update_memory()
        elif choice == '5':
            delete_memory()
        elif choice == '0':
            print("Выход...")
            break
        else:
            print("Неверный выбор. Попробуйте еще раз.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nВыход...")
