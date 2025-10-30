import os
import glob
import struct
import logging
import io
import json
import re
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from enum import Enum, IntEnum
from multiprocessing import Process, Queue, freeze_support
from logging.handlers import QueueHandler
import webbrowser
import math
import socket
import hashlib

from waitress import serve
from PIL import Image
from flask import Flask, request, jsonify, send_from_directory

# ==============================================================================
# PART 1: FLASK SERVER LOGIC
# ==============================================================================

app = Flask(__name__)
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

# --- Constants and Application Paths ---
MAGIC_PS4 = 0x7f434E54
ICON0_ID = 0x1200
PARAM_SFO_ID = 0x1000
CACHE_FOLDER_NAME = "cached"
DB_FILE_NAME = "db.json"
CONFIG_FILE_NAME = "configs.json"
DEFAULT_SHOP_TITLE = "PS5 PKG Virtual Shop"
DEFAULT_PORT = 5000
ITEMS_PER_PAGE = 10

def get_base_path():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.abspath(os.path.dirname(__file__))

BASE_DIR = get_base_path()
CACHE_FOLDER_PATH = os.path.join(BASE_DIR, CACHE_FOLDER_NAME)
DB_FILE_PATH = os.path.join(BASE_DIR, DB_FILE_NAME)
CONFIG_FILE_PATH = os.path.join(BASE_DIR, CONFIG_FILE_NAME)

APP_CONFIG = {}
PKG_LOOKUP = {}
CATEGORIZED_DATA = {}

# --- PKG File Handling ---

def sanitize_filename(name):
    if not name: return None
    name = name.replace('\x00', '').strip()
    name = re.sub(r'[\\/*?:"<>|]', '_', name)
    if not name: return None
    return name

class PackageBase:
    FLAG_ENCRYPTED = 0x80000000
    def __init__(self, file: str):
        if not os.path.isfile(file): raise FileNotFoundError(f"The PKG file '{file}' does not exist.")
        self.original_file = file; self.files = {}; self.content_id = None
    def _safe_decode(self, data):
        if isinstance(data, bytes): return data.decode('utf-8', errors='ignore').rstrip('\x00')
        return str(data).rstrip('\x00')
    def read_file(self, file_id):
        file_info = self.files.get(file_id)
        if not file_info: raise ValueError(f"File with ID {file_id} not found.")
        with open(self.original_file, 'rb') as f:
            f.seek(file_info['offset']); data = f.read(file_info['size'])
        return data

class PackagePS4(PackageBase):
    MAGIC_PS4 = 0x7f434E54
    def __init__(self, file: str):
        super().__init__(file)
        with open(file, "rb") as fp:
            magic = struct.unpack(">I", fp.read(4))[0]
            if magic == self.MAGIC_PS4: self._load_ps4_pkg(fp)
            else: raise ValueError(f"Unknown PKG format: {magic:08X}")
    def _load_ps4_pkg(self, fp):
        try:
            header_format = ">5I2H2I4Q36s12s12I"; fp.seek(0)
            data = fp.read(struct.calcsize(header_format))
            unpacked = struct.unpack(header_format, data)
            self.pkg_entry_count = unpacked[4]
            self.pkg_table_offset = unpacked[7]
            self.content_id = self._safe_decode(unpacked[14])
            self.__load_files(fp)
        except Exception as e:
            logging.error(f"Error loading PS4 PKG file: {str(e)}"); raise
    def __load_files(self, fp):
        fp.seek(self.pkg_table_offset, os.SEEK_SET); entry_format = ">6IQ"
        for _ in range(self.pkg_entry_count):
            entry_data = fp.read(struct.calcsize(entry_format))
            file_id, _, _, _, offset, size, _ = struct.unpack(entry_format, entry_data)
            self.files[file_id] = {"id": file_id, "offset": offset, "size": size}

