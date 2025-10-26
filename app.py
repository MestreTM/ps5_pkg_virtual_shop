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

from waitress import serve
from PIL import Image
from flask import Flask, request, jsonify, send_from_directory

# ==============================================================================
# PART 1: FLASK SERVER LOGIC
# This section contains all the backend logic for the web server, including
# PKG file parsing, caching, and API endpoint definitions.
# ==============================================================================

app = Flask(__name__)
# Suppress standard Flask/Werkzeug logging to the console to avoid clutter.
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

# --- Constants and Application Paths ---
# These constants define magic numbers, file IDs, and default file/folder names.
MAGIC_PS4 = 0x7f434E54
ICON0_ID = 0x1200
PARAM_SFO_ID = 0x1000
CACHE_FOLDER_NAME = "cached"
DB_FILE_NAME = "db.json"
CONFIG_FILE_NAME = "configs.json"
DEFAULT_SHOP_TITLE = "PS5 PKG Virtual Shop"
DEFAULT_PORT = 5000

def get_base_path():
    """
    Determines the base path for the application, supporting both
    normal execution (.py) and bundled executables (e.g., via PyInstaller).
    """
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.abspath(os.path.dirname(__file__))

# Define absolute paths for critical files and folders.
BASE_DIR = get_base_path()
CACHE_FOLDER_PATH = os.path.join(BASE_DIR, CACHE_FOLDER_NAME)
DB_FILE_PATH = os.path.join(BASE_DIR, DB_FILE_NAME)
CONFIG_FILE_PATH = os.path.join(BASE_DIR, CONFIG_FILE_NAME)

# Global dictionary to hold the application configuration.
APP_CONFIG = {}

# --- PKG File Handling Functions and Classes ---

def sanitize_filename(name):
    """
    Removes null bytes and illegal characters from a string to create a
    valid filename.
    """
    if not name: return None
    name = name.replace('\x00', '').strip()
    name = re.sub(r'[\\/*?:"<>|]', '_', name)
    if not name: return None
    return name

# Enums for representing different PKG metadata types.
class DRMType(IntEnum): NONE = 0x0; PS4 = 0xF; PS5 = 0x10
class ContentType(IntEnum): UNKNOWN = 0x0; GAME_DATA = 0x4; GAME_EXEC = 0x5; PS1_EMU = 0x6; PSP = 0x7; THEME = 0x9; WIDGET = 0xA; LICENSE = 0xB; VSH_MODULE = 0xC; PSN_AVATAR = 0xD; PSPGO = 0xE; MINIS = 0xF; NEOGEO = 0x10; VMC = 0x11; PS2_CLASSIC = 0x12; PSP_REMASTERED = 0x14; PSP2GD = 0x15; PSP2AC = 0x16; PSP2LA = 0x17; PSM = 0x18; WT = 0x19; PSP2_THEME = 0x1F
class IROTag(Enum): SHAREFACTORY_THEME = 0x1; SYSTEM_THEME = 0x2

class PackageBase:
    """
    A base class for handling package files, providing common functionality
    like file reading.
    """
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
    """
    A class specifically for parsing PS4 PKG file headers and metadata.
    """
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
    """
    Parses the binary param.sfo data to extract the package title.
    """
    try:
        magic, _, key_table_offset, data_table_offset, num_entries = struct.unpack('<IIIII', sfo_data[0:20])
        if magic != 0x46535000: return None
        index_table_offset = 20
        for i in range(num_entries):
            entry_offset = index_table_offset + (i * 16)
            key_off, _, data_len, _, data_off = struct.unpack('<HHIII', sfo_data[entry_offset:entry_offset+16])
            key_start = key_table_offset + key_off; key_end = sfo_data.find(b'\x00', key_start)
            key = sfo_data[key_start:key_end].decode('utf-8')
            if key == "TITLE":
                data_start = data_table_offset + data_off
                return sfo_data[data_start:data_start+data_len].rstrip(b'\x00').decode('utf-8')
        return None
    except Exception as e:
        logging.error(f"Error parsing SFO: {e}"); return None

# --- Configuration and Cache Management ---

