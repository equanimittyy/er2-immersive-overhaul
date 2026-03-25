# Game Architecture — Audio

> Extracted from reverse-engineering Easy Red 2 (Unity 2022.3.61f1, IL2CPP) with Cpp2IL and dnSpy.

## Audio Loading

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

## Weapon Sounds — `GenericGun`

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

### Key Methods

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

## Voice Lines — `VoiceManager`

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

## Soldier — Audio Playback

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

## See Also

- [Plugin Design](plugin-design.md) — How to intercept these classes with Harmony patches
- [Audio Guidelines](audio-guidelines.md) — File format specs for replacement audio
