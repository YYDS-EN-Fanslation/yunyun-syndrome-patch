"""
yunyun_patcher.py
=================
All-in-one translation + image patcher for Yunyun Syndrome.

Run order (all handled automatically):
  1. Patches locale asset bundles with replacement images (image_patches.csv)
  2. Patches the localization string bundle
  3. Patches data.unity3d language lines
  4. Checks Player.log for existing CRC mismatches for all modified bundles
     - If found: patches catalog.bin immediately
     - If not:   launches the game, watches the log in real time,
                 kills the game once CRC lines appear, patches catalog.bin

Dependencies:
    pip install unitypy pillow psutil

File layout:
    yunyun_patcher.exe
    yunyun_config.json          (auto-generated)
    Patches/
        strings.json
        lang_strings.json
        50-yysrp.csv            (or any *.csv)
        pending_edits.json      (optional, from YunDebugMenu)
    ImagePatches/               (optional — omit if not patching images)
        image_patches.csv
        *.png

image_patches.csv format:
    sprite_name,image_file
    Logo_EN,logo_en.png
    YUNYUN_002_Main_000_010_1_EN,yunyun_main.png
    DemoTgsEnd_en,demo_end.png

    sprite_name : exact Texture2D asset name as it appears in the bundle
    image_file  : PNG filename inside ImagePatches/ (no path prefix)

NOTE: Images go in ImagePatches/ next to the exe, NOT in
      UserData/LocalePatches/ImagePatches/ (that was the old MelonLoader mod).

Atlas-backed sprites (MAAM_STAMP_*, Mama_EN) are not supported — skip them.
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
import UnityPy, json, struct, shutil, re, sys, time, subprocess
try:
    import psutil
except ImportError:
    print("\nERROR: psutil is not installed.  Run:  pip install psutil")
    sys.exit(1)
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

    game_dir = find_game_in_steam()
    if game_dir:
        cfg["game_dir"] = str(game_dir)
        save_config(cfg)
        return game_dir

    print("  Could not auto-detect game path — asking user...")
    game_dir = ask_game_dir()
    cfg["game_dir"] = str(game_dir)
    save_config(cfg)
    return game_dir

# ── Paths (initialized in main) ───────────────────────────────────────────────
GAME_DIR       = None
GAME_EXE       = None
BUNDLE         = None   # localization-string-tables-english(en)_assets_all.bundle
BUNDLE_SHARED  = None   # localization-assets-shared_assets_all.bundle
BUNDLE_EN      = None   # localization-assets-english(en)_assets_all.bundle
CATALOG        = None
DATA           = None

LOG_PRIMARY   = Path(os.environ.get("APPDATA", "")).parent / "LocalLow/AllianceArts/Yunyun_Syndrome/Player.log"
LOG_SECONDARY = Path(os.environ.get("TEMP", "")) / "AllianceArts/Yunyun_Syndrome/Player.log"

_BASE_DIR    = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
PATCHES_DIR  = _BASE_DIR / "Patches"
STRINGS      = PATCHES_DIR / "strings.json"
LANG_STRINGS = PATCHES_DIR / "lang_strings.json"
IMAGE_DIR    = _BASE_DIR / "ImagePatches"
IMAGE_CSV    = IMAGE_DIR / "image_patches.csv"

# Which bundles were actually modified this run — used by step_crc
_modified_bundles: list[Path] = []

def _find_csv() -> Path | None:
    # Check Patches/ first, then fall back to base dir
    for search_dir in (PATCHES_DIR, _BASE_DIR):
        preferred = search_dir / "50-yysrp.csv"
        if preferred.exists():
            return preferred
        candidates = [p for p in sorted(search_dir.glob("*.csv")) if p.name != "image_patches.csv"]
        if candidates:
            return candidates[0]
    return None


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
    return LOG_PRIMARY


def scan_log_for_crc(log_path: Path):
    """Return list of (provided, calculated) ints for all CRC mismatch lines in log."""
    if not log_path.exists():
        return []
    text = log_path.read_text(encoding="utf-8", errors="ignore")
    return [(int(p, 16), int(c, 16)) for p, c in CRC_PATTERN.findall(text)]


def _catalog_crc_offset(catalog_data: bytearray, bundle_name: str) -> int | None:
    """Return the byte offset of the CRC field for bundle_name in catalog_data, or None."""
    needle   = bundle_name.encode()
    idx      = catalog_data.find(needle)
    if idx == -1:
        return None
    name_end = idx + len(needle)
    slen     = struct.unpack_from('<I', catalog_data, name_end + 16)[0]
    post     = name_end + 20 + slen
    return post + 8


def patch_catalog_entry(catalog_data: bytearray, bundle_name: str, calculated: int) -> bool:
    """
    Patch the CRC entry for a single named bundle inside catalog.bin data.
    Mutates catalog_data in place. Returns True if patched, False if not found or already correct.
    """
    crc_off = _catalog_crc_offset(catalog_data, bundle_name)
    if crc_off is None:
        print(f"  ERROR: '{bundle_name}' not found in catalog.bin — cannot patch CRC.")
        return False

    stored = struct.unpack_from('<I', catalog_data, crc_off)[0]
    if stored == calculated:
        print(f"  {bundle_name}: CRC already correct ({hex(calculated)}) — skipping.")
        return False

    struct.pack_into('<I', catalog_data, crc_off, calculated)
    print(f"  {bundle_name}: patched at offset {crc_off}: {hex(stored)} → {hex(calculated)}")
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
    print(f"  Launching via Steam: {STEAM_URL}")
    try:
        subprocess.Popen(["cmd", "/c", "start", "", STEAM_URL], shell=False)
        return True
    except Exception as e:
        print(f"  Steam launch failed: {e}")
        return False


def watch_log_for_crc(log_path: Path, expected_count: int, timeout: int = 60, after_offset: int = 0):
    """
    Poll Player.log every 0.5s until we see at least `expected_count` CRC mismatch
    lines, or until timeout. Returns list of (provided, calculated) tuples, or empty on timeout.
    """
    print(f"  Polling log: {log_path} (offset={after_offset})")
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            if log_path.exists():
                raw = log_path.read_bytes()
                text = raw[after_offset:].decode("utf-8", errors="ignore")
                results = [(int(p, 16), int(c, 16)) for p, c in CRC_PATTERN.findall(text)]
                if results:
                    return results
            else:
                pass  # log not yet created
        except OSError:
            pass
        time.sleep(0.5)

    # Debug: show what we actually found in the log after offset
    try:
        if log_path.exists():
            raw = log_path.read_bytes()
            tail = raw[after_offset:].decode("utf-8", errors="ignore")
            crc_lines = [l for l in tail.splitlines() if "CRC" in l]
            if crc_lines:
                print(f"  DEBUG: CRC lines found after offset: {crc_lines}")
            else:
                print(f"  DEBUG: No CRC lines found after offset. Log size={len(raw)}, offset={after_offset}")
    except Exception as e:
        print(f"  DEBUG: Could not read log: {e}")

    print("  Timed out — not enough CRC mismatches found in log.")
    return []


# ── Step 1: Patch locale asset bundles with replacement images ────────────────

# Sprites that are atlas-backed and cannot be individually replaced.
# Attempting to swap their Texture2D would corrupt all sprites in the atlas.
_ATLAS_BACKED = {
    "sactx-0-1024x1024-DXT5|BC3-Mama_EN-bc15d7c2",  # backs all MAAM_STAMP_*_EN
}

# Map each patchable sprite name to the bundle it lives in.
# "shared" = localization-assets-shared_assets_all.bundle
# "en"     = localization-assets-english(en)_assets_all.bundle
_SPRITE_BUNDLE_MAP = {
    "Logo_EN":                       "shared",
    "YUNYUN_002_Main_000_010_1_EN":  "shared",
    "DemoTgsEnd_en":                 "en",
}

# Per-sprite texture rect overrides.
_SPRITE_TEXTURE_RECTS = {
    "Logo_EN": (0, 128, 1024, 769),
}




def _parse_image_csv() -> list[tuple[str, Path]]:
    """
    Parse image_patches.csv.
    Returns list of (sprite_name, png_path) for valid, existing entries.
    Format: sprite_name,image_file   (header required)
    Comment lines starting with # are ignored.
    Quoted fields supported for names containing commas.
    """
    if not IMAGE_CSV.exists():
        return []

    import csv as _csv
    import io
    entries = []

    raw = IMAGE_CSV.read_text(encoding="utf-8")
    # Strip comment lines so DictReader sees the header as the first line
    filtered = "\n".join(l for l in raw.splitlines() if not l.strip().startswith("#"))

    reader = _csv.DictReader(io.StringIO(filtered))
    if reader.fieldnames is None or "sprite_name" not in reader.fieldnames:
        print("  WARNING: image_patches.csv missing header (sprite_name,image_file) — skipping")
        return []

    for i, row in enumerate(reader, start=2):
        sprite = row.get("sprite_name", "").strip()
        fname  = row.get("image_file",  "").strip()
        if not sprite or not fname:
            continue

        png_path = IMAGE_DIR / fname
        if not png_path.exists():
            print(f"  WARNING: image_patches.csv line {i}: PNG not found: {png_path} — skipping")
            continue

        if sprite in _ATLAS_BACKED:
            print(f"  WARNING: '{sprite}' is atlas-backed and cannot be individually replaced — skipping")
            continue

        entries.append((sprite, png_path))

    return entries


def _patch_textures_in_bundle(bundle_path: Path, patches: dict[str, Path], dry_run: bool) -> bool:
    """
    For each sprite_name → png_path in patches, find the Texture2D asset in
    bundle_path by m_Name and replace its image data with the PNG.
    Uses Pillow directly to encode pixels, bypassing UnityPy's export chain
    (which pulls in astc_encoder, fmod, etc. that we don't need).
    Returns True if any textures were patched and the bundle was saved.
    """
    if not patches:
        return False

    from PIL import Image as PILImage
    import struct as _struct

    print(f"  Loading {bundle_path.name}...")
    env = UnityPy.load(str(bundle_path))

    patched = 0
    for obj in env.objects:
        if obj.type.name != "Texture2D":
            continue
        tex = obj.read()
        name = tex.m_Name
        if name not in patches:
            continue

        png_path = patches[name]
        print(f"    {name} ← {png_path.name} ({tex.m_Width}x{tex.m_Height})")

        if not dry_run:
            # Open PNG with Pillow, convert to RGBA
            # PNG must be sized to match the original texture dimensions exactly.
            # No automatic resizing is done — prepare your PNG at the correct size.
            img = PILImage.open(png_path).convert("RGBA")
            orig_w, orig_h = tex.m_Width, tex.m_Height
            if img.size != (orig_w, orig_h):
                print(f"      WARNING: {png_path.name} is {img.width}x{img.height}, expected {orig_w}x{orig_h} — resizing")
                img = img.resize((orig_w, orig_h), PILImage.LANCZOS)

            # Unity stores RGBA32 bottom-up, flip vertically
            img = img.transpose(PILImage.FLIP_TOP_BOTTOM)
            raw_pixels = img.tobytes()  # RGBA bytes, no compression

            # Patch the typetree directly — set format to RGBA32 (4) and
            # replace image data, bypassing all encoder imports entirely
            d = obj.read_typetree()
            d["m_TextureFormat"] = 4          # RGBA32
            d["m_Width"]         = orig_w
            d["m_Height"]        = orig_h
            d["m_MipCount"]      = 1
            d["m_MipMap"]        = False
            d["m_IsReadable"]    = True
            d["m_StreamData"]    = {"offset": 0, "size": 0, "path": ""}
            d["image data"]      = raw_pixels
            obj.save_typetree(d)

        patched += 1

    # For sprites in _SPRITE_TEXTURE_RECTS, replace the polygon mesh with a
    # simple quad covering the specified texture region.
    if not dry_run:
        import struct as _struct
        for obj in env.objects:
            if obj.type.name != "Sprite":
                continue
            try:
                d = obj.read_typetree()
            except Exception:
                continue
            name = d.get("m_Name", "")
            if name not in patches or name not in _SPRITE_TEXTURE_RECTS:
                continue

            tx, ty, tw2, th2 = _SPRITE_TEXTURE_RECTS[name]
            ppu = d.get("m_PixelsToUnits", 100.0)
            full_w = float(d.get("m_Rect", {}).get("width",  1024.0))
            full_h = float(d.get("m_Rect", {}).get("height", 1024.0))

            # UV coords (0..1) for the specified rect
            u0 = tx / full_w
            u1 = (tx + tw2) / full_w
            v0 = ty / full_h
            v1 = (ty + th2) / full_h

            # World-space half extents
            hw = (tw2 / 2.0) / ppu
            hh = (th2 / 2.0) / ppu
            # Pivot offset from center of full texture to center of rect
            px = (tx + tw2/2.0 - full_w/2.0) / ppu
            py = (ty + th2/2.0 - full_h/2.0) / ppu

            s0 = b""
            for vx, vy in [(-hw+px,-hh+py),(hw+px,-hh+py),(hw+px,hh+py),(-hw+px,hh+py)]:
                s0 += _struct.pack("<fff", vx, vy, 0.0)
            s1 = b""
            for u, v in [(u0,v0),(u1,v0),(u1,v1),(u0,v1)]:
                s1 += _struct.pack("<ff", u, v)
            idx = []
            for i in [0,1,2,0,2,3]:
                idx += [i, 0]

            rd = d["m_RD"]
            rd["m_SubMeshes"] = [{
                "firstByte": 0, "indexCount": 6, "topology": 0,
                "baseVertex": 0, "firstVertex": 0, "vertexCount": 4,
                "localAABB": {
                    "m_Center": {"x": px, "y": py, "z": 0.0},
                    "m_Extent": {"x": hw, "y": hh, "z": 0.0}
                }
            }]
            rd["m_IndexBuffer"] = idx
            rd["m_VertexData"]["m_VertexCount"] = 4
            rd["m_VertexData"]["m_DataSize"] = s0 + s1
            rd["textureRect"] = {"x": float(tx), "y": float(ty), "width": float(tw2), "height": float(th2)}
            rd["textureRectOffset"] = {"x": 0.0, "y": 0.0}
            d["m_RD"] = rd
            obj.save_typetree(d)
            print(f"    Mesh replaced for '{name}': rect=({tx},{ty},{tw2},{th2})")

    if patched == 0:
        print(f"  No matching Texture2D found in {bundle_path.name}.")
        return False

    if dry_run:
        print(f"  [DRY RUN] Would replace {patched} texture(s) in {bundle_path.name}. Nothing written.")
        return False

    print(f"  Saving {bundle_path.name} ({patched} texture(s) replaced)...")
    backup(bundle_path)
    bundle_path.write_bytes(env.file.save())
    print(f"  Saved.")
    return True


def step_images(dry_run: bool = False):
    print("\n[ Step 1/4 ] Image patches")
    print("-" * 40)

    if not IMAGE_DIR.exists():
        print(f"  ImagePatches/ folder not found — skipping image patches.")
        print(f"  (Create {IMAGE_DIR} with image_patches.csv inside to enable image patching)")
        return
    if not IMAGE_CSV.exists():
        print(f"  image_patches.csv not found in ImagePatches/ — skipping.")
        return

    entries = _parse_image_csv()
    if not entries:
        print("  No valid image patches found — skipping.")
        return

    print(f"  {len(entries)} image patch(es) loaded:")
    for sprite, png in entries:
        bundle_hint = _SPRITE_BUNDLE_MAP.get(sprite, "unknown bundle")
        print(f"    {sprite} ← {png.name}  [{bundle_hint}]")

    # Split patches by target bundle
    shared_patches: dict[str, Path] = {}
    en_patches:     dict[str, Path] = {}
    unknown = []

    for sprite, png in entries:
        target = _SPRITE_BUNDLE_MAP.get(sprite)
        if target == "shared":
            shared_patches[sprite] = png
        elif target == "en":
            en_patches[sprite] = png
        else:
            print(f"  WARNING: '{sprite}' has no known bundle mapping — skipping")
            unknown.append(sprite)

    print()

    if shared_patches:
        modified = _patch_textures_in_bundle(BUNDLE_SHARED, shared_patches, dry_run)
        if modified:
            _modified_bundles.append(BUNDLE_SHARED)

    if en_patches:
        modified = _patch_textures_in_bundle(BUNDLE_EN, en_patches, dry_run)
        if modified:
            _modified_bundles.append(BUNDLE_EN)


# ── Step 2: Patch localization string bundle ──────────────────────────────────

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
    print("\n[ Step 2/4 ] String bundle")
    print("-" * 40)

    by_name: dict[str, dict[int, str]] = {}
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

    pending_path = PATCHES_DIR / "pending_edits.json"
    if not pending_path.exists():
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
        if total_patched > 0:
            _modified_bundles.append(BUNDLE)
        print(f"\n  Saved {patched_count} tables, {total_patched} strings total.")
    return True, csv_lang_patches


def _load_shared_table_data() -> dict[str, dict[str, int]]:
    """
    Load SharedTableData from the shared assets bundle.
    Returns: { table_collection_name: { key_string: m_Id } }
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
    Merge pending_edits.json into the by_name map.
    pending format: { "table::entry_key": { "table": str, "entry": str, "value": str } }
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

        bundle_table = None
        for name in by_name:
            base = name.replace("_en", "").replace("_EN", "")
            if base == table or name == table or name == table + "_en":
                bundle_table = name
                break
        if bundle_table is None:
            bundle_table = table + "_en" if (table + "_en") in by_name else table
            by_name.setdefault(bundle_table, {})

        by_name[bundle_table][mid] = value
        merged += 1

    print(f"  pending_edits  : merged {merged} edits ({missed} unresolved)")

# ── Step 3: Patch data.unity3d language lines ─────────────────────────────────

def pid_variants(pid):
    yield pid
    if pid < 0:
        yield pid + (1 << 64)
    elif pid >= (1 << 63):
        yield pid - (1 << 64)


def step_lang(csv_lang_patches: dict | None = None, dry_run: bool = False):
    print("\n[ Step 3/4 ] Language files (.lang)")
    print("-" * 40)

    translations = {}
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

# ── Step 4: CRC patch ─────────────────────────────────────────────────────────

def step_crc():
    print("\n[ Step 4/4 ] CRC patch")
    print("-" * 40)

    if not _modified_bundles:
        print("  No bundles were modified — skipping CRC patch.")
        return

    modified_names = [b.name for b in _modified_bundles]
    print(f"  Bundles to patch CRC for: {', '.join(modified_names)}")

    log_path = find_log()
    if log_path.exists():
        try:
            log_path.unlink()
        except OSError:
            pass

    print("  Launching game to capture CRC mismatches...")
    if not launch_game():
        print("  ERROR: Could not launch game. Patch catalog.bin manually.")
        return

    expected = len(_modified_bundles)
    print(f"  Waiting up to 15 seconds — expecting {expected} CRC mismatch(es).")
    results = watch_log_for_crc(log_path, expected_count=expected, timeout=15)

    if not results:
        print("  No CRC mismatches captured.")
        print("  Launch the game manually to the title screen, then run the patcher again.")
        return

    print(f"  {len(results)} CRC mismatch(es) captured. Closing game in 3 seconds...")
    time.sleep(3)
    kill_game()

    # Each CRC mismatch line: provided = what catalog.bin currently says for that bundle,
    # calculated = what the bundle actually hashes to.
    # Match each log line to the right bundle by comparing provided against the stored
    # catalog CRC for each modified bundle — never cross-apply CRCs between bundles.
    catalog_data = bytearray(CATALOG.read_bytes())
    any_patched  = False

    for bundle_path in _modified_bundles:
        crc_off = _catalog_crc_offset(catalog_data, bundle_path.name)
        if crc_off is None:
            print(f"  ERROR: {bundle_path.name} not found in catalog.bin")
            continue

        stored = struct.unpack_from('<I', catalog_data, crc_off)[0]

        # Find the log entry where provided == stored (this line belongs to this bundle)
        match = next(
            ((p, c) for p, c in results if p == stored),
            None
        )
        if match is None:
            print(f"  {bundle_path.name}: no matching CRC log entry (stored={hex(stored)}) — skipping")
            continue

        _, calculated = match
        if stored == calculated:
            print(f"  {bundle_path.name}: CRC already correct ({hex(calculated)}) — skipping.")
            continue

        struct.pack_into('<I', catalog_data, crc_off, calculated)
        print(f"  {bundle_path.name}: patched at offset {crc_off}: {hex(stored)} → {hex(calculated)}")
        any_patched = True

    if any_patched:
        backup(CATALOG)
        CATALOG.write_bytes(catalog_data)
        print(f"  catalog.bin saved.")
    else:
        print("  No catalog entries needed updating.")

# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Yunyun Syndrome Translation + Image Patcher")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be patched without writing any files."
    )
    args = parser.parse_args()
    DRY_RUN = args.dry_run

    print("=" * 40)
    print("  Yunyun Syndrome Patcher")
    if DRY_RUN:
        print("  *** DRY RUN — no files will be modified ***")
    print("=" * 40)

    GAME_DIR      = get_game_dir()
    GAME_EXE      = GAME_DIR / "Yunyun_Syndrome.exe"
    _aa           = GAME_DIR / "Yunyun_Syndrome_Data/StreamingAssets/aa/StandaloneWindows64"
    BUNDLE        = _aa / "localization-string-tables-english(en)_assets_all.bundle"
    BUNDLE_SHARED = _aa / "localization-assets-shared_assets_all.bundle"
    BUNDLE_EN     = _aa / "localization-assets-english(en)_assets_all.bundle"
    CATALOG       = GAME_DIR / "Yunyun_Syndrome_Data/StreamingAssets/aa/catalog.bin"
    DATA          = GAME_DIR / "Yunyun_Syndrome_Data/data.unity3d"

    print(f"  Game : {GAME_DIR}")
    print()

    step_images(dry_run=DRY_RUN)
    _, csv_lang_patches = step_bundle(dry_run=DRY_RUN)
    step_lang(csv_lang_patches, dry_run=DRY_RUN)

    if not DRY_RUN:
        step_crc()
    else:
        print("\n[ Step 4/4 ] CRC patch")
        print("-" * 40)
        print("  Skipped in dry-run mode.")

    print("\n" + "=" * 40)
    if DRY_RUN:
        print("  Dry run complete. No files were modified.")
    else:
        print("  Done! Launch the game.")
    print("=" * 40)