# 🎮 PS5 PKG Virtual Shop

![imagem](https://i.imgur.com/uy0G3pW.png)
**PS5 PKG Virtual Shop** is a simple yet powerful web-based interface for managing and installing your local PS4/PS5 `.pkg` files on a jailbroken PlayStation 5.  
The application runs on your computer and serves a clean, controller-friendly web UI directly to your PS5’s browser, allowing one-click installation via **etaHEN’s Direct Package Installer**.

#### ⚠️ Attention - At the moment the script only accepts the ps4 PKG format.

---

## 📘 About the Project

This project provides a **graphical user interface** to manage a local collection of `.pkg` files for a jailbroken PS5.  
Instead of typing URLs manually or using command-line tools, this server scans your local folders, extracts metadata (like titles and icons), and presents everything in a **categorized, console-friendly storefront**.

It is built with a **Python Flask backend** and a **lightweight vanilla JavaScript frontend**.

![imagem](https://i.imgur.com/wP8KSDp.png)

---

## ✨ Features

- **Automatic Scanning:** Detects and indexes all directories defined in your configuration file.
- **Rich Metadata:** Extracts title, content ID, and icon from `.pkg` files automatically.
- **Categorized Interface:** Organizes packages into tabs based on folder structure (e.g., `games`, `apps`, `dlc`).
- **PS5 Optimized:**
  - Restricts access to PS5 consoles only.
  - Controller navigation with L2/R2 for category switching.
- **Pagination System:** Adds “Next” and “Previous” buttons for browsing large collections.
- **Real-Time Search:** Instantly filter your collection by title.
- **One-Click Installation:** Click a game card to send it directly to the PS5’s download queue via **etaHEN DPI v2**.
- **Customizable:** Configure title, folder paths, and more through a simple `configs.json` file.
- **Lightweight:** Only requires **Python, Flask, and Pillow** — no heavy dependencies.

---

## 🧰 Prerequisites

Before you begin, make sure you have:

- A **jailbroken PlayStation 5 console**
- **etaHEN** running with **Direct Package Installer (DPI v2)** active
- **Python 3.x** installed on your computer
- Both your **PC and PS5 on the same local network**

---

## ⚙️ Installation & Setup

Follow these steps to get the server running on your computer.

### 1️⃣ Clone the repository

```bash
git clone https://github.com/MestreTM/ps5_pkg_virtual_shop.git
cd ps5_pkg_virtual_shop
```

### 2️⃣ Install Python dependencies

Using a virtual environment is recommended.

```bash
# Create and activate a virtual environment
python -m venv venv
source venv/bin/activate  # On Windows use: venv\Scripts\activate

# Install required libraries
pip install Flask Pillow
```

### 3️⃣ Configure your library

When you first run the server, it will create a `configs.json` file automatically.  
Alternatively, you can create it manually in the project root.

Example `configs.json`:

```json
{
    "shop_title": "My PS5 Library",
    "paths": {
        "games": "C:\\Users\\YourUser\\Documents\\PS5\\PKG\\Games",
        "apps": "/home/user/ps5/apps",
        "dlc": "D:\\PKG_Collection\\DLC",
        "updates": "/path/to/your/updates"
    }
}
```

- **shop_title:** The main title displayed in the web interface.  
- **paths:** A dictionary where each key represents a category (tab name) and each value is the folder path containing `.pkg` files.

### 4️⃣ Run the server

```bash
python app.py
```

The server will start and display which folders are being monitored.  
The first scan may take some time if you have a large collection — subsequent scans will be faster thanks to caching.

---

## 🕹️ Usage

### 🔧 Find your Computer’s IP Address

- **Windows:** Open Command Prompt and type `ipconfig`
- **macOS/Linux:** Open a terminal and type `ifconfig` or `ip -a`

Look for your **IPv4 Address**, which should look like `192.168.1.100`.

### 🌐 Open on your PS5

On your PS5, open the web browser and navigate to:

```
http://<YOUR_PC_IP>:5000
```

Replace `<YOUR_PC_IP>` with the IP address you found earlier.

### 🛒 Browse and Install

- Tabs will represent each category.  
- Use **L2/R2** to switch between tabs.  
- Use **Next/Previous** to navigate through pages.  
- Click any game or app card to **install directly** to your PS5 via **etaHEN**.

---

## 📁 File Structure

```
.
├── app.py              # Flask backend logic
├── configs.json        # User configuration (title and paths)
├── db.json             # Cached PKG metadata
├── static/
│   ├── script.js       # Frontend logic (rendering, search, navigation)
│   ├── style.css       # Web interface styling
│   ├── index.html      # Main HTML page
│   ├── l2.svg          # Controller icon (L2)
│   └── r2.svg          # Controller icon (R2)
└── cached/             # Extracted icons storage
```

---

## 🙏 Acknowledgments

- Thanks to the developers of **Flask** and **Pillow**.  
- Huge appreciation to the **PlayStation homebrew community** for their amazing work on exploits and tools like **etaHEN**.  
- Based on **mour0ne shop**.  

---

> 💡 *PS5 PKG Virtual Shop — your personal digital library, beautifully organized.*
