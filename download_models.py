#!/usr/bin/env python3
"""Download specific TTS model(s) to app/model/ directory.

Usage:
    python download_models.py silero    # Download only Silero
    python download_models.py xtts      # Download only XTTS
    python download_models.py all       # Download both (default)
"""
import argparse
import sys
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(
        description='Download TTS models for Airi voice assistant',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''Examples:
  python download_models.py silero
    Download only Silero TTS model to app/model/silero/
  
  python download_models.py xtts
    Download only XTTS model to app/model/xtts/
  
  python download_models.py all
    Download both models (default if no argument)
  
  python download_models.py
    Same as 'all' - downloads both models
        '''
    )
    
    parser.add_argument(
        'backend',
        nargs='?',
        default='all',
        help="Backend to download: 'silero', 'xtts', or 'all' (default: all)"
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
    
    if not backends_to_download:
        print(f"❌ Unknown backend: '{args.backend}'")
        print("Available options: 'silero', 'xtts', or 'all'")
        return 1
    
    # Import TTS engine factory
    try:
        from app.tts_backends import get_tts_engine_by_name
    except ImportError as e:
        print(f"❌ Import error: {e}")
        print("   Make sure you're running this from the project root directory")
        return 1
    
    total = len(backends_to_download)
    failed = []
    
    for idx, backend_name in enumerate(backends_to_download, 1):
        print(f"\n[{idx}/{total}] 📥 Downloading {backend_name.upper()} model...")
        print("-" * 60)
        
        try:
            print(f"   Initializing {backend_name} engine...")
            engine = get_tts_engine_by_name(backend_name)
            
            print(f"   Loading/downloading model. This may take a few minutes...")
            engine.load_model()
            
            print(f"✅ {backend_name.upper()} model loaded successfully")
            
            # Show storage location
            if backend_name == 'silero':
                model_path = Path("app/model/silero")
            else:  # xtts
                model_path = Path("app/model/xtts")
            
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
