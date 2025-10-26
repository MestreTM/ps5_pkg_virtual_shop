import os
import glob
import struct
import logging
import io
import json
import re
from enum import Enum, IntEnum
from PIL import Image
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__)
# Log level set to INFO to provide more details during execution
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

# --- Constants ---
MAGIC_PS4 = 0x7f434E54
ICON0_ID = 0x1200
PARAM_SFO_ID = 0x1000
CACHE_FOLDER_NAME = "cached"
DB_FILE_NAME = "db.json"
CONFIG_FILE_NAME = "configs.json"
DEFAULT_SHOP_TITLE = "PS5 PKG Virtual Shop"

# --- Absolute Paths ---
# Defines base paths for the application's files.
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
CACHE_FOLDER_PATH = os.path.join(BASE_DIR, CACHE_FOLDER_NAME)
DB_FILE_PATH = os.path.join(BASE_DIR, DB_FILE_NAME)
CONFIG_FILE_PATH = os.path.join(BASE_DIR, CONFIG_FILE_NAME)

def sanitize_filename(name):
    """Removes illegal characters and null bytes from filenames."""
    if not name: return None
    name = name.replace('\x00', '').strip()
    name = re.sub(r'[\\/*?:"<>|]', '_', name)
    if not name: return None
    return name

# A simple static logger class for different log levels.
class Logger:
    @staticmethod
    def log_information(message): logging.info(message)
    @staticmethod
    def log_warning(message): logging.warning(message)
    @staticmethod
    def log_error(message): logging.error(message)

# Enumerations for PKG metadata.
class DRMType(IntEnum): NONE = 0x0; PS4 = 0xF; PS5 = 0x10
class ContentType(IntEnum): UNKNOWN = 0x0; GAME_DATA = 0x4; GAME_EXEC = 0x5; PS1_EMU = 0x6; PSP = 0x7; THEME = 0x9; WIDGET = 0xA; LICENSE = 0xB; VSH_MODULE = 0xC; PSN_AVATAR = 0xD; PSPGO = 0xE; MINIS = 0xF; NEOGEO = 0x10; VMC = 0x11; PS2_CLASSIC = 0x12; PSP_REMASTERED = 0x14; PSP2GD = 0x15; PSP2AC = 0x16; PSP2LA = 0x17; PSM = 0x18; WT = 0x19; PSP2_THEME = 0x1F
class IROTag(Enum): SHAREFACTORY_THEME = 0x1; SYSTEM_THEME = 0x2

# Base class for handling PKG files, providing common functionalities.
class PackageBase:
    FLAG_ENCRYPTED = 0x80000000
    def __init__(self, file: str):
        if not os.path.isfile(file): raise FileNotFoundError(f"The PKG file '{file}' does not exist.")
        self.original_file = file; self.files = {}; self.content_id = None
        self.drm_type = None; self.content_type = None
    def _safe_decode(self, data):
        if isinstance(data, bytes): return data.decode('utf-8', errors='ignore').rstrip('\x00')
        return str(data).rstrip('\x00')
    def read_file(self, file_id):
        file_info = self.files.get(file_id)
        if not file_info: raise ValueError(f"File with ID {file_id} not found.")
        with open(self.original_file, 'rb') as f:
            f.seek(file_info['offset']); data = f.read(file_info['size'])
        return data

# Handles PS4-specific PKG file parsing.
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
            Logger.log_error(f"Error loading PS4 PKG file: {str(e)}"); raise
    def __load_files(self, fp):
        fp.seek(self.pkg_table_offset, os.SEEK_SET); entry_format = ">6IQ"
        for _ in range(self.pkg_entry_count):
            entry_data = fp.read(struct.calcsize(entry_format))
            file_id, _, _, _, offset, size, _ = struct.unpack(entry_format, entry_data)
            self.files[file_id] = {"id": file_id, "offset": offset, "size": size}

# Parses the PARAM.SFO file data to extract metadata, such as the title.
def parse_sfo(sfo_data):
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

