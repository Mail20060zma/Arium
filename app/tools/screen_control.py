import os
import time
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional
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
        
        # Настройки сетки
        self.global_cells_x = 20
        self.global_cells_y = 12
        self.zoom_cells_x = 10
        self.zoom_cells_y = 10
        
        pyautogui.FAILSAFE = False

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
        """Скриншот всех экранов с глобальной сеткой."""
        self.cell_mapping = {}
        image_paths = []
        cell_id_counter = 1
        
        # Перебираем все мониторы (индексы с 1)
        for i, monitor in enumerate(self.sct.monitors[1:], start=1):
            sct_img = self.sct.grab(monitor)
            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
            
            # Если мониторов несколько, можно добавить префикс M1-, M2- 
            # но для простоты мы используем сквозную нумерацию клеток.
            img_with_grid, mapping = self._draw_grid_on_image(img.copy(), self.global_cells_x, self.global_cells_y, start_cell_id=cell_id_counter)
            
            # Добавляем инфу о мониторе в маппинг
            for cid, data in mapping.items():
                data["monitor_index"] = i
                self.cell_mapping[cid] = data
                cell_id_counter = max(cell_id_counter, cid + 1)
            
            save_path = self.tmp_dir / f"screen_global_m{i}_{int(time.time())}.jpg"
            img_with_grid.save(save_path, quality=85)
            image_paths.append(str(save_path))
            
        self.current_state = "global"
        self.current_zoom_region = None
        self.last_image_path = image_paths[0] if image_paths else None
        
        return {
            "status": "success",
            "message": f"Скриншоты сделаны (мониторов: {len(image_paths)}). Доступны клетки от 1 до {len(self.cell_mapping)}.",
            "image_paths": image_paths
        }

    def zoom(self, cells: List[int]) -> Dict[str, Any]:
        """Зум в указанные клетки."""
        if self.current_state != "global":
            return {"status": "error", "message": "Зум доступен только из глобального вида. Сначала вызовите screen_view."}
            
        if not cells:
            return {"status": "error", "message": "Не указаны клетки для зума."}
            
        valid_cells = [c for c in cells if c in self.cell_mapping]
        if not valid_cells:
            return {"status": "error", "message": "Указаны неверные номера клеток."}
            
        # Находим к какому монитору относятся клетки
        # Предполагаем, что зум происходит в рамках одного монитора
        target_monitor_idx = self.cell_mapping[valid_cells[0]].get("monitor_index", 1)
        monitor = self.sct.monitors[target_monitor_idx]
        
        # Bounding box для выбранных клеток (относительно локальных координат скриншота монитора)
        min_x = min([self.cell_mapping[c]["x"] for c in valid_cells])
        min_y = min([self.cell_mapping[c]["y"] for c in valid_cells])
        max_x = max([self.cell_mapping[c]["x"] + self.cell_mapping[c]["w"] for c in valid_cells])
        max_y = max([self.cell_mapping[c]["y"] + self.cell_mapping[c]["h"] for c in valid_cells])
        
        width = max_x - min_x
        height = max_y - min_y
        
        # Margin 50% для захвата соседних элементов
        margin_x = int(width * 0.50)
        margin_y = int(height * 0.50)
        
        z_x = max(0, int(min_x - margin_x))
        z_y = max(0, int(min_y - margin_y))
        z_w = min(monitor["width"] - z_x, int(width + 2 * margin_x))
        z_h = min(monitor["height"] - z_y, int(height + 2 * margin_y))
        
        # Глобальные координаты для mss
        bbox = {"top": int(z_y + monitor["top"]), "left": int(z_x + monitor["left"]), "width": int(z_w), "height": int(z_h)}
        sct_img = self.sct.grab(bbox)
        img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
        
        # Масштабируем x6 для лучшей читаемости и детализации
        img_upscaled = img.resize((img.width * 6, img.height * 6), Image.LANCZOS)
        
        img_with_grid, mapping = self._draw_grid_on_image(img_upscaled, self.zoom_cells_x, self.zoom_cells_y, prefix="Z-")
        
        # Возвращаем координаты обратно к оригинальному масштабу, чтобы клик был точным
        for k in mapping:
            mapping[k]["cx"] = int(mapping[k]["cx"] / 6)
            mapping[k]["cy"] = int(mapping[k]["cy"] / 6)
            mapping[k]["w"] = int(mapping[k]["w"] / 6)
            mapping[k]["h"] = int(mapping[k]["h"] / 6)
            mapping[k]["x"] = int(mapping[k]["x"] / 6)
            mapping[k]["y"] = int(mapping[k]["y"] / 6)
        
        self.current_state = "zoom"
        self.current_zoom_region = {"x": z_x, "y": z_y, "w": z_w, "h": z_h, "monitor_index": target_monitor_idx}
        self.cell_mapping = mapping
        
        save_path = self.tmp_dir / f"screen_zoom_{int(time.time())}.jpg"
        img_with_grid.save(save_path, quality=85)
        self.last_image_path = str(save_path)
        
        return {
            "status": "success",
            "message": f"Зум выполнен. Доступны клетки от 1 до {len(mapping)} с префиксом 'Z-'.",
            "image_paths": [str(save_path)]
        }

    def click(self, cell_id: int) -> Dict[str, Any]:
        """Клик по клетке."""
        if cell_id not in self.cell_mapping:
            return {"status": "error", "message": f"Клетка {cell_id} не найдена."}
            
        cell = self.cell_mapping[cell_id]
        cx, cy = cell["cx"], cell["cy"]
        
        target_monitor_idx = cell.get("monitor_index", 1)
        if self.current_state == "zoom" and self.current_zoom_region:
            target_monitor_idx = self.current_zoom_region.get("monitor_index", 1)
            
        monitor = self.sct.monitors[target_monitor_idx]
        
        if self.current_state == "zoom" and self.current_zoom_region:
            global_x = monitor["left"] + self.current_zoom_region["x"] + cx
            global_y = monitor["top"] + self.current_zoom_region["y"] + cy
        else:
            global_x = monitor["left"] + cx
            global_y = monitor["top"] + cy
            
        pyautogui.click(global_x, global_y)
        
        self.current_state = "global"
        self.current_zoom_region = None
        
        return {"status": "success", "message": f"Клик по ({global_x}, {global_y}). Зум сброшен."}

    def type_text(self, text: str) -> Dict[str, Any]:
        pyautogui.typewrite(text, interval=0.01)
        return {"status": "success", "message": f"Текст введен."}

# Экземпляр
screen_manager = ScreenManager()

def screen_view() -> Dict[str, Any]:
    """Делает скриншот всего экрана и накладывает координатную сетку. Возвращает путь к картинке."""
    return screen_manager.view()

def screen_zoom(cells: List[int]) -> Dict[str, Any]:
    """Делает зум (увеличение) в указанные клетки. Принимает список номеров клеток, например [42, 43]. Возвращает картинку."""
    return screen_manager.zoom(cells)

def screen_click(cell_id: int) -> Dict[str, Any]:
    """Кликает по центру указанной клетки. Сбрасывает зум после выполнения."""
    return screen_manager.click(cell_id)

def screen_type(text: str) -> Dict[str, Any]:
    """Вводит переданный текст с клавиатуры."""
    return screen_manager.type_text(text)