def parse_sfo(sfo_data):
    results = {"title": None, "category": None, "title_id": None}
    try:
        magic, _, key_table_offset, data_table_offset, num_entries = struct.unpack('<IIIII', sfo_data[0:20])
        if magic != 0x46535000: return results
        index_table_offset = 20
        for i in range(num_entries):
            entry_offset = index_table_offset + (i * 16)
            key_off, _, data_len, _, data_off = struct.unpack('<HHIII', sfo_data[entry_offset:entry_offset+16])
            key_start = key_table_offset + key_off; key_end = sfo_data.find(b'\x00', key_start)
            key = sfo_data[key_start:key_end].decode('utf-8')
            data_start = data_table_offset + data_off
            data_bytes = sfo_data[data_start:data_start+data_len]
            data = data_bytes.rstrip(b'\x00').decode('utf-8', errors='ignore')
            if key == "TITLE": results["title"] = data
            elif key == "CATEGORY": results["category"] = data
            elif key == "TITLE_ID": results["title_id"] = data
        return results
    except Exception as e:
        logging.error(f"Error parsing SFO: {e}"); return results

# --- Config & Cache ---

def load_or_create_config():
    if not os.path.exists(CONFIG_FILE_PATH):
        logging.warning(f"'{CONFIG_FILE_NAME}' not found. Creating a new one...")
        base_example_path = "C:\\Users\\YourUser\\Path\\To\\Your\\pkgs"
        default_config = {
            "shop_title": DEFAULT_SHOP_TITLE,
            "port": DEFAULT_PORT,
            "scan_on_startup": False,
            "paths": { "games": os.path.join(base_example_path, "games") }
        }
        try:
            with open(CONFIG_FILE_PATH, 'w', encoding='utf-8') as f:
                json.dump(default_config, f, indent=4)
            return default_config
        except Exception as e:
            logging.error(f"Failed to create '{CONFIG_FILE_NAME}': {e}"); raise
    try:
        with open(CONFIG_FILE_PATH, 'r', encoding='utf-8') as f: config = json.load(f)
        if "paths" not in config or not isinstance(config["paths"], dict):
            raise ValueError(f"'paths' not defined or malformed in '{CONFIG_FILE_NAME}'.")
        if "scan_on_startup" not in config:
            config["scan_on_startup"] = False
        return config
    except Exception as e:
        logging.error(f"Fatal error reading '{CONFIG_FILE_NAME}': {e}"); raise

def save_config(config_data):
    try:
        with open(CONFIG_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, indent=4)
        logging.info(f"Configuration saved to '{CONFIG_FILE_NAME}'.")
        return True
    except Exception as e:
        logging.error(f"Failed to save configuration: {e}"); return False

def load_cache():
    if os.path.exists(DB_FILE_PATH):
        try:
            with open(DB_FILE_PATH, 'r', encoding='utf-8') as f: return json.load(f)
        except json.JSONDecodeError: return {}
    return {}

def save_cache(cache_data):
    try:
        with open(DB_FILE_PATH, 'w', encoding='utf-8') as f: json.dump(cache_data, f, indent=4)
    except IOError as e: logging.error(f"Could not save cache: {e}")

def format_file_size(size_bytes):
    if size_bytes == 0: return "0B"
    gb = size_bytes / (1024**3);
    if gb >= 1: return f"{gb:.2f} GB"
    mb = size_bytes / (1024**2);
    return f"{mb:.2f} MB"

def get_local_ips():
    ip_list = []
    try:
        hostname = socket.gethostname()
        ips = socket.gethostbyname_ex(hostname)[2]
        for ip in ips:
            if not ip.startswith("127."):
                ip_list.append(ip)
    except Exception as e:
        logging.warning(f"Could not determine all local IPs via hostname: {e}")

    if not ip_list:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                ip = s.getsockname()[0]
                if ip and ip not in ip_list:
                    ip_list.append(ip)
        except Exception as e:
            logging.warning(f"Could not determine primary local IP using fallback: {e}")
    return ip_list

# --- Core Scanning Logic ---

