"""
dump_lang_json.py
Extracts English and Japanese lines from .lang TextAssets in data.unity3d.
"""
from pathlib import Path
import smartformattag_patch
import UnityPy, json

BASE = Path(__file__).parent
DATA = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Yunyun_Syndrome\Yunyun_Syndrome_Data\data.unity3d")
OUT  = BASE / "lang_strings.json"

env = UnityPy.load(str(DATA))

files = []
for obj in env.objects:
    if obj.type.name != "TextAsset":
        continue
    data = obj.read()
    if not data.m_Name.endswith(".lang"):
        continue
    text = data.m_Script
    if isinstance(text, (bytes, bytearray)):
        text = text.decode("utf-8", errors="replace")

    try:
        parsed = json.loads(text)
    except Exception:
        print(f"WARNING: Could not parse {data.m_Name}, skipping")
        continue

    en_lines = None
    jp_lines = None
    for entry in parsed.get("List", []):
        lang = entry.get("Language", "").lower()
        if lang == "en":
            en_lines = entry["Lines"]
        elif lang == "ja":
            jp_lines = entry["Lines"]

    if en_lines is None:
        continue

    # Pad jp_lines if shorter than en_lines
    if jp_lines is None:
        jp_lines = [""] * len(en_lines)
    while len(jp_lines) < len(en_lines):
        jp_lines.append("")

    files.append({
        "path_id": obj.path_id,
        "name":    data.m_Name,
        "keys":    parsed.get("Keys", []),
        "lines":   [
            {
                "japanese":   jp_lines[i],
                "original":   en_lines[i],
                "translated": en_lines[i],
            }
            for i in range(len(en_lines))
        ]
    })

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(files, f, ensure_ascii=False, indent=2)

total = sum(len(f["lines"]) for f in files)
print(f"Dumped {total} lines across {len(files)} .lang files to {OUT}")
print("Edit the 'translated' fields, then run apply_lang_patch.py")