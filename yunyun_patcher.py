"""
yunyun_patcher.py
=================
All-in-one translation patcher for Yunyun Syndrome.

Run order (all handled automatically):
  1. Patches the localization string bundle
  2. Patches data.unity3d language lines
  3. Checks Player.log for an existing CRC mismatch
     - If found: patches catalog.bin immediately
     - If not:   launches the game, watches the log in real time,
                 kills the game once the CRC line appears, patches catalog.bin

Dependencies: UnityPy  (pip install unitypy)
Place alongside your strings.json and lang_strings.json.
"""

# ── Inline: smartformattag_patch ──────────────────────────────────────────────
# Monkey-patch for UnityPy TypeTreeHelper to handle unknown ManagedReference
# types (e.g. SmartFormatTag) as raw byte passthroughs instead of crashing.

import UnityPy.helpers.TypeTreeHelper as _TTH

_orig_get_ref_type_node = _TTH.get_ref_type_node
_orig_read_value        = _TTH.read_value
_orig_write_value       = _TTH.write_value


def _patched_get_ref_type_node(ref_object, assetfile):
    try:
        return _orig_get_ref_type_node(ref_object, assetfile)
    except ValueError:
        return None


def _patched_read_value(node, reader, config):
    if node.m_Type != "ReferencedObject":
        return _orig_read_value(node, reader, config)
    value = {}
    for child in node.m_Children:
        if child.m_Type == "ReferencedObjectData":
            ref_type_nodes = _patched_get_ref_type_node(value, config.assetsfile)
            if ref_type_nodes is None:
                value["_raw_data"]  = None
                value["_raw_start"] = reader.Position
                continue
            value[child.m_Name] = _orig_read_value(ref_type_nodes, reader, config)
        else:
            value[child.m_Name] = _orig_read_value(child, reader, config)
    return value


def _patched_write_value(value, node, writer, config):
    if node.m_Type != "ReferencedObject":
        return _orig_write_value(value, node, writer, config)
    if isinstance(value, dict):
        for child in node.m_Children:
            if child.m_Type == "ReferencedObjectData":
                ref_type_nodes = _patched_get_ref_type_node(value, config.assetsfile)
                if ref_type_nodes is None:
                    continue
                _orig_write_value(value[child.m_Name], ref_type_nodes, writer, config)
            else:
                _orig_write_value(value[child.m_Name], child, writer, config)
    else:
        for child in node.m_Children:
            if child.m_Type == "ReferencedObjectData":
                ref_type_nodes = _patched_get_ref_type_node(value, config.assetsfile)
                if ref_type_nodes is None:
                    continue
                _orig_write_value(getattr(value, child.m_Name), ref_type_nodes, writer, config)
            else:
                _orig_write_value(getattr(value, child.m_Name), child, writer, config)


_TTH.get_ref_type_node = _patched_get_ref_type_node
_TTH.read_value        = _patched_read_value
_TTH.write_value       = _patched_write_value
print("[patch] TypeTreeHelper patched — unknown ReferencedObject types will be silently skipped.")

# ── Imports ───────────────────────────────────────────────────────────────────
import os
import UnityPy, json, struct, shutil, re, sys, time, subprocess, psutil
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox

# ── Config ────────────────────────────────────────────────────────────────────
CONFIG_FILE = Path(sys.executable).parent / "yunyun_config.json"

def load_config():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

def ask_game_dir():
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    messagebox.showinfo(
        "Yunyun Syndrome Patcher",
        "Please select your Yunyun Syndrome install folder.\n\n"
        "This is usually:\n"
        "C:\\Program Files (x86)\\Steam\\steamapps\\common\\Yunyun_Syndrome"
    )
    folder = filedialog.askdirectory(title="Select Yunyun Syndrome install folder")
    root.destroy()
    if not folder:
        print("No folder selected — exiting.")
        sys.exit(0)
    return Path(folder)

