# Tools & Decompilation

## Required Tools

| Tool | Purpose | Source |
|------|---------|--------|
| Cpp2IL (2022.1.0-pre+) | Generate C# dummy DLLs from IL2CPP | github.com/SamboyCoding/Cpp2IL |
| dnSpy / ILSpy | Browse decompiled classes | github.com/dnSpy/dnSpy |
| AssetRipper | Extract game assets (audio, textures, etc.) | github.com/AssetRipper/AssetRipper |
| BepInEx 6 BE | IL2CPP mod loader | github.com/BepInEx/BepInEx |
| HarmonyX | Runtime method patching | (bundled with BepInEx 6) |
| Audacity | Audio editing | audacityteam.org |
| ffmpeg | Audio format conversion (OGG→WAV at export) | ffmpeg.org/download.html |

## Decompilation Notes

- Game uses **IL2CPP** (no Mono), metadata version **31.1**
- Unity version: **2022.3.61f1**
- Cpp2IL command: `Cpp2IL.exe --game-path "<ER2 path>" --output-as dummydll`
- Main game assembly: `Assembly-CSharp.dll` in `cpp2il_out/`
- Old Cpp2IL versions (2022.0.x) don't support metadata v31 — use 2022.1.0-pre-release.21+

## Extracting Vanilla Audio (AssetRipper)

The `data/` folder is gitignored because the extracted assets are too large to commit (~1.3 GB of audio). To reproduce it locally:

1. Download **AssetRipper** from github.com/AssetRipper/AssetRipper
2. Open AssetRipper and load the `Easy Red 2_Data` folder from your game install
3. Export as **Primary Content**
4. From the export, keep only `Assets/AudioClip/` — delete everything else (textures, meshes, prefabs, etc. account for ~56 GB you don't need)
5. Move/copy the `Assets/` folder into `data/` at the repo root so the structure is:

```
data/
└── Assets/
    └── AudioClip/
        ├── *.wav          # ~6,200 files, ~219 MB of weapon sounds and voice lines
        └── *.ogg
```

The extracted audio is used as reference material to catalogue clip names, check sample rates/channels, and match volume levels when preparing replacement audio.

## See Also

- [Architecture](architecture.md) — Classes discovered via decompilation
- [Getting Started](getting-started.md) — Full setup requirements
