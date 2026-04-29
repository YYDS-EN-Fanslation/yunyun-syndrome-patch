"""
apply_lang_patch.py
Reads lang_strings.json, applies translated lines back into data.unity3d.
"""
import smartformattag_patch
import UnityPy, json, shutil
from pathlib import Path

BASE    = Path(__file__).parent
GAME    = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Yunyun_Syndrome\Yunyun_Syndrome_Data")
DATA    = GAME / "data.unity3d"
STRINGS = BASE / "lang_strings.json"

# ── Load translations ─────────────────────────────────────────────────────────
with open(STRINGS, encoding="utf-8") as fh:
    files = json.load(fh)

translations = {entry["name"]: entry for entry in files}

changed = sum(
    1 for entry in files
    for line in entry["lines"]
    if line["translated"] != line["original"]
)
print(f"Loaded {changed} changed lines across {len(files)} files")
if changed == 0:
    print("No changes detected. Edit 'translated' fields in lang_strings.json first.")
    raise SystemExit

# ── Apply to bundle ───────────────────────────────────────────────────────────
print("Loading data.unity3d (this may take a moment)...")
env = UnityPy.load(str(DATA))

patched_count = 0
skipped_mismatch = 0
for obj in env.objects:
    if obj.type.name != "TextAsset":
        continue

    raw = obj.read()
    name = raw.m_Name

    if not name.endswith(".lang"):
        continue
    if name not in translations:
        continue

    entry = translations[name]
    if not any(l["translated"] != l["original"] for l in entry["lines"]):
        continue

    text = raw.m_Script
    if isinstance(text, (bytes, bytearray)):
        text = text.decode("utf-8", errors="replace")

    stripped = text.lstrip()
    if not stripped:
        print(f"  Skipping {name} (empty)")
        continue
    if not stripped.startswith("{"):
        print(f"  Skipping {name} (not JSON format)")
        continue

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"  JSON error in {name}: {e}")
        print(f"  Context: {repr(text[max(0, e.pos-40):e.pos+40])}")
        continue

    lines = entry["lines"]
    has_keys = all(l.get("key") for l in lines)
    bundle_keys = parsed.get("Keys", [])

    bundle_en = next(
        (e for e in parsed.get("List", []) if e.get("Language", "").lower() == "en"),
        None
    )
    if bundle_en is None:
        print(f"  No EN entry found in {name}, skipping")
        continue

    bundle_lines = bundle_en.get("Lines", [])

    if has_keys and bundle_keys:
        key_to_translated = {l["key"]: l["translated"] for l in lines if l.get("key")}
        new_lines = []
        for i, bkey in enumerate(bundle_keys):
            if bkey in key_to_translated:
                new_lines.append(key_to_translated[bkey])
            else:
                new_lines.append(bundle_lines[i] if i < len(bundle_lines) else "")
    else:
        new_lines = [l["translated"] for l in lines]
        if len(new_lines) != len(bundle_lines):
            print(f"  SKIP {name}: line count mismatch — json={len(new_lines)} bundle={len(bundle_lines)}")
            skipped_mismatch += 1
            continue

    patched = False
    for lang_entry in parsed.get("List", []):
        if lang_entry.get("Language", "").lower() == "en":
            lang_entry["Lines"] = new_lines
            patched = True
            break

    if not patched:
        print(f"  No EN entry found in {name}, skipping")
        continue

    raw.m_Script = json.dumps(parsed, ensure_ascii=False, indent=4)
    raw.save()
    patched_count += 1
    print(f"  Patched: {name} ({len(new_lines)} lines)")

print(f"\nPatched {patched_count} files.")
if skipped_mismatch:
    print(f"Skipped {skipped_mismatch} files due to line count mismatch — check your dump.")

# ── Save ──────────────────────────────────────────────────────────────────────
print("Saving data.unity3d...")
backup = DATA.with_suffix(".unity3d.bak")
if not backup.exists():
    shutil.copy2(DATA, backup)
    print(f"Backed up to {backup.name}")

patched_data = env.file.save(packer="lz4")
DATA.write_bytes(patched_data)
print(f"Saved ({len(patched_data)} bytes)")
print("\nDone. Launch the game.")