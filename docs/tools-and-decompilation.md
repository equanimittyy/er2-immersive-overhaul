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

## Decompilation Notes

- Game uses **IL2CPP** (no Mono), metadata version **31.1**
- Unity version: **2022.3.61f1**
- Cpp2IL command: `Cpp2IL.exe --game-path "<ER2 path>" --output-as dummydll`
- Main game assembly: `Assembly-CSharp.dll` in `cpp2il_out/`
- Old Cpp2IL versions (2022.0.x) don't support metadata v31 — use 2022.1.0-pre-release.21+

## See Also

- [Architecture](architecture.md) — Classes discovered via decompilation
- [Getting Started](getting-started.md) — Full setup requirements