def load_or_create_config():
    """
    Loads the application configuration from configs.json. If the file
    doesn't exist, it creates a default one.
    """
    if not os.path.exists(CONFIG_FILE_PATH):
        logging.warning(f"'{CONFIG_FILE_NAME}' not found. Creating a new one...")
        base_example_path = "C:\\Users\\YourUser\\Path\\To\\Your\\pkgs"
        default_config = {
            "shop_title": DEFAULT_SHOP_TITLE, "port": DEFAULT_PORT,
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
        return config
    except Exception as e:
        logging.error(f"Fatal error reading '{CONFIG_FILE_NAME}': {e}"); raise

def save_config(config_data):
    """Saves the provided configuration data to configs.json."""
    try:
        with open(CONFIG_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, indent=4)
        logging.info(f"Configuration saved to '{CONFIG_FILE_NAME}'.")
        return True
    except Exception as e:
        logging.error(f"Failed to save configuration: {e}")
        return False

def load_cache():
    """Loads the PKG metadata cache from db.json."""
    if os.path.exists(DB_FILE_PATH):
        try:
            with open(DB_FILE_PATH, 'r', encoding='utf-8') as f: return json.load(f)
        except json.JSONDecodeError: return {}
    return {}

def save_cache(cache_data):
    """Saves the provided cache data to db.json."""
    try:
        with open(DB_FILE_PATH, 'w', encoding='utf-8') as f: json.dump(cache_data, f, indent=4)
    except IOError as e: logging.error(f"Could not save cache: {e}")

def format_file_size(size_bytes):
    """Converts bytes into a human-readable string (MB or GB)."""
    if size_bytes == 0: return "0B"
    gb = size_bytes / (1024**3);
    if gb >= 1: return f"{gb:.2f} GB"
    mb = size_bytes / (1024**2);
    return f"{mb:.2f} MB"

# --- Core Scanning Logic ---

def scan_and_cache_packages(pkg_folder_path, category_name, cache):
    """
    Scans a directory for .pkg files. For each file, it either retrieves
    data from the cache or processes the file to extract metadata (title, icon)
    and saves it to the cache.
    """
    logging.info(f"Scanning directory: [{category_name}] {pkg_folder_path}")
    if not os.path.isdir(pkg_folder_path):
        logging.warning(f"Path for '{category_name}' is not a directory, skipping.")
        return ([], set())
    os.makedirs(CACHE_FOLDER_PATH, exist_ok=True)
    pkg_files_on_disk = glob.glob(os.path.join(pkg_folder_path, "*.pkg"))
    pkg_data_list = []
    for pkg_path in pkg_files_on_disk:
        filename = os.path.basename(pkg_path)
        try:
            mtime = os.path.getmtime(pkg_path)
            # If file is in cache and unmodified, use cached data.
            if pkg_path in cache and cache[pkg_path].get('mtime') == mtime and 'install_url' in cache[pkg_path]:
                cache[pkg_path]['category'] = category_name
                pkg_data_list.append(cache[pkg_path])
                continue
            
            # Process the new or modified file.
            logging.info(f"Processing file: {filename}")
            pkg = PackagePS4(pkg_path)
            title = parse_sfo(pkg.read_file(PARAM_SFO_ID)) if PARAM_SFO_ID in pkg.files else None
            
            # Extract and save the icon (ICON0.PNG).
            image_path_rel = None
            if ICON0_ID in pkg.files:
                image_base_name = sanitize_filename(pkg.content_id or os.path.splitext(filename)[0])
                if image_base_name:
                    image_filename = f"{image_base_name}.png"
                    image_save_path_abs = os.path.join(CACHE_FOLDER_PATH, image_filename)
                    Image.open(io.BytesIO(pkg.read_file(ICON0_ID))).save(image_save_path_abs, format="PNG")
                    image_path_rel = f"{CACHE_FOLDER_NAME}/{image_filename}"
            
            file_size = os.path.getsize(pkg_path)
            pkg_data = {
                "filepath": pkg_path, "filename": filename, "title": title,
                "content_id": pkg.content_id, "file_size_bytes": file_size,
                "file_size_str": format_file_size(file_size), "image_path": image_path_rel, 
                "mtime": mtime, "category": category_name,
                "install_url": f"/serve_pkg/{category_name}/{filename}"
            }
            cache[pkg_path] = pkg_data
            pkg_data_list.append(pkg_data)
        except Exception as e:
            logging.error(f"Failed to process {filename}: {e}")
    return (pkg_data_list, set(pkg_files_on_disk))

def clean_orphaned_cache_entries(cache, all_found_files_on_disk):
    """
    Removes entries from the cache that correspond to .pkg files which no longer
    exist on disk.
    """
    orphaned_keys = [key for key in cache if key not in all_found_files_on_disk]
    if orphaned_keys:
        logging.info(f"Cleaning {len(orphaned_keys)} orphaned entries from cache.")
        for key in orphaned_keys: del cache[key]
    return cache

# --- Flask API Routes ---

@app.route('/')
def index(): 
    """Serves the main HTML page for the web interface."""
    return send_from_directory('static', 'index.html')

@app.route('/static/<path:path>')
def send_static_file(path): 
    """Serves static assets like CSS and JavaScript."""
    return send_from_directory('static', path)

@app.route('/cached/<path:path>')
def send_cached_image(path): 
    """Serves cached package icons."""
    return send_from_directory(CACHE_FOLDER_PATH, path)

@app.route('/api/settings')
def get_settings(): 
    """Provides basic settings like the shop title to the frontend."""
    return jsonify({"shop_title": APP_CONFIG.get("shop_title", DEFAULT_SHOP_TITLE)})

@app.route('/api/check_agent')
def check_agent():
    """Checks the User-Agent to determine if the client is a PS5."""
    user_agent = request.headers.get('User-Agent', '')
    is_ps5 = "PlayStation 5" in user_agent
    logging.info(f"User-Agent Check: '{user_agent}' -> is_ps5: {is_ps5}")
    return jsonify({"is_ps5": is_ps5})

@app.route('/api/scan', methods=['GET'])
def api_scan_packages():
    """
    Triggers a full scan of all configured PKG directories, updates the
    cache, and returns a JSON list of all found packages.
    """
    paths = APP_CONFIG.get("paths")
    if not paths: return jsonify({"error": "PKG paths not configured."}), 500
    try:
        cache = load_cache()
        all_pkg_data, all_found_files = [], set()
        for category, path in paths.items():
            scanned_data, found_files = scan_and_cache_packages(os.path.abspath(path), category, cache)
            all_pkg_data.extend(scanned_data)
            all_found_files.update(found_files)
        save_cache(clean_orphaned_cache_entries(cache, all_found_files))
        return jsonify(all_pkg_data)
    except Exception as e:
        logging.error(f"Error in /api/scan: {e}", exc_info=True)
        return jsonify({"error": f"Internal server error: {e}"}), 500

@app.route('/serve_pkg/<category>/<path:filename>')
def serve_pkg_file(category, filename):
    """
    Serves a specific .pkg file for download/installation on the console.
    """
    directory_path = APP_CONFIG.get("paths", {}).get(category)
    if not directory_path or not os.path.isdir(directory_path): return "Invalid category", 404
    logging.info(f"Serving file: {filename} from {directory_path}")
    try:
        return send_from_directory(directory_path, filename, as_attachment=True)
    except FileNotFoundError: return "File not found", 404

# --- Flask Server Runner ---

def run_flask_app(config, log_queue):
    """
    This function is the entry point for the separate server process.
    It sets up logging to communicate back to the GUI and starts the
    Waitress WSGI server.
    """
    queue_handler = QueueHandler(log_queue)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()
    root_logger.addHandler(queue_handler)
    global APP_CONFIG
    APP_CONFIG = config
    port = APP_CONFIG.get('port', DEFAULT_PORT)
    logging.info(f"Server starting on http://0.0.0.0:{port}")
    logging.info(f"Access locally at http://127.0.0.1:{port}")
    serve(app, host='0.0.0.0', port=port, _quiet=True)

# ===================================================================
# PART 2: GRAPHICAL USER INTERFACE (GUI) WITH TKINTER
# This section defines the desktop application window, its controls,
# and the logic for managing the server process.
# ===================================================================

class AppGUI(tk.Tk):
    """
    The main application class for the Tkinter control panel.
    """
    def __init__(self):
        super().__init__()
        self.title("PS5 PKG Server Control Panel")
        self.geometry("800x600")
        self.server_process = None
        self.log_queue = Queue()
        self.create_widgets()
        self.load_config_to_gui()
        self.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.process_log_queue()

    def create_widgets(self):
        """Initializes and lays out all the GUI elements."""
        main_frame = ttk.Frame(self, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        # --- Settings Frame ---
        config_frame = ttk.LabelFrame(main_frame, text="Settings", padding="10")
        config_frame.pack(fill=tk.X, expand=False)
        config_frame.columnconfigure(1, weight=1)
        ttk.Label(config_frame, text="Shop Title:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.shop_title_var = tk.StringVar()
        ttk.Entry(config_frame, textvariable=self.shop_title_var).grid(row=0, column=1, sticky=tk.EW)
        ttk.Label(config_frame, text="Server Port:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.port_var = tk.StringVar()
        ttk.Entry(config_frame, textvariable=self.port_var).grid(row=1, column=1, sticky=tk.EW)
        ttk.Button(config_frame, text="Save Settings", command=self.save_gui_config).grid(row=2, column=0, columnspan=2, pady=10)
        # --- Paths Frame ---
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
        # --- Bottom Control Frame ---
        bottom_frame = ttk.Frame(main_frame)
        bottom_frame.pack(fill=tk.X, expand=False, pady=(10, 0))
        self.start_button = ttk.Button(bottom_frame, text="Start Server", command=self.start_server)
        self.start_button.pack(side=tk.LEFT, padx=5)
        self.stop_button = ttk.Button(bottom_frame, text="Stop Server", command=self.stop_server, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=5)
        self.status_label = ttk.Label(bottom_frame, text="Status: Stopped", foreground="red", font=("Helvetica", 10, "bold"))
        self.status_label.pack(side=tk.LEFT, padx=10)
        # --- Log Frame ---
        log_frame = ttk.LabelFrame(main_frame, text="Logs", padding="10")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=10)
        self.log_text = tk.Text(log_frame, state='disabled', wrap='word', height=10, bg="#2b2b2b", fg="white")
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text['yscrollcommand'] = log_scrollbar.set
        log_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        # --- Hyperlink Setup for Logs ---
        self.log_text.tag_configure("hyperlink", foreground="cyan", underline=True)
        self.log_text.tag_bind("hyperlink", "<Enter>", self._show_hand_cursor)
        self.log_text.tag_bind("hyperlink", "<Leave>", self._show_arrow_cursor)
        self.log_text.tag_bind("hyperlink", "<Button-1>", self._open_link)
        self.hyperlink_map = {}

    def _show_hand_cursor(self, event): self.config(cursor="hand2")
    def _show_arrow_cursor(self, event): self.config(cursor="")
    def _open_link(self, event):
        """Opens a URL when a hyperlink in the log is clicked."""
        tag_name = next((tag for tag in self.log_text.tag_names(self.log_text.index(f"@{event.x},{event.y}")) if tag.startswith("hlink-")), None)
        if tag_name in self.hyperlink_map:
            webbrowser.open_new_tab(self.hyperlink_map[tag_name])
            
    def setup_logging(self):
        """Configures the root logger to send messages to the GUI's log widget."""
        root_logger = logging.getLogger()
        root_logger.handlers.clear()
        root_logger.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s %(levelname)s: %(message)s', '%H:%M:%S')
        text_handler = TextHandler(self.log_text, self)
        text_handler.setFormatter(formatter)
        root_logger.addHandler(text_handler)

    def process_log_queue(self):
        """
        Periodically checks the multiprocessing queue for log records from the
        server process and displays them in the GUI.
        """
        try:
            while True:
                record = self.log_queue.get_nowait()
                logger = logging.getLogger(record.name)
                logger.handle(record)
        except Exception:
            pass
        self.after(100, self.process_log_queue)

    def load_config_to_gui(self):
        """Loads settings from configs.json and populates the GUI fields."""
        global APP_CONFIG
        APP_CONFIG = load_or_create_config()
        self.shop_title_var.set(APP_CONFIG.get("shop_title", DEFAULT_SHOP_TITLE))
        self.port_var.set(str(APP_CONFIG.get("port", DEFAULT_PORT)))
        for item in self.tree.get_children(): self.tree.delete(item)
        for category, path in APP_CONFIG.get("paths", {}).items():
            self.tree.insert("", tk.END, values=(category, path))

    def save_gui_config(self):
        """Reads values from the GUI fields and saves them to configs.json."""
        try:
            new_config = {
                "shop_title": self.shop_title_var.get(),
                "port": int(self.port_var.get()),
                "paths": {self.tree.item(i)['values'][0]: self.tree.item(i)['values'][1] for i in self.tree.get_children()}
            }
            if save_config(new_config):
                global APP_CONFIG
                APP_CONFIG = new_config
                messagebox.showinfo("Success", "Configuration saved successfully!")
            else:
                messagebox.showerror("Error", "Failed to save configuration.")
        except ValueError: messagebox.showerror("Invalid Input", "Port must be a number.")
        except Exception as e: messagebox.showerror("Error", f"An error occurred: {e}")

    # --- Path Management Methods ---
    def add_path(self):
        """Opens a dialog to add a new category and path."""
        dialog = PathDialog(self, title="Add Path")
        if dialog.result: self.tree.insert("", tk.END, values=dialog.result)

    def remove_path(self):
        """Removes the selected path from the treeview."""
        if (selected_item := self.tree.selection()) and messagebox.askyesno("Confirm", "Remove selected path?"):
            self.tree.delete(selected_item)
    
    def edit_path(self):
        """Opens a dialog to edit the selected category and path."""
        if not (selected_item := self.tree.selection()): return
        category, path = self.tree.item(selected_item)['values']
        dialog = PathDialog(self, title="Edit Path", initial_category=category, initial_path=path)
        if dialog.result: self.tree.item(selected_item, values=dialog.result)

    # --- Server Control Methods ---
    def start_server(self):
        """
        Starts the Flask server in a new process.
        """
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
        """Checks if the server process is alive and updates the GUI status."""
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
        """Terminates the server process."""
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
        """Disables or enables all 'Save' buttons to prevent config changes while the server is running."""
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
        """Updates the status label text and color."""
        self.status_label.config(text=f"Status: {text}", foreground=color)

    def on_closing(self):
        """Handles the window close event, ensuring the server is stopped."""
        if self.server_process and self.server_process.is_alive():
            if messagebox.askyesno("Exit", "The server is running. Stop server and exit?"):
                self.stop_server()
                self.destroy()
        else:
            self.destroy()

# --- GUI Helper Classes ---

class TextHandler(logging.Handler):
    """
    A custom logging handler that redirects log records to a Tkinter Text widget.
    It also detects and formats URLs as clickable hyperlinks.
    """
    def __init__(self, text_widget, app_gui_instance):
        super().__init__()
        self.text_widget = text_widget
        self.app_gui = app_gui_instance
    def emit(self, record):
        self.text_widget.after(0, lambda: self.append_log(self.format(record)))
    def append_log(self, msg):
        self.text_widget.configure(state='normal')
        
        # Search for a URL in the log message.
        url_match = re.search(r'(https?://[^\s]+)', msg)
        if url_match:
            url = url_match.group(1)
            parts = msg.split(url, 1)
            
            # Insert the text before the URL.
            self.text_widget.insert(tk.END, parts[0])
            
            # Insert the URL with a unique hyperlink tag.
            link_start = self.text_widget.index(tk.END)
            link_tag = f"hlink-{link_start.replace('.', '-')}"
            self.app_gui.hyperlink_map[link_tag] = url
            self.text_widget.insert(tk.END, url, ("hyperlink", link_tag))
            
            # Insert the rest of the message.
            self.text_widget.insert(tk.END, parts[1] + '\n')
        else:
            self.text_widget.insert(tk.END, msg + '\n')

        self.text_widget.configure(state='disabled')
        self.text_widget.yview(tk.END)

class PathDialog(tk.Toplevel):
    """
    A modal dialog window for adding or editing a category and its associated path.
    """
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
    # freeze_support() is necessary for multiprocessing in bundled executables.
    freeze_support() 
    gui = AppGUI()
    gui.setup_logging()
    logging.info("Application started. Configure and press 'Start Server'.")
    gui.mainloop()
