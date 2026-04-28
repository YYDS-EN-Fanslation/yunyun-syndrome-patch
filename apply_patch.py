"""
apply_patch.py
Reads strings.json, applies translated strings to the bundle,
saves patched bundle, then patches catalog.bin CRC from Player.log.

Run order:
  1. python dump_strings.py       — edit strings.json
  2. python apply_patch.py        — patch bundle + catalog
  3. Launch game                  — if CRC mismatch, run patch_crc.py
  4. python patch_crc.py          — patch catalog with correct CRC
  5. Launch game again            — should work
"""
import smartformattag_patch
import UnityPy, json, struct, shutil, re
from pathlib import Path

BASE    = Path(__file__).parent
GAME    = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Yunyun_Syndrome\Yunyun_Syndrome_Data")
BUNDLE  = GAME / r"StreamingAssets\aa\StandaloneWindows64\localization-string-tables-english(en)_assets_all.bundle"
CATALOG = GAME / r"StreamingAssets\aa\catalog.bin"
STRINGS = BASE / "strings.json"
LOG     = Path.home() / r"AppData\LocalLow\AllianceArts\Yunyun_Syndrome\Player.log"

# ── Load translation data ─────────────────────────────────────────────────────
with open(STRINGS, encoding="utf-8") as f:
    tables = json.load(f)

by_name = {}
changed = 0
for table in tables:
    diffs = sum(1 for e in table["entries"] if e["translated"] != e["original"])
    if diffs:
        by_name[table["name"]] = table["entries"]
        changed += diffs

print(f"Loaded {changed} changed strings across {len(tables)} tables")
if changed == 0:
    print("No changes detected. Edit 'translated' fields in strings.json first.")
    raise SystemExit

# ── Apply to bundle ───────────────────────────────────────────────────────────
print("Loading bundle...")
env = UnityPy.load(str(BUNDLE))

patched_count = 0
skipped_mismatch = 0
for obj in env.objects:
    if obj.type.name != "MonoBehaviour":
        continue
    d = obj.read_typetree()
    if "m_TableData" not in d:
        continue

    table_name = d.get("m_Name", "")
    if table_name not in by_name:
        continue

    entries         = by_name[table_name]
    sorted_entries  = sorted(d["m_TableData"], key=lambda e: e["m_Id"])
    n_bundle        = len(sorted_entries)
    n_json          = len(entries)

    if n_json != n_bundle:
        print(f"  SKIP {table_name}: entry count mismatch — json={n_json} bundle={n_bundle}")
        skipped_mismatch += 1
        continue

    for bundle_entry, json_entry in zip(sorted_entries, entries):
        bundle_entry["m_Localized"] = json_entry["translated"]

    obj.save_typetree(d)
    patched_count += 1
    print(f"  Patched: {table_name} ({n_bundle} entries)")

print(f"\nPatched {patched_count} tables.")
if skipped_mismatch:
    print(f"Skipped {skipped_mismatch} tables due to entry count mismatch — clean dump needed.")

# ── Save bundle ───────────────────────────────────────────────────────────────
print("Saving bundle...")
backup = BUNDLE.with_suffix(".bundle.bak")
if not backup.exists():
    shutil.copy2(BUNDLE, backup)
    print(f"Backed up original to {backup.name}")

patched_data = env.file.save()
BUNDLE.write_bytes(patched_data)
print(f"Bundle saved ({len(patched_data)} bytes)")

# ── Patch catalog CRC from log if available ───────────────────────────────────
print("\nChecking Player.log for CRC mismatch...")
try:
    log_text = LOG.read_text(encoding="utf-8", errors="ignore")
    pattern  = r"CRC Mismatch\. Provided ([0-9a-f]+), calculated ([0-9a-f]+) from data\."
    matches  = re.findall(pattern, log_text, re.IGNORECASE)
    if matches:
        provided_hex, calculated_hex = matches[-1]
        provided   = int(provided_hex,  16)
        calculated = int(calculated_hex, 16)
        print(f"  Found: provided={hex(provided)} calculated={hex(calculated)}")

        catalog_data = bytearray(CATALOG.read_bytes())
        needle   = b"localization-string-tables-english(en)_assets_all.bundle"
        idx      = catalog_data.find(needle)
        if idx == -1:
            raise ValueError("Bundle name not found in catalog.bin")
        name_end = idx + len(needle)
        slen     = struct.unpack_from('<I', catalog_data, name_end + 16)[0]
        post     = name_end + 20 + slen
        crc_off  = post + 8

        cat_backup = CATALOG.with_suffix(".bin.bak")
        if not cat_backup.exists():
            shutil.copy2(CATALOG, cat_backup)

        struct.pack_into('<I', catalog_data, crc_off, calculated)
        CATALOG.write_bytes(catalog_data)
        print(f"  Catalog patched at offset {crc_off}")
    else:
        print("  No CRC mismatch in log yet — launch game once, then run patch_crc.py")
except Exception as e:
    print(f"  Could not patch catalog: {e}")
    print("  Launch game once, then run patch_crc.py")

print("\nDone. Launch the game.")
print("If you get a CRC mismatch, run patch_crc.py then launch again.")