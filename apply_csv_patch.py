"""
apply_csv_patch.py
==================
Reads a CSV patch file in YunyunLocalePatcher format and applies it to
the localization bundle and data.unity3d — the same targets as yunyun_patcher.py.

CSV format (RFC 4180, header required):
    TableName,Key,Text
    Text_en,UITitle/Load,TEST TEST TEST
    YUNYUN_001_Main_000_000.lang,en/0,"【???/Yunyun】Hello hello"

Key formats:
    Bundle tables  (TableName like "Text_en"):
        Key is the named string key (e.g. UITitle/Load).
        Resolved to m_Id via SharedTableData.
    .lang files    (TableName ends with ".lang"):
        Key is "language/index" (e.g. en/0).
        Only "en" language and integer index are used.

Run:
    python apply_csv_patch.py [path/to/patch.csv]

If no path is given, looks for 50-yysrp.csv alongside this script.
Game path is resolved the same way as yunyun_patcher.py (registry / saved config).
"""

# ── Inline: smartformattag_patch (same as yunyun_patcher.py) ─────────────────
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

# ── Imports ───────────────────────────────────────────────────────────────────
import csv
import json
import re
import shutil
import struct
import sys
from pathlib import Path

import UnityPy

# ── Config / paths (mirrors yunyun_patcher.py) ────────────────────────────────
_BASE_DIR    = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
CONFIG_FILE  = _BASE_DIR / "yunyun_config.json"
BUNDLE_NAME  = "localization-string-tables-english(en)_assets_all.bundle"
STEAM_APP_ID = "2914150"


def load_config():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def find_game_in_steam():
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
            for m in re.finditer(r'"path"\s+"([^"]+)"', text):
                library_paths.append(m.group(1).replace("\\\\", "\\"))
        except Exception:
            pass

    manifest = f"appmanifest_{STEAM_APP_ID}.acf"
    for lib in library_paths:
        p = Path(lib) / "steamapps" / manifest
        if p.exists():
            try:
                text = p.read_text(encoding="utf-8")
                m = re.search(r'"installdir"\s+"([^"]+)"', text)
                if m:
                    gp = Path(lib) / "steamapps" / "common" / m.group(1)
                    if gp.exists():
                        return gp
            except Exception:
                pass
    return None


def get_game_dir() -> Path:
    cfg = load_config()
    if "game_dir" in cfg:
        gd = Path(cfg["game_dir"])
        if gd.exists():
            return gd
    gd = find_game_in_steam()
    if gd:
        return gd
    print("ERROR: Cannot find game directory. Run yunyun_patcher.py once to set it up.")
    sys.exit(1)


def backup(path: Path):
    bak = path.with_suffix(path.suffix + ".bak")
    if not bak.exists():
        shutil.copy2(path, bak)
        print(f"  Backed up → {bak.name}")


# ── CSV loader ────────────────────────────────────────────────────────────────

