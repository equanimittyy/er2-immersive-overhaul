# Plugin Design

## Mod Structure

The exported mod uses a manifest-driven approach. The audio editor generates all of this via **Export Mod**.

```
Easy Red 2/
└── BepInEx/
    └── plugins/
        └── ER2AudioMod/
            ├── ER2AudioMod.dll      # Compiled plugin (see Building below)
            ├── manifest.json        # Maps original clip -> replacement file
            └── audio/
                ├── ger1_gotHit1.ogg # Replacement clips
                ├── vehicle_crash_1.wav
                └── ...
```

`manifest.json` structure:
```json
{
  "ger1_gotHit1.wav": {
    "replacement": "ger1_gotHit1.ogg",
    "references": [
      {"entity": "Voice-ger-1", "action": "iVeBeenHit"}
    ]
  }
}
```

## Loading Strategy

1. **On plugin startup (`Load`):** Read `manifest.json` from the plugin directory
2. **For each entry:** Load the replacement file from `audio/` using `AudioClipLoader.LoadAudioClipFromStreamAsync()`
3. **Cache all loaded clips** in a dictionary keyed by original filename
4. **Apply Harmony patches** that swap clips at the interception points below

## Harmony Patches — Weapons

Patch `GenericGun.PlayFireSound` — intercept and swap `AudioClip` fields before the original method reads them. The plugin matches by the original clip's name against the manifest.

```csharp
[HarmonyPatch(typeof(GenericGun), nameof(GenericGun.PlayFireSound))]
class Patch_PlayFireSound
{
    static void Prefix(GenericGun __instance)
    {
        var clips = AudioModPlugin.ReplacementClips;

        if (__instance.fireSound != null &&
            clips.TryGetValue(__instance.fireSound.name + ".wav", out var c))
            __instance.fireSound = c;

        // Same for fireSound_loop, fireSound_tail, fireSound_start,
        // fireSound_distance, fireSound_distance_loop, fireSound_distance_tail
    }
}
```

## Harmony Patches — Voices

Patch `VoiceManager.GetVoice` — return a replacement clip instead of the original.

```csharp
[HarmonyPatch(typeof(VoiceManager), nameof(VoiceManager.GetVoice))]
class Patch_GetVoice
{
    static bool Prefix(VoiceManager.VoiceClip clip, int index,
                       ref AudioClip __result)
    {
        string category = clip.ToString();
        if (AudioModPlugin.VoiceClips.TryGetValue(category, out var clips)
            && clips.Length > 0)
        {
            int i = (index >= 0 && index < clips.Length)
                ? index
                : UnityEngine.Random.Range(0, clips.Length);
            __result = clips[i];
            return false; // skip original
        }
        return true; // run original
    }
}
```

## Building the Plugin DLL

The audio editor's **Export Mod** generates plugin source code in `src/`. To compile:

### Prerequisites

- [.NET 6.0 SDK](https://dotnet.microsoft.com/download/dotnet/6.0) or later
- BepInEx 6 Bleeding Edge installed in your ER2 game folder
- Cpp2IL dummy DLLs generated (see [Tools & Decompilation](tools-and-decompilation.md))

### Steps

1. Run **Export Mod** in the audio editor — this creates `data/export/BepInEx/plugins/ER2AudioMod/`
2. Open a terminal in the `src/` subfolder of the export
3. Update the `<HintPath>` entries in `ER2AudioMod.csproj` to point to your local paths:
   - `BepInEx.Core.dll`, `BepInEx.Unity.IL2CPP.dll`, `0Harmony.dll` — from `BepInEx/core/`
   - `UnityEngine.dll` — from `Easy Red 2_Data/Managed/`
   - `Assembly-CSharp.dll` — from your Cpp2IL `interop/` output
4. Build:
   ```
   dotnet build -c Release
   ```
5. Copy the output `ER2AudioMod.dll` into `BepInEx/plugins/ER2AudioMod/` alongside `manifest.json` and `audio/`
6. Launch the game

### Verifying

Check `BepInEx/LogOutput.log` after launching — the plugin logs how many replacement clips were loaded.

## See Also

- [Architecture](architecture.md) — Game audio classes being patched
- [Audio Guidelines](audio-guidelines.md) — Format specs for the WAV files
- [Tools & Decompilation](tools-and-decompilation.md) — How to inspect game code
