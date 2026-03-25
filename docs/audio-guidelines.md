# Audio File Guidelines

## Format Requirements

- **Format:** WAV (16-bit PCM recommended)
- **Sample rate:** Match the originals if possible (use AssetRipper to extract and check). Common rates: 44100 Hz or 22050 Hz
- **Channels:** Mono for 3D positional sounds (weapons, voices), stereo only for ambient/music
- **Duration:** For looped sounds (`fireSound_loop`), ensure seamless looping — the loop point is the start/end of the file
- **Volume:** Normalize to roughly match vanilla levels. The game applies its own volume multipliers

## Sound Categories

### Weapon Sounds

Folder names map directly to `GenericGun` fields (see [Architecture](architecture.md#weapon-sounds--genericgun)):

| Folder | Field | Description |
|--------|-------|-------------|
| `fireSound/` | `fireSound` | Single shot |
| `fireSound_loop/` | `fireSound_loop` | Looped fire (MGs) |
| `fireSound_tail/` | `fireSound_tail` | Tail/echo after firing stops |
| `fireSound_distance/` | `fireSound_distance` | Distant single shot |
| `fireSound_distance_loop/` | `fireSound_distance_loop` | Distant looped fire |
| `fireSound_distance_tail/` | `fireSound_distance_tail` | Distant tail |

### Voice Lines

Folder names map directly to `VoiceManager` array field names (see [Architecture](architecture.md#voice-lines--voicemanager)). Multiple WAV files in a folder become the replacement array — the game picks randomly or by index.

## See Also

- [Architecture](architecture.md) — Class fields these files map to
- [Plugin Design](plugin-design.md) — How replacement files are loaded and applied
