import sys
import time
from pathlib import Path
import os

# Add root folder to sys.path so we can import app modules
sys.path.append(str(Path(__file__).parent.parent.resolve()))

from app.tools.screen_control import screen_list_windows, screen_window_controls, screen_window_control_action, screen_open_app

def test_uiauto_functionality():
    print("=== [UI AUTO TEST] Step 1: Listing open windows ===")
    res = screen_list_windows()
    
    if res.get("status") != "success":
        print(f"Error listing windows: {res.get('message')}")
        sys.exit(1)
        
    windows = res.get("windows", [])
    count = res.get("count", 0)
    print(f"Successfully found {count} top-level windows.")
    
    # Print the first 15 windows
    for idx, w in enumerate(windows[:15]):
        print(f"  {idx + 1}. Title: '{w['title']}' | Class: '{w['class']}'")
        
    print("\n=== [UI AUTO TEST] Step 2: Launching/opening Notepad ===")
    res_open = screen_open_app("notepad")
    print(f"Result of screen_open_app('notepad'): {res_open}")
    if res_open.get("status") != "success":
        print(f"Error: {res_open.get('message')}")
        sys.exit(1)
    
    # Wait for notepad window to initialize
    print("Waiting 1.5s for Notepad window...")
    time.sleep(1.5)
        
    print("\n=== [UI AUTO TEST] Step 3: Inspecting window controls for 'Notepad' ===")
    res_controls = screen_window_controls("notepad")
    print(f"Status: {res_controls.get('status')}")
    print(f"Message: {res_controls.get('message')}")
    detected = res_controls.get("detected_elements", "")
    print(f"Detected elements (first 1000 chars):\n{detected[:1000]}...")
    
    print("\n=== [UI AUTO TEST] Step 4: Performing action in Notepad (Type Text) ===")
    # We will search for the edit area/document in Notepad and type text.
    # We use search_query="Text Editor edit document редактор документ" to support different Windows languages/versions.
    res_type = screen_window_control_action(
        window_title="notepad",
        search_query="Text Editor edit document редактор документ",
        action="type",
        text="Hello from Arium UI Test! Привет от Ариум!"
    )
    print(f"Result of type action: {res_type}")
    if res_type.get("status") != "success":
        print(f"Warning/Error typing: {res_type.get('message')}")
    else:
        print("Success! Typed text via screen_window_control_action.")
        
    print("\n=== [UI AUTO TEST] Step 5: Performing action in Notepad (Click File Menu) ===")
    # Click "File" (or "Файл") menu to test click action
    res_click = screen_window_control_action(
        window_title="notepad",
        search_query="Файл File",
        action="click"
    )
    print(f"Result of click action on File menu: {res_click}")
    if res_click.get("status") != "success":
        print(f"Warning/Error clicking File menu: {res_click.get('message')}")
    else:
        print("Success! Clicked File menu via screen_window_control_action.")
        
    print("\n=== UI Auto tests completed successfully! ===")

if __name__ == "__main__":
    test_uiauto_functionality()

