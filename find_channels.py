import requests
import sqlite3
import os
from fuzzywuzzy import fuzz
from base64 import b64decode
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

load_dotenv()

# --- CONFIG ---
SERVER = os.getenv("SERVER")
USERNAME = os.getenv("USERNAME")
PASSWORD = os.getenv("PASSWORD")
BASE = f"{SERVER}/player_api.php?username={USERNAME}&password={PASSWORD}"
DB_FILE = "xstream.db"
REFRESH_DAYS = int(os.getenv("REFRESH_DAYS", "7"))
THREADS = int(os.getenv("THREADS", "100"))  # adjust based on your connection speed

if not all([SERVER, USERNAME, PASSWORD]):
    raise ValueError("SERVER, USERNAME, and PASSWORD must be set in .env")

SGT = timezone(timedelta(hours=8))

# ============================================================
#  DATABASE SETUP
# ============================================================

def get_db():
    return sqlite3.connect(DB_FILE)

def setup_db():
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            stream_id      INTEGER PRIMARY KEY,
            name           TEXT,
            category_id    TEXT,
            epg_channel_id TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            category_id   TEXT PRIMARY KEY,
            category_name TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS epg (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            stream_id INTEGER,
            title     TEXT,
            start_ts  INTEGER,
            stop_ts   INTEGER,
            FOREIGN KEY (stream_id) REFERENCES channels(stream_id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()
    conn.close()

# ============================================================
#  REFRESH TRACKING
# ============================================================

def get_last_refresh():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT value FROM meta WHERE key='last_refresh'")
    row = c.fetchone()
    conn.close()
    return datetime.fromisoformat(row[0]) if row else None

def set_last_refresh():
    conn = get_db()
    c = conn.cursor()
    now_str = datetime.now(tz=SGT).isoformat()
    c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('last_refresh', ?)", (now_str,))
    conn.commit()
    conn.close()

def needs_refresh():
    last = get_last_refresh()
    if last is None:
        return True
    last = last.replace(tzinfo=SGT) if last.tzinfo is None else last
    return (datetime.now(tz=SGT) - last) > timedelta(days=REFRESH_DAYS)

# ============================================================
#  FETCH EPG (per channel, used in threads)
# ============================================================

def fetch_epg_for_channel(stream_id):
    """Fetch EPG for a single channel — runs in thread."""
    try:
        r = requests.get(
            f"{BASE}&action=get_short_epg&stream_id={stream_id}&limit=50",
            timeout=5
        )
        listings = r.json().get("epg_listings", [])
        results = []
        for item in listings:
            title    = b64decode(item.get("title", "")).decode("utf-8", errors="ignore")
            start_ts = item.get("start_timestamp") or item.get("start", 0)
            stop_ts  = item.get("stop_timestamp")  or item.get("stop", 0)
            try:
                start_ts = int(start_ts)
                stop_ts  = int(stop_ts)
            except:
                start_ts = 0
                stop_ts  = 0
            results.append((stream_id, title, start_ts, stop_ts))
        return results
    except Exception:
        return []

# ============================================================
#  FETCH & STORE
# ============================================================

def fetch_and_store():
    print("\n🔄 Fetching data from Xstream API...")

    conn = get_db()
    c = conn.cursor()

    # Clear old data
    c.execute("DELETE FROM channels")
    c.execute("DELETE FROM categories")
    c.execute("DELETE FROM epg")
    conn.commit()

    # 1. Fetch & store categories
    print("   📂 Fetching categories...")
    r = requests.get(f"{BASE}&action=get_live_categories", timeout=10)
    categories = r.json()
    c.executemany(
        "INSERT OR REPLACE INTO categories (category_id, category_name) VALUES (?, ?)",
        [(cat["category_id"], cat["category_name"]) for cat in categories]
    )
    conn.commit()
    print(f"   ✅ Stored {len(categories)} categories")

    # 2. Fetch & store channels
    print("   📺 Fetching channels...")
    r = requests.get(f"{BASE}&action=get_live_streams", timeout=30)
    channels = r.json()
    c.executemany(
        "INSERT OR REPLACE INTO channels (stream_id, name, category_id, epg_channel_id) VALUES (?, ?, ?, ?)",
        [(ch["stream_id"], ch["name"], ch["category_id"], ch.get("epg_channel_id", "")) for ch in channels]
    )
    conn.commit()
    print(f"   ✅ Stored {len(channels)} channels")

    # 3. Fetch EPG in parallel using threads
    print(f"   📋 Fetching EPG using {THREADS} threads...")
    total       = len(channels)
    completed   = 0
    all_epg     = []

    stream_ids = [ch["stream_id"] for ch in channels]

    with ThreadPoolExecutor(max_workers=THREADS) as executor:
        futures = {executor.submit(fetch_epg_for_channel, sid): sid for sid in stream_ids}
        for future in as_completed(futures):
            completed += 1
            print(f"\r   ⏳ EPG Progress: {completed}/{total} channels", end="", flush=True)
            result = future.result()
            if result:
                all_epg.extend(result)

            # Batch insert every 1000 records
            if len(all_epg) >= 1000:
                c.executemany(
                    "INSERT INTO epg (stream_id, title, start_ts, stop_ts) VALUES (?, ?, ?, ?)",
                    all_epg
                )
                conn.commit()
                all_epg = []

    # Insert remaining
    if all_epg:
        c.executemany(
            "INSERT INTO epg (stream_id, title, start_ts, stop_ts) VALUES (?, ?, ?, ?)",
            all_epg
        )
        conn.commit()

    conn.close()
    print(f"\n   ✅ EPG fetch complete")
    set_last_refresh()
    print("✅ Database refresh complete!\n")

# ============================================================
#  SEARCH
# ============================================================

def format_time(ts):
    if not ts or ts == 0:
        return "N/A"
    try:
        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(SGT)
        return dt.strftime("%d %b %Y, %I:%M %p SGT")
    except:
        return "N/A"

def search_programme(query, days=7, threshold=70, category_filter=None):
    print(f"\n🔍 Searching for: '{query}' (within {days} day(s))\n")

    now_ts    = int(datetime.now(tz=SGT).timestamp())
    cutoff_ts = int((datetime.now(tz=SGT) + timedelta(days=days)).timestamp())

    conn = get_db()
    c    = conn.cursor()

    # Build category filter
    cat_clause = ""
    cat_params = []
    if category_filter:
        c.execute(
            "SELECT category_id FROM categories WHERE LOWER(category_name) LIKE ?",
            (f"%{category_filter.lower()}%",)
        )
        cat_ids = [row[0] for row in c.fetchall()]
        if cat_ids:
            placeholders = ",".join("?" * len(cat_ids))
            cat_clause   = f"AND ch.category_id IN ({placeholders})"
            cat_params   = cat_ids

    # Search EPG titles
    query_sql = f"""
        SELECT e.title, ch.name, cat.category_name, e.start_ts, e.stop_ts
        FROM epg e
        JOIN channels ch  ON e.stream_id    = ch.stream_id
        JOIN categories cat ON ch.category_id = cat.category_id
        WHERE e.start_ts >= ? AND e.start_ts <= ?
        {cat_clause}
    """
    c.execute(query_sql, [now_ts, cutoff_ts] + cat_params)
    epg_rows = c.fetchall()

    # Search channel names
    ch_sql = f"""
        SELECT ch.name, cat.category_name
        FROM channels ch
        JOIN categories cat ON ch.category_id = cat.category_id
        WHERE 1=1 {cat_clause}
    """
    c.execute(ch_sql, cat_params)
    ch_rows = c.fetchall()
    conn.close()

    matches = []

    for (title, ch_name, cat_name, start_ts, stop_ts) in epg_rows:
        score = fuzz.partial_ratio(query.lower(), title.lower())
        if score >= threshold:
            matches.append({
                "score":     score,
                "programme": title,
                "channel":   ch_name,
                "category":  cat_name,
                "start":     format_time(start_ts),
                "end":       format_time(stop_ts),
                "start_ts":  start_ts,
                "source":    "📋 EPG"
            })

    for (ch_name, cat_name) in ch_rows:
        score = fuzz.partial_ratio(query.lower(), ch_name.lower())
        if score >= threshold:
            matches.append({
                "score":     score,
                "programme": ch_name,
                "channel":   ch_name,
                "category":  cat_name,
                "start":     "See channel name",
                "end":       "See channel name",
                "start_ts":  now_ts,
                "source":    "📺 Channel Name"
            })

    if not matches:
        print("❌ No matches found.")
        return

    # Deduplicate and sort
    seen   = set()
    unique = []
    for m in sorted(matches, key=lambda x: x["start_ts"]):
        key = (m["programme"], m["channel"])
        if key not in seen:
            seen.add(key)
            unique.append(m)

    print(f"✅ Found {len(unique)} match(es):\n")
    print("-" * 60)
    for m in unique:
        print(f"  [{m['score']}%] {m['programme']}")
        print(f"  📺 Channel  : {m['channel']}")
        print(f"  📂 Category : {m['category']}")
        print(f"  🕐 Start    : {m['start']}")
        print(f"  🕑 End      : {m['end']}")
        print(f"  🔎 Source   : {m['source']}")
        print("-" * 60)

# ============================================================
#  MAIN
# ============================================================

def main():
    print("=" * 60)
    print("       📡 Xstream EPG Channel Finder")
    print("=" * 60)

    setup_db()

    last = get_last_refresh()
    if needs_refresh():
        if last is None:
            print("\n⚠️  No data found. Fetching for the first time...")
        else:
            print(f"\n⚠️  Last refresh: {last.strftime('%d %b %Y %I:%M %p')} — data is stale, refreshing...")
        fetch_and_store()
    else:
        next_refresh = last + timedelta(days=REFRESH_DAYS)
        print(f"\n✅ DB is fresh | Last: {last.strftime('%d %b %Y %I:%M %p SGT')} | Next: {next_refresh.strftime('%d %b %Y %I:%M %p SGT')}")

    force = input("\n🔁 Force refresh data? (y / press Enter to skip): ").strip().lower()
    if force == "y":
        fetch_and_store()

    query           = input("Enter programme to search: ").strip()
    days_input      = input("Search within how many days? (Enter = 7): ").strip()
    threshold_input = input("Match sensitivity 1-100? (Enter = 70): ").strip()
    category_input  = input("Filter by category? (e.g. sport / Enter for all): ").strip()

    days       = int(days_input)      if days_input.isdigit()      else 7
    threshold  = int(threshold_input) if threshold_input.isdigit() else 70
    cat_filter = category_input if category_input else None

    search_programme(query, days=days, threshold=threshold, category_filter=cat_filter)

if __name__ == "__main__":
    main()