# Loads the application configuration from configs.json, or creates a default one if it doesn't exist.
def load_or_create_config():
    if not os.path.exists(CONFIG_FILE_PATH):
        app.logger.warning(f"'{CONFIG_FILE_NAME}' not found. Creating a new one...")
        base_example_path = "C:\\Users\\YourUser\\Path\\To\\Your\\pkgs"
        default_config = {
            "shop_title": DEFAULT_SHOP_TITLE,
            "paths": { "games": os.path.join(base_example_path, "games") }
        }
        try:
            with open(CONFIG_FILE_PATH, 'w', encoding='utf-8') as f:
                json.dump(default_config, f, indent=4)
            app.logger.warning(f"Please edit '{CONFIG_FILE_NAME}' to configure your PKG paths.")
            return default_config
        except Exception as e:
            app.logger.error(f"Failed to create '{CONFIG_FILE_NAME}': {e}"); raise
    try:
        with open(CONFIG_FILE_PATH, 'r', encoding='utf-8') as f: config = json.load(f)
        if "paths" not in config or not isinstance(config["paths"], dict):
            raise ValueError(f"'paths' not defined or malformed in '{CONFIG_FILE_NAME}'.")
        app.logger.info(f"Config loaded successfully from '{CONFIG_FILE_NAME}'.")
        return config
    except Exception as e:
        app.logger.error(f"Fatal error reading '{CONFIG_FILE_NAME}': {e}"); raise

# Loads the PKG metadata cache from db.json.
def load_cache():
    if os.path.exists(DB_FILE_PATH):
        try:
            with open(DB_FILE_PATH, 'r', encoding='utf-8') as f: return json.load(f)
        except json.JSONDecodeError: return {}
    return {}

# Saves the given data to the db.json cache file.
def save_cache(cache_data):
    try:
        with open(DB_FILE_PATH, 'w', encoding='utf-8') as f: json.dump(cache_data, f, indent=4)
    except IOError as e: logging.error(f"Could not save cache: {e}")

# Formats a size in bytes into a human-readable string (MB or GB).
def format_file_size(size_bytes):
    if size_bytes == 0: return "0B"
    gb = size_bytes / (1024**3);
    if gb >= 1: return f"{gb:.2f} GB"
    mb = size_bytes / (1024**2);
    return f"{mb:.2f} MB"

# Scans a directory for PKG files, extracts metadata, and updates the cache.
# It checks file modification times to avoid reprocessing unchanged files.
def scan_and_cache_packages(pkg_folder_path, category_name, cache):
    app.logger.info(f"Scanning directory: [{category_name}] {pkg_folder_path}")
    if not os.path.isdir(pkg_folder_path):
        app.logger.warning(f"Path for '{category_name}' is not a directory, skipping: {pkg_folder_path}")
        return ([], set())
    os.makedirs(CACHE_FOLDER_PATH, exist_ok=True)
    pkg_files_on_disk = glob.glob(os.path.join(pkg_folder_path, "*.pkg"))
    found_files_set = set(pkg_files_on_disk)
    pkg_data_list = []
    for pkg_path in pkg_files_on_disk:
        filename = os.path.basename(pkg_path)
        try:
            mtime = os.path.getmtime(pkg_path)
            is_cached = pkg_path in cache
            is_mtime_match = is_cached and cache[pkg_path].get('mtime') == mtime
            is_cache_valid = is_cached and 'install_url' in cache[pkg_path]
            if is_mtime_match and is_cache_valid:
                cache[pkg_path]['category'] = category_name
                pkg_data_list.append(cache[pkg_path])
                continue
            
            app.logger.info(f"Processing file: {filename}")
            pkg = PackagePS4(pkg_path)
            title = None
            try:
                sfo_data = pkg.read_file(PARAM_SFO_ID)
                title = parse_sfo(sfo_data)
            except Exception: pass
            
            image_path_rel = None
            image_base_name = sanitize_filename(pkg.content_id or os.path.splitext(filename)[0])
            if image_base_name:
                try:
                    icon_data = pkg.read_file(ICON0_ID)
                    image_filename = f"{image_base_name}.png"
                    image_save_path_abs = os.path.join(CACHE_FOLDER_PATH, image_filename)
                    Image.open(io.BytesIO(icon_data)).save(image_save_path_abs, format="PNG")
                    image_path_rel = f"{CACHE_FOLDER_NAME}/{image_filename}"
                except Exception: pass
            
            file_size_bytes = os.path.getsize(pkg_path)
            pkg_data = {
                "filepath": pkg_path, "filename": filename, "title": title,
                "content_id": pkg.content_id, "file_size_bytes": file_size_bytes,
                "file_size_str": format_file_size(file_size_bytes),
                "image_path": image_path_rel, "mtime": mtime, "category": category_name,
                "install_url": f"/serve_pkg/{category_name}/{filename}"
            }
            cache[pkg_path] = pkg_data
            pkg_data_list.append(pkg_data)
        except Exception as e:
            app.logger.error(f"Failed to process {filename}: {e}")
    return (pkg_data_list, found_files_set)

