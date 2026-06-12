import sys
sys.stdout.reconfigure(encoding='utf-8')
import time
import queue
import threading
import random
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.ui.overlay import launch_overlay, AnimationState

def mock_event_generator(event_queue):
    # Wait for UI to initialize
    time.sleep(2)
    
    try:
        # 1. Test state transitions
        print("\n-> Testing Listening state...")
        event_queue.put({"type": "state", "state": AnimationState.LISTENING, "text": "Слушаю ваш запрос..."})
        time.sleep(3)
        
        print("\n-> Testing Thinking state...")
        event_queue.put({"type": "state", "state": AnimationState.THINKING, "text": "Анализирую информацию..."})
        time.sleep(3)
        
        # 2. Test screenshot flash
        print("\n-> Testing Screenshot flash on monitor 1...")
        event_queue.put({"type": "screenshot", "monitor_index": 1})
        time.sleep(2)
        
        # 3. Test zoom preview
        print("\n-> Testing Zoom preview on monitor 1...")
        # Mock some cells in the middle of a 1920x1080 screen
        mock_cells = []
        for i in range(1, 5):
            mock_cells.append({
                "x": 400 + i * 100,
                "y": 300 + i * 50,
                "w": 90,
                "h": 45,
                "label": f"Cell {i}"
            })
        event_queue.put({
            "type": "zoom_preview",
            "monitor_index": 1,
            "region": {"x": 350, "y": 250, "w": 600, "h": 400},
            "cells_data": mock_cells
        })
        time.sleep(3)
        
        # 4. Test click ripples at random positions
        print("\n-> Testing Click ripples at coordinates...")
        for _ in range(5):
            x = random.randint(200, 800)
            y = random.randint(200, 600)
            print(f"   Clicking at ({x}, {y})")
            event_queue.put({"type": "click", "x": x, "y": y})
            time.sleep(1)
            
        # 5. Test Speaking state
        print("\n-> Testing Speaking state with speech sentences...")
        sentences = [
            "Привет! Я готов помочь вам с управлением экраном.",
            "Открываю браузер и перехожу на нужный веб-сайт.",
            "Пожалуйста, подождите, я выполняю ваше поручение."
        ]
        for sentence in sentences:
            event_queue.put({"type": "state", "state": AnimationState.SPEAKING, "text": sentence})
            time.sleep(3.5)
            
        # 6. Idle state
        print("\n-> Transitioning back to Idle...")
        event_queue.put({"type": "state", "state": AnimationState.IDLE, "text": "В режиме ожидания"})
        time.sleep(3)
        
    except KeyboardInterrupt:
        pass
    finally:
        print("\n-> Sending close event...")
        event_queue.put({"type": "close"})

def test_ui():
    print("🎨 Starting PyQt6 Overlay Test in Isolation...")
    print("This will test: status widget, click ripples, screenshot flash, and zoom preview.")
    
    event_queue = queue.Queue()
    
    # Start generator in background thread
    generator_thread = threading.Thread(
        target=mock_event_generator,
        args=(event_queue,),
        daemon=True,
        name="MockGeneratorThread"
    )
    generator_thread.start()
    
    # Start overlay on main thread
    launch_overlay(event_queue)
    print("🎉 Test finished!")

if __name__ == "__main__":
    test_ui()
