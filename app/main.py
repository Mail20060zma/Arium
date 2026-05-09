import sys
import logging
from pathlib import Path

# Добавляем корень проекта (Arium) в sys.path, чтобы работал импорт from app.engine.core...
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.engine.core import AriumEngine

def main():
    Path(__file__).parent.joinpath("logs").mkdir(exist_ok=True)
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(str(Path(__file__).parent / "logs" / "app.log"), encoding="utf-8"),
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    settings_path = str(Path(__file__).parent / "utils" / "settings.json")
    engine = AriumEngine(settings_path)
    engine.start()

if __name__ == "__main__":
    main()

