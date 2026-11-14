import os
import sys  # 新增：用于检测打包环境
import time
import threading
import platform
import shutil
import re
import math
import json  # 新增：用于配置保存
from tkinter import filedialog, colorchooser, messagebox, Menu
from functools import lru_cache
from datetime import datetime

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageOps, ImageGrab, ImageTk

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
    """ 获取配置文件路径：快速启动器可指定目录，否则保存在程序同级目录下 """
    override = os.environ.get("DAJI_CONFIG_DIR")
    if override and os.path.isdir(override):
        return os.path.join(override, "daji_config.json")
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
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler
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
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler
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
        
        
        self.init_sidebar()
        self.init_frames()
        self.show_frame("resizer") 
        self.bind_all("<MouseWheel>", self.on_global_scroll)
        # 首屏先完成布局渲染，再于空闲时分批预建其余页面，避免抢占首屏绘制
        self.after(1000, self._preload_next)

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

            # 4. 调度滚动事件到当前显示的页面（页面叠放后不能再用 winfo_viewable 判断）
            for name, frame in self.frames.items():
                if name != getattr(self, "_current_name", None): continue
                if frame and frame.winfo_exists():
                    
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

        self.btn_watermark = self.create_nav_btn("💧  自动水印", "watermark", 2)
        self.btn_renamer = self.create_nav_btn("📝  批量改名", "renamer", 3)
        self.btn_converter = self.create_nav_btn("🏭  格式工厂", "converter", 4)
        self.btn_compressor = self.create_nav_btn("🔨  图片液压机", "compressor", 5) # 新增按钮
        

        ctk.CTkLabel(self.sidebar, text="请尽情吩咐妲己，主人", text_color="gray").grid(row=8, column=0, pady=(10,10), padx=20, sticky="w")
        self.sw_dark = ctk.CTkSwitch(self.sidebar, text="☀️ 日间模式", command=self.toggle_theme)
        if config_mgr.data["theme"] == "Dark":
            self.sw_dark.select()
            self.sw_dark.configure(text="🌙 夜间模式")
        else:
            self.sw_dark.deselect()
            self.sw_dark.configure(text="☀️ 日间模式")
        self.sw_dark.grid(row=9, column=0, padx=20, pady=(0, 20), sticky="w")


    def create_nav_btn(self, text, name, row):
        btn = ctk.CTkButton(self.sidebar, text=text, height=45, anchor="w", font=("Microsoft YaHei UI", 14, "bold"), fg_color="transparent", text_color=("gray10", "gray90"), hover_color=("gray70", "gray30"), command=lambda: self.show_frame(name))
        btn.grid(row=row, column=0, padx=10, pady=5, sticky="ew")
        return btn

    # 首屏先显示，其余页面在空闲时预建；切换时只做必要的布局与重绘
    FRAME_CLASSES = {
        "resizer": ResizerFrame,
        "watermark": WatermarkFrame,
        "renamer": RenamerFrame,
        "converter": ConverterFrame,
        "compressor": CompressorFrame,
    }
    # 轻页面先建，最重的水印页放最后，尽快让大多数切换变顺滑
    _PRELOAD_ORDER = ("renamer", "converter", "compressor", "watermark")

    def init_frames(self):
        self.container = ctk.CTkFrame(self, fg_color="transparent")
        self.container.grid(row=0, column=1, sticky="nsew", padx=0, pady=0)
        self.frames = {}

    def _ensure_frame(self, name):
        frames = getattr(self, "frames", None)
        if frames is None: return None
        if name not in frames and self.winfo_exists():
            try:
                frame = self.FRAME_CLASSES[name](self.container)
                # 所有页面叠放在同一区域：切换只改堆叠顺序，不再反复布局重排
                frame.place(relx=0, rely=0, relwidth=1, relheight=1)
                frames[name] = frame
                if getattr(self, "_current_name", None) is None:
                    frame.tkraise()
                else:
                    frame.lower()
            except Exception as e:
                print("Preload Frame Error:", e)
        return frames.get(name)

    def _preload_next(self):
        if not self.winfo_exists(): return
        pending = [n for n in self._PRELOAD_ORDER if n not in self.frames]
        if not pending: return
        self._ensure_frame(pending[0])
        if len(pending) > 1:
            self.after(250, self._preload_next)

    def get_frame(self, name):
        return self._ensure_frame(name)

    def show_frame(self, name):
        if getattr(self, "_current_name", None) == name:
            return  # 已在当前页面，跳过重复重绘
        target = self.get_frame(name)
        target.tkraise()  # 只切换堆叠顺序，不触发布局重算
        btns = {"resizer": self.btn_resizer, "watermark": self.btn_watermark, "renamer": self.btn_renamer, "converter": self.btn_converter, "compressor": self.btn_compressor}
        prev = getattr(self, "_current_name", None)
        if prev in btns:
            btns[prev].configure(fg_color="transparent", text_color=("gray10", "gray90"))
        btns[name].configure(fg_color=["#3B8ED0", "#1F6AA5"], text_color="white")
        self._current_name = name

    def toggle_theme(self):
        if ctk.get_appearance_mode() == "Light": 
            ctk.set_appearance_mode("Dark")
            self.sw_dark.configure(text="🌙 夜间模式")
            config_mgr.data["theme"] = "Dark"
        else: 
            ctk.set_appearance_mode("Light")
            self.sw_dark.configure(text="☀️ 日间模式")
            config_mgr.data["theme"] = "Light"
            
        resizer = self.frames.get("resizer")
        if resizer and hasattr(resizer, "update_canvas_bg"): resizer.update_canvas_bg()
        watermark = self.frames.get("watermark")
        if watermark and hasattr(watermark, "update_bg"): watermark.update_bg()

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