def load_csv(csv_path: Path):
    """
    Returns:
        bundle_patches: { table_name: { named_key: text } }
        lang_patches:   { filename:   { en_index: text } }
    """
    bundle_patches: dict[str, dict[str, str]] = {}
    lang_patches:   dict[str, dict[int, str]] = {}
    skipped = 0

    with open(csv_path, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None or "TableName" not in reader.fieldnames:
            print("ERROR: CSV missing header row (TableName,Key,Text)")
            sys.exit(1)

        for row in reader:
            table = row.get("TableName", "").strip()
            key   = row.get("Key",       "").strip()
            text  = row.get("Text",      "")

            if not table or not key:
                skipped += 1
                continue

            if table.endswith(".lang"):
                # Key format: "en/0"  →  language part ignored, index used
                parts = key.split("/", 1)
                if len(parts) != 2 or not parts[1].isdigit():
                    print(f"  WARNING: Bad .lang key '{key}' in row — skipping")
                    skipped += 1
                    continue
                idx = int(parts[1])
                lang_patches.setdefault(table, {})[idx] = text
            else:
                # Bundle string table — key is the named string key
                bundle_patches.setdefault(table, {})[key] = text

    return bundle_patches, lang_patches


# ── Bundle patch ──────────────────────────────────────────────────────────────

def load_shared_table_data(bundle_dir: Path) -> dict[str, dict[str, int]]:
    """
    Returns { collection_name: { named_key: m_Id } }
    Needed to resolve named keys from the CSV to numeric m_Id values.
    """
    shared = bundle_dir / "localization-assets-shared_assets_all.bundle"
    if not shared.exists():
        print(f"  WARNING: Shared bundle not found at {shared.name} — bundle key resolution unavailable")
        return {}

    result: dict[str, dict[str, int]] = {}
    env = UnityPy.load(str(shared))
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        d = obj.read_typetree()
        if "m_Entries" not in d:
            continue
        collection = d.get("m_TableCollectionName", "")
        key_to_id = {}
        for e in d["m_Entries"]:
            k   = e.get("m_Key", "")
            mid = int(e.get("m_Id", 0))
            if k and mid:
                key_to_id[k] = mid
        if key_to_id:
            result[collection] = key_to_id
            print(f"  SharedTableData: {collection} ({len(key_to_id)} keys)")
    return result


def apply_bundle_patches(bundle_path: Path, bundle_patches: dict[str, dict[str, str]]):
    if not bundle_patches:
        print("  No bundle patches — skipping.")
        return

    bundle_dir = bundle_path.parent
    print("  Loading SharedTableData to resolve named keys → m_Id...")
    shared = load_shared_table_data(bundle_dir)

    # Build { table_name: { m_Id: text } } by resolving named keys
    by_name: dict[str, dict[int, str]] = {}
    unresolved = 0

    for table_name, key_map in bundle_patches.items():
        id_map: dict[int, str] = {}

        # SharedTableData collection name strips the _en suffix
        base = table_name.replace("_en", "").replace("_EN", "")
        candidates = [table_name, base, base + "_en"]
        shared_map = next((shared[c] for c in candidates if c in shared), None)

        for named_key, text in key_map.items():
            if shared_map and named_key in shared_map:
                id_map[shared_map[named_key]] = text
            else:
                print(f"  WARNING: Cannot resolve '{named_key}' in '{table_name}' to m_Id — skipping")
                unresolved += 1

        if id_map:
            by_name[table_name] = id_map

    if not by_name:
        print("  No resolvable bundle entries — skipping bundle patch.")
        return

    print(f"  Loading bundle...")
    env = UnityPy.load(str(bundle_path))
    patched_tables = 0
    patched_entries = 0

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
        count  = 0
        for entry in d["m_TableData"]:
            mid = int(entry["m_Id"])
            if mid in id_map:
                entry["m_Localized"] = id_map[mid]
                count += 1

        obj.save_typetree(d)
        patched_tables  += 1
        patched_entries += count
        print(f"  Patched: {table_name} ({count} entries)")

    print(f"  Saving bundle ({patched_tables} tables, {patched_entries} entries)...")
    backup(bundle_path)
    bundle_path.write_bytes(env.file.save())
    print(f"  Bundle saved.")

    if unresolved:
        print(f"  {unresolved} entries could not be resolved via SharedTableData.")


# ── .lang patch ───────────────────────────────────────────────────────────────

def apply_lang_patches(data_path: Path, lang_patches: dict[str, dict[int, str]]):
    if not lang_patches:
        print("  No .lang patches — skipping.")
        return

    print("  Loading data.unity3d (this may take a moment)...")
    env = UnityPy.load(str(data_path))
    patched_files = 0

    for obj in env.objects:
        if obj.type.name != "TextAsset":
            continue
        raw = obj.read()
        name = raw.m_Name
        if not name.endswith(".lang"):
            continue
        if name not in lang_patches:
            continue

        index_map = lang_patches[name]  # { en_index: text }

        text = raw.m_Script
        if isinstance(text, (bytes, bytearray)):
            text = text.decode("utf-8", errors="replace")

        if not text.lstrip().startswith("{"):
            print(f"  Skipping {name} (not JSON format)")
            continue

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as e:
            print(f"  JSON error in {name}: {e}")
            continue

        bundle_en = next(
            (e for e in parsed.get("List", []) if e.get("Language", "").lower() == "en"),
            None
        )
        if bundle_en is None:
            print(f"  No EN entry in {name} — skipping")
            continue

        lines = bundle_en.get("Lines", [])
        count = 0
        for idx, translated in index_map.items():
            if idx < len(lines):
                lines[idx] = translated
                count += 1
            else:
                print(f"  WARNING: {name} index {idx} out of range (len={len(lines)}) — skipping")

        bundle_en["Lines"] = lines
        raw.m_Script = json.dumps(parsed, ensure_ascii=False, indent=4)
        raw.save()
        patched_files += 1
        print(f"  Patched: {name} ({count} lines)")

    print(f"  Saving data.unity3d ({patched_files} files patched)...")
    backup(data_path)
    for pf in env.files.values():
        data_path.write_bytes(pf.save(packer="lz4"))
    print(f"  Saved.")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  Yunyun Syndrome CSV Patch Importer")
    print("=" * 60)

    csv_path = Path(sys.argv[1]) if len(sys.argv) > 1 else _BASE_DIR / "50-yysrp.csv"
    if not csv_path.exists():
        print(f"ERROR: CSV not found at {csv_path}")
        print("Usage: python apply_csv_patch.py [path/to/patch.csv]")
        sys.exit(1)

    print(f"\nCSV: {csv_path}")
    bundle_patches, lang_patches = load_csv(csv_path)

    total_bundle = sum(len(v) for v in bundle_patches.values())
    total_lang   = sum(len(v) for v in lang_patches.values())
    print(f"Loaded: {total_bundle} bundle entries across {len(bundle_patches)} tables")
    print(f"        {total_lang} .lang lines across {len(lang_patches)} files")

    if not bundle_patches and not lang_patches:
        print("Nothing to patch.")
        sys.exit(0)

    GAME_DIR    = get_game_dir()
    BUNDLE_PATH = GAME_DIR / "Yunyun_Syndrome_Data/StreamingAssets/aa/StandaloneWindows64" / BUNDLE_NAME
    DATA_PATH   = GAME_DIR / "Yunyun_Syndrome_Data/data.unity3d"
    print(f"Game dir: {GAME_DIR}\n")

    if bundle_patches:
        print("=== Bundle patch ===")
        apply_bundle_patches(BUNDLE_PATH, bundle_patches)

    if lang_patches:
        print("\n=== .lang patch ===")
        apply_lang_patches(DATA_PATH, lang_patches)

    print("\n" + "=" * 60)
    print("  Done. Run yunyun_patcher.py to also fix the CRC if needed.")
    print("=" * 60)
