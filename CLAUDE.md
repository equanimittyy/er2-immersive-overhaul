# ER2 Immersive Overhaul

## Project Overview

Audio modding project for Easy Red 2 (Unity 2022.3.61f1, IL2CPP). Uses BepInEx 6 + HarmonyX to intercept audio playback at runtime and swap in custom clips.

## Key Concepts

- Game is compiled with **IL2CPP** (not Mono) — requires BepInEx 6 Bleeding Edge (IL2CPP x64)
- Audio replacement works via **Harmony patches** on `GenericGun` (weapons) and `VoiceManager` (voices)
- Custom audio files are **WAV format**, loaded using the game's own `AudioClipLoader`
- Decompilation uses **Cpp2IL** (2022.1.0-pre-release.21+) to generate dummy DLLs from metadata v31.1

## Documentation

All design and reference docs live in `docs/`:

- `docs/architecture.md` — Game audio classes and their relationships
- `docs/plugin-design.md` — BepInEx plugin structure, loading strategy, Harmony patches
- `docs/audio-guidelines.md` — Audio file format, sample rate, and preparation specs
- `docs/tools-and-decompilation.md` — Required tools and decompilation workflow
- `docs/getting-started.md` — Requirements, existing mods reference, and next steps
