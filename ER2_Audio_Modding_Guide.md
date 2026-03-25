# Easy Red 2 — Audio Modding Guide

## Overview

Easy Red 2 is a Unity game (2022.3.61f1) compiled with IL2CPP. Audio modding requires a BepInEx 6 plugin that intercepts audio playback at runtime and swaps in custom clips.

### Requirements

- **BepInEx 6** (Bleeding Edge, IL2CPP x64, build 717+)
- **C# / .NET** knowledge
- **Audacity** or similar for preparing WAV files
- **Cpp2IL** (2022.1.0-pre-release.21+) or **Il2CppDumper** for decompiling
- **dnSpy** or **ILSpy** for browsing the dummy DLLs

---

## Game Architecture — Audio

### Audio Loading

The game has a built-in `AudioClipLoader` class that handles WAV file loading:

```csharp
public class AudioClipLoader : MonoBehaviour
{
    // Loads a WAV from a Stream, returns AudioClip via callback
    public static void LoadAudioClipFromStreamAsync(
        Stream stream,
        string fileName,
        Action<AudioClip> callback
    );

    // Internal WAV decoder
    private static WavData DecodeWav(Stream stream);

    // Determines audio type from file extension
    private static AudioType GetAudioTypeFromFileName(string fileName);

    // Internal WAV data structure
    private class WavData
    {
        public float[] audioData;
        public int sampleCountPerChannel;
        public int channels;
        public int sampleRate;
    }
}
```

**Key takeaway:** Use WAV format for all replacement audio. The game's own loader handles decoding.

---

### Weapon Sounds — `GenericGun`

`GenericGun` is the base class for all firearms. It contains these audio-related fields:

```csharp
public class GenericGun : Weapon, ImpactSpecifier, ISerializationCallbackReceiver
{
    // --- Audio Clips ---
    public AudioClip fireSound;              // Single shot
    public AudioClip fireSound_start;        // Start of looped fire (MGs etc.)
    public AudioClip fireSound_loop;         // Looped fire
    public AudioClip fireSound_tail;         // Tail/echo after firing stops
    public AudioClip fireSound_distance;     // Distant single shot
    public AudioClip fireSound_distance_loop; // Distant looped fire
    public AudioClip fireSound_distance_tail; // Distant tail

    // --- Audio playback ---
    protected AudioSource audioSource;       // The AudioSource component

    // --- Sound IDs (loaded from bundles) ---
    public string sound_equip_gun;
    public string sound_holster_gun;
}
```

**Key methods:**

| Method | Purpose |
|--------|---------|
| `PlayFireSound(Soldier user)` | Main entry point — plays the appropriate fire sound |
| `SingleFireSound(Soldier user)` | Plays a single shot sound |
| `StartLoopedFireSound(Soldier user)` | Starts looped fire (coroutine) |
| `ForceStopLoopedSound(Soldier s)` | Stops looped fire sound |
| `UseCloseSound(Soldier user)` | Returns true if close-range sound should be used |
| `UseDistantSound(Soldier user)` | Returns true if distant sound should be used |
| `UseLoopedSound(bool isCloseSound)` | Returns true if looped sound variant should be used |
| `SetUpAudioSource()` | Configures the AudioSource component |
| `PlayFireFX(Vector3 fireDir, Soldier user)` | Plays fire VFX and sound together |
| `EmptyChamberClick()` | Plays empty chamber click sound |
| `SetReverb(bool reverbOn)` | Toggles reverb filter |

---

### Voice Lines — `VoiceManager`

`VoiceManager` stores all soldier voice clips as public `AudioClip[]` arrays, indexed by a `VoiceClip` enum:

```csharp
public class VoiceManager : MonoBehaviour
{
    public float volumeMultiplier;

    // Retrieves a clip by type (random or sequential index)
    public AudioClip GetVoice(VoiceManager.VoiceClip clip, int index = -1);

    // --- Voice clip arrays ---
    public AudioClip[] iVeBeenHit;
    public AudioClip[] medic;
    public AudioClip[] imReloading;
    public AudioClip[] imUnderFire;
    public AudioClip[] AAAAAH;
    public AudioClip[] scream_long;
    public AudioClip[] yes;
    public AudioClip[] yesSir;
    public AudioClip[] watchYourFire;
    public AudioClip[] enemyInfantrySpotted;
    public AudioClip[] enemyTankSpotted;
    public AudioClip[] enemyArtillerySpotted;
    public AudioClip[] enemyDown;
    public AudioClip[] granade;
    public AudioClip[] thankYou;
    public AudioClip[] coveringFire;
    public AudioClip[] imMoving;
    public AudioClip[] imCharging;
    public AudioClip[] iSurrender;
    public AudioClip[] imTakingTheLead;
    public AudioClip[] moveThere;
    public AudioClip[] attackThere;
    public AudioClip[] charge;
    public AudioClip[] attackThatTank;
    public AudioClip[] attackThatVehicle;
    public AudioClip[] followMe;
    public AudioClip[] letsSpreadOut;
    public AudioClip[] lineFormation;
    public AudioClip[] columnFormation;
    public AudioClip[] timeToRetreat;
    public AudioClip[] getOut;
    public AudioClip[] getIn;
    public AudioClip[] letsMoveTank;
    public AudioClip[] fireTank;
    public AudioClip[] gunReloadedTank;
    public AudioClip[] enemyHittedTank;
    public AudioClip[] enemyDestroyedtank;
    public AudioClip[] enemyMissedTank;
    public AudioClip[] enemyNotPenetratedTank;
    public AudioClip[] gotHitTank;
    public AudioClip[] radiomanIsDead;
    public AudioClip[] gunnerIsDead;
    public AudioClip[] commanderIsDead;
    public AudioClip[] driverIsDead;
    public AudioClip[] illTakeHisSeat;
    public AudioClip[] getOutTankOnFire;
    public AudioClip[] getOutTankDestroyed;
    public AudioClip[] numbers;
    public AudioClip[] artillerySupportAt;
    public AudioClip[] tankSupportRequest;
    public AudioClip[] artilleryStrikeIncomingAt;
    public AudioClip[] keepYourHeadDown;
    public AudioClip[] noArtilleryAvailable;
    public AudioClip[] tankSupportIncoming;
    public AudioClip[] noTankAvailable;

    // Enum for all voice clip types
    public enum VoiceClip { ... }
}
```

