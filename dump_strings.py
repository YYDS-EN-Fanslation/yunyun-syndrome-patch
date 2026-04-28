"""
dump_strings.py
Dumps all string table entries to strings.json with Japanese alongside English.
Sorted by table name, then by m_Id within each table for chronological context.
"""
from pathlib import Path
import smartformattag_patch
import UnityPy, json

BASE      = Path(__file__).parent
GAME      = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Yunyun_Syndrome\Yunyun_Syndrome_Data\StreamingAssets\aa\StandaloneWindows64")
EN_BUNDLE = GAME / "localization-string-tables-english(en)_assets_all.bundle"
JP_BUNDLE = GAME / "localization-string-tables-japanese(ja)_assets_all.bundle"
OUT       = BASE / "strings.json"

print("Loading Japanese bundle...")
jp_env = UnityPy.load(str(JP_BUNDLE))
jp_strings = {}
for obj in jp_env.objects:
    if obj.type.name != "MonoBehaviour":
        continue
    d = obj.read_typetree()
    if "m_TableData" not in d:
        continue
    for entry in d["m_TableData"]:
        jp_strings[int(entry["m_Id"])] = entry["m_Localized"]

print(f"Loaded {len(jp_strings)} Japanese strings")

print("Loading English bundle...")
en_env = UnityPy.load(str(EN_BUNDLE))
tables = []
for obj in en_env.objects:
    if obj.type.name != "MonoBehaviour":
        continue
    d = obj.read_typetree()
    if "m_TableData" not in d:
        continue

    entries = sorted(d["m_TableData"], key=lambda e: int(e["m_Id"]))
    table = {
        "path_id": obj.path_id,
        "name":    d["m_Name"],
        "entries": [
            {
                "m_Id":       int(entry["m_Id"]),
                "japanese":   jp_strings.get(int(entry["m_Id"]), ""),
                "original":   entry["m_Localized"],
                "translated": entry["m_Localized"],
            }
            for entry in entries
        ]
    }
    tables.append(table)

tables.sort(key=lambda t: t["name"])

for t in tables:
    print(f"  {t['name']}: {len(t['entries'])} entries")

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(tables, f, ensure_ascii=False, indent=2)

total = sum(len(t["entries"]) for t in tables)
print(f"\nDumped {total} strings across {len(tables)} tables to {OUT}")
print("Edit the 'translated' fields, then run apply_patch.py")