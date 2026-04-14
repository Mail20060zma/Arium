import sys
import os

# Add the project directory to sys.path to allow importing from app modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.utils.settings import Settings
from app.tts_backends import get_tts_engine

def main():
    print("Starting Arium Model Download Utility...")

    # Instantiate Settings
    settings = Settings()
    
    print("\n[1/2] Fetching Silero TTS Engine...")
    try:
        silero_engine = get_tts_engine(tts_backend='silero')
        print("Loading/Downloading Silero model. This might take a moment...")
        silero_engine.load_model()
        print("✅ Silero model loaded successfully.")
    except Exception as e:
        print(f"❌ Error loading Silero model: {e}")

    print("\n[2/2] Fetching XTTS Engine...")
    try:
        xtts_engine = get_tts_engine(tts_backend='xtts')
        print("Loading/Downloading XTTS model. This might take a while on the first run...")
        xtts_engine.load_model()
        print("✅ XTTS model loaded successfully.")
    except Exception as e:
        print(f"❌ Error loading XTTS model: {e}")

    print("\nAll model download tasks have finished.")

if __name__ == "__main__":
    main()
