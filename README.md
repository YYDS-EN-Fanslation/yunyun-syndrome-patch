# yunyun-syndrome-patch

Translation patcher for *Yunyun Denpa Syndrome*. Writes translations directly into the game's asset files, no runtime mod required.

Part of the [YYDS EN Fanslation Project](https://github.com/YYDS-EN-Fanslation).

---

## Which should I use?

**This patcher** writes translations into your game files. Once patched, the translation works without MelonLoader or any other mod. If the game updates you'll need to repatch.

**[yunyun-syndrome-mod](https://github.com/YYDS-EN-Fanslation/yunyun-syndrome-mod)** applies translations at runtime via MelonLoader without touching your game files. Easier to update, easier to remove.

---

## What it patches

**String tables** cover UI text, social feed posts, wiki entries and everything that is not dialogue. These are stored in Addressables bundles.

**.lang files** contain story dialogue and scene text. JSON TextAssets stored inside `data.unity3d`.

After patching the string bundle the game's `catalog.bin` needs a new CRC or it won't load. The patcher handles this by briefly launching the game, reading the mismatch out of `Player.log`, and writing the fix.

---

## Usage

Download the latest release, extract it, and run `yunyun_patcher.exe`. The game path is detected automatically via the Steam registry. If that fails a folder picker opens and the path gets saved to `yunyun_config.json` for next time.

The exe expects `strings.json` and/or `lang_strings.json` to be in the same folder. These are included in the release. It patches the bundle, patches the lang files, launches the game briefly to capture the CRC, then closes it. Launch the game normally after it finishes.

### CSV patches

Drop a CSV patch in YunyunLocalePatcher format (`TableName,Key,Text`) next to the exe as `50-yysrp.csv` and it gets picked up automatically. Priority order when all sources are present: `strings.json` then CSV then `pending_edits.json`. Later sources win on conflict.

### After a game update

Restore your backups first:

```
data.unity3d.bak  →  data.unity3d
*.bundle.bak      →  bundle file
catalog.bin.bak   →  catalog.bin
```

Then run the exe again. The patcher will reapply everything.

---

## For contributors

The standalone scripts are for generating fresh translation files after a game update. Requires Python 3.10+ and `pip install unitypy psutil`.

```
python dump_strings.py   # produces strings.json
python dump_lang.py      # produces lang_strings.json
```

Edit the `"translated"` fields in the output files, then run the exe.

Dump from vanilla game files. `dump_lang.py` uses `data.unity3d.bak` automatically if it exists. If you've already patched and have no backup, restore it before dumping or the diff will be wrong.

---

## Files

| File | What it does |
|---|---|
| `yunyun_patcher.py` | Source for the exe |
| `dump_strings.py` | Dumps string tables to `strings.json` |
| `dump_lang.py` | Dumps `.lang` files to `lang_strings.json` |
| `apply_patch.py` | Standalone string table patcher |
| `apply_lang_patch.py` | Standalone `.lang` patcher |
| `apply_csv_patch.py` | Applies a CSV patch in YunyunLocalePatcher format |
| `smartformattag_patch.py` | UnityPy compatibility fix, needed by the standalone scripts |

---

## Translation file formats

### strings.json

```json
[
  {
    "path_id": 123456,
    "name": "Text_en",
    "entries": [
      {
        "m_Id": 4503599694085120,
        "japanese": "日本語テキスト",
        "original": "Original English",
        "translated": "Your translation here"
      }
    ]
  }
]
```

Only edit `"translated"`. The `"original"` field is how changes are detected.

### lang_strings.json

```json
[
  {
    "path_id": 789,
    "name": "story_ch1.lang",
    "keys": ["0x1a2b", "0x3c4d"],
    "lines": [
      {
        "key": "0x1a2b",
        "japanese": "日本語テキスト",
        "original": "Original English",
        "translated": "Your translation here"
      }
    ]
  }
]
```

### pending_edits.json

Exported by [YunDebugMenu](https://github.com/YYDS-EN-Fanslation/yunyun-syndrome-debugmenu). If it's next to the exe the patcher merges it in automatically.

```json
{
  "TableName::EntryKey": {
    "table": "TableName",
    "entry": "EntryKey",
    "value": "Translated text"
  }
}
```

---

## Notes

The CRC step launches the game and waits up to 30 seconds for the mismatch to appear in `Player.log`, then closes the game automatically. If it times out, launch the game manually to the title screen and run the exe again.

Backups are created once and never overwritten. That's intentional.

Windows only.

---

## Looking for something else?

- [yunyun-syndrome-mod](https://github.com/YYDS-EN-Fanslation/yunyun-syndrome-mod) (runtime mod, no file modification)
- [yunyun-syndrome-translation](https://github.com/YYDS-EN-Fanslation/yunyun-syndrome-translation) (translation files)
- [yunyun-syndrome-debugmenu](https://github.com/YYDS-EN-Fanslation/yunyun-syndrome-debugmenu) (in-game editor)
- [YYDS EN Fanslation](https://github.com/YYDS-EN-Fanslation) (org overview)
- [Discord](https://discord.gg/jYjTd5qpKv)