# Removes entries from the cache that no longer correspond to files on disk.
def clean_orphaned_cache_entries(cache, all_found_files_on_disk):
    orphaned_keys = [key for key in cache if key not in all_found_files_on_disk]
    if orphaned_keys:
        app.logger.info(f"Cleaning {len(orphaned_keys)} orphaned entries from cache.")
        for key in orphaned_keys: del cache[key]
    return cache

# --- Flask App Initialization and Configuration Loading ---
try:
    APP_CONFIG = load_or_create_config()
except Exception as e:
    app.logger.error(f"FATAL ERROR ON INITIALIZATION: {e}")
    APP_CONFIG = {"paths": {}, "shop_title": "Error: Config Failed"}

# --- Flask Routes ---
@app.route('/')
def index():
    # Serves the main HTML page.
    return send_from_directory('static', 'index.html')

@app.route('/static/<path:path>')
def send_static_file(path):
    # Serves static files like CSS and JavaScript.
    return send_from_directory('static', path)

@app.route('/cached/<path:path>')
def send_cached_image(path):
    # Serves cached images (like game icons).
    return send_from_directory(CACHE_FOLDER_PATH, path)

@app.route('/api/settings')
def get_settings():
    # API endpoint to get basic settings, like the shop title.
    title = APP_CONFIG.get("shop_title", DEFAULT_SHOP_TITLE)
    return jsonify({"shop_title": title})

@app.route('/api/check_agent')
def check_agent():
    # API endpoint to check if the client is a PS5 based on its User-Agent.
    user_agent = request.headers.get('User-Agent', '')
    is_ps5 = "Playstation 5" in user_agent
    app.logger.info(f"User-Agent Check: '{user_agent}' -> is_ps5: {is_ps5}")
    return jsonify({"is_ps5": is_ps5})

@app.route('/api/scan', methods=['GET'])
def api_scan_packages():
    # API endpoint to trigger a scan of the PKG directories and return the package list.
    paths = APP_CONFIG.get("paths")
    if not paths:
        msg = "'paths' dictionary not configured. Please check 'configs.json'."
        return jsonify({"error": msg}), 500
    try:
        cache = load_cache()
        all_pkg_data = []
        all_found_files = set()
        for category, path in paths.items():
            normalized_path = os.path.abspath(path)
            scanned_data, found_files = scan_and_cache_packages(normalized_path, category, cache)
            all_pkg_data.extend(scanned_data)
            all_found_files.update(found_files)
        cache = clean_orphaned_cache_entries(cache, all_found_files)
        save_cache(cache)
        return jsonify(all_pkg_data)
    except Exception as e:
        app.logger.error(f"Error in /api/scan: {e}", exc_info=True)
        return jsonify({"error": f"Internal server error: {e}"}), 500

@app.route('/serve_pkg/<category>/<path:filename>')
def serve_pkg_file(category, filename):
    # Serves the actual PKG file for download/installation.
    paths = APP_CONFIG.get("paths")
    if not paths:
        return "Server not configured", 500
    directory_path = paths.get(category)
    if not directory_path or not os.path.isdir(directory_path):
        app.logger.error(f"Invalid category or path for /serve_pkg: {category}")
        return "Invalid category", 404
    app.logger.info(f"Serving file: {filename} from {directory_path}")
    try:
        return send_from_directory(directory_path, filename, as_attachment=True)
    except FileNotFoundError:
        app.logger.error(f"File not found: {filename} in {directory_path}")
        return "File not found", 404

# --- Main Execution Block ---
if __name__ == '__main__':
    print("==================================================")
    print(f"Starting {APP_CONFIG.get('shop_title', DEFAULT_SHOP_TITLE)}...")
    paths = APP_CONFIG.get("paths")
    if paths:
        print("Monitoring the following folders:")
        for category, path in paths.items():
            print(f"  - [{category}]: {path}")
    else:
        print("ERROR: No PKG folders configured. Please check 'configs.json'.")
    print("\nWARNING: This server exposes your .pkg files on the local network.")
    print("Access from other devices (like your PS5) using your computer's IP:")
    print("Example: http://YOUR_NETWORK_IP:5000")
    print("\nAccess locally: http://127.0.0.1:5000")
    print("==================================================")

    app.run(host='0.0.0.0', port=5000, debug=True)
