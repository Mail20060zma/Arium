import os
import time
import subprocess
import base64
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional, Callable
import pyautogui
import mss
from PIL import Image, ImageDraw, ImageFont

class ScreenManager:
    def __init__(self, tmp_dir: str = "tmp/screen"):
        self.tmp_dir = Path(tmp_dir)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.sct = mss.mss()
        self.current_state = "global"
        self.current_zoom_region: Optional[Dict[str, int]] = None
        self.cell_mapping: Dict[int, Dict[str, int]] = {}
        self.last_image_path: Optional[str] = None
        self.ui_callback: Optional[Callable] = None
        
        # Настройки сетки
        self.global_cells_x = 20
        self.global_cells_y = 12
        self.zoom_cells_x = 10
        self.zoom_cells_y = 10
        
        pyautogui.FAILSAFE = False

    def _run_ocr(self, image_path: str) -> List[Dict[str, Any]]:
        """Запуск встроенного Windows OCR через PowerShell reflection."""
        image_path = str(Path(image_path).resolve())
        
        ps_script = f"""
        Add-Type -AssemblyName System.Runtime.WindowsRuntime
        
        [void][Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
        [void][Windows.Storage.FileAccessMode, Windows.Storage, ContentType=WindowsRuntime]
        [void][Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
        [void][Windows.Media.Ocr.OcrEngine, Windows.Media.Ocr, ContentType=WindowsRuntime]
        [void][Windows.Storage.Streams.IRandomAccessStream, Windows.Storage, ContentType=WindowsRuntime]

        $asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | 
            Where-Object {{ 
                $_.Name -eq 'AsTask' -and 
                $_.GetParameters().Count -eq 1 -and 
                $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' 
            }})[0]

        function Await-WinRT ($asyncOp, [Type]$resultType) {{
            $concreteMethod = $asTaskGeneric.MakeGenericMethod($resultType)
            $netTask = $concreteMethod.Invoke($null, @($asyncOp))
            return $netTask.GetAwaiter().GetResult()
        }}

        try {{
            $opFile = [Windows.Storage.StorageFile]::GetFileFromPathAsync("{image_path}")
            $storageFile = Await-WinRT $opFile ([Windows.Storage.StorageFile])

            $opStream = $storageFile.OpenAsync([Windows.Storage.FileAccessMode]::Read)
            $stream = Await-WinRT $opStream ([Windows.Storage.Streams.IRandomAccessStream])

            $opDec = [Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)
            $decoder = Await-WinRT $opDec ([Windows.Graphics.Imaging.BitmapDecoder])
            
            $opBmp = $decoder.GetSoftwareBitmapAsync()
            $bitmap = Await-WinRT $opBmp ([Windows.Graphics.Imaging.SoftwareBitmap])

            $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
            if (-not $engine) {{
                throw "Could not create OCR engine."
            }}
            
            $opOcr = $engine.RecognizeAsync($bitmap)
            $ocrResult = Await-WinRT $opOcr ([Windows.Media.Ocr.OcrResult])
            
            $results = @()
            foreach ($line in $ocrResult.Lines) {{
                foreach ($word in $line.Words) {{
                    $rect = $word.BoundingRect
                    $results += "$($word.Text)|$($rect.X)|$($rect.Y)|$($rect.Width)|$($rect.Height)"
                }}
            }}
            $output_str = $results -join "`n"
            $output_bytes = [System.Text.Encoding]::UTF8.GetBytes($output_str)
            $base64 = [System.Convert]::ToBase64String($output_bytes)
            Write-Output $base64
        }} catch {{
            $err_msg = $_.Exception.ToString()
            $err_bytes = [System.Text.Encoding]::UTF8.GetBytes("ERROR: " + $err_msg)
            Write-Output ([System.Convert]::ToBase64String($err_bytes))
            exit 1
        }}
        """
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore"
            )
            stdout_clean = proc.stdout.strip()
            if not stdout_clean:
                return []
                
            decoded_bytes = base64.b64decode(stdout_clean)
            decoded_str = decoded_bytes.decode("utf-8", errors="ignore")
            
            if decoded_str.startswith("ERROR:"):
                return []
                
            words = []
            for line in decoded_str.strip().split("\n"):
                line = line.strip()
                if not line or "|" not in line:
                    continue
                parts = line.split("|")
                if len(parts) == 5:
                    text, x, y, w, h = parts
                    words.append({
                        "text": text,
                        "x": int(float(x)),
                        "y": int(float(y)),
                        "w": int(float(w)),
                        "h": int(float(h))
                    })
            return words
        except Exception:
            return []


    def open_app(self, app_name: str) -> Dict[str, Any]:
        """Запускает приложение по имени или пути."""
        import os
        import subprocess
        from pathlib import Path
        
        app_name_clean = app_name.strip()
        if not app_name_clean:
            return {"status": "error", "message": "Имя приложения не может быть пустым."}
            
        lower_name = app_name_clean.lower()
        
        # 1. Сначала проверим, не запущено ли уже это приложение (по заголовку окна)
        try:
            import uiautomation as auto
            root = auto.GetRootControl()
            matching_window = None
            
            # Определяем ключевые слова для поиска по заголовку
            keywords = [lower_name]
            if lower_name in ["yandexmusic", "яндекс музыка", "yandex music"]:
                keywords = ["яндекс", "yandex", "music", "музыка"]
            elif lower_name in ["telegram", "телеграм"]:
                keywords = ["telegram", "телеграм"]
            elif lower_name in ["chrome", "браузер", "google chrome"]:
                keywords = ["chrome", "google chrome", "браузер"]
            elif lower_name in ["notepad", "блокнот"]:
                keywords = ["notepad", "блокнот"]
            elif lower_name in ["calc", "калькулятор"]:
                keywords = ["calc", "калькулятор"]
            elif lower_name in ["explorer", "проводник"]:
                keywords = ["проводник", "explorer"]
                
            for child in root.GetChildren():
                name = child.Name
                if name:
                    name_lower = name.lower()
                    if any(kw in name_lower for kw in keywords):
                        matching_window = child
                        break
                        
            if matching_window:
                # Нашли открытое окно. Переводим на передний план.
                try:
                    matching_window.Restore()
                    matching_window.SetActive()
                    matching_window.SetFocus()
                except Exception:
                    try:
                        matching_window.SetActive()
                        matching_window.SetFocus()
                    except Exception:
                        pass
                return {
                    "status": "success",
                    "message": f"Приложение '{app_name_clean}' уже открыто (окно: '{matching_window.Name}'). Переведено на передний план."
                }
        except Exception:
            # Игнорируем ошибки проверки окон, продолжаем запуск
            pass

        # Сопоставление популярных названий с файлами/путями
        mappings = {
            "яндекс музыка": "yandexmusic",
            "yandex music": "yandexmusic",
            "браузер": "chrome",
            "browser": "chrome",
            "телеграм": "telegram",
            "telegram": "telegram",
            "калькулятор": "calc",
            "блокнот": "notepad",
            "проводник": "explorer",
            "paint": "mspaint",
        }
        
        target = mappings.get(lower_name, app_name_clean)
        
        # Разрешение стандартных путей для популярных приложений на Windows
        if target.lower() == "yandexmusic":
            possible_paths = [
                os.path.expandvars(r"%LocalAppData%\Programs\YandexMusic\YandexMusic.exe"),
                os.path.expandvars(r"%LocalAppData%\Yandex\YandexMusic\YandexMusic.exe"),
                os.path.expandvars(r"%ProgramFiles%\Yandex\YandexMusic\YandexMusic.exe"),
                os.path.expandvars(r"%ProgramFiles(x86)%\Yandex\YandexMusic\YandexMusic.exe"),
            ]
            for path in possible_paths:
                if os.path.exists(path):
                    target = path
                    break
        elif target.lower() == "telegram":
            possible_paths = [
                os.path.expandvars(r"%AppData%\Telegram Desktop\Telegram.exe"),
                os.path.expandvars(r"%LocalAppData%\Programs\Telegram Desktop\Telegram.exe"),
            ]
            for path in possible_paths:
                if os.path.exists(path):
                    target = path
                    break
                    
        try:
            # Пытаемся запустить напрямую через os.startfile
            os.startfile(target)
            return {"status": "success", "message": f"Приложение '{app_name_clean}' запущено."}
        except FileNotFoundError:
            # 2. Умный поиск ярлыка (.lnk) в меню Пуск и на Рабочем столе
            shortcut_dirs = [
                os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs"),
                os.path.expandvars(r"%AppData%\Microsoft\Windows\Start Menu\Programs"),
                os.path.expandvars(r"%UserProfile%\Desktop"),
                os.path.expandvars(r"%Public%\Desktop"),
            ]
            
            found_shortcut = None
            for d in shortcut_dirs:
                d_path = Path(d)
                if not d_path.exists():
                    continue
                try:
                    for file in d_path.rglob("*.lnk"):
                        stem_lower = file.stem.lower()
                        if lower_name in stem_lower or (lower_name == "yandexmusic" and ("yandex" in stem_lower or "яндекс" in stem_lower) and ("music" in stem_lower or "музык" in stem_lower)):
                            found_shortcut = str(file.resolve())
                            break
                except Exception:
                    pass
                if found_shortcut:
                    break
                    
            if found_shortcut:
                try:
                    os.startfile(found_shortcut)
                    return {"status": "success", "message": f"Приложение '{app_name_clean}' найдено через умный поиск и запущено."}
                except Exception as e:
                    return {"status": "error", "message": f"Ярлык '{app_name_clean}' найден, но не удалось запустить: {str(e)}"}
            
            # 3. Попытка запуска через shell
            try:
                subprocess.Popen(f'start "" "{target}"', shell=True)
                return {"status": "success", "message": f"Приложение '{app_name_clean}' запущено через shell."}
            except Exception as e:
                return {"status": "error", "message": f"Не удалось запустить '{app_name_clean}': {str(e)}"}
        except Exception as e:
            return {"status": "error", "message": f"Ошибка при запуске '{app_name_clean}': {str(e)}"}

    def list_windows(self) -> Dict[str, Any]:
        """Возвращает список всех открытых окон верхнего уровня."""
        try:
            import uiautomation as auto
            root = auto.GetRootControl()
            windows = []
            for child in root.GetChildren():
                name = child.Name
                class_name = child.ClassName
                if name and child.IsEnabled and not child.IsOffscreen:
                    windows.append({
                        "title": name,
                        "class": class_name
                    })
            return {"status": "success", "windows": windows, "count": len(windows)}
        except Exception as e:
            return {"status": "error", "message": f"Ошибка перечисления окон: {str(e)}"}

    def window_controls(self, window_title: str, search_query: Optional[str] = None, control_type: Optional[str] = None) -> Dict[str, Any]:
        """
        Находит окно по заголовку (с поддержкой синонимов), разворачивает его, активирует доступность
        и возвращает список всех интерактивных элементов управления, сгруппированных по типам.
        """
        try:
            import uiautomation as auto
            root = auto.GetRootControl()
            
            target_window = None
            title_lower = window_title.lower().strip()
            
            # Определяем ключевые слова для поиска по заголовку (умный поиск окна)
            keywords = [title_lower]
            if title_lower in ["yandexmusic", "яндекс музыка", "yandex music"]:
                keywords = ["яндекс", "yandex", "music", "музыка"]
            elif title_lower in ["telegram", "телеграм"]:
                keywords = ["telegram", "телеграм"]
            elif title_lower in ["chrome", "браузер", "google chrome"]:
                keywords = ["chrome", "google chrome", "браузер"]
            elif title_lower in ["notepad", "блокнот"]:
                keywords = ["notepad", "блокнот"]
            elif title_lower in ["calc", "калькулятор"]:
                keywords = ["calc", "калькулятор"]
            elif title_lower in ["explorer", "проводник"]:
                keywords = ["проводник", "explorer", "cabinetwclass"]
                
            for child in root.GetChildren():
                if child.Name:
                    name_lower = child.Name.lower()
                    if any(kw in name_lower for kw in keywords):
                        target_window = child
                        break
                    
            if not target_window:
                return {"status": "error", "message": f"Окно с заголовком '{window_title}' не найдено."}
                
            # Восстанавливаем окно если свернуто
            try:
                target_window.Restore()
                target_window.SetActive()
                target_window.SetFocus()
            except Exception:
                try:
                    target_window.SetActive()
                    target_window.SetFocus()
                except Exception:
                    pass
            
            # Ждем отрисовки
            time.sleep(0.8)
            
            # Пробуждение Chromium/Electron
            is_chromium = "chrome" in target_window.ClassName.lower() or "widget" in target_window.ClassName.lower()
            rect = target_window.BoundingRectangle
            w = rect.right - rect.left
            h = rect.bottom - rect.top
            
            if is_chromium and w > 0 and h > 0:
                # Делаем правый клик и Escape внутри webview
                click_x = rect.left + w // 2
                click_y = rect.top + min(200, h // 2)
                pyautogui.rightClick(click_x, click_y)
                time.sleep(0.3)
                pyautogui.press('escape')
                time.sleep(0.5)
                
            # Собираем элементы
            controls = []
            
            def walk(control, depth=0):
                if depth > 25:
                    return
                try:
                    if control.IsOffscreen:
                        return
                except Exception:
                    pass
                try:
                    name = control.Name
                    c_type = control.ControlTypeName
                    auto_id = control.AutomationId
                except Exception:
                    return
                    
                is_actionable = c_type in ["ButtonControl", "MenuItemControl", "HyperlinkControl", "TabItemControl", "EditControl", "DocumentControl", "CheckBoxControl", "RadioButtonControl", "ComboBoxControl", "ListItemControl", "GroupControl", "TextControl"]
                
                if is_actionable:
                    short_type = c_type.replace("Control", "")
                    # Фильтр по типу элемента
                    if control_type and control_type.lower() != short_type.lower():
                        is_actionable = False
                    # Фильтр по названию элемента
                    if name and search_query and search_query.lower() not in name.lower():
                        is_actionable = False
                
                if name and is_actionable:
                    controls.append({
                        "name": name,
                        "type": short_type,
                        "auto_id": auto_id
                    })
                        
                try:
                    for child in control.GetChildren():
                        walk(child, depth + 1)
                except Exception:
                    pass
                    
            walk(target_window)
            
            # Группируем элементы по категориям для удобства LLM
            from collections import defaultdict
            grouped = defaultdict(list)
            for c in controls:
                grouped[c["type"]].append(c["name"])
                
            summary_parts = []
            for t, names in sorted(grouped.items()):
                # Собираем уникальные отсортированные названия элементов данного типа
                names_str = ", ".join(f"'{n}'" for n in sorted(set(names)))
                summary_parts.append(f"{t}s: {names_str}")
                
            summary_str = "; ".join(summary_parts) if summary_parts else "интерактивные элементы не найдены"
            self.current_state = "global"
            self.current_zoom_region = None
            
            return {
                "status": "success",
                "message": f"Окно '{target_window.Name}' активно. Элементы загружены.",
                "detected_elements": summary_str
            }
        except Exception as e:
            return {"status": "error", "message": f"Ошибка анализа элементов окна: {str(e)}"}

    def window_control_action(self, window_title: str, search_query: str, action: str = "click", text: Optional[str] = None, control_type: Optional[str] = None) -> Dict[str, Any]:
        """
        Находит окно по заголовку, активирует его, выполняет семантический поиск элемента по search_query
        и производит над ним указанное действие (click, double_click, right_click, type, clear_and_type, scroll).
        """
        try:
            import uiautomation as auto
            import re
            root = auto.GetRootControl()
            
            target_window = None
            title_lower = window_title.lower().strip()
            
            # Поиск окна
            keywords = [title_lower]
            if title_lower in ["yandexmusic", "яндекс музыка", "yandex music"]:
                keywords = ["яндекс", "yandex", "music", "музыка"]
            elif title_lower in ["telegram", "телеграм"]:
                keywords = ["telegram", "телеграм"]
            elif title_lower in ["chrome", "браузер", "google chrome"]:
                keywords = ["chrome", "google chrome", "браузер"]
            elif title_lower in ["notepad", "блокнот"]:
                keywords = ["notepad", "блокнот"]
            elif title_lower in ["calc", "калькулятор"]:
                keywords = ["calc", "калькулятор"]
            elif title_lower in ["explorer", "проводник"]:
                keywords = ["проводник", "explorer", "cabinetwclass"]
                
            for child in root.GetChildren():
                if child.Name:
                    name_lower = child.Name.lower()
                    if any(kw in name_lower for kw in keywords):
                        target_window = child
                        break
                        
            if not target_window:
                return {"status": "error", "message": f"Окно с заголовком '{window_title}' не найдено."}
                
            # Восстанавливаем окно
            try:
                target_window.Restore()
                target_window.SetActive()
                target_window.SetFocus()
            except Exception:
                try:
                    target_window.SetActive()
                    target_window.SetFocus()
                except Exception:
                    pass
                    
            time.sleep(0.8)
            
            # Пробуждение Chromium/Electron
            is_chromium = "chrome" in target_window.ClassName.lower() or "widget" in target_window.ClassName.lower()
            rect = target_window.BoundingRectangle
            w = rect.right - rect.left
            h = rect.bottom - rect.top
            
            if is_chromium and w > 0 and h > 0:
                click_x = rect.left + w // 2
                click_y = rect.top + min(200, h // 2)
                pyautogui.rightClick(click_x, click_y)
                time.sleep(0.3)
                pyautogui.press('escape')
                time.sleep(0.5)
                
            # Собираем элементы
            candidates = []
            
            def walk(control, depth=0):
                if depth > 25:
                    return
                try:
                    if control.IsOffscreen:
                        return
                except Exception:
                    pass
                try:
                    name = control.Name
                    c_type = control.ControlTypeName
                    c_rect = control.BoundingRectangle
                    auto_id = control.AutomationId
                except Exception:
                    return
                    
                is_actionable = c_type in ["ButtonControl", "MenuItemControl", "HyperlinkControl", "TabItemControl", "EditControl", "DocumentControl", "CheckBoxControl", "RadioButtonControl", "ComboBoxControl", "ListItemControl", "GroupControl", "TextControl"]
                
                if is_actionable and name:
                    short_type = c_type.replace("Control", "")
                    candidates.append({
                        "control": control,
                        "name": name,
                        "type": short_type,
                        "rect": c_rect,
                        "auto_id": auto_id
                    })
                    
                try:
                    for child in control.GetChildren():
                        walk(child, depth + 1)
                except Exception:
                    pass
                    
            walk(target_window)
            
            if not candidates:
                return {"status": "error", "message": "В окне не найдено интерактивных элементов."}
                
            # Умный поиск совпадений по ключевым словам (синонимам)
            query_tokens = [tok.strip().lower() for tok in re.split(r'[, ]+', search_query) if tok.strip()]
            
            best_match = None
            best_score = -1
            
            for cand in candidates:
                cand_name_lower = cand["name"].lower()
                cand_auto_id_lower = (cand["auto_id"] or "").lower()
                cand_type_lower = cand["type"].lower()
                
                if control_type and control_type.lower() != cand_type_lower:
                    continue
                    
                score = 0
                matched_tokens = 0
                for tok in query_tokens:
                    if tok == cand_name_lower:
                        score += 10 # Точное совпадение
                        matched_tokens += 1
                    elif tok in cand_name_lower:
                        score += 5 # Частичное совпадение
                        matched_tokens += 1
                    elif tok in cand_auto_id_lower:
                        score += 2 # Совпадение ID
                        matched_tokens += 1
                        
                if matched_tokens > 0:
                    score += (1.0 / (len(cand_name_lower) + 1)) # Приоритет для коротких имен
                    if score > best_score:
                        best_score = score
                        best_match = cand
                        
            if not best_match:
                return {"status": "error", "message": f"Не удалось найти элемент управления по запросу '{search_query}'."}
                
            # Выполняем действие
            ctrl = best_match["control"]
            c_rect = best_match["rect"]
            cx = (c_rect.left + c_rect.right) // 2
            cy = (c_rect.top + c_rect.bottom) // 2
            
            act = action.lower().strip()
            
            if act == "click":
                pyautogui.click(cx, cy)
            elif act == "double_click":
                pyautogui.doubleClick(cx, cy)
            elif act == "right_click":
                pyautogui.rightClick(cx, cy)
            elif act == "type":
                if not text:
                    return {"status": "error", "message": "Параметр 'text' обязателен для действия 'type'."}
                pyautogui.click(cx, cy)
                time.sleep(0.15)
                pyautogui.typewrite(text, interval=0.01)
            elif act == "clear_and_type":
                if not text:
                    return {"status": "error", "message": "Параметр 'text' обязателен для действия 'clear_and_type'."}
                pyautogui.click(cx, cy)
                time.sleep(0.15)
                pyautogui.hotkey('ctrl', 'a')
                time.sleep(0.1)
                pyautogui.press('backspace')
                time.sleep(0.1)
                pyautogui.typewrite(text, interval=0.01)
            elif act == "scroll":
                try:
                    ctrl.SetFocus()
                except Exception:
                    pyautogui.click(cx, cy)
                time.sleep(0.1)
                pyautogui.scroll(120)
            else:
                return {"status": "error", "message": f"Неизвестное действие '{action}'."}
                
            return {
                "status": "success",
                "message": f"Выполнено действие '{action}' над элементом {best_match['type']} '{best_match['name']}' в окне '{target_window.Name}'."
            }
        except Exception as e:
            return {"status": "error", "message": f"Ошибка выполнения действия в окне: {str(e)}"}

    def _get_font(self, size: int) -> ImageFont.FreeTypeFont:
        try:
            return ImageFont.truetype("arialbd.ttf", size)
        except:
            return ImageFont.load_default()

    def _draw_grid_on_image(self, img: Image.Image, cells_x: int, cells_y: int, prefix: str = "", start_cell_id: int = 1) -> Tuple[Image.Image, Dict[int, Dict[str, int]]]:
        draw = ImageDraw.Draw(img, 'RGBA')
        width, height = img.size
        
        cell_w = width / cells_x
        cell_h = height / cells_y
        
        # Для зума (когда prefix == 'Z-') делаем шрифт еще меньше (0.12 вместо 0.18)
        font_scale = 0.12 if prefix == "Z-" else 0.18
        font_size = max(10, int(min(cell_w, cell_h) * font_scale))
        font = self._get_font(font_size)
        
        mapping = {}
        cell_id = start_cell_id
        
        font_size = max(10, int(min(cell_w, cell_h) * 0.18))
        font = self._get_font(font_size)
        
        mapping = {}
        
        # Рисуем линии сетки
        for x in range(1, cells_x):
            px = int(x * cell_w)
            draw.line([(px, 0), (px, height)], fill=(255, 255, 255, 80), width=1)
        for y in range(1, cells_y):
            py = int(y * cell_h)
            draw.line([(0, py), (width, py)], fill=(255, 255, 255, 80), width=1)
            
        for y in range(cells_y):
            for x in range(cells_x):
                # Координаты центра
                cx = int(x * cell_w + cell_w / 2)
                cy = int(y * cell_h + cell_h / 2)
                
                mapping[cell_id] = {"cx": cx, "cy": cy, "w": cell_w, "h": cell_h, "x": int(x * cell_w), "y": int(y * cell_h)}
                
                # Точка прицела в центре
                r = max(2, int(min(cell_w, cell_h) * 0.03))
                draw.ellipse([(cx - r, cy - r), (cx + r, cy + r)], fill=(255, 0, 0, 180))
                
                # Номер клетки (в левом верхнем углу)
                text = f"{prefix}{cell_id}"
                tx = int(x * cell_w + 4)
                ty = int(y * cell_h + 4)
                
                # Основной красный текст (без белой обводки)
                draw.text((tx, ty), text, font=font, fill=(200, 0, 0, 255))
                
                cell_id += 1
                
        return img, mapping

    def view(self) -> Dict[str, Any]:
        """Скриншот всех экранов без сетки и разметки."""
        image_paths = []
        for i, monitor in enumerate(self.sct.monitors[1:], start=1):
            if self.ui_callback:
                self.ui_callback("screenshot", monitor_index=i)
            sct_img = self.sct.grab(monitor)
            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
            
            save_path = self.tmp_dir / f"screen_global_m{i}_{int(time.time())}.jpg"
            img.save(save_path, quality=85)
            image_paths.append(str(save_path))
            
        self.current_state = "global"
        self.current_zoom_region = None
        self.last_image_path = image_paths[0] if image_paths else None
        
        return {
            "status": "success",
            "message": f"Скриншоты сделаны (мониторов: {len(image_paths)}).",
            "image_paths": image_paths
        }

    def type_text(self, text: str) -> Dict[str, Any]:
        pyautogui.typewrite(text, interval=0.01)
        return {"status": "success", "message": f"Текст введен."}

# Экземпляр
screen_manager = ScreenManager()

def screen_view() -> Dict[str, Any]:
    """Делает скриншот всех экранов в чистом виде (без координатной сетки)."""
    return screen_manager.view()

def screen_type(text: str) -> Dict[str, Any]:
    """Вводит переданный текст с клавиатуры."""
    return screen_manager.type_text(text)

def screen_list_windows() -> Dict[str, Any]:
    """Возвращает список всех открытых окон верхнего уровня."""
    return screen_manager.list_windows()

def screen_window_controls(window_title: str, search_query: Optional[str] = None, control_type: Optional[str] = None) -> Dict[str, Any]:
    """Находит окно по заголовку, разворачивает его и возвращает список всех интерактивных элементов управления, сгруппированных по типам."""
    return screen_manager.window_controls(window_title, search_query, control_type)

def screen_window_control_action(window_title: str, search_query: str, action: str = "click", text: Optional[str] = None, control_type: Optional[str] = None) -> Dict[str, Any]:
    """Находит окно по заголовку, разворачивает его, ищет элемент по ключевым словам и производит над ним указанное действие (click, type и т.д.)."""
    return screen_manager.window_control_action(window_title, search_query, action, text, control_type)

def screen_open_app(app_name: str) -> Dict[str, Any]:
    """Запускает приложение по имени или пути."""
    return screen_manager.open_app(app_name)