def get_game_dir():
    cfg = load_config()
    if "game_dir" in cfg:
        game_dir = Path(cfg["game_dir"])
        if game_dir.exists():
            return game_dir
        print(f"  Saved path no longer exists: {game_dir}")
    print("  Game install path not set — asking user...")
    game_dir = ask_game_dir()
    cfg["game_dir"] = str(game_dir)
    save_config(cfg)
    return game_dir

# ── Paths (initialized in main) ───────────────────────────────────────────────
GAME_DIR     = None
GAME_EXE     = None
BUNDLE       = None
CATALOG      = None
DATA         = None

LOG_PRIMARY   = Path(os.environ.get("APPDATA", "")).parent / "LocalLow/AllianceArts/Yunyun_Syndrome/Player.log"
LOG_SECONDARY = Path(os.environ.get("TEMP", "")) / "AllianceArts/Yunyun_Syndrome/Player.log"

STRINGS      = Path(r"C:\Users\jvnki\Documents\edits\strings.json")
LANG_STRINGS = Path(r"C:\Users\jvnki\Documents\edits\lang_strings.json")

BUNDLE_NAME   = "localization-string-tables-english(en)_assets_all.bundle"
GAME_EXE_NAME = "Yunyun_Syndrome.exe"

CRC_PATTERN   = re.compile(
    r"CRC Mismatch\. Provided ([0-9a-f]+), calculated ([0-9a-f]+) from data\.",
    re.IGNORECASE
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def backup(path: Path):
    bak = path.with_suffix(path.suffix + ".bak")
    if not bak.exists():
        shutil.copy2(path, bak)
        print(f"  Backed up → {bak.name}")


def find_log() -> Path:
    for p in (LOG_PRIMARY, LOG_SECONDARY):
        if p.exists():
            return p
    return LOG_PRIMARY   # return primary even if missing; we'll create-watch it


def scan_log_for_crc(log_path: Path):
    """Return (provided, calculated) ints if a CRC mismatch line exists, else None."""
    if not log_path.exists():
        return None
    text = log_path.read_text(encoding="utf-8", errors="ignore")
    matches = CRC_PATTERN.findall(text)
    if matches:
        p, c = matches[-1]
        return int(p, 16), int(c, 16)
    return None


def patch_catalog(calculated: int):
    catalog_data = bytearray(CATALOG.read_bytes())
    needle   = BUNDLE_NAME.encode()
    idx      = catalog_data.find(needle)
    if idx == -1:
        print("  ERROR: Bundle name not found in catalog.bin — cannot patch CRC.")
        return False
    name_end = idx + len(needle)
    slen     = struct.unpack_from('<I', catalog_data, name_end + 16)[0]
    post     = name_end + 20 + slen
    crc_off  = post + 8

    stored = struct.unpack_from('<I', catalog_data, crc_off)[0]
    if stored == calculated:
        print(f"  Catalog CRC already correct ({hex(calculated)}) — nothing to do.")
        return True

    backup(CATALOG)
    struct.pack_into('<I', catalog_data, crc_off, calculated)
    CATALOG.write_bytes(catalog_data)
    print(f"  Catalog patched at offset {crc_off}: {hex(stored)} → {hex(calculated)}")
    return True


def kill_game():
    killed = False
    for proc in psutil.process_iter(["name"]):
        try:
            if proc.info["name"] and proc.info["name"].lower() == GAME_EXE_NAME.lower():
                proc.kill()
                killed = True
                print(f"  Killed process: {proc.info['name']} (PID {proc.pid})")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    if not killed:
        print("  Game process not found — may have already closed.")


def launch_game() -> bool:
    """Try exe first, fall back to steam:// URL. Returns True if launched."""
    if GAME_EXE.exists():
        print(f"  Launching via exe: {GAME_EXE}")
        try:
            subprocess.Popen([str(GAME_EXE)])
            return True
        except Exception as e:
            print(f"  Exe launch failed: {e}")

    print(f"  Falling back to Steam URL: {STEAM_URL}")
    try:
        subprocess.Popen(["cmd", "/c", "start", "", STEAM_URL], shell=False)
        return True
    except Exception as e:
        print(f"  Steam launch failed: {e}")
        return False


def watch_log_for_crc(log_path: Path, timeout: int = 120):
    """
    Poll Player.log every 0.5s for a CRC mismatch line.
    Reads the entire file each time — handles fast crashes, log recreation, anything.
    Returns (provided, calculated) or None on timeout.
    """
    print(f"  Polling log every 0.5s for up to {timeout}s: {log_path}")
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            if log_path.exists():
                text = log_path.read_text(encoding="utf-8", errors="ignore")
                matches = CRC_PATTERN.findall(text)
                if matches:
                    p, c = matches[-1]
                    return int(p, 16), int(c, 16)
        except OSError:
            pass
        time.sleep(0.5)

    print("  Timed out — no CRC mismatch found in log.")
    return None



# ── Step 1: Patch localization string bundle ──────────────────────────────────

def step_bundle():
    print("\n=== Step 1: Patching localization string bundle ===")

    if not STRINGS.exists():
        print(f"  strings.json not found at {STRINGS} — skipping.")
        return False

    with open(STRINGS, encoding="utf-8") as f:
        tables = json.load(f)

    by_name = {}
    changed = 0
    for table in tables:
        diffs = sum(1 for e in table["entries"] if e["translated"] != e["original"])
        if diffs:
            by_name[table["name"]] = [e["translated"] for e in table["entries"]]
            changed += diffs

    print(f"  {changed} changed strings across {len(tables)} tables")
    if changed == 0:
        print("  No changes — skipping bundle patch.")
        return False

    print("  Loading bundle...")
    env = UnityPy.load(str(BUNDLE))
    patched_count = 0

    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        d = obj.read_typetree()
        if "m_TableData" not in d:
            continue
        table_name = d.get("m_Name", "")
        if table_name not in by_name:
            continue

        translated_list = by_name[table_name]
        sorted_entries  = sorted(d["m_TableData"], key=lambda e: e["m_Id"])
        n_bundle        = len(sorted_entries)
        n_json          = len(translated_list)

        if n_json != n_bundle:
            print(f"  {table_name}: json={n_json} bundle={n_bundle} — truncating to {n_bundle}")
            translated_list = translated_list[:n_bundle]

        for entry, translated in zip(sorted_entries, translated_list):
            entry["m_Localized"] = translated

        obj.save_typetree(d)
        patched_count += 1
        print(f"  Patched: {table_name} ({n_bundle} entries)")

    print(f"  Saving bundle ({patched_count} tables patched)...")
    backup(BUNDLE)
    patched_data = env.file.save()
    BUNDLE.write_bytes(patched_data)
    print(f"  Bundle saved ({len(patched_data)} bytes)")
    return True

# ── Step 2: Patch data.unity3d language lines ─────────────────────────────────

def pid_variants(pid):
    yield pid
    if pid < 0:
        yield pid + (1 << 64)
    elif pid >= (1 << 63):
        yield pid - (1 << 64)


def step_lang():
    print("\n=== Step 2: Patching data.unity3d language lines ===")

    if not LANG_STRINGS.exists():
        print(f"  lang_strings.json not found at {LANG_STRINGS} — skipping.")
        return

    with open(LANG_STRINGS, encoding="utf-8") as f:
        files = json.load(f)

    translations = {}
    for file in files:
        for v in pid_variants(file["path_id"]):
            translations[v] = file

    changed = sum(
        1 for file in files
        for line in file["lines"]
        if line["translated"] != line["original"]
    )
    print(f"  {changed} changed lines across {len(files)} files")
    if changed == 0:
        print("  No changes — skipping lang patch.")
        return

    print("  Loading data.unity3d (this may take a moment)...")
    env = UnityPy.load(str(DATA))

    for obj in env.objects:
        if obj.type.name != "TextAsset":
            continue
        pid = obj.path_id
        if pid not in translations:
            continue

        file = translations[pid]
        if not any(l["translated"] != l["original"] for l in file["lines"]):
            continue

        raw  = obj.read()
        text = raw.m_Script
        if isinstance(text, (bytes, bytearray)):
            text = text.decode("utf-8", errors="replace")

        text = re.sub(r'\\x[0-9a-fA-F]{2}', '?', text)

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as e:
            print(f"  JSON error in {file['name']}: {e}")
            print(f"  Context: {repr(text[e.pos-40:e.pos+40])}")
            continue

        new_lines = [l["translated"] for l in file["lines"]]
        for entry in parsed["List"]:
            if entry.get("Language", "").lower() == "en":
                entry["Lines"] = new_lines
                break

        raw.m_Script = json.dumps(parsed, ensure_ascii=False, indent=4)
        raw.save()
        print(f"  Patched: {file['name']}")

    print("  Saving data.unity3d...")
    backup(DATA)
    patched = env.file.save()
    DATA.write_bytes(patched)
    print(f"  Saved ({len(patched)} bytes)")

# ── Step 3: CRC patch ─────────────────────────────────────────────────────────

# The patched bundle always produces this CRC — hardcoded since it's deterministic.
CRC_CACHE_FILE = Path(sys.executable).parent / "yunyun_crc.dat"

def load_cached_crc():
    if CRC_CACHE_FILE.exists():
        try:
            return int(CRC_CACHE_FILE.read_text().strip(), 16)
        except Exception:
            pass
    return None

def save_cached_crc(crc: int):
    CRC_CACHE_FILE.write_text(hex(crc))

def step_crc():
    print("\n=== Step 3: CRC catalog patch ===")

    log_path = find_log()

    # Check if we have a cached CRC from a previous run
    cached = load_cached_crc()
    if cached:
        print(f"  Using cached CRC: {hex(cached)}")
        patch_catalog(cached)
        return

    # No cache — need to launch the game to get the CRC
    if log_path.exists():
        try:
            log_path.unlink()
        except OSError:
            pass

    print("  Launching game to capture CRC...")
    if not launch_game():
        print("  Could not launch game. Patch catalog.bin manually.")
        return

    print("  Waiting for CRC mismatch (up to 120 seconds)...")
    print("  The game will be closed automatically once the CRC is captured.")
    result = watch_log_for_crc(log_path, timeout=120)

    if result:
        provided, calculated = result
        print(f"\n  CRC captured — provided={hex(provided)} calculated={hex(calculated)}")
        print("  Closing game...")
        kill_game()
        time.sleep(2)
        patch_catalog(calculated)
        save_cached_crc(calculated)
        print(f"  CRC saved to cache.")
    else:
        print("\n  Failed to capture CRC automatically.")
        print("  Launch the game manually, let it reach the title screen,")
        print("  then run the patcher again.")

# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  Yunyun Syndrome Translation Patcher")
    print("=" * 60)

    try:
        import psutil
    except ImportError:
        print("\nERROR: psutil is required for process management.")
        print("Install it with:  pip install psutil")
        sys.exit(1)

    GAME_DIR = get_game_dir()
    GAME_EXE = GAME_DIR / "Yunyun_Syndrome.exe"
    BUNDLE   = GAME_DIR / "Yunyun_Syndrome_Data/StreamingAssets/aa/StandaloneWindows64/localization-string-tables-english(en)_assets_all.bundle"
    CATALOG  = GAME_DIR / "Yunyun_Syndrome_Data/StreamingAssets/aa/catalog.bin"
    DATA     = GAME_DIR / "Yunyun_Syndrome_Data/data.unity3d"
    print(f"  Game dir: {GAME_DIR}")

    step_bundle()
    step_lang()
    step_crc()

    print("\n" + "=" * 60)
    print("  All done. Launch the game.")
    print("=" * 60)