def scan_and_cache_packages(pkg_folder_path, category_name, cache):
    logging.info(f"Recursively scanning directory: [{category_name}] {pkg_folder_path}")
    if not os.path.isdir(pkg_folder_path):
        logging.warning(f"Path for '{category_name}' is not a directory, skipping.")
        return ([], set())

    os.makedirs(CACHE_FOLDER_PATH, exist_ok=True)
    pkg_files_on_disk = glob.glob(os.path.join(pkg_folder_path, "**", "*.pkg"), recursive=True)
    pkg_data_list = []

    for pkg_path in pkg_files_on_disk:
        filename = os.path.basename(pkg_path)
        try:
            mtime = os.path.getmtime(pkg_path)
            
            if (pkg_path in cache and cache[pkg_path].get('mtime') == mtime):
                pkg_data = cache[pkg_path]
                pkg_data['category'] = category_name
            else:
                logging.info(f"Processing file: {filename}")
                pkg = PackagePS4(pkg_path)

                sfo_info = parse_sfo(pkg.read_file(PARAM_SFO_ID)) if PARAM_SFO_ID in pkg.files else {}
                
                pkg_data = {
                    "filepath": pkg_path,
                    "filename": filename,
                    "title": sfo_info.get("title"),
                    "content_id": pkg.content_id,
                    "category_type": sfo_info.get("category"),
                    "title_id": sfo_info.get("title_id"),
                    "mtime": mtime
                }
            
            unique_id = pkg_data.get("content_id")
            if unique_id:
                install_url = f"/serve_pkg_id/{unique_id}"
                image_base_name = sanitize_filename(unique_id)
            else:
                file_hash = hashlib.md5(os.path.abspath(pkg_path).encode('utf-8')).hexdigest()
                install_url = f"/serve_pkg_hash/{file_hash}"
                pkg_data['file_hash'] = file_hash 
                image_base_name = file_hash

            pkg_data['install_url'] = install_url

            if 'image_path' not in pkg_data or not os.path.exists(os.path.join(BASE_DIR, pkg_data['image_path'])):
                if image_base_name and PARAM_SFO_ID in pkg.files: # Re-read SFO to get ICON
                    try:
                        pkg_for_icon = PackagePS4(pkg_path)
                        if ICON0_ID in pkg_for_icon.files:
                            image_filename = f"{image_base_name}.png"
                            image_save_path_abs = os.path.join(CACHE_FOLDER_PATH, image_filename)
                            Image.open(io.BytesIO(pkg_for_icon.read_file(ICON0_ID))).save(image_save_path_abs, format="PNG")
                            pkg_data['image_path'] = f"{CACHE_FOLDER_NAME}/{image_filename}"
                        else:
                            pkg_data['image_path'] = None
                    except Exception:
                         pkg_data['image_path'] = None
                else:
                    pkg_data['image_path'] = None
            
            file_size = os.path.getsize(pkg_path)
            pkg_data['file_size_bytes'] = file_size
            pkg_data['file_size_str'] = format_file_size(file_size)
            
            cache[pkg_path] = pkg_data
            pkg_data_list.append(pkg_data)

        except Exception as e:
            logging.error(f"Failed to process {filename}: {e}")

    return (pkg_data_list, set(pkg_files_on_disk))


def clean_orphaned_cache_entries(cache, all_found_files_on_disk):
    orphaned_keys = [key for key in cache if key not in all_found_files_on_disk]
    if orphaned_keys:
        logging.info(f"Cleaning {len(orphaned_keys)} orphaned entries from cache.")
        for key in orphaned_keys: del cache[key]
    return cache

