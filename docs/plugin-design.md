# Plugin Design

## Folder Structure

```
Easy Red 2/
└── BepInEx/
    └── plugins/
        └── ER2AudioMod/
            ├── ER2AudioMod.dll          # The compiled plugin
            ├── Weapons/
            │   ├── fireSound/           # Single shot replacements
            │   │   └── *.wav
            │   ├── fireSound_loop/      # Looped fire replacements
            │   │   └── *.wav
            │   ├── fireSound_tail/      # Fire tail replacements
            │   │   └── *.wav
            │   ├── fireSound_distance/  # Distant shot replacements
            │   │   └── *.wav
            │   └── ...                  # Other sound categories
            └── Voices/
                ├── iVeBeenHit/
                │   └── *.wav
                ├── medic/
                │   └── *.wav
                ├── imReloading/
                │   └── *.wav
                ├── enemyInfantrySpotted/
                │   └── *.wav
                └── ...                  # Other voice categories
```

## Loading Strategy

1. **On plugin startup (`Awake`):** Scan the folder structure above
2. **For each WAV file found:** Use `AudioClipLoader.LoadAudioClipFromStreamAsync()` to load it into an `AudioClip`
3. **Cache all loaded clips** in dictionaries keyed by category name
4. **Apply Harmony patches** that swap clips at the right interception points

## Harmony Patches — Weapons

**Option A: Patch `PlayFireSound`** — intercept the method and swap the `AudioClip` fields on the `GenericGun` instance before the original method reads them.

```csharp
[HarmonyPatch(typeof(GenericGun), "PlayFireSound")]
class Patch_PlayFireSound
{
    static void Prefix(GenericGun __instance)
    {
        if (AudioModPlugin.WeaponClips.TryGetValue("fireSound", out var clip))
            __instance.fireSound = clip;

        if (AudioModPlugin.WeaponClips.TryGetValue("fireSound_loop", out var loopClip))
            __instance.fireSound_loop = loopClip;

        // ... etc for each sound field
    }
}
```

**Option B: Patch `GenericGun.Fire`** or `OnGunFire` for more control over when replacements happen.

## Harmony Patches — Voices

**Option A: Patch `VoiceManager.GetVoice`** — return a custom clip instead.

```csharp
[HarmonyPatch(typeof(VoiceManager), "GetVoice")]
class Patch_GetVoice
{
    static bool Prefix(VoiceManager.VoiceClip clip, int index,
                       ref AudioClip __result)
    {
        string category = clip.ToString(); // e.g. "medic"
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

**Option B: Replace the arrays directly** on each `VoiceManager` instance after it loads, using a postfix patch on `Soldier.GetSoldierVoice`.

## See Also

- [Architecture](architecture.md) — Game audio classes being patched
- [Audio Guidelines](audio-guidelines.md) — Format specs for the WAV files
- [Tools & Decompilation](tools-and-decompilation.md) — How to inspect game code
