import sys
import os
from pathlib import Path

# Добавляем корень проекта в sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.tools.screen_control import screen_view, screen_zoom

def test_grid():
    print("📸 Делаем глобальный скриншот (screen_view)...")
    res1 = screen_view()
    print(res1["message"])
    print(f"✅ Изображения сохранены: {res1.get('image_paths', [])}")
    
    print("\n🔍 Делаем зум в центр экрана (например, клетки 105, 106, 125, 126)...")
    # Номера клеток зависят от разрешения, для 20x12 центральные клетки где-то в районе 100-140
    res2 = screen_zoom([259])
    if res2["status"] == "success":
        print(res2["message"])
        print(f"✅ Увеличенные изображения сохранены: {res2.get('image_paths', [])}")
    else:
        print(f"❌ Ошибка: {res2['message']}")

if __name__ == "__main__":
    test_grid()
