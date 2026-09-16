import os
import sys  # 新增：用于检测打包环境
import time
import threading
import platform
import shutil
import re
import base64
import io
import math
import json  # 新增：用于配置保存
from tkinter import filedialog, colorchooser, messagebox, Menu
from functools import lru_cache
from datetime import datetime

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageOps, ImageGrab, ImageTk
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# === 拖拽库支持 ===
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:
    class TkinterDnD:
        class DnDWrapper: pass
        @staticmethod
        def _require(self): pass
    DND_FILES = "DND_Files"

# --- 全局设置 ---
APP_NAME = "妲己工具箱 v1.1.1beta版"

# =========================================================================
#  通用工具与配置管理
# =========================================================================
def resource_path(relative_path):
    """ 获取资源绝对路径，用于 PyInstaller 打包后寻找文件 """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

def get_config_path():
    """ 获取配置文件路径，保存在程序同级目录下 """
    if getattr(sys, 'frozen', False):
        base_path = os.path.dirname(sys.executable)
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, "daji_config.json")

class ConfigManager:
    """ 配置管理器：负责加载和保存用户设置 """
    def __init__(self):
        self.path = get_config_path()
        self.data = self.load()

    def load(self):
        default_cfg = {
            "theme": "Light",
            "resizer": {"w": 1080, "h": 1080, "mode": "direct", "prefix": "妲己批量", "crop_ratio": 1.0},
            "watermark": {
                "mode": "text", "watermark_text": "禁止盗用", "watermark_path": "",
                "text_color": "#000000", "scale": 0.15, "opacity": 0.3, "angle": 30,
                "tile_mode": True, "tile_spacing_x": 1.0, "tile_spacing_y": 1.0, "position": "右下角"
            },
            "renamer": {"prefix": "Image_", "start": 1, "digits": 2},
            "converter": {"target_format": "JPG", "quality": 90},
            "compressor": {"quality": 75}
        }
        if os.path.exists(self.path):
            try:
                with open(self.path, 'r', encoding='utf-8') as f:
                    saved = json.load(f)
                    # 简单合并，确保新版本的键存在
                    for k, v in saved.items():
                        if k in default_cfg and isinstance(v, dict):
                            default_cfg[k].update(v)
                        else:
                            default_cfg[k] = v
            except: pass
        return default_cfg

    def save(self):
        try:
            with open(self.path, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"Save Config Error: {e}")

# 全局配置实例
config_mgr = ConfigManager()
ctk.set_appearance_mode(config_mgr.data["theme"])
ctk.set_default_color_theme("blue")

def parse_dropped_files(data):
    if not data: return []
    if '{' in data:
        paths = re.findall(r'\{(.+?)\}|(\S+)', data)
        return [p[0] if p[0] else p[1] for p in paths]
    return data.split()

def get_font_path():
    system = platform.system()
    if system == "Windows":
        candidates = ["simhei.ttf", "msyh.ttc", "simsun.ttc"]
        font_dir = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts")
        for f in candidates:
            p = os.path.join(font_dir, f)
            if os.path.exists(p): return p
    return "simhei.ttf"

@lru_cache(maxsize=32)
def load_default_font(size):
    path = get_font_path()
    try: return ImageFont.truetype(path, size)
    except: return ImageFont.load_default()

def apply_thin_scrollbar(scrollable_frame):
    try:
        sb = scrollable_frame._scrollbar
        sb.configure(width=8)
        sb.configure(button_color=("gray85", "gray25")) 
        sb.configure(button_hover_color=("gray60", "gray40"))
    except: pass

# =========================================================================
#  核心逻辑
# =========================================================================
class WatermarkProcessor:
    def get_scale_factor(self, w, h):
        short, long_ = min(w, h), max(w, h)
        if short == 0: return 50, 1.0
        ratio = long_ / short
        return int(short), (1.0 / (ratio ** 0.4)) if ratio > 2.0 else 1.0

    def create_layer(self, cfg, size):
        img_w, img_h = size
        base_dim, damping = self.get_scale_factor(img_w, img_h)
        base_dim = max(base_dim, 50)
        
        if cfg["mode"] == "image":
            if cfg["watermark_path"] and os.path.exists(cfg["watermark_path"]):
                try: wm = Image.open(cfg["watermark_path"]).convert("RGBA")
                except: wm = self._dummy("无法加载")
            else: wm = self._dummy("无图片")
        else:
            wm = self._text_wm(cfg["watermark_text"], cfg["text_color"])
            
        scale = float(cfg["scale"]) * damping
        if cfg["mode"] == "image": target_w = max(20, int(base_dim * scale))
        else: target_w = max(20, int(base_dim * scale * 4))

        max_allowed_w = int(img_w * 0.95); max_allowed_h = int(img_h * 0.95)
        if target_w > max_allowed_w: target_w = max_allowed_w

        ratio = target_w / max(1, wm.width)
        target_h = int(wm.height * ratio)
        if target_h > max_allowed_h:
            ratio_h = max_allowed_h / max(1, target_h)
            target_w = int(target_w * ratio_h)
            target_h = max_allowed_h
        
        wm = wm.resize((target_w, target_h), Image.Resampling.LANCZOS)
        if float(cfg["angle"]) != 0:
            wm = wm.rotate(float(cfg["angle"]), expand=True, resample=Image.Resampling.BICUBIC).convert('RGBA')
            if wm.width > img_w or wm.height > img_h: wm.thumbnail((max_allowed_w, max_allowed_h), Image.Resampling.LANCZOS)

        alpha = wm.split()[3]
        alpha = ImageEnhance.Brightness(alpha).enhance(float(cfg["opacity"]))
        wm.putalpha(alpha)
        bbox = wm.getbbox()
        if bbox: wm = wm.crop(bbox)
        return wm

    def _dummy(self, txt):
        img = Image.new('RGBA', (100, 50), (0,0,0,0))
        ImageDraw.Draw(img).text((10,10), txt, fill="red", font=ImageFont.load_default())
        return img

    def _text_wm(self, text, color):
        if not text: text = " "
        f_size = 200
        sys_font = load_default_font(f_size)
        W, H = 4000, 4000
        img = Image.new('RGBA', (W, H), (0,0,0,0))
        draw = ImageDraw.Draw(img)
        draw.text((W/2, H/2), text, font=sys_font, fill=color, anchor="mm")
        bbox = img.getbbox()
        return img.crop(bbox) if bbox else Image.new('RGBA', (10, 10), (0,0,0,0))

    def process(self, path, cfg):
        try:
            base = Image.open(path).convert("RGBA")
            base = ImageOps.exif_transpose(base)
            wm = self.create_layer(cfg, base.size)
            can = Image.new('RGBA', base.size, (0,0,0,0))
            can.paste(base, (0,0))
            if cfg["tile_mode"]:
                sx = wm.width + int(wm.width * float(cfg.get("tile_spacing_x", 1.0)))
                sy = wm.height + int(wm.height * float(cfg.get("tile_spacing_y", 1.0)))
                sx, sy = max(sx, 10), max(sy, 10)
                start_y = -sy
                while start_y < base.height:
                    start_x = -sx
                    while start_x < base.width:
                        can.paste(wm, (int(start_x), int(start_y)), mask=wm)
                        start_x += sx
                    start_y += sy
            else:
                bw, bh = base.width, base.height
                ww, wh = wm.width, wm.height
                margin = max(5, int(min(bw, bh) * 0.02))
                max_x = bw - ww; max_y = bh - wh
                pos = cfg["position"]
                x, y = 0, 0
                if "右" in pos: x = max_x - margin
                elif "左" in pos: x = margin
                else: x = max_x // 2
                if "下" in pos: y = max_y - margin
                elif "上" in pos: y = margin
                else: y = max_y // 2
                final_x = max(0, min(x, max_x)); final_y = max(0, min(y, max_y))
                can.paste(wm, (int(final_x), int(final_y)), mask=wm)
            out_dir = os.path.join(os.path.dirname(path), "已加水印")
            if not os.path.exists(out_dir): os.makedirs(out_dir)
            return can, out_dir, os.path.basename(path)
        except Exception as e: return None, str(e), None

class ResizerProcessor:
    @staticmethod
    def process_image(input_path, output_folder, target_w, target_h, mode="direct", crop_ratio=None, prefix="妲己改尺寸", anchor=(0.5, 0.5), crop_scale=1.0):
        try:
            with Image.open(input_path) as img:
                if img.mode in ("RGBA", "P"): img = img.convert("RGB")
                img = ImageOps.exif_transpose(img)
                final_img = None
                
                if mode == "direct":
                    final_img = img.resize((target_w, target_h), Image.Resampling.LANCZOS)
                else:
                    target_aspect = target_w / target_h
                    img_aspect = img.width / img.height
                    
                    if img_aspect > target_aspect:
                        base_crop_h = img.height
                        base_crop_w = int(base_crop_h * target_aspect)
                    else:
                        base_crop_w = img.width
                        base_crop_h = int(base_crop_w / target_aspect)
                    
                    final_crop_w = int(base_crop_w * crop_scale)
                    final_crop_h = int(base_crop_h * crop_scale)
                    
                    center_x = img.width * anchor[0]
                    center_y = img.height * anchor[1]
                    
                    x1 = int(center_x - final_crop_w / 2)
                    y1 = int(center_y - final_crop_h / 2)
                    
                    if x1 < 0: x1 = 0
                    if y1 < 0: y1 = 0
                    if x1 + final_crop_w > img.width: x1 = img.width - final_crop_w
                    if y1 + final_crop_h > img.height: y1 = img.height - final_crop_h
                    
                    x1 = max(0, x1)
                    y1 = max(0, y1)
                    
                    box = (x1, y1, x1 + final_crop_w, y1 + final_crop_h)
                    final_img = img.crop(box).resize((target_w, target_h), Image.Resampling.LANCZOS)

                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:19]
                new_filename = f"{prefix}_{timestamp}.jpg"
                output_path = os.path.join(output_folder, new_filename)
                final_img.save(output_path, "JPEG", quality=95)
                return True, new_filename
        except Exception as e: return False, str(e)

# 监听器
class ResizerWatchHandler:
    def __init__(self, output_folder, params, log_callback):
        self.output_folder = output_folder; self.params = params; self.log_callback = log_callback
    def dispatch(self, event):
        if event.event_type == 'created': self.on_created(event)
    def on_created(self, event):
        if event.is_directory: return
        filename = os.path.basename(event.src_path)
        
        if filename.startswith("."): return
        if not filename.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.webp')): return
        
        time.sleep(1)
        
        self.log_callback(f"检测: {filename}")
        
        scale = self.params.get('crop_scale', 1.0)
        anchor = self.params.get('anchor', (0.5, 0.5))
        
        success, msg = ResizerProcessor.process_image(
            event.src_path, self.output_folder, self.params['w'], self.params['h'], 
            self.params['mode'], self.params['crop_ratio'], self.params['prefix'], 
            anchor,
            scale
        )
        if success: self.log_callback(f"✅ 完成: {msg}")
        else: self.log_callback(f"❌ 失败: {msg}")

class WatermarkWatchHandler:
    def __init__(self, log_callback, get_cfg_func):
        self.log_callback = log_callback; self.get_cfg_func = get_cfg_func
    def dispatch(self, event):
        if event.event_type == 'created': self.on_created(event)
    def on_created(self, event):
        if event.is_directory: return
        filename = os.path.basename(event.src_path)
        if filename.startswith(".") or "WM_" in filename: return
        if not filename.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.webp')): return
        time.sleep(1)
        cfg = self.get_cfg_func()
        self.log_callback(f"检测: {filename}")
        proc = WatermarkProcessor()
        img, out_dir, name = proc.process(event.src_path, cfg)
        if img:
            try:
                img.convert("RGB").save(os.path.join(out_dir, f"WM_{name}"))
                self.log_callback(f"✅ 水印: {name}")
            except Exception as e: self.log_callback(f"❌ 保存失败: {e}")
        else: self.log_callback(f"❌ 失败: {out_dir}")

# =========================================================================
#  Resizer Frame (改尺寸)
# =========================================================================
class ResizerFrame(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        # 从配置加载
        self.saved_cfg = config_mgr.data["resizer"]
        
        self.watching = False
        self.manual_images = []
        self.ref_image_path = None
        self.current_img_index = 0
        self.resize_mode = ctk.StringVar(value=self.saved_cfg.get("mode", "direct"))
        
        # 裁剪状态变量
        self.crop_anchor = (0.5, 0.5)
        self.crop_scale = 1.0 
        
        # 导出路径记忆 (Session级别)
        self.manual_save_path = None
        
        self.drag_data = {"x": 0, "y": 0, "mode": None}
        self.preview_offset = (0, 0)
        self.locked_ratio = None
        self.img_scale_ratio = 1.0 
        self.is_locked = True 
        
        self.current_box_coords = None 
        self.handle_coords = {}
        
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0)
        self.grid_rowconfigure(0, weight=1)
        self.create_preview_area()
        self.create_control_panel()
        self.update_ui_state()
        self.drop_target_register(DND_FILES)
        self.dnd_bind('<<Drop>>', self.on_drop)

    def on_drop(self, event):
        files = parse_dropped_files(event.data)
        imgs = [f for f in files if f.lower().endswith(('.jpg','.jpeg','.png','.webp','.bmp'))]
        if imgs:
            if self.mode_seg.get() == "自动监听":
                self.ref_image_path = imgs[0]
                self.log(f"参考图: {os.path.basename(imgs[0])}")
            else:
                self.mode_seg.set("手动选择")
                self.on_mode_switch("手动选择")
                self.manual_images = imgs
                self.current_img_index = 0
                self.lbl_count.configure(text=f"已加载: {len(imgs)} 张")
                self.btn_run_man.configure(state="normal")
                self.log(f"拖入: {len(imgs)} 张")
            self.update_nav_buttons()
            self.draw_schematic()

    def create_preview_area(self):
        self.preview_container = ctk.CTkFrame(self, corner_radius=0, fg_color=("gray90", "#202020"))
        self.preview_container.grid(row=0, column=0, sticky="nsew", padx=(0, 2), pady=0)
        
        self.cv_preview = ctk.CTkCanvas(self.preview_container, highlightthickness=0)
        self.cv_preview.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.9, relheight=0.9)
        self.cv_preview.bind("<B1-Motion>", self.on_canvas_drag)
        self.cv_preview.bind("<Button-1>", self.on_canvas_click)
        self.cv_preview.bind("<Configure>", self.on_canvas_resize)
        
        self.btn_prev = ctk.CTkButton(self.preview_container, text="<", width=30, height=50, command=self.prev_img, fg_color="transparent", text_color="gray", hover_color=("gray80", "gray30"), font=("Arial", 20))
        self.btn_next = ctk.CTkButton(self.preview_container, text=">", width=30, height=50, command=self.next_img, fg_color="transparent", text_color="gray", hover_color=("gray80", "gray30"), font=("Arial", 20))
        
        self.lbl_hint = ctk.CTkLabel(self.preview_container, text="拖拽图片到这里\n或点击右侧选择", font=("Microsoft YaHei UI", 16), text_color="gray")
        self.after(200, self.update_canvas_bg)

    def update_nav_buttons(self):
        if self.mode_seg.get() == "手动选择" and len(self.manual_images) > 1:
            self.btn_prev.place(relx=0.02, rely=0.5, anchor="w")
            self.btn_next.place(relx=0.98, rely=0.5, anchor="e")
        else:
            self.btn_prev.place_forget()
            self.btn_next.place_forget()

    def prev_img(self):
        if not self.manual_images: return
        self.current_img_index = (self.current_img_index - 1) % len(self.manual_images)
        self.draw_schematic()

    def next_img(self):
        if not self.manual_images: return
        self.current_img_index = (self.current_img_index + 1) % len(self.manual_images)
        self.draw_schematic()

    def on_canvas_resize(self, event): self.draw_schematic()
    def update_canvas_bg(self):
        mode = ctk.get_appearance_mode()
        bg_col = "#E0E0E0" if mode == "Light" else "#202020"
        self.cv_preview.configure(bg=bg_col)
        self.draw_schematic()

    def create_control_panel(self):
        self.ctrl_scroll = ctk.CTkScrollableFrame(self, width=340, corner_radius=0, fg_color="transparent")
        self.ctrl_scroll.grid(row=0, column=1, sticky="nsew")
        apply_thin_scrollbar(self.ctrl_scroll)

        ctk.CTkLabel(self.ctrl_scroll, text="参数设置", font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w", padx=15, pady=(20, 10))
        self.mode_seg = ctk.CTkSegmentedButton(self.ctrl_scroll, values=["手动选择", "自动监听"], command=self.on_mode_switch)
        self.mode_seg.set("手动选择")
        self.mode_seg.pack(fill="x", padx=15, pady=10)
        
        card_algo = ctk.CTkFrame(self.ctrl_scroll)
        card_algo.pack(fill="x", padx=15, pady=10)
        ctk.CTkLabel(card_algo, text="处理模式", font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", padx=10, pady=(10,5))
        ctk.CTkRadioButton(card_algo, text="直接拉伸 (Direct)", variable=self.resize_mode, value="direct", command=self.update_ui_state).pack(anchor="w", padx=10, pady=5)
        ctk.CTkRadioButton(card_algo, text="按比例裁剪 (Smart Ratio)", variable=self.resize_mode, value="smart", command=self.update_ui_state).pack(anchor="w", padx=10, pady=5)
        ctk.CTkRadioButton(card_algo, text="自定义裁剪 (Custom Crop)", variable=self.resize_mode, value="custom", command=self.update_ui_state).pack(anchor="w", padx=10, pady=(5,15))

        card_size = ctk.CTkFrame(self.ctrl_scroll)
        card_size.pack(fill="x", padx=15, pady=10)
        ctk.CTkLabel(card_size, text="输出尺寸 (px)", font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", padx=10, pady=(10,5))
        self.preset_frame = ctk.CTkFrame(card_size, fg_color="transparent")
        self.preset_buttons = {}
        def add_preset(txt, w, h):
            btn = ctk.CTkButton(self.preset_frame, text=txt, width=50, height=28, fg_color="gray", command=lambda: self.apply_smart_preset(w, h, txt))
            btn.pack(side="left", padx=4)
            self.preset_buttons[txt] = btn
        add_preset("1:1", 1000, 1000); add_preset("3:4", 900, 1200); add_preset("4:3", 1200, 900); add_preset("9:16", 1080, 1920); add_preset("16:9", 1920, 1080)
        
        sz_box = ctk.CTkFrame(card_size, fg_color="transparent")
        sz_box.pack(fill="x", padx=10, pady=(5,15))
        
        self.e_w = ctk.CTkEntry(sz_box, placeholder_text="宽")
        self.e_w.pack(side="left", fill="x", expand=True, padx=(0,2))
        self.e_w.insert(0, str(self.saved_cfg.get("w", 1080)))
        self.e_w.bind("<KeyRelease>", lambda e: self.on_entry_change(e, "w"))
        
        self.btn_lock = ctk.CTkButton(sz_box, text="🔒", width=30, height=28, 
                                      fg_color="transparent", text_color="gray", 
                                      hover_color=("gray85", "gray30"),
                                      command=self.toggle_lock)
        self.btn_lock.pack(side="left", padx=2)
        
        self.e_h = ctk.CTkEntry(sz_box, placeholder_text="高")
        self.e_h.pack(side="left", fill="x", expand=True, padx=(2,0))
        self.e_h.insert(0, str(self.saved_cfg.get("h", 1080)))
        self.e_h.bind("<KeyRelease>", lambda e: self.on_entry_change(e, "h"))

        self.action_container = ctk.CTkFrame(self.ctrl_scroll, fg_color="transparent")
        self.action_container.pack(fill="x", padx=0, pady=10)
        
        self.man_frame = ctk.CTkFrame(self.action_container)
        self.btn_files = ctk.CTkButton(self.man_frame, text="📂 选择图片 (或直接拖入)", command=self.sel_files, height=40)
        self.btn_files.pack(fill="x", padx=10, pady=(15,5))
        self.btn_clear = ctk.CTkButton(self.man_frame, text="🗑 清空图片", fg_color="#E57373", hover_color="#C62828", command=self.clear_images)
        self.btn_clear.pack(fill="x", padx=10, pady=(0,5))
        self.lbl_count = ctk.CTkLabel(self.man_frame, text="当前未选择图片", text_color="gray")
        self.lbl_count.pack(pady=(0,5))
        
        # === 新增：导出路径显示 ===
        self.btn_out_path = ctk.CTkButton(self.man_frame, text="📂 保存至: 未设置 (每次询问)", fg_color="gray", command=self.change_save_path)
        self.btn_out_path.pack(fill="x", padx=10, pady=(0, 10))
        
        self.btn_run_man = ctk.CTkButton(self.man_frame, text="🚀 开始导出", state="disabled", fg_color="green", height=45, font=("Microsoft YaHei UI", 15, "bold"), command=self.run_manual)
        self.btn_run_man.pack(fill="x", padx=10, pady=(0,15))
        
        self.auto_frame = ctk.CTkFrame(self.action_container)
        ctk.CTkLabel(self.auto_frame, text="输出前缀:").pack(anchor="w", padx=10, pady=(10,0))
        self.e_prefix = ctk.CTkEntry(self.auto_frame)
        self.e_prefix.pack(fill="x", padx=10, pady=(0,10))
        self.e_prefix.insert(0, self.saved_cfg.get("prefix", "妲己批量"))
        self.btn_folder = ctk.CTkButton(self.auto_frame, text="📂 选择监听文件夹", command=self.sel_folder, height=40)
        self.btn_folder.pack(fill="x", padx=10, pady=5)
        self.btn_ref_img = ctk.CTkButton(self.auto_frame, text="🖼️ 上传预览参考图", command=self.load_ref_image, fg_color="gray")
        self.btn_ref_img.pack(fill="x", padx=10, pady=5)
        self.lbl_folder = ctk.CTkLabel(self.auto_frame, text="未选择", text_color="gray")
        self.lbl_folder.pack(pady=5)
        self.btn_run_auto = ctk.CTkButton(self.auto_frame, text="📡 全自动模式启动", height=45, font=("Microsoft YaHei UI", 15, "bold"), command=self.toggle_watch)
        self.btn_run_auto.pack(fill="x", padx=10, pady=(0,15))
        self.man_frame.pack(fill="x", padx=15)

        ctk.CTkLabel(self.ctrl_scroll, text="运行日志", font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", padx=15, pady=(10,  5))
        self.log_box = ctk.CTkTextbox(self.ctrl_scroll, height=150, font=("Consolas", 11))
        self.log_box.pack(fill="x", padx=15, pady=(0, 20))
        self.log_box.configure(state="disabled")

    def toggle_lock(self):
        self.is_locked = not self.is_locked
        if self.is_locked:
            self.btn_lock.configure(text="🔒")
            try:
                w = int(self.e_w.get())
                h = int(self.e_h.get())
                if h != 0: self.locked_ratio = w / h
            except: pass
        else:
            self.btn_lock.configure(text="🔓")

    def update_ui_state(self):
        mode = self.resize_mode.get()
        # 更新配置
        config_mgr.data["resizer"]["mode"] = mode
        
        if mode == "smart": 
            self.preset_frame.pack(fill="x", padx=10, pady=(0, 5), before=self.e_w.master)
            self.is_locked = True
            self.btn_lock.configure(state="normal", text="🔒")
            try:
                w = int(self.e_w.get()); h = int(self.e_h.get())
                self.locked_ratio = w / h
            except: self.locked_ratio = 1.0
        else: 
            self.preset_frame.pack_forget()
            self.btn_lock.configure(state="disabled", text="×")
            self.is_locked = False
            self.locked_ratio = None
        self.draw_schematic()

    def apply_smart_preset(self, w, h, active_key):
        self.locked_ratio = w / h
        self.e_w.delete(0, "end"); self.e_w.insert(0, str(w))
        self.e_h.delete(0, "end"); self.e_h.insert(0, str(h))
        self.on_entry_change(None, "w") # 触发保存
        self.is_locked = True
        self.btn_lock.configure(text="🔒")
        for key, btn in self.preset_buttons.items():
            if key == active_key: btn.configure(fg_color="#1F6AA5")
            else: btn.configure(fg_color="gray")
        self.draw_schematic()

    def on_entry_change(self, event, source):
        # 联动逻辑
        if self.is_locked and self.locked_ratio:
            try:
                if source == "w":
                    val = int(self.e_w.get())
                    new_h = int(val / self.locked_ratio)
                    if abs(new_h - int(self.e_h.get() or 0)) > 1:
                        self.e_h.delete(0, "end"); self.e_h.insert(0, str(new_h))
                elif source == "h":
                    val = int(self.e_h.get())
                    new_w = int(val * self.locked_ratio)
                    if abs(new_w - int(self.e_w.get() or 0)) > 1:
                        self.e_w.delete(0, "end"); self.e_w.insert(0, str(new_w))
            except: pass
        
        # 保存配置
        try:
            config_mgr.data["resizer"]["w"] = int(self.e_w.get())
            config_mgr.data["resizer"]["h"] = int(self.e_h.get())
        except: pass

        if not self.is_locked:
            try:
                w = int(self.e_w.get()); h = int(self.e_h.get())
                if h != 0: self.locked_ratio = w / h
            except: pass

        for btn in self.preset_buttons.values(): btn.configure(fg_color="gray")
        self.draw_schematic()

    def on_canvas_click(self, event):
        if self.resize_mode.get() == "direct": return
        self.drag_data["start_x"] = event.x
        self.drag_data["start_y"] = event.y
        self.drag_data["mode"] = None
        
        if self.handle_coords:
            for tag, (cx, cy) in self.handle_coords.items():
                if math.hypot(event.x - cx, event.y - cy) < 12:
                    self.drag_data["mode"] = "resize"
                    self.drag_data["handle_tag"] = tag
                    return
        
        if self.current_box_coords:
            x1, y1, x2, y2 = self.current_box_coords
            if x1 <= event.x <= x2 and y1 <= event.y <= y2:
                self.drag_data["mode"] = "move"
                return

    def on_canvas_drag(self, event):
        if self.resize_mode.get() == "direct": return
        if not hasattr(self, 'preview_img_obj') or self.drag_data["mode"] is None: return
        if not self.current_box_coords: return
        
        dx = event.x - self.drag_data["start_x"]
        dy = event.y - self.drag_data["start_y"]
        
        try:
            img_disp_w = self.preview_img_obj.width()
            img_disp_h = self.preview_img_obj.height()
        except: return
        off_x, off_y = self.preview_offset
        x1, y1, x2, y2 = self.current_box_coords
        
        if self.drag_data["mode"] == "move":
            nx1 = x1 + dx; ny1 = y1 + dy; nx2 = x2 + dx; ny2 = y2 + dy
            if nx1 < off_x: diff = off_x - nx1; nx1 += diff; nx2 += diff
            if ny1 < off_y: diff = off_y - ny1; ny1 += diff; ny2 += diff
            if nx2 > off_x + img_disp_w: diff = nx2 - (off_x + img_disp_w); nx1 -= diff; nx2 -= diff
            if ny2 > off_y + img_disp_h: diff = ny2 - (off_y + img_disp_h); ny1 -= diff; ny2 -= diff
            
            self.current_box_coords = (nx1, ny1, nx2, ny2)
            self.update_box_visuals(nx1, ny1, nx2, ny2)
            center_x = (nx1 + nx2) / 2; center_y = (ny1 + ny2) / 2
            self.crop_anchor = ((center_x - off_x)/img_disp_w, (center_y - off_y)/img_disp_h)
            self.drag_data["start_x"] = event.x; self.drag_data["start_y"] = event.y
            
        elif self.drag_data["mode"] == "resize":
            htag = self.drag_data["handle_tag"]
            curr_w = x2 - x1
            change_w = 0
            if "L" in htag: change_w = -dx
            elif "R" in htag: change_w = dx
            if change_w == 0:
                # 简单估算高度带来的宽度变化
                curr_h = y2 - y1
                ratio = curr_w / max(1, curr_h)
                if "T" in htag: change_w = -dy * ratio 
                elif "B" in htag: change_w = dy * ratio
            
            new_w = curr_w + change_w
            if new_w < 20: return
            if new_w > img_disp_w: new_w = img_disp_w
            
            try: target_w = int(self.e_w.get()); target_h = int(self.e_h.get())
            except: target_w, target_h = 100, 100
            target_ratio = target_w / target_h
            img_ratio = img_disp_w / img_disp_h
            
            if img_ratio > target_ratio: max_box_w = img_disp_h * target_ratio
            else: max_box_w = img_disp_w
                
            new_scale = new_w / max_box_w
            if new_scale > 1.0: new_scale = 1.0
            if new_scale < 0.1: new_scale = 0.1
            self.crop_scale = new_scale
            self.draw_schematic()

    def update_box_visuals(self, x1, y1, x2, y2):
        self.cv_preview.coords("crop_box_rect", x1, y1, x2, y2)
        w = x2 - x1; h = y2 - y1
        self.cv_preview.coords("line_v1", x1+w/3, y1, x1+w/3, y2)
        self.cv_preview.coords("line_v2", x1+2*w/3, y1, x1+2*w/3, y2)
        self.cv_preview.coords("line_h1", x1, y1+h/3, x2, y1+h/3)
        self.cv_preview.coords("line_h2", x1, y1+2*h/3, x2, y1+2*h/3)
        r = 6
        self.handle_coords = {
            "handle_TL": (x1, y1), "handle_TR": (x2, y1),
            "handle_BL": (x1, y2), "handle_BR": (x2, y2)
        }
        self.cv_preview.coords("handle_TL", x1-r, y1-r, x1+r, y1+r)
        self.cv_preview.coords("handle_TR", x2-r, y1-r, x2+r, y1+r)
        self.cv_preview.coords("handle_BL", x1-r, y2-r, x1+r, y2+r)
        self.cv_preview.coords("handle_BR", x2-r, y2-r, x2+r, y2+r)

    def draw_schematic(self):
        self.cv_preview.delete("all")
        self.current_box_coords = None
        self.handle_coords = {}
        
        pil_img = None
        mode = self.mode_seg.get() 
        
        if mode == "手动选择" and self.manual_images:
            if self.current_img_index < len(self.manual_images):
                pil_img = Image.open(self.manual_images[self.current_img_index])
        elif mode == "自动监听" and self.ref_image_path:
             try: pil_img = Image.open(self.ref_image_path)
             except: pass

        if not pil_img:
            self.lbl_hint.place(relx=0.5, rely=0.5, anchor="center")
            if mode == "自动监听":
                self.lbl_hint.configure(text="请上传参考图\n以预览裁剪线框")
            else:
                self.lbl_hint.configure(text="拖拽图片到这里\n或点击右侧选择")
            self.btn_prev.place_forget(); self.btn_next.place_forget()
            return

        self.lbl_hint.place_forget()
        try:
            cw = self.cv_preview.winfo_width(); ch = self.cv_preview.winfo_height()
            if cw < 10: cw, ch = 400, 300
            
            ratio = min(cw/pil_img.width, ch/pil_img.height) * 0.9
            self.img_scale_ratio = ratio 

            new_w = int(pil_img.width * ratio); new_h = int(pil_img.height * ratio)
            self.preview_img_obj = ImageTk.PhotoImage(pil_img.resize((new_w, new_h)))
            
            px = (cw - new_w) // 2; py = (ch - new_h) // 2
            self.preview_offset = (px, py)
            
            self.cv_preview.create_image(px, py, image=self.preview_img_obj, anchor="nw")
            
            if self.resize_mode.get() == "direct": return

            try: target_w = int(self.e_w.get()); target_h = int(self.e_h.get())
            except: target_w, target_h = 100, 100
            
            target_ratio = target_w / target_h
            img_ratio = pil_img.width / pil_img.height
            
            if img_ratio > target_ratio: 
                base_box_h = new_h; base_box_w = base_box_h * target_ratio
            else: 
                base_box_w = new_w; base_box_h = base_box_w / target_ratio
            
            box_w = base_box_w * self.crop_scale
            box_h = base_box_h * self.crop_scale
            
            center_x = px + new_w * self.crop_anchor[0]
            center_y = py + new_h * self.crop_anchor[1]
            x1 = center_x - box_w / 2; y1 = center_y - box_h / 2
            
            if x1 < px: x1 = px
            if y1 < py: y1 = py
            if x1 + box_w > px + new_w: x1 = px + new_w - box_w
            if y1 + box_h > py + new_h: y1 = py + new_h - box_h
            x2 = x1 + box_w; y2 = y1 + box_h
            
            self.current_box_coords = (x1, y1, x2, y2)

            self.cv_preview.create_rectangle(x1, y1, x2, y2, outline="#007AFF", width=2, tags=("crop_box", "crop_box_rect"))
            self.cv_preview.create_line(x1+box_w/3, y1, x1+box_w/3, y2, fill="#007AFF", dash=(4,4), tags=("crop_box", "line_v1"))
            self.cv_preview.create_line(x1+2*box_w/3, y1, x1+2*box_w/3, y2, fill="#007AFF", dash=(4,4), tags=("crop_box", "line_v2"))
            self.cv_preview.create_line(x1, y1+box_h/3, x2, y1+box_h/3, fill="#007AFF", dash=(4,4), tags=("crop_box", "line_h1"))
            self.cv_preview.create_line(x1, y1+2*box_h/3, x2, y1+2*box_h/3, fill="#007AFF", dash=(4,4), tags=("crop_box", "line_h2"))
            
            r = 6
            self.handle_coords = {
                "handle_TL": (x1, y1), "handle_TR": (x2, y1),
                "handle_BL": (x1, y2), "handle_BR": (x2, y2)
            }
            self.cv_preview.create_oval(x1-r, y1-r, x1+r, y1+r, fill="#007AFF", outline="white", tags=("crop_handle", "handle_TL"))
            self.cv_preview.create_oval(x2-r, y1-r, x2+r, y1+r, fill="#007AFF", outline="white", tags=("crop_handle", "handle_TR"))
            self.cv_preview.create_oval(x1-r, y2-r, x1+r, y2+r, fill="#007AFF", outline="white", tags=("crop_handle", "handle_BL"))
            self.cv_preview.create_oval(x2-r, y2-r, x2+r, y2+r, fill="#007AFF", outline="white", tags=("crop_handle", "handle_BR"))
            
            self.cv_preview.tag_raise("crop_box")
            self.cv_preview.tag_raise("crop_handle")

        except Exception as e: print(e)

    def load_ref_image(self):
        p = filedialog.askopenfilename(filetypes=[("Img", "*.jpg *.png")])
        if p:
            self.ref_image_path = p
            self.log(f"参考图: {os.path.basename(p)}")
            self.draw_schematic()

    def clear_images(self):
        self.manual_images = []
        self.ref_image_path = None
        self.current_img_index = 0
        self.lbl_count.configure(text="当前未选择图片")
        self.btn_run_man.configure(state="disabled")
        self.update_nav_buttons()
        self.draw_schematic()

    def on_mode_switch(self, value):
        if value == "手动选择":
            self.auto_frame.pack_forget(); self.man_frame.pack(fill="x", padx=15)
        else:
            self.man_frame.pack_forget(); self.auto_frame.pack(fill="x", padx=15)
        self.draw_schematic()

    def show_image_preview(self, path):
        self.update_nav_buttons()
        self.draw_schematic()

    def sel_files(self):
        fs = filedialog.askopenfilenames(filetypes=[("Img", "*.jpg *.png *.jpeg *.webp")])
        if fs:
            self.manual_images = fs
            self.current_img_index = 0
            self.lbl_count.configure(text=f"已就绪: {len(fs)} 张图片")
            self.btn_run_man.configure(state="normal")
            self.log(f"已加载 {len(fs)} 张图片")
            self.update_nav_buttons()
            self.draw_schematic()

    def log(self, msg):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def get_params(self):
        try:
            w = int(self.e_w.get())
            h = int(self.e_h.get())
            if w <= 0 or h <= 0: return None
        except: return None
        
        # 保存前缀
        config_mgr.data["resizer"]["prefix"] = self.e_prefix.get()
        
        return {
            "w": w, "h": h,
            "mode": self.resize_mode.get(),
            "prefix": self.e_prefix.get().strip() or "妲己批量",
            "crop_ratio": None,
            "crop_scale": self.crop_scale,
            "anchor": self.crop_anchor
        }

    # === 路径变更逻辑 ===
    def change_save_path(self):
        d = filedialog.askdirectory()
        if d:
            self.manual_save_path = d
            self.btn_out_path.configure(text=f"📂 保存至: {os.path.basename(d)}")
    
    def run_manual(self):
        p = self.get_params()
        if not p: return messagebox.showerror("错误", "尺寸无效")
        
        # 路径判断
        if self.manual_save_path:
            od = self.manual_save_path
        else:
            od = filedialog.askdirectory(title="选择保存位置")
            if not od: return
            self.manual_save_path = od
            self.btn_out_path.configure(text=f"📂 保存至: {os.path.basename(od)}")

        self.log("启动手动处理...")
        sd = os.path.join(od, "妲己改尺寸结果")
        if not os.path.exists(sd): os.makedirs(sd)
        
        anchor = self.crop_anchor
        scale = self.crop_scale
        
        for f in self.manual_images:
            _, msg = ResizerProcessor.process_image(f, sd, p['w'], p['h'], p['mode'], None, "手动", anchor, scale)
            self.log(f"处理: {os.path.basename(f)}")
        messagebox.showinfo("完成", f"全部完成，已保存至:\n{sd}")
        self.manual_images = []
        self.lbl_count.configure(text="处理完成")
        self.btn_run_man.configure(state="disabled")
        self.draw_schematic()

    def sel_folder(self):
        d = filedialog.askdirectory()
        if d:
            self.watch_dir = d
            self.lbl_folder.configure(text=os.path.basename(d))
            self.btn_run_auto.configure(state="normal")

    def toggle_watch(self):
        if not hasattr(self, 'watch_dir') or not self.watch_dir: return messagebox.showwarning("提示", "请先选择需要监听的文件夹！")
        if self.watching:
            if self.observer: self.observer.stop(); self.observer.join()
            self.watching = False
            self.btn_run_auto.configure(text="📡 全自动模式启动", fg_color=["#3B8ED0", "#1F6AA5"], hover_color=["#36719F", "#144870"])
            self.mode_seg.configure(state="normal")
            self.log("监听停止")
        else:
            p = self.get_params()
            if not p: return messagebox.showerror("错误", "尺寸无效")
            od = os.path.join(self.watch_dir, "妲己自动结果")
            if not os.path.exists(od): os.makedirs(od)
            self.observer = Observer()
            h = ResizerWatchHandler(od, p, self.log)
            class Handler(FileSystemEventHandler):
                def on_created(self, event): h.dispatch(event)
            self.observer.schedule(Handler(), self.watch_dir, recursive=False)
            self.observer.start()
            self.watching = True
            self.btn_run_auto.configure(text="⏹ 妲己正在拼命修改尺寸中", fg_color="green", hover_color="#F22A2A")
            self.mode_seg.configure(state="disabled")
            self.log(f"正在监听: {self.watch_dir}")

class WatermarkFrame(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        # 读取配置
        self.cfg = config_mgr.data["watermark"]
        
        self.watching = False
        self.manual_files = []
        self.current_img_index = 0
        self.preview_w, self.preview_h = 640, 360 
        
        # 导出路径记忆 (Session)
        self.manual_save_path = None
        
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0)
        self.grid_rowconfigure(0, weight=1)
        self.create_preview_area()
        self.create_control_panel()
        self.drop_target_register(DND_FILES)
        self.dnd_bind('<<Drop>>', self.on_drop)
        
    def on_drop(self, event):
        files = parse_dropped_files(event.data)
        imgs = [f for f in files if f.lower().endswith(('.jpg','.jpeg','.png','.webp','.bmp'))]
        if imgs:
            self.tab_ctrl.set("手动处理")
            self.manual_files = imgs
            self.current_img_index = 0
            self.lbl_man.configure(text=f"拖入: {len(imgs)} 张")
            self.log(f"拖入图片: {len(imgs)} 张")
            self.update_nav_buttons()
            self.upd()

    def create_preview_area(self):
        self.left_container = ctk.CTkFrame(self, corner_radius=0, fg_color=("gray95", "#202020"))
        self.left_container.grid(row=0, column=0, sticky="nsew", padx=(0, 2))
        self.left_container.grid_rowconfigure(0, weight=1)
        self.left_container.grid_columnconfigure(0, weight=1)
        
        self.preview_canvas = ctk.CTkCanvas(self.left_container, width=self.preview_w, height=self.preview_h, highlightthickness=0)
        self.preview_canvas.grid(row=0, column=0, sticky="") 
        
        self.btn_prev = ctk.CTkButton(self.left_container, text="<", width=30, height=50, command=self.prev_img, fg_color="transparent", text_color="gray", hover_color=("gray80", "gray30"), font=("Arial", 20))
        self.btn_next = ctk.CTkButton(self.left_container, text=">", width=30, height=50, command=self.next_img, fg_color="transparent", text_color="gray", hover_color=("gray80", "gray30"), font=("Arial", 20))
        
        self.lbl_prev_img = ctk.CTkLabel(self.preview_canvas, text="")
        self.lbl_prev_img.place(relx=0.5, rely=0.5, anchor="center")
        
        self.ratio_frame = ctk.CTkFrame(self.left_container, fg_color="transparent")
        self.ratio_frame.grid(row=1, column=0, pady=5)
        ctk.CTkLabel(self.ratio_frame, text="预览比例:").pack(side="left", padx=5)
        self.ratio_seg = ctk.CTkSegmentedButton(self.ratio_frame, values=["16:9", "4:3", "1:1", "9:16"], command=self.change_preview_ratio)
        self.ratio_seg.set("16:9")
        self.ratio_seg.pack(side="left", padx=5)
        self.after(200, self.update_bg)

    def update_nav_buttons(self):
        if len(self.manual_files) > 1:
            self.btn_prev.place(relx=0.05, rely=0.5, anchor="w")
            self.btn_next.place(relx=0.95, rely=0.5, anchor="e")
        else:
            self.btn_prev.place_forget()
            self.btn_next.place_forget()

    def prev_img(self):
        if not self.manual_files: return
        self.current_img_index = (self.current_img_index - 1) % len(self.manual_files)
        self.upd()

    def next_img(self):
        if not self.manual_files: return
        self.current_img_index = (self.current_img_index + 1) % len(self.manual_files)
        self.upd()

    def change_preview_ratio(self, value):
        ratio_map = {"16:9": (640, 360), "4:3": (640, 480), "1:1": (500, 500), "9:16": (360, 640)}
        self.preview_w, self.preview_h = ratio_map.get(value, (640, 360))
        self.preview_canvas.configure(width=self.preview_w, height=self.preview_h)
        self.upd()

    def update_bg(self):
        mode = ctk.get_appearance_mode()
        bg = "#E0E0E0" if mode == "Light" else "#303030" 
        self.preview_canvas.configure(bg=bg)
        self.upd()

    def create_control_panel(self):
        self.ctrl_scroll = ctk.CTkScrollableFrame(self, width=340, corner_radius=0, fg_color="transparent")
        self.ctrl_scroll.grid(row=0, column=1, sticky="nsew")
        apply_thin_scrollbar(self.ctrl_scroll)

        ctk.CTkLabel(self.ctrl_scroll, text="水印设置", font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w", padx=15, pady=(20, 10))
        card_content = ctk.CTkFrame(self.ctrl_scroll)
        card_content.pack(fill="x", padx=15, pady=5)
        self.wm_mode = ctk.StringVar(value=self.cfg.get("mode", "text"))
        row_mode = ctk.CTkFrame(card_content, fg_color="transparent")
        row_mode.pack(fill="x", padx=5, pady=5)
        ctk.CTkRadioButton(row_mode, text="文字水印", variable=self.wm_mode, value="text", command=self.upd).pack(side="left", padx=5)
        ctk.CTkRadioButton(row_mode, text="图片Logo", variable=self.wm_mode, value="image", command=self.upd).pack(side="left", padx=5)
        self.e_text = ctk.CTkEntry(card_content, placeholder_text="输入文字")
        self.e_text.pack(fill="x", padx=10, pady=(5,5))
        self.e_text.insert(0, self.cfg.get("watermark_text", "禁止盗用"))
        self.e_text.bind("<KeyRelease>", lambda e: self.upd())
        self.row_btns = ctk.CTkFrame(card_content, fg_color="transparent")
        self.row_btns.pack(fill="x", padx=5, pady=(0,10))
        self.btn_img = ctk.CTkButton(self.row_btns, text="选图", width=80, command=self.sel_wm_img)
        self.btn_col = ctk.CTkButton(self.row_btns, text="颜色", width=80, command=self.sel_col)
        ctk.CTkLabel(self.ctrl_scroll, text="执行操作", font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w", padx=15, pady=(20, 5))
        self.tab_ctrl = ctk.CTkTabview(self.ctrl_scroll, height=180)
        self.tab_ctrl.pack(fill="x", padx=15, pady=(0, 20))
        t1 = self.tab_ctrl.add("手动处理")
        ctk.CTkButton(t1, text="📂 选择图片 (或拖入)", command=self.sel_man_files).pack(pady=5, fill="x")
        self.lbl_man = ctk.CTkLabel(t1, text="未选择", text_color="gray")
        self.lbl_man.pack()
        ctk.CTkButton(t1, text="🗑 清空图片", fg_color="#E57373", hover_color="#C62828", command=self.clear_images).pack(pady=5, fill="x")
        
        # === 新增：导出路径显示 ===
        self.btn_out_path = ctk.CTkButton(t1, text="📂 保存至: 未设置 (每次询问)", fg_color="gray", command=self.change_save_path)
        self.btn_out_path.pack(pady=(5,5), fill="x")
        
        ctk.CTkButton(t1, text="🚀 导出结果", fg_color="green", height=45, font=("Microsoft YaHei UI", 15, "bold"), command=self.run_man).pack(pady=10, fill="x")
        t2 = self.tab_ctrl.add("自动监听")
        ctk.CTkButton(t2, text="📂 选择文件夹", command=self.sel_watch_dir).pack(pady=5, fill="x")
        self.lbl_watch = ctk.CTkLabel(t2, text="未选择", text_color="gray")
        self.lbl_watch.pack()
        self.btn_watch = ctk.CTkButton(t2, text="📡 全自动模式启动", height=45, font=("Microsoft YaHei UI", 15, "bold"), command=self.toggle_watch)
        self.btn_watch.pack(pady=10, fill="x")
        self.tab_ctrl.set("自动监听")
        ctk.CTkLabel(self.ctrl_scroll, text="样式微调", font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w", padx=15, pady=(10, 5))
        card_style = ctk.CTkFrame(self.ctrl_scroll)
        card_style.pack(fill="x", padx=15, pady=10)
        def sl(p, txt, k, f, t):
            ctk.CTkLabel(p, text=txt).pack(anchor="w", padx=10, pady=(5,0))
            s = ctk.CTkSlider(p, from_=f, to=t, command=lambda v: self.set_cfg(k, v))
            s.set(self.cfg[k])
            s.pack(fill="x", padx=10, pady=(0,10))
        sl(card_style, "缩放大小", "scale", 0.05, 1.0)
        sl(card_style, "透明度", "opacity", 0.1, 1.0)
        sl(card_style, "旋转角度", "angle", 0, 360)
        
        # Position & Tiling Control Card
        card_pos = ctk.CTkFrame(self.ctrl_scroll)
        card_pos.pack(fill="x", padx=15, pady=5)
        
        self.sw_tile = ctk.CTkSwitch(card_pos, text="全屏平铺防盗模式", command=self.toggle_tile_mode)
        self.sw_tile.pack(anchor="w", padx=10, pady=(10, 5))
        if self.cfg.get("tile_mode", True): self.sw_tile.select()

        # Spacing controls (Wrapper)
        self.frame_spacing = ctk.CTkFrame(card_pos, fg_color="transparent")
        
        ctk.CTkLabel(self.frame_spacing, text="横向间距:", font=("Microsoft YaHei UI", 11)).pack(anchor="w", padx=10, pady=(2,0))
        self.sl_sx = ctk.CTkSlider(self.frame_spacing, from_=0.0, to=3.0, number_of_steps=30, command=lambda v: self.set_cfg("tile_spacing_x", v))
        self.sl_sx.set(self.cfg.get("tile_spacing_x", 1.0))
        self.sl_sx.pack(fill="x", padx=10, pady=(0,5))

        ctk.CTkLabel(self.frame_spacing, text="纵向间距:", font=("Microsoft YaHei UI", 11)).pack(anchor="w", padx=10, pady=(2,0))
        self.sl_sy = ctk.CTkSlider(self.frame_spacing, from_=0.0, to=3.0, number_of_steps=30, command=lambda v: self.set_cfg("tile_spacing_y", v))
        self.sl_sy.set(self.cfg.get("tile_spacing_y", 1.0))
        self.sl_sy.pack(fill="x", padx=10, pady=(0,10))

        # Fixed position controls (Wrapper)
        self.frame_fixed = ctk.CTkFrame(card_pos, fg_color="transparent")
        ctk.CTkLabel(self.frame_fixed, text="固定位置:").pack(anchor="w", padx=10)
        self.c_pos = ctk.CTkComboBox(self.frame_fixed, values=["右下角","左下角","左上角","右上角","中间"], command=lambda v: self.set_cfg("position", v))
        self.c_pos.set(self.cfg.get("position", "右下角"))
        self.c_pos.pack(fill="x", padx=10, pady=(0,15))

        self.toggle_tile_mode() # Set initial visibility

        ctk.CTkLabel(self.ctrl_scroll, text="运行日志", font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", padx=15, pady=(20, 5))
        self.log_box = ctk.CTkTextbox(self.ctrl_scroll, height=120, font=("Consolas", 11))
        self.log_box.pack(fill="x", padx=15, pady=(0, 20))
        self.log_box.configure(state="disabled")
        self.after(500, self.upd)

    def log(self, msg):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def set_cfg(self, k, v):
        self.cfg[k] = v
        # 更新全局配置
        config_mgr.data["watermark"][k] = v
        self.upd()

    def toggle_tile_mode(self):
        is_tiled = bool(self.sw_tile.get())
        self.cfg["tile_mode"] = is_tiled
        config_mgr.data["watermark"]["tile_mode"] = is_tiled
        
        if is_tiled:
            self.frame_fixed.pack_forget()
            self.frame_spacing.pack(fill="x", padx=5, pady=5)
        else:
            self.frame_spacing.pack_forget()
            self.frame_fixed.pack(fill="x", padx=0, pady=0)
        self.upd()

    def upd(self):
        mode = self.wm_mode.get()
        self.cfg["mode"] = mode
        config_mgr.data["watermark"]["mode"] = mode
        
        if mode == "text":
            self.btn_img.pack_forget(); self.btn_col.pack(side="left", padx=5)
            self.e_text.configure(state="normal")
        else:
            self.btn_col.pack_forget(); self.btn_img.pack(side="left", padx=5)
            self.e_text.configure(state="disabled")

        self.cfg["watermark_text"] = self.e_text.get()
        config_mgr.data["watermark"]["watermark_text"] = self.e_text.get()
        
        try:
            is_light = (ctk.get_appearance_mode() == "Light")
            bg_rgb = (224, 224, 224) if is_light else (48, 48, 48)
            w, h = self.preview_w, self.preview_h
            base = Image.new("RGBA", (w, h), bg_rgb)
            
            if self.manual_files and os.path.exists(self.manual_files[self.current_img_index]):
                try:
                    user_img = Image.open(self.manual_files[self.current_img_index]).convert("RGBA")
                    ratio = min(w/user_img.width, h/user_img.height) * 0.95
                    nw, nh = int(user_img.width * ratio), int(user_img.height * ratio)
                    user_img = user_img.resize((nw, nh), Image.Resampling.LANCZOS)
                    px = (w - nw) // 2; py = (h - nh) // 2
                    base.paste(user_img, (px, py))
                    sim_w, sim_h = nw, nh
                    offset_x, offset_y = px, py
                except:
                    sim_w, sim_h = w, h
                    offset_x, offset_y = 0, 0
            else:
                sim_w, sim_h = w, h
                offset_x, offset_y = 0, 0

            proc = WatermarkProcessor()
            wm = proc.create_layer(self.cfg, (sim_w, sim_h))
            prev = Image.new("RGBA", base.size, (0,0,0,0))
            prev.paste(base, (0,0))
            
            if self.cfg.get("tile_mode", True):
                sx = wm.width + int(wm.width * self.cfg.get("tile_spacing_x", 1.0))
                sy = wm.height + int(wm.height * self.cfg.get("tile_spacing_y", 1.0))
                sx, sy = max(sx, 10), max(sy, 10)
                start_y = -sy
                while start_y < base.height:
                    start_x = -sx
                    while start_x < base.width:
                        prev.paste(wm, (int(start_x), int(start_y)), mask=wm)
                        start_x += sx
                    start_y += sy
            else:
                bw, bh = sim_w, sim_h
                ww, wh = wm.width, wm.height
                m = max(20, int(min(bw, bh) * 0.02))
                max_x = bw - ww; max_y = bh - wh
                x, y = 0, 0
                pos = self.cfg.get("position", "右下角")
                if "右" in pos: x = max_x - m
                elif "左" in pos: x = m
                else: x = max_x // 2
                if "下" in pos: y = max_y - m
                elif "上" in pos: y = m
                else: y = max_y // 2
                final_x = max(0, min(x, max_x)) + offset_x
                final_y = max(0, min(y, max_y)) + offset_y
                prev.paste(wm, (int(final_x), int(final_y)), mask=wm)
            
            tk_img = ctk.CTkImage(prev, size=(w, h))
            self.lbl_prev_img.configure(image=tk_img)
        except Exception as e: print(f"Preview error: {e}")

    def clear_images(self):
        self.manual_files = []
        self.current_img_index = 0
        self.lbl_man.configure(text="未选择")
        self.update_nav_buttons()
        self.upd()

    def sel_wm_img(self):
        p = filedialog.askopenfilename()
        if p: 
            self.cfg["watermark_path"] = p
            config_mgr.data["watermark"]["watermark_path"] = p
            self.upd()
            
    def sel_col(self):
        c = colorchooser.askcolor(color=self.cfg.get("text_color", "#000000"))[1]
        if c: 
            self.cfg["text_color"] = c
            config_mgr.data["watermark"]["text_color"] = c
            self.upd()
            
    def sel_man_files(self):
        fs = filedialog.askopenfilenames(filetypes=[("Img", "*.jpg *.png")])
        if fs: 
            self.manual_files = fs
            self.current_img_index = 0
            self.lbl_man.configure(text=f"已选 {len(fs)} 张")
            self.update_nav_buttons()
            self.upd()

    # === 路径变更逻辑 ===
    def change_save_path(self):
        d = filedialog.askdirectory()
        if d:
            self.manual_save_path = d
            self.btn_out_path.configure(text=f"📂 保存至: {os.path.basename(d)}")

    def run_man(self):
        if not self.manual_files: return
        
        if self.manual_save_path:
            od = self.manual_save_path
        else:
            od = filedialog.askdirectory()
            if not od: return
            self.manual_save_path = od
            self.btn_out_path.configure(text=f"📂 保存至: {os.path.basename(od)}")

        self.log("开始手动处理水印...")
        proc = WatermarkProcessor()
        for f in self.manual_files:
            c, _, n = proc.process(f, self.cfg)
            if c: 
                c.convert("RGB").save(os.path.join(od, f"WM_{n}"))
                self.log(f"成功: {n}")
        messagebox.showinfo("完成", "处理结束")
            
    def sel_watch_dir(self):
        d = filedialog.askdirectory()
        if d: self.watch_dir = d; self.lbl_watch.configure(text=os.path.basename(d))

    def toggle_watch(self):
        if not hasattr(self, 'watch_dir') or not self.watch_dir: return messagebox.showwarning("提示", "请先选择需要监听的文件夹！")
        if self.watching:
            if self.watcher: self.watcher.stop(); self.watcher.join()
            self.watching = False
            self.btn_watch.configure(text="📡 全自动模式启动", fg_color=["#3B8ED0", "#1F6AA5"], hover_color=["#36719F", "#144870"])
            self.log("监听停止")
        else:
            self.watcher = Observer()
            wp = WatermarkWatchHandler(self.log, lambda: self.cfg)
            class H(FileSystemEventHandler):
                def on_created(self, event): wp.dispatch(event)
            self.watcher.schedule(H(), self.watch_dir, recursive=False)
            self.watcher.start()
            
            # === [FIX 2] 新增状态标记，修复“停止”按钮无反应的问题 ===
            self.watching = True 
            
            self.btn_watch.configure(text="⏹ 妲己正在拼命打水印中", fg_color="green", hover_color="#F22A2A")
            self.log(f"开始监听: {self.watch_dir}")

class RenamerFrame(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        # 从配置加载
        self.saved_cfg = config_mgr.data["renamer"]
        
        self.files = []
        self.manual_save_path = None
        
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0)
        self.grid_rowconfigure(0, weight=1)
        self.init_ui()
        self.drop_target_register(DND_FILES)
        self.dnd_bind('<<Drop>>', self.on_drop)

    def on_drop(self, event):
        files = parse_dropped_files(event.data)
        count = 0
        for f in files:
            if f not in self.files:
                self.files.append(f)
                l = ctk.CTkLabel(self.scroll_list, text=os.path.basename(f), anchor="w")
                l.pack(fill="x", padx=5)
                count += 1
        if count > 0:
            self.update_preview()
            self.log(f"拖入添加: {count} 个文件")

    def init_ui(self):
        self.left_panel = ctk.CTkFrame(self, fg_color=("gray95", "#202020"))
        self.left_panel.grid(row=0, column=0, sticky="nsew", padx=(0,2))
        ctk.CTkLabel(self.left_panel, text="文件列表 (可直接拖拽文件到此处)", text_color="gray").pack(pady=5)
        self.scroll_list = ctk.CTkScrollableFrame(self.left_panel)
        self.scroll_list.pack(fill="both", expand=True, padx=5, pady=5)
        
        self.ctrl_panel = ctk.CTkScrollableFrame(self, width=300, corner_radius=0)
        self.ctrl_panel.grid(row=0, column=1, sticky="nsew")
        apply_thin_scrollbar(self.ctrl_panel)
        
        ctk.CTkLabel(self.ctrl_panel, text="批量改名", font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w", padx=15, pady=(20, 10))
        self.btn_add = ctk.CTkButton(self.ctrl_panel, text="📂 添加文件", command=self.add_files)
        self.btn_add.pack(fill="x", padx=15, pady=5)
        self.btn_clear = ctk.CTkButton(self.ctrl_panel, text="🗑 清空列表", fg_color="red", command=self.clear_list)
        self.btn_clear.pack(fill="x", padx=15, pady=5)
        ctk.CTkLabel(self.ctrl_panel, text="命名规则:", font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", padx=15, pady=(15, 5))
        self.e_prefix = ctk.CTkEntry(self.ctrl_panel, placeholder_text="前缀 (如: Travel_)")
        self.e_prefix.pack(fill="x", padx=15, pady=5)
        self.e_prefix.insert(0, self.saved_cfg.get("prefix", "Image_"))
        self.e_prefix.bind("<KeyRelease>", self.update_preview)
        row_num = ctk.CTkFrame(self.ctrl_panel, fg_color="transparent")
        row_num.pack(fill="x", padx=15, pady=5)
        ctk.CTkLabel(row_num, text="起始号:").pack(side="left")
        self.e_start = ctk.CTkEntry(row_num, width=60)
        self.e_start.pack(side="left", padx=5)
        self.e_start.insert(0, str(self.saved_cfg.get("start", 1)))
        ctk.CTkLabel(row_num, text="位数:").pack(side="left", padx=(10,0))
        
        self.e_digit = ctk.CTkComboBox(row_num, values=["1", "2", "3", "4"], width=80)
        self.e_digit.set(str(self.saved_cfg.get("digits", 2)))
        self.e_digit.pack(side="left", padx=5)
        
        ctk.CTkLabel(self.ctrl_panel, text="预览:", font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", padx=15, pady=(15, 5))
        self.lbl_preview = ctk.CTkLabel(self.ctrl_panel, text="Image_01.jpg", text_color="#1F6AA5", font=("Consolas", 14))
        self.lbl_preview.pack(pady=5)
        
        # === 新增：导出路径显示 ===
        self.btn_out_path = ctk.CTkButton(self.ctrl_panel, text="📂 保存至: 未设置 (每次询问)", fg_color="gray", command=self.change_save_path)
        self.btn_out_path.pack(fill="x", padx=15, pady=(25, 5))
        
        ctk.CTkButton(self.ctrl_panel, text="🚀 开始批量改名", height=45, fg_color=["#3B8ED0", "#1F6AA5"], font=("Microsoft YaHei UI", 15, "bold"), command=self.run_rename).pack(fill="x", padx=15, pady=(5,10))
        
        ctk.CTkLabel(self.ctrl_panel, text="运行日志", font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", padx=15, pady=(10, 5))
        self.log_box = ctk.CTkTextbox(self.ctrl_panel, height=150)
        self.log_box.pack(fill="x", padx=15, pady=10)
        self.log_box.configure(state="disabled")

    def add_files(self):
        fs = filedialog.askopenfilenames()
        if fs:
            for f in fs:
                if f not in self.files:
                    self.files.append(f)
                    l = ctk.CTkLabel(self.scroll_list, text=os.path.basename(f), anchor="w")
                    l.pack(fill="x", padx=5)
            self.update_preview()
            
    def clear_list(self):
        self.files = []
        for w in self.scroll_list.winfo_children(): w.destroy()
        
    def update_preview(self, event=None):
        pre = self.e_prefix.get()
        try: digit = int(self.e_digit.get())
        except: digit = 2
        fmt = f"{{:0{digit}d}}"
        self.lbl_preview.configure(text=f"{pre}{fmt.format(1)}.jpg")
        
        # 保存设置
        config_mgr.data["renamer"]["prefix"] = pre
        config_mgr.data["renamer"]["digits"] = digit
        try: config_mgr.data["renamer"]["start"] = int(self.e_start.get())
        except: pass
    
    def log(self, msg):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def change_save_path(self):
        d = filedialog.askdirectory()
        if d:
            self.manual_save_path = d
            self.btn_out_path.configure(text=f"📂 保存至: {os.path.basename(d)}")

    def run_rename(self):
        if not self.files: return
        
        if self.manual_save_path:
            od = self.manual_save_path
        else:
            od = filedialog.askdirectory(title="选择保存改名文件的文件夹")
            if not od: return
            self.manual_save_path = od
            self.btn_out_path.configure(text=f"📂 保存至: {os.path.basename(od)}")

        pre = self.e_prefix.get()
        try: start = int(self.e_start.get())
        except: start = 1
        try: digit = int(self.e_digit.get())
        except: digit = 2
        
        # 更新config
        config_mgr.data["renamer"]["start"] = start
        
        count = 0
        for i, f in enumerate(self.files):
            ext = os.path.splitext(f)[1]
            new_name = f"{pre}{str(start+i).zfill(digit)}{ext}"
            dst = os.path.join(od, new_name)
            try:
                shutil.copy2(f, dst)
                self.log(f"成功: {new_name}")
                count += 1
            except Exception as e:
                self.log(f"失败: {e}")
        self.log_box.see("end")
        messagebox.showinfo("完成", f"已处理 {count} 个文件")

# =========================================================================
#  格式转换工厂
# =========================================================================
class ConverterFrame(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        # 从配置加载
        self.saved_cfg = config_mgr.data.get("converter", {"target_format": "JPG", "quality": 90})
        
        self.files = []
        self.manual_save_path = None
        self.target_format = ctk.StringVar(value=self.saved_cfg.get("target_format", "JPG"))
        
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0)
        self.grid_rowconfigure(0, weight=1)
        
        self.create_widgets()
        self.drop_target_register(DND_FILES)
        self.dnd_bind('<<Drop>>', self.on_drop)

    def on_drop(self, event):
        files = parse_dropped_files(event.data)
        self.add_files_to_list(files)

    def add_files_to_list(self, file_list):
        count = 0
        valid_exts = ('.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tiff', '.ico', '.gif')
        for f in file_list:
            if f not in self.files and f.lower().endswith(valid_exts):
                self.files.append(f)
                l = ctk.CTkLabel(self.scroll_list, text=os.path.basename(f), anchor="w")
                l.pack(fill="x", padx=5)
                count += 1
        if count > 0:
            self.log(f"添加: {count} 个文件")

    def create_widgets(self):
        # --- 左侧：文件列表 ---
        self.left_panel = ctk.CTkFrame(self, fg_color=("gray95", "#202020"))
        self.left_panel.grid(row=0, column=0, sticky="nsew", padx=(0,2))
        
        header = ctk.CTkFrame(self.left_panel, fg_color="transparent")
        header.pack(fill="x", padx=5, pady=5)
        ctk.CTkLabel(header, text="文件列表 (支持拖拽/粘贴)", text_color="gray").pack(side="left")
        
        self.scroll_list = ctk.CTkScrollableFrame(self.left_panel)
        self.scroll_list.pack(fill="both", expand=True, padx=5, pady=5)
        
        # --- 右侧：控制面板 ---
        self.ctrl_panel = ctk.CTkScrollableFrame(self, width=320, corner_radius=0)
        self.ctrl_panel.grid(row=0, column=1, sticky="nsew")
        apply_thin_scrollbar(self.ctrl_panel)
        
        ctk.CTkLabel(self.ctrl_panel, text="格式转换工厂", font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w", padx=15, pady=(20, 10))
        
        # 动作按钮区域
        btn_box = ctk.CTkFrame(self.ctrl_panel, fg_color="transparent")
        btn_box.pack(fill="x", padx=15, pady=5)
        
        self.btn_add = ctk.CTkButton(btn_box, text="📂 添加文件", command=self.add_files)
        self.btn_add.pack(fill="x", pady=5)
        
        self.btn_paste = ctk.CTkButton(btn_box, text="📋 从剪贴板粘贴文件", fg_color="gray", command=self.paste_from_clipboard)
        self.btn_paste.pack(fill="x", pady=5)
        
        self.btn_clear = ctk.CTkButton(btn_box, text="🗑 清空列表", fg_color="#E57373", hover_color="#C62828", command=self.clear_list)
        self.btn_clear.pack(fill="x", pady=5)

        # 格式选择区域
        ctk.CTkLabel(self.ctrl_panel, text="目标格式:", font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", padx=15, pady=(15, 5))
        
        self.seg_fmt = ctk.CTkSegmentedButton(self.ctrl_panel, values=["JPG", "PNG", "WEBP"], variable=self.target_format, command=self.update_options)
        self.seg_fmt.pack(fill="x", padx=15, pady=5)
        
        # [Fix] Changed row_fmt to self.row_fmt to allow referencing in update_options
        self.row_fmt = ctk.CTkFrame(self.ctrl_panel, fg_color="transparent")
        self.row_fmt.pack(fill="x", padx=15, pady=5)
        ctk.CTkLabel(self.row_fmt, text="其他格式:").pack(side="left")
        self.combo_fmt = ctk.CTkComboBox(self.row_fmt, values=["BMP", "ICO", "TIFF"], width=100, command=self.on_combo_change)
        self.combo_fmt.pack(side="left", padx=10)
        
        # 质量控制区域 (仅 JPG/WEBP 显示)
        self.quality_frame = ctk.CTkFrame(self.ctrl_panel, fg_color="transparent")
        self.quality_frame.pack(fill="x", padx=15, pady=10)
        ctk.CTkLabel(self.quality_frame, text="压缩质量 (1-100):").pack(anchor="w")
        self.slider_quality = ctk.CTkSlider(self.quality_frame, from_=1, to=100, number_of_steps=99)
        self.slider_quality.set(self.saved_cfg.get("quality", 90))
        self.slider_quality.pack(fill="x", pady=5)
        
        # 导出路径
        self.btn_out_path = ctk.CTkButton(self.ctrl_panel, text="📂 保存至: 源文件夹 (自动创建子目录)", fg_color="gray", command=self.change_save_path)
        self.btn_out_path.pack(fill="x", padx=15, pady=(25, 5))
        
        ctk.CTkButton(self.ctrl_panel, text="🚀 开始转换", height=45, fg_color=["#3B8ED0", "#1F6AA5"], font=("Microsoft YaHei UI", 15, "bold"), command=self.run_convert).pack(fill="x", padx=15, pady=(5,10))
        
        ctk.CTkLabel(self.ctrl_panel, text="运行日志", font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", padx=15, pady=(10, 5))
        self.log_box = ctk.CTkTextbox(self.ctrl_panel, height=150)
        self.log_box.pack(fill="x", padx=15, pady=10)
        self.log_box.configure(state="disabled")
        
        self.update_options(self.target_format.get())

    def add_files(self):
        fs = filedialog.askopenfilenames(filetypes=[("Image", "*.jpg *.png *.webp *.jpeg *.bmp *.tiff *.ico")])
        if fs: self.add_files_to_list(fs)
        
    def paste_from_clipboard(self):
        try:
            # 尝试从剪贴板获取文件列表
            data = self.master.clipboard_get()
            # Windows 复制文件时，剪贴板里可能是路径
            # PIL ImageGrab 更好用
            img = ImageGrab.grabclipboard()
            if isinstance(img, list):
                # 如果是文件路径列表
                self.add_files_to_list(img)
            elif isinstance(img, Image.Image):
                messagebox.showinfo("提示", "检测到剪贴板是图片数据而非文件。\n请先将图片保存为文件，或直接拖拽文件进来。")
            else:
                # 尝试解析文本路径
                if data:
                    paths = [p.strip() for p in data.split('\n') if os.path.exists(p.strip())]
                    if paths: self.add_files_to_list(paths)
                    else: self.log("剪贴板中没有有效的文件路径")
        except Exception as e:
            self.log(f"粘贴失败或剪贴板为空: {e}")

    def clear_list(self):
        self.files = []
        for w in self.scroll_list.winfo_children(): w.destroy()
        self.log("列表已清空")

    def on_combo_change(self, value):
        self.target_format.set(value)
        self.seg_fmt.set("") # 清除分段按钮选中
        self.update_options(value)

    def update_options(self, value):
        if value in ["JPG", "WEBP"]:
            # [Fix] Corrected the 'after' argument to point to a sibling widget (self.row_fmt), not the parent
            self.quality_frame.pack(fill="x", padx=15, pady=10, after=self.row_fmt)
        else:
            self.quality_frame.pack_forget()
            
        # 保存配置
        config_mgr.data["converter"]["target_format"] = value

    def change_save_path(self):
        d = filedialog.askdirectory()
        if d:
            self.manual_save_path = d
            self.btn_out_path.configure(text=f"📂 保存至: {os.path.basename(d)}")
        else:
            self.manual_save_path = None
            self.btn_out_path.configure(text="📂 保存至: 源文件夹 (自动创建子目录)")

    def log(self, msg):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def run_convert(self):
        if not self.files: return messagebox.showwarning("提示", "请先添加图片文件")
        
        target_fmt = self.target_format.get()
        quality = int(self.slider_quality.get())
        
        # 更新配置
        config_mgr.data["converter"]["quality"] = quality
        
        count = 0
        self.log(f"开始转换... 目标格式: {target_fmt}")
        
        for f in self.files:
            try:
                # 确定输出目录
                if self.manual_save_path:
                    out_dir = self.manual_save_path
                else:
                    out_dir = os.path.join(os.path.dirname(f), "已转换")
                
                if not os.path.exists(out_dir): os.makedirs(out_dir)
                
                fname = os.path.splitext(os.path.basename(f))[0]
                out_path = os.path.join(out_dir, f"{fname}.{target_fmt.lower()}")
                
                with Image.open(f) as img:
                    # 格式特定处理
                    if target_fmt == "JPG":
                        img = img.convert("RGB") # 移除透明度，否则报错
                    elif target_fmt == "ICO":
                        # ICO 通常需要特定尺寸，这里做个限制防止过大
                        if img.width > 256 or img.height > 256:
                             img.thumbnail((256, 256), Image.Resampling.LANCZOS)
                    
                    # 保存
                    if target_fmt in ["JPG", "WEBP"]:
                        img.save(out_path, quality=quality)
                    else:
                        img.save(out_path)
                        
                self.log(f"✅ 成功: {os.path.basename(out_path)}")
                count += 1
            except Exception as e:
                self.log(f"❌ 失败 ({os.path.basename(f)}): {e}")
                
        messagebox.showinfo("完成", f"已完成 {count} 个文件的转换")

# =========================================================================
#  新增：图片液压机 (Compressor)
# =========================================================================
class CompressorFrame(ctk.CTkFrame):
    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.saved_cfg = config_mgr.data.get("compressor", {"quality": 75})
        self.files = []
        self.manual_save_path = None
        
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0)
        self.grid_rowconfigure(0, weight=1)
        
        self.create_widgets()
        self.drop_target_register(DND_FILES)
        self.dnd_bind('<<Drop>>', self.on_drop)

    def on_drop(self, event):
        files = parse_dropped_files(event.data)
        self.add_files_to_list(files)

    def add_files_to_list(self, file_list):
        count = 0
        valid_exts = ('.jpg', '.jpeg', '.png', '.webp', '.bmp')
        for f in file_list:
            if f not in self.files and f.lower().endswith(valid_exts):
                self.files.append(f)
                l = ctk.CTkLabel(self.scroll_list, text=os.path.basename(f), anchor="w")
                l.pack(fill="x", padx=5)
                count += 1
        if count > 0:
            self.log(f"添加: {count} 个文件")

    def paste_from_clipboard(self):
        try:
            # 尝试从剪贴板获取文件
            img = ImageGrab.grabclipboard()
            if isinstance(img, list):
                # 复制的是文件列表
                self.add_files_to_list(img)
            elif isinstance(img, Image.Image):
                # 复制的是纯图片数据 (例如网页右键复制图片)
                messagebox.showinfo("提示", "检测到剪贴板是图片数据而非文件。\n为了安全起见，请先将图片保存为文件，或直接拖拽文件进来。")
            else:
                # 尝试获取文本路径
                try:
                    data = self.master.clipboard_get()
                    if data:
                        paths = [p.strip() for p in data.split('\n') if os.path.exists(p.strip())]
                        if paths: self.add_files_to_list(paths)
                        else: self.log("剪贴板中没有有效的文件路径")
                except:
                    self.log("剪贴板为空或不支持的格式")
        except Exception as e:
            self.log(f"粘贴失败: {e}")

    def create_widgets(self):
        # --- Left Panel ---
        self.left_panel = ctk.CTkFrame(self, fg_color=("gray95", "#202020"))
        self.left_panel.grid(row=0, column=0, sticky="nsew", padx=(0,2))
        
        ctk.CTkLabel(self.left_panel, text="待压缩图片列表 (支持拖拽)", text_color="gray").pack(pady=5)
        self.scroll_list = ctk.CTkScrollableFrame(self.left_panel)
        self.scroll_list.pack(fill="both", expand=True, padx=5, pady=5)
        
        # --- Right Panel ---
        self.ctrl_panel = ctk.CTkScrollableFrame(self, width=320, corner_radius=0)
        self.ctrl_panel.grid(row=0, column=1, sticky="nsew")
        apply_thin_scrollbar(self.ctrl_panel)
        
        ctk.CTkLabel(self.ctrl_panel, text="图片液压机", font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w", padx=15, pady=(20, 10))
        
        # Buttons
        self.btn_add = ctk.CTkButton(self.ctrl_panel, text="📂 添加图片", command=self.add_files)
        self.btn_add.pack(fill="x", padx=15, pady=5)
        
        self.btn_paste = ctk.CTkButton(self.ctrl_panel, text="📋 从剪贴板粘贴图片", fg_color="gray", command=self.paste_from_clipboard)
        self.btn_paste.pack(fill="x", padx=15, pady=5)

        self.btn_clear = ctk.CTkButton(self.ctrl_panel, text="🗑 清空列表", fg_color="#E57373", hover_color="#C62828", command=self.clear_list)
        self.btn_clear.pack(fill="x", padx=15, pady=5)
        
        # Slider
        card_qual = ctk.CTkFrame(self.ctrl_panel)
        card_qual.pack(fill="x", padx=15, pady=20)
        
        header = ctk.CTkFrame(card_qual, fg_color="transparent")
        header.pack(fill="x", padx=10, pady=5)
        ctk.CTkLabel(header, text="压缩强度 (1-100%)").pack(side="left")
        self.lbl_val = ctk.CTkLabel(header, text="75%", font=("Arial", 12, "bold"), text_color="#1F6AA5")
        self.lbl_val.pack(side="right")
        
        self.slider = ctk.CTkSlider(card_qual, from_=1, to=100, number_of_steps=99, command=self.update_slider_label)
        self.slider.set(self.saved_cfg.get("quality", 75))
        self.slider.pack(fill="x", padx=10, pady=(0,10))
        self.update_slider_label(self.slider.get())
        
        ctk.CTkLabel(card_qual, text="小数字 = 更模糊 / 体积更小\n大数字 = 更清晰 / 体积更大", font=("Arial", 10), text_color="gray").pack(pady=(0,10))
        
        # Shortcut
        self.btn_lossless = ctk.CTkButton(self.ctrl_panel, text="✨ 一键无损压缩 (推荐)", fg_color="#E67E22", hover_color="#D35400", command=self.set_lossless)
        self.btn_lossless.pack(fill="x", padx=15, pady=5)
        
        # Output Path
        self.btn_out_path = ctk.CTkButton(self.ctrl_panel, text="📂 保存至: 源文件夹/液压处理", fg_color="gray", command=self.change_save_path)
        self.btn_out_path.pack(fill="x", padx=15, pady=(20, 5))
        
        self.btn_run = ctk.CTkButton(self.ctrl_panel, text="🔨 启动液压机", height=45, fg_color=["#3B8ED0", "#1F6AA5"], font=("Microsoft YaHei UI", 15, "bold"), command=self.run_compress)
        self.btn_run.pack(fill="x", padx=15, pady=(5,10))
        
        ctk.CTkLabel(self.ctrl_panel, text="运行日志", font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", padx=15, pady=(10, 5))
        self.log_box = ctk.CTkTextbox(self.ctrl_panel, height=150)
        self.log_box.pack(fill="x", padx=15, pady=10)
        self.log_box.configure(state="disabled")

    def update_slider_label(self, val):
        self.lbl_val.configure(text=f"{int(val)}%")
        config_mgr.data["compressor"]["quality"] = int(val)

    def set_lossless(self):
        self.slider.set(95)
        self.update_slider_label(95)
        self.log("已设定为无损压缩模式 (95% + 算法优化)")

    def add_files(self):
        fs = filedialog.askopenfilenames(filetypes=[("Img", "*.jpg *.png *.webp *.jpeg *.bmp")])
        if fs: self.add_files_to_list(fs)

    def clear_list(self):
        self.files = []
        for w in self.scroll_list.winfo_children(): w.destroy()

    def change_save_path(self):
        d = filedialog.askdirectory()
        if d:
            self.manual_save_path = d
            self.btn_out_path.configure(text=f"📂 保存至: {os.path.basename(d)}")
        else:
            self.manual_save_path = None
            self.btn_out_path.configure(text="📂 保存至: 源文件夹/液压处理")

    def log(self, msg):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def run_compress(self):
        if not self.files: return messagebox.showwarning("提示", "请先添加图片")
        
        quality = int(self.slider.get())
        count = 0
        self.log(f"开始压缩... 目标质量: {quality}%")
        
        for f in self.files:
            try:
                if self.manual_save_path:
                    out_dir = self.manual_save_path
                else:
                    out_dir = os.path.join(os.path.dirname(f), "液压处理")
                
                if not os.path.exists(out_dir): os.makedirs(out_dir)
                
                fname = os.path.basename(f)
                out_path = os.path.join(out_dir, fname)
                
                with Image.open(f) as img:
                    fmt = img.format
                    # Handle alpha channel for JPG
                    if fmt == "JPEG" and img.mode in ("RGBA", "P"):
                        img = img.convert("RGB")
                    
                    if fmt in ["JPEG", "JPG", "WEBP"]:
                        img.save(out_path, quality=quality, optimize=True)
                    elif fmt == "PNG":
                        # PNG ignores quality param in save, use optimize
                        # PngImagePlugin uses compress_level (0-9)
                        img.save(out_path, optimize=True, compress_level=9)
                    else:
                        img.save(out_path)
                        
                # Calc reduction
                orig_size = os.path.getsize(f)
                new_size = os.path.getsize(out_path)
                ratio = (1 - new_size/orig_size) * 100
                self.log(f"✅ {fname}: 减小 {ratio:.1f}%")
                count += 1
            except Exception as e:
                self.log(f"❌ {os.path.basename(f)} 失败: {e}")
                
        messagebox.showinfo("完成", f"液压处理完成: {count} 张图片")


class DajiToolbox(ctk.CTk, TkinterDnD.DnDWrapper):
    def __init__(self):
        super().__init__()
        self.TkdndVersion = TkinterDnD._require(self)
        self.title(APP_NAME)
        
        # === 新增：设置窗口图标 (加载外部 logo.ico 文件) ===
        try:
            # resource_path 确保打包后能找到临时目录中的图片
            # "logo.ico" 是你放在代码同级目录下的图标文件名
            icon_file = resource_path("logo.ico")
            if os.path.exists(icon_file):
                self.iconbitmap(icon_file)
        except Exception as e:
            print(f"Set Icon Error: {e}")
            
        self.geometry("1200x800")
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        
        # === 核心配置：请在这里替换为你自己的 Base64 字符串 ===
        # 使用在线工具 (如 base64-image.de) 将你的收款码图片转为 base64
        # 下面是一个占位的灰色图片 (20x20像素)
        self.donate_qr_code_b64 = """
                data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAaEAAAGfCAYAAAD22G0fAAAAAXNSR0IArs4c6QAAAARnQU1BAACxjwv8YQUAAAAJcEhZcwAAEnQAABJ0Ad5mH3gAAABhaVRYdFNuaXBNZXRhZGF0YQAAAAAAeyJjbGlwUG9pbnRzIjpbeyJ4IjowLCJ5IjowfSx7IngiOjQxOCwieSI6MH0seyJ4Ijo0MTgsInkiOjQxNX0seyJ4IjowLCJ5Ijo0MTV9XX0Y38rpAAD/OElEQVR4Xuy9eZxlRX02/tQ552699+zDLOyLgIACCqIYREBChLhEY8yLBoUYUUnUvL6+8ReirzEmgpq8EF+VlxhwjwSRqHFQMIgMIiCL8LIPs/R0z9rbXc9S9fvjnufwvdXndt/uvt0zA/eZT02fU6fqW9+t1nOqrjLGGHTQQQcddNDBPoBjR3TQQQcddNDBYqHTCXXQQQcddLDP0OmEOuiggw462GfodEIddNBBBx3sM3Q6oQ466KCDDvYZOp1QBx100EEH+wxt74Q6X3y3jnbqKo1WWtx8QHo2XWPMlLh2YKHozgeLxc9ilTMdpuOhmS+0CwtFtxXsy7IPFLRTR/PuhLTWDfdsOLTWU569mCB1ICus1It8JvPNtoIzj50vjf58QHppgc/kX5kes+xUSCOKojnTaBdsORlnP7fjZwtJ29bnvoCUuZm8tj6k3WcL0jXGIIqihFa7YPOcpmPJg8w3V5leaJB6krafj27m3Qk5TiMJMqSUglJqXswdyFBKIQxDaK2hlEoqFPXFa9v5qTcdN8CtQCkFAAiCICkviqKEfrts4LoujDFwHCcpg3K4rgvEvDDOcRyEYZh0JK2CenBdNykTAKIoSsqdDb35gjZRSqX6O9PwL3U+GxtKsCxew6r0iwFpZ8osdc44aR/G0+9akZ31gjTov57nwff9RP52gGXIesHy6Le8D8MwyReGYYNNXoyg79G/qQ+2AfPRjZrviQmyctC4L3aDQVQm2Yik6YTxrBSz1R3N1yxPs3LnAzYaKm6g0ngPwxCe5zXkaVU2ppWysWNrJf9CgDKy8a3Vashmswk/bHDZGTNPFEUNemgVs9HXQiIMwwa9T8cPG252RHOBMQZhGMJ13TnTmAkcoElZbPva+rfvX4yQvmDrDjP4xnRoSydkM2THzZW5FwKoXv61K5Y9ilBiROa6bku6I21Jyy6nnZBOZ1+zItMH7A6lVb6auWUr+mgn0uSjjCoeMdsVk/bzPA8mHqTJzmk6aLHEQZqMa5VGOyF9S8otbSttQr5nA8pHHUua7JBmS7MZJH8m7vDsTtOWh3Fp8r6YIHUn2xr6xVzR9k6IcRL28xcD5HIYrMaXOuNf27ml488EmZbXdIq0Ed98kMavbX+Zhtd8Phs+2DDZeiNmQ2u+sBtgKbPkybaFtH8r/EpaEHnSdLHQME2W1XlN+WzwGWbJL8ti3jAMkclkGnTdDmixJM576aNaDCBsu7+YIX1TicHXbGzcDPOmoESDYzc80rgvNtBx7YaExpSVVaaRz+W6dDOw4sp1a8a1u/LQ+YgwDBM5oihKlqSCIEjKlfxQBzOB6STvzL/YPiVtZF/b7zyknEq812uVX7vuhPH7NKnDxQLLlGv+lNnmJwiCxAelDK3ySx+S9pXvGNsByTuvWZ5MA+u1AutQtMjvIfc32Hagfdqhk3nNhMbHx/GDH/wAIyMjgKhEEvb9iwV0+LPOOgsnnXRSw3sBYy1ZGWPw+OOP4/7778fw8HDyfK7Q8fLPG9/4Rhx66KHIZDJ2klnDGAPf93HdddehWCxOeSFNWZRSyGazuPDCC7F27dpEbqZtxR+MaJCoh927d+Pf/u3fUCqV7OQLCqUUDjvsMLz61a/GsmXLoKwlRjnbmZiYwAMPPID7778/yT+XjlM2jocffjhOOeUUrF27dtZ05ouhoSHcfvvtSf0mKLuKG+dcLoe3ve1tWLFiRcOSYSu82s0P9Ts2Noavf/3rqFar86oLRG9vL0499VSccMIJCT3qU/IQRRE2b96Mf//3f29oz9gZGTF4fDHCGIPly5fjd37nd7B+/fpEP63Yuhnm1Qlt27YNH/zgB/Hggw9OaZCI+TB3IIM6+Ku/+itcfPHFyOVyDc9kxTLG4NZbb8V1112Hhx9+GMaYpJFvxTysTJxJMf+Xv/xlvOY1r0GhULCzzBrGGJRKJZx11lnYsWNH6kiIlXRgYAD//M//jFNOOQXZbHaKDK34BGdDbOSfeOIJvPvd757SIC40lFI4++yz8Zd/+Zc4+uijgVhmaUPKNzQ0hK997Wv4v//3/zbEzxbM5zgOXv/61+O9730vXvGKV7Skt3biwQcfxJVXXomHHnoIEPYlVNwJ9ff348Ybb8Txxx8/5b3VTDxLetRpGIbYtm0bLrzwQoyNjbWlE1q9ejUuu+wyXHzxxQ30pExKKQRBgI0bN+K//bf/BsQ24KAOwi9fzDj66KPxiU98Aq9+9auBFmw8I8w88Nxzz5kzzjjDOI5jAKQGpZRRSk2Jf6EFKaNSyjiOY5RS5qqrrjKVSqVBb1rrhr/GGPPNb37TnHzyyYm+ZqO3tLRKKXPLLbeYcrksSp47tNZmYmLCrF69uimPvF6yZIn5+c9/bmq1WpJXhpkg02mtTRRF5qGHHjJr1qyZIudihDe+8Y3mt7/9bQN/URRN4XfTpk3mwx/+cFO9tBpk+gsvvNDceeedSVmLifvuu8+ccsopDXwxyDrf19dnfvWrXxnf9xvsNhtbU6daaxMEgXn66afNwMDArHXXLKxdu9Z84QtfmFKufe37vtmwYYOBqMc2LRnma+v9Ldjy2EEpZU444QTz05/+NNHlfDGvIQZHhPboyE6TFs+4tPgDBZJ3+9rWi0SazIxjnmZ5Z4t20YGwtx1HO6Y9wzztzDLl8qUN0k971i7YdrHtZd9LXm29zAYLKdNMSNO3tLW85gyB6WdrDzu9nNW3G5J3pJQty3wxzXxsPTBO/l0IzKsTahV0JlvIhXKyxUIz3lmBZoLUBXVj66cVSD0upNPQhixPlplWns1/s3Q2ZDrH2ghr05SQz1opZ7ZI82Pb1rLcVuXdH5Emk4k7m7QOYjq7TAepI6kr25/3F9j8tEsP+xK2fRdb94vSCRG2weiAiyXsQoKN01whdTFbOjIP9Utd2zqfD1gOX9I2A9PIdyZz4cOuFIyzYdOeiw6bQYmTG9LoMp48SH7TeD1QMJ3OpR6kraVubJs0g21fSTdN3/sazWSy5T+QsK/1vOCdEJ10OkGbGXZ/h5SJMsjGdzaQjVg7lgCaNQ7zgf2ZKunaPKfZczr726AupO8gbvCpW3ktYdtjvjDx57yhOIIJsczUB2VL03e7+Fhs2HJIyA6Gf+20rdrbTifpsj7sj7D5tjvTAwnNeJd2XkhMrcVtBh1JCkQDLpaQCwnpjJR1NhUnTX7StL80agZbtxAdQxr9+UDa0wb5tjuHVhojCbuCs0zu7UDcIbJTsNO206+ceDnQbpR5b8SeE9u/7TwHEtL0aOuado7iPWJaa4RhmHTYraCZfuY6mFtMSH3Q3pwZH2iwbSvjW22H5ooFt3JaZaRjy5HjgRSkHCblXddsYTeYpCs3BrYSJC2Vovc02A3NdMhkMg30bLlJy+4AZXyrZaWlZ1nNlsaYxnXdls9rs3VoB9qBdoaYEVK/doNpy5vG5/4OqQMblJsdjeM48Dwv+cvrVkBdMbA86m9/he2fvCfPth/tz4FIi6f/LyRa85R5oFlllEazHXF/D3IpiHGEbdhWocS7h7QGrZVg0zMpDblEs7zNwFONpew2ZMfH57ZjtwKWI/Nw0+10MkEslbUCW4d2IORSpN3A2rq25T8QIWWR8kg5CSX2qM0W0jds/7Drwv4Ayaey3heiBX/aH4PNu5R1MTCvzarPPfcc3vnOd2Ljxo0J86yQEo7jIJvNoqurK2lI7DQHEqIoQrVaRaVSmXG0dtVVV+Hyyy9HPp+3HwFid/wtt9yCa6+9Fg8//HBSqTFLRzDiU2atNb75zW/i1a9+NfL5fCod6Yjj4+PTHhNkjEGxWMQZZ5yB3bt3N210HMdBX18fbrjhBrzsZS9L7J1WfjOwYkg9bNq0CZdddhl27tyZWi4RhiEqlQqq1eq06QAgl8uhv7/fjp6Cs846Cx/60Idw2GGHwRWbiKlrYtu2bbjuuuvwf/7P/0nimG4mP5GQdeiiiy7CRz7yEbzmNa+xk80Z4+Pj8H3fjm6A1hq//e1v8Zd/+Zd48MEHk3hbp47jIJfL4eabb8ZLX/pSeJ6X8E87zgYmHnkPDQ3hnHPOwcTExJQy54K1a9fiIx/5CP78z/98Wr6CIMAdd9yB8847L7UtI5RSyGQy6O7uRjabBURdPtDgxBuEy+UyarVasgIDURfl9Utf+lJ8/vOfx9lnn21RmhsWpRPKZrM49thj8bGPfSwZ3dppDiQMDw/jBz/4AX7+8583NC5pcs3UCdGwW7ZswRNPPIHR0dFUOq2CDqO1xplnnjnlKBUbxhg888wzuOaaazA8PGw/TsDG4Uc/+hFqtVpT/lg5X/3qV2PJkiUNlbLVCmo7PgAMDg7i9NNPR3d3d2rZ1Nnw8DD+4z/+Az/72c9S0xGe5+EVr3gFrrjiCvvRFAwPD+PRRx/F2NjYlAopr8vlMp566ik88cQTDflna0+ZfiE6oWuuuQb33nsvarWa/Sgp2xiDsbEx3H///di7d29TGVQ8Yz3rrLMwMDAwZRY/G5vL60qlgg0bNiAIgoZ0c8VCdEIveclLcPHFF+PQQw+dNu3+DhWffPH1r38dd9xxB8rlsp0kkU8tQCcEe/fqbLBp0ybzqle9asYdw/l83px77rnGNDkt4EDDk08+aS655JIpstr3AFJPTNjXsHW/ceNGc9RRR03hfX8Kxx9/vNmyZUtTGYgnn3zSXHbZZQ15aRdpn2w2a/7wD//Qzp6KH/zgB+a4446bwtNCBcnnRRdd1PYTEy6++GLT3d09pVy77BdSkCcmNPMdE5+Y8JOf/MRgBl0opcxrX/ta88ADD9gkDkhorc2f//mfm76+vlTZZR3ar05M6KCDDjrooIP5oNMJddBBBx10sM/Q6YQ66KCDDjrYZ+h0Qh100EEHHewzdDqhDjrooIMO9hk6nVAHHXTQQQf7DJ1OqIMOOuigg32G/aoTMuK8KG78WogNYHKDabOd/4sBbgokdJPDT2WcnUfGpx1VQ5rMZ+s3DMMpx+PMB9PRUSlHnshnsI79eaGBGzvT5HPE+XM8Bsbe+Jlm9/0Bkve5oJlfpOnpQIJdn1lHF9uOtu8sdvkzYVFOTMjn8zjzzDPxk5/8BCbedcu/BBvKxx57DH//93+PSqXScCyKTXMuoLPzWAqtNTzPS+5/93d/F29729vQ29trZ23AU089hc9+9rP4l3/5lwa+0mSf6cQExLJv3LgRt956K5555pkpMnuehyAIoJRCf38/PvShD+Gkk05K8ts8MO7v//7v8cgjj6BarTbEO46TdFiZTAY7duzA/fffj2KxmNCZK5RS6OnpwYc//GEcf/zxgOhYWCb1pLXG5s2b8bGPfQyIeUvTIQAcf/zx+NGPfoR169Y1pLXx1FNP4aqrrsJXvvKVJE76G2lns1m8+c1vxre+9S2ROx233norPv7xj+PRRx+1H7UMm4e3vvWt+IM/+ANkMpnE9+nv/GuMwapVq3DUUUdh+fLlNsk5413vehduuukmlEol+1FT/afBdV1orZHP53HllVcmxxqx4ZX1txmoE0ccbWSMwa5du/DRj340dff+XLAQJyaceeaZ+MIXvoCXvexlDc+0dXzPnj178LOf/Qw33XQToihqkHWhkc/n8c53vhPnn38+YNlXxW0g7fThD38Y119/PSYmJqbILv33BXligvx9+Z///OdmyZIlJpfLmVwuZ7LZbNtDJpNJQqFQSOKvuOIKs3v37oSvZmjniQlBEJgoisx3v/tdc/rpp0+RO5PJmHw+n8StX78+2a0s9aa1NmEYmiiKEtq///u/b/r7+002mzX5fN7k83mTzWYTWqSdzWZTeZ9LUEqZwcFBs2HDBjM5OWmq1aqp1WqmVquZarWahEqlYiYnJ81dd92VlO26blM+DvQTE5RSxnEco5QySinz8Y9/3IyPjzfoROqK+vJ9P7GxtO180K4TE2iv3t5e84tf/MKUSqVUOaYLaf5RLBbNY489Zvr7+6eUOdewmCcm2G3btm3bzKc+9amGOrhYob+/31xzzTUmiqLEj8IwTNoM2R6/6E9MYE8bRRF834fv+6jVagiCIAmMn28wxiAIAkRRlJQRhmHqctZCQ4mDOsMwbJCTPFarVfi+n/xWSxAEyeiZ4CiFcSaeaZCOH+uTtEmrWq0iDMOmI765QMXnx2UyGeRyueSnFbLZLHK5XMPfVn9y4UCHEUsitBV1IvXhum6iO+pnPktdCwn6IH++gTxns9kGGaYLuVwOmUwmyc88nEk0m7Hsz2A9lPY2xiAMw4Y6uFiBBxNLnbJ92B+wX3i37WiyMeVSTrsaSVmWdJR9AU7NYb2bYsckwYZLOo5MZ+JlHAlZCZhP6pJ5F8IZeZqyLBeCJ17L9X+7Y32xgbahrWHZzrbvvgZ9Tto4zc6tgHkp+wvlvRCEDWnbxQT1mtZOyPq5L7HfeLXthIzDAjSSfBlvN4iL7SB2oyudVcqu4jVz8igrprzmM6azGwdZlixH0mgHWD5Dmq75V9p6ofjZn0BbSjtJyGe2Huy0+wNc103qE4TtZ2NHmZayZzKZKX5zoMC2HWXS8fuXVvXSDthlyTopbbYvsV90QlQMDUWl8N5W5Hyh4pEW6bK8dpczE/hSGvHMQTqo3RlxxCl5ls4j0zE/HY1xzCcrPePaBdLikqHNryxbxSMxZc18X8igvAxpjZLU2f7UWKRBxx/3EHOtSzKfjpemZ0tjfwHbLcKJf3HWrneLAfJCftiOQLQRi82Tjf2qxqu4c5A/rtbuikdHCIKgoVG0G/XFhux07AbKiX90CmIk7Vq/7iobfBW/b6BcskJI+qTVbqiUX4llY0odS54l7/viE9bFhBJfCXLJSeqE8lMf8rmM2x9AWdjIkW/aea52pN8cqL7Ahp46kDrSWid2XwwYMZChLl3xw4yybdhXaH8LNAfIxpJOzJkKn7ULdASIURcW4N1Tq2BnQcgKTZ3Id0fkUfLpxCMtpuczOr+JGwc6v6wQ7XZC8icbEdpRykYb25+s0g420uLSsNj2axXSftSFbGSpl0wmM8UPae9WddAK2kGLPNH3IHyLcrYKuw6QHvV2oIEdqfRndgRykL3QoB1UyjIwB4r7GvuNhdkoQYywqKyFAmmblHcxiw0pZxoPrNgzVXBZaeWSI0RjJjGXSi7p2hWN0PGIj07PtHJ2xArJhpdxiMuQ/iDpME42VszLRpBlsOJLTKe/NMiOU+a19cBnMg6CVymDXT71SNh05mKnVmCXQzn5TKax7Ux5TPyFnC0Tn3HmZ399Kv3R7rxoQ93mQdJioZmebB0tNKSOJRbKn+aC/YeTDhYUrOSIKwWdMBK/Jz8TmI+NsmyEZefSKpRSyOfzDT/hLBsuNkqMZ+ME692KrOBssKdrvIy1Tj4dmMa1NmHasPmRvMqGliCf+6JhSlvyg8UnZZQzM+ahnjlQoL34Hof6lWnlbB6iQ9LxjJBpSYP6s3ns4IWH/ebEBOKOO+7AhRde2Jbd+7PF5Zdfjk9+8pNYunSp/agB7T4xAQC+/e1v4+qrr8Z9993XoB+b/sqVK3HDDTfgnHPOacgP0VDz70UXXYTbbrsNlUqlIR0bBjaKy5cvx1lnnYWBgYGGdDaiKMK3v/3txDZsRO1yBwYGcPPNN+P0009HNpudYmvKpLXG9u3b8bd/+7dJY8Z4iBG4iZepjj766IbZERs32eDt3LkTP/3pT3HXXXdN0aG89jwPJ510Et773vfGXKXDGIPJyUns2LEDpVKpgUdHLG9orVEsFvHoo4/i4YcfTvK78ZKjbcvzzz8fb3jDG5DNZuHE7/wo20LjX/7lX/Dggw/C931A2MPmcdmyZTjzzDOxcuXKhnjZmSqlkM1m8ed//udYu3YtMplMwzPCxH6yYcMGDA0NoVarJTaTadkJ7d69G5/97GdRq9WSvPPBYp6YYGNoaAhf/epX8alPfQoQ+l4MFAoFfPazn8WHPvShJC5NfrMPT0zodEICL/ROiPqW6TzPw3HHHYfPfe5zOOywwyyKz8PEjfF5552H3bt3J3xxdCtHra12QgBQrVYxNDSUdGgSvFdKYdOmTbjyyiuxc+fOhnhJ28TLe6Ojo5iYmGhIw2umcxwHPT09Mx6Ho5TCaaedhne961045JBDkrzGmhkYY7Bt2zbccMMN+NrXvpY8Y0cpywaAwcFBDA4OJp3vYo76d+/ejWKx2LA8RjnIhzEGJ5xwAj7xiU/gpJNOapjFSDsx30EHHYRcLtcwIOBz6iKKIvzZn/0ZNm7ciGq12kBH0tJaIwgCDA0NQVtLrnNFpxPafzsh2EcozAbtOrZH4vbbbzc9PT1TaCxGuPzyyxf92B7iW9/6ljnllFMa6Nj0lFJm1apVZsOGDXZ2Y1J0e+GFF5pCodCQ33GcBpqO45jTTz/dPPHEExa1RmitzeTkpFm9enVy/IykqeLjaFR8bM/Pf/5zU6vVkrw2GCePorGPEQnDMDli5OGHHzarV69OeE4rN01f9r2Ms583CxdeeKH57W9/m3rUibx+9tlnzV/8xV806DmNP3lv22MxQprcNs8AzKmnnmruueceo+Mjg+QRUfYRQtKeMp1MH4ahueCCC0x3d3eD/LYdp+NzrmExj+2xsW3bNnPllVdOkW8xQqFQMP/4j//YwE+a/LpzbE8HiwV7FCaXVkw8um8WOCqVNOz7mcD0HImlzYAIjoztYKeX90xjx/Pe5n02IN865X0S77lEZ/PAvJL/NFkWA83KtHmVS45SJpleygLrfZnMk/ZRAv/OxyYdHPjodEIvAsjGTzYaslGZTeVPozPbBpU07CUhPlPWJ6V8LsuSDT/z2bTaDfKWhmZ65rUtB9Pxfl9C8mzH2fLY/EN8QShpyHvaSOrDTiPTEftaLx0sPDqd0IsQrNiygsvGZbpgNxJIaUimg6Ql89k07GsZSId/pRxEWtx8IMuRemCc7DCN9XUY0/FZGm9pcQsFuyx5z2vJp21baQOZ17aNvOeM187D51JPMs5O28ELD1NblA4WDc0qN2ZoKGYDlTIyt+NsPpohCIKksSVIp1kDQ8gyeO2IvWEyv6Qh7+VLfD6DGIXLfHK5rBlPrcJYP0Zm649xTCvv2RlJ2C/bpUytwKY3H6TRYpxcirPtLtMZsStf2stOY3fMtg6oZ+az9WTD5me2mI52B4uHTie0D8FK0KwRYuWGqHBKNIDTVSL5zG4Y5gqOZiVI2w52GqQ0Ovxrp7fTsfGyOx8+l7QYVJNZm+TPLrcZ7M6S/Nj0pfzkide23tLiWoEso1n5M0HyJu95Tf3ZaSTPlCuNBiH5U/GJHcwjAyyeSDstnmCnx2etIE1uW64OFh9Ta2kHiwZZiewGhfF8xlHhdJ/0yspLpDUczDvbDabkQ5YrK7GONx7KtIxnWjs/09kNVhrfPOTVxA2ZnYYNk2xk+EyC8fw7E7gRk/SbjdCjKGoYyaelmS9sfRO2PqaD1I9Mr4S/GWPg+35DGUzP/Ep80q3FkUwE45iWNOSskmDZlEPumZK6lGn46XsrPmyXR0i6HewbzGy9DhYMOt4PYVcQWTG4GZP3vu9DiXPg7IaE6ZiGFdRubBBvDJSNzExg42E3LLz2PA9u/AN2ki4bJ9mAMN4un2nZmbCR4bWOT20mDXYMMq8M84WkT/CIGupDliX5lfftgtQf7zGHEyuk7XhPOWiTtL1tTNfMBzBNwx7FpybIdLb+SNf+YEXmkTqlHWYC80jepV91sO/Qutd20HawQUXciPT29qK/vx99fX3o6+tDT08PlixZgoGBgYb4KIowMTGBsbExjI+PY3x8HJOTk1P+Tk5OTqn4sDquTCYDYwyKxWJCq1mwNxga66cGdDwTCoIA5XIZxWIRY2NjyfXk5CQmJiYwOTmJYrGIUqmUjGYlPV6THuNzuRyWLl2Krq4uDA4Ooq+vD93d3ejp6UF3dzd6e3vR09OT/BbNdFBKIZfLob+/f9rQ09MDz/NQqVQwOjqa6GFiYgLlchkTExOJbOVyGY7joKurC93d3ejr60NXVxf6+vqm0J1r6O3tRV9fH5YtW5Zc9/f3I5/Pw7XOCmyGTCaDnp6eBrp9fX3o7e1toFkoFJIBhbQNROOv4hPpy+UySqUSSqVSog/+3b17N8bHx1Eul+F5Hvr6+hKftssfGBhAT08PCoVCIgs7DcmDnHFS17aubL3xCCLHcRpO0U8bDHWweOicmCCw2CcmGLG8MTExgd27d6NaraZ2GJz57N69G9dee21yNAw7GDka5Ihv+/btSUdE2A2+MQaFQgEHHXQQstlski4NURThmWeeaWgAbDuyY123bl3y0962jljxM5kMjjzySHznO99p6HyYjvdKKVSrVWzevBnVarXhaBiZB7FP3nDDDfjud7+bxEm7kK7runj961+Pq6++OkmXBmMM7r//fnzrW9/Cli1bkngpO2mvWLECb3jDG3DBBRckuqW8ksf5gLTCMEwGMEop7NmzB5/5zGfwn//5nzOW9Q//8A84++yzG3xS2hCxHQuFAlavXo18Pp90REq820HsE7VaDZdeeikeffTRKT/DIuXPZrP4i7/4Cxx//PEoFAow1gyS6bTWGBoawh/8wR9gcnIy0WVaB7RixQp8/etfx+rVq6fIIJHJZLBkyRIsXboUWiwhslzm7ZyY0Dkx4UV1YkLaznKpH/mcGBkZMeeee25Snjy9wOaBO7TlTm353HXdWe/Yt3e3yx3vMp1MI8uRvGSzWXPyySc3yCxl1dapCWEYpp6wwF36xhjzxBNPmMsuu2wKL/Z9Lpczb3/72xNazaC1Nrfeeqs54YQTEnltGSn/IYccYj7/+c9PsWG7IOnxL8vavn27eetb39ogZ7Nwww03mMnJyUR/UucEy7JtI3XN+NHRUfPKV77SeJ7XoBepGwAml8uZW265xRSLxYb8dvB93zz99NOmv78/oce/tq+tXbvWDA0NzUrPlIl+JdE5MaFzYsKLCnL0x3tg6mxAQv64HeK0csQil8fkMzsNUj4VbhWkawcl1uptuvY9+ZejUMkrr+VzCUlPprH/TodW0iBFXltGyS/BUXY7QTklD2l6nAlSt2nx8rktux0neZLXkib5lUuvhH1POtQf5eQzOSPjDKmVd0IEeSBPdvkdLD46ndA+BCsAK68NGScrvR3HtKy8BCuZbCCYj0grtxkkrWZIK4s8Mb+EI07zTtODjOO1TUOCz2ZKM91zCZZHHm2dM04+s+VotazZguWZOTaos+FL0pZlGfHFZhrmqhtbFtsHIPLO5X2O9KVmPHSwONivOiE6hFyrXSjIirTQZTWDnInIisDKSrCyQPwkNOP5V1ZMSUt2RGnPZws7v7xPeyb/2vFKjGYlKI8MSLHZbDGXPIj5lT4p+edfSdvWw1yRRsfmoZ2w5ZGNu7K+SJO2lT7WjK+0eBnHMtNmPjIty2CwdT8TJP3Fgi3XvoBtG6nPfY19q5kY0tkIOtZcRnjTQTotnaOd9GcDViJ5L+PoIFI3ctZg8z6TQ9nP0/Q+Hez8zWA7fFqcrASyAbPzEVLONJltGu20qe0zNm37Him2bBVperAHK1JmYjblUAYZ0iDLsq8JJb6SQ1xf7a/0mtFHE31C+AWvm2G6Z2mwy2pW/nwh7WjrbV91SORFidn9bPW3ENg32rBgOzV/g8ZYn4W2C461BIQ5OHM7IPmAqHh0kunipX7oUHKkqsQsY1/INhPsijBbW1Mmab+FkpW6lPdIee8jr+eie9rZbhyoJxlsv6XN2wUjltkkL+SNZan4E23pc9TVbGR/oYL25I/1YZE7oWYDVRXXv3b6zFyxeNqYBrICUTm8lp98tgNKfM5srLOqJB+LAfJCHppVXrkxVTqwbJAcMQLVWsONf9Fzf3E0Cdve9sjZlj8N0kfkved5yd6ndoCNsCxH3tMGsjwOLJiuVV5Im/am/agf2p/pSFdetwu2nKRP/5LlcV+Wiuur3IPzYgRllz4uO+fZfEgxX8iBi+0/vN7X2C86IVmJ2Ciz0s2mErcCHc8YWGmkISQfiwET/2y1dAzGG2s9nBVcdiyyQzLGJAeM8l4+259A2RzHSfb82A3bTKCc1BHtKTv1dkA2JrSLiSswy5RpZWPDzkTaaTrIdEp0znKgYeJOKQiChmUw+kW7wPLc+CQGyso4+iB5C4IASJHhxQjZ6Etfoa7aaaeZQP+lHWVnRHvua+x7DmLHpVLc+NgXx3HgeR5yuRyy2SwymUxbAo+WYZmMl+UuFuiQlJ0NqI5PCojijYAcXSLeTMc0duMkK30URYm8tg72dfA8Lzn6plqtolarQcdHGIVhmMg7U2CnzPS+76NWqyU6bQdIK5fLIZPJIJvNIpvNJv7De+qZFR1xYzTbmXwgZhG0p4k3p/KYpSg+BYPp6Su2D8wH9M1arYZqtQrf9xMd80w56r9arcKLj1Ki7GyAX4xg+0IbsuFn25PP56fUiYUKcpXB9h3acF/bab86McEYg4mJCWzevDlpXDmqbYdTq7iTC+I1bM/z4Pt+4jRLly7FypUrkxFsM7TzxATq4Mc//jFuvPFGPProo4C1zMTrMAyRyWTw3HPPYXx8PHkuOzHy4LourrrqKpx22mnJ7vT9AayYAFCr1TA8PIz/9b/+V3Imnkw3HYyYTbHhZ0e2Y8cOjI6OJmmlv1EP2WwWb37zm/Gtb31LUJ0KYwzGxsYwPDycNPRRFCGbzTZUavrW0qVLsXz5cmhxxl0URcnpBtNB2pr3d9xxB77+9a/jwQcfbGjg2bBlMhkE8Qx4aGgIe/fuFRTTccMNN+DNb34zuru77UcJjDF47LHH8Hd/93d49NFHp/gPfS0MQ+RyOTz55JPwfb+BN0L65Pe//3287nWvQ1dX1xR5iTAMsWnTJrziFa/A2NiY/bgBa9aswd133421a9e2ZeARtOHEBG29swvDEKOjoxgZGYHruvB9vy28tgLHcbBq1SosW7YskYe+KWE6JyY8/9v0YRiaSqVifN835XLZ+L5vKpWKqdVqxvf9tgTSDYIgoVur1UwQBA08NUO7TkwwxpggCIzW2nz3u981p59+uslmsyaTyZhMJpNc53K55D6bzU45fSBtJ7ZSytx8881mfHx8ivz7S5icnDR33XXXFJnl/Uwhl8uZXC6X5MvlclN21cvd3ozLZrPmD//wD21zpCKKoim8+7HPSP+hD9mnC9i+3gqY//vf/74566yzGnwgn8+bfD5vPM8zuVzO5PN5k81mjeu6U/wvLdxwww0NpxY0w3333Zf4ZDMb8a/UOU/RsPXvuq659dZbTalUMialDSCCIDBPPvmkGRgYmMK7HdasWWM2b948qxMTpoPfphMT6AO8DoJgis8sVqAP0qfS9P6iPzHB7nG59CH/tmtJzvO8ZDqslEri+HexwelyFB/8KZea5BKIjJMjYoLXHJFz5Lk/Lsdx5kmbSvmkvNMFLk9xeUjqivppBzj641KKlCETL89J2aQPcTScNtpPgz3rZz7KSf3UajXUajVEUdQgO/O3CyZ+zyjLkHqXtmJ6/m02g3ixYl/WQ866bL/aX+y0KJ2QnAbKaZ5UghL7XqgkKq/VStwKbPqzbSgkbAOaObyLkOVKfdBZ7OcQfEtnYrzUswSXqwhZTquQaeWSC5elZDopC9OwfNqa11KONH7SbGTnkc/YCbcTNr1mtpH3dhrCtgUhGwtb5mb2UqI+SX9eKExno+nKtuuFTcOmqywfaQYp/2zBfM3s0SrSbCP1IfmfTpaFBPmRdmAc/U3yb8uykJhdizkP2E7K9XUJaczFCHMtE6KSSJn4LmmuRktzWgljvU+T6elcfA8B8eUY0zC9LcdsIDsSCP2RL14zGGtDrM17M1AmpqN8sqHm/Uy05gMpi12O7IhtffC51AvjCMbTHrb+GCd1IfUg80h67QJlsuVmOfLdaZpdpRwQvtmMbyO2TBhrMMUVA6kLPpM+MV2wQfuRTlqaNMjy7UbdLjMtoAVeFyIQdrwSp2NIvdp/FwqL0glJJVBoWAIfyJAOOZ8RVRpsB0rTl3R+GUdQ50yX9rwVNLNXs/g0fmZTlg3bj5rRWggbIOZJdjyMkz7NONk4waLRTF8EZSNtW047zm6EFwssS/p9Gs+MYx52LjKe6YjQ+qpQ5rdltO+bgXmZnuXNZA8JOy2vbZoHKtL8s1X9zhWL0gmlCSRHRVLwAyEQdsVrd+MnwbKlQ9jOoVJ+VwhWxZVypFXo6ZA2irchKzrT26PFViDp8F7KTzr2bIR57E6gnWB5nI3IOCm7zYPNuw3Gy3RSJspqN/rM14zuQkH6g807r+V9GJ8aYMsl88uZnqQp5ZVQKaP46QLpQZTVjHYzSJ9jhyrltMvc3wN5pjy8NynLdAuBhaupFjidlpDGpEMeSEE28hCfrS4kjDVCtnmR6eRf+5qQjjgTZHm2czKO10ip1K2WJemkXaeBfGEW5bQK0mKDI/UvIXUul5UkP1KHafqTf/mctiUNea+tvR7tlLtVsOGyZSKkDpSYJdppjZglZTKZBn3bAxnKyWtbr82CTQvT8J0GSYf65718fiAGWP5DXcm4hcCidUK2kCbevEUHolEPhGAbzVgdEp+1A3aZdvmEzZMT759hkGkkLca3An7VhpSGntcsG2KZZrZlpcmnrFOdZbzkw5nDb8y0AtKkLLJcW3YVf00n+ZC24b1tL/teppfgUlUaL7beFhK27hnX7LkSjbaxTp2QssDq2GR+xtn6lHpICzZNBjWHhlbyYcMu90AJELNPs8ibjhelEzLGwPd9VKvVJHAndqVSOeACGxZtHSbJv+2EdAJW2kwmg3w+j3w+j0KhgHw+j2w2i66uLnR1dSWftvNzXurbj08UsPXPEwtmgjEmsR/zMVSrVZTL5eR5GO/ul5AO3yrYULiui1wuh1wuh0KhgO7u7gYdUA/8SfF2wcSbDflpeDP/ZVwQBIncKu6MeM3ASh4EQUP+crkM3/dRLpeTOB1vLMzn84l9pd0LhUKyhYEd12JB+mU2m03sQ7vIwJMltNaoxicwpOmQ7UQURYldKZ8td6FQgOM4CMNwSh21Qy0+SQNiRksZZuOXMi3rP+sWZbDL3t+DtIecVcvOaCGxKCcmZLNZnHDCCfjUpz6VNKSIjchlOjvP/oytW7fiu9/9Lm677bbEoWksNoA6HvFNd2KCxLe//W1cffXVuO+++xJ92Hrhp8cvf/nLsWTJkoaRuf1VnOu6uOiii3DwwQcjEx/oyQqk4/cVJm5gV65ciWOOOQZdXV2Co6kwxuD2229PaLES8p40HcfBqaeeit7e3mkbRt/38dBDD+GVr3xlg5zSP4je3l684hWvAERDEIZhgz8BQLVaxaZNm7Bt27YkLk2frZ6YAAA7d+7EU089hWq1Csdx4Isd71KnmUwG69evx6GHHtpU34j1uHnzZjz33HMI4mN6GAqFAoIggOu6COOTA+677z5s3rw5GfxwzxLl5z6dxx9/HNu3bxecp6OVExMA4P7778f73vc+3H///Q32ISifMQavetWr0N3dncjB59I3stkszj//fKxduxY9PT0N/otYL5RxfHwc3/jGN1Cr1aDEu076BJfrBgYG8Pa3vx19fX0JnTRks1msWbMGhx9+OIIgaBgckDY7tFZOTHAcByeccAIuu+wyHH744Yk9KOuBBOr0K1/5CjZs2IBSqdRgR/oubdXuExMWpRNy4nPg7I1ThJ1+f4eJN/EF4gh7dka2LO3ohNz4RGylFFauXIkvfelLOPvssxt0yfRhfLQPAFx88cW48847Ua1Wk3JI04mPRHJdFyeffDK+/OUv48gjj0zS2TDx8TWnn346hoaGGp7JTkMphf7+fnzjG9/Aaaed1tABErxu1gnZ6QDg+OOPx/e+9z2sXr061Yd4/9RTT+Haa6/F9ddfn8Tb+sQsO6Ef//jH+OQnP4lHH30UnuchiI/Jod4Rl3HIIYfgfe97H97//vcnZUXxkT1SB8YYfO5zn8PnPvc5+PFRN4Sk6TgOzjvvPFx22WU47bTTptjbER8o7NmzBx/96Edx0003JbSaoV2dENHf34/vf//7OOmkk5IldqTYx/d9XHrppbjrrrtQLpcbdMf0Kn4fdNBBB+G2225Dd3c3HGfqchl1sGPHDlxwwQUYHh5uGLTYWLNmDa644gr86Z/+aTJQZHmSj2CGY3tknOu6yGazCT3aw86zv4My+fGGaOoFwoayDrW7E1qULltrDd/3USqVMDk5icnJSRSLxSSUSqUDKpTL5YZd4nT+hXI++/1GV1cXent70d3dje7ubvT09KCnpwfd3d3o7+9HV1cXuru7EYYhyuVyg64nJydRKpUwMTGR/K1UKi3x7jgOJiYmGuxWLBYxMTExJV46rx1aBRsa0ioUConcXV1didwMvb296OnpaTi1oB0IwzCRdXx8HKXYj23fLRaL8H0fEA0cGyhb7iAIEv2zTpBmuVxOygiCAPl8foq9e3t7Ez/gdTuXIWcDY0xiD/Io/ZKhr68vkVvqzm4HJicn4ft+Ym+brtRBoVBAtVptsENa4FInVxMgbDIbv5T1JIoiVCqVhH/WAbvs/T2QZ87KKWcrbUI7sCidUAftRVqFkRVKot2O1G56raLVcm352wVZOVuB3dB10DiL4P1MaCVNBwc2Op1QBx100EEH+wydTqiDDjrooIN9hk4n1EEHHXTQwT5DpxPqoIMOOuhgn6HTCXXQQQcddLDP0OmEOuiggw462GeY12bVoaEhXHHFFXjggQda/nT1xYb/+T//Jy6++GLkcjn7UQOm26xKqHiz6g033IBzzjmnIT9SNt9ddtlluOuuuxo2q6bhZS97Ga6++moceuih9qMExhhMTEzgmGOOwcjICJDyyS3jBgYGcPPNN+P0009HNptN+LIx02ZVieOPPx4/+tGPsG7dOkDIauOpp57CVVddha985StJXJo+M5kM3vCGN+Cf/umfRO50bNy4EV/96lexadMm+1ED1q5di3e/+9245JJLgGk+LzbG4DOf+Qz+9m//FpVKxX7cwOc555yDSy+9FKeeeqqdrEEHe/bswZVXXokf/vCHdrIpaHWz6iOPPIJPfOITeOSRR6a1T09PD77+9a/j+OOPTzbUpskeRRF+//d/H3fccQdKpVJT/3FdF4ceeijuvfdeDAwMNDy3MTIygre+9a3Yvn37FFoSq1atwqWXXopLLrmkqe8g3hN29913493vfjcwi0/yX+iQdeiYY47BX/3VX+GMM86wk80J8+qEtNYoFot2dAex83JHted5U47ysCtCOzohiUj8XHgrJu7u7p52s6M5gDuhZvA8r6Wjis455xx8/OMfT06U4GkJiOXlSRGe5yVH6qTt8ifMDJ0Q4cQnjdg0uTlaiWNzwvhntwNxdl0ztNoJkV5onQMo7U655ckBMp3EfDohux4wTso9HRzHQS6Xg+M4U/iUIE3WHULq+sUON/5p+7R2bU4w84TWuhNSQhRFJooio7Vu0FVavDHGfOtb3zKnnHKKAWCUUg1/GZRSZtWqVWbDhg0NeWWZaeXZvKWFmaC1NmNjY2bVqlUN/Ej+GDc4OGh+/vOfm1qtluRNQ61WM/fee28qHTscf/zxZsuWLUneZjSffPJJc9lll03J3ywopaYNjuOYCy+80Dz00EMNuqQdGcIwbMqTHa+1Np/+9KdNoVCYwk9aIB+O46Ty57puSzpkuOGGG0yxWGzgKQ2UMwzDKX7EeD6z89kyG2NMGIbm937v90x3d3cil82bUsp4nmeOPPJIMzo62pA/jW4URSYIggbemoXpbCQh5bPlfrEGae80Pdr3s8G8uzGOEDqhMTjimHqgPsLiKGq6UfJcIGnZI0aWNVN4sYJ2aRZ4FphtT8R2ZHBTfi+LaBbfKsgHebH547FO8y1Hgn5EH0rzI9d1kxmanW8hIcuQup8pNLMR6Ul7M72UGy/i9o56ZlApepwr5t0JdZAONhISNOhCgI5iNwhpfHTwPOzKlhZk5yMbKcLuFGRnMVfYPDQLNtLi5oJWy5GyS3nT8swXkp92lsX8KmXwSLC8+dj0QIZtX/ve1tds0OmEFgnSSLYB54NmjYB83kFz2Pqzg0wDqyHkMzteNmoyzUxI8xE72M/t+1bLmg2mo2vLLeVvF9LKVgv8fsaW2bbpiw22XWfr29Nh3p2QXUk6oR5soyGlAWsH0hxD3ktnmS68WCFtkhZs/dg6tnVn29h+Pl/Y/mWHdiHNN2yfabds06FZWTY/zUKrsNPbeW26L5aQhnb53Lw7IbsSdEI9zIRW0rSK6cq0+WoWXqywK5sdmumIFZPxzSpsWt5msPMSaTw0K2+hMBMPC8kLy7Xp23qVPNqhVdj5ZF47/sUUbDvbtpgP5t0JdTA3tNOIHbxwoVIa2mZop0/ZjZD9rBnayUMabPrkZTqebNg0psNs0r5YMd9OadE6IcnkbBi2004n8FzKaDXdbDEd3emeQTyfKR0hdTIbHcz0fCY0y8+REyxZJJ8ybTPIBnC6dEQa/fmCNOUvZtoN83QN9mxh57f1llbGXGROyyPjZLlSdkJZX17K+FYwnV2lvLLctLSYJj4Npsn+MhvNeJgLbFpp9Gzd70+QOpN+3kyW2WJem1VbhdYatVoNu3btAiyh7OLz+TyWL1+efOXFtPzLTXNRFCW/DiqfOdZGQsQ7upcsWSJKeR463miolMLk5CTGx8dn3OzXTpD3W2+9FV/5ylfwyCOPNDyzsXLlSlx11VV4zWte0xAv9cnrZcuWIZ/PN1Q6qUv+rdVq2LNnD8IwTNLZMMagWCzida97XYMdJUhzcHAQX/rSl3DKKadM+WlrmS6KIjz77LN4z3ve00CHaZgOAA499FB87nOfw4oVK5I00s683rZtG7797W/j1ltvncLfXKCUwhlnnIFLL70Uhx12GJAiN+G6Lvr6+tDX1zdFxxJmms2qrus2NPr89dSZTtyYDT7xiU/gNa95DQqFQkO81hqe5yV1IpPJYMmSJcnPxafVrVZAHURRhEsvvRR33303arVagx4lXcdxsGbNGvzoRz9Cd3d34gvzAduLsbGx1K/f2g0pj1IK3d3dGBwcbPAHyQN1Pjk5idHR0bbxp+LN4z09PQ1xNowxGBsbQ6lUmvIFqLSfG2/A7+/vRz6fB5rQmw0WpRMKggDPPvssrr32Wpj4JIEoiuDEv8lOAwDAEUccgcsuuwyFQmFa4YaGhnD77bfj3nvvhdYamUwm6YSoLMQKOv300/G2t72tQbGyw2LcnXfeiR/+8Icol8sNZS0UyKtSCk8//TR+85vfYOfOnQ1ppDMbY9DT04OzzjoL69evT57zs1KtdaJbpRQuu+wyHHXUUchms0nHasustcaWLVtw4403TinbRhAEuPHGG1Eul6c4KK8RDyQuuOACrFq1Kqnw0sbM4zgOMpkMDj74YMAaEPAeMc/VahXbt29HEATJAIVyyuB5HgYHB9HX15fQmQ+MMSiXy9izZw+q1SqUUg08UBZjDJYuXYqzzjoLZ511VgMN+hphpumEYHWqJ5xwAs4++2wcdthhDbqeD0ZGRlAsFhFFUVIO/cJ1XYRhCMdxcPDBB+MP/uAPsG7duoQn8jBbPmjbH/7wh9i6dSuCIEilQR/u6+vD29/+9rZ1vmNjY7j99tvxs5/9LPET7q+CpfN2Y2BgAKeffjp+93d/tyGeupR+f+edd+KWW26ZckrFXJHL5fDGN74RZ555ZiIjy6RP8vp73/se7r33Xvi+DxPPgKWfI/aT1atX4/d///dx1FFHNZSVZs+WYO9eXQhUKhVz2223GcdxDJrsBOfO77PPPtuMjo4mO9EJe/fuAw88YP74j/842UXuuq7xPC/Zhc0d5fl83rz//e+fcUdvFEXm2muvNStXrpyyK32hAne6UwapG6kj+6/rusZ13Qbd2fkdxzG33nqrKZfLxqScbEHdhmFo7rnnHnP00UdP4c8O+Xw+lR8ZaGPJj20jpslms+bkk09usA354zX94De/+Y05/PDDp/gQ9cD7o446ynzlK1+ZIu9cQxRF5gc/+IE58cQTG8r1PM9kMplEbtd1zWGHHWauvvrqprvKCd3kxATKQX05jmMuuugic+eddyY71m3+5hIuvvhi09vb22BDaSPyc/LJJ5uNGzcmviJphGFoizUFWviZFrqgTUlnOloy33ywZcsW88EPfjCROa3O2L4810Ba/Lt+/XrzN3/zNw0nMEgdEFEUmWuuucYUCoUpNOca+vv7zTXXXNNgOxuM+9CHPmR6e3un6CSTySTXnueZE0880WzYsGFamrPBgr8T4uiJPWs2m4WKR8EQm/9MvDxjjEnOeCKYBmK6zl6do4UoihCGYcNIjTt7mVfHsy4uO9l0wzBMlgkWI3AkRj6clI1yRvDH3cpa7JJnfsSjFADJeW0yUCf2aIUjXzt9WuBMII0W03D0ZOJRtbSXtJHrug1LP77vJ3axeXTi3dpcKuA9aTKO5Sgx4ptvcOLZaq1WS5YiIM5VI0x85hhn4eSFtmkF1IWx/NVYvjHfwFUC6ohl0X6I/YJpldAz85DGdFCingNArVYDxEkTvHZdFyauD9K3qY92wHGcxHbKmn0Qtr/PNdg0OauhLqkXJ169MLHepY2cuA7NN5RKJdRqtQbaxvIvmwf6L+3Cmb8RZ/VJSJpzwYJ3QrZi2QgRrFhMi3jZh0pkGplOQjo5jYzY+HRmxpEOK5DMa4xBJpNp2/R/NqBsdAZYnS2EPGEYNjgT00mdyjS2zmQ6XnueJ1I0B52SDpsGyoF4YEDHhWVrysJ3RjwQEaJykB75zOVycK1OmBWGPJE/W+75gH4TxYfCUg76D/lz40GCxGz5kPLa9p8trWZgo0jYPqGadDq2rluBtAffLUlbSVCn1LUTN9LtgIkb08UAZSYol4y32yZYg2I+ny+y2ewUvbNM6prtpud5UKIdIi/0c8rBg0tlmvn45oJ3QtJhZeNCQRknDSX/SjppxuUzSVMGO475JB2bl8WE5ENC8m4bWMcjf+kwjOdfeU1I+WV+xs0EWRasRlJe8x6Crp2X8bSFzEv+JFT8LsaWRzcZQbYiT6tQ1uxB+pLUvUwn87bqU1KH0v5o0T6tQupL0rfLkDLz71z4YTnSJ2RgnO0zsAaK84VsOMmTiWfl7SzHlo/y0Ecg5KLNnWkG2vOB9Ffph/Kag0XCTs/2JI1WO/Q2fwozQCqYSFO2dA4+p6PAarT4137O/PI5wWsqlEplOjpou0YgrYA8pcltg3Jy1MJ76g0xPdkITufYMr/dQTSDsQYGtmMSUgZ5baelHPI54+VfPmN5jhi92TQlTwsBqXs2Ks3kkxW9FTAv7ahE3aH8CwXJt9SdbQPyJ+OnA/VF3mUZaUHKKPmYL6hLiTR52wFJT8om62Izvdr52wXKL+sM20GbF6kryS/jZLtCOvPhecE7IYLOZS/9SIemIM0qmy20bbw0SPq851+7I5LPFiNAjCSkPLZB5T35ZTq7kzHGNCxryXikNArMa/PWLLB86aRsJGVZvLdlkZD0ME1apqFc5J882f7C+IWEbNBoB9NkEDMbXqRvE9J27YDUt7QlrBG6tKG0E+NaQbN0tJGkKe953Sz/bCFtI+nymvK1I8gy7fppd8YS7eSBQbYvKuZDi6/emI5p0niRcWnXMs1c0LZOKE2phLEaPRpdopkybDRTBK+pPKl0W2GyJ7ch+WsW7PQyn420MhDzNF2YDjKNTMt46nK6EQr5knqwebAD0zSTCZYd5F+CvEmasJYqkGIz+dfOa2M6/uaKZvLY+qB8DITdURLNZE6zXZpczei2CptPopn+VUqnPxNkGTatZjLag7N2w5ZZ2reVQNj2m05W3tuDQ5sXibQyJWy+7GCng+CHdT/NFjYNk+LnNr25Yt6dkGQyLY7Mk8m0kSJhCxPF+0BshaSlt/O2opRmHdFMsHmRxpD39rU0XNrzZpCyybKa5eUar60PeS9Hv5glHyzbiFmp5Mcuqxma8Ug6fM442VEpq5MlPwzthk3TbojlIAsp6W1IXdloFidls+W072eC5FPyQh3b/KXx1AokLSLNTmnyLQSmk6eVMlWKj6bxLf9KP5Xl2/LLfPK6Gc+t8GvXEfLPe/nXhl0eeSYN+/lcMa9OyBiDWq2GyclJVCoVlMtllEql5FqGavx5b5pBSIsGKxaLDfRKpVJCp1KpoFKpwPd9uK6L7u5uFAoFdHd3o6urK7kvFAro6upCV1cXcrlce5UW/1RwoVBALpdDT08P8vk8urq6Gsrt6upq+DKFBmxm9GZgPi6R5PN5dHd3J7J2dXWhp6cnKbNQKKCnpyd5r9AuKKUSmalv6twOsEazhG37KIoSnykWi4mNpd35Vy4h7E+QlTkIggZ/LRaLqFarqNVqKBaLiVz2T0e3CqlLLT6HZ10k/ekCvz6lLzbrQLXWDbagXHbdnCmw/pfL5SnbAWzIsmU+m+ZsQ7lchu/7STsg69Rs9A/RETmOk9RFu+2R9aNQKCCTyUzxfek3hBf/5LwMPT09DXW9K27T7IFkGowx8H0/aTfpj9IXqV/HcRrqtC0LZc3n8w2zqPliXicmlMtl/OpXv8Lw8HDD55s2Y7VaDY899hj+4R/+oWF244pPfpnn2GOPxUc/+lF0dXXBafKJpuM4GB0dxRNPPIEtW7Y0GFeJ0Rxio5511ll473vfa1GZimuvvRZXXnkl9uzZYz9qQG9vL4499lisX78eJn4HI2d4RrwjePjhh/HMM880fDadJlMzSId14701p512GlauXJnEI5bT1u2HP/xhvPSlL53y2blto3vuuQfvete78OSTTzaks+G6Ls4//3zk83l4npd8es2PIShXGIa48847MT4+njrzpR6UUli3bh3+7u/+LtGLIz7RhfgsfPPmzbjqqquwc+fOhP801z3yyCPx0Y9+FJdddpn9aM649dZb8fGPfxyPPvqo/agBK1euxPnnn4/f/d3fhRNvR9Dx3jgTf6pOvd988834/ve/n+hQ6knKdtFFF+EjH/nIlGOapOwm7vweeOABbNmypSFdGq655hr8+te/TnbGE7JcpRQOO+wwXHLJJTjiiCNE7jqkjZpB+i4brVe+8pVYtWpVw+DMhon3t9x+++0Jj2npZoO9e/fiP/7jP7Bhw4YGvllPZ1Mn2fgvW7YMJ510Evr7+6HiTom0mcYYgyVLluDss8/GW97yFiCuJ2681YA6Z33YsGEDbrjhhqSzZp1g/eD9zp078fjjj2PHjh2Cs6nI5/P4oz/6I5x//vlJ/kwmk5xYQfkB4KGHHsKWLVsQBEGDjlgHyeu6detw8cUX44QTTkhsQ3nnBHv36mywadMmc9ppp5lMJtN05z7ELn8+k9fMwzSe5zXklWklvRNPPNF87WtfazhVwcS7jrmDl7uSiZl29l5zzTVm6dKlU/i3w1FHHWWuu+66ZAc7d0Lb0FqbK664wuRyuYaTBGx5mgUlTiDg/Zo1a8xtt93WsFPZ3rUcBIGp1WpTdmQTdtzGjRvNUUcdNaV8m5fe3l4zNDRkjDHG9/2Gne463gWutTa7du0yZ555ZiI35aDsUhfSZ2hrO0ge0q5lOPLII82Xv/xlId388YMf/MAcd9xxU8qSvJAfW1Z5AgHTpNWTZrLxxAQb0se11mZ4eNi87W1vm6K7tJBWp5rx4sQnmUibUSabblqQdLLZrLnlllvMxMRE4rPSF+lDvu+bxx9/3CxbtmwKvfkE8sJTOxgnn80UlPDfM88809x3333CKs9DyhXFp13w2q67QRAk7YfUC9P4vp/Q0nF785Of/MScd955U/izg5TTcZwpp8pIX/3iF79oxsbGEj6DIEiuyZ/klc+mOx2kFcyj+6qPdHK5XDLi0+ITWgY5Sua1HG3JUY4WpxmwZ+X7DcZxdCVpc8Qge2Tm4TPMctljOpAe+eEskLzKsjOZDDKZTDLqYHwrIO/Mxyk9ZbUh5eTu8HZC6pujI5YJYU/aJu05R3S0hXwu89q2oj+Rxv4E8sO/lMPEX2RJX5V5bB+eCfQtI05noG9QP3a6tJD2zhCWHNI+UbxxlbTpkzZdO0DUEdLhkphdJvlhyGQyDTP7dgSWx/aK9VbyMRNIh3lZJxlPOWW5SmydUGIjMOsBN3/aZRB8RtrUqZ0uDbKtYJmSF3lvt7XyS2by4MWnnJAufbhV/aVhXp2QEbuQbSZM7Kx0XplGx1NC3ssduBSKBoJQACEdgU7lOE7DWreyKjk7t3aCjiCXpagPVqAwPgqIjkf+Wu0k6CiO48D3/UQvdETE+uROfpYLq9NuF2gnJXZY856g3EwDURmkfXhNu7OSRdbSJv/anfz+AikbIe0geac9ZbD9uxlIL4oiZDIZuPFhtdSfzcN0sP2CNoHoENgQscFlYP2cCeSH+RCfaE8/tXnV1pFa9HWb1/mCbQ99tFX9Q9RH2ly+Z4IYHLPu0b+l71KnpAFRhymztKUrXluwzZutXmgD6phyUA8QeiFfrIfUD/mT/EC09XNF69pvAhZOpTNOMohYkWEYJp0CBTJiVCfT0xAcadiGYT4dHz8fhiEymUzSUEse6Bi8ny+knLxn58O1bhqOxlfi0/BsNjurI0S8+GgbXkM0bFE8MmK5rABhvKZMHbULdFzqmHbhNQBUKpVEftpV2o+BNmGFZAXjc+pM/qWc+yOU1bnKe4iZpHwm088EW8+ELKMVsFwb5IPl0G94z+ez9Sc2wPl8HtVqFSZlxQIxX/TvMAyT099nI9t0cOKOmgM2Fc9KpI/OBNmWSL6oI0LqT6Zhgx/FZwxG4uxDdk5K+Hsozmqj/zDPbEBeZR1ju0KdU376GMujDeSMjfyRLuPngrnnjMHCpZNSGCrTsQ7qk3llGualEpy4MSXovHQmpmE84k4gzUCSr3ZAOoojpq7SMDIt4YrZUisw1gZIdi6UkXKzDDo/79kptQOkS33bsrIyyQ8hTMpo2LYz8/GedEmbabBAM9p2gTpXcQW264S0C9Pxue0zaVCirjA9dSr1NRNkWmlHmwbrkuSRHYqUoxWo+JBaOaiUNEgbcSPreV6ystEuGGsjN2WXz2cC6xdi3fGjE9qGOqR88pkjlu8ccT6ezMNrCD+SuoqsGfZMYF7mkfaUHaGJO2fyJmlLOWgj0mkH5t06pSmE11QojWbDTmc/Y2AZaWmpYDoUDWenl3wwv61Im3YzTJeG/KalIQ/N9GGDstjyQsywZByvbX1INOOrFZCeDLBme6bJKNfWtQT1Tt3ZDSPTSFns+zS+ZFpJayEg+bXLl/qQz3kt+eI901Bvkn8l3rHYemoVkmazvGyc7DS8plz8S0gbMK0R9U8+l38lvTR/oV8QNo1WwIaXOjQp9poOTC9llvltGWWw4zlAlHLJtNQZy5LtRqs82/azdSppyjZD0qfMRgw+CTvtXDDvTsgWyoYtfFoDJRUlBZbP7bQStlLS4qSy7L9ySmxXqDQwHzuUNCPYZRJMTzltyDxSVumszJ9Go1m5NmbTGdqQ/MlympWZxi/lkra00zGtajLKbAbT5IOI6fLMF9JWynrhK+NlOjufhM2vzbutH1nGbJBWNoQM9nMn5SML2kQu9dj88K9NM01+0pCy2PVSWR1BK2BZsvNp1pk0A9NIWuysWbdboYMU3aRB6k3qeC7lUGZbBurftoudh3XKWJ0RZZ8rZmfFNoCVUyqwmULTlNMqZB7pdHzGaWWaUVqFLQOdkaBxbJq8Z/5mPCjRuDA+jV4rYP60MhcTkne7fNWk4SPvtm7tNBBySd2lYbpn8wHLlfzaMsnnNt+8t+VlGoglGXbM8lm7YPOVRt+IpRvJ+3R5IOxsg+mV9TUZ46kT3stGsRVIfmz92vfTQfLBsFB2aEZ7NvxKHm1akqZNXy69SnnT0Cy+FSx6JwTBsK0Ue1TCdLMZ8VBZ04Flsnz26q0aFVYlpcEIybuUNQ3NZJYyKNE42w1PM7ppkPomuC49k87aCcmDsn4mIU1f5Hs6/fC+FVtO92w+YLkypPHLe8m3ncbmUYuPbZrpqZ1I8wnKlzbiZ7xtJ+bTLX4FaNNgoJxpPM0GsnObD6T+qas0nc0HpGXrgD7QCpgvLX0ar3acfU/bE2l0Z4t5nZjw3HPP4Z3vfCc2btzY4CQ2Sdd1MTg4iFe/+tUNipSgYLt378Y999yTCEuHtBV54okn4qMf/Sj++I//WFB5viGT5WzZsgUPPfRQQzqZxonXoB977DE89NBDqFarDWlt9PX14aUvfSmOOuooKDGqo7NIfv/1X/8VP/7xj5NflYTV2LLsNWvW4IgjjkB/f3+DnCp2bjZCfX19OP3007F27doG/cwEW9+E1hqbNm3C5z//eQwNDdmPEyil0NfXh8cffxyrVq0CLDkIYwzGxsbwpje9CRs3bkw+YbXLp1y9vb143etel3xwIUfWpKeUwuTkJO6+++5Ej5KmvF67di0uvPBCvP71r09sIenwr1IKq1evximnnJLw1AytnpjQ1dWFww8/HIcffnjS6ErfgLDDU089haeeeiqxK58ry59e8YpX4KKLLsKxxx6b5FcpHz6USiX86le/wnPPPRdzM3+Mj4/jwQcfxNjYWKIz8kmw/DPOOAMDAwNAiqzUezabxWtf+1ocdNBBDashMh0HRZOTk/iP//iPpnWReWq1Gv7rv/6raToin8/jiCOOwJFHHpnoV/qE9IuZwLTHH388/uRP/gSHH354ErdQoLzk9ZFHHsEtt9yC3/zmN3bSBrBde/bZZxNZ06CUwiWXXIKzzz4bXV1dSRwhfbKvrw/HHXccli5d2pBurvIveCeklEI+n8cpp5yCf//3f0cQBMnLdpmHFeree+/FH/3RHyWfcko68j6tE5KOQKWFYYhvfvOb+O///b8DMR35ybMWR2i89a1vxfvf/34sWbIkoZmGrVu34rrrrsMtt9wCJ96flMlkGj6l5HumcrmMWq3WYERes1wAeNOb3oQ//dM/xQknnJDIqkTF1/Ha8969e/GJT3wC9957LyDkbAVpTuK6LiqVStMjdgg6Xzs6IcqUyWRw9NFHNxylkvYeBQCeeOIJvP3tb8fIyEhDvA3XdZOz/MgffY15WPYb3vAGfOUrX7FJTEErnZCKjyB673vfi8suuywpL4q/9JLbEwDg85//PP7xH/+xYW+bFtsNpI4KhQLy+XziV3xGn3BdF0uWLMHHPvYxnHvuuU11MxtorfHII4/gYx/7WMMAzonfTUpfz+VyuOmmm3DCCSckgwhC2lEphSuuuAL33nsvisUi3PjFPOs+bZXL5bBq1Sp84xvfQG9vL7SYeTEN9bBr1y6cf/75GBkZmbYurF69Gn/2Z3+G9773vQ31jrqy/a0ZZLpcLoe+vr6WZnhzgSxL+hPbHLYt06FareLTn/40rr/++gZdO/HXeWyLHcdJzoXkp9hSnybeSuG6Lo488khceeWVOPPMMxtsPZPumsI+QmE22LRpk3nVq17VcOyFfQSGUsp0dXWZc889Nznuwff91GMetNbmpz/9qent7U2O1mhG98QTTzQ33nijTaIBURSZUqlkrr322oajRpByHJDjOOYDH/iA2bVrl01mCp544gnzJ3/yJ1P4kkeb8F4ekcEg5SAfb3/7280999wz5ZghecxHFEVm+/bt5pxzzkmlNV2w088mL9P39/eb4eHhBv5saK3N3r17zWtf+1qTzWZTy+J9Nps1p512WkKn2dElWmvz4IMPmvXr1yf57aNveLyMXYb0IZk2n8+bP/zDP7S4T8dMx/awvIMPPthcffXVU3i3EUWR+fSnP226urqm8Gfryo5zUo7HUkqZVatWme985zt2UfPCvffea0499dSGcqReyfvg4KC56667TK1WS/JK2XV81Ey1WjVvfOMbTXd39xSakrbneeaoo44ye/fuTWjwaCwd1wPS3bJliznkkEOm6NEO69atM//0T/9kdHxMTjPbtAJpW8mL/NsOyONy7KNxWuW/VCqZD37wgw36lkf1SB3Zz2QamfaEE04wP/3pT1P1MBcsTBcuYMR7F46SuDM3ijeK2aMlHe/qZU/c6kjDiHVpjhyYl6MpjkbtcmdTFkcEiMu04VjvJSgXxEifkGlYtp2PsypnHkdkUCeI6UoZFhu0Daw1ZvlX2pBxts6l7ml7eW+DtNKezQeka9vd9m85CiW/9ujdlln6o+SfswjqxKbTDlAWlbJMZeubdZqQ9uU99+gQ0t+lfW1Qb7DqEuK8rey7k/yyDlH/s/EHWx/SJrB4nS+of4hVk9nSZrsr+bTbOsqT5mvMz8A4mw+Zd7aYe85ZwIgvLQg2PpwOSsXQWVnJWq1gtiLpMNLZudQB4YzMm9YopIGNC/knfR0fR0RnseVl+RC82uXRIagTJY7HQby8yGvM0fjkg2VLeosByiflpKy8ZsWRNuTSg/QJ8i71zfTNwDKlfeYLli/t5Hleg+1YnvR12htCFple8invaT/64UKCskn9yuvAOpGZPFI2M83eMXnNpSbSkI2drGcQddt13ZZP0WAelkua9LlWQHls+ZTY/NlOe8jOh7Sl7mcC213ySpmNONcQoo1mWQyMkwNhiI6ItOeD5jV1FiATFNRmSokvR1TsBIxLA5UsnVk6oZ2X6dMgFSrjkLIZL433NDhiVmfLYo/K7LJlPPNO95w6kPIybjaQtrFHnLOlNR1oX3kv7Q/RGCCuZLJ86pYw4stD5uM942xQd0iZIbRqYxsyj+SP8Yyz/QHCz6gDmVaCstjxzcCyW00/V0jZbX9mPZDP03RsxHE5hNSdjCeoS0mP5ds8tQL6kvR/e3BM2HHSNrJ8eW3Xq/lC6kfKm6bfZpA+YlIGX9SxTEt7KDF7tX2WHxKpJu3XbDDV8nMAmZBCOuIgP0L2ykY4sxSQ8VQAr2VFpuMzv02PRqIiSaNdTiJ5RMw3jcMyyMNMweZZi9Et9cdyOOKUz8jHTEHqjbySbrtBOZSY6TCeAaJRkI0Q5bflMmIJypatWZD8ENS3HT8dpG8h5tGxThmWUFZH46T89g7LJt12+WY70Yp+pH9ylk+bsz7THnzhLWW309LOzEP9cCTOePpUM/9KC7I9YnmOtcSt44NEZRn2IIjPZDxl2N8h/ZiQfFPn1A9llDqGNTOVNpgL2tIJIcVhJWOOdS4YjSiNx3gKrkTHQ8XZCmKZ0jmYlnTTGt/5QpZlG1UappUgZWB+8ivjjfUjgJTPptcsML2kuVCQ+pHyyHKpJ9pa2jQtvdR3q4Hgtf2Xum8FlEnKRr+FaNR4zcrL9GzopM2krO3yzcWEGx9KrMRXW5QpTbcqPj9O6s3WK6yOgDrjtbQddch8tv3tYHcmsnwZT99kGY516jfzMJ1N80CDsk7Et0G5KK+cBckwV7StE0IKszYYz84hDXS4NGUwTj5jhZdx8prPZMM+X9AAcjTAkazd+MwU7NmibUwpM9NJh7HppQWJ6Z61AyZ+XyFnamlgo+tanx0zPa+lP7jW+0FblrRgp7WvW4GdnnqXHaddJp9LGvwr8/D6QATllzNC2oygbLIeykZepmO8E89OuLTtpMyeZIcifaZZkHRkpwYhB++V+JkH3qfxIMuUs6kDEZRfzkLloFcJX5XtuLT1XJHeE8wSdodCA/GaRpagoAxMK68l5DM7jgqSsOkyrh2QdO1yKWsrgXzbNAgpA/+yYZdyzxRsMI6Vsd1QVgOdxgOsBlnG2WkIm44tZ1pIw0zP08C0HGzwXtKR9rLrhMR0/nMgwViNUzPbpenF1h+DzCM7K5sWy7VpNgvyOfPKdknyLvmAmK3LOF6Tjnx2ICISHyUQ8pr3RrzbREp9nQua15QW4Hkeli1bhnXr1mHNmjVYu3Yt1q1bh7Vr12LNmjVJ3EEHHYT+/n4MDQ1hy5YtGB4exrZt27B161Zs27YNQ0NDyf2ePXsaHMV2DsROF8UbN7du3Yrh4WFs3boVW7ZswdatWxN627Ztw/bt2zE2NjaF1nzgeR4GBgZw0EEHJTIfdNBBDbKvW7eupbB69WqsW7cOS5cuhRf/aiGmMa7jOFiyZAnWrl2bBJtmWpD2WBfzyevly5c3fb8xW6h4ar98+fJENukPDORr2bJlScfKIGlRD9lsFitWrMD69etblpll2/Kui/UxODgoOG+OfD6PFStWYM2aNVi/fv0UPfJ+xYoVCMOwwa95vWXLlsTHt27dirGxsaTxsiv7gQLaJooijIyMYNu2bRgeHm6Qe9u2bdi8eXMSV6lUkkZdQtpex1s0du7cmeTbunUrtm/fnrQhjN+1axdWrlw5xe52OOigg9DX19fQiDbTe61WS9oUu32iTNu2bcOuXbuSGRnRjOb+DqUUBgYGsGrVqqSu8Jr6Y91bsWIFcrlcosP5tq3zOjFhbGwMN998M7Zv3w4tfnEPwhiyx6zVaslvisiRshIj561bt+K73/1uMn23GybEDfGqVatw6qmn4vjjj59SnhwZhWGIBx98ED/5yU8a0qXh8ssvxyc/+cnkOIpm2LNnD+6++2489NBDDfzzL6wlhOlAfo499liceuqpOOigg6aMFhHLZYxBsVjErbfeiueeey7RzXSjbsJOK/W6bds23HTTTdi9e7eV63moWZyYUKvVcNNNN2Hz5s1JGbYstNPSpUtx6aWXTpnWU15e7969G9/85jdRLpeT5b5WIHmU9BzHwdFHH403velNVo6pePLJJ3HHHXdg586dyfKh3ZDxmqdjOOLTX+aRg4xf/vKX+OUvf9nwyb6tg5kgy1+xYgX+9//+33jb295mJ5sz7r//frzvfe/D/fff32A/CSf+TZ23ve1tWLNmzZSZIqwZ9/e+9z1s2rRpyhI6rLaiv78f733ve5Pf6zHxsl8Ub49gOSx/pjrQ19eH0047DS9/+csBqyzyp+J3W88++yz+9V//FcZ6D2vj0EMPxQUXXJDUB0lnf0G5XMbHP/5x/NM//VMSJ/2G90opnH/++XjZy16GfD4PxIMLaU8OFleuXInXve51OPjgg5P8M+l/Wti7V2cL7kDmLmQbWmtTKpXMbbfdlrrTW+7Ildeu6xrP86bs3JVpHccxnucZiNMJGFzXTXZ181rSSAuXX3652b17ty1CKrhLOIoiE4ZhQ1yaHtLAXcZBEDScjCB3X8t7uWO81TKagXR83ze//vWvzdFHHz1FHzKoWZyYICH9QvKu4130Mo+Uk3qVabhrfjZopsvZQNqY9za01uaZZ54xV1xxRYNfSp+jP9qnHszkl82CzLdixYq2n5hw3333mVNOOWVa/qQssu7JE0r4PJvNNtBietJhfZcnX8h0Ul+s1+vWrTPPPvtsqk0ktDgpQZ6YYPuE7/vmJz/5SUPZ5I/lMpx11lnm17/+dUK/HfWy3SiVSuZDH/rQFH3Ke4YvfOELZmxsLJGFoI5kHWCamfTeCubRfT0PuSkvbdTAHpQjRHukIEcPckTCl4Oyl+VzFY9g5GehDHxG8Nrmaz6QfNijBV7PFCgzP11lvISMI21bzzbdZkHSpF4zmUxbf8GSvBH0Cz5jIGzeYMkpZzzSd2zZZOCs2oYdZ983g2N9sSh54F/OePjLmdIvKTNnRgxGfCK8P4K8TwfKDWEfnfL5sjEGvu836IP2Ih1+dSWXuKS/UF/My7IlrWYBsS/q+Hw+0rR9RTV5F8U2hOlZvhdvI9HTfGw1X0j+2gVp17R2hdcyXrZzTCPbu7li3lqTwvBexvGazGpryiqdxKYln1NoGWenI+gosrLTiZByUoJ0bJvudLBltZ23lSAh4yivEh0qdSefSUjeZb60tBLTPZsLSE/qHKJBohxs2KXeCPIsbSLlZ1yabEzH57I8xlOHrUCWBSEXeSEP2vo0W6KZX2kxkJL8SbnYCS4mpIwS1KO8l3a25WR6mWc6e0v61C/rMFLaC7mcOVOA1Q7JfATLI9Jkk+Xzmst2pEU+Jb+Mt+vFTGB66sGmOR2apeU7YHsARP6lTiD0xmfSVxk3VyyKZxtrZEhlep7XMJJp5viMp0KlElR80rCdpxmc+KMGOXOTTr6YMOJ33SUfsmKo+EW/HB3aDmDEpk+ZV3a2iwFpQwjHpW6V+CRdi2Na7EofBEFDHGWQdNJ0IJ/zulmFb1Uv0i9oAzfeH8NPiKVPUj7GcaTcCnR87BPzy3jSWyxQv1KvhBF1l77HvxL0Rbt+Mb2Mk/eyvku9SrtKuq3qRfJIOtR1Mz+xIeVm2czLmR7ivTTkj0GLwVCrsN/LQPhDK3CsAQBtQt/V4jNr8kY9S30vJFrXxhxBAzjxDnMqRMWjmEh8/ui6LrLZLLSY2lIZVCaNLh2Z0/hWoK3j8mXDQT4WC0psEqP8slJQJpOyUZVOjtjR3PjlN8ToulWdtAvUJ+LKE8WffSI+7UE2AOSZ15FYfuWgwq4I0icQy8kypH9IPyFfBHU4F91IXjzPS5beJD/UO2ULwzDhuxXYmzkl0uIWErKuSt+kXKxL7CBkeplW2pm2YTp5zfzsiBlvxHE7cvlL8jUTSIOQDTv5aMUnZL2D0A3iLzjpA258QLCUEXF5vu8n99NB6o4+Tlq6xT06Mm8mk0mWJOVziJmcXU9b1ct8MLP15gk6lxM3NLKCycaCyqKBpOHo0FSeEzdgNDJiB2gFjvWbGqRLRctyFxqUlRVENtqsWHQEWEcCSdmN6Oj5l061L0Cnp64h3ntJW9MXmF7KxMpLGSJx2CLj6VsqHtCwA+Bz8tAOm8qyYG2+JK8E7cG4VhoLgjxLm1Lu2dCZLzgooHyyzshBAmK+qAPGp8lPu6uUHfoyj+zg+Jw8ReKnzUm7Fb2ouM641uG3tKNtw2aQPmWsY23IFztGyTuET7d64Cr54eBMWXWFOpgJTCcHejKedN14EkDema4VvcwHrUkxT9D5aDzZmEoBeS+dFZaT85l0PiOW+2aCzEeHhhgR2I6zkGBFhOXclFPyRPmUqKR8JsFKRZ3pWUzd2wXKgbjjlI0E5aO8trNTblsHTvxDXrIBYeBz2Yk1g51vJsi0RjQ65J280q/IhxEvclstC6JhgMi32PaDGKzRfyQP9DGIlQX6HGHnkYMipuV9mj3s0+jtVRTmbcXmsDpAWTZt1KqOJR3bB/hX8sc8hNRdK9ApH19A8DETpA8ZMWgi7zKN7/uJfzNPK2XMF4vSCRFUBI1FZ7AVJY1Ix5OGk+khGjo6QCuBoGHSOoCFDo442FLyRJAXNm5GyCv1Qr6b6WCxIWWUOoWQk85Nnm0wPa+VtV4tn/MZYnpML/PL9BKS17Qg06TpWT5jQ8l8ssOS9pgu2PzLBlN2UAsNJUbssn4g1gd9UFtL4+TdtpXMw3jKzPIYj5QZjn0v45lHlmUHCP2x45F1j2lmgs037S11Q1o2XSM6gVYg+Za+BEtf0wXqx+bDWDyTJuOlXRYaC+7VFNB1XeTzeeTzeWQyGeRyOeTzeRQKhYYgPzKQirOhlEI2m01+kpY/gTxdyOVySZ5CoYDu7u6kTO4ArlQqqFarixIqlQpqtRoqlUryLiANdAoVN1JhGDbQKZfLybV8xpneYjgSYnvR6avVKnzfR7lchu/7yTV5pexyBmvbW/Ku4w8WmJf5Kb+0GyusTU/CxLNLqce0QP4J2dmlVVY3/rlr+nmhUEA2m53ii81CLpdrqBv5+Ge9yfNiQsU/YZ3NZpHNZhv4ktd8h5Pma/LexI1wV1dXIitpsV5SD3IWSb+XjSntYOINwjPVW+lrstElbL6nA/mIogjV2M/5t1KpJD+7zbhardZwIOtsylJNOkgdv7qw5bRDrVaDUgpdXV2Jrahn+iVtm81mE70TzcpvJ+Z1YkKrCIIAW7ZswY033jilR7exadMmfOc732l4N2Ss2ZFSCitXrsRpp52GE088MWkQZoKJG0nOnLhEIBsUJUbViwFWsJe+9KU47bTTsHr16kQWykv5lVKYmJjALbfcgk2bNiU0WLmNeJEZRRHe8Y534NBDD52yBk16xD333IN3vetdePLJJxvSSagWTkxg5azVavj2t7+NkZER+L7f0HCTFuIllxUrVuA973lPklfSk3rYuXMnvvWtb2FiYmLKc9rPcRwMDg7ila98JU455ZQGevZ1FEV44okncNNNNyX00qCUwpFHHonXve51WL58eRJPnUuMjo7innvuwT333JPIao+Um8EI35b++OSTT2Ljxo3YvHmznQVK1Il2n5hgjMHQ0BB++MMfYtu2bYDoEGxd+r6Pb3zjGxgeHk7saKdh3re85S048sgjkyU80jPWxxzj4+O47rrr4Pt+ohPJA+n29/fjkksuQV9f37Q65okJr3zlK5vyhrituuOOO3Deeec16NeGUgqHHHIIzjvvPKxZswZanJIBYXcA6O3txcte9jK89rWvTdLNBrJ9I7/PPvssNm7ciGeffdZOPgVyxhdZP2THvyo+MeHlL395ckoF9TIXnmcFe/fqQiFth7J9HUWRuf3226f8Bj2vGRzHMSeddJL513/91znvULZ332utzTXXXGOWLl06pbzFCO94xzvMr3/969Rd15LP7du3m3POOachL3dyy2ullPnBD35gSqVSAy2TcqrBxo0bzVFHHTWFJ7uMVk9M2Lt3r3nta19rstnsFBpy13s+nzennnpqgw/Yf3n9yCOPmDVr1jTsnpe0KPcxxxxjvvSlLwmOpvKptTa1Ws185zvfmSKnHRzHMRdeeKF56KGHkrwypEE+a5YmDbQzfTOKInPLLbeYM888s0FeqU/+bfeJCc12wuuU3fQTExPmtNNOS06JkLzJ4Hme+cEPfmCKxWKSl3/lda1WM08//bTp7++fQsO2u/28WVi7dq354he/mPAu/0obyRMTZqJPn7PvWf9k2Z/85Cen6G4mkC+bV6212bBhgznvvPOm8GSHQqFgvvjFL07R9XT+2UqadmIBu7dGsFeVow77mr0t44DGESKhxZc6Mn42kO9VYNHfF7BnCRCyy5FLGp9GzOaMGIlikeWS5RJOyvsEXssRng2bjg05UpNxWuzgbwZZ/kyQvgaRl7pOg+SrmXxp4OjZXoqSYBzLl2ma8TMXNBv5KmulgHzY/iZ5kXHTycRrzijsdLB8bLby0o7SnvNpQ+hv9j19Rsbb+mkFTCt5lfGtym/rN+2vBNNPl6adSPe0DjqYA+i4rIwmpUG1HZrvrWRFtUFaSvzGSVoeXs/Uudj5ZoLNN8vGLBqC2YKNY1qDJvmhLPK+gw4OJHQ6oQ7aCjbQbvxZqTxVQDaobDjtTkpey4aV9/Llst3gKvHpd7vB8lgmy+KzdkLSSxupy05ZjuwXQu4OOlhodLy2gwWB4zjJV04Mjthk58a73yEaXXZgYXyShuyoTPwxQS6Xm0Ink8k0bKCcbibEspz4BA/JX1pgI898kvZ0n9fPBZTX7qyRwi+/5uSzDjo4UNHx3g7aChNvLFy9ejUuueQSfO1rX8PPfvYzPPbYY8kPu23fvh3bt2/HM888g//8z/+cMuORR8GwA3jJS16Chx9+GJs2bcLIyAi2b9+OkZERDA0NYfPmzdiyZQtGRkZw99134+KLL04ab9K14bouLrjggoSXZmFkZAQ33ngjXvKSlwBxg88O0p3h3dNsoayvlwDgvPPOwy233JL8EOTQ0FCiu5GRkSQ88sgjuPDCC9vWIXbQwWKh0wl10HYUCgV8+tOfxv/4H/8DF1xwAU455RQcdthhWL16NVauXInly5djxYoVWLFiBQYGBpLZBTudSBxfpOJlONd1MTg4iFWrViX5ly9fjpUrVyZh2bJlGBwcRD7+Ua7pwJka+WgWli9fjr6+vmRZjEtgnLG0G6SNuPPMZrPo7+/HsmXLsGLFCqxcuRIrVqzA6tWrsXTpUixbtgzLly/H4OBgy0dXddDB/oROJ9RB28ClsEwmg4MPPhjLli1Df38/CoVCsrQl07JRt0fvsiG207Lhl7MbprXppc2AJOxyWwFnZphj/pmgUr42s3VkP7OvO+jgQEKnE+qgbbAbRXY6bFTZmUho8Wk5odTzHZqd11GyI6uH+vXzDbcxBhA0G8tUAFSykaIVSFkSGQ2gUP+bMNEAlmCXZMc//8wYA/5TClDKwJgIRuskqdQTVF2OZEaWKMQKIovNjZ2UUR10sFhYlBMTZoM77rgDF154IYrFYkO83VCdeOKJ+OhHP4o//uM/TuK02LXMhkdrjcceeww///nPU1/gmnhtP4oi3Hnnnfjxj388pWwb/f39OOWUU/CSl7wERuw9YENJPh3HwS9+8Qs88sgjDctL5JN5HMfBy1/+cpx11llYv3598kympzxhGGL79u0ol8sNZTIdZclkMnjPe96Do48+esoyDfMQLZ2YgPoRLn/zN1eip6en3pbH5edyObzk2GPxqledkdA1UElnwmZNQQFKQcWNaf05oJz4HgCUqXcS2gAKUHDqachv/XH9Mr5miQbsfOIG2mg4qt7p1MsXnZaq01JJfgFRIwx5jzseU2dLpDOAo6AVZeSD2N7J//Q9HT+nIA4Qb9fT0ICKOyHmNQYwDpRy6npB3XY8ZlIBgNZJeqMMoGJdmsayTdxpAYCKO6zn+asrwiggMoAb60X6ymOPPYZ77rkHlUolplJ/bozBli1bUK1Wk3g+Q6w7flRyySWX4Oijj05O8ZB+SFoAsGvXLnzhC19AqVRK4uaDTCaDJUuWYHBwcEobAcGr1hpPPvkkvvSlLzUMkGS9ni3WrFmDSy+9FH/9138NWDLPFuTltttuw1VXXYUNGzbYSRqQzWbxxje+Eb/zO7/TUC7bClsP08HEbdWSJUtwxhlnYO3atcA85UHdF+eo2QXCfDohGkgqtlar4Xvf+x6uvPLKhnQQNNnoT0xMYHR0NPnqqRkOPfRQvO9978Nb3vIWKLG5DnHHQ/rGGHzmM5/BN77xDdRqNSAuk50QRMfZ3d2Nvr6+5KsnyaOUZ+nSpfjLv/zL5FgaykxanuchCAJ4nocVK1Ygn8/DabK8RbTSCQGAgoODD14PRyk4TtzsG4NDDj0UF198MS6++GIA9bIiDQAGjhPLoky9EwJnD/UO4PnWkrLENjbsHeKWMW4uGzqh5JI9Q73LSJrwuMeol1uHMnVyfJvDriFJ0VAb2AWJTkjHfCPuT1SdiFY65kfFD9gxkHfZCUXxXweAC5j6Bw4aBgZRIo+Duh7qNOqdEGIWo1iNCgZOXb31+Z0yMCruABO9xb6mVNIFOsbAMSbujGKBHAfGAUIDeApJj01//OlPf4ovfelLyaCK/tfb24vPfOYzOProoxuO4wGe97Uo/iHJ5cuXo1AoJHSlryP+4tB1XQRBgO3bt8+qkZwOu3btwne/+13cfPPNU+oUeSCvtVoNw8PDUzohQvLbCvZlJ8ROo7+/v4FvthUztXWE1MMxxxyDj3/843jVq16V2HBesI9Q2Ne4/fbbTU9PT9xMPR/sIzROPPFEc+ONN9rZjbGOnahUKuaf//mfjeu6CR157AevHccxrutOKSctHHnkkearX/1qcpwGj+Owj7sIw9B8+MMfNoVCISlL0rev03iz41avXm02bNiQeuxQGIbGxMd7BEHQwJeEfd/KsT0AjKNcE3cJxlEwrlP/+/rXn21++tMNxhge82FMFGkThJHR2hhttAl1aCIdGW2MibQxYVT/G8XpjdEm0qHROjDaBMaYMA5RPdQJ1dPqOFMSImN0WP9rGu0QGS3+RUabyEQmMmF8R3J1xuIQRybkn48S5cv0knpd3ufv6rw/TysyxvjGmGocwufLi4zRYWSiKDBaC/l1rKj4NoqMCYwxNaNNzUQmpJQ6MlrX5YtMGN/HejP1PL4xphZf60gbHUbGBJExfmhMVC8ioHjCd7TW5oEHHjDvete7krqEuN709vaajRs3Gt/3n9e7qA9EGIYN9OxrLXzYGJP4cDuwdetWc8UVVyTH7MijdXjUDq/Tjh9S4jgsu17MFNasWWP+5m/+Zoo+5gLm37Bhgzn33HOnlGUHpZRxXbdBPl5LO7YSKPsJJ5xgNmzY0ND+zUeuqetTBzjYW7PX54zD/vSVYE/OkV1aGhssQy6pkQ7vOcPhNazRBOnIeHlP8NrmU6bnc/LuxPtneD3vkQqhFJTjAEpB81WDApYvX47169cnyYzRcBwFz3WSJTejNYypj/UNTLxsVNdBvJAGBdSv4rh6CVE9KIYQBiGAEIBf/2tCQMd/TQRlQsAEgPEBU4MxFQDlOJSgUITCJBRKUKYMpcuAKQOmJEKxHjAJg0loTMBgEkZNwqgJQE0ADu8nARShTAmOqf9VpgxlKoCuwugKYCoA6n8NKjAow6AEgyIMyoCpQmkfymg4RtVnMZwwmXjaU9duHDQUdLxAV9fb87EOtHGhjYIx9dmPNs8vBBJ1awBGKcB1YJRJXinplLqwYsUKrF+/vmH0zFUALq9J/6bf00/lJ+1pPqnEnjBYv7fVDtgrFhB8sExj/TaZsZbbDzSwzeCsTuqTumgVsi1xrB9fnA9mbnEPINiNsrKm2fLeTo/YKK1MT+UmRlhlqZSztRCXZfNn/5WBeWznYcXkXhpJ1y7LLnO+qHes9Q2mdXnrHUxvXw+WLFkypUxj4g8PAHiuC9dRcIyG0hEQ+kBQRWViD0Z3DWHPjq3YM7wZe4Y3Y9fIZuwa3oRdw88mYefwM9g5/DR2bY/vd2zCjuFnsWP7M9i5/Vns3P4sdm1/BruHnsKe7Y9jbPhxjG7/LcaGfoPxbfdjbNu9GN+2EZNDv0Rx+Jeo7PglqjvvQnX3L1HbUw/+3l/CH70L/ugv4I/9AsHEfyEc/y9E4z+Hmfgv6Ml6iCbvQBCHaPLniMZ/AT12F/TYLxGN/hLh3rsQ7b0L0ehdiMbuhh7biGj0XkSjv0I4djfC0bsQjt2FcOyX8f2v4I8+BH/sGdQmh1CZHMbE3u3YObQJO7Ztxs7tW7Fz+7Yk7N6+BbuHt2LXyDB2Dm/H1i1b8NRTz+Cpp57B008/i6eefhZPPfMsnnpmE55+Jr7e9ByefW4LNm/dhpGdOzFZLCESAzaD+vqoqr+2g9aNvxFkjEFPT93O0k/DMEyWdWRaORCSPkEfteNtNIufK0zKD0OyDDkAlfwRlHU6fvdXUDYpgz04aBXNbGjHzRYvqHdCtsJVvL57/fXX4/LLL2/IL3vxNKVOhyOOOAIf+9jHcMkllzQYl+vZpGeMwUc+8hH88z//c/K7HrYzNCtbpnNdN6lAq1evxvXXX49zzz03SUtZmJ407Q6MsGVu9Z3Q82MWg0ym/lJTa40PfOByfPazn0Uul4dSzx8fE0UGTvwivf4yJgJMCGNCRLUKJvfswm/uvw9bNz+HwK/BRCGCsAZjOGY39S/EUJ8M1N+61EfpyvXqOjGAC8CDRsZoeCZABgEKGQ3jTwJRESoswUUNnuMj52kUcg668h7yBQe5HOC4Bq4bDx48wHEA5dY/mFAe4LqA8hRczwWUqc8gjIaBhhMpeGEWrs4BxoU2EYwOoZSBchwoKBjjIArrHx5oRNAIYaDrBSALgx6EagVM7kiEmeWoGA9Du8axaetOlCshYDwo40DBgXIdGMeBdhxEjgc/AoaGdmDz5u2o1UIgk4V23PrrMB2/EXIdZPNZ5Lu70NVdwPr1a3Hyy07ECce9BKtXLEchl6v7ozF14YG63epTVSD2xyAIcMMNN+DSSy8F4sFYGIYYHBzEj3/8Y5x88snJOyHbx9JgP5d57Ov5YmhoCF/4whfwxS9+MWmEZR3jNQeh9OHZzhbSsK/fCUHoUMpqtz/TgXoyxuCkk07CVVddhde97nUN+psrXlAzIY6+pPPaoxweFSOdi8pt1dmVOKNMliWdGqITsNPJzZitlMsOiLJxhEnavIboiBcCz//SpoLWBlobGAO4rpccp0NoXU+nHAcGEYwJUV8QClHcM4KnHvw1HrjnF9j6zOOoFfciKo9BV8fhhWVkdVWEGrKmhkwcPF2FixpcU4GjK1BRBY6uwdM1ZEwFWV1G3pTQrYoYyFQw6JTR50yiB6Poifaiy9+FfHUEucoQsuUtcIub4JWfQ7a6DflgK7pqW9EVbEN3uA29egg9wVYU/M3o8jejy9+EXr0VS50hDKit6Ak3oRBsQo/ejF6zGb3mOfTr59BvnkWvfhp9+kn0mifRqx9Hn3kSffpp9Jmn0WeeQZ/ehN7oOfSEm9ETbkVXtB157EJeTaLgVJB1KtBBEWFQhjE1KBVCqQBG12CiGrTvA1EIT9U/bIg0oNwsHC8H42QAJws4GURwUa1F2LN3HEND2/H0M5vw05/dgX/4h6vxmc9ejV/88l7UQo0ICnAUQl23k4KqfxoeI4oPmpWDtzAM4cQ/u27/9Dp9XgY+s+uCXH2Qvmt3FvMJpEeacpbGv7I8mRax77d7eXAxQDloMyVsCGsw3kog5PV8dfKC6oTCMEx1XAil2cttNMpsFElaRnQoSnQOfCZpSkM68TlgNlxxXhmsDsWJ17NZ2WXF4lIIafDZbOWaCTyMVMMg0hEQzxgctz5ChxIjr6i+DAfUvxBTcAAdYXjzJjxw79146unHMTa+BzW/BB3WoKMAMAaucqB0fclOIf4czURQJoLSEYyJoKIQThDADUNktIanNRwNKF1/l2IiIAwMan6EWqQQIIfI6UXg9qGielDU3ZjwuzBZ7UHFX4qqvwK1YBkq/gpUghWo1JahWl2KCkNlCSrlAZSKvahU+qH1Kjg4CFqvRKSXINC9CFFApPIInTx85BAigwAeapGDaggEcBGqDEKTRahzCHQWtSCLSs1DsawwWQwxPjqB0b17UCwWEYQGEVxEykOgPASOC+160Cqeo3gulOfWvwzxHERKIYCBryMEYVgPkUZkUE/veNAK0EYjjDTcTA5PPr0J1/3LDbjp5lvjd3wKGccFYBBGzy9X0be5BAzhW/RXnhNo+yNEPbHB/Ha9kY1mu8Hy0sqw6yzE8ncQBA1LeAcK7PaNtpitHNIXJC1bh3PBC6oTYoVQYjYiFSb/shOYqxKZxxEzInaAzcq2Ow47DTs02fnQ6IxLG40lDX+cxx79tA0q/jAh3rdj4j6C3YxBPQ4AXJed0fMvebds3oJHH30UIyM7UCqX6mtFygUcB47nwXE9RCZ+ua4cGKNglAOjXBg40HBh4MHAhTH1js1xPCiVAZQHozwYJwOjXIQREMFDCA+hysJ3cvBVAb7pRi3qgh91oxr0olTpRaXcg2K5F8VyL8aL3Rid6MaeiS7sGc9jotyLargMldpSFMuDqFSXQGM1jLsexl0L5a2Dj6Wo6H5U0Y+q6UcN/aiYPlSiHpTDLkz6BeyZdLBnAtgzCeyeMNg5qjGyJ8D2XVUMjRSxdWgUz23ahi2bt2HHjlGUKgahycG43YDbBTgFwM1DZfKAl4XjZmHgItQGkTaIoBAaDR3bPIqi5CDY+uKmBnQ93nEcVH0f1ZqP4ZFd+OnP/gs/2XBH/GGIghO/w3Pc+sDH9iXpj5wN8d5JWY2o+8PzcawnhBFL06xPpNNO/yWPLJ/lwGofjLUUKOW2697+jrQ2gHpmZzwb2LZinF3GbDDvTkgWbl/b9/xLwW3GpQNIg1NY2aPXG8HGUZN0ED63+SA4GrCdcLaQ5ZAWBD3JkyyD13a59j3jZKWQoE5sfdlxti6k89k0m4K2cbjpMd6fowCg/v4jETf+y6/pgloNW7dsxjPPPovxYhFlP8DoRAmZfBd8rVAJNEK49dG/k0VoMqhpF7XIgx9lEKkCQmShkYdGfSZhnC74OoeayaJqMqgaD6HKIHIzqGkD42QQOR4qoUItyqAW5VALC/D9AkqlDCYnXUxOuhidcFGqFrBrj8HwzghbdwQY2etg53gWO0Yz2DWex0RtEJVoBSYqS1AMlsPpOwZdgydAdR8BdB0G03UwdG4douxa+N4alM0qTPjLMF5bimKwDBO1pRivDmCi2odirR/lsB81049QDcJklsHLL0OusAy1mody1cXIjjKe2bQLjz0xhEef2IZHHtuE3zz0BB588P/h4Ycfx0MPP4oHf/MI7rvvQTz2/57AyI4dGB4ZwdC2rRgZ3oadO4axc+cO7Ngxgp07RrBzxzB27diBXTtGMDI8jF27dmF0bByj45P4f48/jdtvvxO7do417E1SmDqKRopPyzQynn5rP7PjlPUxj4Sddz5wxNdxsj6k+f909cSuW/wrZZBp7LRp5TUD08qy2Xa1qhtbRhO3m2w7bdAe0i7N+Lflmwvm9WGC1hoTExOoVqtQ8fsWMiMFVEohm82ip6engVklXo4xLU9BLpfL8H0fmUwmdT362GOPxZ/8yZ/gggsugDEGmUwmeXfixCcdq3ga/Y1vfAMf+9jHkrJZlqQHAPl8Hl1dXU0rBHHwwQfj0ksvxZvf/Gbo+FNsx3EQhmEyeoviUwv++q//Gtdddx1830/kRYoxm8HmefXq1bj22mvx6le/GqZJJbch7UDZ6IRRFOHhhx/G5ZdfjmeeecbKacNB/8AACl25eGlCQyngz973Z/ibT34K9dUzU58FUTQTAQp47pmncftPf4JHH7kfoV8ETAQTabiOg2q5DB1FcOL3EDqKYtr1T7Xqy0kOQl0/vkab2LcMEET1opQJ4WofS3s9HLl+KZb2uPBMDQ58+H4NJtIwYQhPA24YwlSr0GFYb3TdulxOPgOjHPgmQq4rj3xXHl7GQyaXRVdXFzzPhYFGb18vlqxaiUzGQ6RD6KgGpeMZn4mgIz9ZYjQm/nTaD2F0/RPzeEETOgKiyIWOCghNN6pRH3aMKeyuZvGr3z6HX/7mSeyeqEIpD0ZHMGENjvZh6vMaRHBQixQqQYRAm/hL7tjWcKHiryuUMTCm/qm7RlSfXcKB5+XR1zuIvu5uHLx2DS5/3yU475zX1D/7FoMq6bPf+c538IEPfCBZQtNao6+vD9/+9rdx0kknIZPJNPjb/oJt27bh6quvxhe/+MWW614zUK5MJoPu7m5ArKwg1gnTOI6DFStW4C1veQs+8IEPJO2C7NyaQYkPJbLZLIIgSHjfuHEjvva1r+Guu+6ys80aLKdSqaBarTbVDdubl770pfj85z+Ps88+204yJ8yrE9q9eze+9KUvYfPmzcmnmiRnxEjI8zwcddRR+MhHPgIII0rnJkZGRvCzn/0MQVCvwHL0IjuHUqmErVu3YseOHUA83WfHJ53MGIMnn3wSv/rVr1Irh+Th7LPPxkUXXVQ/lmYaVKtVbN26FcPDw0l5NKSsuK7r4te//jUee+yxBse0O97pwDxM39XVhde85jVYvXp1S/klyCfB/Lt27cIvfvELjI+Pi9SNUEqhq6sbn/pf/wuDgwOo7+Wpf3513HHH49RTX1F/H2PiUxIiHS/dRYAy+Pfv/Rtu+fd/w66RLcg4IWqVEsrlEowGslkPGaf+m0DQBrVqLX7/4CEII/hBgDCKUOjqQhiE8MP6e7G+/gHAzaBYKqM0OQbXVHHIqn6cftIRWL0kD1dXkHEi6CiCDiMg0MhqICyWUNyzB0G5XG+gHQf9SwdwyFGHo3/5cqhsFm4+CydT/1TOcV04caMbRiFcz0N3fx/yhTxcz4PruXBUfRZYf48VAKZW37dEdRuuXQbJ3qYo0Ah8hTDIIojyKPkZjJYz2Ly7hh/+12/wX796FLsnfEC50FEI6ACOiQBjEBkHxjgIjEKg6598wAUcF1COqnc+KlNfXDOof62nonon5TgIjQPXy2OgfykGevuxpL8XF15wDj7y55fBQb2jtwdpSik888wzuPPOOxNfMvES8bnnnovly5c35LH9bV9iITqho48+Gu94xzuwZs2aKe+1ZFqewLBly5akXZipE2IZ1KMcWAPA4OAg1q5di8HBQSvn3GCMwTe/+U388pe/RK1Wa5CBPPDvftUJPffcc3jHO96B+++/P5mF2AYAgFwuh1e/+tXJ54SMZ2NsOwWvbYdmesdx8OCDD+Kqq67Ct771rQZazD/TtSyHce9///vxyU9+EkuXLk3SpOHpp5/GZz/7WVx//fUJ75J/dkS2TJR1Jge0YfMs5WkFtn4ZR13SdtNBKYX+/n489thjWLFiRd02cSdUf4sQ82jqDa7RGo7rxBEan/v7v8N/3PLvyLkaAz1ZFCdGsW3rNmhj0NPTjZXLVyGbzaJSrqBarWFwYBCFQheKpRLGJyZRLpVx8KGHoFqtolKpoFQuY/3BhwJeFiM7dmJkeBscXcFLDlmOV510BA5e2YO+gkGm/t0XVGSQhQsv0Bgf3oE927YhrFTq3zG7LroGevDSU07GwOqVQDYL7Tr1A3QcBwZApKP6hk9joBSQLeSRyWbhuB6y2Txc14OrAMcxUCqAMtV6ZxQfw6OUEx9GGkAbH1EUIvIj+HEnFJoCajqHYtSFzXsC/PD2+3HXA09i72QIA6e+AdhoKKOhNBBqB9o4CHX9rDcoQLn177INNCKj6htW60wDysB16n6nHQdaeXC8LnR196Ovuwf9PV044xUvwz9e9cn4+J/YnFadSfMlk7KsLf19f8BCdEKvfe1r8cUvfhEnnHBCUq/lQJllbN++HV/96lfxyU9+MnnWKuR7MbYzSim8/vWvx0c/+tGGrRpzBWl+6EMfwte+9rWm5/UxXbs7oenXnWaAir9ukV+N0PFs55PGkXEynVQy4xknr3lv07Tzynj7mc1vWvqZgsyrUtZPbUzHezMo0cnZdFn+TIFpZR7SbKUDIvjJboPdhK9yKciA74JMfGdQLpWQz+UwMDCAfL4LA4NLMTC4DEp5cL08egeWoKd/Cbx8N5xsHoW+QQwsW4n+JcvR1d0HN5tHV+8AevoG0dM3gIyXQzaTQzabRy7XBc/LAXARhgblUgWu42LlsuVYtWI5Dlq+HGuWL8e6FSuwenAQS/J5LC0UsHrJIFYsWYrB/n54XgZQQKVaxWS5iInSJIqVIsrVIirVIiq1EoKgDGgfWtcQBWUEQQl+dQx+cTeiyZ3QpV0w5Z1AeSdQ2QlURoDSMExpCHpyK8LxrQgmtsIf347a+Aiqk7vgF0cRViYQ+uX6YpkCVCaLbFcXevoH0Dc4iL6lSzC4fBkGVyxH39Kl6FmyBN0Dgyj0DaC7vx99gwMYXDaIZcuWYsmSAfT39aG3uwf5XB6e6wGIP6mPNCKtYeJP6yNt4Aca1SBCqVLF7r1742PkGpfSJWxftJ8TzeJfaJB12v5rP1dWGzFdYBpYqzWse3xG+u0IpG0Pkhfalq21hDPAVqCtIPuZsT5OkGlIj8+UMBy/fpPp7fzG6sFtnrhkZ49Y0vhsFmQZpAfROMs4/rXzkp/ZIo0G+Z8p2HIwf6u81Dcvxn9j1Pfax/9MfGCoEnwaA2iNIPARRhGCMEKp4kOrDLr7l6AWAYFxMVkJUI0A7WZRCQ32TpQwWqyg5EcohxrV0GBk5x7sHp3A+GQZxWIJu3fuwvjoOHw/glJZRJGD8fEytm/fid279iIMNBw4cJQLBwqONtA1H2GlDBUGyLkePC8LPzTYMzaJ3aPjGJ2YQLFSQrlWQSUooxqU4EdlAFU4qgpHlaFMESYcgwrH4IR74QY74fnDcGvb4FS2QVW3AtUtSVCVLQhLz0GXt0KXtwPVHXD8PXDDCWR1GVn48JQGHAeRcmEcr/6BBhQipaCd+l9fG9QijarWqBmDmo7gG4MQGqEOEEQ1GB3VD5hVDlzlwlVe/d2Qif1E6/pXh6Z+yKxRDtxMFnDdegeF+nKe7Rv0OQn6ejOk5TnQIeuOEV+KGXG8j52W1471Nd50geDsSnZEMr/kZz6BtNiGkQd5vVCYVyckFWasLy6kgmA11nZaXjeDEd+2U1GMV+K8KTlKkIFpmR7xS8U0ZbcSJF/Mq8RLRLt8litnENPJmwabjvzk1S6vWTApesdsGwsTH/ECwFFO8rMMnBGZeIRtUG/woAA4CtVaDTt27MDo2ASgMigWq9i1ewyhcTBRrGLX6Di27diJXWPjGC9VsHV4BE8++yw2bx/Cnolx1LTG0MgO7Ny7F2PjE/D9Knbv3oHhkWHs3rsXFT9AoBVGJ6vYvnMUW7bvxI49Y9g1VsTeYhXjFR+TtQDlIEQ1jFCq+ZisVFGtBgj8ENoAkdbQJoQfVuGHZej4zDcHZXiqBM+Mw9V7kTWjyOq9yJndyJnd8KJhqGAbTG0LotpzCMubEZa3IixvQ1QZRljdAV3bU++4dBEuKvBUFZ6qwVU1ePVdRPH7JCD0Q4yNjmLXzl3YOTyCkaH60TzPPfccNm/dgm3bt2Nk507s2rMHu/fswa7dO7FzxzCGt2/FyPB27NqxC6N7x1EqVuAHIYxRcF0Pnuchk83Cy3jwMll42TzcbA65rm6sWLUKy1atRKifr1f0d/trLP418Uxa3ktfYvwLCZSPHYqsW83kZbzWOtFXq0HWddpC0qTO5xNkByfLZTvLshYK8+qEYDFI5mW8sToKxAb0PC8Rnnl4TcWo+IwnFX/cwHR2Q8q9CpHYZyMNJ2Hizoo/rSD5Jc/SGGmBaWReba0HSx4l37Z+poOyTkfgFzhOPDKijmynSgukIfmeDS/gXg+n/ik2DKC1gVIuHOXGMyAFpep7hOrlxC9rY/2UyxUMbduOxx77f3juua0YmyjWl9C0xtah7diybRu2Dm3HeLGE0clJDO3cie07dmLv+ASK5TLGJsaxd3QUoxOjcD0Fo0KMju/BWHEM2gGQzWLSDzAyNondxQrGAo0dpSqGJ0vYWfaxo1zDrlqAcaNQ1Ap7yxXsnZiEHwTIZjP1n5FQERR8KFQAU4Iyk/AwjowehRfuQi7chYLZhVw0DK+2DU5tK7S/GUHwHIJgC4JgCGE0jCjajUhPINIlaF2F44RwXQ3PQz1kFLwM4HgayovqzxyFjOMAYYCgXIKuVOCEATJQKHgZdOfz6OvpRS9Ddy96u7vQ392N/p4C+ru70NPdje7uPvT29KO/bwD9fYMY6B9Ef/8A+voG0NfXh67ubuS7Csh3F5DJ5dDV04O1Bx+MdQevj3814/mRfWJ38U6WPqeUSr70knVW+uQLETr+qpR1EKINaFYfCVs/0wXS51dxDHa58w1cYaJshImPDJNxC4F5dUIUgkrmtYkdmIJFUQTf9xvutegolDUNZH7EMxbbOE48OrA7ATbYiE9P4AgOYimPDTjzybKYdiaQb45KSC+KImSz2QY6dmdIR23FsEa8s6EzOnFnS3qt8kzZqUNYa82tgF8sgp057a8NTFQ/a8zEpynUkzngG+5Ihwh1AD/0EUURurt7sOagNcjlCtAa8UFtDpyMC60A5XlwvAwcLwPPyyLUBm42B+MYOJ6DdQevxfEvPQ5r167G4EAv+gd60NPbAy+bQ007QKYAXzsYmyhj73gRo8USRksVFMMIbm83BtetxKpD1mDt4Wtx6FHrcejha9Dbn4Xj+tC6jCiYRFgbQ1TdA6e2B9loL3JmDHk1hjz2wgl3QQW74IR74OgxOGYCDorwVAWeE8BxDeA6iNwMQpWBhhdvsDVwlIFSGsrRUJ6B8nT9jDoP8DxV33FlgIwDuMrA4YkQRiEKIuiIo1edfAzhOvHgwjj1FVADhJFGGAbwwxpqgV/foOoHiHSEMPBRKU6gWpkElIbruAAUXAeI4vP+6B/Sx2y/lZtVWYch/O2FCFtO1lNbXtmu8dqImcd0kOl5L6/bqV/SDYKgoXM1TU66aDfmJYWtUHkdiTOhHMdp+DbeiOk+72XjSAMzjkaUkHHMF8WfMLIzcuMjOmw+JS0+a8UxCJnecRxks9kpDkH+pA7k37lANWkYWFazAItn5iUvrcqu3HgfmIo/A44/OnCUgus4qH8oHMB1TP1rZIO4E1L1X/V0AECjr6cLJx53LF5xyskYHOxHEERQyoXWEYD4K0fjwEEGjsnCaBeOyiLSBnAAjQBuxkOhUEAh66I37yHvKkRB7fm9FMqDqzw4cIAoQhhUUasVEURV5PtyWLp2CZYePIjlhw5g+SF9WLoih4xbgasmkctU4DpVKFOGE44DlR1AeRgZfw/cYA9MuBueMwFXlWFMBToKYTTgwUEGLhydhTZdqEa9CNwVCNzl0O4gNPJQyqt33qh/yeY4GsoxCKMaXA/o7u2Cm8nWv2qDrndEiDt57cB1PEChfhCq8hEpH1rX37dFWgFOBnCz0Mqtqz8+hNXxFOA4MEYhrNUQVcvQpTG4fhEFD/Dc+scLdYs1Hs3DBk82fPQr1mvp17avzRQk5H07G0BZF1iGjKOcdt2xA/dCmrhdopxyfxTpSFAnsjyIQepMncp0OpsvKI9cbWJcq23DfNBc6jaCiqPiHfFZsO0QzQK/wKPhfd9PNoDKn692xKjMxD05adARjDFJx0GeOFNqxcBKnHkVRVEyQ/A8r2HGx3I5c5G8tQolls8QO7Mbn+OFFOdMC1JOR6wxz8nBGutWvadRbLwQ/8aN5mHMiYtpYxBqA60AN+PAy7rIF3LI5fIwyoXjZqBcF5Ex8afQCtooREYhMg40PGjUZ0kaBuVqDVoDXYUuZD0PSpt4n5Kqf9KMuo9xk2fkVxGEZdSiIirBOIr+KIr+XpT8PShXd8PxAoS6hghAxTfYM1ZDsWwAeMhmgJwXIquqcE0peafjoApH1QDlQzn1Q1p1FNR5Rw9UdjUq5iCU9CoEziCQ7YdxCwi1W9+karLQKo9IZeBku6C8LBzlQTlZQGXrxyA5gHJdeF4O2WwBuVwe+VwO+VwWuXymvscqm4ObLcDJdMWHlzrwPBeZrIdMxoPnOXAzLlwvA8/LIONl4v1b9U2zUejDjT81B/hOb3oYY5KzBOlfOl4ah2jEZB1uFkiP/hiGYTLrZ32dL4wY1edyuYZVE/LAsqYLnCmw07B/R2k2/NrtAa/lwJn1n9fZbBZuvEQ2p/q7H6L11nAesBs8J34nRGPRCaYDG06IToA05MtRlkNnoKPbjsHlOpmvVaPSQWynYWcjnVmWL3mbDYwYpag5HNsh+ZH8QXyg0S407BmKd/Cb+nypvptSOZgsl/DUc0/jsScew57xvdDawI+i+rlxBohMfB6ajuLTEQCgPoo3cTc3vGMnNm3eitGxSZRKVUwUSyiVqwhDA8fJAJGCCxcZ14PnKjgI4cCHYyowuggdTiAKxhDW9iLwJ+EHJdQig8hdiseeKePHtw/jrnt3Y2h3iJrJwbge4BooRPWfjjCAawJ4KkAmE8LNBlCZAHA0jPJQDQsY3uXiR7dtwvf/80nc8/B2jNZc1JCHr/MITA9C04+a7kXF9CBwuhFEWWidg+t1w8v1wMl2w2Ty0F4OxvPqM0mF+qZXHQKhgY6A0LjQyANOAY5bb2AzroLn1H/mIv4RiPqJFKY+S43CCCYeHFSrNVTKFRQn6z+f4jjy+8d0KPGOkr7EuiDjWoFMG8U/h8IZB0M7wPojN8KTdqv82vWZbQ/zz4Zf2R7wmnRlOSZ+N6OswXir5ezvmPdm1Xe+853YuHFjgxFskq7rYuXKlXjTm96UNPxMQ+VzNDATqPg9e/bgoYceavgNHFkuO4ZMJoNXvepVeMtb3pKMXjgi4iiGPBx33HE45ZRTkM/XR4TNMD4+jt/85jd4/PHHk1EK4jLD+OgeHb+8vOWWW3DnnXcmy0SzXWKgrqifnp4evPvd78YxxxyTVKJWOjXptHR08r5161Zcf/31yekTaVBKoa9vAI8//hhWrVpVjzTx12+If/RbmXhzJmB0fdmIM6UPfuiD+Peb/g2u8ZFBgGzWRSabR7kWIowyqPghlKsBZWBM/d2GghMvXbmot8Ah4FXgIkDe8dCT64LWQKgBDRdhpGGCKgYKCi87Zi1ee+ox8KIJRP44dFiC54TIuSHyqoa8U0NWBXBMANfz4OW6YDJL4fYfip/8/De445fb0ZVXOO81fXjNyTms7Kmgz/XhmFp9E64y9d8FyihEroNIOXDhwkUeIZZjPFyJh54y+Nw/P4Cq1njFSS7e9UenY+2SLFTgQyGHEAXUTBaB241A9UCjH8gchGdHInz9pv/Erx99FBO1GoAMTOTAgYbj1ud5Jn6HVp91uTDGqy9hKg3PMfUOp6596PgUBRPFg6/AR6VaQeQo5Hv6sXb9YTj88KOwavkg/ukfPoGMkm/zmoONYbFYTE5OoZ/TX+22wAYbWYg2wHEcLF26FB//+Men/ETIXFEqlfDQQw/hwQcfTNoGxOU71qb4VrF69WqcccYZWLp0KbQ4CcHuIIaGhvDVr34Vn/rUpwCr05b6cRwHZ5xxBi666CJ0dXXBxINPOchm/nXr1uGkk07C2rVrk/j5wBiDD3/4w7j++usxMTExpR3nvVqAzaqL0gmpeOZCxcp4tOCoEswTxR87yOk/xJRWxY1zLpfDxRdfjM997nMNedkhSb4zmQyy2eyMjmiMQa1Wf//AeyWmzVKe/+//+//w1a9+FdVqNSlPdgCtgDp1XRfLli3Dl7/8ZfzO7/wOVIujIVYM2TCYuPKFYYj7778f73vf+/D000/bWRMopdDb14/HH/9/WN3QCZm4E3LiDqc+c9Gm/sWcE3dEH/jgh3DTd7+NrArQkwcKeQ9wXBg3iyjMY9fecShHIzIauv6JVnwMTn0GVX9PEQJuGQ4CDHR3o7+7D46TgVIZaOWiVgtQmRhDVldw+klH4OxXHgcnHEWtvAd+bQyuqiLrBsirKvKqWu+EEMJxFTL5LqjcUpj8evz4pw/jv+7ei4xncP5ZPXj9GQWs6q2h1wvgmhCOm4NxgFDVYDyFyMkg0i6UyUChgEANYCxYhl8/5uPjf3s3tAuce9ZSvP+952PNkhycoAZlsqhFOVSNhyjThWqUgUEXkFmGTSMBbvjeD3HfY4+iWKsBKgNlXCit4SCqH4vEk8WVA2McGLiAUXCh4an4B+ng1Jcv4yU9E9VH0jrwUQtqCKGQ7+3DukOOwNq1B2OgJ49/+T9/D68+Z22pE9Jao1qt4vd+7/fwyCOPwPf9xLeYplXQlx3HwcEHH4w777wTvb29Lfn4TDDGwPf95KtYGS95baUsyuS6bsNPWLB+2zTSOiHZscr6+J73vAef+tSn0N3d3VBHZXrEZ9XZy4rzgdmHndD8hxgtQseHnU5OTqJYLKJYLGJiYgLFYhGlUgmTk5OzCqVSKekEqCAqTYm1WRM33r29vejp6UFPTw/6+/vrn6p2daG7uzsJ8t3STMjlcgnNvr4+9Pb2oru7O4nr7e1Fb29v8im6EdNszLJyEnSCQqGAvr6+RJ6ZgpSdsjKuv7+/pUNbASTvfjjErt9JOeoNH+DUT9Ouf8kNDUBrgyiIEAURXDgo5PPIZTPIuh4co4DIwIQGoV8/2BSmvkRRPyA0in9ED3Gov4+oRT6qfg2lahmlchnVag2+H6JaDaA1f/bCgYl/wsD3Q9Rq9YaoWqvFX4uFqPoBgqCMoDqGqLIHA/kQh60GjjssjzXLc8g59d8piiIXockhUl3QTi8Cpwc104Mg7IPWS6HNckRmOUIMwng9WLJyJV792uNx5u+8DKec9hr0Dh4OlT0ITn4tVG4NnNwquPlVUJklcLxeKDcPOA60iRAGFeioCmOqgKlBmQAOIihT/0kGow20jurvoCIfKqrCMRVA12C0Xz+3Lj4yyBgNHYXQOqp/wVjfP1z/UcL4Jzii+GifmZvg5yEbW9bhUqmEUqmEiYmJpL5PF9gWFIvFhrhyudxQp+cLpRRyuVxSVxn6+vrQ3d2d1GO77thB5u3q6kraGjmgbRVpjTzbKvLW1dU1hWeW3a4OaF+jhZanPWg26tezeBcDMWqAqAR2r23PRiRsx5adQrM800FWRHY0zRp0Pp8NlFgXJ39p8k8XHOurJimzimepUo7maKafuFcCkp8BYGum43dCjgI8pz6jqflAqRhicrKC4mQZlUql3jjGX4RpHSAMawiCCny/ippfjTuPAH5NI6hFmJgoYmRkJ4ZHhjE8MoztI8PYtXsXiuUS/CDEZLGESqWKYqmCcsWH7xsEoUIYOggjD1GUQag9BNpFNTDw/QBBrQTlj+KItTmceeoAzjx1EIeu6UbG9RCZHHzTBR99qKkB1Nyl8J2V8NUq+HoVQn0QNNbCeGuhsquR7V6O9Ycdgne882y87Q/fgJNOOg2h6cNYKYfxWhfG/TyKQR6VIItyFajUNKp+WP/gplqGXylChRW4kQ8n8uFEIZzIwDUOHKPqf+HA0QZK+3CiEtygCNdUoBDUf8lWh/EPAmroqN5xOcqFglOfxCoHrlv/Ws/AwMtk6ueszqIeKLGXT/pla/7UWFZavbTj2wUllrFlWXbdsYNMz/ozFzCvXZf5TKaZzarJgYj01nKBIA05V8eyja5iZ0ozIp871qeXdtmy07Dpp8GWwYiX/nZFZPmEXXYrkKMsXs+GjkxLx4YYGLROz8QhBVRbnIQp6wfBAPlsBn09Xejt6YPrFlBNOgUNowy6u7uQzWWQz2fqy3K6hjCqIQxrCEMfoR8g8kNENY0g0AhCU5/xmPoI3hiNMAoRRAE0NMqVCsaLRYxPFFEq+whCB9pkoFGAUT0wqgcRuhA5ORjlIQwUoA1UVMJBy7I44eglOGRtN/JZhSB0UAsLqKEfVWcpKliCMpbBd9ci9A6GcQ9BpNYhUGsQOKsRucsBtw/5ri4cc/Q6HHbwSriOh117KhjZW8X2OIyMlrF7rISx8UmMT0xgslQ/WbxamkBYGoMbVJCLfGRDH14YwIsiuFEEJzJQkYETGXjQyDsavZkQ/fkIA10uegoZeC7qv0Krdbw36XkT8bMDpRQK+Tx6enpRyBdQKBRSZrfNYfsV/9LHWqlLEqybso6y7rYD5E026LI+tFIHSEPKyTo5W5nlYFnKK2c4Nm+y7BcKFqQTsg1BhcmGOQ3N4tGkUee9iaexzcqRBpTxpGPElzJzMa6kSQeynUvyNJtyjPj6TzqqLSPTyr8E8zZ73tIoS5jm+dz1pRwbJgn1WZCCRiEL9PV46OnxkOt2kevy0NOfRb7LQSar0NXlIpNRyHgushkP2WwW2WwGnlffQOm6gOcoeKq+H8dVDpTjwKj6UpJBfboVRREibVALaqjUqqjUfNQijUAr+KGHauCg4jso1VwUKw5KFRehKaBc8xAEGYQ+UPP9+Cy2COXAQdHPoxj2oqaWIvSWI3CWIFRLEDkrAO8gOPlVMNklCJ0++OiBbwoITRZBLUJYKyGsjsOvluq/laQc1CKNsh+iGkQIohBGhXDcAMoJAFOFp0vIO2X0ZQ0Guxws6VLoz2kUXB9Z1OCZGlxdgYrKcHQFS/s8HL5uCY478iAcffgarFyxFPlcFo5S9cVQXe+koyhCGAYIoxBR/PPduVwOA4MDWLJkCQYHBuCo+nFMrUDWJ+n3tn9hhrpNOtKv+a43jRamiZ8Jsi7MpYMjf+wsZJ1qxlNaPGWW1+TNro9GfB1n52snmtFjefJvs7RzQWveNgekGZdLb7aSkTLiSbumodMMz79y1kFI4xnrpaDkhY7QCtLSSrp2mYyz07UK0uKszy7biE+vm+mYNBDzQj5a5ed5O9RDvWeKf2WVThm/b1CxbuvfuGm4pgYVjUPrvYAag8pMItSjiDAG1y3BmCIcx4ejImSzGfR09aAr3wXP8YD6WwsoFdXfjRgDGAeem61/hWc8OE4GjuPBzbhQjkalWkGg6z/gprwMIuXBh4dq5KIcOCgHLqqBh0rNRbHooFIrYKzoYrTkoOhnUQyyKKOAMnoxGfWjqAcQZJYBuRVwskvhZJbAmAFo04sacqgpF75jECoNoxRcZJB388gaYLArh8G+LPp6HeTyIdxMAMcNAOXDoAKoEjJeDRmnAhVNosurYP2qbhx3xFK87LiDcMoJh+CYw5djaY9Gj1fGsp4IS3oN+rtCOHocq5blcNyRq3HScYfgxOOPwkGrV8LoCFEQQOsIQVCDsjeQRhqOOGIpm8mgr7fveWML2CN2WdfoU9If5eAOVl2RPid9kX7rxEtP/Pxb0pH3af49E1he2ufftgytQsol81NPUge2PtixUI/Us0wn00NsVZkrv2mweSUoE3mXtrNtYOedDVprfZqACoTY7AnR2Ekhpgsyn8xD2IbkPRXhWD/4JPPwms9kGgiFSprtChAV2Agnm+1n2jOBZfErHTlq4nPqmXEQHbAWHVvTIMpxACiF+gtvY6CNQWQ02BHFXzDDddz4dO0AnlNBBpPImnEU3El0e0Us7/OxdnmILncMCHaiP19FfyFCl6fh6ACO0egq5NHf241sRsF168fTuG4GrleAcgpQKgs4WRiVgYGDSCv4ERBEEeAouNkMvEwWynWhoRBoB7XIRaBdRCYLgxyCyEXVd1CqAeNljdHJCONljZKvUImyKAcZFGsKxXKIKDTwXLfeEUYBgqCEIJqEUSU4XhWOW4IJx4HqOAo6QLcKkdVl9Hf5GOipYUlviOUDCisGFJb0hOjKlOHpCZhwAiosw0OAgZ4cXn78sTj91Jfh/2fvv+M1O646X/hbtcOTTk6do1pZrWxZki3JliwHMLawCcYGjG1mGL3zYsyYGZjPfQffAQaYARsuMJc0ARuHIQwzOAknJUtWzqHVOZ/uk+OT9t5Vdf+oXU/X2TrdfVrdtuX36tef6rP3fmrXrrBqrQqr1rrh2it4/bWXc9G2DaweqnLh5mHe8sarueHqi7j2sm2s6q8Smoz24jxps4HOUrTK8tmO9aVkjEEbq6SQJC3a7ZZtc0sdVtnBKLTKOqolrpeYU2i6ObpxNO3iOlr3+5ovYNw9hf4oljlu4Oiv+MxPy/WvV0twcNcu/37/c6rXLp7wDsC7eiim+90Kro4d7/Xz7/IivPOJjsc4ujhbnJtUljGxUSzoSoL/XpFIi8TqP3fX7j2XVvGZO6Dq4L5RrHyX5tkEvzOJwr4QZzD7WAnEMiMil49iXRhPIPtrz8W2eHlwAtXOdIymY+/NLobl0x9jD+NnmUJjXSkIodBpk5JIWN0NV22VvO/t/Xz0Zy/nzp+6hne8oZ/Ng4IKTWgtEKWLVEVGVxxRK8f58lxAratGVK7QTDIaDQW6RLUyQG/3MH29Q/T3DzM4NExffy+lSoU0zRDSukIgkCADZBgRhCFC5orIWqCVIDWaDGgpWGilzNXbzC20aTYT0iShtTDPwvQY6eIEsZqnzBzVaJpaaYZKPE0oxhD6CKEZpSwmKJspanKGMJmgImeoBmN0hWMMxDOsri6yvrvBmlqdgdICtaBOmTYlAaEymHaGUNBqtpidW2B2ZpbF+VkiFJvXDLBpVS+XbN3AVZdeRH+ti/FjE8xM1ZmZbHBg31EOHTpKs9nuKIWAFcrGaGuaRxhEYGlAZ4osy8jSjDRNrKBKW96bJ5iRC3hChQItO3qTnpVp98zRW1GoFWmNwgDJMT8X1zFF/7vF/vf9DC4/Ply+3bV/vINlVolc3X0vgg+/nvHK4bdrsb2Mx1NeCc6KE5p8BOSIwc+IWGbv5mTBL3CROZtl9nsc3LtOsPgN53/bVZyT3kWB6Rq/WLFnE6RnaoNCR3b35woubfI6cOVz33AdGa/OXJn956cOdvSGUQhpPBVsA0JYJ3ZYihIBhGFAgERitbc2rS9z6xuG+ekf3cyHf+wi3nH9Oq7YJLlmW8z7f2Q7/+oXruXnfvx8br2hjzVDUAoSQpEgUYSBpKunhzCKqXUPsO38y7j00ivZuHkbA4Or6O4doLdvgP6BQQYHhhkeGmZgYAhj7Kn+MAyRgXXDHUQSGQbIQCKkIBAQhRDHhig2hKFBkJElDZL6LEl9lqw1T9qcoTV7nObMEdL5I6j6EUiOItLDBOkosRmnxAShGifIJoj1DM3Zg5TlHCSjyHSUKDtGSY1T1VPUmKYnmKUnrFMN2pRkRiQ0gRSoLGN6eprZmXnGjo9x8OBBJsbHyLImUmTEgaJaAp21qNcXicMSGzdsoVzu4uChoxw+fDRfGbCHUzWWJp09ukAIpIAkbTMxOc5LO17k8cceZddLOxEI4jCygwmPZh1dO7g+JQpu7V0cR2N+fP9Mn8j7peuzfjyRzwrc6obr/y5tlx9Hu+e6755tcPlx5TJ5vn2rJ3irR+6ZyfmGW9EopvvdDH5fDzxrNNqzCuPq3s+fe+dscFaHVScnJ/nLv/xL9u3b1yEYCkzRZ8Qng8kFBPnBrrvvvrtjF86l5WdTCMHAwACXXXYZW7du7bwv8g7hKkbna8u33HILH/jAB5Z8z8X1hdWOHTt48sknSZKkE/dsYIzhb//2b7n77rs7J8ndcyekzgRCCFatWsVnPvMZbr/9digIM5euK9c999zDkSNHlpiCd1C5eRRyCxC7d++m2Wx2fi9CCEOlUuG3fus/0Ndj/dprAZk9BWStORuBUMKuyoUynxkpBFM8f/+fk47dz2B5lkjNIE0T7eZKYYWg0kdT1xifLfHIU7Pc9/BB9h2dp21iFBUIKmDKrFu3mbhUYXp6miRpk2UZSe4wTwhNQEKZJiPdghuu2MJwbwmj23amhiEMIDYpoWoR6SaRbiJEGy0aGJ0ijATjrA0YgjC2ttoiQzVKGO6rMtzfRVgymLJGSWU38nObdUoHGFWiHA9QLtcIQk1Kipap3YMxAdKECCNRChopzCaChaRCmwG07ubYkWmefOZ5RKXEXKvJ/EKL+kKDpD7P5jWDXHXVVSSUGJ9v8oX/+TV6e/u47aYbadRb3P/Yc+w5NoOIqmglrCmkwB5u1UqSJCkmS1FGIUsx5a4eSpVuAhFx2cXb+Nr//iyB0MDSg5eOWe3du5cHHnig85vWmna7zac+9SkOHDiwZIC3XL+97bbbWLduXYfx+ulrbyl5cHCQX//1X6dSqSyhW9dnnBBsNBp8+ctfptlsLvnW9xOubly+2+02x44d49ChQx3himfVxTF6YwyrV6/mvPPO6/Tl7wVcve3fv5/x8fElg1jHS1xbSinZsGEDP/ZjP8Yll1yypG1eKc5KCKVpyujo6DkhAFfIhx9+mF/6pV9iYWFhSYP5xApw0UUX8XM/93O8613vWvK7L/RcpfX39zMyMrKEOBwx+8Ty+c9/nj/5kz9hdnaWc4WJiQlmZmaWCByXr3MhhBxMziQcMRtj+OhHP8p3vvMdWq1W531Xf64upJScd955fPSjH2Xt2rVL0jwBy5KFEGzbto1Q2kO9Wliz/wZtXQ4YgdC5CZ9AoLGacYIxjj33GVpHvkYpPUqQzGHSBloojBAQhshyLzoeRIUbmZgf4X999Xnu+c6LLCSSlJhMh9RqQ1x80dUsLCzy0q4XqTcX0frEMqvRipCUqmwx0h1y+/UXs35VD8JkhCHIQBBHkpLIiFSDQC0SZHMEsommgckypIaAAIO2BleRBHFErSzoLiv6u0L6usrEJTAVhZGKchBilCA1Iakok+kamG6iKKKnTyKDDGUS6/FUh6AEaIEhoKUi5pKI2aRCXfejTA/7dh7h2ed20LtqhOn6InMLLVqNhIWZSdYO93DV1VeTEHPw+DRf/vq3GRga5i03voH6Qp1vP/4ce4/NoGUZkRt8TY0V9sYE1qKBsgoLYblEuauHMK4iTMj2i7bxrS//DZgUIa27c0cn5DTzxS9+kX/7b//tCcrIae3QoUO0Wq0OfTnG5QulKIr4wz/8Q2644QZKpdJJGZjI7dJt3bq1c+9o1+/DAGNjY7z//e9nbGzsjPvTdxN+nQ0ODvKWt7yF9773vZ0Zhovj8yGAu+66i09/+tOdQfj3Eh/+8Ie59dZbKZfLS3iJXxYhBOVymZGREarVKnh5f6U4KyFEYUTt4CfpiOdU8Ansvvvu44477mBxcbHzzLc64LB9+3Y+/vGP87M/+7MdweMTaLFx3WjDxfPvnQr0n/3Zn/GJT3yC6enpzndeKZyQcaOZ4gztlUCcQgjh1burh3e9613cc889NBqNJfH8+hZCcP311/NXf/VXnH/++UviLcWJPIvckmZqrFVsIU2uAQdCCbseJyHNlJ0QyVFGn/5z6ge/TKTGCVSKMAqlMwgkMorJZBUdDiDL53F8ZoQvf20n9z3yInMthQ4iFIJSqcbmDRdikOw9uIexiQnqi3WUskuCaEOgEqoyZU1vzDtu2s62TSMEZISBAWkohRCLlEjXCdUcUs0QySZgNchCJZEmF0IYVBAj4ohyrOkqZXSVDLVIUipJRFkjA0VFWhfiLVmiKXtoZr2MjcHhQxO8/W1bkXIRKQyBsGmjrG08TUSiS8xnZeayGnOqj0x1s+vZvex4aTcj69czs9hgoZ6ysNBi/NhRRoa6uPa610O5iwefeJYHHnmaNatW8/ab3khjYZFvP/Ysu45MoWRMFJZACtoqIwOMkdZqRZaA0ZSqVcpd3ciwgjABl5y/lXvu+jvQGUYEubuOvPVzmv3sZz/Lz/3czxHkdhIdnTuaKi6d+QjDkL//+7/n1ltv7VgbcHDX7j3XT/w47t59UynF+Pg4N954I0eOHFn2m98v+OVYv349H/nIR/h3/+7fdeqoGMfV75//+Z/zr//1v6Zery8p+3cTjg/84R/+IR/84Afp7u7u/ObPxlwe/QG042lng7Oe7xWJxz3zM+ffLxd8onXXfnrLEddy77rnrlLx0iteu3h+Wg5+Hl5p8L/hOk8xb+cK7pu+cBOFfTEfLn8+gbl8LR+8NoTcrI49IfSyoggblCY/p2IfKgWZCUlljSQaoB0N0wqHaclhGmKIthihofo5dLzNo0/uYd+hSTIVIWVMIAOkMCjdYnz8APXGJH39VdasHWT9htWsXTfCqlVDrF41xJo1IwwPDdHVXQMkcVyiHJeIo5hISqv5ZXIdc2EIQk0QKOIQKrGgEgnKIVSikGq5Qq3aRalUg6CEiUqoICQxhkxrVKIRmcBkCp0plJIoKjSzCodGW7ywa5oks58CgdHCmstBoAlRRCgRWRcOcQUdlGkT0sgMLSVJTEQQ9yLjPlJdZq4hOHR8kWdeOsxTLx5g18ExFhNYbClGJ2cYn56j2c5yi+Uyd1luQApkIHKzPVarREC+j2fb1PqDyk8Zi2CJNiSFPlL86/9e7ANF+H2j+NynXxevmAf3zM+Pg//t73fw81Z87q7930/0teXf+24GH8U61d7elN/G/vUrHVQ7nFMh5BdquQI6uEz7cZZrAHevCtox7jc3k3Hx/Pf9RnVx/d/8WZHbIDyXcHl2jehQrBM/z0UsVy4KaRSvXVx37eprueDq5vREJHJSEShjl98MmkBCKAQBwhoaFRKkxDij2lJaVV8TMzEbsX+sypHpAUbnBzkyN8CR2UH2T/ax41CZZ/fAo88tcO9DB3n4yd2Mjk+jDNasDAJprIfR+sIss9PjkLXp66qwaniAVUMDDPb1MNDfzWB/D319NeI4Yr7RQCMIwpAgkHZDHhAiwBBhRISQJYSMrAkbAUFkiEqCUrlMtdqHMVUmJjSjxyHR3SSmSmpCktyNgs6Etd6QSVppiXZSY3FeMjG+QGAEUktrwi3TGK0xxlpuUEQoQpQIMSJCBmVEUKGlAg5PL7L7+CxP7xnlpdFZ9owtsG+iztEFxe7ji9z/xEt884EnOTQ6TaIkMwttnnxxD8/sOcjUYhsjI0QQWcvi+V4VRiCUsqZ8tAGsPTxjrEAWwhAE+ZkvoDi68PuZ9vwG+X3Zp7flaLbYX4u/+2ng7fv49/67/lmicwn/GyvhC8uVh/y5n38KZfLf8/tisT+6eCf7zrmCy2exjfz8+nlx1375XglOX8OngU8g7t4PxXjueXHU7gcXd7mRkYvvni33vFhRLviCx6GYxkqwXGMUA4U8+2V2z4vxT/bsZCjm3WmtuHeL33Nwv6vcQdeKv4cdyYswPwhkDNJgma2xjuS0EPkSHW4rCUyZl/a1+faTdb79VJsHnmzw4NMNHnymxf1PNLjn0Xn+6f4xvnLPIR584hijky0yI5ChRErrtbUUxvY7gEraNOfnaczOUp+dobkwR9JYoL04T31hlnp9nkZzkcnpaRpJGyNzJQMJYb50hiyhRQVFBU0FRAkjQ2QkCcsxUaVGWOolzaocPJzx4kstZhcrtE0/LWo0TUiiItoqpKHKLKgqDdXL/EKZw4cWmZlcZNO6PgIjUO0MnWq0kmhdQpsKmSmTUUITY2QJIctEURdaVNh7bIrnD45z/1O7uf+pXTz8wgGePzLBWNMwncaMzqUcn6rTahu0CpivJ+w8NMbeY7PMtjWKwDoAlFYtHS0wmUbmJ4mNVoBdNrXWyQ3GZEhriJvcfPlpacIURsQ+Xfso0mNxYObiiMIyuvEEF9733LVDMa2zQTHvJxNCJ+szfv6K+fLju/IVB+QuXRdXnELL+LsBlw+/LZcrk5/f4nbMmWL5Gj4D+BksVnIx8ybXtiiuia4ExUp3DWgKhLoSLFeZLhRHIcuhSATu3tXBSvPif89/r5j2cnVbvHblcXtPxXjnCnb0KRDC2l+25hPIVXrtpatBIbGWS03I5HTCS7vneOKZCR558jiPPjnG409P8syLs+za2+bomGBmoUwzq6FFFcIYGeZq1YFVYw3CgCCUBBIwCqNSyFLIMlAKtEIYBcagjabebNBsJSiDFZrOLbmQGCExxChTRZkami4IeiDqxkQ1sqBCmxKi0kdQGWB83vDSwTpTzSoLaoBWsJomw9TVEA2xhkVWs6iGODSmePalMTITc9mVF1FvZyjKpFRJdY2UXpTst+6+ZS866IWwFxF2E4U9RFGNuYWExbZmvpkxOdtgarFBPdUkhGQyJiNCyNi6cDASZUIyE3eEmhU3JtdbtALYGljK26wTsG2JVSABu8dnp69nhiJN+sHRpuuvvrJRkUYd/br+WIxT7K8r7bNnAr+/FdXKi/kpxl8uz8U4fr24Z65uxDJCpziYXC79s4HLi0vf1an/DbmMIlWxHV4pzkoIucz6mXMFctfur1/pxtMOW2nmi/Gk5/P+TCrBzwOFKb5PAKeC+55fLodifZwK/vt+8AWTE9h+3pYrr0vHLVEI79T1uYIrltbKGg61w+XOPpAQzlacFUgdPicFxgisX6AK6G6M6kGpLrTqwpgaQtQIwy7iqAshSyAiO4qX0r4vc09rEozUSGFZrcTkm/7k518kgZQIEdJotqg3mlbLzS09YTp7XIgQqKBFD1r0oWUfKugnkT00qLCoA9KoTN+6QSpDNR5/cZrvPHOMA1NlxhojzJqNzOiNTKUbWdSbOD5f5ZndM4xON9l62Tp6RoZZVJLFLKauu2mJAUy0irC2nlLPeso96yh3r6NUW0u5sopyuZ+AsvUKa0BnVrElCp2nVHsSWBhNENglNpAEIkLoCJXJvI08muqs6eslh1c7wkfkjQfYGdGJZjsdXL9xKNKve+bonFxDLo7jJbTsIHKfXv47Pq27a78v+H3kXMGl73iD+4ZfXj9feHzEf8fF99939WNyHuj4l3tPKXt4uFhu9w0Xf6U8ZiXw80r+HZ/v+N93z9zflfDL0+GsUnCVK3OB4irGVSgFwnSFc/dnshfjVwaeAFRKLRn9nw4iZ84yP0/jj8pcI58OPoHownJW0S7VqYLJ96OKBO5+14W1d6VUx+eRyUeUbu/JJ2R373f2cwFX5kDadjMmd/Dj5cdou+kdOKvN2nI1Iay5nSgqEwZVQlEhpIQ0EWiJVgKTGXSmEcYqNQRSEuSb6taIqf2u0ORjfI0xVjXbGFcPdg/EIEiSjHq9icp07n/HtZu2M7kgQMsYLaooUSOhl7bqpplVqach9dTQzBRdfT1cdOkFrFo3yLMvTPG3/+tZvnb383zr/l08/uw4z780zZPPHOYbdz/JkdEjvO51m7n0kvNotuYwQoOwQlHKiDiOiCqxXfIrhURRSBjGyDCmFJcJZYhKMySGWBqqAZRNikwaBFmTSLUJdJtAp+ikDUohEXmbBEu6tDF2787Rg99/rOzx+4wVVGcKR4/+QUyfBh0d+3DLwHi07ujf0bG79t91cRwTd/dn0udOF8iXtf1+5e797xb7lVJqyQFPnxdSWIL0+7tfDy6+n7bjk3j7zH5ezwX8vC0H29ftUQiWGVgU2+lMcdYcylVMh0EUhIXLrKtYpw4tX8FMyE/Hveu+u9J0XKNmuRtup/7t0ltpOnhCWOcCwqXr0jldcHWlc0Hml8U9c+V1xOkOnvrvuDr2iVTlnmfPhjiKCHJTL8ZY3zRCSsvIjEEKQRiEVmi4FwRWXdtOX2wd64yQjFAoImlDKTRUIkEtDqiVA6qlkHIkiCSUpKQsQ0pBREmGlGRohZPIlR5yO94y98RqAGXAGIEyAQuLDVLlBIHNq9NPE8bYpTkZoUWVVFdoplWa7QpJViLTIVpnSNFieLDCdVdfwrvf+Uauv/oiFucUu3aMcf+9O/nGPz3Dffc8j2o1eMM1m7j60g1UZYMgncc051HNeWS2iEin0M3DmPpeqO/B1A+gm8egPYtUTTDWFt3czDhpI0W22+jFOVico5ws0q2bdJkWZdWkQpuu2FCNBUa1UbqNDLTd18mtHdChtRNtaDo3xgpip/m4ZA1u5TTjPJU6MzR4giTIB5w+Hbfb7Q4tu35TpFE3uHJpOLi8O5p39O733XMRisw2y7KOkHX5cf1P5Gea8Oqg2I9NgR+6e8cL/Wfum2EYLilnMV2/Ts8FXFu5thCegFH5ID+KIsQyKyzFdjpTnFUpTO4yd3Z29mUeEufm5jreFefn52m1rNFEP7PFgp8KrqH8uGma0mg0aLVaS751quB7gHTv1et1FhcXaTabK8pLkLv1rVQq1Gq1jnfSvr4+ent7O55Le3p6ThtcOlEUgdeBHRGQN7LOBaUQglar1fFg2Wg0aDQanXp3ZWm32x3i8dM6WywsLDA7P0e93qBZb1Cv12nnLp21UnbzG7sFc6IqJV09vYysWs2qkRFWjQyybvUgG9aOsGnDKjatX82G9UOsXTvImpEBBnqrVEshpVASBpJQylxNOyAQIQERUgZIKWwInCmeCBmECBlgci29uYUGSaoQMrDaenZ1D4lGogmlQQplXYcb631UoxAoYplRClpEzBOqGapBg6Euw1WXbORtt7yOO374Zn7qvTfzM+97Ax/+wA382Duv4PILBugKZ4nUJBWxQFfYphom0J5Gt8ZJG0dJFw+Q1g/Qbhym2Ril3hin0ZxldmGKickxRob6uHDbMFs29HPR1tVcefFmtp+/kUu2rufayy7g2ssu4PVXb+emG6/jyisuoX+ghpQKrRNMbqg0l0H5SMDCeIxc5ILH0oeNdiZ04jNBY0zHY6/zPlqr1SiXy1Sr1Y4X3+7ubowxHY+rjoYdzfp8wx9AORp2weTCwvWTWq32sn51NqGrq6tTF66+nMBw3ybnA7VarePp2PV7vy56cq+oYRhSr9eZnZ3tlLfVarGwsNDhQfV6HaUU1WqVrq4uyuXyyzy+ujotl8vnTAiJXLCkaUq9Xu+0gfNw63u/npubo91ud/iRL4xeKc7qsOro6Cj/x//xf7Bz584lIyGfOE2+LHTllVfyn//zf4a8QX1h5BP/Pffcw7ve9S4WFxc7zxx8hloul1m1ahWDg4OdZysRIC5uGIYvG4VNTEwwOjraGQmdDBs3buSDH/xgx1qDq0KdmwnyO/qp4IQKwNe//nU+97nPsWPHjiVxis0TxzGbN2+mr68PCmkYb+lACMGePXuYn5/vjKQcXGdyuP766/n0pz/NBRdcsCSeD2MMi/UmP/ZjP8rc7KytN2N3Ft7//g/wSx/7pROGTJ3Kdq6EhUn4u8/9Bc8/9R1ikREbRSglIrB7PjqfvSSZop0qpmfnmZ6ZZ7HeIkk1xgirDm0EygiytI0yLZTJUFqgjURriVFYzS+dEJAS6iYjvRVuvv5y1o30EIs2JZEQS4UkJdAZEgUmQ5ACys4OJAhh95qE1BgytIYwKoMOCQO7bxGGmkBq4iCgFEmiWINoIWhTjQSBUEipMCiMjAjCgCiEIJIQhrRMzHzWzaJeTSLXs5gO89zOUXbt3o/WsDBfp1quUS2XWVxYQKcZQ0NDpFkGYUSp1sux2QUeeep5Dh+foJlohAmRIsqVLwRGGKusoe0hVZ1lKK0JozLV7j7iahcykARoLr5oG1/94v8kWCK6luIzn/kMH/zgByGnozC0vp/+6I/+iEsuueRlatOOztxA6k//9E/ZuXNnxzSW4xWOjh0tr1+/nk9/+tPUarUl9OoYn+MfaZouSe9soZTiySef5M477wSvDxtv+c89v/LKK/nlX/5lLrroIrR3+N3vkwAzMzM88MAD3HXXXZ20XBp+PwyCgJtuuok77riDUqnU4Z9usO7Hfeyxx/jrv/5rHn744c6zs4GUkvXr1zM4ONhZ3nTlkJ4pMCEEW7Zs4WMf+xjXXXcdrIDPnQ5nJYQOHDjA+9//fh599NFOhovJGWNtjt1888189atfXRLHJzqH0wkh/6/7ZpibRS9+ezm477tO4aa3PpGdLp3zzz+fX/3VX+VDH/pQ530KhFos13Iw+dQ/DEP+5m/+hk9+8pM88cQTnTwW8+TyTKFzuHu/bv24xfIU22mlQmh+oc5FF1/A+PHjNg1t179++V/9Mr//+78HuU6WMQYpQkyWa8iR8uV/+Bw7X3iUcqApCYPlPTI/cQSZMqSZJskypmcXGJ+cYXZukWY7Q6sTQsgYQaZTlG6RqsQe/jQBxgQYDVploFMCUmTaoBpp3vyGK9m8tp+ySCjLlHKoiWRGqFtI3UDoBtq0EEJ3jJpKIawPI1KCwCClwGgwSEIZQWCQYUooDaUgphSEyDBDBAmBVNaChLBLmNooCCLCKCQMDDIQmCimSYnppItZNUISbKZt1vPS7gkOHDoKBCwuNIijEnEUsbiwSLPRII5jWq02rVQRV7uZbSY8s2MX0/OLJEqAjgiEtRDeEUIYhMnI0gSdpigDUVym2t1rhZAMkGguvug8vvrFv1+xEHLMsaenh7vuuotrrrmmw8B8OFo2xvCe97yHb33rW53RtE+H7r0wDNm8eTMPPfQQ/f39L6NX1xf8/nGukKYpd999N+94xzte1m98CCG4+eab+YM/+AMuv/xyZG6g1S+/63+jo6P8xV/8Bb/1W7+1JA2/HK4sd955J7/3e79HpVLpxPMFtIv3zW9+k9///d/n61//+pI0Xwn8OizWZ1GgCiG47LLL+OQnP8ltt93WeXY2OKv5nPD2dhxchfpx3HPhGQ31M+4TaZGwThXPoUjMy8HPBx6B+GkV0z0Z/Dguv344E7iRIIV0/fz6+fPjLnfvl8/dL5ev4v3KYGc/WhuE22MRAiHcIhwn1ICtRnQOSVQuU652E1d6Ccr9hOUBosoAYWUAEfegggoJIW0dkCBRMiATkrY2tJSiqRT1NKOeJDTTjEaS0UwUrczQShXNdkKz3abVTqg3WyzWmyw2W8zMNZmaWaSdGpARBCHkh1MlGRGLRGaO2MwTmXkqskVsmpR0k7JpUyGhbBqU9CJlM0+ZeWLmKVFHqkVCGgR6HqFmkGqOkDqSJsK0gTZZ1rAGVFUdqRrIrIXIEkSWQqbQqUKliiwzGCJm5lrs2HmAZ17cw459h3l6134efXE3T+4+wFN7D/Hgczt55MU9PLZjDw8//QLPvbSHuYUmSkvIxYcxtk1cMNqgMk1gj+rmWnESIWx83VHycT6GVgbtLckU+7QPn/6Wo0+fvv17/z2/f7k+c7LvnSucLH3/ucuTqwO8Mhbz7//m4PdRB5ee33dPlpezhf+t5QJenv1n5zJfZyWEiply1+43H65CfabLSYjxdAUrVlBxuYmcuRcJYDkU83km8AnrlaRjCnakTkaoDid77uDXyXJx5TLaf2e0oWhOmOqxDu203WdwvwusrxpX3+5TxjA1NcP+Q0fZte8oO/aM8vxLR3nqhf08+cxuHnvqJR57agePPr2Dx5/dwfMv7WP3gSMcOHqcI8fGODo2zrGJSY5PTnBsaoKx6SnGp2eZnFlgamaOyalZJianmZicZmpmltm5BeYXF0kyTaOdMj3foJkajLQHQ5WIyEyANhp0C9QigWgR0KIkU2ISIhIiMuypnIyYhHKQUSJF6haBaRFod06phTRtApkCKVpnNm2sSjVaIU2G1AppDIGRCBUhshipSwgdY1SI0jFHj8/xws5DPLtjPy/sPsxzew7zzJ4jvHhwnF2j0+w5PsuByUVGZ5ocnpxnbHKBdgLahATCzoKEcKqJOY0KAVjB5HSwjTHWJ5TLZkeF3UZZCXx6Ox2KtO0PXN3zIv2eqt9+N1HkZT6WY7yuTxUHlH6ZfH7kl8/9XvzNf+auXfDTPB1cGsV8n+xdP98nq4NzjeVzchZwjK7YkK4CZGGZqFjhIl+LLDLnYsOfDu49p1FCoVHPFU7WWMs9Ww5Ow8fPm0vzXOW3WMf+tavv0+W3mCf7DrkEshe2XU8wQH9YvWfvfr7zyOM88PDj3P/g49zz7Uf55r2P8PW7v8O37n2I+x54jIcfeZrHnnie517Yxe49hzh0+BjHxiYZm5hiYmqKqekppqanmZqeZmZ+gbmFOrNzi8wvLFJvNGm1E9LcqGYQxogwRkQhiy17+LOpJIkpkeYho4QiRpkAZIiQYEgJI0UQZAiRABnSaEIhCUWQu6xQBChKAaAVRlltO7sUSedYKIR2tpHvnWEERodoVUZnNdA9BPQR0IPRVVotycx8m2ZbkqiIelswV9fM1Q2LbUlTlUiokFImo4zK868JwQQII/NlRIHR1kac0QZh7HKi1iq/dwYvclViQ+6OIh9HnGi211DAcn3I7xfuern+5PMkx9OW45V4Qst/7n/Hvb8SFN/xv1387vcDZyWE/AL5jMwv9HLw4/pCy68UX1KfrBKXC0X4z5b7/ZXAfcvPq2PSRaI5VXDv+fny32eFZT5dcHDLJy5dPK0fTpNf97uNa4MgX47D7g0tybv7rABkwPGJCfbsO8De/YfZd+AIBw4d48joOMfHp5mcmmdmts78fIPFhSb1xTatZkaWYfd8lEFlCqU0WimyTJFlmlRplAYhAuK4RLVWpau7i1p3N9WuHmRUQkRlJmbrHDk+zdHJRWYamoYu0aZKW/SQygGycBAd9ZMF3aRE6CCCKMLIINehi9CUQJQRQZlAxhgTIGWEFAHaBCRKkmSS1EQYWYGgBkEVTQktS2SySttUaaoeGqqflhokMasgWEMYrQbRx2JT0EwEca2XWvcg5Wo/UdRFICoIykjKCBODiYEIIeLc5UIIRnZUs41WYLQ1IGuszTqMRusMIci1CmXebk6NzopNcoPkr2F5+P3HeNq6uqAg5OI43ubg8zv3jkvL/92lY7xlPsc3/ftiXy8Glw//2n/31YCzEkI+g3IF9RmdD7dkZgqn+l0DFBvBadv5wsjFO1UopptlWafxlsvXK4H7VjH/7tonglMF49VFMc3it84muHQoEKGD20w9XQgCic49dgphZzzGGGs9wZ1cLwhRK68E7UzRSjLSTJEZbc3DBJIgigiimDAIkTKwWnXGCrZASAIRWAOmCDsbkQFBbuTMGJAyIIoiyqUStWrVOkHLXXkTxigZcXR8hmde2sfjz+3l2V1H2X14mn1jdQ5OKY4uVJhoDzHVXkXDbKBu1lDXw7TlEGk4QBr02xDmQfaRBH00sirNrEaiu0lFNwl9tOkjEX2kwQAqGCChlxb9JOEqWsFa6uFGFkvnUS9dSL10IY34PPvMrGK2VebQ2Byziy2UCTCElOIqPV19dFe7qYRlIhES6IDQhIQiJEDms5p87iVM7r/JavkFgUAIuyRoUAgMUZR78xT2ALDACi1rT+41AXQ6uH7r8xS/PxcHlu7a8THH29x7xUFhsd9SEHz+M7eKcrrg3nV5dlhuG+P7gbMSQnjS2WdW/r0rdHHvwTWWX0kUmDG5mQ/3fCXw08Gb1p5rOGHryumXb6V59Ql5uTrzBfDZwJW/mE9XN2marijPMt9eONFu1q23yZUVIBc6eVrWtE/+WAaYQKCERpGiaKNNG2USjMlyHTljBRx2CSn3RQBuSQnrNE9ou3wUSit8yuWYUimiVIqIImvbTgFJZiCIWEw0e49M8OATu/jiNx7mb794N3//lQf4x289yz89Mso9zyzyyA7YMz7M4dm1HJga5ujsAHNqDYvBOubEKuYYYpYhZsUQrfJ6kvIGmsE66mItc3o183o1zWA9DbmGeTXITDbAbDZIQ65jRq9lLFvPcbOF4+ICRuUFHNKb2bkwyFNHBQ/smOLuJ3bztfu+w+Fj47kmXYlyqUytXKa7Uqa/VmGgWqM7jqgEgkhrUBnCKKTQIBRKp2iTYkwGJsWYFEFmVc8xyACCUGJNTmi7Joc1aGqURuRLdSc2+V5DEY72pZSEuet48r5U1NB1/dr1cffMF0h+PHfvjli4OO5d97vJ95KL6vAng/u+zxtd3/9u8MYzxVlxOZGfosWrSAd37TNAPJMdjun6v51gbifeb7fbK64sJ9hcYy1X6ecCLu9FweM0//xynApOxVUW7FS5NM7lSEUUzAA54jZnYN7HjrywdsvyNG1+BYHnAK0D1+bG+rSxrM9gtzAMOjBoaZ18a7R1AWHZpVUv9pIyHcGfkSl7ILMUh5TjmCiw/oZQCpPlbj8MGCFIjCCTEYmISIRkPjEcmU7YeWSBx3ZN8tVHDvJ339rNX33xaT7zj0/w2S8+xRe+8gxfun8vTx9os2tC8uTBNo/safHMUcm+uT5mgy00uy5hSmzmwOIAO8arvDhRZcdkjedGIx7bnfHw8y2e3K05vjDCztEK39nR5OtPTvA39+7mL/7xUf6v/3E3f/jX/8Qff/rL/MXnvsTf/eO32HNglPlGC6XtmakkTclUZg2QSohLAb293fT29VDtqhKHAcaoXAnCCnFX7VorkqRFmrZJszZJq0maNGm3FjHa2p8zWuWGXZ25U7spZM7RisH/v8IXBu5coM9jin9dXJMLAyeInLBxwfXH4oDdCRsnjEQ+cPTPZp4MMrdm4PM+kQ90Hf/5fuP0nOcUMLnFhDiOOwVzFeoq3FW+Y36+Hv3JppMOQW5Xza/8UwV/tOHy4Kd3LivclcGVTRSm3O63UwVHjCYXOI5AXf7PVTCFkZP7Nnl7JEly0rYo5hcgyw1rmtxKgsHkAiSHsEHmNjath067WZ8JTVsrEgwZAiUDTBhiwhAlJSqQ6EBaiweBhChAxCEiCiCSyFJIqRJTKcf0dNeolCJCAShF0m7RbjVRWUq92SRJFZmRhKUaWpZRskQmyqQyJg1j0jikVYYZ4HgCzx2d5+E98zyws839L87x7GjKs8c0X318kj/737v5vc88xX/4rw/xG39xP7/1Xx7kd//qIX7vc4/zh//zaf7g757kP/31o/zH//4Ev//p5/mTz73EF75ylOcOdPPw8yl//oWn+eO/fpD/+3/cz3//0v186b7HefT5vRw8NkerFRCFXbRTQIQIGWGEQEuBEmBCCaUQEQeElYhabxc9/b1E1ZhMK9rtNkmSoZXBqrwphNSEkaBaK9Pb28XAYC9r165m/fq1DAz2EZcjpDBWW0+KXBDlgx5rnO81nAQ+X/PNaDnh4fqwD1EYcLs+7tJyfZXCYLxoBMD/ljOjc6pgPN7rnoW5qbLTHcr/XuGsDqvW63UeffRRJiYmOszMl+KuUpVSjI2Ncd99971MKhcbYWRkhBtvvLFjGNHNLliBEPEr3zFVpRT33nsv/+W//JfTvr9SdHV1ceGFF7J582bwylkc3ZwK7ndX9g0bNnDBBRd0LCGcaziCFN7ZriiKSNOUiYkJHn/88WUPCDsIISjFIW9729solcoYQAh73ub8C7axffuldjRuMqsKjBV4SluB9PO/8GH+9m8/bwVSrkHmlBeMsf8ZLXKvpye+azW48tmNyGeNQlCp1Ojr60Ol1pBniCGwCWNkwHyzhQoiwnIVYySNej0/VW8IhEAYjRYpSmqMFpRMFZOkiKxNKDR93YKbbrqaweFBnnpmF8+9cIBMkftKsgZShZvhCWuf2mANt8YGSjpg09q1/OxPv49ndjzPl++9l5lWk0SDDkJKpSrVUhdxUCYWZeIoJjWQKmUNh0uJETZVuwwqMNjBRBRYu3lGa0qlEt3d3ZSiGIwmkPm+TygJopByHBEbTSSgp7uLoFTisaee5dDh4yRKIKOSdWuhMy6+8Hzu/tqXbf2fhHz9w6oOPT09fOMb3+Dqq69+Wf/2oZTijjvu4J577qGeu68u9kmRrzBs2bKFRx999LvWH06GNE255557eNvb3rZs/hyEEAwPD7N9+3YGBgYw+UzI9f/OIE1rarUaW7du5ZJLLunwBinly6xGAJx33nlcccUVHT7q+qyzMuP44cTEBLt27eLYsWOdd5dDkiR8/vOf52tf+xp4ws8v28nK6MPnH9u3b+dTn/pU57Dq2eKshJDWmlar1al4V8Ai2u02Dz/8MD/1Uz/VaRwXr/j5G2+8kb/6q7+iVqsteX4m8Cu43W7zmc98ho9//OPFaK8YUkriOF6iYOHgl+1M8O53v5t/8S/+Bdu3b4dCo3+34L7xxBNP8LGPfYx9+/YVo3QghKC7u8b9993H8MiqnEvZvMVxTKkUky+a5c+dpqPACM0/+2c/zxf+x+c7B1ltzNx/DfkgJF8dskLICikN+XkkYzfejSEMQnoHBqlUqiStBlljkSqavlLElo2buezyK3j46ed4Ytc+ZFwjFCFZltJIGmQ6IcgdYGsMSljhF+iAWApMuwVa0V2V3PSG17Fm7WqefX4nTz3zEpmBVINCYkwAwtqo02i7pmAyq0cXCGIj2LpxHT/70z/N408/yV333MtMo4kSAVpYZYxyqUo5rhDLmCiwbsyVUnb2GEi0sEofQmB9KUmroBEFAUbYMpTimP7+fgYG+omjCIm2ygaBgdAQpm3Ki002rBri0muuZnRqmn/82rd4ftd+UhMQxOV8CdRwyYUX8E9f+iJReFIZ9JoQ8hAEAaVSacnAu9hnjTGsWbOGD3zgA/zyL/+ypfOT8D7yPfBSqfSyOnTpur8qN1B8utlMs9nkE5/4BH/2Z3/WeXa6ci0H/9vnWgid1XKclHKJgUJnrK8Yurq6iOO4Y6TQBWfI0A9JknSMHb7S4L/f1dVFqVQqZv2s4ISvb7DVheL9SkOapp1R7Xc7uPpxf8vlMs1m82V5KobFxUWqtRrd3bad7ftduQCiI3yWsDBvp1sYYacSGoyyvm7cTFhrq0bsVIq11qjMLU1aLTCrqGAoRTFxXMIgkNJuDptMESrNhqF+rtt+CW+89hq6yxVQBqENsQyIg4Aw1xiTAgIREBARyhCBQesMhCYIACHRRhIEJcpxTBBYxYwoFIRRgIxCRBijjcSIECFCojAmCkMwhiAUlCohqUnIjLbW9IxAEhIQIrREZ1b1XBuFkLnFV2MtlBs3IzSmo/ihtEEpA1itQZC02ylz8wuMj09x6PBR9u87wN7de3lpxw6eeepJXnjqSRaPHWVzfz99pZCxI4eYnZpCqcw6AcwSApUSGU0sBctt7b2G5aGUotFovKyPFO8bjQZAp8+4UOyXri8WBRC5EPD/BkFApVJ52fvF4HjvqxlnJYRew//bsdxo6sQMyWJpHCdL7F+335T/kAup3PxpR0NOaIPQmlAbylJQDUNiIRD5zDsMYxASZayrCGEyVo8MMDDQYzXvtF3CiKKQKAwJAis0pAyRWJVvGQTWn44UNhsYUq2tGnkcEkQgY2v5h0BDaDAyI4gFcSSIA0MktHOsTSghCiWlSkSWpfmOmECI3OUJduVAKasZKAOJDCQikB0NRLDuMoQQGG3IUkWSZmTKIGSMCEukWrLYTBmfXuDw6Dh79x9i3759HNp/gNEDR5gZG6c7Dlk72Ee7Uefo4SPMLTRQGQRGUzEZ3aTU0gZBc9H6gVquWV/Da/gu4TUh9Bq+Z8gH9tbMqfG8fBqD0HamJEy+L67s4UkJCK2IBZQEdEchvaWYIE0RaQrKYIxEhhEiCGi129QX5ynFkoH+HgypNSAq7YZsFJcIwxJSxkiRu4QQVoNISisIDAJlQAtBEIUEUWR9FIHdp9EaSYpWbUKZEQlr3icWihBNYCCUgiiUBELkzuXyZTVhvb+6cpNbU5BSEMQRQRQiAuueG+fYLwgQCFSmabcTaxlCGyDEiJCMgEQLUgXtJKPRaNCuN6Gd0hOW2bJ+PVEgOT56nPGJKRrNBKMNJQwV3WYgUFyxcTVvuPxihLYakK/hNXyv8JoQeg3fMxhjN9q1Y+b5ap3ID6fK3JyM44JC2CWwACjJgPUjw9zxtrdz0zXX0B0G6FYTlEaIACFDDIJ20qLVXCCQip6eCkJo60oBgwwCoigmisvWrI/MD8iKfB9DSuudVAo7s1KaQIbUqjUqcYnAgFQQZIZIKSKlCNKEWKeEOiNQitAYpAFphF3G8+y1CU8ACSDIXXdLaa2J5z7Kc1fmwgotKQiFyJUprDZiu9UiaSf2bJawe1MiCInLVUqVKkFUIkBQlQFrBwbYvH4dmVIcOXqMyZk5stSeLwp0m65Acf6aAW57/dXcfO1V1q/Sa1Oh1/A9xGtC6DV8T+A2QnW+LWTECe04OwOyWnN2Mc/ODAQgjEYCkZBsXLWK97/nR3nnW27j4q2bKBmNSVNrPQFBpjWpSjEqIURTq5QIpZ1VaJN7p5QhYVgiCEuWeUuJyM3YIANEEBHkxwjm5xZoNVts2biem294Ha+7/FIu3ryZbevWsK6vnzU9NfrikN5yRFc5oqsc099TY9VQP8PDQwyPDBOXyjSaTbSx+0xC2HJJKYnCiCiKCXJtxURlZEZjZL5BL2XuRRYCYQglhAK0SlFpgjGaMPdkK4wmjEKiSoWoVCYKAnpKEVvXr6G/rwcCycTMLI1WG4EmMCnV0LB57RCXX3gea4cG0EkLyBDixJmj1/Aavtt4TQi9hu8+cn52QpH5BIOzgiYP7loItNG54LBnXwSadqPO2NHDVELB66+8jHUj/ZAmZO0EjEDmZ6Gi0CogVOOIOJSAQXUOUwukDAiiCBGGGCEQQYAIAoIwQoYRMoxRRjA6OsbhQ0cZHlrFT/z4j/Phn/1p3v8Td/ATd7yDt735jdxy/VW8/qpLueLSi7jqiku49trLuf76a7j5luu56abr2LzlPMYmpjg2NoEyIGWIyGdbQRBY/0Jh7sJZK5IsI1P2fImU0i4TCkEAdp8JbZ3w6RSjE4Sx/owCozFZAjojjCPCSpkwFAxUI87bsJogClBCMDk3hzIaadpUZMbG1QNcdvH5DAz1c/DIEXbt2wda272x14TQa/ge4TUh9Bq+93Aac0uEj7Fac8YKIGPXqzpuurU2NBoLPPv00xzYu5vVw31cfP4muioROm1bzbUoIgwCUJq02aQSx0SBXQ5zZ410fogwCAPCyAoCNwsKwggpI4QMQUTMztd5+rkXefjRJzh2fIJypYvzz7uA619/A+9469u440fezY/96Hv4kXf+CO961x286913cOtb3so1113P+RddQisz3PWNb3F4dAyDXTJEBMggJAyjjuUJbax1a19T0BiDUQqVpiiVWk02o0Fbp30ma6HSJugEYdrotEmWtAijiEq1SjkOGOyJWLeqFxFL6kpxfGoKbVJikbB2oMIFW9YxMNjPYpJxdGaO3UdHIQxPCP/X8Bq+B3jVCSGTn0I+V8Hp0UdRdNrg6/ufCiI/y1B8f7ngH0Q7FzD5CehiOZcLjuGeDiI3v3S6EOYebP26TdMU5ZkXWtH5A5PH86wx+DbLhJSEUQhSkihNokGEgiAKGBs/xkJ9nrS1yPmbN7BmaACyhFa9jkpTJIJyXKIUhFScFp2xqs/WeZs15ikDiONwyTkPEYQQhBgZEZYrBOUKUwuL3HX3vfzGf/x9/s/f+V3++C/+kv/2uS/wzQe/w7O79rD70GGOHB/jwKHDvPDSbp54+jnue/Bh/vGr3+BLd32DJ555wRokDaxqnZASmVsCEWKpiSeZn61SSpElKWm7TdJqkbTalo6FPYhqz6dZlXJrtiezS3QqRasMIQw9tRLrRnoZ7K8SlmNmF+eZnBwna9Xpr0ouv2Aj523agAgiJhaa7J+cYffR41ZIyiCfo/5g4Fz3h5XCamaGL+sny4XvFx84E/630uBbvTkXOKvDqitFq9Xi/vvvX3IAzP11EPkhriuuuILf/u3fplwuE0VRRyi5308FP033jSzLeOqpp/jqV7/aMa/j4ma5O14X9+jRo+zfv38JU10OlUqFDRs2MDIyAvnSifvrviHyU9H79+/nyJEjSywpuO/5neInf/In+ZVf+RWuueYa8Mri8quUIggCkiThpZdeYnZ2dkk8453YDoKANE07p7mLwtWPn2UZu3fv5nd+53c4dOjQkng+hBBUKiV+8Rd/kVqtC2NOmBFZt24dW7ZsKb4CgMZ66/zwRz7MF77wBQwGle/PYAwBgkDL3DipNf+jBGhpUMYa2YwldEcR29auoqoN111xMVu3rqVnoJ97H3qOex58iqnpGYZ6Stx4+YW85223IqKIR3Yf4gtf+hrTdYUJSyhhkIEkjEuEoTU1ZV0c6NwbqbOvl5JluWFVraxVamly30EZRoHKLJ0FgcgtGEhwatfGYIREBCEyiBBBiAhitBFWPVza9pC5ZXIprKt0LUO7rIjVwDPpCaEShhIRBCijSbMMI6C7t58wtMoV9XoLYwylUkxZJKyNGtx60QhvesP1mK4RvviN+/jSN+5FypSbrr6M7RdtIyp3M9sS7D46xWPP72bt5q18+atf6dDQcvh+H1Z1tOveTdOUJ598smN94GQolUps2LCBdevWkSQJURR1rA/4AiI9zWFVv1/29fWxefNmenp60LkJLp2fcXOM2hjDwMAA73znO/nwhz/ciXemKFpMmJ+f5/Dhw0xPTxejLkGSJHzzm9/kkUce6eTb0XmQ244rlnE5OF4RBAFbt27lzjvv5JprrllSH68UryohZIwhiiJ6eno6hXKVtpLKcjaRXEOb3MDge97zHj7xiU8sqSg/jkv/v/23/8Z/+k//6bQNu2XLFn7pl36JD3zgA5h8+cQ1qPuGzs+w/Pqv/zr//b//99OOxE4mhIxnkBXg2LFj/OIv/iLf/va3l60Tv34/97nPcdNNNy05rOun6+q23W5Tr9dPmT9jNAsL8/zQD72TycnJJQR855138hu/8RvFV2AZIQTGGtzM8y2AQNvDqNLkVhQCQSZOCKFKHDDUXWO4VqOUZVxx8XlcduFGhoaHGJtu8q37H2XHjp10lQOuu/QC3v2WNyPCiPuf28mX73uYyXpCSwtkFFkr0lIicpcQgQzQ2qDJzT3lJvJTlaF0hjbWCrWUIPO2VkrZQYWxDDMOrFsFgz1vq4zVABQEyCgiDO0MCKzWnZABUgikEITSngvSGkxu6shojc4UKknIshSEQYYBBNYVtwxDjJTUurool0o0Gy3a7YRQCLpLET2yzZZew1uu2MT2iy+kGffymb/9Bx55bDeXX7qR119xPgP9/eiol4PTCQ+/sIeXDh3lwksu5Z++8sVck6/QkDm+n0LI0aef/ujoKO985zsZHR09Jf2uXbuWf/kv/yUf+chHOoMnBzdAlLmbhVMJIQchBDfeeCO/+Zu/yfbt218mXPx3pZSUy2Wq1aqXwulh8lmxs8zioLXm/vvv50//9E+5++67l/xWRLlc5uMf/zg/8zM/g/YMpLr+7/jUSuDKE4YhtVqtI2hX+v7JcOYi+bsIN5Kfmppienqa6elpJiYmGB8fZ2JigsnJyVOGsbGxTjz3zszMDACDg4MMDQ11/g4MDDAwMMDQ0BBDQ0MMDw+v2FSQlJJardZ5b2RkhMHBQfr7+ztpu3TL5fISYeII9WQjzSKKoyYpJQsLC4yPjzM5Odmpp+npaebm5piYmGBqaorJycnO6NARiusY/rXJrWj7eT5ZGBgYYG5ujtnZWSYnJzvt5E6ErwxWHdtf7tFCY4RGWd+kKJUrEeSqykEUU+rqpp4p5lsJh4+PMVuvk6YJF563iZtffxUb1qyBTFCNA0qBRLUTaqXYbtxj1aaFXQFEa+f2wO59CKcIkCsNnPhrD7Ua7B4RMkYEJWRUQYZlgqhKGNeQUQUTljFBGROWIChjRIyWgV2CC2OrNh1av0lBriFna0Kc6IZGI7R1B66zlCxLSbOUdprSTFISZVAypJEplJC00ox2psm0IQwiSkFASbfplSkb+rrYMDJMV7lEiYz65HEGY83l521g9eAgYVBmdj7h4JEpjk/Mk2lJmh+G+kGwX+rTsKP3U4WpqSkWFhY6AsgXLkWhdDq4+HEcd3jK8PDwkj40MDDA4OBghy+Uy+XTrrAsB59P+EKt3W4zPT39snIWw9TUFFLKTh6Hh4cZHBxkYGCgw69WGlwZ+/r6kIUBvF+fZ4pXjRByROAXxuQzmZUSiM/oi4zXh5u5FNP1hcSp4N7zR2YiH8G5kYVrJJkfNnQN5Z6datS2HFwZ3FJJ4A5X5vl1I3S8/KjcMrefhgt+3RTrZ3k4t8T2W8V2Wml5ljLfE9vfxtgTRKZTVnumJghCgriEjEqkQF0pjk3PsmP3Xmbn5ygFgusuv5h3v/VG3vSGy7j8kgvYtGEdmzas5bwtmxjoriGMIgxFx521/3VjNMY3LSQEQkiCjjtvSSgkAQGBCIlkbG29yZhQRoRBjAxKIHPdNSPRSIwMcovYgS1DEBJFoa1DnNkIqyvYaTut0Eqhswydz7aU1jYYTZZfCyGsDT6dEaCJBcQoyiR0mSZrapJLt6zj0gsuYNXQIL2VkL6y4HXbN7BupI84KlNvw94jk+w5eIzFZoqQUW4K6NWJIh37NLiSAZ3I92+K/KCY7krg4onCTMLlabn+4PfJlX6H/D23r+P3V8cDTgeXN5PPqtLcKrfLj/v9dHB5cLzV8U9X5pWmsxxOz3G/R3AN45iqq+wzgWP8/rt+JTmCcfc+wfjXK4HfgK6B/d/cX5em3+ju+ZnAj689m2suuDTdPpp75t519/4zPz8rgU3HXvv15+5XhhMiwMEI0wmgrftprPWEUIbEcZlMQ6olKSGLbcX+I8fZs+8gMxPjDHSVuPWGK3n/e97BLW+8jqGBHgYHeti6cS3btq63Bjmt7rc1DYRVfxbY5a0TauO5NyNh/SOFQhAJSSwC4s7fgFiE9i95CCKCwDrTMwir9i2sAoLIBYbEEOSHb13pbZ2dCNpoMp3ZPSmVz9QEdh8rtCEKIQ4MImsSmwTZmqeSNajpJpV0kZGyYvuWES7dso5KFFGLSwz2lHn7W27k9VdfTG93jYaCAxPz7D4yztR83bpIN4Y4DG3eVtqU3wcsR2crmWG4fujfL3d9Ovj9RynVYc6m4CjOH8y6b59pf8NLl8Kg1/GAlcJ91wkud7/SNPyZD9777vps8KoQQn7D+gQhXuHIofjXryTtrd2639x3V9og/ntFwnbPXTxHmH4cR5Argcubi+9mVQ7FPLv4jniL3y7WtV8//rvLBVt3J+I6uPeLeVkOLjdC5O7BnU1T94OxG/bCGIwyREFEKSqTtBWtRNNMNY1EMz5VZ/fugxw5cIBkfoZVfTUu3LKeVYM9SJOStBaJQ832S86nv7dmVZtNbmHASHJViCXeX+35GDoCw7ozksShdZ/gQiit63HrblwShSFREFrbb87KQ24MVKA7mmsYjTUKZJDC2Lj50p8Qdk9JGUWqM7JcAIWhJC6FlEsR5VgSSUVJplREQpwtErVm6BNN+tQiq+OM7ZuGuPKCtQx2x2SNBkmzjlQNrrrsfC68cAu1nh5GZ+u8eGiM0ZkF2hrI6xu0rY9XBVc4AZ/5+vTqeINPiyeD/57ro8U+spI+6b51srS0tyJxsvgr6ScOLl137SAKgu5k8N+R+aqMe+7y6ffxkwUf/jO/Xl8pTl+K7xFcQYoFPpMC+o1bfM9n/P5v5iTS/XQo5tMfBflxfGJxjbfSbzj43/Kv3ff8ulPL+F8SnsB03z/TPDj4rxXr4HRwr3ZmQsIaCzXuRxeMNVwqDYQyIhAhSZLRThSNdspiO2WhkTF6bJqjh4+wODNJoFqEpo1qLdCoz7K4MENjcYbzt23mgm2bKZftDNF+0JltsN/yP20FBHY2Jq32WxBKglAggtxNkpVj6ABMYK07hIEkDgLiUBIGkjAQhDLXvstSdJqCzpCYjruFJS0gBBpBZrCu5aS1WxfnbstLsSQONBEJsWlRNk26TZMrN41w06VbuGbzEDdeuJ6brjqfLWv7kboFWYpJ2gQk9PWWGV41RN+qVRydq7N/co65RJMKKxaDIEBnaW7i6NV3TuhUNHuy5z6E58zN3fNKaLggzPwB7XJp+X10ud9PBffecnm2g5fTs2//Hfd9xwv8318J3LuOr7xSnL4U3wO4CnKF8pmo3+grgatcX+i4a59o/EZxz1fSqHjfcIJnuQZwz7Q3bXZ5ONk7y6FIRH6+3e9+3fl1VozrUIzj4OfvZEHrE3Xmvunul6u/Tgzj+NqJd+xtvj6VPxZgLWcb8k18iUozssQa1hQiRCn7bqYMaZKRJS1Uu4lJmmRJnfrCDK3GPCZrsWa4n9dfcwVrh/sJjMJkmVUFV7mF7vybTnFBGHINNoEIJSK0xuuMNBipQCqM1GihyKRGSYMWhkBKSlFEOYopRSFxGBIF1uKBMMYyeK3p2I3ILRO4fSqtNZnRuV09QRCGxOWYUjkmjgJCaQhRxEIR64Q4bbCut8xt117G219/OT/0+su5/brLuGTLGqplASajFErKpYha2bqiUEIgKjUWlaCuIBGyowwicrfe1tZehvXk9OqA3898mi7ui54KprDsvlzfXa6vFOFo3s+H3xeW6wd+P1nu/mRw/dR9x+ddK2X8xX7qwxegKwnF7xXvXylO33pngFNl6mQVgfebz9xdWq90Oa6IImE4wbScUDod3DfcO8sRtIMjGP/en64XCWE5+Om7awoCzniCSOUqp35+pDcVd/FfEfKDpi4ffl0siebu8z8nSuaZSchh/YRKjLD7KUaAsyBttKbdamFURhgElEsl4iikVok5b/NaNm1aRxiFzC/MsjA/g0qaCJOStRbpjiQ9oeG6S7Zx5db1jFQlZdMk1G2ETjEqyztALowM1g6dkGgRomRg3Y8LEEYRYe2tDfdWqZQCu9RmTG5xWyIDe3CxHEeUIjsrigNBGAQYA0qZfHfIzoWUNmRKkamMNEvJVIbWGikhigJKsVVmsJa9JRAQyoiIgN5yhU2rhxmoRvZc0FAXq/prGJXQaDTsPpeELElZmKtz+PA4+w4e49DRMaQMiOPYCp7cpKydQVu1cds0y9Pi9xMn629F2jsZjKee7N7x+9NKsBzzNwUBxzJ8yPGa4vOToZhHd29OMoA8GYp5cteuHor58eMX0/fL7v92JuVaDmcthFymHUP0M1msAPfXDw7+vT9zOFP47y4n1NxfX/i4+K8EPoGQp+eXReYjNRen+B2nZeLeLdab/17xXQe/TKIgWIso1v3J0lwOLqbINXbMMtN6P9/2eT7Rcb9jz/4IYYWPnX3kygpSQmC9lSo0mVFkKiFptzA6s6rW0hBJWD3Ywxuuv4pNm9fTSFpMTE7RXKwTB5KhgV6G+rqpogkW59k2UOOHrr+CN195PluHKnRH1v2CMalViMid6Tlr10YLMhOQEaKEFY7SKCpCsXmkj5uvvYKLN62ju1ICA5mCRAlSbWcxUlrbdWEAYWCX9BCCVqrICCCIMUGIBrIsIUtbpGmTLEvRJiMMBFEoCKStP2spu4SmRJIGGBMyMriKrkqNo4cPcWD/Hg4eOsDBo4c5enycqelFpmfmGT1+jH37D/LM07t54rEXefa53Rw8OEqEpBYGSJWBzttDKoTUgMBaq3v1wfUlcnpy1yvFCZp8eV9a7tnJUOzLduCw9KA6p0hzuWdFuHddHyv2ZZ9HnAp+mf2/FHhgMSzHy4tKDQ4rycepcGatWIBSitnZWY4dO9bRSR8fH+f48eOMjY0xPj7O+Pg4Y2NjNJtNVq1a1dFXHxwcZPXq1axevZrh4WFWrVrF2rVrlxxOOxP1bAoS2RGJMYZ6vb7kvJH7e/z4cSYmJpiYmGBsbIw0Tenv7+/o058sDAwMkKZpp5zufJIrqyv3xMQEi4uLmHz05hOE61COmbfbbaampl52zsnlzd1PTU1RrVaX5GdkZKRzXmH16tUdvf5qtXrGHfW7Ct82jzufY8jJUFq7cYA9TGo36m1nUBitUFkCRiF1SsmkRFmTxdlxJieOMzk5wdTUNPNziwQyYMPadawZGaGEQjQXed3F2/jQj7+Ln37PD/P6yy9iqLdGICBNUkSQm9PpTNDyQ6UiQCIJgFhCX63KNdsv4dILtvGmN76BrRs3UooijDvAqo2VYybf05KSIAytL6HQuohQ2mCknTUZIFMpSdJGZXYfRkqrEGEFkBWQSuezLSPIFLQT+61atcrs3BxjY8dptprMLywwN7fAzOw8R0aPs+/gQXbv28ehw8eZm2+TtDXCSKpxTCUMCQRok6GMwsi87pFY/YxXEd28iuAYsMzP6ji+d/z4cWZnZzt99myD6+9jY2Odc1DubODExARzc3MddeuzheOTCwsLHR7uvjU2Ntbh78ePH+f48eNMTU11Dt/7fPaV4qwsJhw6dIgPf/jDPPfcc7RarU5mfGFAbjLjmmuu4XOf+xw6N2nhZk4urluieuihh/jJn/xJFhcXwauglcKZ43AjdPesXC53Th5rrUlzd9pCCNI0xRjDhz70IT7+8Y/T29vrpfhyHDhwgD/5kz/h7//+7wmCgCzLlpQ5TdNOGVut1hKTIiLfIHUE5PIUhmG+RHJClVzkM45KpUKSJARBQH9/P3/0R3/Em9/85iVE4FRF/Wm2K7PfFq8UxhgW5me56KJLOD42toT4Pv7xj/P7v//7nXiuDFYA2nb4+Y98hM9/4XOWIQthl4HyZjXmxMDB/55AEOf2qpyduSAQVJI6lw5K3nv7jfRWAtqNRUpRQHdXje7ubgYHhhkcHKZarpJpRb3ZsrOauEbdxByba/PQczu5677vsPfYJKVaN4HRBPmSWmoESgQoo5BkhLpJX1lw9aXnc93VV1Aq1yCq8tKBMb55/8McGZ1AaUMchZTjmCiUYOzgwhhDphWZliRpBjIgjktIIcjShHarSbvdskIwigjDgEooiMMAIQWZhiTLhVCWopt1KrrFhesGeNv1l1PJFumJNJtWj9Bbq7CwOE+93aRSrhHJEjNz88zMzaHiMqLWj+xZze6xBt9+eif7j02RCIkMJWFouHDbeXz9K1+xY4WT+Pn+flpMWA5Hjx7lxhtv5MiRI0v6fBHr16/n4x//OB/72Mc6NLocTmcxwX8WhiGVSgWRCyVnBuxcwOTL9q4+3bXjk1pr2u3cpuApUKlU+N3f/V0++tGPdp4Vy+/uf+3Xfo3Pf/7zHespjr+Euc1ImS/pX3jhhfz2b/82b3rTmzppnA3OarhjjCFJEmZnZ1lYWGB+fr7jW31ubq4T5ufnybJsid/znp4eenp6Os/ctfOHLnKjmmYZoXYquD0kN9MgP2jVaDSYnZ1lbm6OZrNJs9ns5Ller9t1dGOo1Wr09vaeMlSrVUw+cpibm1tS3oWFBdrtdidd/6CZmw35e0JZZvcB2u32Ep/0Lm+NRoOZmZnOvRNGpVKJarXaCT09PXR1dVGrWUZcLpdftv79/YezsXZquPyKXIU5CsMlJkIMhsGBXtatGmL18DADA/2UK1VSBTNzdfYfPMKOl3Zy+PBhJscnmJmcZmpignazSdpqUYoi1q1Zy8jwaoyGLFNorRCByHuEQqIIMYgsJcKwbvUIl1x0PuVKiSCUNBt1QimIpSAS1tVClrRZmJ9jfn6OJM0Q0lrOlqE1+iilQGUJKm2jsgStlF38CgJbRiEIZQBueVsp0jSh3W5ZmlhcZLHRoNFKWVhcpN5qE1UqVKo9ZBoIQ/oGh1izZj3dPX0oJH1Dw1xz/fVc87rXcelll3HlFZdz6cUXsmpkiFI5QkiDCQxGChT5WaqTCKDXsHRJUClFvV6n2WwyNzdHvV5nfn6e+fn5JfzvlYSFhQVarVaHtywuLrK4uEir1aJer9NqtU4pdM8ETsglSUK9Xmcu59mLi4vU6/UOX5qfn2d2dpZ2u90RUOT99WzycnqOcAoYY2i1WksMjDom6zJZZIJOsruRsmPM7jd/JOWkbzGNk8FP16HI+GVuH0rmJoKcECAvj/H2X04XWGaj0k/T1YMvRAPPqoJjrH4Duvf8Bnb3Lm1Xb9K5fs7r3TI6W3b33H33e4WVf+9EPHdOyD8vJISw9tbsoRtkIHBesREQRjFGWBcMMogxIkALiRaSdpoyOzvHgQMHePbZZxkdHWVhfp6pqRkOHznGrn0HeWnvAUbHJkHYkasI7MxD6RSMIhKGsjSUhGa4r4fzt25mYKAXhCZJWkRRwPGjR1iYmcKkTYRqg07QOqXdbrNYb1Bvtsm0sTrdQBBIhDGoLENlKcYohLSO9hztSzRJ0qbZrNNstWi1W7SSNu12C5WldkkPQ6I0GRJFQGKgkWQs1FukmbYO9IKAwVWrWLNxE5XeXrp6exkeWUVfXz8XXXgRWzdvodpVRQvIjEELkGFkF0tP39X+Xwufj5ic+bol9XM56HP93fEKd+/PhM6G8Rfh80mfd/vlC3Njz0X+JF7B/pyPV/5mzijiOF5S+S5DruL84N6h0IjFQviM3aW5Eubm3nFp+s/cvd+Iy6W53LMiliuXe+7K7efZEYvwBI7JG7NY9iL8/BSJ3BGi+5arN5dmMX/fV7j6OH312tF4XkdZlpEkbdIsRWnbCbWB6bk6R8dnmK0nJCJABSWIysTVbmr9g/QMDiPjMiIqs9Bsc/DYBM+8uIuHnnqOb377Ye5/+AmOTc6AsHsgJldXNsYghQbVRrUWqcWSLRvXsnnjeoSQZJmm1U5YXKwzNnqU3lqZ1YO91MoBgVDEUUC5XEIZw2KjQStJsdJTEEl72FUYa6JHaG2VMqSlGavOrWm3W9QbDeqNBq1W2yos6BMuymUUsthKmFlokOqA1EgyI6m3EhqtNtoIhAhYaDTZvW8fzzz/Is/u2MULO3dz9PgYs/MLtNOkczBVyNx9RhyjsRaFXsPycP272O/9pTL3+7kI7nvkPNNP/1zBF2bLpe2+61Z13O8+jyu+cyY4OfdbAVyGfcnpnlNgoL5E95myH6d47ae3kkL6wm85hkyBiNzv/rdWgmI+XSO57/jEIj3bccU8FfPil9OvmyD3GGoKsx0Xz3+vKGT9dL6fcDkomuwBrEkdb/hthD1No7Qiy1La7RZpmthyao2RkoVUMzrT4Phck/G5FpMLTeaaGS1CZKWHSv8I/Ws3s+miK1h93iVUBtdwfKHNc3sP8ezuAxwen6GVWSOp2hiUhlQpe05Ha1TSJCRh7XAf2zZvoL+3G6MNQgaEYczevXtoLM5xyYXbeP21V3LphdsYGeyjHAe5w7wYEGSZa3O3tBjY1S7j+ozOTQjZuhH5O0mS0W4nJGmK0hqEQAYhpUqZcq1KM1UcHj3OzGKdRqKpJxnzjRYzcwvMzs8xO7/A5PQ0Y1MzHB2fYt+R4+w7coxdBw7z4COP8dLuPdSbzTxfAikFpTjKG+RE27yGpfD5hN+v/IHzuUKx7/r841z2aT89ke/JOR7j+Iu7dn9dXnS+cnM2+TkrIeRQzGSxooqZ9+/9Qjr4FcAZSlmfuftYrjJ95u3HWwmKefNnHi4U47hnxRGNX18uFN8zy6hqurLi1eVK8//9QF6qZevdF0JCCEz+xBi7N2KM3QiWgSSMYuqpYWKxzXRLMZ9CQ4e0ZYn5RHB0epFdR6c4MttmQZQx3UMMbLyQ3rVbaFFirq3IRAhBgJ116c45HmU0adIG1Wawt8b5W9azbtUgobBCQmWGeqPJnj176emusnXzOi6/9AKuu+ZKLrv4Qgb6ekEra/onCFHKOh6zmm/WJp2zmGDQaKORgVXrttYaJBh7KFhpKxDtwVarumfrRdBKDXsOjnPg6HEm5xaZWWyy0GiTaoMMQpQ2LDRaZFogwgpKRrQI2bH/EA8/9QwHjozSTjJAYJRCoinF1nacrf3XcDIUadfnLcXfzgY+byg+O5fw889JyuDzmeLfs+U5ZyWETpWBYmUF3kFJV+hinGKlFyvHPTvZN4tM2njnl/xnDi4dPx8nS9tHMY57t/jXXbvv+zMYP6/LlalYNy4Ntx5bjO/ulxvBnAyn+70Im08g13BzKOZ1ufx1luNOMcx29WCMZdhSCISQRFFkPaDmSgpBFLHYzjg+M8d8OyWVIToqkwVlFjLB3uOz3P/0Tv7xvsf42288yF0PPsNTe48wttAmEZFVyQ4EQhgEyqqAGwNGojOrDl4phWzeuIatm9bSUyuhtUJrQTtR7D9wmKmpKTZtXEd/X42+3hqbN67l0osvZOP6tUShVUAARZq2aTabtFotMpXmR6EkUub1IXIakCIXhFbgGiRSBPbMTm5gFANZmtFstUg1TM422HNwlNGJGeYaCTqMKXd1E5UrpFqTZAYRlgkrPcRdA9SV4Jld+9h1+CjzrcR6UUUijSGWkt5aBauo/fLlGUfHZ0ozP6jw+4/fbx38vuvg9+Mi/RfvHZZ77qez3PNif3sl8HmiQ7Gt3fdc8GdiDuI02wkrwVm9XayM4nXxHq9xi5VQjO9D5pvwxcL71+7+ZB2l+Mx/x6V/pnAN4hrIpecaxm/A4nsunv/Mz6P/rp+uC24D0X/fh7svpuvDPS+2xclgy7h83ijkYUmZjbVMbePn7+fPwYolPy1nzoZ8CSsuxcRRiDCGMB/MJEpzbHKa45MzzCw0mG+2mWslTC62ODK9wPMHRvnGo8/x9994gP9x1z38zVe+yb2PPMn47BwEAlC5GnWK0VYIaW3P9wQSRkYGOG/rBoaHepH2MBNpppmfb7Br916q1S7WrVtNtRojsHtBfb3d9PZ2YbOoUCrJZ0JtGo1Fmo0GyrniyLXlRK5tYevAqgjbanFnlUICERDKwFpe0IYkUWghSAyMTS9wfHqO2XobwhIiLlFP2sw3migCtCyREqOjGvtGJ9l56ChT9RapsIeCAymIhKAcSPq6a0iy3KHQyfsyxfZdhv5+EOHK6ZfN73v+M/+5X/bl6qEYv/jX5xU+rzvRH070ZU7CD0+GYhxL524p+ERwvy2HYr34+fLff6U4c857hnCVWWSYvhZcsZFcgV3h/DVK98x/x78/XXBpS8+nj5+XlVSoi1tM128of0YiCxYT3HfdvXvHj+8gcy0+v07MMjM8BxfHvy4GB79OT19u9z7W4nNhCRFPmPnleRm8z3TqCjvr8X/U2s6GSqWYQNoZigysLx6lDVpIphca7D9ylINHjzI2NcXY5BRHjh/n6MQU43OLTDUSZpoZh8an2LHvCIeOjbHYbOVWDUAIaylBCFsWlWbWBlwYsmXTBtavW02pZA+bgqDdTjl0eJTjxyc5//xt9PR05669DVmWUl+cZ2FuFrSiVikhdUYUWMsHaEWrYdV5s3zfzrW19bpKPtsytlsKidJWZTqMIqIo7qhv20O9Ei2gkcH0QoOJ2Tkm5uaZnJ1nZqFBM9OkRtJMNPONlMNj0zy3ex9T9SY6CK3FBgNCawIMIYZSKDHG+luyFsWX0typBlbF+x9EuD7g9nDJ+8hy/Yxlylzsd0X4/drVZ5EvUnjf75t+nlwcvz1OFXyYnP/6PMT/th98geWe+dfnAt91IWQ8Nb/R0dGOhYFjx45x7NixzilcZ31gcnKyU3jXaGmadtQEXeGDIKC3t5c1a9awevVqRkZGOhYYThXWrFnDqlWrGBkZYc2aNaxZs4ahoSFGRkaWuBU/FaSUnW8X01q1atUSKxDOpa9Ld7nG1lpTKpUYGhrqpOfyOzQ01Mn3yMgIIyMjlEolosiePSmm5epbSsnMzEzn1LVfz67uXf3Pzs52FBpODntua3h4hFWrVnXys2rVKrq7uxFnMC23rn1OCPLOM2sjgSCQRFFIqRRTKkV5uSAQ1iq1MYbMQDPLmJ5fYGZunumZGY6NHeP4+Bgzc7MsLNbRBmQYY2SIEgIjA4IosoMaYc3phGFgFQOMdd+AVqxeNczWzZvo6e4CYZUFkiRlamqG/fsPUi5V2Lp1K5VKyZ4tEpCkCcfHjjM7O8WqoQG2bdnEUH8vlTggkoY4tIyt3W7TaiUopZG5WrbJNSXb7TZpllmRl7t3cAFjyNKULM2wk8QAKa3V7XqaMjU/z5GxMQ6PjTMxP89cs81sI2FyocmRiWnue+hRDhw5Rnf/EJXuHqSUVkFCa8hSYimJcgUJOoMpS0+ycJTCZ0qngov/gwLH3IMgWNKf16xZw7p161i7dm2H3zi+4Z9fO1kIgoCuri7Wrl3bedel6dJzz7u7u9GeEhfL9O8sPxQ7MDDwMv5WDMPDw6RpuqS/T0xMcOzYsY5lBMd7wzBcwm+Gh4c7eXX5dFZvosgqsfj5eqU4K4sJBw4c4AMf+AAPPfTQkgorJhmGIRs2bODd7343xlNnFB5jdrORAwcO8OUvf5l2u71kpFBMd+3atbzpTW/ida97HXSWik4tnf33tWe5wT2/8sorufHGGymXy95bL8fs7CyPPvoozz///JJv6lxTxB2yBfjiF7/Igw8+2DlLRWEk4uJdddVVvOlNb2LDhg2dOKYwBTb5Ydq3v/3tbNq0qRPHMQgXx73zl3/5l+zevftl57gcXD42btzIT/3UTzE8PNz57eUwpEmb//pf/yuNRn7CP28fl3f/2ydgbbL9wi/8Al/4whesqnHutwdyzwoupjiRp1IUU4ojBHZzPsAaNNUqQ6LRWlGOJf1dJfqqMZVSRBBGNBPFwWPTHB2bp5GBDO2IvxSXqNWqhLE1l2NyXz+oLJ/dxQgMAW1uveV6brj6EnqrEUalKGUYn5znmed388KLu7ls+3ZuuOFqerpCwkBiCBgbn+a+bz/A8fFJrnnd9WzYtIWdu3bz3As7mJyepdVOaTZbVlsOSblSISrFZJkiUwphDEmrhTaaNLM26ISwe0cSg1EZSlmPq1JihafOqISatYM99FcDBqoRIwM1empVlA6otwJmG5qXDh9j15Ex+tev4d0/8VPs3H2AA/sPopp1ApMS6JThgV5+/Mffyy9//N/Yc01BSG64rkNnAJ/97Gf50Ic+hMwtBFCwmOBbDCjSg3qVW0ww+WDgyJEj/MM//EOnX/l9zEFrzd69e/niF7/IsWPHTvptIQS9vb1cd9113H777UsEuvBWRVx+HnzwQb74xS++jF/4cYwxXHDBBUb1uzMAALHXSURBVLz5zW/moosu6nxrOSilmJmZ6dQ3eTpOkPll6uvr65j6cvkLw7CzEuP6+/DwMLfccgsbN270vvTK8T0RQq7wQW7iBm/U4Rix8EbSxVF5kO8D+Ez78ssv51d+5Vf4mZ/5mU684neXg8tLlmUdQeATmMvDqeAIE6+8vsDEW0b7N//m3/Cnf/qnS8waufL6+X3f+97Hxz/+ca655ppOvThB6b/jvkmhvC6Oq6ssy3jve9/LN7/5TRqNRue5E/4uf0EQcO211/LpT3+aCy+8sJPe8rCaWghpR/IF7Tw/j/47vhAy2i6B5ZZ6cscG+XW+XxQFIZVymTAI0FqBMQR5LJlrc4EmS5sM93exarCXWjlGSsncYpNde48ys6AxAbTt68RRTK2rRlwpI6MSUgYYkyFNRiCFNVqatlg11MMPv+1Wztu4hlIIKstoNRP2HzzKAw89TqY0b3/721m7ZphSJJDS0E4Uz72wgyefeoqe3l7e+MZbGBpejQxC9u4/wDfuvo/xyWnm5urW7huCUrlMEIakaYJWGVEYYpQm1YpmK0UbTRRGSCGstW+j7FKkUihj6z6Qmq5YsHqwh76KpLskqMYBgZQoE9JMI2bqGbsPHWW2mXHJ1Vfwvp/5WR597CmeefIpWvUFypGgFAjWrRnmZ376Z/ngh38BnSpEFKGNPZeE176f/exn+fCHP9zpxwDd3d184xvf4JprrvmBFkKOv7i+7PcZ/969e++99/Kxj32MZ599FgpLd34fXbt2LR/5yEf49//+33f6tvBMlbkBuNaaP/uzP+NXf/VXqdfrnT7qILwZ0W233ca/+lf/ire+9a2d35dDq9Xi137t1/jjP/5jKPAMx+sc7/jUpz7Fhz/8YWq1GqbgpdnxFpX7K/Pbzl0vV6crwek57jmAK4RSagmRFhvMFzIOrrGcNPYbwhXeNVTxt+UCeX7cNNpVqP/76eDyWBRY0tswdI3lyi48gYtXZpcP/5kpnC9y+SzG8e+FRxTOIoRfl06ouXfdb04QrbTsQtrlIb8d8Waip4PAek7NF36WeHUI8nMyoXcWSgqJdDSQZhilyLIEtGbj+g0M9A0QyAhNSKOtmVtsk2hBBiS5YWwAIQ3aaIw2YAxJktJupQgiBAFGp5RLgisuv4iBwV6QEm0CIKLeSDl8ZJT5uTkuuugChoYGKJVitIEsMywsNjh05AhJmrJm9Wr6eruJA4gD2LR+LT/89rexaeNGyuUyJvfvrZdoTVotOZlb3JbyxGkqYQzCaASaUNolQ6tIoQjjCBlZKwdGCEQYQVSiTchiJqhrwUyjRYJARHZfdXx0lMWZKUI03dUS3dUyA/29bNi4ia7uXtAg41JucmgpDTka8wWQgx/vBxWuz7n+5soZBAFRFHX6NgWeVXxWvPZ5gP976Jmj8nmh6/9FIeXScXxELMPfisGl6fLhP/f5gf9OGFqXJO5brl78tHx+fLZt/10XQn6hjScwXEUuV4DiOz7cvfu9+NtKgl/5Dv43TwdHjK4MjmH6hOQLKP+bDsvloVg218AuuPeKz1zeHaEEBWdf7nc/XZYRoqeDVip3zPbyNnB5PS062nUnHnXeE3bfxyopkPu6yTudNuhcAMVhyMUXbuXH3vMurrrqCghC5hsJ9VRQzySJjEmRKB1iZISQIRiJyjJ0Zt0XCEApTb3eoNVskLbrrF41wKaN66hWS3YAQECrrTh6bJwjR4/TP9DPBRdso1K1sy6BJFOG0bExxicn6eruZv26dXRVy5QiSRxK+nq6WLNqiGqlTLlcohSXIAisqRxvvwVsbwyikLhcIohjEGCwLsGl0RidWSvi0Dn8KgKBCewKmpGghCQ1IU0lmW9lTC02aKaaNWvXEochSaPBLddfz6989Bf5j//ht/jjP/q/+L//9D/z7z7xCW5/xw9ZtxHYc0qCE3RTpDeX7yJt/SDD9VNTGPj5cHVxYgBxgkH7sxafN/hCxA1OHYp9xqVdTKvYd13cMwkOfnsV+ZKL5/66eO7+ZG1+Nu3/8lo+x1iuMK5R/IK4a9d4PooFds/8inENtZJQZM7LfXMlcO8bzwRPsbzFbwtPWLhvOxTzYZbRSPLz7uL4f10+/PRMoXO5+O6dIiGeDHYWZBmUn9bJ8PI6zQ9cevtALh8COkzPGCtw0AaTKVSa2H2QAIzK6Omp8bbb38JVV1xOX08vxkCzndFoZyQ6Nz0qJFYFzo7mtTHWfxHGCqF8iU8Ka/26VI44//xtVGsV4qhEGJWQUYlGWzE2Pk2SKi646GKGRkbQBpqtNqkyLNbb7D94hMVGm76BIapd3TSabeYWFpidm2N6Zoajo0eZmpxAG0W5UiIOQ9D50mauaCGkIIoj4jimUq1QKsUdf0tBrtJthEALg7F6Ctb+XKYIkAQyRBOQKEEmIlJCFlpt4lqVG2++iX/50Y/ym//+N/j/3HknH/zZD/Ke97yHW297C6+77nouvuxyNm05j56e3g5LEAIQbhiQGzYVgssu284v/LN/zhXbLyeUQUf1vjN1y5vZNvWp6ePVBtfHHN0Kzzo9noDw47olqpPBpSU9ayp+Pxfespzr236/cashDu7af/90wY/nymC8/luM739DeMuHruz+u/71K8VZCSG/kL50X67wPvyGcfcuFOP498Jjwv7vPuNdCYpxi/crgSuXC05YOLj8Llc2n+n79eaufSLx3y0Sr/+9k+WjiGKa7tlKIHKfPw5+XiwXOlW9ms7sR2Ny76lAfmjTWFs99vCks7WtNSZL85CQZSk9PRXefPMbuPTSS+jq6qJUqSJlRLud0Gy2SJOELGkjhbGKBzpDCDBGoXOFCK0zQqlZv34Vt916Ez/+Y3dw8803c/31b2Bhsc2LL+3luRd3sWvfAV7as5d9hw9TTxIa7YQdO/fw5NPP8/BjT/LYk8/w0GNPsmf/YRptxfj0PI8/8wLffuQJ7vvOY9z97e/w1a9/k3vuf4DxqUkwmlBquioR/d1VaqUo18jT6EyhjUCGEVEYEAis6rSwy5caQQqkQqAdo1Cakggg1aACjI5oZ5K2lvQNjfDWt7+dX//EJ/it3/xNfuon38dNN93CxRddwoaNGxkaGqGnp59StZsgqkJQQhFgpG0XnbstNwIUhgyNwnD+BefzS7/0S/zuf/htfuv//ARvfctb6O3rwwjrPRa1NDgdFEdzxT7i4GjXXWe5gzg/zvcCfr/y8+T/VuwvK83jyd73+72L5/46AeDg/1ZM51RY2ldfDuOt6Li2cu3lf9PBv15p+U+GsxJCPjNzmXX3fsaKxOYq22fGflpFLJdW8bsrHc0vhzNt0OVQbChXF8s1lpsJ+QTuUMxLsV5dWYvlLdadG8GJgmAqEtZyeTgZjDaQz1YcTpTXi3hKGBvyUTYuDbtVgxV1AqM0Ok3yTXm7JLV6ZIDbbr2FN77xBuI44qWdu9i77wAz8wu0k5Sk3abdahFg91EwikBoa/rHnBjVG63o7qqydctGNm9az5VXXc6bbnkz/f2DNJoZL+3cyyOPP8193/4ODz/+OK0sZc269Rw8eJh77rmfB7/zMI889gTfeeQxnntxB/P1JokyjE1Os2P3Xl7as4+5Rou41s2uffsZm5wgLsUIoSnFAevWjHDFZRdz3paNlELnhdUaUtVKk6UpJssQWiE6KumGzAi0sM7+jDYIZdBKYxQoLcl0wODwOt5482389Ac/xD/75/+Cn/jxn+DGG29k/br1lCsVpMxNFeVBIBEisO7DhXVlob1/tr0EuUinVuvioosu5rbb3sKHfu4j/Oqv/Rvu/Jd3MrR62Fo89+jAgGtUe+8xOp8Gfdos9hGf1r5X8L9Z7B/L0X4Rfrn8cvp9VhT6dDE4uDpxz33+V+zzy8HFKQpTxxNcmuYke03+N1byvVeClXOgk6AoUE6V8WIlFn9fCVx8v6Ecwb4a4Rr8ZHAbfD78snGKuj0dinXsiA0vLf/ZSlAkXHI+Y8yJmc6p4N7vCCP7cufeCg+9ZLkqCCSrV43w1ttv5R1veyuVSoXHH3+cr37lLvbs3Ud3dw/DIyPIILAMPLd84NziuDYQIt/sFbB23VquuuoqhoaHaLZa9PT2oxEIEXHBhZewect5DAwO09Pbx6bNW7hs+3a6envpHxxi0+atZNoQRBHbLriQN7zxDWzfvp2LL76Yyy7fzqYtm9mydQtvvvXN3PjGG3jLW27lh97xVi677GJed+1VvO2tb+Yd73gL1157JQODvUgBURSis4xWo0mr2SRLU5TOUFrnGoRLubuUkkxpGu2EtjYQxpx/8WX88Lvew/ve/0He+c47uPyKq+ju7cV41uA6LS3sncjrXBprP05gNQ8l+axUCwIjOkojBkBDEEQMrVrFTTffwk//3M8yvGrE7klJjZIaLQ0E+TKehyIj9mnd0pClreLs4AcVro8sxwP8/uzqwq8TVx8+zrS/+nGL/MPPmygoR7jnxbj+7z7Opp1eXjNnCFeAYmW665NVPt67Z4Ll4vtE+2pBsVH8fLvlQ3+q7f++HKG4OnZlLcZ36Zl8ROO0btw7Lo7/Pfe7/51T4WR1D8Yf8J4WlsHZ8y+WEdLRABNotMowRiEFxHHE6tWrecONN3LLTbcgheSRhx/hK1/5J/YfOMiaNWt43XXXsW3b+URRTJJan0tg9ySMtp59S6UyCEGmFFIGDAwMsm79emQQ8cyzz3Nk9BhDw6vYsvU83vHD7+Rtb3sHr7/hRt548y1c9/rr2XLeNq57/Q3cdvvtvPm2t3DjG2/ippvfxI03voG33v4WfuRHfoh3/NBbeetbb+PWW2/h6qsuZ/v2i/nxH/tRrr3mSi695CJuu/UW3vnDb+f8bVsplyMGh/pZs2YErVO0SlFpStZOrPIEdvamHdMxVrCirFIFQpBoaGaaoFpl+7XXcsdPvo93/uh7uezyq+ju6bciJl/31ObE7CYjI0WRoklRZCZDkZGS0iYjFYoM605cIzrjhM7cVYCdzdqZz9DwCGEcoYRGodBSoWSGFhrkUvorwn9WpOnlrn8QUCyr65duf6fY74rBh+unjlcU++/p4OIU28DlqRi3mDcHP9+msDd2tjgrzi2lpFKpUKvVqFQqVCoVyuVyJzivn+VymVKpBHmmfSa60sosQucuupvNJo1Gg1ar1bl+NQTnvdUYQ6lU6tRPtVpdUlfu3qmALgeZb2a6ekvT9GXfaTab1Ot12m1rMLPRaBBF0cu+udz3oygiSZKXlWFJeRrWCKez6LxUEL58RLschLBb1SJXx7bmenIBlC+5CWHAKHSWIjCsGhnmhutfz403XM/C/AL/dNfX+NrXvk59sckVV1zJ5VdeSVd3N+12glL2PFC5VEJKSRhG9A/0s/2y7axevRohA5SyS5mTk1M8+cSTfPv+B/j2tx/k+edfRGvYfvmVdPX20d3bRxiX2LR5K5dfcRWDQ8OsWbueRqvF1NQM73rXu3n962/oHCo877ytbNiwnt7eHs7fto0rrriCWrXC6tWrOXjwAE888Rhr16xmy5bNHDlyhMcee4z5uVn6++xMKEtSpBBEQWBdhEcRCMiMPcyqsgyTKaSxMxKlNEYIegaGuP6WN/H+D32Em267nYGhkVz8SoL8nzSAzjBoUqGoi4x5kTIl2oyJJkdlncNinoNmlgNmlkMsMMoiU6LFgkxpyYxEKDI0Wmi01JjcC62UIRKZz5TyQYVxi3dWR95nfj7N+7zCf16pVCiVSh3e8Er4w/cTxXzr3HOy318dv/L/NhoNkiRZ8r7ra36fErkyg0vzVKHZbHaszbh3fbhvmFz13ucnxfy5PLZaLbLccefJ0j0TnNVh1fHxcX73d3+Xffv2keUeP4sZc4Wcm5vjwQcffFmmXQUXZw7LwS/o5s2bueOOO7jtttsgb+izqYhzjSA/CPvggw+yY8eO3DDlCZXcojbdLbfcwo/+6I9y3nnnddJw5TG59l0QBLRaLZ599lkmJiaW1K/IN3PL5XLHzNG3vvUtDh061GkTF8/91VoTRREjIyPccsst9Pb2dr5dhEAQBgG33HILcdkyCFcGKd1y6NL0Ldxh1X/O3/zN/7C22ow1aKqNFQhaWZtpQSAplSP6enoRQK1W5dqrr+INN9yAShPuvedunnz8cfoGBrjhxhtYu24D+w8e5LnnXmBicoZKrZtKrcbo6HGmpmdYtWYd5194Addddz33338vTz79FHOzs5RKJbq7akgpSLOEOI4ZGBjkTW+6hauuvIqFxQX6entZWFigVqsxNDTI1NQkBw4c5Jvf/Dq9Xb382//fv+XQgUM8+MADXHrZxaxePWzNDYUhpXLM4OAQlUqZxYVFHnroIQ4dOsQll1xCX/8A4+MTLMwvkqYZL720k7vvvpdWkoGIkDIkCATtpM1ifZEsS3P3Dna/SOZ7QiKQdHV3894ffy93/n//BRddcCGluIQ0VrWDfEkSmRtDEhmpNMyTMpk0mMmazKg6s7pOwzRp6jaJSjFCUA5iukWZQVGjX1bpDSr0xBW6wjJlQgIEoQkJcs1Dg8i3k+wAwtqfM9ZbnpE2GznN/87v/A7PPPMMSZL7h/JG/25gI4Rg1apV/MEf/AGVSqXzbDmcy8OqK4XL7/33388v//Iv89RTTxWjLBEcg4OD3H777bzvfe/rzIacVp3rt856y1133cVnPvOZzgDWnEQIXXHFFdxxxx1cffXV3ldfjna7zac//Wm+9KUvdZ4tl54xhp//+Z/n9ttv71iMcbzKFPaLenp6uOSSS+jv71+SnjzJIPp0OCsh5JAkCVFkbXwth1arxYMPPsjb3va2ToFErt6ol9lkXw4u7eWy6yrx1QYhBJ/85Ce58847OzNB15A+cS1Xb355tHda+/jx43zwgx/kW9/6VodIHURBQeNLX/oSt956a6cju7qnsN77yCOP8MEPfpBdu3Z1nhUhhKC3u4cXd+xgzdo1ne/avydGfEFg0z1RpoIQytWjXX6MsafRtbZ+deI44rZb38z6tWvpqtXYsH49SavN/ffey0PfeYjzz9/Km2+9lVqti2eee57HH3+cTBm2nHc+F150CSII+c7DDzM9M8uNN76R/sFBdux4ieeff56ZuRnSNIFcCBrsX2fDrb+/h65aD7WuKj09vaxaNUKpXLLKDkFAqVRidPQoaLjs8sswSnP48GEuuHAbAs3QwAAbN21iZmaayclJtmzZwurVq5FSMj83x33338+unbu54IKL2LRpMxOTkzz6yGM8+thj2EW2gCCIAEOr1WSxsUimMwIZILVVQsBYd+Bxtcq73/sefvt3f4ehkSGkyPdwjJ1tSts4aKFJhGbOtBnPmhxQ4zw1uot906OMLUwz21ygmbZp6YRUKxCCWAb0xV10y5j+qMK63iHOX7uRbf2bWBMN0RtU6BZlSoSYNKMcRd6hY4O1rKFsfoMAKa3VD0dzrv87OvFpSXkH2pfrF0W8GoWQXyZX5mK/d2rZfj6KdeSn5f46IXaqshYhvCW9IHeQ2W63l1UvF95KlfutmPft27fzyU9+kttuu61TzrOpz3MihBwcEfkMzuRGG++//37e8Y53dJ5HUbRkU34l2XCV46Sye1Yulzsjh1cL3MjmD/7gD7jzzjsJPKsEjhiMMbRaLUr58hF5PTgC8xUuXL0ePXqUf/7P/znf/OY3O9Nsn0B84vxf/+t/8da3vpVqtYopjDiNMaRpShRFKxZCfT29PPPMM6zfuAHhqdG6mZDIbb+5+BbFmZBBaIPdR8qDcG7KbSe7/S238ZZbb2Wgr58D+/dz/3338cLzL3Dh+du4/fbbEULy1a9+lV179rJ6zRquvuZaenr72b1nLzv37KGrq5d1GzZy7NgYh48cYXp2BpVb3QhCCfkB2CA/1JumaT5I0MRxmZ6eLuK4nO+pm44ymTHCGlQ1kiRr09PdTbPZtLPPJKGrVqO/f4C5uVmmpqYYGhrqMICk1WJ8YpJmq0WpZJecjIFWs007sa62rQVtUGlCK2nTTK0PIqGtdwVhIBABUanMDW+4iU9//rN0d3dTKlmm7ZY3ATAaZRQNqRnVTR5q7OJre57kyeO7adAm0SlKpXY5NAwwobXiYOkpINOKCEmcGcLUUApDRroH2L5mGzetv4JrKhcyJGJ6iAiR6NQQWJexVhrlh5JdGzua85mVs+zh+oL73dF1HMcdgXUyJvdqFEJF+GV0Ay73PIqiJctlfh/FOwDrx7N9buUrSHizGv/7FEyiOR7l4rhrIQSlUol2u40xhssvv5xPfepTvPnNb+6kYzyTYWeKVzZ/WgYus87kiiMcVzBXceQM2jEwR6ing4vnGsWvVCeAXHqvhuDWTLMsW7L05pbVXH7L5fLLiMMxR+0to7kGdsLbeutcOlJy8OvZrzMXx30njmNEYdPzVEFrTegNHk78Zpn0qeB+P3Eg1VosUEYjhC2vfaZ4+JFH+ccvfYm7vvY1vvGtb7Fj5y62btvG1a97PYePHudv//4f2Lv/IBdedDHXXnsdi4sNHnjwOxwZPcbmzVtZvWYtu3bv5pnnnuXYcWtcslwu09PTQ7VSpVbtore3l1q1Rimu0NvThzGGMIwJwxClDEm7TZpmKGXQGqQMieKILNNWFRnJ3PwCrXbK3PwirXbG8bFpXtyxm+Nj0zSaGfsPHGH37gO88OJuXtq5n8npeebnGoyNTXH4yHGOjo4xOT1Lo5nQalth1Gq1aDebHbcSYWS9yYpc+1nKgG3btvHvPvHrDA32US1FRFifQDI/09MiY15kjIomDzR284eP/y2f/ObnuXfiBY5UmoyVW8xUUxrdglaXoFXWNENFI9Q0I0O9pGlUBPMVw0xFM9NjGK8mvNA6xlf3PMxfPPQ/+ctn/44X1BFmSFkkRUeWmziaAJHXnaUt198dstx2o6NV4fGHKLKHdl2/8d/7QYDP/1y/8fdQotwCNfkqkuvjPh90gyZXL75BU/I+vJJ+S0GguW/4z9x3HF9x/MH9JV/W83k53gxLeAfwXwnOWgi5jLpCuUKawtJPFEWde8fAXKP4KFYgp0jbT8/9fbUEP++OKKVn4sPF8QnFldM99zuo8WaZ7jf33KXnX7u/Lq773X3Pz6P//qmClNbiW5DPfFweTqRh/7q2BWsBO3+axzHWZAISIUOkyAUwBi0ECsHs/AJPPv0Md993H8fGJ9l+xVVsv/xKdu7ew1f/6Ws0221ef8ONDA4P8+JLO9m5cxfd3d1sPe88kiThqaeeYt/efbRarSX2uYQQBGFEEFnvrFG5lBs0jajUuonLFUQQkqSKZjul3mzTbCW0k4xGK6HRTGininqzRZIp0syQaUhSTautyJR1yZ0pjZABYVSmWuuit7efnr5+unt66erppVypApI006SZopUkNJOUdpqAEIRhRHdXF93d3VQqVYIgzLXTBOs3bOSDH/o5rrnmKgIpkVaj2h4MNYYExTwpzydj/NXh+/i9x/43X51+gaPdCfUoIdMtEAohNUYqlFDoXC1bCIMJDBqDyAyBCdCBIA0MmTSoQLEYJuzNprjryJP84YN/xxenHuO4blInJRMZRtgTqkJrAmFt/2lv5u/g93GfxnzacfG+lzDeoM3BZ97LQQjr5sRdO77mAh7PMp7tPXdfjF/8lvaMLBf7b/G9YnDv+99wz3z4vLiYB/ddJ6wczlXbnLUQOlml+ESmc002x1RdpfiS1QWXjovrRvN+pfwgQeUWeN0ow5XR/S0SoA8/nshHG07BYbn4y6FYv/5zB9cOp8OJtl2arrGWcDrP8dIXwh6CNBiEsfsWJ3TjrDCyasB2E9sYUMqACFizbgOXX3U1A8MjvLRnLy/t2UtXbx8bNm9lanaOnbv3Mjk1hQxDGs0WO3fu5MUXdzA5NUWaWoUDZ4gxTVM7ylMKoy2jUcqqc7vZapYpkiSl1W7TzLWB6o0G8wuLzM8vMDc3x9zcHDMzszbMzjI7O8fCYp3FRp16q0WzndBKUtJUo7VBG+u0Li5VqFSq1Lq66erppauvl+6+Xrp6e+jq7qaryypVlKsVql01QrfHaqx0F0LQ29fH9Tdcz4//xE9QKpfI0twaubaWDRKhmSXhufph/vHAA3xx90M81xxlvNRkIWyRhQopNBKVa68pjFBo4WyZW3oSxlppEFgNPBNICCQmELQDw1yYMha1eLp+mC888TW+vu87TCSzNElRwhGCxGlNFgcrjqmeoJ8TtOzTvCwoOn0v4PLrvpkVFK78fOp8JmI8wbKSvK4kjoPjg65e/Gu/Tr/b8Msf5Hv5TlifSXmWwzkTQv69H1wGi9dOuPjvkFe638Du7w9iKBKQPxosjkSEJ5SKoxLhjQ5dmv5Sxung17P7677nhKT//GShQ2wF9+L2+VJCdARLvk8kxQlGZP86y2L22pic3yIQQUCSZoyNT7DjpZ08/uSTPPv8C4xNTDI7v8Cefft4YcdLTExOsbBY5+ix4+zcvYd9+w8wNTPdEfYibwPHRNI0zVVNm7RbLVrNlj0cmodmvU6jbtVakyTpCCfbJqZz5kbKkDCMiOISUalEuVKhUqtQqVYo18qUyjFxOSKMQ4IoQIYSk5vAEYEgjHOHfeWYuBwTlUKiOCSMgs5+yolvGkAQyICtW7fyI+9+F2vWrobcKZ8ACAyZUCyieLExyl1HH+WeQ09ytDGBClKkNISBsHtAgfVuHhh7PstgXWgoCVoIRK7YoKUiQ2FQKG3dR5hAYkJJFkAawYJsc7A+zjeee4BHDj3HdHsxn1OdOE9k8r0/n2n56NCUd+3i+7T3/YBxs/+CYOrQtgfXF4sC92zg6NZfPfHzxAr67LkKDqawJeJ+L9bHmWBlXOwUWK5h3LVfgOLvPrNdrlEdiu/9oASX3+Uas3i9XFz3zKFICO43//mp4AjFT9M994XlyYJba1u+DDYsB6MNJlcFd1pPYOyZIIPdxzaiM2Mit+DcaieMT0yye88e9uzbx9TMNIuNOuOTExw6coSpmWlmPa+qcwvzJPlylsjVv90+RBRFRLGdWSilSNoJrWaTdrNJmiT2DI7O3TxgR3pRFFEqlahWq9Rqtc6Zt2q1Rq2ri66ubrq6u+2SWdWegyuVI+JcmMhA5pOB3IWEXXDEuPMz7t5kKJ2RqRSdZagsJUmsPyFX96EM6Ovp5YorruCmm2+ywknnigDGzmSaQrO/PcG9o09z79FnOJROo0oGGWhE2ibQ9hwW5Cry1nISYIWPEsI6FUTkeVaYQGFXXu2+GARWBTw1RImmksFAucbQyCBhV5lUBrQQJLi0LNk4WnFMtYiltPRyplek2e8mzDIzDnft/3Xl8Z8X454tlvQ/r27859+LgCdkHVw+3PXZ4KyFkE88yz13GfVhllmG8uMViaCY9g8C/HpZ7to1arEx/XuWqT93789ETofl6s+v2+I3/p/23jtMjupMF39PVYfpSZJGo4CEJARIQgiBsUmWTY42CzgS1oAxGJN2Wdv4rjc8Nmuvr6/vLmm5/BaD7TXpLjbXNljY2ORgbHKwAIMIklBAOcyMJnSoOr8/qt7S199Up+keBei3n/N01Qnf+dIJFc6pWLATscGgUjxwAXzmsy17sdzpdDrcPw2A5c6WQeCmMuHy1XCJi0Xe85At5JH3PFgAnrUYKuSQ9wrwAeS8PIZy4avFjoGTCB/iOwbB1ZUPY4KvkLak02htzaC1NYNUOvgUAwzgJlwkU8HankymBW2tGbS1ZtDe1oa21la0Rgt7M0inW5BKpZFKppBIJJFwEnAd3lIGeC0XwAdgYa0HCz+4anGCPL4twPPz8Pm1VD8I8MOdvo2BccMhw7dwHBczpu+BQw8+BJMmTQrI26Aua3zkUMAGDODpta/jjytfwbtDmzCY9pFPWHihfh3fR8Jxgr3mwudLCFKCnRVggltvAVU4CQNrgwWuwavfLkzOIDnkoDufwb7pyThpynycPvtjOHne4dhr4kz0mBxW+/3Yajx4VIfwWe0TTJPp/Jcd/PaGvAqTbYxxUg62I5ZpJM92B92SjAN5kbIj1BVlHynqHoQgXiOEcjTpXPrNCqhLVy2ENjLjdpVQDlbdT9bxELIyXf/zuBrnZD6jrnpQwnZxQSLoHIvvkQNBvyjrAmk6TrBzRrol2iGheABiN2hgjBMGudEmYBwTPmowcMM3xhzXCRdjBuUcvtxhTJSfCydTyQRaWzPo6GxHZ2dwBdPe3o62tgza2lrR1taKTGsLMpkWZFrSSKdSSCWCq7cguHDdBBzHDesK/g0A13HgusFLJ64bXAXJIJ+5+daHH+7aYMPFvY4xSDguUqkkUskk0i1puIkE4AQDhGNczJo1GwcddDCsRfDJbwcAgt0LBo2Pxf3v4enVb2DJwAYMpSxyLpB3gi1IXdeFY9xgZ2vfCQYe44RfKjKI7gHCRF+8TRgg4QevZ7fkHbTlk+hGJ+Z2zMDR0z6Cz8w8EmfNORqn7bUAs8ZOxaatm/H4uy/iifdewcrBTShEm/zEDzDx/rNtQkrfl33E9gJ5Ig88lv8y32hBtz/ZN8TlGa0g7cRzmVYv6lonNDg4iFdeeQWbN2+GUYqR/7lcDq+++ir+4R/+ARDG4zMSycK4cePwoQ99KGq4cpStg9UdAsdxcNFFF+Gv/uqvoje0EOpFvmlijMHatWuxbNky9Pb2DhsopCNs2rQJV111FV544QVRUwDpINZaLFy4EMcdd1y0Alo6jQ0beKFQwNKlS/G//tf/wooVKxTFbTDGIJ1M4ctf/jJa29vgi6uhGTNmYNas2WHOYCClbY0J+P7P/+8G/OTHP8K7S5bAeMHmpNbaYLe48NZNtEM0gsHGwsKzwYsEgaoCX3AcF7YQzOj58bVEMhl8ItsC+Xwhei7E7WHS6TQS4dZI1gYdJD2WemHHaJzg+zyO48BNJMJPXAcP203Y+LbBBrfYoqjw4Va45of5+RKE53nw/PCTrybo9IOvpwIJ+rvjIFvII+t7GBwcRGe6DV8+5zxc8T++gfYxnbDGwnWAgp+D7/hYiyxufedx3PPus1hS2IDBVAG+4yMJJ1iTFTabvO/BSSbI8bYhwgS3RMNDONaH6xeQLFi0+S0Yb8Zgt9YJ2GvcdOwzaSZmde2OKc4YpG0Wm3M9eLVvBZ5e8TpeW7kMEzJd+Ot9j8EJU/ZHp03A2OB5FsRat0WLFmHTpk3RFjX0I8Jai0wmg0MPPbTodeY4NHqdkO/72Lx5M1588UVAtT3yyvNFixbhxhtvxNKlS8vWPVLIusgv+enq6sKMGTPQ1dWli40KOHEAgD333BMXXXQRDjzwwJJ6rAV1DUIrVqzApZdeildeeSV6v5yMyhmMH+6d1NvbWzSaW7WIzRiDww47DD/96U+j/aPy+XzRA/1dBX64iWhHR0e0HseEnQzl4QBrjMHdd9+Nm266Ca+88koRDQhn9MI1Rj09PcE+bkon1Cn/OQhlMhlAzDSZl/lyuRx6e3tjHxwT1lr0923Fpz/zaazfsCGaJHieh6985Sv4l3/5DgCEVx/bblE4TiDHY48+gv+47lo8+dijcMLnRNZa+NbAC7eW4Y5j0VYzNlihGdxeA2CCh/VeIey0w1eXqetAzw4KuWAQsuG+fRyEaAeEOg06v0DHtIe1QUdjwp0fHBNc8VgAVrxcQQQ6ZZfOly3ChaOOCa/q+CKHF+z5hmA7nSAtLOr7ML4H3xbgW4O876FggKFcHnvtPgOXXXARzj33HJikE96/8DGYHwASDl4trMa1L96LJ3rewZbEEDzHC3TmA45vkTLBBCjvezCug5zx4YUPhIwFnDAEux4YJDwfbXAw1ktjj9REfKh7Ng6cMhezxs7AGLcTeZvH5kIPVvavwVMbXsXD7/4ZK7Nb4BuDiWjFObOOwOkzP4qp7lgkbSqgG64Vcl0XV1xxBR5++GFs2LAhagMEJ2u77bYbHnzwQXR0dAzTuUQjByEbvuX29NNP43Of+1zUh3HwZJsJ/CR447Kvrw8I36LT7bGRkO0V4TZfF198MY488kiddVSQSCSiPr6lpQUdHR1Fr6XXg7oGoXfffRdf+MIX8Kc//QlWDCjSyCRPBXLWQ+eT1RtjcPTRR+Puu+9GR0dHFCc7610FWi59HnTQwSK0RCKBn//859EVjswv9aYbWSkd87/UlZDUIwe2SrDhIDR7zhysXrO6KO2KK67AVVddVRS3jadgJr5u3Vr8+7/9G/77tluRHxyIGrK1266EPAtYY+DZcOAxwUBkTcBzwSvAegge/FsDa01wleQYpFIppJIpWGuRHcqFeg1upyWTCbS0ZNDSkobjuvD94OrKWgvf37ZoMvCxYHC1JrjK8j0/HDgQvHZstg00wZtfwVoYE8Yzjf7KwY66p14cJ3h2ZRFcNVnfh18owHEQvI1mLHII1hF9/JAFuOzCi3D0UUcFtN1gqVUWWQyigAfWvIibXn8QL+XfQzYd6MtD8Jp7Im8ws3MSprRPwIpVy9GXG8DWlI+sa+E7wQsKCc8i5RkkPYMMkug0LZiW6cIhM/fDwZP2xR6pCehAC1wYDPh5rBzaiOfX/QWPv/UcXutbhQ0dPnIZB17ew/ghFydPnIdz9jwSHxk3By02BTe8ymLbv+GGG3DNNddg2bJlgR5VF5RIJDBjxgw8//zzGDt2bFGaRiMHIYS7ODz66KM48cQTo/4MMe1Zwqq94LQ8I0Vc2+cE9thjj8U3vvENnHDCCbrYqID9FXW3rX0P71NqRV3PhGSjQsioNBobIc+ZhwrVxmLDZToFYyOW0GV3NlAW8q3PqRe55x7/pVGl3jS0/ktB8yFRzQBEeHbbcwzE2MWqZ1oBf8FtrImTdsPRxxyLDx90MIyTCF93dmDDFwrcRCJY5GiA4CIk+JaQQbBlDTyLhHGQcA3gWfiFcINOG/xbz8L6CHZfcJOwcJAveHATSSRTLcFnvk3wrMhNuHCTDtykC+MmUPAtsvkCsvkCcoU8Cr4Haw1cvood3upLOAaO9WG9AvxC8OE5eAVYrwDreYDvA3741h83EBXBhYOkm0AqkYRrnEDEgg8b7gnnuEnAJGAcN3ge5TgwxmLKjKmYOnM6fC6/Cddl+XAxAGDZ5jXozfcBpgB4WVg/F7y27VikUxnMSE3E5XPOwpcPOAnz3UnoHkojk3eR8hykPIOWgkHboMGkwRYc1DINF8w/Cd/6+Jdx/rSTcXBqT4xHC3zksdJfj4d6nsX/99qduHnRr/B8bhnWtw9hKAMUnGBg9KzFhv4+rB3owyB8+KZ44mnDbV/GjRsX+YiGDa/6TczCT+lj1fo/se0WcVAv+6I4GkbdetPtlmX4X6pPqwTSlRMhOWmBavuy/6wWWk+UvdLaJqlryi37kVJ9Sq2oaxAiIzLIjqmUgUuBCpbPTxivFUkjeeGiw1rq2VlAeeWlPPW4s8ojZ0PSvpJ/2oMOHNyK8nHU0Ufj86efjhkz94SbSMA3BolUCtlsFrl8HslUKtp4Mxpg7LZnN8FdL3YG2/zDhC++JBIJJNzgRYJkMhm84dbWFjwPSiQA1fE4jhPdpuNkgOkm7BhSqdSwTw6wDDuO4J0+E21JJEEf9X2/6LVra4OrH/AYwc7YcJzw9p+Fa4CkcTCucwzGjhsbDEB+MAjBAAUA/fCwbqAXg/ksAB+JhIOk6wTKsj4K+QI2r9+AQfTi4+Pn4ysf/zQOnzQX03JtGLvZYFyPi5l2PI6f8RH87XFn4hsf/xJOmfwxzHYmogMuLIBNyOKZLW/glj/fg5v/+As8seYVrGvPoretgME0gu8RhVfUngNsGtqKTdl+5GHhha/KO+KKcMqUKdG+cNKGshOW2/bItiB9jm2lWtAOpEueJGgb+jFEJ8slBvQd8mvCdY+18KOfdZE3thspcz2Q/kZQ9kQiEclIv5ftg/lGG3XVUNSgVJB5RgIaA2rGHackmb4rgbxL/qm/7eUAtUDalg3OiFklz/m8iHDCgSudTuNTn/40rvzOd/CxIw5HIplALhfsbOCEL0kU/ODWV1BeTHAQruKPGvq221vSD1zXRTKRQCqZhBPy4YZvrRkxs7U25NsGtxmCN+CClxGsb1Hw8ih4OdgwnYNcKpWKBiGGdEu48DSdRDLlIpF04CYMjGNh4QVrbsKFn/KcbwgiXMjqGcB3EL4IAbjWIAGDztY2dLa2w+EAFI7FeQCDsNg8NIB8+DVUeIDvWcADEtaBsT7WDGzCs0tfQpufwIczM/Dl+Z/Al+eeiLOmfxwX7HsSrjj0TFyyz6k4rn0+9kyMwVjHhYschpDDc7mluPmt3+OGl3+DB9a+jlXpPAbaUhh0AN91gw1VfQeO58Mg2F2hPzuErUODKCD4nAOvttl2x48fH3XCtBvTt9mn+CNwBG3pi8mbTC8H6QvsW1he+jbBNKYXwv3fmCYDedE0SoFXIbLty/9qZaoE2TYgJkWl+JR1U558uE/laGFUejkKopVaKiA0BjsvdlpSGZKOhIzTjrGzB0I6IqHz7AyQ9rSigebz+WiHXTmLY+cDdjbGQUfHGBx5zNH4l3/9V/zbv/87Tj/jDMzdd1/stttu6Bw7Bi0tLRF9hKvt4/Rgw28RkQ82NCfcp5B0gry6c2FjDK4UOIDyygbhjgX5XA75QvAwljSYLxlusplKBZueBq9oF8+OWbfvb/tcRTDj3XZlxG8qWesBDoI1UAbwCgXAWjg22ME65brwC1708p0FUIAPDxZ568FxE4Bx4VvAWAcJuHAtgp203QJeWPIaNhY2I2UMZqUn4pO7fwRnzz0en51xOBZ0zsZe6fEYZ1rgwkEOPpZ563D324/gJ8/eg9+++zxeL2zE+lQBvUmLbMKBdZOw1sD4Bkk3gYR1YD0brPMyBnnrBx/BC2/fWnH10NbWtm3AF/Zl+jb7b7sNJG1Am9O/5BVLqaBpsG7SY5B5SVPSlvzJPNLHdN1xQYLnkp9GQcpG+aTslEf6reTPhFd5o4m6XkxYtmwZvvCFL+Cpp54apjit2Epg/qOOOgoLFy5Ea2srIJzFF/tNSWd5P8Bai7vuugtXX3119Oo1naYWSN3YmLfj6oG1Fr29vdhnn32wZs0aQNjswgsvxPe///3oPr/mg8ehRwAIZo69Pb1Yu3YNNm7aiKHBQeQLBRQK4W0JTvkVothwIEE0gQnW8CAcZIBgwWtwBSQ6EgQvFEjyxgRrdSz1zttEJnw7zgnWAxkTrllC8Pq45GEbbDTIQdiRepFuK4taAJ4bPIBOGQPrFwJefR+zZs3BnjP3Ci6aYGBdIOcAW5DDagzi/zx1B/7UuxhrnH7kXR/GMfCc4Had4xm02RTG9zq47JBTceLEj2BSohMpBK+dBzc7ffjWQ9YAq/xe/HnDm3hq+Yt4bfO7WOL1oS8dbOsTvDDhAwkn2Eg8X4CxBslUEvm8B3gGbXkXM20HPrvnx/G5OUdhN7SizQafNeEVSF9fH0477TQ88cQTkY44APnh7hrTpk3D008/PewVZOqVvrdixQocccQRWLFiRTQoxGHatGn4xje+gb/9278FVP8k/bRQKOCxxx7DSSedFKXFQfJh1KLWalCKLuNL0SOvxx9/fFUvJkj/lH4YJ5vWrT4fLTRkEHrmmWeKZhkEha0EOdvhINTe3g4oo0gFEtKBSGdXAQdWay1+/vOfFw1CUA5UDaRz2VEahPbdd1+89957RWnnnHMO/vVf/xXTpk0DSjjxNrsFC1MJHpkgY9AzV+n0xY0kCPQFY7iINcwbLoYtKo9ghwIMo1UbtJk0CZmu0xCmWwS34yyAhLVwuKcRbHCPzgZfVwWCw5wD9COPdRjEj1+6G79b+xJWOn0oJANVFIyF5xi41iDtOejsd3B49xx8ef/TMK91N4xBAoBBAcFgtTnfj9d7VuDpdW/gmXWv453BVdiUGMRg0oGfSMCFA1OwKPg+fDf4GKDxvODZnOPA9ywc30XbkME+qQk4Y68jccrMBZiANFptMDmgDyxZsiTqN6S+mc5B6Nlnn8XYsWMj/9FXJ9ZarFq1CkcccUT0BeFS2H333fGNb3wDf/d3fwerJrXsWxC+HScHITd8640DKH0Lymeseju4GnDQjfO5UjRYV62DEOUk/+RV163bQVyeRqPuXlsbph4EM9ptrzpKRXCAKWWcXR1S1loceXtC8iTts379eqxYsSI6j+sMtpXlK14m7JxNcMwBJDyvHIht57IBBUHmkmWKr4Z2Fl0Hu4xv49WGT41s+JPiOwDSMOiAi+mdE9DqtMDCge848IyFb4MBzMJHvpBDIWnxl03L8drQSmyy/RhCHoPIYb3dipe2LsXdy/+EO95+CPetfBYv9y3FupZBbG3xUEj6wU7bXgHwfSSNgxQcuNYGvBoLz88H++A5wSv24zOdmNQ6Bq1w4YayyP5hyZIlGBwcLIqjz7OzRMzu7jq/LFcNZB0aMt4TH+aU9UD4FsGBbCSQdfKY5yOlGQefL9so3qXe+JyI6ezXq9VtPajrSoiLVZ9//vmih30QSqyGvBEziY997GP40Y9+VLRILY5GoVBAf38/BgcHo5nKrgg6x7333osf/vCH+POf/wyUkLkSqEf+N/pKqKenB/vuuy/WrFkT1WGtxdy5c/E3f/M3uPjii2HEglw9c5WQvELdg4/Lr7FtgWjxMwTE+R7Pw1FHrvQRF2Wx9W6jWzyoEVZcuMXxwPg42hI23C/PcUywy4GxKJjgmYpjHbg2HLhDcXwDFFBADgU8ufl1/Mei+/BsdgUG0h58U4ANVogCBQ/JQrD+J501OGbWR3Dm9MOxd8sE9AxuxWubV+Cp9W/ghQ1vY1V2CwppH0NOHrmUDw8eXCTg+C5MwcDxneAbRo4JXrCAB98Jro6MSQB+AuMGk/jMlINw7l5HYr/2aWhBIlonRN/46U9/iptvvhkrV66MdKbtPn36dNx6661oa2uL4qTPMe+aNWvwla98BWvWrImd/BCTJk3COeecgzPPPBNWXLVIGGOQy+Xw5JNP4rzzzoteHpDp0m9TqVT4Zdt0NHEmNG0NXgXxn3EDAwPo6ekBSkzmWH+1V0K+76O3txf9/f1F/Jnw7T/2m77vo7OzM9I3dcS8o4m6BqEtW7bgF7/4Bd555x24MW+y1AprLfbcc0+ce+650cMw0pO0rbVYu3Ytnn32Wbz66qvIh5+prkOUHQI6guM4ePXVV/GHP/whutU1EllkA7GjNAjNnTsX69atgxWzpM7OTnzuc5/Dd7/7Xey2225RA2cjg+Atggmfz4RxQeca3I6TeaVMxdg2OKBCR88aApJB58yBiCUkb6XoaLBIwHZYi+BD+25ZHm0wIBpjAD98ScFYFGCRQPCiAWywDgeOQfAStw8LD28XNuDal+/F/Rv/gg1uPwrJAoI7YAbW85DyXRgPcH2D3VJjcPLMQzGtbTxWbV6Dl1a9iTd716CnpYBcGsGGq46PguMDMHB8A8d3kUACDoI3B314wSJkx0PBWFgn2ODUGXIwwx+LL806Bp+f8VFMcTuRDPmmLxhjcN999+HFF1/E0NBQJDvTOANPJoP1WRJMN+HtJHbSuVwuejmhHEhT3l6T9jBhe1yyZAluueWWonjyaMWt/+nTp+Poo4/GlClTIttKvsrBdd3o1XXSBIAXXngBjzzySMk30shLtYNQLpfD448/jueeey6S14rbjKzfdV0cc8wx2H///ZFKpQDxmGS0UdcgtD0hGzIALFq0CFdddRXuuOMOnfUDC9nR2VEYhPr6+opeTGBdALDvvvvisssuw9lnn4329vaiNDo/z63hWBB8csExw+9P8+UAKZM8rhbab4yRT6SKb+w1ArIeGWfEhCMOxoS7P8QMQg6coDOPbs8Brg0/zWB8bEYe//fdP+LnS57Em9n3MNiSR87xYY0Dx1qgACRcN3i2lPfQnW5Dwhpk81kM2AKyro9CwsI4NtpSyQ93IneQAKwDE74UYVwDHwUU/Hywu4PxYZIpmJxB59YEPto1B1/a+wQcNWEftMOFG/JdCwrhfoaHHHIItmzZopOLMHXqVPzpT3/C7rvvXlK3ALBy5UpcffXVuO6664r8YaQwxuCII47AtddeiwMPPFAnjxg33ngjvvGNb2BgYEAnASMYhAYGBvCP//iPuP7666O4OPmNMbjmmmtw/vnno7OzsyhttFHaak00oSAHAsIJn+O9/vrr+MlPfoI//vGP6OnpiTpdiFe1rQ2fawg4wfcNok7b2mCBqqMWA7LeamaZFWG58FUnNBbUkx/eky/1jIMyybRADwg+o6CaafAshlcNQAoJHDR9LuaN2R1jvCQcL0h3AFgbLLJ0E4ngjbmUg7UYwEpsxdpkDj1pD4NJH3nHogDwww9wkYSDcAcHN9jR3Pf94NMT1gKugU0Eu3HnhrJI5oHJpg2H7jYHe3ZNQmqEA1ATHzw0B6EmqkYul4s6TA4QwbqXIO6ll17CxRdfjLvuugs9PT1RGgcjOYAZE7zmbGHh+WF6GNhpEyxnw9sG5WHD64XiwYoD2bbzIDQK5I/yyWM3XH8kZScvRq3bABBt4MoPoIOaCtccAcHzI+O6wfMYC8ww3Thi9wMwa+xUOPlgwapX8GGsAy/vo79/MLhV5BjkrI+88eG7BnBcOE4Kxibh+0nkTQoFNw0vkUTeOMhaH1nrw3OCgYdP1Kw1yBYKgOsilWxFJuvgsMmz8bGu2Zhk2mD9wmiP8U28T9AchJqoGsnwUwj6+ZscmJYvX46vf/3rOPbYY/G9730PDz74IBYvXoyNGzdGn1dAuI7G94NV9tzq34a3rdhpM47xJlwYK+OrCdvyRyw3HKyLfPvi9XvJAwfY4Cpm+KDkiwHbMcGOCW64LRBg4UaDQPC5OeMm4RgHGQCHTJiN4/c4GLNbd0P7gIM2m0LaJJFwXLS2ZFAoFGCsh4TrIJFIwXWTwXeR/OClA8c6sEjAOsHn1fOFQrCTuQ1fcnAM4ARv3vnWIJFogRlyMHarg4+M2xOn7PlxzOmYgg4njaSTgg2nBE00UQ7NQaiJmsCO1KoBwxfbqAwNDeGVV17BDTfcgPPPPx+f+MQncOihh2Lu3LmYNWsW5syejdmzZ+OA+fvjM5/+NGDDF7PDKx5eQbEOYwzefPNNLFiwAPvttx/23ntvzJo1C7NDOkGYFQYdNxuzZs0SYQ5mz56DuXP3xd/+7eVavBHDcRysWrUK3/ve9zB79mzsu+++2HvvvTFnzpyo7jlz5kTh+uuvRy6Xi/SJ8HrHcV14Pq+DHDzy0CP46zPPwuxZs3Ds0cfgph/+sOiOZnC9YdCKJCaaVhw9eT4+O30B9ktNQftQEiYX3nu0HjLJBFwfcD0Lp+ADOR8mBzh5wC0YJHwHjg0nFK6LTCqFtkQSaWPg+F7w0oIxME4SrpOE9VxkBlx8rH0WLt73kziwcxrGmhQSAGy4W0ITTVRCcxBqompwts41BRCDEgckEz47yOfz2LJlC9asWYPly5dj6dKlQViyFO+88w7eefttvPPOO1i1alVEl4OOfBZE+rlcDitXrsRbb70VlH/nHbz99tsivCPCkjAE+WR4O6x36dKlWL9+fZF8IwV14XkeNm/ejHfeeQdvvfUWlixZEtVXzOvb0bd0JEx4izL4jEWgk8GBQax5bzWWvP0Onn/uOfz6nl/jpZdegmMceL4HFw4S4ULSFt/BNHccjpnyYXx67tGYM2Z3pIYs0k4CQ7kh5HKDcJzguZzPKzRr4YQ7lzuODwMP1svB+HlYvwDfy8N6eYBfvvUBN2+RGXIxeagVH+veB2ftdxw+0jkT3U4rUtZFwrrB4lb9ALCJJmLQHISaqBr5fD4aICBuQfGYVy9WvFgA8dyD+YwNNiO1ljtJI7jlI25PMS/BY9LZ9gSpPEo99wkGuca4P/nV51IXMo8+5mC7LY77OACWn88AkB0cwiuLFuH2W29DX28fEtZBwg92ReALDBmkMS01ER+b+CF8Yo/DsGfbRHi5PEzahZewyCKPrOMjn0DwTMgNVr76CQ++k4dBDo7NImksHOPDN8FaIOsEV1SJPDAmn8I+TjfOmvZRXL7PqThkzN4Y57TCsW64B4WBYy2c8FMcTTRRDo1phU18ICAfoMtBBTEdMTtgOUhBDRsGwdtxQdq22b/skBHTqQfnMpRG8DiDeYrpjMYTCykzQt61zhgv8xcNTDLecYItcsK4jZs24v7f349f/uKXAIIB3fjB59ABA9cCGbiYmOzE3h27ozvdAeQtUm4Srgck/ODjdakCkPKC4FoL41sY6yMJH2lrkPKAZB5I5g1SOYPMkIOx+TT2apmIo6btjzPnHo0zpy/AYZ3TMcm0IWUTANzwlZBt+1400UQl7FKDkG6s3C2ZW+vzU86VQql8+jtGpWCMieomPR3Ij6ZZ6tgJv23T0tJS9A2bdDqNTCYTfe48nU4XvSFWDb9QgwI7Rd4O8jwPuVwO2Wy2bOBnFyhbJpNBa2trEW8MrFPWjbDbj3YuMAa+tRgaGkQ2m8XQ0FBRXYzL5YIvpfKBfiiRCOXBuo3cj63KstVCDjwaWu/lUHRVxIEq7NgtLPL5ApYuW4of/ehHePjhhzGUHQJMcGVpjIWBBwd5WOQx5OcxkMsihTQS/QZjh9KY6YzHbNOFmYUOTB5KY8yQg9Yhg3TWQSrrIJV1kc4l0TKURMdgCyYMdWAvOxGHtM7CKVMOxdl7HYMv7HE0Pjn1I5jbthvafRdp68K1TvjSRLDJaxCq801C66dUO9Xtrpo2YMLbxa2trVH74n8mkxlGu1JgO8zlchgaGkI+n8fg4CAGBwNfpu/Sp+XuC/o2LGHDq2a2LfLW2tqKZDIZ9SuZTAYp8an6XR271GJVebxq1So8+uijeP7554vSqjGMfIYht9v485//jGeffTZayV0KY8aMwUEHHYR9990XiHkTivSMMXjyySfxyiuvRE7IToZ5eLz33nvjwx/+MCZMmACIqw7yynL9/f145JFHsHz58ogfyi9pW7VYVeuPLxMAwLp16/DQQw9VXBjo+z6WLl0avThAviBoWmsxMDCA+++/P9pKhXVrOyUSCUydOhVf//rX4YTfE9KvYFOe1atX44YbbkBvb28kX7XQegGAVCqFz3zmM7jzzjt19hHBWot3330X119/Pa677rooLg7GGPzTP/0T/vmf/zl2ITF5BYBf//rXuPrqq/HkH/4QpIV5WlpacPrpp+Paa67d9pVS48FHHkPIY4UdxEPr3sAtTy/EFicPN+FgXvdU7DN2OtqcFAbzQ9iY7cXG3Fb0elkM+jnkbfD5CNe4aHPbMDbZjvHJTuyWHoc9OiZg5rhJmJrpwji0oMUapOAGX7012wae0NJAtGFs5faI0Lds+Aq+7/tYv349rr322mi7GatubdL/Ojs78fWvfz3SQSls2bIFDz/8MJ544omorG5bqLL/oH0ymQy6u7ujyWahUIjKS59rb2/Hvvvui4MPPnhYvZKetRZPPPEE7rnnnui5K/OwDGnPmTMHxxxzDObOnSs4G45dYbHqLjMIQTVqP3wbi507nbcaMB8dxw23HPrhD3+I//k//yc2bdqkixRhr732whVXXIFzzz03itMDGv//4R/+ATfffDOy2WwUT4eikxlj8JnPfAaXX345PvzhD0dxpOeHCx2ttVizZg0uuugiPPLII7GOJB260iCE8CroxRdfxIUXXoi33npLUCuGMQZjxozBE088gSlTpsAXVyWOWFjq+z42btyIc845B08//TQK4YfANK+kyQ/FsSzjNb8AMDQ0VCRftdB6wS44CP0hHIQQlk8mk1iwYAF+/vOfo7u7O4y3AAoYQh5L8pvxu3dfxn0v/AntE8Zh5qRp+Oge+2BO61R0OGnkkcMmb2swCGUHMJDPouAVYCyCQailHeNax6A72YlxphUdSKEVDtIwSFkTfMI8vJESbg0nxpuRDUL0c/pCNvxGFUKdSL/lse/7yGQyRbc642DDzzTkcrlIt6yHdRgxMJQD7fP000/jn/7pn7Bo0aIimtruU6ZMwXnnnYd//Md/jOpgH6CPc7lcxKOUPZFIFJ274Vd/K8m9KwxC5SXYyUBD0SFTqRRaW1ujy3betioXWltb0dbWhvb2diSTSbS1tRVdkuuZeBx4ydza2hqFZDI5rC5espNvBogOilcPrusWlWtpaYlod3R0RHLyg2CosdFIp5M65C0F3kooF7Zu3RrdzshkMmhvb0dHRwfa2tqQCW8RkEfSlwtVJb88zufzGBgYwNatWzE4OIiBgQEMDAxEx/L2huyAPqiQ9h4zZkw0AQhTo90OWvMpTMi1YsHEfXDWvsfii/udhCPa98V0pwOTkMI0tGE/dzIOz8zCJ8cegM9NOARnTV6Av97tYzhj8qE4eew8LEhNw1wzBlOQwlgArbBIAzAwsMaBdYKdgLxwQ1U/HH6qH3q2wQl33uBExFpbdDuKbZa3w9geWltbK3bEhOM40e1j3R+0tbUNS4sLbJMtLS1wHAf5fD665TY0NISBgYHoNpz052w2CyMmWhxAtS+zT5Iyt7e3D+MhFe7v9n5AddbbCcBOzYazEPm1P84SdGcfF6z6IiL//XBlv3aKOJCWBh2LMOqtMIRXHrJeeS4dlHVIRyV9plcL8iH5prwI9UfdlAssz85C60p2iDzWdTPIvKRNevyXPEndstwHHf39/ZEdAp0YGLhImCS6M2Nx+N4H4pyPnYJjdzsQe2AMxiOJcUijHWm0IY12pNCBFMYgiXFIYqzvYoyfxBibQKd1MAYGHXDQZh20WAcJ6wC+CV5khI32peDgAw4+VoUawCt+OXmz4s1Lgml6p+tSoM9JP46jVynovPRP9h2aVydc3M2rfcpHGWV/wTJMl/Tj+H6/tINdRgoawBefSeazCWtttPCvXPDDW3i82nEcJ6Lhht+erwaFQqFo8KCDyAGCjsJbhiZmIGBD47EbLvyUcso8dOi4ZzLVgDogLwQbAumVCwQnAVrHlDufzxfdTpQB4WDsia3kk8nksI6HPNmwkTOPbLgfNJjwEwKe52H58uXR9kjWch2PA8dz0GbS2D3dhenpLnSEg0nKc2A8B9Z3ULAuPLjw4cCHCx8urEnCtw5868AxCRg42+612fBSJ1S99IVg+Av/OfDIkakK0DdseNuMNmabhZpMUWa903Yp0Pel30tfY33al0sFSZO6YDzr4D/7C1mOZWSbZ5uQfYg8J0j3/dIOdplBiEZkZ+26brAxY9hx8S2ZcoGdOIDofnMi5lv3lcD8Xngl4ag1MWxEHPB46Sw7ZYTOSUeWz05sOLOVgyLrYxluoVONI+pBi41A3tZjveUCOwo2KoJphLQH06kjxjEfG1lObMfPhkk+3XD2KPN8UGFFJ71ixQq89tprGBoaCjqwQiF48zDYXQdJAGkLZKxB0geMF6zbMduWB0W3zpww3riAcZxo/PB9D77PbXtcwFU2DEO9oG8WCoXIt/lshOdyQid9pBqwDMQEKK4s+SgV2BZM2J+wDcnbY7IdE7INsa/I5/Ow4ioqkUgMu9VuRBuSfMs+Z1fHLiOFvCKQBmajrAWFQiG6PGaDlrQrgQ4tnVo6lxwkOVDQcflPyFthpM1zlpN1UlY6ZDVgg4WgD6FH6fDlAh2f8rETYKMgffktFD+8mmN9zMM06kfGc6A1otGzkUs/+KCBdmBHNDQ0hNtvvx1r1qwJ7JJwYVwDDz4Kls/iAB9+0NKTTvAGm7WAh2DdUBiMF6w3gg94AHKwyMHAdxOwbiLYXdvaYAeg8GpHdh4WQTnPBM+J4IpLpDLwxW1wdurW2ugbYdpXOCGRkx76VSVIf6fvsu1X26GzHNs82yN9nu2Z/5JPJ5yEcbkBZWU8xJUfZXLCuzU6X7Uy7wqoTvM7GdgYCbeGWzTsFNnByQ4aVRqXziwdmqBTk44vbjMwjuXYoUA4qISMo7NzVijpVQM2dvIC0elLecoFTU/KIY/d8E3Bcnk5kFlxS0XyJXnzw22AmKZ5+aCAdqBfWGvx6KOP4r//+7+xatUqwBhY+MF3gRwDHz4KthDeSfPhGx8+gtewjbHRm9VAOFiYbT0Cl5uGH/EOgjEwTjCw0VV1BxKOT8OeFZWCo96spJ/ITpxtSrYz+gf/qwH9SrYr6lG2i0ogzwRpyAGDbY3plIWyyfJQAyQnbeSJ7R2iT6hF7p0d2odqhjYclRl3TqPIc12+FErRpEGkUUhXdv7aIWT5UvQqBdYl/wmmy7wInUhC66tUHDtszW81YDlZt5ZfyxYXWK+ON0IXbGi+GOxZL8tL/uWxrEPLyONqr/4aDc2z/IeYfOirPsT4gJbRilu71cCKK88tW7bg9ttvxy233ILFi9+AVyjAgYUxPqz1wishDz4shnKD+NPTf8KyZe8glx0CEHxGHMaHDx+e4TeFLJIwSFgD1zdwfHHbLcbtCp4H2OBDhMFn1wFw57gYWWVATFsppzvtc1a1iXIBMf6j+4VqoPkhbVkP+ZK88ViWKVcn00zYhmR/KeuqBNYpJy+6XklLyxGXXkvfXQl1D0KIefCmBYRQBJ1AOkMtwsi8vnjDi4pjPVJ5evBjHp0PJTpCHVg3Ox6Eg0tcPdpYLCPj5K0t2SgkeC55qBaUlYGw4a3Mci8ZaLmZz4hZalwd8urUqlsJWn7dMaCMT5SKH01QfmkDKBtxFiyfWTCPLE89yA5B06oGUu9Lly7Fbbfdhquvvhq//MX/w1/+8hds2rQR2cFBDA70Y+26dXjyj0/ixh/ehB/84H9j8RtvwMvlAM8LrlcMgtt04S/Y+y14ruQgGqeiASj4BhTbgYUb7nXnmOCzE/B8wLMwttgn4oL2LekzUjfUj4yX/5quDhDPZNlXGWEH5qkGmi/ND9PksewfyEelwLLsQxgnr7IqgXUjhj+ZxjsTkm+C5XRb17YbKeparNrX14dHH30U69atgy/u/UMw7oT3cSdOnIhPfOIT8GOeA8QJXg5+uCDytddew9tvv11EQ3b6JuwMZs+ejQULFhTRMOL1aA4Ajz76KBYuXIi+vr6ivBqZTAZTp07FhAkTorIaNNLSpUuxdu3aYQ6g5T/kkENw3HHHYcaMGZEsWi/WWqxduxbnnnsuHnzwwaI0iDr5H7dYVdK04aC3fPly/PjHP44+2x0HE95GOOCAA4ru1Us7U5++7+O1117DwMBAkZ7j6t6yZQvuvvvuIpkb5dyE1gtqXKz67rvv4rnnnsOWLVsiOeMGkaGhIaxatQpr166NbkmyXfCfsv3VX/0VTj755Oi2kwR5RcxiVZ2X+m5pacHJJ38SJ5xwPPbeey9MnDQRrZkM8gUPfVv7sWLlSix+YzHeXbYMe02fge6xXUFdjoE1Br4xsMYCPpBwErDWx8SJE3HooYeiu7s7qDfcEcECCL5+a+B7FvlCDg898BDWr1sL6237/pBxHfhVNG36AsTtpvb2dpx22mmx62GkbwwMDOC3v/0tBgYGyvpMW1sb9ttvv2iHAelvfo1vmrKeJ554Al/72tfw0ksvFaWTFm09btw4HH744TjllFOieHb+pcA6ZLuSNKdMmYL58+dj2rRpumgR8vk8fvGLX+Dhhx+Oyks69EkAmDFjBiZOnDjsrgv7bPZ1XV1dOOSQQzB58mQglLeSPOVQ1yC0atUqfPWrX8Wrr74azQBJjkI4joNUKoUPf/jDuPXWW6M0pldreAjDWGvx+uuv45ZbbsHChQuHNXDpVMlkEp///Odx5ZVXRnUyf0G8rm2tRU9PDzZv3hw7M5d477338Itf/AIPPPBAEU80ktTDhRdeiJNPPjl67kF5ySc7qI6ODowbNw7pcN81QurHNngQIgYGBrBu3bqiFwo0rLUYHBzEV77yFWzatCmyLQd9Hhtj0NHRgW9+85uYP39+9DoxRAdD2XO5HJYsWYLTTjttmO7IbyOg9YIaB6FHHnkEV111Fd566y0kk0nkcrmo0Um9Tp48GaecckqRPH74AJoDF/OPGzcOXV1dRXHSN3hcahDiP3U+fvx4fOtb38JJnzgJBkDCdeG6Qb2eF078rA/r+bj+mmvxzJ+eRv/AQPisKPiSq3EcGM8PvmlkLfY/YH9869vfwvz5+yF4PhQMPtYCrssRyWLr1q34ype/jEV//jO8fCHQjTHwYeFHW/mUhrQ3r8p333133H333ejo6IjSpJ6oozVr1uCss86KtogqhcmTJ+PLX/4yzj777Mh21B3pVtuRMn+5QYj5TDgR7ujowIQJE6I6q4ENBwkvXMog29pHP/pRnH/++TjiiCN0sSJYa7F+/Xr09PREsjpiYJO4+eab8eCDD6K/vz/iXfpZIpGA7/vYZ5998M1vfhMf/ehHo7LV6i4Wtg4sXbrULliwwLquawFYE+4QaYwpCplMxh5//PHW87woFAoF6/u+JlkWnudFZV5++WV7zjnnWGOMdRzHOo4TW3c6nbaXXHJJVK/v+zafz1vf96NzxhUKBV1lLN588017wQUXWMdxovoRNknGURdXXXWVHRgYiOq31kZ1Sp5kPKH14/u+Xb16tT3++OOj+mSQ+gdgFy5caAcGBobRkvXoOkvB8zy7efNmu9tuu0UyS93z2HVd29XVZR9//HGbzWZL0vZ93w4ODtrnnnuuSF/8b2SIo51KpeyZZ56p2YrFwoUL7bx586wxxiaTyWG0eDxz5kx77bXXFvlVnN4ZpP3pG8xH3HPPPfbwww8vqkvqH6HP7bbbbvauu+6yvrXW833rFTxrPWutZ61fsNb61nq+tb5v7XnnnGfHto2xSePahHGtY1xrjGtdJ2FdONaFYw2MPeigg+3TzzwT0PN9W/DCUPBsWJG1nm97N222Hz3kUJtOJGzCMTZhHOtGbXC4PeKClCmZTNq9997bbtq0qUgfsg0xfvny5Xb69OlFbTAu7L777vaaa64Zpn/5Xy1Y/rHHHrMHHnjgsLpkKNU36XylAstqGscff7y9//77NWuxiJNZ6pTHX/va1+yYMWMiW7DeRCIR8WyMsQcccIB96KGHiujWoj+NOoavbdCjuxxB5SyDtycctVq+WuiRm6O1nE1qOOJZBI/5aiTjOVshnUpBz3RIl3R4LPm14vmQEbMvxsVByztSaPqar2qCLEsZpIyUT88uiTh6jtgBgtB621lA35Bv6fE/jmfqIw5aN3Hly4HlaQfq3bc22G3bmOBbSeFzHAMEOxyY4BXqPHzk4cMzgHUcwAluxXlm2y4IAGDhw/O3PT8xBoCxcB0HsOFe6Mag4PuwTnBLz3ecaCsf65jgCZPQkQ6EUc8nKBf9hX4l+w2rXujQtHWQdEmLPGibNAKsi/WxLqZVCswH9TwGQv5qoPPKNsq6qGMbXimxPzTiOb5R+i/Xd9WChgxCBBmi4mW8J1a8IxTIK7FgLA5akYQfs90OFWnCdUDyVog2piwjjVIuEDzm69Ou60YL0KT8PJaX1DQojY0YGePkbQQkXfKhZYwLvOUJ5chSb8xDnTCPlJvlEb5yHnfPe2cCeXLU68SaZ+n3vIVCebXeIHRfrcysD4KO7EiD/2K/BgBYi4Lnw0cwAFk3WM3q+x48Lw/fL0SfALfGD1/zBuBbJFwXjkMZ/OAjhHyFIbRptGuBAXzPg8835KwN8qoJiAwE2zHC5xi5XC56XqZ1RF2b8Ha7jC8VfLH+RvsZdSj5aQTIO6F5qhSgXtqRfNMXK8GqibovHl3IflPX6YeTQ7ZN0vHEgnnZd9WD+imEoACE7GCNehDPvHHvy5cCGyCdlUqBULSkLRXJzp/l6MBUvKRFPiuBdKxwCCkj6bti0SqdQdYvZZCOJRtlvZD8sE7qifXXCvJJvqVj64FVDzKyPAcr6pL5djbQ3uSTdpXykG9H7MxBG9I3OAmDagvVtAPyIM91RwL5QTm2SdfASQQfnAtyBFcxTrim1AVgwq+sum6woBXWRoNTcEfNh+MYOI6B5xcAg+DZUfgs2C94cI0LxzgwcKNtf0yVV0Lkn/6USqUiealv6UuyvfFFmXKBNuFxsc4aDy2fEVdiMr1ckD7BNsY0+mItMOIuEN+Kle1UtlsONhC+y35Lr1VkGCkqe34FWNGRUnEmbFwcCCAaXz6fj5QoG1Al6NFXCs765LnmxQlXY7O8L24L0tF5Xg0kfSkLBzzyQvnJhxFXC4xjOlRHw4e09YJ2kLSl81VrA8pInclBXcrphVe9tAOdnXVJWfWgJXW3s4ByEE44Qyz1IgflQuj3hUL4sD6ElJ+0RyIzadIesPy4djDQwAkGD9jwVWwE/zY6F+t/fAsULDzRsfFKxPO88ApIdqbbeKA8hULwKQhjLfywDG1eKpAG6Ug/YadHuGp/Ryfcyod+WCkwL8uasO3Kq/ZGgfyzXbB+iA2XKwU/7KP4QkBB7O4i9V4O0tcg+iNOkkiTepftmGVZn6Qj+3aZPhKMvGQINiD+6yDzQXSs0vGqQVxeKsqGHSPBevkvy9nwvr6Mk52g5LkUaCzKLQ0tz6F41NCNTELGezFb9JSqR54TlM9R+9ExXykeNJhPyg41cNLucoLBf9pQluWATMTZuZGolTY7EpbTOo6TkTog2JnrsvpcHpOGjpdgHeTPmG1f8DHGAI4TBOPCgYMkXKSQQAIujA2ubzwAng3uxsECxgavs/kWsHBgjAPHScAYN9rQNKjWCQe64C04OOEr3CED2569FyPiU7Ub+g/PoXxDyip9TftzOZiY3TxYPq5+/c9jzbtEMEBvGyTi8nEwqgTyKwfJOP+qBbps3OARx7MccCDkpD7qwXAORoi4Dk5CMmyV01UD3TmRDtPi6FHhuhwHQqs6S6ZXAjt0Xd4Ra0gYEHM1xE6DNCRMTOMkLcZLxyEdrQOW1fR3NCif9gXqhDZrBKQe+V8rbepXx0F0SDJe+4L2r3LQHSxi/KFqhIMBgwkbu4vwAgnFr2GVxjb5ttW/zX6Uk5AyxPmstL30BenDpMtj6dekKUNc24+DrAuCV07SpH/Iuk14Ncj2pP2J9HjFomlpWU0VfQxC+ryKZt+q664E6ob1U89W3dqUvJJHWSfLETJ/vWjYIESDasUTUgCmV6tIDas62FoUQQWzrOarGsi6NSRvdF4aW8or/+NosbyUzcY8X2FgY+GlPhtrKfo7EpQNMVeDo8mr1KfWbSlQh7qjjdMvj0mbDbeaehDDH+lWy2slhONRgCrVLHnguZbbE7dUySvTpW6kTDKN8aUGE9JlHyN1wUlfNWA+TgzIqw4Q8vJfygbBP2WQdEvJAWXXasA62KdIf6qGDnmWvEpfLsUP6ywls44vJ3MlVGe9EnBdF2PGjMGkSZMwfvx4jB8/Ht3d3VFg3Pjx49HS0oINGzZgw4YN2LJlCzZu3Bidb9y4sarAvJs2bUJvb2/Rg7NqHRFiEKExTOjgg4OD2Lx587B6dejr60MikUB3dzcmTpyIrq4uTJw4MVqASNnHjRsH3/exadMmrF+/PuKdYf369diyZQs2bdqEnp6eotd/dSPgfzL88iLr0XoeM2YMuru7MWbMGLS0tETldjbQBplMBl1dXZgwYQImTJhQJEu9oaurKzqWuuru7kZXVxeSyeQw2+qwYcMG9PX1RY1MN2hpK9/3MTAwgE2bNkW2rtW/t2zZErUL+gU/aVDuTkOj4YTPW3p6eiLe+L927Vps3rwZ69evx9q1a7F169borVCozlH6n7UWbW1tmDBhAiZOnFhkE2mrCRMmoKOjI2obmzdvLtIj9bN+/Xr09/dj3Lhxw2yvw7hx46KXHWRfoTtYP/ys+ObNm6N6daBtt27dGpWlX0ha0k/S6TS6urqK+odyobu7GxMmTIh8lXFjx45FV1cXxowZM2xheykMDAxg48aNWLduHTZu3Bj52JYtW7B+/fqoP3IcJ2qLrFOeT5gwAZMnT8aYMWOiW8xEPf1MXTsm9PX14cEHH8SaNWsiQ2jQEIODg1i+fHk0a+IMZiTVG2OwceNGvPzyy1i8eHFER47GdIp0Oo0vf/nLuOGGGwA18+bgxbxPPPEE7rvvvsi5SiGdTmP8+PEYO3YsHLX62IjLW8/zsG7dOmzatAlWPNCPq//AAw/E0UcfjRkzZhQ1Eqkfay2y2SweeOABrFy5sqhO5iN93/dx6qmnYurUqdH923pgrUVvby/22WefaHsfWS9hjMHYsWNx991346Mf/WjU8HX91M/mzZtx5513Rg9KNb3Rgg1X+i9fvlwnFcEYg2XLluHpp5/Gxo0bh8lMuay16OzsxAEHHID999+/yN7VwoQDGgdox3GwbNkyvPTSS5HO43wcACZOnIj/83/+D04//fQovRS++MUv4pe//CX6+/t1UkTTGIPu7m4cffTRw1b6M53/vu/j7rvvxvr162HVTFmeJxIJXHDBBZgzZw7S6XSsrf3w9lg2m8WyZcuiiabWuQ3beyaTwfTp02O395Foa2vDAQccgAMOOCBqd4jxYc/zsHjxYtx0002w6gpYymyMwXvvvYc//vGPWL9+PRAzGBFjx47FggUL8IlPfKKmyXIcLepz+vTpOPDAAytu25PNZnHvvffiscceK5Jbwoa+NmnSJIwbNy6yKfsx2h0hT11dXfjYxz6GqVOnFqXLfDVBr16tFb7YgaBUGBgYsA888IBNJBIWYvWv67rDVgJXCswry7muG6Ux8DydTtvLLrss4rfcDgU33HCDHT9+/LA6dZg1a5b98Y9/XLT6uFAoDFt57XmeveKKK2wmk4l2EyC/5JMrqs866yz77LPPRivCNW++Wtkt9SvPyUccjXrg+77dsmWLnTx58jAda72PGzfOPvbYYzabzUZl40B5tP80CqX05/u+zWaz9mc/+9kw2+pA+0hZmaZl12lytbxMKxWkT/Nf1q/rlOcTJ060P//5z4vkL4Vzzz3XtrW1DeNd0pRtVPLjum7Ubnmu27DkXZ6n02n7m9/8xvb29kY7pmi75PN5m8/n7dtvv227uroi2mw/UhfGGDtt2jS7dOnSop0USoH+IOvWfpfNZu3vf//7YTKVCnE21sdTp061//Iv/zKsrmqh+fTUzhHl0N/fby+//PIifvlP+1K3119/ve3p6bGFQiHaPYZ1+aqPIx8jlUli+LBYI2z4RUCCs4S4IGcVUA/NOMKXCgjr4r+cmckrkUqQswv5z2N5XiqQjpwBuGLvOgbOOnjOGa5VswemSZpx4FWTBnnicVyenQ1Sj/qh62hA20vbo1RgHlmW5bWe9TnUlYumrYP0acmjpLG9IW9ZkzfOko1q0zamDclyXsyOKVB6kz4u5aYdoHzH1vCGnMxP/hgv9Q4li4yT8YS2Txw9adtqEScvhI7ieNHQvJNX8kP7ST+X9KUscXarVaY41D0ISSGlo0gwTRqE8SjReDXiHEbWFVdvKVDB2pi1KlUaFTGvejPI/LqOOB3IOE1D5iE9woS3PHSj2Jkg5aGuKEMtuh8JtK6qqY/5dIdoYtZqSLlkg62mHiJOFzyO84PRgpSXOqC/W/GJcepG8i35Jc/0Vd32CeZ1xK1sxAxErIv6l3mrgeQpjk8Zzzq0LgjqQ6ZpPdAPdLlqIfWneZQ0S0HLamIGL8qg9avro+11edQok0bdgxDhiBW9cYqzMR0whODVQishTnhZR5wyoWZW5KtaXliOMkueaETteBB1e+LtnDj+NaTeWFdcOcaTblyeRoJ1ETzWcktIOaRTx9mlXsi6+D8Svcj8tIVVL7ZouUy4ZRTTa4HmUZ83AtXwJGWN0wHUW2PMyzxyQmbEcwZJg4F02P5K1SnzFdS3m6qF5I3+Rt5ZJ+N4l0XHa7mNejPVhn7gidf0Gcd00isF1su6WL6WNhLHaymQR9LWZXgs+Wc89TkSjLxkCCqHoJEIKs2oj0nJPI6YLXAgk3H8Z146naQjFwTGLQ6UPPrq0w+StixTDSQNOoxMk9C8WWuRSqWiY5aRPGl+WJZXXdphZCMeiTzlwDo0XWkniAWAhXCXCx1Ii5CzXtKi30B07LI847WOZVxcXZShWpAGZaIvy3TNh415pbtaSN7jdK31LPNoPkpByy91YpXfsC6tS/LnlXnBSJYhXdpR2jOurAZ5jMtfi35l+zSiHfHciCs7yso8Ml0OLAxy30jNI/VFXbE8ryhL6YI8kCc96a0E1oVQdvIB4T/MJ20tyxFSd9ov60HdgxCEoFJACCMbsZUL88s87HCsmin44TcsSFfmZadAWqxffr9G1wU1AEmlsj6dPw66YfJ4aGhI5IqH53lFbwZx9wbKBuEs1TQuliU9NhjKQidvBNgQqEPZ+dDGVi2C42yVPDK/tDNvIdKmstF44fY4JryykLJKejyXNjQxjSQurhK0Lci7fk21EbAxW1O5arsa2oELGZmPeq4E6sgRCxelHpnOzUFpS6lfaQfZITOvtDfjfd9HOp2O2onmlfzn83m0tLQA4eRSdrysU5aV7bgR0LovFApF+6VVAuUlj064bx3L5vN5JBIJZLPZooGAZWzYhmhnhO2aumeZaniRNiY9KRd9WL5dSPtVK2+9aIj1jNqczxeDCoWg0I64kuH+YrKRkQYNqTseprOjcl0XqVQKvu8jmUwin88X1e+LjUDZOZImQdqSt3KQhqGxjDFRw8nn89GtGNZPXvxwHUIymYwGWC98bT2hNnSttnH5vh+tMaLz0h6aZr3www6Y9UAMBkY0onw+HzU+lpPpjti/T/4zjTyTBmWBmLhQ99ShFQ2UvJE/lqsVkmeeQ3TUjQT9iP7KDoK+RH9hXogBgHauhifKQLsxTvuJbEu0EQNtQX3TRn7MBsNS7xxg3HDHebZJ2hChbLSvF97Oom7iQP3UC/IsfZZ153I5oIY2qXVrxSCQSCRQKBSK1vlIv/JF+/XERqIciOjbpfQhwfzkhWVtOMlgn8yrOEfs9FGtrPWirlroOKWUQcXTcaUiqGDp5HRiHtOQpGHE7MKIRiNnyizrhAOK7OzJA2nQ6eV5NQ5Nvig/40hHzt44SLrikt6KzRSls9FZNO1KMDH3o6lL6rxRkPQpE+sg3yYckAvh7TjdaGhH2k/SpI4KhcKwyYI8ZlnWLXUr4/VxraBMUPxD+E+joO3lhZ25IzaMRVg/j+nnLCt1VArSbnG6kXcsJD2Zn7ZhvYXwK8X0Z6kzrUNHbW/FemhD13WRy+Ui2aWtdRkjBo16QXk4gWKgDtg3VQLLMNB2tCNtJnUj7Ut5qRM3nMBTftKtBizjqkm+L56pSf2jhsl4o1DXIETGZWcjnYPpTOPMTioeqiNh5804bSwZpFMwjkqWeWTj1crWPGsHKhc0WI90VE89mIRomEyX9TKdQcscF+LK8MqokaDerLp/LNMpA/NI2WReTw345J2DsW44Vtyi0GUIK2ab5K8UJF/lgiM6IvLB8qPRUHmFQb+UNoboRKTc9HHm0/6hA32Dg5Ej2ihC21A2WTeU70I8K2NbIt9EqfK0L+WQbRnCl0hP60P2E6xDno80SDvzX/Yx1SCOpvRl9hNQbYb/uh5ZPk6/5YLUL8vpdmrF5FXTl342Wqhrx4TNmzfj9ttvx4oVK2DDToKNn4ajI61evRq/+MUvhgnN/GRjr732wnnnnYdMJlPk8PynUlatWoXHH38czz//fJFCpRMhbCTz5s3DMcccE9XFe7KcvZFuW1sb2tvbo46gFPL5PPr6+jAwMACIhsUZi+ThiSeewJ///OeigZA64DkAzJkzBx/5yEcwadIkUVNlSDnZAfthJ37eeedh9uzZFVeTV4t8Po9bbrkFW7duhS+erdHW0pF7enrQ398PJ5z1ykZkxCAxZcoUfPWrX434hvAZ5rPWYvXq1fiv//ovbNq0KaqLeZjfWouJEyfiqKOOwmGHHRbplvqQKBQKeOutt/C73/2uKD4Ob7zxBu677z689957kc+SprRlvZCy8Hy//fbDEUccgT322CPqPJzw9gk7cQ5AW7ZsweDgoCY7DOPGjUM6nYYjrjKkjuijy5cvx9133120qwR1Srkdx0Emk8EFF1wQ7fZBWzAv5bLWYuPGjRgaGhpmO2lvJxyguru7Ix3LAZ8ym/CKeePGjdFEuB6Q7ooVK/CrX/0q4k3KXA2Yn2hvb8f8+fNx2GGHwQ/bjR9OyuWAZK3FggULcMoppxQ9N5a2od7ffvttPP7441i8eHFUPg6O46CjowPt7e2A6CM9zyvq54wxOOKIIzBv3rzY/kLL1EjUNQgtX74cF154IZ5//vmoIZBZ6WQ87+vrA5RTSgMbY3D44YfjjjvuQGdnZ1FDZzli0aJFuOGGG3DXXXcVOb7MR5qpVArpdDqql4MPO0fmP+ecc/C1r30NXV1dUT1xWLZsGf7zP/8Td911V2RQG96XL4hvx1hrkcvlkM1mh8kK0WkjvAWSSqWiskQl40tassE4joP/+3//L4466ihkMhlVamTwfR/9/f2w4hYp4yF47enpwYUXXogXXngBBfG9EgibsKOZN28eHnvssaI0SZM6eu2113DmmWdi5cqVUT4rrrbYKc2aNQuXXnopzjvvvIgnSZfwfT+yTSX8/ve/x3e/+1385S9/AWJo0gaNhuM4+OQnP4lLL70UH/3oR6P6KC99t1AoYMuWLfjWt76Fe++9d5isGtdccw1OPPFEtLW1RfqlLiHs8OKLL+Ib3/gGXnzxxaJ4gnro6OjAL3/5S3z4wx+OHsBL+zGf53m48MIL8cc//hEDAwMl9ZdMJjF16lT89re/jfoBOQhB3HVYs2YNPve5z2HVqlXD6IwENry1yMFc+lq10Po3og+injkIyEEIAC644AJ873vfiybhCCdMUn5rLR5//HHccMMNePjhh0Xp4WhpacE3v/lNfOlLXyqytQ37KylXOp2OBiDyyXQtU0Oht1CoBcuWLbMf//jHi7aB4PYQPC4VL7e3YJzruvbYY4+1PT090TYRtsS2Ly+99JI9++yzLcQWI6TDONajg97mh1tvXHbZZXbDhg26qmF466237AUXXBBtdyHrlXzoIHVRisdS+ikVdF5Zz8KFC+3AwIBmf8TgNh2ltg9i2LRpkz3iiCNsOp0uK2cqlbIHH3xwRLscFi1aZKdOnTqMDmlT9n322cf+8Ic/tFb4jfQfucVInF9p+L5v77nnHjtv3rxhNtHn9QbdVowx9rTTTrNPPPFEEc9yKxWer1271p5xxhlV8XTLLbfYvr6+WF2QnrXWPvfcc/aggw4qoql17ziObW9vt88884zNZrPD+JJ6zOVy9tRTT7Xt7e0RDUmX/4lEwu611152y5YtJWVm3IoVK+yMGTOK+oB6gtaf5EunlQsyf9yxDix36aWX2v7+/sgeVrQNqYMHH3zQnnjiicPq1aG1tdVed911w2zCYx0n0wjJy2ig/I3zKiBHUjnScuSUs3NCj65GXTHxmLd7ZHlJv9RMSuaX+RDmNWIma8TtgGrB/H54L550tJyIqV8eU1Y5O6kVceW0vBL6vBZQFtKQdWvZS82i9LHMp1GKvswv81gxAy8FaQ/NcxyYn7wyDiV0qeWrFpIvCSm/FT4r85bSdSnIPNRfKXpEnFx+ePeBVzvkkfTklZWmQWhZNC/yXMpO+qxzpJD1y3rj4uLOK0HTjLMnz3WdWm8yD/VcDSQPVjw3lW1F612XGU3UNQhJh6Ngkvk46DxSqXIAouBaAayT+SXiFKeNJXllI5GoxD+h5fBjnjtA1a/50MeaZq0woWNLvcTppNR5NbDq/jzr07TkOdMlb+SLHZ62O6HtF5dX60zT1On1QtqJxzowrVwenV/7vzyWOjAlbjESmrYOUm/yXNpH55HHuvOS+mZeSRNCNn3bXsoFJZvMxwGnHmhZ5D9i/EnzQJBOqUBaWic6DaJtEDJNn0s+JN1qYcSkz4rbr3KyIOvTvIwWhveaIwCVQQeDEkAKVypIOvI5jVa0E7OTghUNoVIgPSe8L8s46SCVoBsEy0haut644Ih9qeJk0rLXi1rl1CBfEq54LbcafpmPtPg6rNSLX2YthDznMfWGUC7ph40AafL+PTsOyTd5YZrmW8pXLlAWo15QkDQkKKt88UPT1IGQ9SGsh3Qom6QpZZJ05MAl7QBlbwh/0bwwr+TFxPhbPZD0pL0kf1pXIwlST/RlW2KSDWVfqS8jXjLS6bKNlAPrkbJT/zqdfMTxM5qoexDSSofqpHVaKVBYaUzGQzmHPGd6NQaBcgoIx9cDSzXQBpJOVg1kJ2NF56sdpBGQuuN5rbqDmDUZ0RDIezX8attSdj0I6zIQExAbdrp85V82SCdc3Eo+GwEnfIlC8yYnSxpazmpBPUqZWT6uXZXjoRRkfulr8pj/tJHMI+OoZ+27kg7z+SVeNCJdG7M2TtOsB+RdH5sSd0XqBe0l6UvdEVKvPGY8/VrrII5OOfjhowOpT7YnKPtr1FLPSFC35uk4VLQRr8syvRrQiY1400k6JJUuFULD1aok8kjQOIVCoar1NbLB6bprbTS6PEF9NgrayaTzVQt9hVGqg6wE2bjccOW8jte0mZf1F8LFrJIf5meDawSoN9KW9XGVuVGdB8E06WvlIK9mKD91ImnzmO3F1rg9U1ybiYvjK7ysT8oo+aN9GKc7N4J80o+oH4a4jnokg2wpsD55bEL7NMpfUKJNSZtZceWqdSbbvBG7hNC21JvOWwqcPDlqIlWqLG1K36rWp+pBPCdVwojVynGKrxZ0BtLgDJcOQ8PF1VGr89AYPJZIpVLR1jvlIHdhkDyRnqZbCZSPDgLVITcCkq5GtfzK2yg2ZgCuBtKu5EWueNc6kPzmcrkinTAfG5nkjTauF6yDExXGkb6Ux1V7vDG9WsTZhnVSPnYObC827NiTyeSwukuBsmg78NioHTwoLzsxK2552vCWqkwjn6TLgYcDihNu+yPzM56B2+RIOvXAiM2DNRpBX4J1yH5L189+S/LDvNSvTpegTishm81Gkzxtb9qYdLQtEMpQqGIXmXpQXe9TAlSW7sQoBIU2YrYTF5iHTpoP96wiJH3p4LI866sUpNKlg5uw4VVzJZTL5VAQW8qQPxPKLXkrF1iOvPsxtzQaAdKTNoGaSVcTstlsxLsG81SCzEdd5cM9xCSkjb1wdwUnZpW9rJMdmBU+Um9gQ02EWzGRvg3rtaIudsxyxkk/1raPC6TJcqTDepiPHQPPuT6N9qwUNE0eUxbqkv+mxKBEWtyDjFeGUl6I2ThpU1cQW1z54naRJ7YCk3TqgbQPhO9paF2NJEgd0Ceh7iQwnjqVfiL9mHDFXQCeVzPpaGlpidZuSb4geKCNpV+Qr2rrqQd1LVbdsGEDbrzxRrzzzjuRU1FAGqMa8lI5++yzD7761a+itbU1itOw1mLFihX43e9+hyeffBJW7MRcqT5Jz1dvtB133HH4zGc+E60uLoW1a9fiN7/5DR5//HFAdBqyoVUDJ3w5whiDd999F6+//jo2btxYlKeSPBLUOf8XLlyI4447Lrq6k7JLna9fvx5/+tOfosXEpeC6Lj71qU8hk8kUldfHW7Zswac//Wk89dRTyOVyRXkIE3aeU6dOxXe+8x1YcVsHwjbsoFauXIl/+7d/w5YtW4pkhOgwfd/H5MmTcdJJJ+GYY46J9Z1aYa3FSy+9hF/84hdYuXJlFAfRYZCf1tZWzJo1C3Pnzo3SKZeWPw6k44YLqY0xmDBhAqZPn46xY8dG9UFMIPify+Xw+uuvY926dZrsMOy3336YOHFi7NUIr6p838eSJUtw2223YenSpVG61Dvj0uk0vvnNb2KPPfaIOjyoPoD2XLRoUbTDAfOQFsSg19XVhX/9138tWrQZh1WrVmHBggVYuXJl2bbX2tqKefPmYc6cObBhfyH7rGrsUyvI98DAAN5++20sWrQo6iOoO/4j1MFxxx2Hs846q2gCQr1zIuY4Dl555RXcd999eOWVV4rq1GhpacHnP/95HH/88VEc+x3pSwBw8MEHY6+99iq6M6F5HA3UNQjlcjmsWLECW7duBWIcqloBJAttbW2YOXNmNPrq8sybzWaxfv36om1cahWFDYPluru7MXny5Iojfy6Xw9q1a6O6CSM6hUoyE6z7/vvvx2233Ratypdp1ULq24pBiDsm0NmYzgbx6quv4lvf+haWLVumKG6DCbc1+vWvf43x48dHcRq2ikGI+rHWoqWlBXPmzCma/cbly2azeOedd4puDUg/43k6nUZ3d3fEY70wxqC3txerV6+OPkFAvUl+jTGYMmUKzjzzTJx99tmCQryeNKQsRvjRiy++iPvuuw+LFy+O6Gh9WmvR2dmJc845B4ceemjF+n74wx/ihRdeGLZbhAmveNgmhoaGsHLlSgwNDcGKgQqCX87Q9957b7S0tBSlSz5t2PF/7Wtfwz777BPtIC1pEia8zb/33ntHV1ylUO0gNGnSJHzxi1/EWWedBYR8axs2ErIfWLt2LX71q1/h5ptvHmY3KD8fP348pkyZIigFMMInHMdBX18f1q5dG/W9peA4DiZPnowJEyYAQt9Sdvrz3//93+NTn/oU2trahtlvtPQEBEzVDb2ilqtzGwFNZzRp1wLJh6RTDc04Ge6880570EEHWcSseq42yFXZiNkxgSuhNe9PPfWUnT179jB6mvaYMWPs6tWrh/Eu4Yc7Jhx55JE2lUoV8SNpybhK6TJPqbS4+NEKpVbnz5w501577bVaJWX1RcT5hLXW/vrXv7aHH354Sfm4+8fEiRPtz3/+c108Fueee260awHpGLHThozjvz7mud7lQfOneb333nvt1q1bI160zHFtqZRufN+3K1assNOnTx/Guw677767vfbaa0vS0mC+keSX59Zau2LFCnvllVcW8aj1JfWq03SolB4XypVh2nXXXRftVqNDOVSTpxzqeiZE6FGSI3sjoOmMJu1aIPmQdOqhScTRbQSMuJ1TTx0sI2d1tSJuRijP4+JKpRGl4hsNPeOuxifJdzn+S0HrW9Y1Upq6jI15ESZO57oeltH0SkHT1HobSVuqNh9qzBuHOH3HyS19QtqvVP2SrqavUSk9DuXKlIqnDJL/cnRGioYMQk3Uhmo6rfcztBNLZ4/TS7k0VGhg2xuSF/7H8a15jpNPp8tjfT9fly2HenWlea8Vsmw1dKgbXS9lrkX2aiF9TtMvxXNc3lLQsuwMkPrUcmhetR10/lpQ9yBEZTZDcaiEavJ8UFBOd9q5S3UMcdAdiQ6Ngua/lBxxQecjNC2ZTx5TDid8oK/LxIU4HWi+yumnXFolGPHmLOlo/nSohGryVAtdN2lL3ZF/opK+Ropq7dEI2DJvlCKGl0byU/cgpBlrhiCUakDauB9USF3FxRO6QZTTXykblAqjAc1DOZCHOF7KlbfqlV7KquNKhTgdaN2Uql/SqQaV8mre4kIlxOlvpNB18ljH1ws9EGuYGP8dLZiYgZXxOk6iUXzVPQg1UT1oMBq9iQCyw5FhpNAdabnQaEi6tDdnmKWg88t4o57jSZ5luXIDh0ac/DzW8Ro2ZgeHWsGytXRicfnIi36W1QhoPUi540KtkDrU+pb20TaKi28UtA9pGaWcpXgfCZo94ShBO0qcIT/I0M7NwE6llJ60XiW0fjXtuNBoSLrkM26WyXTZ4DWoDy5ylLTjngnF1REHTYtxMi3OBrXUQWhdSBq10GNemd+EA3SlJRXVgnKXk71UkOVrgaYBQSfORnG2qxdxfGu+NI/6vB5+moPQKEE7ijRanJN/kCCdF2GHKhfnSejGEKdXjUZ2TNUgrkHqhs1zufOAhpbRimcQciBjnDw2YleDSojTmaQNMchpaFmrBct44e4XpXQQh7h8Uj/cX61eSF3SXrpuOUjptFp0I22oacXRYZyObxRIV/qeBPVRzn9HinhPqxJ++MnuZhgetm7disHBwaItiOh4pRr4Bw3sQNLpNFpbW9HW1ob29vYodHR0oK2tDa2trdH2I+V0Z8LdBpLJJFpaWopoxYVGffac8H0f2WwWg4OD6O/vR39/P7Zu3YqtW7dG5wMDAxgaGhq2NZXuXOgriUQCLS0taG1tLeK9tbUVmUwm0llLSwt838fAwMAwX9QhmUyio6OjSM/yv729HW1tbchkMkgmk0Ud1Eg6IHbqCBd6Dw4OYmBgINKP5k+HUnn6+/sxODgYLSJuBGy4F182m0V/f38RDwMDA1EYHByMtu/SA0i1kAMawoE/lUohk8mgo6OjyB4ySNs0ArRPPp+PfJd+K31X6rseuTXq2jFhzZo1+O53v4u//OUvNTvmBwHGGFxyySX41Kc+Fa0QL4Wf/exnuPrqq/H8889HTiEbbzXQ5fSOCQTzEE8//TS++MUv4s033yzKJ2GMQWdnJ9544w1MnjwZiKHDuEo7JhBcFX/jjTcOoyNhrcXSpUvxD//wD1i7di2gOjaJ3XffHWeccQZOOeWUos5T0y8UCnj88cfx3e9+tyi+HqTTaUydOhW77767TiqCMQZnn302zjnnnIp+sXHjRqxcuRI9PT06qUiu/v5+/Pa3v624jQsAfO5zn8O+++5bsRNbvHgxrrnmGixevDhW17XCGIN58+ZhzJgxDbtSzeVyePHFF6MNT0th9913xxVXXIGvfvWrsf5AFAoFvPjii/j7v/97oMRVGMsecMABuOyyyzB79mydpQirVq3Cj370o8jX4mgipHvaaafhkksuKbuRsrUWL7zwAn72s5/hueee08kjxl577VVyxxjZt+y555647LLL8JGPfERnGxn06tVasHTpUrtgwYJhK3953AywV111lR0cHNSqGwa5YwJ1WKsudTm9YwKhVzfXumNCKTqMq7RjAkMqlbIHH3ywJhGLV155xU6bNq2IH00PgJ01a5a96aabisrKVexENpu1d95557Dy2yMYY+w///M/x9qmFkh51q5da08//fRhdcWF2267rWjXglJ4/vnn7UEHHVRS17tS4I4JtoTfErlczt5///0WZXyMaUceeaR98cUXNYlhWLlypb3yyiujnQk0LVnXJZdcYvv7+zWJCOT9gQcesCeccMIwOqMVZN+y//7724ceekizNmKUvrfRRBNNNNFEE6OM5iDURBNNNNHEDkNzEGqiiSaaaGKHoTkINdFEE000scPQHISaaKKJJprYYWgOQk000UQTTewwNAehJppooiRKradpoolGYbsMQkZsUSF3DDCjuA3F9oKUYTRkIU25U0AikRi2c4CsW+qai8yA4u1BuFrf87yoXCU44WeACRN+Jph7m1UD13WjxXDkmTzYEptEsk4//Bw780q908f4uWkJbR/WoXVYCqRPvvViPql78kHILWXkp6ppG6nPWiHrlbtyVILUs1y1TxpSf6V0VE091YL6hdAL47cn6G+0tfQxCcmfTK+lLTWxDXXtmLBs2TJ84QtfwFNPPRU5rolZyZ5KpTBnzhx8/etfL3Iy3/ejhqnL7MxYvXo1fvvb3+IPf/hD5KhswLKTtNbiqquuwmWXXVZ2BTQq7Jgg9TN+/HhccMEFmDdvXtEg43keXNeF53lwHAee5+H4448vWgFNnhzHKWpga9aswTPPPIO+vr4injSM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"""
        
        self.init_sidebar()
        self.init_frames()
        self.show_frame("resizer") 
        self.bind_all("<MouseWheel>", self.on_global_scroll)

    def on_global_scroll(self, event):
        # === 防御性编程：捕获所有可能的异常以防止闪退 ===
        try:
            # 1. 获取事件源组件，如果无法获取则忽略
            try:
                widget = event.widget
                if not widget: return
            except: return

            # 2. 智能过滤：如果是下拉框、文本框或输入框，交给它们自己处理
            try:
                widget_str = str(widget).lower()
                if isinstance(widget, ctk.CTkComboBox) or "combobox" in widget_str: return
                if isinstance(widget, ctk.CTkTextbox) or "textbox" in widget_str: return
                if isinstance(widget, ctk.CTkEntry) or "entry" in widget_str: return
            except: pass
            
            # 3. 计算滚动单位
            scroll_units = 0
            # 增加滚动速度倍率
            speed_factor = 4 
            
            try:
                if platform.system() == "Windows":
                    if event.delta: scroll_units = int(-1 * (event.delta / 120) * speed_factor) 
                elif platform.system() == "Darwin":
                    if event.delta: scroll_units = int(-1 * event.delta * speed_factor)
                else:
                    if getattr(event, 'num', 0) == 4: scroll_units = -1 * speed_factor
                    elif getattr(event, 'num', 0) == 5: scroll_units = 1 * speed_factor
                    elif event.delta: scroll_units = int(-1 * (event.delta / 120) * speed_factor)
            except: return # 计算出错则退出
            
            if scroll_units == 0: return

            # 4. 调度滚动事件到当前可见的页面
            for name, frame in self.frames.items():
                # 必须确保 frame 存在且可见，防止访问已销毁对象的属性
                if frame and frame.winfo_exists() and frame.winfo_viewable():
                    
                    # 特殊处理：改名界面和转换界面有左右两栏
                    if name in ["renamer", "converter", "compressor"]:
                        try:
                            # 坐标计算可能失败，加 try
                            pointer_x = self.winfo_pointerx() - self.winfo_rootx()
                            left_boundary = 200 # 侧边栏估计宽度
                            
                            if hasattr(frame, 'left_panel') and frame.left_panel.winfo_exists():
                                left_boundary += frame.left_panel.winfo_width()
                            
                            # 根据鼠标位置决定滚动左边还是右边
                            if pointer_x < left_boundary:
                                if hasattr(frame, 'scroll_list') and frame.scroll_list.winfo_exists():
                                    frame.scroll_list._parent_canvas.yview_scroll(scroll_units, "units")
                            else:
                                if hasattr(frame, 'ctrl_panel') and frame.ctrl_panel.winfo_exists():
                                    frame.ctrl_panel._parent_canvas.yview_scroll(scroll_units, "units")
                        except: pass
                    
                    # 其他界面 (Resizer, Watermark) 通常滚右侧控制面板
                    else:
                        target = None
                        if hasattr(frame, 'ctrl_scroll'): target = frame.ctrl_scroll
                        elif hasattr(frame, 'ctrl_panel'): target = frame.ctrl_panel
                        
                        if target and target.winfo_exists():
                            try: target._parent_canvas.yview_scroll(scroll_units, "units")
                            except: pass
                    return # 找到可见页面后即可退出循环

        except Exception as e:
            # 最后的防线：打印错误但不让程序崩溃
            print(f"Scroll Error (Ignored): {e}")
            pass

    def init_sidebar(self):
        self.sidebar = ctk.CTkFrame(self, width=200, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_rowconfigure(6, weight=1) 
        
        # === 修改开始：使用 Frame 包裹标题和小字 ===
        # 创建一个透明的容器放在 row=0
        title_frame = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        title_frame.grid(row=0, column=0, padx=20, pady=(30, 30), sticky="ew")
        
        # 主标题
        ctk.CTkLabel(title_frame, text="妲己图片工具箱", font=("Microsoft YaHei UI", 22, "bold")).pack(anchor="w")
        # 副标题（你要加的小字）
        ctk.CTkLabel(title_frame, text="妲己永远听从主人，因为被设置成这样", font=("Microsoft YaHei UI", 11), text_color=("gray60", "gray40")).pack(anchor="w", pady=(2, 0))
        # === 修改结束 ===

        self.btn_resizer = self.create_nav_btn("📏  批量改尺寸", "resizer", 1)

        self.btn_resizer = self.create_nav_btn("📏  批量改尺寸", "resizer", 1)
        self.btn_resizer = self.create_nav_btn("📏  批量改尺寸", "resizer", 1)
        self.btn_watermark = self.create_nav_btn("💧  自动水印", "watermark", 2)
        self.btn_renamer = self.create_nav_btn("📝  批量改名", "renamer", 3)
        self.btn_converter = self.create_nav_btn("🏭  格式工厂", "converter", 4)
        self.btn_compressor = self.create_nav_btn("🔨  图片液压机", "compressor", 5) # 新增按钮
        
        # === 新增：捐赠作者按钮 (放在底部) ===
        self.btn_donate = ctk.CTkButton(self.sidebar, text="☕  捐赠作者", height=45, anchor="w", font=("Microsoft YaHei UI", 14, "bold"), fg_color="transparent", text_color=("gray10", "gray90"), hover_color=("gray70", "gray30"), command=self.show_donate)
        self.btn_donate.grid(row=7, column=0, padx=10, pady=(10, 0), sticky="ew")

        ctk.CTkLabel(self.sidebar, text="请尽情吩咐妲己，主人", text_color="gray").grid(row=8, column=0, pady=(10,10), padx=20, sticky="w")
        self.sw_dark = ctk.CTkSwitch(self.sidebar, text="☀️ 日间模式", command=self.toggle_theme)
        if config_mgr.data["theme"] == "Dark":
            self.sw_dark.select()
            self.sw_dark.configure(text="🌙 夜间模式")
        else:
            self.sw_dark.deselect()
            self.sw_dark.configure(text="☀️ 日间模式")
        self.sw_dark.grid(row=9, column=0, padx=20, pady=(0, 20), sticky="w")

    def show_donate(self):
        win = ctk.CTkToplevel(self)
        win.title("支持作者")
        win.geometry("350x480")
        win.resizable(False, False)
        win.transient(self) # 模态
        
        # 居中显示
        win.update_idletasks()
        try:
            x = self.winfo_rootx() + (self.winfo_width() - 350) // 2
            y = self.winfo_rooty() + (self.winfo_height() - 480) // 2
            win.geometry(f"+{x}+{y}")
        except: pass
        
        ctk.CTkLabel(win, text="感谢您的支持与鼓励！", font=("Microsoft YaHei UI", 18, "bold")).pack(pady=(30, 10))
        ctk.CTkLabel(win, text="开发不易，您的支持是我更新的动力", font=("Microsoft YaHei UI", 12), text_color="gray").pack(pady=(0, 20))
        
        # 二维码显示区域
        qr_frame = ctk.CTkFrame(win, fg_color=("gray90", "gray20"), width=260, height=260)
        qr_frame.pack()
        qr_frame.pack_propagate(False)
        
        self.lbl_qr_img = ctk.CTkLabel(qr_frame, text="正在加载二维码...", font=("Microsoft YaHei UI", 12), text_color="gray")
        self.lbl_qr_img.place(relx=0.5, rely=0.5, anchor="center")
        
        # === 彩蛋计数器 ===
        self.donate_click_count = 0

        def on_qr_click(event):
            self.donate_click_count += 1
            if self.donate_click_count >= 5:
                messagebox.showinfo("恭喜你发现了彩蛋", "设计狗都不干！")
                self.donate_click_count = 0 # 重置

        def load_embedded_qr():
            try:
                # 1. 获取字符串
                raw_b64 = self.donate_qr_code_b64
                if not raw_b64: 
                    self.lbl_qr_img.configure(text="未配置二维码数据")
                    return

                # 2. 智能清理：处理 data URI scheme 前缀 (例如 data:image/png;base64,...)
                if "," in raw_b64:
                    raw_b64 = raw_b64.split(",", 1)[1]
                
                # 去除所有非Base64字符（换行、空格等）
                b64_data = re.sub(r'[^a-zA-Z0-9+/=]', '', raw_b64)
                
                # 3. 自动补全 Padding (Base64长度必须是4的倍数)
                missing_padding = len(b64_data) % 4
                if missing_padding:
                    b64_data += '=' * (4 - missing_padding)
                
                # 4. 解码并加载
                img_data = base64.b64decode(b64_data)
                img = Image.open(io.BytesIO(img_data))
                
                # 限制尺寸，防止图片过大撑爆窗口，保持比例
                img.thumbnail((250, 250)) 
                
                tk_img = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
                self.lbl_qr_img.configure(image=tk_img, text="")
                
                # 绑定点击事件 (彩蛋)
                self.lbl_qr_img.bind("<Button-1>", on_qr_click)
            except Exception as e:
                print(f"QR Decode Error: {e}")
                # 在界面上显示简短错误，方便排查
                self.lbl_qr_img.configure(text=f"加载失败\n{str(e)[:20]}...")

        # 加载
        load_embedded_qr()

        ctk.CTkButton(win, text="关闭", command=win.destroy, fg_color="transparent", border_width=1, text_color=("gray10", "gray90")).pack(pady=(30, 20))

    def create_nav_btn(self, text, name, row):
        btn = ctk.CTkButton(self.sidebar, text=text, height=45, anchor="w", font=("Microsoft YaHei UI", 14, "bold"), fg_color="transparent", text_color=("gray10", "gray90"), hover_color=("gray70", "gray30"), command=lambda: self.show_frame(name))
        btn.grid(row=row, column=0, padx=10, pady=5, sticky="ew")
        return btn

    def init_frames(self):
        self.container = ctk.CTkFrame(self, fg_color="transparent")
        self.container.grid(row=0, column=1, sticky="nsew", padx=0, pady=0)
        self.frames = {}
        self.frames["resizer"] = ResizerFrame(self.container)
        self.frames["watermark"] = WatermarkFrame(self.container)
        self.frames["renamer"] = RenamerFrame(self.container)
        self.frames["converter"] = ConverterFrame(self.container)
        self.frames["compressor"] = CompressorFrame(self.container) # 新增注册

    def show_frame(self, name):
        for f in self.frames.values(): f.pack_forget()
        btns = {"resizer": self.btn_resizer, "watermark": self.btn_watermark, "renamer": self.btn_renamer, "converter": self.btn_converter, "compressor": self.btn_compressor}
        for k, btn in btns.items():
            if k == name: btn.configure(fg_color=["#3B8ED0", "#1F6AA5"], text_color="white")
            else: btn.configure(fg_color="transparent", text_color=("gray10", "gray90"))
        self.frames[name].pack(fill="both", expand=True)

    def toggle_theme(self):
        if ctk.get_appearance_mode() == "Light": 
            ctk.set_appearance_mode("Dark")
            self.sw_dark.configure(text="🌙 夜间模式")
            config_mgr.data["theme"] = "Dark"
        else: 
            ctk.set_appearance_mode("Light")
            self.sw_dark.configure(text="☀️ 日间模式")
            config_mgr.data["theme"] = "Light"
            
        if hasattr(self.frames["resizer"], "update_canvas_bg"): self.frames["resizer"].update_canvas_bg()
        if hasattr(self.frames["watermark"], "update_bg"): self.frames["watermark"].update_bg()

    def on_close(self):
        # 保存配置
        config_mgr.save()
        
        for f in self.frames.values():
            if hasattr(f, "observer") and f.observer: f.observer.stop()
            if hasattr(f, "watcher") and f.watcher: f.watcher.stop()
            if hasattr(f, "is_monitoring"): f.is_monitoring = False
        self.destroy()
        os._exit(0)

if __name__ == "__main__":
    try:
        app = DajiToolbox()
        app.protocol("WM_DELETE_WINDOW", app.on_close)
        app.mainloop()
    except Exception as e:
        import tkinter.messagebox
        tkinter.messagebox.showerror("Error", str(e))