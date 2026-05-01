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
_BASE_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
CONFIG_FILE = _BASE_DIR / "yunyun_config.json"

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

STEAM_APP_ID = "2914150"

def find_game_in_steam():
    """Try to auto-detect the game install path via Steam registry and libraryfolders.vdf."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            steam_path = winreg.QueryValueEx(key, "SteamPath")[0]
    except Exception:
        return None

    library_paths = [steam_path]
    vdf = Path(steam_path) / "steamapps" / "libraryfolders.vdf"
    if vdf.exists():
        try:
            text = vdf.read_text(encoding="utf-8")
            for match in re.finditer(r'"path"\s+"([^"]+)"', text):
                library_paths.append(match.group(1).replace("\\\\", "\\"))
        except Exception:
            pass

    manifest_file = f"appmanifest_{STEAM_APP_ID}.acf"
    for lib in library_paths:
        manifest = Path(lib) / "steamapps" / manifest_file
        if manifest.exists():
            try:
                text = manifest.read_text(encoding="utf-8")
                m = re.search(r'"installdir"\s+"([^"]+)"', text)
                if m:
                    game_path = Path(lib) / "steamapps" / "common" / m.group(1)
                    if game_path.exists():
                        print(f"  Auto-detected game at: {game_path}")
                        return game_path
            except Exception:
                pass
    return None


def ask_game_dir():
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    messagebox.showinfo(
        "Yunyun Syndrome Patcher",
        "Could not auto-detect your Yunyun Syndrome install folder.\n\n"
        "Please select it manually.\n"
        "It is usually:\n"
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

    # Try auto-detection first
    game_dir = find_game_in_steam()
    if game_dir:
        cfg["game_dir"] = str(game_dir)
        save_config(cfg)
        return game_dir

    # Fall back to manual picker
    print("  Could not auto-detect game path — asking user...")
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

# JSON files are expected alongside the exe
_BASE_DIR    = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
STRINGS      = _BASE_DIR / "strings.json"
LANG_STRINGS = _BASE_DIR / "lang_strings.json"

def _find_csv() -> Path | None:
    """
    Find a CSV patch file alongside the exe.
    Picks the first *.csv found, preferring 50-yysrp.csv if present.
    Returns None if no CSV exists.
    """
    preferred = _BASE_DIR / "50-yysrp.csv"
    if preferred.exists():
        return preferred
    candidates = sorted(_BASE_DIR.glob("*.csv"))
    return candidates[0] if candidates else None


def _load_csv_patches(csv_path: Path, shared: dict[str, dict[str, int]]):
    """
    Parse a CSV in YunyunLocalePatcher format and resolve named keys to m_Id
    using already-loaded SharedTableData.

    Returns:
        bundle_by_name : { table_name: { m_Id: text } }
        lang_by_file   : { filename:   { en_index: text } }
    """
    import csv as _csv

    bundle_by_name: dict[str, dict[int, str]] = {}
    lang_by_file:   dict[str, dict[int, str]] = {}
    unresolved = 0

    with open(csv_path, encoding="utf-8", newline="") as fh:
        reader = _csv.DictReader(fh)
        for row in reader:
            table = row.get("TableName", "").strip()
            key   = row.get("Key",       "").strip()
            text  = row.get("Text",      "")
            if not table or not key:
                continue

            if table.endswith(".lang"):
                parts = key.split("/", 1)
                if len(parts) == 2 and parts[1].isdigit():
                    lang_by_file.setdefault(table, {})[int(parts[1])] = text
                else:
                    print(f"  CSV WARNING: bad .lang key '{key}' — skipping")
            else:
                # Resolve named key → m_Id via SharedTableData
                base = table.replace("_en", "").replace("_EN", "")
                shared_map = next(
                    (shared[c] for c in (table, base, base + "_en") if c in shared),
                    None
                )
                if shared_map and key in shared_map:
                    bundle_by_name.setdefault(table, {})[shared_map[key]] = text
                else:
                    unresolved += 1

    total = sum(len(v) for v in bundle_by_name.values())
    print(f"  CSV: {total} bundle entries across {len(bundle_by_name)} tables"
          + (f", {unresolved} unresolved" if unresolved else ""))
    if lang_by_file:
        print(f"  CSV: {sum(len(v) for v in lang_by_file.values())} .lang lines"
              f" across {len(lang_by_file)} files")
    return bundle_by_name, lang_by_file

BUNDLE_NAME   = "localization-string-tables-english(en)_assets_all.bundle"
GAME_EXE_NAME = "Yunyun_Syndrome.exe"
STEAM_URL     = f"steam://rungameid/{STEAM_APP_ID}"

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
    """Launch via Steam URL."""
    print(f"  Launching via Steam: {STEAM_URL}")
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

def step_bundle(dry_run: bool = False):
    """
    Patch the localization string bundle.

    Matching strategy: m_Id keyed (not positional).
    m_Id is a deterministic hash of the key string — stable across patches
    as long as entries aren't renamed. New entries added by the devs are
    simply skipped (no translation = no patch), so insertions never shift
    existing translations onto the wrong entries.

    Also ingests pending_edits.json from YunDebugMenu if present, merging
    in-game edits on top of strings.json translations.
    """
    print("\n[ Step 1/3 ] String bundle")
    print("-" * 40)

    # ── Build m_Id → translated map from strings.json ──
    by_name: dict[str, dict[int, str]] = {}  # table_name -> {m_Id: translated}
    changed = 0

    if STRINGS.exists():
        with open(STRINGS, encoding="utf-8") as f:
            tables = json.load(f)
        for table in tables:
            id_map = {}
            for e in table["entries"]:
                if e["translated"] != e["original"]:
                    id_map[int(e["m_Id"])] = e["translated"]
                    changed += 1
            if id_map:
                by_name[table["name"]] = id_map
        print(f"  strings.json   : {changed} changed entries across {len(by_name)} tables")
    else:
        print(f"  strings.json   : not found — skipping")

    # ── Merge CSV patch (overrides strings.json on conflict) ──
    # Override order: strings.json < CSV < pending_edits.json
    # Each layer overwrites the previous for any shared keys.
    csv_path = _find_csv()
    csv_lang_patches: dict[str, dict[int, str]] = {}
    if csv_path:
        print(f"  CSV patch      : {csv_path.name} (resolving keys...)")
        shared = _load_shared_table_data()
        csv_bundle, csv_lang_patches = _load_csv_patches(csv_path, shared)
        for table_name, id_map in csv_bundle.items():
            by_name.setdefault(table_name, {}).update(id_map)
    else:
        print(f"  CSV patch      : none found")
        csv_lang_patches = {}

    # ── Merge pending_edits.json from YunDebugMenu (highest priority) ──
    # Format: { "table::entry_key": { "table": str, "entry": str, "value": str } }
    # These are in-game edits exported from YunDebugMenu. They override both
    # strings.json and any CSV patch for any shared keys.
    pending_path = _BASE_DIR / "pending_edits.json"
    if pending_path.exists():
        print(f"  pending_edits  : found — merging in-game edits (highest priority)")
        try:
            with open(pending_path, encoding="utf-8") as f:
                pending = json.load(f)
            _merge_pending_edits(pending, by_name)
        except Exception as e:
            print(f"  pending_edits  : WARNING — could not load: {e}")
    else:
        print(f"  pending_edits  : none found")

    if not by_name:
        print("\n  Nothing to patch — no changes detected in any source.")
        return False, csv_lang_patches

    # ── Apply to bundle ──
    print(f"\n  Applying to bundle...")
    env = UnityPy.load(str(BUNDLE))
    patched_count = 0
    total_patched = 0

    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        d = obj.read_typetree()
        if "m_TableData" not in d:
            continue
        table_name = d.get("m_Name", "")
        if table_name not in by_name:
            continue

        id_map = by_name[table_name]
        patched = 0

        for entry in d["m_TableData"]:
            mid = int(entry["m_Id"])
            if mid in id_map:
                entry["m_Localized"] = id_map[mid]
                patched += 1

        obj.save_typetree(d)
        patched_count += 1
        total_patched += patched
        print(f"    {table_name}: {patched} strings")

    if dry_run:
        print(f"\n  [DRY RUN] Would patch {patched_count} tables, {total_patched} strings. Nothing written.")
    else:
        backup(BUNDLE)
        patched_data = env.file.save()
        BUNDLE.write_bytes(patched_data)
        print(f"\n  Saved {patched_count} tables, {total_patched} strings total.")
    return True, csv_lang_patches


def _load_shared_table_data() -> dict[str, dict[str, int]]:
    """
    Load SharedTableData from the shared assets bundle.
    Returns: { table_collection_name: { key_string: m_Id } }
    Used to resolve pending_edits.json key strings to m_Id values.
    """
    shared_bundle = BUNDLE.parent / "localization-assets-shared_assets_all.bundle"
    if not shared_bundle.exists():
        print(f"  WARNING: Shared bundle not found at {shared_bundle.name}")
        return {}

    result = {}
    try:
        env = UnityPy.load(str(shared_bundle))
        for obj in env.objects:
            if obj.type.name != "MonoBehaviour":
                continue
            d = obj.read_typetree()
            if "m_Entries" not in d:
                continue
            # SharedTableData — m_TableCollectionName links to the StringTable name
            collection = d.get("m_TableCollectionName", "")
            key_to_id = {}
            for e in d["m_Entries"]:
                key = e.get("m_Key", "")
                mid = int(e.get("m_Id", 0))
                if key and mid:
                    key_to_id[key] = mid
            if key_to_id:
                result[collection] = key_to_id
    except Exception as ex:
        print(f"  WARNING: Could not load SharedTableData: {ex}")

    return result


def _merge_pending_edits(pending: dict, by_name: dict[str, dict[int, str]]):
    """
    Merge pending_edits.json (from YunDebugMenu export) into the by_name map.
    pending format: { "table::entry": { "table": str, "entry": str, "value": str } }
    Requires SharedTableData to resolve entry key -> m_Id.
    """
    shared = _load_shared_table_data()
    if not shared:
        print("  WARNING: Cannot merge pending_edits — SharedTableData unavailable")
        return

    merged = 0
    missed = 0
    for composite_key, edit in pending.items():
        table = edit.get("table", "")
        entry = edit.get("entry", "")
        value = edit.get("value", "")
        if not all([table, entry, value]):
            continue

        # StringTable name in bundle is like "Text_en", shared collection is "Text"
        # Try both with and without the _en suffix
        candidates = [table, table + "_en", table.rstrip("_en")]
        mid = None
        for candidate in candidates:
            if candidate in shared and entry in shared[candidate]:
                mid = shared[candidate][entry]
                break

        if mid is None:
            print(f"  pending_edits: could not resolve {table}::{entry} to m_Id — skipping")
            missed += 1
            continue

        # Find the matching bundle table name
        bundle_table = None
        for name in by_name:
            base = name.replace("_en", "").replace("_EN", "")
            if base == table or name == table or name == table + "_en":
                bundle_table = name
                break
        if bundle_table is None:
            # Create new entry in by_name for this table
            bundle_table = table + "_en" if (table + "_en") in by_name else table
            by_name.setdefault(bundle_table, {})

        by_name[bundle_table][mid] = value
        merged += 1

    print(f"  pending_edits  : merged {merged} edits ({missed} unresolved)")

# ── Step 2: Patch data.unity3d language lines ─────────────────────────────────

def pid_variants(pid):
    yield pid
    if pid < 0:
        yield pid + (1 << 64)
    elif pid >= (1 << 63):
        yield pid - (1 << 64)


def step_lang(csv_lang_patches: dict | None = None, dry_run: bool = False):
    print("\n[ Step 2/3 ] Language files (.lang)")
    print("-" * 40)

    # Build translations map from lang_strings.json (path_id keyed)
    translations = {}  # path_id → file entry
    if LANG_STRINGS.exists():
        with open(LANG_STRINGS, encoding="utf-8") as f:
            files = json.load(f)
        for file in files:
            for v in pid_variants(file["path_id"]):
                translations[v] = file
        changed = sum(
            1 for file in files
            for line in file["lines"]
            if line["translated"] != line["original"]
        )
        print(f"  lang_strings.json : {changed} changed lines across {len(files)} files")
    else:
        print(f"  lang_strings.json : not found — skipping")

    # CSV lang patches are name-keyed (filename → {index: text})
    csv_lang_patches = csv_lang_patches or {}
    if csv_lang_patches:
        print(f"  CSV .lang         : {sum(len(v) for v in csv_lang_patches.values())} lines"
              f" across {len(csv_lang_patches)} files")

    if not translations and not csv_lang_patches:
        print("  Nothing to patch.")
        return

    print(f"\n  Loading data.unity3d (this may take a moment)...")
    env = UnityPy.load(str(DATA))

    for obj in env.objects:
        if obj.type.name != "TextAsset":
            continue

        raw  = obj.read()
        name = raw.m_Name
        pid  = obj.path_id

        in_json = pid in translations
        in_csv  = name in csv_lang_patches
        if not in_json and not in_csv:
            continue

        file = translations.get(pid)
        if file and not any(l["translated"] != l["original"] for l in file["lines"]) and not in_csv:
            continue

        text = raw.m_Script
        if isinstance(text, (bytes, bytearray)):
            text = text.decode("utf-8", errors="replace")

        stripped = text.lstrip()
        if not stripped:
            continue
        if not stripped.startswith('{'):
            continue

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as e:
            print(f"  JSON error in {name}: {e}")
            print(f"  Context: {repr(text[e.pos-40:e.pos+40])}")
            continue

        bundle_en = next(
            (e for e in parsed.get("List", []) if e.get("Language", "").lower() == "en"),
            None
        )
        if bundle_en is None:
            print(f"  No EN entry in {name} — skipping")
            continue

        bundle_lines = list(bundle_en.get("Lines", []))
        bundle_keys  = parsed.get("Keys", [])

        # Apply lang_strings.json translations first
        if file:
            lines     = file["lines"]
            has_keys  = all(l.get("key") for l in lines)
            if has_keys and bundle_keys:
                key_to_translated = {l["key"]: l["translated"] for l in lines if l.get("key")}
                for i, bkey in enumerate(bundle_keys):
                    if bkey in key_to_translated:
                        if i < len(bundle_lines):
                            bundle_lines[i] = key_to_translated[bkey]
            else:
                for i, l in enumerate(lines):
                    if i < len(bundle_lines) and l["translated"] != l["original"]:
                        bundle_lines[i] = l["translated"]

        # Apply CSV patches on top (CSV wins on conflict)
        if in_csv:
            for idx, translated in csv_lang_patches[name].items():
                if idx < len(bundle_lines):
                    bundle_lines[idx] = translated

        bundle_en["Lines"] = bundle_lines
        if not dry_run:
            raw.m_Script = json.dumps(parsed, ensure_ascii=False, indent=4)
            raw.save()
        print(f"    {name}")

    if dry_run:
        print(f"\n  [DRY RUN] Would patch .lang files above. Nothing written.")
    else:
        print(f"\n  Saving data.unity3d...")
        backup(DATA)
        patched = env.file.save(packer="lz4")
        DATA.write_bytes(patched)
        print(f"  Saved.")

# ── Step 3: CRC patch ─────────────────────────────────────────────────────────


def step_crc():
    print("\n[ Step 3/3 ] CRC patch")
    print("-" * 40)

    log_path = find_log()

    if log_path.exists():
        try:
            log_path.unlink()
        except OSError:
            pass

    print("  Launching game to capture CRC mismatch...")
    if not launch_game():
        print("  ERROR: Could not launch game. Patch catalog.bin manually.")
        return

    print("  Waiting up to 30 seconds — game will close automatically once captured.")
    result = watch_log_for_crc(log_path, timeout=30)

    if result:
        provided, calculated = result
        print(f"  CRC captured. Closing game in 3 seconds...")
        time.sleep(3)
        kill_game()
        patch_catalog(calculated)
    else:
        print("  CRC not detected in time.")
        print("  Launch the game manually to the title screen, then run the patcher again.")

# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Yunyun Syndrome Translation Patcher")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be patched without writing any files."
    )
    args = parser.parse_args()
    DRY_RUN = args.dry_run

    print("=" * 40)
    print("  Yunyun Syndrome Translation Patcher")
    if DRY_RUN:
        print("  *** DRY RUN — no files will be modified ***")
    print("=" * 40)

    try:
        import psutil
    except ImportError:
        print("\nERROR: psutil is not installed.")
        print("Run:  pip install psutil")
        sys.exit(1)

    GAME_DIR = get_game_dir()
    GAME_EXE = GAME_DIR / "Yunyun_Syndrome.exe"
    BUNDLE   = GAME_DIR / "Yunyun_Syndrome_Data/StreamingAssets/aa/StandaloneWindows64/localization-string-tables-english(en)_assets_all.bundle"
    CATALOG  = GAME_DIR / "Yunyun_Syndrome_Data/StreamingAssets/aa/catalog.bin"
    DATA     = GAME_DIR / "Yunyun_Syndrome_Data/data.unity3d"
    print(f"  Game : {GAME_DIR}")
    print()

    _, csv_lang_patches = step_bundle(dry_run=DRY_RUN)
    step_lang(csv_lang_patches, dry_run=DRY_RUN)
    if not DRY_RUN:
        step_crc()
    else:
        print("\n[ Step 3/3 ] CRC patch")
        print("-" * 40)
        print("  Skipped in dry-run mode.")

    print("\n" + "=" * 40)
    if DRY_RUN:
        print("  Dry run complete. No files were modified.")
    else:
        print("  Done! Launch the game.")
    print("=" * 40)