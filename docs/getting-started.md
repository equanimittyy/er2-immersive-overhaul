# Getting Started

## Requirements

- **BepInEx 6** (Bleeding Edge, IL2CPP x64, build 717+)
- **C# / .NET** knowledge
- **Audacity** or similar for preparing WAV files
- **ffmpeg** — required if your replacement audio is OGG/MP3 (the game's `AudioClipLoader` only supports WAV, so the export converts non-WAV files automatically). Download from [ffmpeg.org/download.html](https://ffmpeg.org/download.html) and add to PATH
- **Cpp2IL** (2022.1.0-pre-release.21+) or **Il2CppDumper** for decompiling
- **dnSpy** or **ILSpy** for browsing the dummy DLLs

See [Tools & Decompilation](tools-and-decompilation.md) for download links and setup details.

## Existing Mods (Reference)

- **Voice Tweaks** (Nexus Mods) — BepInEx IL2CPP plugin that adds soldier voice features (melee voices, medic calls, surrender lines). Demonstrates that audio modding is viable in ER2.
- **Workshop weapons** — Community weapon mods on Steam Workshop already include custom audio (e.g. AVS-36 with Red Orchestra 2 sounds). Uses Audacity for audio preparation.

## Next Steps

1. **Extract vanilla audio** with AssetRipper to catalogue clip names and get reference audio specs
2. **Write the BepInEx plugin** using the patch patterns in [Plugin Design](plugin-design.md)
3. **Prepare replacement WAV files** matching the specs in [Audio Guidelines](audio-guidelines.md)
4. **Test** — start with a single weapon sound replacement, verify it works, then expand

## Documentation Index

| Document | Description |
|----------|-------------|
| [Architecture](architecture.md) | Game audio classes and their relationships |
| [Plugin Design](plugin-design.md) | BepInEx plugin structure, loading strategy, Harmony patches |
| [Audio Guidelines](audio-guidelines.md) | Audio file format and preparation specs |
| [Tools & Decompilation](tools-and-decompilation.md) | Required tools and decompilation workflow |