---

### Soldier — Audio Playback

The `Soldier` class ties weapons and voices together:

```csharp
public class Soldier : Creature
{
    // --- Audio Sources ---
    public AudioSource vfxSoundEmitter;     // Plays SFX
    public AudioSource voiceSoundEmitter;   // Plays voice lines

    // --- Voice ---
    private VoiceManager _voice;            // Reference to soldier's VoiceManager

    // --- Key audio methods ---

    // Play a voice line by enum type
    public void Say(VoiceManager.VoiceClip voiceClip,
                    float delay = 0f, int index = -1,
                    Action<AudioClip> retClip = null);

    // Play a specific AudioClip as voice
    public void SayClip(AudioClip clip);

    // Sync voice line over network
    public void SyncSay(VoiceManager.VoiceClip voiceClip, short clip_id = -1);

    // Load and play a sound from an asset bundle by name
    public IEnumerator LoadAndPlaySound(string sound_name_id,
                                         bool playIfPlayer,
                                         string bundle = "er2bundle");

    // Load voice manager for this soldier
    public IEnumerator GetSoldierVoice(Action<VoiceManager> voice);

    // Volume multipliers
    public float PlayerSoundMultipler { get; }
    public float PlayerVoiceMultipler { get; }

    // Reverb
    public bool IsReverbOn { get; }
}
```

---

## Plugin Design

### Folder Structure

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

### Loading Strategy

1. **On plugin startup (`Awake`):** Scan the folder structure above
2. **For each WAV file found:** Use `AudioClipLoader.LoadAudioClipFromStreamAsync()` to load it into an `AudioClip`
3. **Cache all loaded clips** in dictionaries keyed by category name
4. **Apply Harmony patches** that swap clips at the right interception points

### Harmony Patches — Weapons

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

### Harmony Patches — Voices

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

---

## Audio File Guidelines

- **Format:** WAV (16-bit PCM recommended)
- **Sample rate:** Match the originals if possible (use AssetRipper to extract and check). Common rates: 44100 Hz or 22050 Hz
- **Channels:** Mono for 3D positional sounds (weapons, voices), stereo only for ambient/music
- **Duration:** For looped sounds (`fireSound_loop`), ensure seamless looping — the loop point is the start/end of the file
- **Volume:** Normalize to roughly match vanilla levels. The game applies its own volume multipliers

---

## Tools Reference

| Tool | Purpose | Source |
|------|---------|--------|
| Cpp2IL (2022.1.0-pre+) | Generate C# dummy DLLs from IL2CPP | github.com/SamboyCoding/Cpp2IL |
| dnSpy / ILSpy | Browse decompiled classes | github.com/dnSpy/dnSpy |
| AssetRipper | Extract game assets (audio, textures, etc.) | github.com/AssetRipper/AssetRipper |
| BepInEx 6 BE | IL2CPP mod loader | github.com/BepInEx/BepInEx |
| HarmonyX | Runtime method patching | (bundled with BepInEx 6) |
| Audacity | Audio editing | audacityteam.org |

---

## Decompilation Notes

- Game uses **IL2CPP** (no Mono), metadata version **31.1**
- Unity version: **2022.3.61f1**
- Cpp2IL command: `Cpp2IL.exe --game-path "<ER2 path>" --output-as dummydll`
- Main game assembly: `Assembly-CSharp.dll` in `cpp2il_out/`
- Old Cpp2IL versions (2022.0.x) don't support metadata v31 — use 2022.1.0-pre-release.21+

---

## Existing Mods (Reference)

- **Voice Tweaks** (Nexus Mods) — BepInEx IL2CPP plugin that adds soldier voice features (melee voices, medic calls, surrender lines). Demonstrates that audio modding is viable in ER2.
- **Workshop weapons** — Community weapon mods on Steam Workshop already include custom audio (e.g. AVS-36 with Red Orchestra 2 sounds). Uses Audacity for audio preparation.

---

## Next Steps

1. **Extract vanilla audio** with AssetRipper to catalogue clip names and get reference audio specs
2. **Write the BepInEx plugin** using the patch patterns above
3. **Prepare replacement WAV files** matching the specs of the originals
4. **Test** — start with a single weapon sound replacement, verify it works, then expand
