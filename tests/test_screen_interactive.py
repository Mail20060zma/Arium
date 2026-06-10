import sys
from pathlib import Path
import time

# Добавляем корень проекта в sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.tools.screen_control import screen_view, screen_zoom, screen_click

def main():
    print("🤖 Интерактивный тест управления экраном (Симуляция работы ИИ)")
    print("-" * 50)
    
    # ШАГ 1: Глобальный вид
    print("\n[ШАГ 1] Вызов screen_view()...")
    res_view = screen_view()
    if res_view.get("status") == "error":
        print(f"❌ Ошибка: {res_view.get('message')}")
        return
        
    print(f"✅ {res_view['message']}")
    for i, path in enumerate(res_view.get('image_paths', []), 1):
        print(f"   📸 Монитор {i}: {path}")
        
    print("\nОткройте сохраненные изображения и выберите клетку для зума.")
    
    # ШАГ 2: Зум
    zoom_input = input("👉 Введите НОМЕР клетки для зума (или 'q' для отмены): ").strip()
    if zoom_input.lower() == 'q':
        return
        
    try:
        zoom_cell_id = int(zoom_input)
    except ValueError:
        print("❌ Ошибка: Введите число.")
        return
        
    print(f"\n[ШАГ 2] Вызов screen_zoom([{zoom_cell_id}])...")
    res_zoom = screen_zoom([zoom_cell_id])
    if res_zoom.get("status") == "error":
        print(f"❌ Ошибка: {res_zoom.get('message')}")
        return
        
    print(f"✅ {res_zoom['message']}")
    print(f"   📸 Зум-изображение: {res_zoom.get('image_paths', [])[0]}")
    
    print("\nОткройте зум-изображение и выберите клетку (Z-...) для клика.")
    
    # ШАГ 3: Клик
    click_input = input("👉 Введите ТОЛЬКО ЧИСЛО клетки (например 15) для клика (или 'q' для отмены): ").strip()
    if click_input.lower() == 'q':
        return
        
    try:
        click_cell_id = int(click_input)
    except ValueError:
        print("❌ Ошибка: Введите только число.")
        return
        
    print(f"\n[ШАГ 3] Вызов screen_click({click_cell_id})...")
    print("⚠️ ВНИМАНИЕ: Сейчас мышка переместится и кликнет. Уберите руки от мыши!")
    time.sleep(2) # Даем пару секунд подготовиться
    
    res_click = screen_click(click_cell_id)
    if res_click.get("status") == "error":
        print(f"❌ Ошибка: {res_click.get('message')}")
        return
        
    print(f"✅ УСПЕХ: {res_click['message']}")
    print("-" * 50)
    print("🎉 Тест завершен!")

if __name__ == "__main__":
    main()