def perform_full_scan():
    paths = APP_CONFIG.get("paths")
    if not paths:
        logging.error("Scan failed: PKG paths not configured.")
        return []
    try:
        cache = load_cache()
        all_found_files = set()

        global CATEGORIZED_DATA
        CATEGORIZED_DATA.clear()

        for category, path in paths.items():
            final_category_list = []
            scanned_data, found_files = scan_and_cache_packages(os.path.abspath(path), category, cache)
            all_found_files.update(found_files)

            grouped_by_dir = {}
            for pkg_data in scanned_data:
                dir_path = os.path.dirname(pkg_data['filepath'])
                if dir_path not in grouped_by_dir:
                    grouped_by_dir[dir_path] = []
                grouped_by_dir[dir_path].append(pkg_data)

            root_path = os.path.abspath(path)
            for dir_path, pkgs_in_dir in grouped_by_dir.items():
                if os.path.abspath(dir_path) == root_path:
                    final_category_list.extend(pkgs_in_dir)
                else:
                    pack_title = os.path.basename(dir_path)
                    total_size = 0
                    icon_path = None
                    modal_items_list = []

                    def get_sort_key(pkg):
                        ctype = pkg.get('category_type')
                        if ctype in ['gd', 'gde']: return 1
                        if ctype == 'gp': return 2
                        if ctype == 'ac': return 3
                        return 4
                    pkgs_in_dir.sort(key=get_sort_key)

                    for pkg in pkgs_in_dir:
                        total_size += pkg.get('file_size_bytes', 0)
                        if not icon_path and (pkg.get('category_type') == 'gd' or pkg.get('category_type') == 'gde') and pkg.get('image_path'):
                            icon_path = pkg.get('image_path')

                        modal_items_list.append({
                            "title": pkg.get('title', 'Unknown'),
                            "category_type": pkg.get('category_type', 'N/A'),
                            "install_url": pkg.get('install_url')
                        })

                    if not icon_path and pkgs_in_dir and pkgs_in_dir[0].get('image_path'):
                        icon_path = pkgs_in_dir[0].get('image_path')

                    pack_object = {
                        "is_pack": True, "title": pack_title, "image_path": icon_path,
                        "file_size_bytes": total_size, "file_size_str": format_file_size(total_size),
                        "category": category, "category_type": "pack",
                        "items": modal_items_list, "install_url": None
                    }
                    final_category_list.append(pack_object)

            if final_category_list:
                final_category_list.sort(key=lambda x: x.get('title', ''))
                CATEGORIZED_DATA[category] = final_category_list

        save_cache(clean_orphaned_cache_entries(cache, all_found_files))

        global PKG_LOOKUP
        PKG_LOOKUP.clear()
        for pkg_path, data in cache.items():
            if data.get("content_id"):
                PKG_LOOKUP[data["content_id"]] = pkg_path
            if data.get("file_hash"):
                PKG_LOOKUP[data["file_hash"]] = pkg_path

        logging.info(f"Built lookup map with {len(PKG_LOOKUP)} entries.")
        non_empty_categories = sorted(list(CATEGORIZED_DATA.keys()))
        logging.info(f"Scan complete. Found non-empty categories: {non_empty_categories}")
        return non_empty_categories

    except Exception as e:
        logging.error(f"Error in perform_full_scan: {e}", exc_info=True)
        return []

# --- Flask API Routes ---

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

@app.route('/static/<path:path>')
def send_static_file(path):
    return send_from_directory('static', path)

@app.route('/cached/<path:path>')
def send_cached_image(path):
    return send_from_directory(CACHE_FOLDER_PATH, path)

@app.route('/api/settings')
def get_settings():
    return jsonify({"shop_title": APP_CONFIG.get("shop_title", DEFAULT_SHOP_TITLE)})

@app.route('/api/check_agent')
def check_agent():
    user_agent = request.headers.get('User-Agent', '')
    is_ps5 = "PlayStation 5" in user_agent
    logging.info(f"User-Agent Check: '{user_agent}' -> is_ps5: {is_ps5}")
    return jsonify({"is_ps5": is_ps5})

@app.route('/api/scan', methods=['GET'])
def api_scan_packages():
    try:
        if APP_CONFIG.get("scan_on_startup", False):
            non_empty_categories = sorted(list(CATEGORIZED_DATA.keys()))
        else:
            non_empty_categories = perform_full_scan()
        return jsonify({"categories": non_empty_categories})
    except Exception as e:
        logging.error(f"Error in /api/scan endpoint: {e}", exc_info=True)
        return jsonify({"error": f"Internal server error: {e}"}), 500

