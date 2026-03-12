# 📡 Xstream EPG Channel Finder

A fast, local-first channel search tool for Xtream-based IPTV services. Fetches channel data from your provider's API and caches it in a local SQLite database for instant searching — no repeated API calls.

---

## 🚀 How It Works

| Run Type | Behaviour |
|---|---|
| **First Run** | Fetches ALL data from API → Stores in `xstream.db` |
| **Subsequent Runs** | Reads from SQLite only (no API calls) |
| **Every 7 Days** | Auto-refreshes data from API |
| **Force Refresh** | Type `y` when prompted at startup |

---

## 🔄 Flow Diagram

```
Start
  │
  ├─ DB empty or >7 days old? ──YES──► Fetch API ──► Store in SQLite
  │                                                        │
  └─ NO (use cached data) ◄───────────────────────────────┘
         │
         ▼
    Search SQLite locally (fast, no API calls)
```

---

## ⚡ API Calls Only Happen When:

1. First time ever (empty DB)
2. Every 7 days (stale data)
3. Manually forced by the user

---

## 🛠️ Setup

### 1. Clone the repo
```bash
git clone https://github.com/yourusername/xstream-epg-finder.git
cd xstream-epg-finder
```

### 2. Install dependencies
```bash
pip install requests fuzzywuzzy python-dotenv python-Levenshtein
```

### 3. Configure your `.env` file
```bash
cp .env.example .env
nano .env
```

Fill in your credentials:
```
SERVER=http://your-iptv-server.com
USERNAME=your_username
PASSWORD=your_password
REFRESH_DAYS=7
THREADS=100
```

### 4. Run
```bash
python3 main.py
```

---

## 📁 Project Structure

```
xstream-epg-finder/
├── main.py           # Entry point
├── .env              # Your credentials (gitignored)
├── .env.example      # Template for credentials
├── xstream.db        # Local SQLite cache (auto-generated)
└── README.md
```

---

## 🔒 Security

- Never commit your `.env` file — it contains your IPTV credentials
- `.env` and `*.db` are excluded via `.gitignore`

---

## 📦 Dependencies

| Library | Purpose |
|---|---|
| `requests` | API calls to IPTV server |
| `sqlite3` | Local database caching |
| `fuzzywuzzy` | Fuzzy channel name search |
| `python-dotenv` | Load credentials from `.env` |
| `concurrent.futures` | Multi-threaded fetching |
