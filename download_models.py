#!/usr/bin/env python3
"""Download specific TTS model(s) to app/model/ directory.

Usage:
    python download_models.py silero    # Download only Silero
    python download_models.py xtts      # Download only XTTS
    python download_models.py all       # Download both (default)
"""
import argparse
import sys
import json
from pathlib import Path

def main():
    if sys.stdout.encoding.lower() != 'utf-8':
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(
        description='Download TTS and STT models for Airi voice assistant',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''Examples:
  python download_models.py silero
    Download only Silero TTS model to app/model/silero/
  
  python download_models.py xtts
    Download only XTTS model to app/model/xtts/
    
  python download_models.py whisper
    Download Whisper STT model based on settings.json
    
  python download_models.py vosk
    Download Vosk STT model based on settings.json
  
  python download_models.py all
    Download all models (TTS and STT)
  
  python download_models.py
    Same as 'all'
        '''
    )
    
    parser.add_argument(
        'backend',
        nargs='?',
        default='all',
        help="Backend to download: 'silero', 'xtts', 'whisper', 'vosk', or 'all' (default: all)"
    )
    
    parser.add_argument(
        '--model',
        type=str,
        default=None,
        help="Specific model name to download for STT (e.g. 'large-v3-turbo' for whisper, 'vosk-model-small-ru-0.22' for vosk)"
    )
    
    args = parser.parse_args()
    
    print("🎙️  Airi TTS Model Downloader")
    print("=" * 60)
    
    # Determine which backends to download
    backends_to_download = []
    if args.backend.lower() in ('silero', 'all'):
        backends_to_download.append('silero')
    if args.backend.lower() in ('xtts', 'all'):
        backends_to_download.append('xtts')
    if args.backend.lower() in ('whisper', 'all'):
        backends_to_download.append('whisper')
    if args.backend.lower() in ('vosk', 'all'):
        backends_to_download.append('vosk')
    
    if not backends_to_download:
        print(f"❌ Unknown backend: '{args.backend}'")
        print("Available options: 'silero', 'xtts', 'whisper', 'vosk', or 'all'")
        return 1
        
    # Read settings for STT models
    settings_path = Path("app/utils/settings.json")
    stt_model = "small"
    if settings_path.exists():
        try:
            with open(settings_path, 'r', encoding='utf-8') as f:
                settings = json.load(f)
                stt_model = settings.get('stt_model', stt_model)
        except Exception as e:
            print(f"⚠️ Could not read settings.json, using default STT model: {stt_model}")
            
    if args.model:
        stt_model = args.model
        print(f"📌 Using specified STT model: {stt_model}")
    
    total = len(backends_to_download)
    failed = []
    
    for idx, backend_name in enumerate(backends_to_download, 1):
        print(f"\n[{idx}/{total}] 📥 Downloading {backend_name.upper()} model...")
        print("-" * 60)
        
        try:
            print(f"   Initializing {backend_name} engine...")
            
            if backend_name in ('silero', 'xtts'):
                try:
                    from app.tts_backends import get_tts_engine_by_name
                except ImportError as e:
                    print(f"❌ Import error for TTS: {e}")
                    print("   Make sure you are running inside the .venv or have TTS installed.")
                    failed.append(backend_name)
                    continue
                    
                engine = get_tts_engine_by_name(backend_name)
                print(f"   Loading/downloading TTS model. This may take a few minutes...")
                engine.load_model()
                model_path = Path(f"app/model/{backend_name}")
            else:
                from app.STT import create_stt
                
                # Check if it's the active backend, if not, maybe we should download a default one
                # but we'll just download the stt_model specified in settings.json 
                # (Note: whisper and vosk might use different names, but create_stt handles it if we pass it)
                print(f"   Loading/downloading STT model '{stt_model}'. This may take a few minutes...")
                
                try:
                    engine = create_stt(backend_name, stt_model)
                    model_path = Path(f"model/{backend_name}")
                except Exception as stt_err:
                    print(f"   ⚠️ Could not load STT with model '{stt_model}': {stt_err}")
                    # Try fallback
                    fallback_model = "small" if backend_name == "whisper" else "vosk-model-small-ru-0.22"
                    print(f"   Loading fallback STT model '{fallback_model}'...")
                    engine = create_stt(backend_name, fallback_model)
                    model_path = Path(f"model/{backend_name}")
            
            print(f"✅ {backend_name.upper()} model loaded successfully")
            
            if model_path.exists():
                try:
                    size_mb = sum(f.stat().st_size for f in model_path.rglob('*') if f.is_file()) / (1024 * 1024)
                    print(f"   📦 Location: {model_path.absolute()}")
                    print(f"   💾 Size: {size_mb:.2f} MB")
                except Exception:
                    pass
        
        except Exception as e:
            print(f"❌ Failed to download {backend_name.upper()}: {e}")
            import traceback
            traceback.print_exc()
            failed.append(backend_name)
    
    # Summary
    print("\n" + "=" * 60)
    print("📊 Download Summary")
    print("=" * 60)
    
    if not failed:
        print(f"✅ All {total} model(s) downloaded successfully!")
        print(f"🚀 Ready to use Airi voice assistant")
        return 0
    else:
        success = total - len(failed)
        print(f"⚠️ {len(failed)}/{total} model(s) failed: {', '.join(failed)}")
        print(f"✅ {success}/{total} model(s) completed successfully")
        print(f"\nTip: Check your internet connection and try again")
        return 1

if __name__ == '__main__':
    sys.exit(main())


# # Загрузить Whisper по умолчанию (или то, что указано в settings.json)
# python download_models.py whisper
# # Загрузить конкретную модель Whisper
# python download_models.py whisper --model large-v3-turbo
# # Загрузить конкретную модель Vosk
# python download_models.py vosk --model vosk-model-small-ru-0.22