@app.route('/api/items', methods=['GET'])
def get_items_for_category():
    category = request.args.get('category')
    page = request.args.get('page', 1, type=int)

    if not category:
        return jsonify({"error": "Category parameter is required"}), 400

    all_items = CATEGORIZED_DATA.get(category, [])
    
    total_items = len(all_items)
    total_pages = math.ceil(total_items / ITEMS_PER_PAGE) if total_items > 0 else 1
    start_index = (page - 1) * ITEMS_PER_PAGE
    end_index = start_index + ITEMS_PER_PAGE
    items_for_page = all_items[start_index:end_index]

    return jsonify({
        'items': items_for_page,
        'current_page': page,
        'total_pages': total_pages,
    })

@app.route('/api/search', methods=['GET'])
def search_all_items():
    search_query = request.args.get('search', '').strip().lower()
    page = request.args.get('page', 1, type=int)

    if not search_query:
        return jsonify({"error": "Search query is required"}), 400

    all_matching_items = []
    for category_items in CATEGORIZED_DATA.values():
        for item in category_items:
            if search_query in (item.get('title') or '').lower():
                all_matching_items.append(item)

    total_items = len(all_matching_items)
    total_pages = math.ceil(total_items / ITEMS_PER_PAGE) if total_items > 0 else 1
    start_index = (page - 1) * ITEMS_PER_PAGE
    end_index = start_index + ITEMS_PER_PAGE
    items_for_page = all_matching_items[start_index:end_index]
    
    return jsonify({
        'items': items_for_page,
        'current_page': page,
        'total_pages': total_pages,
    })

# --- Serve Routes ---
@app.route('/serve_pkg_id/<content_id>')
def serve_pkg_id(content_id):
    pkg_path = PKG_LOOKUP.get(content_id)
    if not pkg_path or not os.path.exists(pkg_path):
        logging.error(f"Could not find PKG for Content ID: {content_id}")
        return "File not found by ID", 404
    directory = os.path.dirname(pkg_path)
    filename = os.path.basename(pkg_path)
    logging.info(f"Serving (by ID): {filename} from {directory}")
    return send_from_directory(directory, filename, as_attachment=True)
    
@app.route('/serve_pkg_hash/<file_hash>')
def serve_pkg_hash(file_hash):
    pkg_path = PKG_LOOKUP.get(file_hash)
    if not pkg_path or not os.path.exists(pkg_path):
        logging.error(f"Could not find PKG for hash: {file_hash}")
        return "File not found by hash", 404
    directory = os.path.dirname(pkg_path)
    filename = os.path.basename(pkg_path)
    logging.info(f"Serving (by hash): {filename} from {directory}")
    return send_from_directory(directory, filename, as_attachment=True)

# --- Server Runner ---

def run_flask_app(config, log_queue=None):
    if log_queue:
        queue_handler = QueueHandler(log_queue)
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        root_logger.handlers.clear()
        root_logger.addHandler(queue_handler)

    global APP_CONFIG
    APP_CONFIG = config

    if APP_CONFIG.get("scan_on_startup", False):
        logging.info("Config 'scan_on_startup' is TRUE. Performing full scan now...")
        perform_full_scan()
        logging.info("Startup scan complete.")
    
    port = APP_CONFIG.get('port', DEFAULT_PORT)
    logging.info(f"Server starting on port {port}...")
    logging.info(f" - For this PC: http://127.0.0.1:{port}")
    local_ips = get_local_ips()
    if local_ips:
        for ip in local_ips:
            logging.info(f" - On your network: http://{ip}:{port}")
    else:
        logging.info(" - Could not determine local network IP. Access may be limited to this PC.")
    serve(app, host='0.0.0.0', port=port, _quiet=True)

# ===================================================================
# PART 2: GRAPHICAL USER INTERFACE (GUI) WITH TKINTER
# ===================================================================

class AppGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PS5 PKG Server Control Panel")
        self.geometry("800x680")
        self.server_process = None
        self.log_queue = Queue()
        self.create_widgets()
        self.load_config_to_gui()
        self.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.process_log_queue()

    def create_widgets(self):
        main_frame = ttk.Frame(self, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        config_frame = ttk.LabelFrame(main_frame, text="Settings", padding="10")
        config_frame.pack(fill=tk.X, expand=False)
        config_frame.columnconfigure(1, weight=1)

        ttk.Label(config_frame, text="Shop Title:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.shop_title_var = tk.StringVar()
        ttk.Entry(config_frame, textvariable=self.shop_title_var).grid(row=0, column=1, sticky=tk.EW)

        ttk.Label(config_frame, text="Server Port:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.port_var = tk.StringVar()
        ttk.Entry(config_frame, textvariable=self.port_var).grid(row=1, column=1, sticky=tk.EW)

        self.scan_on_startup_var = tk.BooleanVar()
        ttk.Checkbutton(config_frame, text="Scan on Startup (requires server restart)",
                        variable=self.scan_on_startup_var).grid(row=2, column=0, columnspan=2, sticky=tk.W, pady=5)

        ttk.Button(config_frame, text="Save Settings", command=self.save_gui_config).grid(row=3, column=0, columnspan=2, pady=10)

        paths_frame = ttk.LabelFrame(main_frame, text="PKG Paths (Categories)", padding="10")
        paths_frame.pack(fill=tk.BOTH, expand=True, pady=10)
        self.tree = ttk.Treeview(paths_frame, columns=("category", "path"), show="headings")
        self.tree.heading("category", text="Category Name"); self.tree.heading("path", text="Folder Path")
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(paths_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscroll=scrollbar.set); scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        path_buttons_frame = ttk.Frame(paths_frame)
        path_buttons_frame.pack(side=tk.LEFT, fill=tk.Y, padx=5)
        ttk.Button(path_buttons_frame, text="Add", command=self.add_path).pack(pady=2)
        ttk.Button(path_buttons_frame, text="Remove", command=self.remove_path).pack(pady=2)
        ttk.Button(path_buttons_frame, text="Edit", command=self.edit_path).pack(pady=2)
        ttk.Button(path_buttons_frame, text="Save Paths", command=self.save_gui_config).pack(pady=2)

        bottom_frame = ttk.Frame(main_frame)
        bottom_frame.pack(fill=tk.X, expand=False, pady=(10, 0))
        self.start_button = ttk.Button(bottom_frame, text="Start Server", command=self.start_server)
        self.start_button.pack(side=tk.LEFT, padx=5)
        self.stop_button = ttk.Button(bottom_frame, text="Stop Server", command=self.stop_server, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=5)
        self.status_label = ttk.Label(bottom_frame, text="Status: Stopped", foreground="red", font=("Helvetica", 10, "bold"))
        self.status_label.pack(side=tk.LEFT, padx=10)

        log_frame = ttk.LabelFrame(main_frame, text="Logs", padding="10")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=10)
        self.log_text = tk.Text(log_frame, state='disabled', wrap='word', bg="#2b2b2b", fg="white")
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text['yscrollcommand'] = log_scrollbar.set
        log_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.log_text.tag_configure("hyperlink", foreground="cyan", underline=True)
        self.log_text.tag_bind("hyperlink", "<Enter>", self._show_hand_cursor)
        self.log_text.tag_bind("hyperlink", "<Leave>", self._show_arrow_cursor)
        self.log_text.tag_bind("hyperlink", "<Button-1>", self._open_link)
        self.hyperlink_map = {}

    def _show_hand_cursor(self, event): self.config(cursor="hand2")
    def _show_arrow_cursor(self, event): self.config(cursor="")
    def _open_link(self, event):
        tag_name = next((tag for tag in self.log_text.tag_names(self.log_text.index(f"@{event.x},{event.y}")) if tag.startswith("hlink-")), None)
        if tag_name in self.hyperlink_map:
            webbrowser.open_new_tab(self.hyperlink_map[tag_name])

    def setup_logging(self):
        root_logger = logging.getLogger()
        root_logger.handlers.clear()
        root_logger.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s %(levelname)s: %(message)s', '%H:%M:%S')
        text_handler = TextHandler(self.log_text, self)
        text_handler.setFormatter(formatter)
        root_logger.addHandler(text_handler)

    def process_log_queue(self):
        try:
            while True:
                record = self.log_queue.get_nowait()
                logger = logging.getLogger(record.name)
                logger.handle(record)
        except Exception:
            pass
        self.after(100, self.process_log_queue)

    def load_config_to_gui(self):
        global APP_CONFIG
        APP_CONFIG = load_or_create_config()
        self.shop_title_var.set(APP_CONFIG.get("shop_title", DEFAULT_SHOP_TITLE))
        self.port_var.set(str(APP_CONFIG.get("port", DEFAULT_PORT)))
        self.scan_on_startup_var.set(APP_CONFIG.get("scan_on_startup", False))
        for item in self.tree.get_children(): self.tree.delete(item)
        for category, path in APP_CONFIG.get("paths", {}).items():
            self.tree.insert("", tk.END, values=(category, path))

    def save_gui_config(self):
        try:
            current_config = load_or_create_config()
            current_config["shop_title"] = self.shop_title_var.get()
            current_config["port"] = int(self.port_var.get())
            current_config["scan_on_startup"] = self.scan_on_startup_var.get()
            current_config["paths"] = {self.tree.item(i)['values'][0]: self.tree.item(i)['values'][1] for i in self.tree.get_children()}

            if save_config(current_config):
                global APP_CONFIG
                APP_CONFIG = current_config
                messagebox.showinfo("Success", "Configuration saved successfully!")
            else:
                messagebox.showerror("Error", "Failed to save configuration.")
        except ValueError: messagebox.showerror("Invalid Input", "Port must be a number.")
        except Exception as e: messagebox.showerror("Error", f"An error occurred: {e}")

    def add_path(self):
        dialog = PathDialog(self, title="Add Path")
        if dialog.result: self.tree.insert("", tk.END, values=dialog.result)

    def remove_path(self):
        if (selected_item := self.tree.selection()) and messagebox.askyesno("Confirm", "Remove selected path?"):
            self.tree.delete(selected_item)

    def edit_path(self):
        if not (selected_item := self.tree.selection()): return
        category, path = self.tree.item(selected_item)['values']
        dialog = PathDialog(self, title="Edit Path", initial_category=category, initial_path=path)
        if dialog.result: self.tree.item(selected_item, values=dialog.result)

    def start_server(self):
        if self.server_process and self.server_process.is_alive():
            logging.warning("Server is already running.")
            return
        self.update_status("Starting...", "orange")
        self.start_button.config(state=tk.DISABLED)
        self.save_button_state(tk.DISABLED)
        current_config = load_or_create_config()
        self.server_process = Process(target=run_flask_app, args=(current_config, self.log_queue), daemon=True)
        self.server_process.start()
        self.after(2000, self.check_server_status)

    def check_server_status(self):
        if self.server_process and self.server_process.is_alive():
            self.update_status("Running", "green")
            self.stop_button.config(state=tk.NORMAL)
        else:
            self.update_status("Stopped", "red")
            self.start_button.config(state=tk.NORMAL)
            self.save_button_state(tk.NORMAL)
            self.stop_button.config(state=tk.DISABLED)
            if self.server_process:
                logging.error("Server failed to start or stopped unexpectedly.")
                self.server_process = None

    def stop_server(self):
        if not (self.server_process and self.server_process.is_alive()):
            logging.warning("Server is not running.")
            self.check_server_status()
            return
        self.update_status("Stopping...", "orange")
        self.stop_button.config(state=tk.DISABLED)
        self.server_process.terminate()
        self.server_process.join(timeout=2)
        self.server_process = None
        logging.info("Server has been stopped.")
        self.check_server_status()

    def save_button_state(self, state):
        for child in self.winfo_children():
            if isinstance(child, ttk.LabelFrame):
                for btn in child.winfo_children():
                    if isinstance(btn, ttk.Button) and "save" in btn.cget("text").lower():
                        btn.config(state=state)
                for frame in child.winfo_children():
                    if isinstance(frame, ttk.Frame):
                        for btn in frame.winfo_children():
                            if isinstance(btn, ttk.Button) and "save" in btn.cget("text").lower():
                                btn.config(state=state)

    def update_status(self, text, color):
        self.status_label.config(text=f"Status: {text}", foreground=color)

    def on_closing(self):
        if self.server_process and self.server_process.is_alive():
            if messagebox.askyesno("Exit", "The server is running. Stop server and exit?"):
                self.stop_server()
                self.destroy()
        else:
            self.destroy()

# --- GUI Helper Classes ---

class TextHandler(logging.Handler):
    def __init__(self, text_widget, app_gui_instance):
        super().__init__()
        self.text_widget = text_widget
        self.app_gui = app_gui_instance
    def emit(self, record):
        self.text_widget.after(0, lambda: self.append_log(self.format(record)))
    def append_log(self, msg):
        self.text_widget.configure(state='normal')
        last_end = 0
        for match in re.finditer(r'https?://\S+', msg):
            start, end = match.span()
            url = match.group(0)

            self.text_widget.insert(tk.END, msg[last_end:start])
            link_start_index = self.text_widget.index(tk.END)
            link_tag = f"hlink-{link_start_index.replace('.', '-')}"
            self.app_gui.hyperlink_map[link_tag] = url
            self.text_widget.insert(tk.END, url, ("hyperlink", link_tag))
            last_end = end

        self.text_widget.insert(tk.END, msg[last_end:] + '\n')
        self.text_widget.configure(state='disabled')
        self.text_widget.yview(tk.END)

class PathDialog(tk.Toplevel):
    def __init__(self, parent, title=None, initial_category="", initial_path=""):
        super().__init__(parent)
        self.transient(parent); self.title(title or "Path"); self.result = None
        frame = ttk.Frame(self, padding="10"); frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text="Category:").grid(row=0, column=0, sticky="w", pady=2)
        self.e1 = ttk.Entry(frame); self.e1.grid(row=0, column=1, sticky="ew"); self.e1.insert(0, initial_category)
        ttk.Label(frame, text="Path:").grid(row=1, column=0, sticky="w", pady=2)
        self.e2 = ttk.Entry(frame, width=50); self.e2.grid(row=1, column=1, sticky="ew"); self.e2.insert(0, initial_path)
        ttk.Button(frame, text="Browse...", command=self.browse_path).grid(row=1, column=2, padx=5, sticky="e")
        frame.columnconfigure(1, weight=1); self.initial_focus = self.e1
        box = ttk.Frame(self, padding="5"); box.pack(fill=tk.X)
        ttk.Button(box, text="OK", width=10, command=self.ok).pack(side=tk.RIGHT, padx=5)
        ttk.Button(box, text="Cancel", width=10, command=self.cancel).pack(side=tk.RIGHT)
        self.bind("<Return>", self.ok); self.bind("<Escape>", self.cancel)
        self.grab_set(); self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.geometry(f"+{parent.winfo_rootx()+50}+{parent.winfo_rooty()+50}")
        self.initial_focus.focus_set(); self.wait_window(self)
    def browse_path(self):
        if path := filedialog.askdirectory(title="Select PKG Folder"):
            self.e2.delete(0, tk.END); self.e2.insert(0, path)
    def ok(self, event=None):
        category, path = self.e1.get().strip(), self.e2.get().strip()
        if not (category and path): messagebox.showerror("Error", "Both fields are required.", parent=self); return
        self.result = (category, path); self.destroy()
    def cancel(self, event=None): self.destroy()

# ===================================================================
# PART 3: APPLICATION ENTRY POINT
# ===================================================================

if __name__ == '__main__':
    freeze_support()
    config = load_or_create_config()
    if config.get("docker", False):
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s [%(levelname)s]: %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        logging.info("Docker mode detected. Starting server without GUI...")
        try:
            run_flask_app(config, log_queue=None)
        except KeyboardInterrupt:
            logging.info("Server stopped by user (Ctrl+C).")
        except Exception as e:
            logging.error(f"An unexpected error occurred: {e}", exc_info=True)
    else:
        gui = AppGUI()
        gui.setup_logging()
        logging.info("Application started. Configure and press 'Start Server'.")
        gui.mainloop()
