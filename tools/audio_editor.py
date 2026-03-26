#!/usr/bin/env python3
"""ER2 Audio Editor — maps and manages audio clip assignments.

Uses Unity prefab data (from AssetRipper) to build authoritative
voice clip mappings. No regex guessing.
"""

import json
import mimetypes
import os
import re
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import unquote

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
AUDIO_DIR = DATA_DIR / "AudioClip"
VOICES_DIR = DATA_DIR / "voices"
WEAPONS_DIR = DATA_DIR / "weapons"
VEHICLES_DIR = DATA_DIR / "vehicles"
CUSTOM_DIR = DATA_DIR / "custom"
RO2_DIR = DATA_DIR / "RO2-RS"
MAPPING_FILE = DATA_DIR / "audio_mapping.json"
RO2_CATALOGUE_FILE = DATA_DIR / "ro2_catalogue.json"
SWAPS_FILE = DATA_DIR / "swaps.json"
CONFIG_FILE = DATA_DIR / "config.json"
PORT = 8420
_audio_info_cache = {}  # populated at startup


def load_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {}


def save_config(config):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def get_game_path():
    return load_config().get("game_path", "")


def validate_game_path(path):
    """Basic sanity check on the game path string."""
    if not path or not path.strip():
        return False, ["Path is empty"]
    # Just check it looks like a plausible path — actual validation
    # happens on the user's machine when they build the plugin
    return True, []

# GenericGun audio fields
WEAPON_AUDIO_FIELDS = {
    "fireSound", "fireSound_start", "fireSound_loop", "fireSound_tail",
    "fireSound_distance", "fireSound_distance_loop", "fireSound_distance_tail",
    "reload_sound_full", "reload_sound_half",
    "chamber_sound", "chamber_sound_open", "chamber_sound_close",
    "chamber_sound_open_noAmmo", "chamber_sound_close_noAmmo",
    "boltaction_sound", "pingSound", "fps_change_barrell_sound", "sound",
}

# Vehicle audio fields
VEHICLE_AUDIO_FIELDS = WEAPON_AUDIO_FIELDS | {
    "engine_start", "engine_move", "engine_stop",
    "crashSound", "turnSoundLoop_outside",
}

# ---------------------------------------------------------------------------
# Prefab-based mapping builder
# ---------------------------------------------------------------------------

def build_guid_map():
    """Build guid -> filename map from .meta files alongside audio clips."""
    guid_map = {}
    for f in AUDIO_DIR.iterdir():
        if f.suffix != ".meta":
            continue
        with open(f) as fh:
            for line in fh:
                m = re.match(r"^guid:\s+(\w+)", line)
                if m:
                    guid_map[m.group(1)] = f.stem  # removes .meta, keeps .wav/.ogg
                    break
    return guid_map


def parse_prefab(path, guid_map):
    """Parse a voice prefab YAML and return {field: [filenames]}."""
    with open(path) as f:
        content = f.read()

    actions = {}
    current_field = None
    in_mono = False

    for line in content.split("\n"):
        if line.startswith("MonoBehaviour:"):
            in_mono = True
            continue
        if not in_mono:
            continue
        if line.startswith("---"):
            break

        # Array header: "  fieldName:"
        field_match = re.match(r"^  (\w+):\s*$", line)
        if field_match:
            current_field = field_match.group(1)
            continue

        # Inline value — not an array
        if re.match(r"^  \w+:\s+\S", line):
            current_field = None
            continue

        # Array item with guid
        guid_match = re.match(r"^\s+-\s+\{.*guid:\s+(\w+)", line)
        if guid_match and current_field:
            guid = guid_match.group(1)
            filename = guid_map.get(guid, f"UNKNOWN_{guid}")
            actions.setdefault(current_field, []).append(filename)

    # Filter out Unity internal fields
    return {k: v for k, v in actions.items() if not k.startswith("m_")}


def parse_equipment_prefab(path, guid_map, audio_fields):
    """Parse a weapon/vehicle prefab and return {field: [filenames]} for audio fields."""
    with open(path) as f:
        content = f.read()

    actions = {}
    for line in content.split("\n"):
        m = re.match(r"^\s+(\w+):\s+\{.*guid:\s+(\w+)", line)
        if m:
            field, guid = m.group(1), m.group(2)
            if field in audio_fields:
                filename = guid_map.get(guid, f"UNKNOWN_{guid}")
                actions.setdefault(field, []).append(filename)

    return actions


def scan_from_prefabs():
    """Build the full mapping from voice + weapon prefabs + audio meta files."""
    guid_map = build_guid_map()

    # Collect all audio filenames (excluding .meta)
    all_audio = set()
    for f in AUDIO_DIR.iterdir():
        if f.is_file() and f.suffix != ".meta":
            all_audio.add(f.name)

    mapping = {}
    assigned_files = set()

    # Parse voice prefabs
    for prefab_file in sorted(VOICES_DIR.iterdir()):
        if prefab_file.suffix != ".prefab":
            continue
        entity_name = prefab_file.stem
        actions = parse_prefab(prefab_file, guid_map)
        if actions:
            mapping[entity_name] = actions
            for clips in actions.values():
                assigned_files.update(clips)

    # Parse weapon prefabs
    for prefab_file in sorted(WEAPONS_DIR.iterdir()):
        if prefab_file.suffix != ".prefab":
            continue
        actions = parse_equipment_prefab(prefab_file, guid_map, WEAPON_AUDIO_FIELDS)
        if actions:
            mapping[prefab_file.stem] = actions
            for clips in actions.values():
                assigned_files.update(clips)

    # Parse vehicle prefabs (recursive — subdirs like tanks/, artillery/, etc.)
    for prefab_file in sorted(VEHICLES_DIR.rglob("*.prefab")):
        actions = parse_equipment_prefab(prefab_file, guid_map, VEHICLE_AUDIO_FIELDS)
        if actions:
            # Prefix with subfolder for clarity: "tanks/Sherman M4A1"
            rel = prefab_file.relative_to(VEHICLES_DIR)
            entity_name = str(rel.with_suffix(""))
            mapping[entity_name] = actions
            for clips in actions.values():
                assigned_files.update(clips)

    # Categorise unassigned files into relevant tabs
    unassigned = sorted(all_audio - assigned_files)

    # Weapon-related unassigned
    weapon_uncat = {}
    # Vehicle-related unassigned
    vehicle_uncat = {}
    # Truly misc
    other_uncat = []

    for f in unassigned:
        fl = f.lower()

        # Weapon-related: fire sounds, reload, bolt action, eject, distant MG/SMG
        if any(kw in fl for kw in [
            "_fire", "_reload", "_eject", "boltaction", "pingsound",
            "dist_mg", "dist_smg", "dist_bofors", "dist_flak",
            "m1919 browning", "m2browning", "mg42_", "type99_", "type11_",
            "30mm_fire", "70mm_fire", "ppsh41_", "thompson_eng_drum",
            "cannon_fire", "distant_mg",
        ]):
            # Try to extract weapon name
            stem = Path(f).stem
            if "Dist_" in f:
                cat = "distant_fire"
            elif "_reload" in fl or "_eject" in fl:
                cat = "reload_misc"
            else:
                cat = "fire_misc"
            weapon_uncat.setdefault(cat, []).append(f)

        # Vehicle-related: tank, jeep, vehicle, turret, aircraft, engine
        elif any(kw in fl for kw in [
            "tank", "jeep", "vehicle", "turret", "aircraft",
            "engine", "stuka", "whistle_lcvp", "tanks_threads",
        ]):
            if "tank" in fl or "turret" in fl or "tanks_threads" in fl:
                cat = "tank_misc"
            elif "jeep" in fl or "vehicle" in fl:
                cat = "wheeled_misc"
            elif "aircraft" in fl or "stuka" in fl:
                cat = "aircraft_misc"
            else:
                cat = "vehicle_misc"
            vehicle_uncat.setdefault(cat, []).append(f)

        else:
            other_uncat.append(f)

    if weapon_uncat:
        mapping["_Unassigned Weapons"] = weapon_uncat
    if vehicle_uncat:
        mapping["_Unassigned Vehicles"] = vehicle_uncat

    # Sub-categorise the remaining "other" files by prefix
    OTHER_GROUPS = [
        ("footsteps",    ["fs_", "step_"]),
        ("explosions",   ["explosion_", "artillerysmoke_", "smokegrenade_", "smokeartillery_"]),
        ("grenades",     ["grenade_throw", "grenade_impact", "grenade_explosion",
                          "molotov_", "explsv_"]),
        ("flamethrower", ["flamer_"]),
        ("ui_music",     ["intro_loading", "lorenzo", "objective_updated", "page_turn"]),
        ("radio",        ["radio_noise"]),
        ("ambient",      ["air_raid", "crow", "dog_tags"]),
    ]

    other_categorised = {}
    truly_misc = []
    for f in other_uncat:
        fl = f.lower()
        matched = False
        for cat, prefixes in OTHER_GROUPS:
            if any(fl.startswith(p) for p in prefixes):
                other_categorised.setdefault(cat, []).append(f)
                matched = True
                break
        if not matched:
            truly_misc.append(f)

    if truly_misc:
        other_categorised["misc"] = truly_misc
    if other_categorised:
        mapping["Uncategorised"] = other_categorised

    return {"voices": mapping}


def load_mapping():
    if MAPPING_FILE.exists():
        with open(MAPPING_FILE) as f:
            return json.load(f)
    data = scan_from_prefabs()
    save_mapping(data)
    return data


def save_mapping(data):
    MAPPING_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(MAPPING_FILE, "w") as f:
        json.dump(data, f, indent=2)


def build_refs(data):
    """Build reverse index: filename -> [(entity, action), ...]."""
    refs = {}
    for entity, actions in data.get("voices", {}).items():
        for action, clips in actions.items():
            for clip in clips:
                refs.setdefault(clip, []).append({"entity": entity, "action": action})
    return refs


def get_audio_info(filepath):
    """Get audio metadata from a WAV or OGG file."""
    import struct, wave
    info = {"size": filepath.stat().st_size}
    ext = filepath.suffix.lower()

    if ext == ".wav":
        try:
            with wave.open(str(filepath), "rb") as w:
                info["channels"] = w.getnchannels()
                info["sample_rate"] = w.getframerate()
                info["bit_depth"] = w.getsampwidth() * 8
                info["duration"] = round(w.getnframes() / w.getframerate(), 2)
        except Exception:
            pass
    elif ext == ".ogg":
        try:
            with open(filepath, "rb") as f:
                data = f.read(200)
            idx = data.find(b"\x01vorbis")
            if idx >= 0:
                offset = idx + 7
                info["channels"] = struct.unpack_from("<B", data, offset + 4)[0]
                info["sample_rate"] = struct.unpack_from("<I", data, offset + 5)[0]
        except Exception:
            pass

    return info


def build_audio_info_cache():
    """Build info for all audio clips."""
    cache = {}
    for f in AUDIO_DIR.iterdir():
        if f.is_file() and f.suffix in (".wav", ".ogg"):
            cache[f.name] = get_audio_info(f)
    return cache


def scan_ro2_catalogue():
    """Scan RO2-RS audio files and build a structured catalogue.

    Structure:
    {
      "game": "RO2" or "RS",
      "categories": {
        "voices": {
          "GerNative_01": {         # voice identity
            "faction": "German",
            "language": "Native",
            "type": "infantry",     # infantry or tank
            "actions": {
              "Inf_AttackGeneric": ["path/to/file.ogg", ...],
              ...
            }
          }, ...
        },
        "weapons": {
          "Rifle_Kar98": {
            "type": "Rifle",
            "clips": ["path/to/file.ogg", ...]
          }, ...
        },
        "vehicles": { ... },
        "explosions": { ... },
        "environment": { ... },
        "character": { ... },
        "other": { ... }
      }
    }
    """
    if not RO2_DIR.exists():
        return {}

    catalogue = {}

    for game_dir in sorted(RO2_DIR.iterdir()):
        if not game_dir.is_dir():
            continue
        game = game_dir.name  # "RO2" or "RS"
        categories = {
            "voices": {},
            "weapons": {},
            "vehicles": {},
            "explosions": {},
            "environment": {},
            "character": {},
            "other": {},
        }

        for folder in sorted(game_dir.iterdir()):
            if not folder.is_dir():
                continue
            name = folder.name
            files = sorted(str(f.relative_to(RO2_DIR))
                          for f in folder.rglob("*.ogg"))
            if not files:
                continue

            # Voice folders: AUD_VOX_Chatter_* or AUD_RS_VOX_Chatter_*
            if "_VOX_Chatter_" in name:
                # Parse voice identity from folder name
                # RO2: AUD_VOX_Chatter_GerNative_01 / AUD_VOX_Chatter_Tank_GerGer_02
                # RS:  AUD_RS_VOX_Chatter_Eng_01 / AUD_RS_VOX_Chatter_Jap_03
                parts = name.split("Chatter_")[1]  # e.g. "GerNative_01" or "Tank_GerGer_02"
                is_tank = parts.startswith("Tank_")
                if is_tank:
                    parts = parts[5:]  # strip "Tank_"

                # Split into identity: everything up to last _NN
                identity_parts = parts.rsplit("_", 1)
                voice_id = parts
                voice_num = identity_parts[1] if len(identity_parts) > 1 else "01"
                voice_name = identity_parts[0] if len(identity_parts) > 1 else parts

                # Parse faction and language from voice_name
                FACTION_MAP = {
                    "Ger": "German", "Rus": "Russian",
                    "Eng": "American", "Jap": "Japanese",
                }
                faction = "Unknown"
                language = "Unknown"
                for prefix, fac in FACTION_MAP.items():
                    if voice_name.startswith(prefix):
                        faction = fac
                        language = voice_name[len(prefix):]
                        if not language:
                            language = "Native"
                        break

                # Group files by action
                actions = {}
                for fp in files:
                    fname = Path(fp).stem
                    # Strip intensity/variant suffix: _H_274, _L_280, _N_265, _NOR_1, etc
                    action = re.sub(r"_[HLN]_\d+.*$", "", fname)
                    action = re.sub(r"_(NOR|LOW|HER|SUP|OFF)_\d+.*$", "", fname, flags=re.I)
                    action = re.sub(r"_\d+$", "", action)
                    actions.setdefault(action, []).append(fp)

                full_id = ("Tank_" + voice_id) if is_tank else voice_id
                categories["voices"][full_id] = {
                    "faction": faction,
                    "language": language,
                    "type": "tank" if is_tank else "infantry",
                    "voice_num": voice_num,
                    "actions": actions,
                }

            # Weapon folders: AUD_Firearms_* or AUD_RS_Firearms_*
            elif "_Firearms_" in name or name.endswith("_Firearms") or "_Flamethrower_" in name or "_Melee_" in name:
                # Extract weapon type and name
                if "_Firearms_" in name:
                    weapon_part = name.split("_Firearms_")[1] if "_Firearms_" in name else name
                elif "_Flamethrower_" in name:
                    weapon_part = "Flamethrower_" + name.split("_Flamethrower_")[1]
                elif "_Melee_" in name:
                    weapon_part = "Melee_" + name.split("_Melee_")[1]
                else:
                    weapon_part = name

                # Parse type from prefix: Rifle_, SMG_, MG_, Pistol_, etc
                wtype = "misc"
                for t in ["Rifle", "SMG", "MG", "LMG", "HMG", "BAR", "Pistol",
                          "AT", "Shotgun", "Tails", "Flamethrower", "Melee"]:
                    if weapon_part.startswith(t):
                        wtype = t
                        break

                categories["weapons"][weapon_part] = {
                    "type": wtype,
                    "clips": files,
                }

            # Vehicle folders
            elif "_Vehicle_" in name:
                categories["vehicles"][name] = {"clips": files}

            # Explosions
            elif "_EXP_" in name:
                categories["explosions"][name] = {"clips": files}

            # Environment/ambient
            elif "_Environment" in name or "_ENV_" in name or "_EnvironmentSounds" in name:
                categories["environment"][name] = {"clips": files}

            # Character (footsteps, foley, bodyfall)
            elif "_Character" in name or "_Weapon_Foley" in name:
                categories["character"][name] = {"clips": files}

            # Everything else
            else:
                categories["other"][name] = {"clips": files}

        catalogue[game] = categories

    return catalogue


def load_ro2_catalogue():
    if RO2_CATALOGUE_FILE.exists():
        with open(RO2_CATALOGUE_FILE) as f:
            return json.load(f)
    catalogue = scan_ro2_catalogue()
    if catalogue:
        RO2_CATALOGUE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(RO2_CATALOGUE_FILE, "w") as f:
            json.dump(catalogue, f, indent=2)
    return catalogue


def load_swaps():
    if SWAPS_FILE.exists():
        with open(SWAPS_FILE) as f:
            return json.load(f)
    return {}


def save_swaps(swaps):
    SWAPS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SWAPS_FILE, "w") as f:
        json.dump(swaps, f, indent=2)


PROFILES_DIR = DATA_DIR / "profiles"


def list_profiles():
    if not PROFILES_DIR.exists():
        return []
    return sorted(p.stem for p in PROFILES_DIR.glob("*.json"))


def save_profile(name):
    """Save current swaps + custom files as a named profile."""
    import shutil
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    swaps = load_swaps()
    profile = {"swaps": swaps}
    with open(PROFILES_DIR / f"{name}.json", "w") as f:
        json.dump(profile, f, indent=2)
    # Copy custom files into profile dir
    profile_audio = PROFILES_DIR / name
    if profile_audio.exists():
        shutil.rmtree(profile_audio)
    profile_audio.mkdir()
    for custom_name in swaps.values():
        src = CUSTOM_DIR / custom_name
        if src.exists():
            shutil.copy2(src, profile_audio / custom_name)
    return {"ok": True, "name": name, "swaps": len(swaps)}


def load_profile(name):
    """Load a named profile, replacing current swaps."""
    import shutil
    profile_path = PROFILES_DIR / f"{name}.json"
    if not profile_path.exists():
        return {"ok": False, "error": "Profile not found"}
    with open(profile_path) as f:
        profile = json.load(f)
    # Restore swaps
    save_swaps(profile["swaps"])
    # Restore custom files
    CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
    profile_audio = PROFILES_DIR / name
    if profile_audio.exists():
        for f in profile_audio.iterdir():
            if f.is_file():
                shutil.copy2(f, CUSTOM_DIR / f.name)
    return {"ok": True, "name": name, "swaps": len(profile["swaps"])}


def delete_profile(name):
    import shutil
    profile_path = PROFILES_DIR / f"{name}.json"
    profile_audio = PROFILES_DIR / name
    if profile_path.exists():
        profile_path.unlink()
    if profile_audio.exists():
        shutil.rmtree(profile_audio)
    return {"ok": True}


EXPORT_DIR = Path(__file__).resolve().parent.parent / "export" / "ER2AudioMod"


def _wav_rms(filepath):
    """Compute RMS loudness of a WAV file. Returns float or None."""
    import math, struct, wave
    try:
        with wave.open(str(filepath), "rb") as w:
            n = w.getnframes()
            if n == 0:
                return None
            sw = w.getsampwidth()
            raw = w.readframes(n)
            ch = w.getnchannels()
            total = n * ch
            if sw == 2:
                samples = struct.unpack(f"<{total}h", raw)
                peak = 32768.0
            elif sw == 3:
                samples = []
                for i in range(0, len(raw), 3):
                    val = int.from_bytes(raw[i:i+3], "little", signed=True)
                    samples.append(val)
                peak = 8388608.0
            elif sw == 1:
                samples = struct.unpack(f"{total}B", raw)
                samples = [s - 128 for s in samples]
                peak = 128.0
            else:
                return None
            sum_sq = sum(s * s for s in samples)
            rms = math.sqrt(sum_sq / total) / peak
            return rms if rms > 0 else None
    except Exception:
        return None


def _ogg_rms(filepath):
    """Compute RMS of an OGG file by decoding to WAV via ffmpeg. Returns float or None."""
    import subprocess, tempfile
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(filepath), "-acodec", "pcm_s16le", tmp_path],
            check=True, capture_output=True,
        )
        rms = _wav_rms(tmp_path)
        os.unlink(tmp_path)
        return rms
    except Exception:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        return None


def _audio_rms(filepath):
    """Compute RMS of a WAV or OGG file."""
    ext = Path(filepath).suffix.lower()
    if ext == ".wav":
        return _wav_rms(filepath)
    elif ext == ".ogg":
        return _ogg_rms(filepath)
    return None


def _apply_gain_wav(filepath, gain):
    """Apply a linear gain factor to a WAV file in-place."""
    import struct, wave
    try:
        with wave.open(str(filepath), "rb") as w:
            params = w.getparams()
            n = w.getnframes()
            sw = w.getsampwidth()
            raw = w.readframes(n)

        ch = params.nchannels
        total = n * ch

        if sw == 2:
            samples = list(struct.unpack(f"<{total}h", raw))
            clamped = [max(-32768, min(32767, int(s * gain))) for s in samples]
            raw_out = struct.pack(f"<{total}h", *clamped)
        elif sw == 3:
            samples = []
            for i in range(0, len(raw), 3):
                val = int.from_bytes(raw[i:i+3], "little", signed=True)
                samples.append(val)
            clamped = [max(-8388608, min(8388607, int(s * gain))) for s in samples]
            raw_out = b"".join(v.to_bytes(3, "little", signed=True) for v in clamped)
        else:
            return

        with wave.open(str(filepath), "wb") as w:
            w.setparams(params)
            w.writeframes(raw_out)
    except Exception:
        pass


def _convert_to_wav(src, dst):
    """Convert an audio file to WAV using ffmpeg. Returns True on success."""
    import subprocess
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-acodec", "pcm_s16le", str(dst)],
            check=True, capture_output=True,
        )
        return True
    except FileNotFoundError:
        return False
    except subprocess.CalledProcessError:
        return False


def export_mod():
    """Export all swaps as a BepInEx mod package with plugin source."""
    import shutil

    swaps = load_swaps()
    if not swaps:
        return {"ok": False, "error": "No swaps to export"}

    data = load_mapping()
    refs_data = build_refs(data)

    # Clean and recreate export dir
    if EXPORT_DIR.exists():
        shutil.rmtree(EXPORT_DIR)

    audio_dir = EXPORT_DIR / "audio"
    audio_dir.mkdir(parents=True)

    # Copy custom files and build manifest
    # AudioClipLoader only supports WAV — convert non-WAV files
    manifest = {}
    convert_failures = []
    equalized = 0
    for original, custom_name in swaps.items():
        custom_src = CUSTOM_DIR / custom_name
        if not custom_src.exists():
            continue

        export_name = Path(original).stem + ".wav"
        dst = audio_dir / export_name

        if Path(custom_name).suffix.lower() == ".wav":
            shutil.copy2(custom_src, dst)
        else:
            if not _convert_to_wav(custom_src, dst):
                convert_failures.append(custom_name)
                continue

        # Volume equalization: match replacement loudness to original
        original_path = AUDIO_DIR / original
        if original_path.exists():
            orig_rms = _audio_rms(original_path)
            repl_rms = _wav_rms(dst)
            if orig_rms and repl_rms:
                gain = orig_rms / repl_rms
                if abs(gain - 1.0) > 0.05:  # only adjust if >5% difference
                    _apply_gain_wav(dst, gain)
                    equalized += 1

        clip_refs = refs_data.get(original, [])
        manifest[original] = {
            "replacement": export_name,
            "references": clip_refs,
        }

    # Write manifest
    with open(EXPORT_DIR / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)

    # Write C# plugin source
    _write_plugin_source(EXPORT_DIR)

    if convert_failures:
        return {
            "ok": False,
            "error": f"ffmpeg failed to convert {len(convert_failures)} file(s): "
                     + ", ".join(convert_failures)
                     + ". Install ffmpeg or use WAV files instead.",
        }

    return {
        "ok": True,
        "path": str(EXPORT_DIR),
        "swaps": len(manifest),
        "files": len(list(audio_dir.iterdir())),
        "equalized": equalized,
    }


def _write_plugin_source(export_dir):
    """Write the BepInEx plugin C# source + project file."""
    src_dir = export_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)

    # Plugin source
    plugin_cs = r'''using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text.RegularExpressions;
using BepInEx;
using BepInEx.Unity.IL2CPP;
using HarmonyLib;
using Il2CppInterop.Runtime;
using UnityEngine;

namespace ER2AudioMod
{
    [BepInPlugin("com.er2.audiomod", "ER2 Audio Mod", "1.0.0")]
    public class AudioModPlugin : BasePlugin
    {
        internal static string PluginDir;
        internal static Dictionary<string, AudioClip> ReplacementClips = new();
        internal static Dictionary<string, string> Manifest = new();
        internal new static BepInEx.Logging.ManualLogSource Log;

        // Keep IL2CPP objects alive so GC doesn't collect them
        private static List<Il2CppSystem.Action<AudioClip>> _pinnedDelegates = new();
        private static List<Il2CppSystem.Object> _pinnedObjects = new();
        private static int _pendingLoads = 0;
        private static int _completedLoads = 0;

        public override void Load()
        {
            Log = base.Log;
            PluginDir = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
            LoadManifest();
            Log.LogInfo($"Manifest loaded: {Manifest.Count} entries");
            LoadAudioClips();

            var harmony = new Harmony("com.er2.audiomod");
            harmony.PatchAll();

            Log.LogInfo($"ER2 Audio Mod loaded: {_pendingLoads} clips queued for async loading");
        }

        private void LoadManifest()
        {
            var manifestPath = Path.Combine(PluginDir, "manifest.json");
            if (!File.Exists(manifestPath)) return;

            var json = File.ReadAllText(manifestPath);
            var lines = json.Split('\n');
            string currentOriginal = null;
            foreach (var line in lines)
            {
                var trimmed = line.Trim();
                if (trimmed.StartsWith("\"") && trimmed.Contains("\": {"))
                {
                    currentOriginal = trimmed.Split('"')[1];
                }
                else if (trimmed.Contains("\"replacement\"") && currentOriginal != null)
                {
                    var replacement = trimmed.Split('"')[3];
                    Manifest[currentOriginal] = replacement;
                    currentOriginal = null;
                }
            }
        }

        private void LoadAudioClips()
        {
            var audioDir = Path.Combine(PluginDir, "audio");
            if (!Directory.Exists(audioDir)) return;

            foreach (var kvp in Manifest)
            {
                var filePath = Path.Combine(audioDir, kvp.Value);
                if (!File.Exists(filePath)) continue;

                try
                {
                    var bytes = File.ReadAllBytes(filePath);
                    Log.LogInfo($"[LOAD] {kvp.Key}: read {bytes.Length} bytes");

                    // Safe indexer copy into IL2CPP byte array
                    var il2cppBytes = new Il2CppInterop.Runtime.InteropTypes.Arrays.Il2CppStructArray<byte>(bytes.Length);
                    for (int i = 0; i < bytes.Length; i++)
                        il2cppBytes[i] = bytes[i];

                    var il2cppStream = new Il2CppSystem.IO.MemoryStream(il2cppBytes);

                    // Pin to prevent GC
                    _pinnedObjects.Add(il2cppBytes.Cast<Il2CppSystem.Object>());
                    _pinnedObjects.Add(il2cppStream.Cast<Il2CppSystem.Object>());

                    // Build lookup keys
                    var manifestKey = kvp.Key;
                    var stem = Path.GetFileNameWithoutExtension(manifestKey);
                    var gameKey = Regex.Replace(stem, @"_\d+$", "") + ".wav";

                    _pendingLoads++;
                    var callback = new System.Action<AudioClip>(clip =>
                    {
                        _completedLoads++;
                        if (clip != null)
                        {
                            clip.hideFlags |= HideFlags.DontUnloadUnusedAsset;
                            ReplacementClips[manifestKey] = clip;
                            if (gameKey != manifestKey)
                                ReplacementClips[gameKey] = clip;
                            Log.LogInfo($"[ASYNC] Loaded: {manifestKey} (also: {gameKey}) — {clip.length:F2}s, {clip.channels}ch, {clip.frequency}Hz");
                        }
                        else
                        {
                            Log.LogWarning($"[ASYNC] Null clip for {manifestKey}");
                        }
                        Log.LogInfo($"[ASYNC] Progress: {_completedLoads}/{_pendingLoads}");
                    });

                    var il2cppAction = Il2CppInterop.Runtime.DelegateSupport.ConvertDelegate<Il2CppSystem.Action<AudioClip>>(callback);
                    _pinnedDelegates.Add(il2cppAction);

                    AudioClipLoader.LoadAudioClipFromStreamAsync(
                        il2cppStream.Cast<Il2CppSystem.IO.Stream>(),
                        kvp.Value,
                        il2cppAction
                    );
                }
                catch (Exception ex)
                {
                    Log.LogError($"[LOAD] Failed {kvp.Value}: {ex.Message}");
                }
            }
        }
    }

    // Native IL2CPP field reader for GenericGun
    static class NativeFieldWriter
    {
        private static readonly Dictionary<string, int> _offsets = new();
        private static IntPtr _gunClass = IntPtr.Zero;

        static int GetOffset(string fieldName)
        {
            if (_offsets.TryGetValue(fieldName, out var cached))
                return cached;

            if (_gunClass == IntPtr.Zero)
                _gunClass = Il2CppClassPointerStore<GenericGun>.NativeClassPtr;

            var field = IL2CPP.il2cpp_class_get_field_from_name(_gunClass, fieldName);
            if (field == IntPtr.Zero)
            {
                _offsets[fieldName] = -1;
                return -1;
            }

            var offset = (int)IL2CPP.il2cpp_field_get_offset(field);
            _offsets[fieldName] = offset;
            return offset;
        }

        public static AudioClip ReadClip(GenericGun instance, string fieldName)
        {
            var offset = GetOffset(fieldName);
            if (offset < 0) return null;
            var ptr = Marshal.ReadIntPtr(instance.Pointer + offset);
            if (ptr == IntPtr.Zero) return null;
            return new AudioClip(ptr);
        }
    }

    [HarmonyPatch(typeof(GenericGun), nameof(GenericGun.PlayFireSound))]
    class Patch_PlayFireSound
    {
        internal static HashSet<int> _activeLoops = new();
        static HashSet<int> _logged = new();

        internal static AudioClip GetReplacement(GenericGun gun, string fieldName)
        {
            var clip = NativeFieldWriter.ReadClip(gun, fieldName);
            if (clip == null) return null;
            AudioModPlugin.ReplacementClips.TryGetValue(clip.name + ".wav", out var replacement);
            return replacement;
        }

        static bool HasAnyReplacement(GenericGun gun)
        {
            foreach (var field in new[] { "fireSound", "fireSound_loop" })
            {
                var clip = NativeFieldWriter.ReadClip(gun, field);
                if (clip != null && AudioModPlugin.ReplacementClips.ContainsKey(clip.name + ".wav"))
                    return true;
            }
            return false;
        }

        // Read the weapon's own audioSource at native offset 0xB8
        internal static AudioSource GetAudioSource(GenericGun gun)
        {
            var ptr = Marshal.ReadIntPtr(gun.Pointer + 0xB8);
            if (ptr == IntPtr.Zero) return null;
            return new AudioSource(ptr);
        }

        static bool Prefix(GenericGun __instance, Soldier user)
        {
            var clips = AudioModPlugin.ReplacementClips;
            if (clips.Count == 0) return true;
            if (!HasAnyReplacement(__instance)) return true;

            bool first = _logged.Add(__instance.GetInstanceID());
            var id = __instance.GetInstanceID();
            var gun = __instance.name;

            bool isClose = __instance.UseCloseSound(user);
            bool isLooped = __instance.UseLoopedSound(isClose);

            var audioSrc = GetAudioSource(__instance);
            if (audioSrc == null) return true;

            if (first)
                AudioModPlugin.Log.LogInfo($"[PLAY] {gun}: close={isClose}, looped={isLooped}");

            if (isLooped)
            {
                if (_activeLoops.Add(id))
                {
                    var startClip = GetReplacement(__instance, "fireSound_start");
                    if (startClip != null)
                        audioSrc.PlayOneShot(startClip, 1f);

                    var loopClip = GetReplacement(__instance, "fireSound_loop");
                    if (loopClip != null)
                    {
                        audioSrc.clip = loopClip;
                        audioSrc.loop = true;
                        audioSrc.Play();
                    }

                    if (first)
                        AudioModPlugin.Log.LogInfo($"[PLAY] {gun}: started looped fire");
                }
            }
            else
            {
                var singleClip = GetReplacement(__instance, "fireSound");
                if (singleClip != null)
                    audioSrc.PlayOneShot(singleClip, 1f);

                if (first)
                    AudioModPlugin.Log.LogInfo($"[PLAY] {gun}: played single");
            }

            return false;
        }
    }

    static class StopHelper
    {
        internal static void StopLoop(GenericGun gun)
        {
            var id = gun.GetInstanceID();
            if (!Patch_PlayFireSound._activeLoops.Remove(id))
                return;

            var audioSrc = Patch_PlayFireSound.GetAudioSource(gun);
            if (audioSrc != null)
            {
                audioSrc.Stop();
                audioSrc.loop = false;
            }

            var tailClip = Patch_PlayFireSound.GetReplacement(gun, "fireSound_tail");
            if (tailClip != null && audioSrc != null)
                audioSrc.PlayOneShot(tailClip, 1f);

            AudioModPlugin.Log.LogInfo($"[STOP] {gun.name}: stopped loop + played tail");
        }
    }

    [HarmonyPatch(typeof(GenericGun), nameof(GenericGun.ForceStopLoopedSound))]
    class Patch_ForceStopLoopedSound
    {
        static bool Prefix(GenericGun __instance)
        {
            if (!Patch_PlayFireSound._activeLoops.Contains(__instance.GetInstanceID()))
                return true;

            StopHelper.StopLoop(__instance);
            return false;
        }
    }

    [HarmonyPatch(typeof(GenericGun), nameof(GenericGun.StopUse))]
    class Patch_StopUse
    {
        static void Prefix(GenericGun __instance)
        {
            if (Patch_PlayFireSound._activeLoops.Contains(__instance.GetInstanceID()))
                StopHelper.StopLoop(__instance);
        }
    }

    // Native IL2CPP array reader for VoiceManager
    static class NativeVoiceReader
    {
        private static readonly Dictionary<string, int> _offsets = new();
        private static IntPtr _vmClass = IntPtr.Zero;

        static int GetOffset(string fieldName)
        {
            if (_offsets.TryGetValue(fieldName, out var cached))
                return cached;

            if (_vmClass == IntPtr.Zero)
                _vmClass = Il2CppInterop.Runtime.Il2CppClassPointerStore<VoiceManager>.NativeClassPtr;

            var field = IL2CPP.il2cpp_class_get_field_from_name(_vmClass, fieldName);
            if (field == IntPtr.Zero)
            {
                _offsets[fieldName] = -1;
                return -1;
            }

            var offset = (int)IL2CPP.il2cpp_field_get_offset(field);
            _offsets[fieldName] = offset;
            return offset;
        }

        public static Il2CppInterop.Runtime.InteropTypes.Arrays.Il2CppReferenceArray<AudioClip> ReadArray(VoiceManager instance, string fieldName)
        {
            var offset = GetOffset(fieldName);
            if (offset < 0) return null;
            var ptr = Marshal.ReadIntPtr(instance.Pointer + offset);
            if (ptr == IntPtr.Zero) return null;
            return new Il2CppInterop.Runtime.InteropTypes.Arrays.Il2CppReferenceArray<AudioClip>(ptr);
        }
    }

    [HarmonyPatch(typeof(VoiceManager), nameof(VoiceManager.GetVoice))]
    class Patch_GetVoice
    {
        static HashSet<int> _logged = new();

        static bool Prefix(VoiceManager __instance, VoiceManager.VoiceClip clip, int index, ref AudioClip __result)
        {
            var replacements = AudioModPlugin.ReplacementClips;
            if (replacements.Count == 0) return true;

            var fieldName = clip.ToString();
            var array = NativeVoiceReader.ReadArray(__instance, fieldName);
            if (array == null || array.Length == 0) return true;

            int i = (index >= 0 && index < array.Length)
                ? index
                : UnityEngine.Random.Range(0, array.Length);

            var originalClip = array[i];
            if (originalClip == null) return true;

            if (replacements.TryGetValue(originalClip.name + ".wav", out var replacement))
            {
                __result = replacement;

                if (_logged.Add(__instance.GetInstanceID() * 1000 + (int)clip))
                    AudioModPlugin.Log.LogInfo($"[VOICE] Swapped {fieldName}[{i}]: {originalClip.name} -> {replacement.name}");

                return false;
            }

            return true;
        }
    }
}
'''

    game_path = get_game_path().replace("/", "\\")
    if not game_path:
        game_path = "C:\\SET_YOUR_GAME_PATH"

    csproj = f'''<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net6.0</TargetFramework>
    <AssemblyName>ER2AudioMod</AssemblyName>
    <LangVersion>latest</LangVersion>
  </PropertyGroup>
  <ItemGroup>
    <Reference Include="BepInEx.Core">
      <HintPath>{game_path}\\BepInEx\\core\\BepInEx.Core.dll</HintPath>
    </Reference>
    <Reference Include="BepInEx.Unity.IL2CPP">
      <HintPath>{game_path}\\BepInEx\\core\\BepInEx.Unity.IL2CPP.dll</HintPath>
    </Reference>
    <Reference Include="HarmonyX">
      <HintPath>{game_path}\\BepInEx\\core\\0Harmony.dll</HintPath>
    </Reference>
    <Reference Include="UnityEngine.CoreModule">
      <HintPath>{game_path}\\BepInEx\\interop\\UnityEngine.CoreModule.dll</HintPath>
    </Reference>
    <Reference Include="UnityEngine.AudioModule">
      <HintPath>{game_path}\\BepInEx\\interop\\UnityEngine.AudioModule.dll</HintPath>
    </Reference>
    <Reference Include="Assembly-CSharp">
      <HintPath>{game_path}\\BepInEx\\interop\\Assembly-CSharp.dll</HintPath>
    </Reference>
    <Reference Include="Il2Cppmscorlib">
      <HintPath>{game_path}\\BepInEx\\interop\\Il2Cppmscorlib.dll</HintPath>
    </Reference>
    <Reference Include="Il2CppInterop.Runtime">
      <HintPath>{game_path}\\BepInEx\\core\\Il2CppInterop.Runtime.dll</HintPath>
    </Reference>
    <Reference Include="Il2CppSystem">
      <HintPath>{game_path}\\BepInEx\\interop\\Il2CppSystem.dll</HintPath>
    </Reference>
    <Reference Include="Il2CppSystem.Core">
      <HintPath>{game_path}\\BepInEx\\interop\\Il2CppSystem.Core.dll</HintPath>
    </Reference>
  </ItemGroup>
</Project>
'''

    with open(src_dir / "ER2AudioMod.cs", "w") as f:
        f.write(plugin_cs)
    with open(src_dir / "ER2AudioMod.csproj", "w") as f:
        f.write(csproj)
    with open(src_dir / "README.txt", "w") as f:
        f.write("Build this project with: dotnet build -c Release\n"
                "Then copy ER2AudioMod.dll to BepInEx/plugins/ER2AudioMod/\n"
                "along with manifest.json and the audio/ folder.\n")


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = unquote(self.path.split("?")[0])

        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        elif path == "/":
            self._send(PAGE_HTML, "text/html")
        elif path == "/api/mapping":
            self._send_json(load_mapping())
        elif path == "/api/refs":
            data = load_mapping()
            self._send_json(build_refs(data))
        elif path == "/api/swaps":
            self._send_json(load_swaps())
        elif path == "/api/audioinfo":
            self._send_json(_audio_info_cache)
        elif path == "/api/ro2":
            self._send_json(load_ro2_catalogue())
        elif path == "/api/profiles":
            self._send_json(list_profiles())
        elif path == "/api/config":
            self._send_json(load_config())
        elif path == "/api/rescan":
            data = scan_from_prefabs()
            save_mapping(data)
            self._send_json(data)
        elif path.startswith("/audio/"):
            self._serve_audio(unquote(path[7:]))
        elif path.startswith("/ro2audio/"):
            self._serve_ro2(unquote(path[10:]))
        elif path.startswith("/custom/"):
            self._serve_custom(unquote(path[8:]))
        else:
            self.send_error(404)

    def do_POST(self):
        path = unquote(self.path)

        if path == "/api/swap":
            self._handle_swap()
        elif path == "/api/swap/ro2":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            self._send_json(self._handle_swap_ro2(body))
        elif path == "/api/swap/revert":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            self._send_json(self._handle_revert(body))
        elif path == "/api/export":
            self._send_json(export_mod())
        elif path == "/api/profile/save":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            self._send_json(save_profile(body.get("name", "default")))
        elif path == "/api/profile/load":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            self._send_json(load_profile(body.get("name", "default")))
        elif path == "/api/profile/delete":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            self._send_json(delete_profile(body.get("name", "")))
        elif path == "/api/config":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            config = load_config()
            game_path = body.get("game_path", "")
            if game_path:
                valid, issues = validate_game_path(game_path)
                config["game_path"] = game_path
                config["game_path_valid"] = valid
                config["game_path_issues"] = issues
            config.update({k: v for k, v in body.items() if k != "game_path"})
            save_config(config)
            self._send_json(config)
        else:
            self.send_error(404)

    def _handle_swap(self):
        """Handle multipart file upload to swap a clip."""
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self.send_error(400, "Expected multipart/form-data")
            return

        # Parse boundary
        boundary = content_type.split("boundary=")[-1].encode()
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        # Simple multipart parser
        parts = body.split(b"--" + boundary)
        original = None
        file_data = None
        file_ext = None

        for part in parts:
            if b"Content-Disposition" not in part:
                continue
            header, _, content = part.partition(b"\r\n\r\n")
            content = content.rstrip(b"\r\n--")
            header_str = header.decode("utf-8", errors="replace")

            if 'name="original"' in header_str:
                original = content.decode().strip()
            elif 'name="file"' in header_str:
                file_data = content
                # Extract filename for extension
                if "filename=" in header_str:
                    fn = header_str.split('filename="')[1].split('"')[0]
                    file_ext = Path(fn).suffix or ".wav"

        if not original or not file_data:
            self.send_error(400, "Missing original or file")
            return

        # Save custom file
        CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
        # Use original filename stem + custom extension
        custom_name = Path(original).stem + "_custom" + (file_ext or ".wav")
        custom_path = CUSTOM_DIR / custom_name
        with open(custom_path, "wb") as f:
            f.write(file_data)

        # Update swaps manifest
        swaps = load_swaps()
        swaps[original] = custom_name
        save_swaps(swaps)

        self._send_json({"ok": True, "original": original, "custom": custom_name})

    def _handle_swap_ro2(self, body):
        """Swap an ER2 clip with an RO2 clip by copying it to custom/."""
        import shutil
        original = body.get("original")
        ro2_path = body.get("ro2_path")  # relative to RO2_DIR
        if not original or not ro2_path:
            return {"ok": False, "error": "Missing original or ro2_path"}

        src = RO2_DIR / ro2_path
        if not src.exists():
            return {"ok": False, "error": "RO2 file not found"}

        CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
        custom_name = Path(original).stem + "_ro2" + src.suffix
        shutil.copy2(src, CUSTOM_DIR / custom_name)

        swaps = load_swaps()
        swaps[original] = custom_name
        save_swaps(swaps)
        return {"ok": True, "original": original, "custom": custom_name}

    def _handle_revert(self, body):
        """Revert a swap back to original."""
        original = body.get("original")
        if not original:
            return {"ok": False, "error": "Missing original"}

        swaps = load_swaps()
        custom_name = swaps.pop(original, None)
        save_swaps(swaps)

        # Delete custom file
        if custom_name:
            custom_path = CUSTOM_DIR / custom_name
            if custom_path.exists():
                custom_path.unlink()

        return {"ok": True}

    def _serve_file(self, filepath):
        mime = mimetypes.guess_type(str(filepath))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(filepath.stat().st_size))
        self.end_headers()
        with open(filepath, "rb") as f:
            while chunk := f.read(65536):
                self.wfile.write(chunk)

    def _serve_audio(self, filename):
        filepath = AUDIO_DIR / filename
        if not filepath.exists() or not filepath.is_file():
            self.send_error(404)
            return
        self._serve_file(filepath)

    def _serve_ro2(self, filepath):
        full = RO2_DIR / filepath
        if not full.exists() or not full.is_file():
            self.send_error(404)
            return
        self._serve_file(full)

    def _serve_custom(self, filename):
        filepath = CUSTOM_DIR / filename
        if not filepath.exists() or not filepath.is_file():
            self.send_error(404)
            return
        self._serve_file(filepath)

    def _send_json(self, data):
        self._send(json.dumps(data), "application/json")

    def _send(self, content, content_type):
        body = content.encode() if isinstance(content, str) else content
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        import sys
        sys.stderr.write("%s - %s\n" % (self.client_address[0], fmt % args))
        sys.stderr.flush()


# ---------------------------------------------------------------------------
# HTML / JS
# ---------------------------------------------------------------------------

PAGE_HTML = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>ER2 Audio Editor</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #1a1a2e; color: #e0e0e0; display: flex; height: 100vh; }

  #sidebar { width: 280px; background: #16213e; border-right: 1px solid #0f3460;
             overflow-y: auto; flex-shrink: 0; display: flex; flex-direction: column; }
  #sidebar h2 { padding: 12px 16px 8px; font-size: 14px; color: #e94560;
                 text-transform: uppercase; letter-spacing: 1px; flex-shrink: 0; }
  .tabs { display: flex; flex-shrink: 0; border-bottom: 2px solid #0f3460; }
  .tab { flex: 1; padding: 8px 4px; text-align: center; font-size: 11px; cursor: pointer;
         color: #888; text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 2px solid transparent;
         margin-bottom: -2px; }
  .tab:hover { color: #e0e0e0; }
  .tab.active { color: #e94560; border-bottom-color: #e94560; }
  .tab .tab-count { font-size: 10px; opacity: 0.5; display: block; }
  .filter-bar { padding: 8px 12px; flex-shrink: 0; }
  .filter-bar input { width: 100%; background: #1a1a2e; border: 1px solid #0f3460;
                      color: #e0e0e0; padding: 6px 10px; border-radius: 4px; font-size: 12px; }
  #entity-list { overflow-y: auto; flex: 1; }
  .entity-item { padding: 10px 16px; cursor: pointer; border-bottom: 1px solid #0f3460;
                 font-size: 13px; display: flex; justify-content: space-between; }
  .entity-item:hover { background: #0f3460; }
  .entity-item.active { background: #e94560; color: white; }
  .entity-item .count { opacity: 0.5; font-size: 11px; }
  .entity-item.edited { border-left: 3px solid #e94560; }
  .entity-item.edited .count { color: #e94560; opacity: 1; }

  #main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
  #toolbar { padding: 12px 20px; background: #16213e; border-bottom: 1px solid #0f3460;
             display: flex; gap: 12px; align-items: center; flex-shrink: 0; }
  #toolbar button { background: #e94560; color: white; border: none; padding: 6px 14px;
                    border-radius: 4px; cursor: pointer; font-size: 13px; }
  #toolbar button:hover { background: #c73650; }
  #toolbar button.secondary { background: #0f3460; }
  #toolbar button.secondary:hover { background: #1a4a8a; }
  #stats { font-size: 12px; color: #888; }

  #content { flex: 1; overflow-y: auto; padding: 20px; }
  #entity-title { font-size: 20px; font-weight: 700; margin-bottom: 16px; color: white; }

  .action-group { margin-bottom: 24px; }
  .action-header { font-size: 13px; font-weight: 600; color: #e94560; padding: 6px 0;
                   border-bottom: 1px solid #0f3460; margin-bottom: 8px;
                   text-transform: uppercase; letter-spacing: 0.5px; }
  .clip-list { display: flex; flex-direction: column; gap: 4px; }
  .clip { background: #16213e; border: 1px solid #0f3460; border-radius: 6px;
          padding: 6px 12px; font-size: 12px; display: flex; align-items: center; gap: 10px; }
  .clip .name { min-width: 240px; overflow: hidden; text-overflow: ellipsis;
                white-space: nowrap; color: #aaa; }
  .clip audio { height: 28px; flex: 1; min-width: 200px; }
  .clip .actions { display: flex; gap: 4px; flex-shrink: 0; }
  .clip .actions button { background: #0f3460; border: none; color: #e0e0e0;
                          padding: 4px 8px; border-radius: 3px; cursor: pointer; font-size: 11px; }
  .clip .actions button:hover { background: #e94560; }
  .clip.swapped { border-color: #2ecc71; }
  .clip.swapped .name { color: #2ecc71; }
  .clip-info { font-size: 10px; color: #666; white-space: nowrap; }
  .badge { background: #e94560; color: white; font-size: 9px; padding: 1px 5px;
           border-radius: 8px; margin-left: 4px; vertical-align: middle; }
  .badge.warn { background: #e67e22; }
  .swap-info { font-size: 11px; color: #2ecc71; margin-left: 8px; }
  .ref-list { font-size: 11px; color: #888; max-height: 200px; overflow-y: auto;
              margin: 8px 0 16px; padding: 8px; background: #1a1a2e; border-radius: 4px; }
  .ref-list div { padding: 2px 0; }
  .ro2-results { flex: 1; overflow-y: auto; max-height: 400px; margin: 8px 0; }
  .ro2-item { display: flex; align-items: center; gap: 8px; padding: 6px 8px;
              border-bottom: 1px solid #0f3460; font-size: 12px; }
  .ro2-item:hover { background: #0f3460; }
  .ro2-item .ro2-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis;
                        white-space: nowrap; }
  .ro2-item .ro2-meta { color: #888; font-size: 10px; white-space: nowrap; }
  .ro2-item audio { height: 24px; width: 160px; flex-shrink: 0; }
  .ro2-item button { background: #e94560; border: none; color: white; padding: 3px 10px;
                     border-radius: 3px; cursor: pointer; font-size: 11px; flex-shrink: 0; }
  .ro2-tabs { display: flex; gap: 4px; margin-bottom: 8px; }
  .ro2-tabs button { background: #0f3460; border: none; color: #888; padding: 4px 10px;
                     border-radius: 3px; cursor: pointer; font-size: 11px; }
  .ro2-tabs button.active { background: #e94560; color: white; }

  .modal-bg { position: fixed; inset: 0; background: rgba(0,0,0,0.6); display: flex;
              align-items: center; justify-content: center; z-index: 100; }
  .modal { background: #16213e; border: 1px solid #0f3460; border-radius: 8px;
           padding: 24px; min-width: 400px; max-width: 800px; width: 90%; max-height: 90vh;
           display: flex; flex-direction: column; }
  .modal h3 { margin-bottom: 16px; color: #e94560; }
  .modal label { display: block; margin-bottom: 4px; font-size: 12px; color: #888; }
  .modal input { width: 100%; background: #1a1a2e; border: 1px solid #0f3460;
                 color: #e0e0e0; padding: 6px 10px; border-radius: 4px;
                 font-size: 13px; margin-bottom: 12px; }
  .modal .btn-row { display: flex; gap: 8px; justify-content: flex-end; }
  .modal button { padding: 6px 16px; border: none; border-radius: 4px; cursor: pointer;
                  font-size: 13px; }
  .modal .btn-cancel { background: #0f3460; color: #e0e0e0; }
  .modal .btn-ok { background: #e94560; color: white; }
</style>
</head>
<body>

<div id="sidebar">
  <h2>ER2 Audio Editor</h2>
  <div class="tabs" id="tabs"></div>
  <div class="filter-bar">
    <input type="text" id="entity-filter" placeholder="Filter entities..."
           oninput="renderSidebar()">
  </div>
  <div id="entity-list"></div>
</div>

<div id="main">
  <div id="toolbar">
    <button class="secondary" onclick="rescan()">Rescan</button>
    <button class="secondary" onclick="showProfiles()">Profiles</button>
    <button class="secondary" onclick="showSettings()">Settings</button>
    <button onclick="exportMod()">Export Mod</button>
    <input type="text" id="clip-filter" placeholder="Filter by filename or action..."
           oninput="renderEntity(currentEntity)" style="background:#1a1a2e;border:1px solid #0f3460;
           color:#e0e0e0;padding:6px 10px;border-radius:4px;font-size:12px;width:260px;">
    <span id="stats"></span>
  </div>
  <div id="content">
    <p style="padding:40px;color:#888;">Select an entity from the sidebar.</p>
  </div>
</div>

<div id="modal-root"></div>

<script>
let data = null;
let refs = {};
let swaps = {};
let audioInfo = {};
let ro2 = {};
let ro2Index = [];  // flat searchable list
let currentEntity = null;
let currentAudio = null;
let currentTab = 0;

var TAB_LABELS = ['Voices', 'Weapons', 'Vehicles', 'Other'];

function esc(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function escAttr(s) { return s.replace(/\\/g,'\\\\').replace(/'/g,"\\'"); }

function entityCategory(name) {
  if (name === 'Uncategorised') return 3;
  if (/^(Voice-|voice-|French Army)/i.test(name)) return 0;
  if (/\//.test(name) || name === '_Unassigned Vehicles') return 2;
  if (name === '_Unassigned Weapons') return 1;
  return 1;
}

function entitySort(a, b) {
  var pa = a.match(/^(?:Voice-|voice-)?([a-z]+)-?(\d+)$/i) || a.match(/^(.+?)\s*\(?(?:Voice\s*set\s*)?(\d+)\)?$/i);
  var pb = b.match(/^(?:Voice-|voice-)?([a-z]+)-?(\d+)$/i) || b.match(/^(.+?)\s*\(?(?:Voice\s*set\s*)?(\d+)\)?$/i);
  var na = pa ? pa[1].toLowerCase() : a.toLowerCase();
  var nb = pb ? pb[1].toLowerCase() : b.toLowerCase();
  if (na !== nb) return na.localeCompare(nb);
  var da = pa && pa[2] ? parseInt(pa[2]) : 0;
  var db = pb && pb[2] ? parseInt(pb[2]) : 0;
  return da - db;
}

function getTabCounts() {
  var counts = [0, 0, 0, 0];
  Object.keys(data.voices).forEach(function(e) {
    counts[entityCategory(e)]++;
  });
  return counts;
}

function switchTab(tab) {
  currentTab = tab;
  currentEntity = null;
  document.getElementById('entity-filter').value = '';
  renderTabs();
  renderSidebar();
  document.getElementById('content').innerHTML = '<p style="padding:40px;color:#888;">Select an entity from the sidebar.</p>';
}

function renderTabs() {
  var counts = getTabCounts();
  var html = '';
  for (var i = 0; i < TAB_LABELS.length; i++) {
    if (counts[i] === 0) continue;
    var cls = i === currentTab ? 'tab active' : 'tab';
    html += '<div class="' + cls + '" onclick="switchTab(' + i + ')">' +
      TAB_LABELS[i] + '<span class="tab-count">' + counts[i] + '</span></div>';
  }
  document.getElementById('tabs').innerHTML = html;
}

async function init() {
  var config = await (await fetch('/api/config')).json();
  if (!config.game_path) {
    showSetup();
    return;
  }
  var results = await Promise.all([
    fetch('/api/mapping').then(function(r) { return r.json(); }),
    fetch('/api/refs').then(function(r) { return r.json(); }),
    fetch('/api/swaps').then(function(r) { return r.json(); }),
    fetch('/api/audioinfo').then(function(r) { return r.json(); }),
    fetch('/api/ro2').then(function(r) { return r.json(); }),
  ]);
  data = results[0]; refs = results[1]; swaps = results[2]; audioInfo = results[3]; ro2 = results[4];
  buildRO2Index();
  renderTabs();
  renderSidebar();
  updateStats();
}

function showSetup(error) {
  var root = document.getElementById('modal-root');
  var errHtml = error ? '<p style="color:#e94560;margin-bottom:8px;">' + esc(error) + '</p>' : '';
  root.innerHTML = '<div class="modal-bg">' +
    '<div class="modal" style="max-width:500px;">' +
    '<h3>ER2 Audio Editor Setup</h3>' +
    '<p style="font-size:12px;color:#888;margin-bottom:16px;">Enter your Easy Red 2 install path. BepInEx must be installed and the game launched at least once.</p>' +
    errHtml +
    '<label>Game install path</label>' +
    '<input type="text" id="setup-path" placeholder="D:\\Steam\\steamapps\\common\\Easy Red 2" value="">' +
    '<div class="btn-row"><button class="btn-ok" onclick="doSetup()">Continue</button></div>' +
    '</div></div>';
}

async function doSetup() {
  var path = document.getElementById('setup-path').value.trim();
  if (!path) return;
  var res = await fetch('/api/config', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({game_path: path})
  });
  var config = await res.json();
  if (!config.game_path_valid) {
    var issues = (config.game_path_issues || []).join(', ');
    showSetup(issues || 'Invalid path');
    return;
  }
  document.getElementById('modal-root').innerHTML = '';
  init();
}

// Keyword map: ER2 action -> search terms for RO2 matching
var ACTION_KEYWORDS = {
  iVeBeenHit:'hurt wounded hit', gotHit:'hurt wounded hit', medic:'bandaging medic hurt',
  imReloading:'reloading', imUnderFire:'takingfire suppressed', AAAAAH:'dying scream',
  scream_long:'dying slow scream', yes:'confirm', yesSir:'confirm', watchYourFire:'friendlyfire',
  enemyInfantrySpotted:'infantryspotted spotted', enemyTankSpotted:'tankspotted spotted',
  enemyArtillerySpotted:'incomingarty artillery', enemyDown:'enemydeath killed',
  granade:'grenade throwing', thankYou:'thanks', coveringFire:'suppressing fire',
  imMoving:'moveto', imCharging:'charging', iSurrender:'', imTakingTheLead:'',
  moveThere:'moveto', attackThere:'attackobj attack', charge:'charging',
  attackThatTank:'attackvehicle tank', attackThatVehicle:'attackvehicle vehicle',
  followMe:'followme', letsSpreadOut:'', lineFormation:'', columnFormation:'',
  timeToRetreat:'retreat', getOut:'bailout getout', getIn:'',
  letsMoveTank:'forward tank', fireTank:'fire tank', gunReloadedTank:'',
  enemyHittedTank:'enemytankdeath hit', enemyDestroyedtank:'enemytankdeath destroyed',
  enemyMissedTank:'', enemyNotPenetratedTank:'',
  gotHitTank:'hit tank', radiomanIsDead:'', gunnerIsDead:'gunnerdead',
  commanderIsDead:'commanderdead', driverIsDead:'driverdead',
  illTakeHisSeat:'', getOutTankOnFire:'bailout fire', getOutTankDestroyed:'bailout destroyed',
  numbers:'', artillerySupportAt:'requestarty callingarty', tankSupportRequest:'requestsupport tank',
  fireSound:'fire single', fireSound_start:'fire start loop', fireSound_loop:'fire loop',
  fireSound_tail:'fire tail end', fireSound_distance:'fire distant',
  fireSound_distance_loop:'fire distant loop', fireSound_distance_tail:'fire distant tail',
  reload_sound_full:'reload', reload_sound_half:'reload', chamber_sound:'chamber bolt',
  boltaction_sound:'bolt action', engine_start:'engine start', engine_move:'engine move',
  engine_stop:'engine stop', crashSound:'crash impact',
};

function tokenize(s) {
  return s.toLowerCase().replace(/[^a-z0-9]/g,' ').split(/\s+/).filter(function(t){return t.length>1;});
}

function buildRO2Index() {
  ro2Index = [];
  for (var game in ro2) {
    var cats = ro2[game];
    // voices
    for (var vid in (cats.voices||{})) {
      var voice = cats.voices[vid];
      for (var action in voice.actions) {
        var clips = voice.actions[action];
        for (var i=0; i<clips.length; i++) {
          ro2Index.push({
            path: clips[i],
            game: game,
            category: 'voice',
            entity: vid,
            action: action,
            faction: voice.faction,
            language: voice.language,
            vtype: voice.type,
            tokens: tokenize(action + ' ' + voice.faction + ' ' + voice.type + ' ' + vid),
          });
        }
      }
    }
    // weapons
    for (var wid in (cats.weapons||{})) {
      var w = cats.weapons[wid];
      var wclips = w.clips||[];
      for (var i=0; i<wclips.length; i++) {
        var fname = wclips[i].split('/').pop().replace(/\.\w+$/,'');
        ro2Index.push({
          path: wclips[i],
          game: game,
          category: 'weapon',
          entity: wid,
          action: fname,
          faction: '',
          language: '',
          vtype: w.type,
          tokens: tokenize(fname + ' ' + wid + ' ' + w.type),
        });
      }
    }
    // vehicles
    for (var vid2 in (cats.vehicles||{})) {
      var veh = cats.vehicles[vid2];
      var vclips = veh.clips||[];
      for (var i=0; i<vclips.length; i++) {
        var fname2 = vclips[i].split('/').pop().replace(/\.\w+$/,'');
        ro2Index.push({
          path: vclips[i],
          game: game,
          category: 'vehicle',
          entity: vid2,
          action: fname2,
          faction: '',
          language: '',
          vtype: '',
          tokens: tokenize(fname2 + ' ' + vid2),
        });
      }
    }
  }
}

function scoreRO2(item, entity, action, clip) {
  var score = 0;
  // Action keyword matching
  var kw = ACTION_KEYWORDS[action] || '';
  var searchTerms = tokenize(action + ' ' + kw);
  for (var i=0; i<searchTerms.length; i++) {
    for (var j=0; j<item.tokens.length; j++) {
      if (item.tokens[j].indexOf(searchTerms[i]) !== -1 || searchTerms[i].indexOf(item.tokens[j]) !== -1) {
        score += 10;
      }
    }
  }
  // Faction matching from entity name
  var el = entity.toLowerCase();
  if (item.faction) {
    var fl = item.faction.toLowerCase();
    if (el.indexOf(fl.substr(0,3)) !== -1) score += 20;
  }
  // Category matching: voice entity -> voice clips, weapon -> weapon clips
  if (el.indexOf('voice') !== -1 && item.category === 'voice') score += 5;
  if (item.category === 'weapon' && (action.indexOf('fire') !== -1 || action.indexOf('reload') !== -1)) score += 5;
  if (item.category === 'vehicle' && (action.indexOf('engine') !== -1 || action.indexOf('crash') !== -1)) score += 5;
  // Type matching: tank voice -> tank clips
  if (el.indexOf('tank') !== -1 && item.vtype === 'tank') score += 15;
  if (action.indexOf('Tank') !== -1 && item.vtype === 'tank') score += 10;
  return score;
}

async function reloadAll() {
  var results = await Promise.all([
    fetch('/api/mapping').then(function(r) { return r.json(); }),
    fetch('/api/refs').then(function(r) { return r.json(); }),
    fetch('/api/swaps').then(function(r) { return r.json(); }),
  ]);
  data = results[0]; refs = results[1]; swaps = results[2];
  renderTabs();
  renderSidebar();
  updateStats();
  if (currentEntity) renderEntity(currentEntity);
}

function fmtInfo(clip) {
  var info = audioInfo[clip];
  if (!info) return '';
  var parts = [];
  if (info.duration != null) parts.push(info.duration + 's');
  if (info.sample_rate) parts.push((info.sample_rate / 1000) + 'kHz');
  if (info.bit_depth) parts.push(info.bit_depth + 'bit');
  if (info.channels) parts.push(info.channels === 1 ? 'mono' : 'stereo');
  if (!parts.length) parts.push((info.size / 1024).toFixed(0) + 'KB');
  return parts.join(' \u00b7 ');
}

function entitySwapCount(entity) {
  var actions = data.voices[entity];
  if (!actions) return 0;
  var n = 0;
  for (var action in actions) {
    var clips = actions[action];
    for (var i = 0; i < clips.length; i++) {
      if (swaps[clips[i]]) n++;
    }
  }
  return n;
}

function renderSidebar() {
  var list = document.getElementById('entity-list');
  var filter = document.getElementById('entity-filter').value.toLowerCase();
  var entities = Object.keys(data.voices).sort(entitySort);

  var filtered = entities.filter(function(e) {
    return entityCategory(e) === currentTab && e.toLowerCase().indexOf(filter) !== -1;
  });

  var html = '';
  for (var i = 0; i < filtered.length; i++) {
    var e = filtered[i];
    var total = Object.values(data.voices[e]).reduce(function(s, c) { return s + c.length; }, 0);
    var edited = entitySwapCount(e);
    var cls = 'entity-item';
    if (e === currentEntity) cls += ' active';
    if (edited > 0) cls += ' edited';
    var countLabel = edited > 0 ? edited + '/' + total : '' + total;
    html += '<div class="' + cls + '" onclick="selectEntity(\'' + escAttr(e) + '\')">' +
      '<span>' + esc(e) + '</span><span class="count">' + countLabel + '</span></div>';
  }
  list.innerHTML = html;
}

function updateStats() {
  var v = data.voices;
  var ents = Object.keys(v).length;
  var clips = Object.values(v).reduce(function(a, ent) {
    return a + Object.values(ent).reduce(function(b, c) { return b + c.length; }, 0);
  }, 0);
  document.getElementById('stats').textContent =
    ents + ' entities \u2022 ' + clips + ' clips';
}

function selectEntity(entity) {
  currentEntity = entity;
  renderSidebar();
  renderEntity(entity);
}

function playClip(el) {
  if (currentAudio && currentAudio !== el) {
    currentAudio.pause();
    currentAudio.currentTime = 0;
  }
  currentAudio = el;
}

function renderEntity(entity) {
  var content = document.getElementById('content');
  var actions = data.voices[entity];
  if (!actions) { content.innerHTML = '<p>Entity not found.</p>'; return; }

  var filterEl = document.getElementById('clip-filter');
  var filter = filterEl ? filterEl.value.toLowerCase() : '';
  var sorted = Object.keys(actions).sort();
  var html = '<div id="entity-title">' + esc(entity) + '</div>';

  for (var i = 0; i < sorted.length; i++) {
    var action = sorted[i];
    var allClips = actions[action];
    var clips = filter ? allClips.filter(function(c) {
      return c.toLowerCase().indexOf(filter) !== -1 || action.toLowerCase().indexOf(filter) !== -1;
    }) : allClips;
    if (filter && clips.length === 0) continue;
    html += '<div class="action-group">';
    html += '<div class="action-header">' + esc(action) + ' (' + clips.length + ')</div>';
    html += '<div class="clip-list">';
    for (var j = 0; j < clips.length; j++) {
      var clip = clips[j];
      var ext = clip.split('.').pop().toLowerCase();
      var mime = ext === 'ogg' ? 'audio/ogg' : 'audio/wav';
      var refCount = (refs[clip] || []).length;
      var isSwapped = !!swaps[clip];
      var swapClass = isSwapped ? ' swapped' : '';
      var customFile = swaps[clip] || '';

      html += '<div class="clip' + swapClass + '">' +
        '<span class="name" title="' + esc(clip) + '">' + esc(clip) + '</span>' +
        '<span class="clip-info">' + fmtInfo(clip) + '</span>';

      if (refCount > 1) {
        html += '<span class="badge warn" style="cursor:pointer" onclick="showRefs(\'' + escAttr(clip) + '\')">' + refCount + ' refs</span>';
      }

      if (isSwapped) {
        html += '<span class="swap-info" title="' + esc(customFile) + '">\u2192 ' + esc(customFile) + '</span>' +
          '<audio controls preload="none" onplay="playClip(this)">' +
          '<source src="/custom/' + encodeURIComponent(customFile) + '" type="' + mime + '">' +
          '</audio>';
      } else {
        html += '<audio controls preload="none" onplay="playClip(this)">' +
          '<source src="/audio/' + encodeURIComponent(clip) + '" type="' + mime + '">' +
          '</audio>';
      }

      html += '<div class="actions">';
      if (isSwapped) {
        html += '<button onclick="revertSwap(\'' + escAttr(clip) + '\')">Revert</button>';
      }
      html += '<button onclick="swapClip(\'' + escAttr(clip) + '\',\'' + escAttr(entity) + '\',\'' + escAttr(action) + '\')">Swap</button>' +
        '</div></div>';
    }
    html += '</div></div>';
  }

  content.innerHTML = html;
}

function closeModal() {
  document.getElementById('modal-root').innerHTML = '';
}

function showRefs(clip) {
  var clipRefs = refs[clip] || [];
  var root = document.getElementById('modal-root');
  var html = '<div class="modal-bg" onclick="if(event.target===this)closeModal()">' +
    '<div class="modal">' +
    '<h3>References</h3>' +
    '<p style="font-size:12px;color:#888;margin-bottom:12px;">' + esc(clip) + ' is used by ' + clipRefs.length + ' entities:</p>' +
    '<div class="ref-list">';
  for (var i = 0; i < clipRefs.length; i++) {
    html += '<div>' + esc(clipRefs[i].entity) + ' <span style="color:#555">/</span> ' + esc(clipRefs[i].action) + '</div>';
  }
  html += '</div>' +
    '<div class="btn-row"><button class="btn-ok" onclick="closeModal()">OK</button></div>' +
    '</div></div>';
  root.innerHTML = html;
}

var _swapCtx = {};

function swapClip(original, entity, action) {
  _swapCtx = { original: original, entity: entity || '', action: action || '' };
  var clipRefs = refs[original] || [];
  var root = document.getElementById('modal-root');

  var refHtml = '';
  if (clipRefs.length > 1) {
    refHtml = '<div style="margin-bottom:8px;"><span class="badge warn">' + clipRefs.length + ' references</span>' +
      ' Swapping this file affects ' + clipRefs.length + ' entities</div>';
  }

  root.innerHTML = '<div class="modal-bg" onclick="if(event.target===this)closeModal()">' +
    '<div class="modal">' +
    '<h3>Swap: ' + esc(original) + '</h3>' +
    refHtml +
    '<div style="display:flex;gap:8px;align-items:center;margin-bottom:12px;">' +
    '<span style="font-size:11px;color:#888;">Original:</span>' +
    '<audio controls preload="none" style="height:28px;flex:1;"><source src="/audio/' + encodeURIComponent(original) + '"></audio>' +
    '</div>' +
    '<div class="ro2-tabs">' +
    '<button class="active" onclick="swapTabRO2()">RO2 / Rising Storm</button>' +
    '<button onclick="swapTabFile()">Upload File</button>' +
    '</div>' +
    '<div id="swap-content"></div>' +
    '<div class="btn-row" style="margin-top:8px;">' +
    '<button class="btn-cancel" onclick="closeModal()">Cancel</button>' +
    '</div></div></div>';

  swapTabRO2();
}

function getRO2VoiceActors() {
  var actors = {};
  for (var i = 0; i < ro2Index.length; i++) {
    var item = ro2Index[i];
    if (item.category === 'voice') {
      var label = item.entity + ' (' + item.game + (item.faction ? ' \u00b7 ' + item.faction : '') + ')';
      actors[item.game + '/' + item.entity] = label;
    }
  }
  var sorted = Object.keys(actors).sort();
  return sorted.map(function(k) { return { key: k, label: actors[k] }; });
}

function swapTabRO2() {
  var tabs = document.querySelectorAll('.ro2-tabs button');
  tabs[0].className = 'active'; tabs[1].className = '';
  var sc = document.getElementById('swap-content');
  var actors = getRO2VoiceActors();
  var filterHtml = '<select id="ro2-voice-filter" onchange="renderRO2Results()" ' +
    'style="background:#1a1a2e;border:1px solid #0f3460;color:#e0e0e0;padding:6px 10px;' +
    'border-radius:4px;font-size:12px;flex:1;min-width:0;">' +
    '<option value="">All voice actors</option>';
  for (var i = 0; i < actors.length; i++) {
    filterHtml += '<option value="' + escAttr(actors[i].key) + '">' + esc(actors[i].label) + '</option>';
  }
  filterHtml += '</select>';
  sc.innerHTML =
    '<div style="display:flex;gap:6px;margin-bottom:4px;">' +
    '<input type="text" id="ro2-search" placeholder="Search RO2 clips..." ' +
    'oninput="renderRO2Results()" style="flex:1;background:#1a1a2e;border:1px solid #0f3460;' +
    'color:#e0e0e0;padding:6px 10px;border-radius:4px;font-size:12px;">' +
    filterHtml + '</div>' +
    '<div class="ro2-results" id="ro2-results"></div>';
  renderRO2Results();
}

function swapTabFile() {
  var tabs = document.querySelectorAll('.ro2-tabs button');
  tabs[0].className = ''; tabs[1].className = 'active';
  var sc = document.getElementById('swap-content');
  sc.innerHTML =
    '<label>Replacement file (.wav / .ogg)</label>' +
    '<input type="file" id="swap-file" accept=".wav,.ogg,.mp3" style="margin-bottom:8px;font-size:12px;">' +
    '<div id="swap-preview"></div>' +
    '<div class="btn-row" style="margin-top:8px;">' +
    '<button class="btn-ok" onclick="doSwapFile()">Swap with file</button></div>';
  document.getElementById('swap-file').addEventListener('change', function(e) {
    var file = e.target.files[0];
    if (file) {
      var url = URL.createObjectURL(file);
      document.getElementById('swap-preview').innerHTML =
        '<audio controls style="width:100%;height:32px;margin-bottom:8px;"><source src="' + url + '"></audio>';
    }
  });
}

function renderRO2Results() {
  var query = (document.getElementById('ro2-search') || {}).value || '';
  var queryTokens = tokenize(query);
  var voiceFilter = (document.getElementById('ro2-voice-filter') || {}).value || '';
  var entity = _swapCtx.entity;
  var action = _swapCtx.action;
  var original = _swapCtx.original;

  // Score and sort
  var scored = [];
  for (var i = 0; i < ro2Index.length; i++) {
    var item = ro2Index[i];
    // Apply voice actor filter
    if (voiceFilter && (item.category !== 'voice' || (item.game + '/' + item.entity) !== voiceFilter)) continue;
    var s = scoreRO2(item, entity, action, original);
    // Apply search filter
    if (queryTokens.length > 0) {
      var matched = 0;
      for (var q = 0; q < queryTokens.length; q++) {
        for (var t = 0; t < item.tokens.length; t++) {
          if (item.tokens[t].indexOf(queryTokens[q]) !== -1) { matched++; break; }
        }
      }
      if (matched === 0) continue;
      s += matched * 15;
    }
    if (s > 0 || queryTokens.length > 0 || voiceFilter) scored.push({ item: item, score: s });
  }

  scored.sort(function(a, b) { return b.score - a.score; });
  var results = scored.slice(0, 50);

  var container = document.getElementById('ro2-results');
  if (!results.length) {
    container.innerHTML = '<p style="color:#888;padding:12px;">No matches. Try different search terms.</p>';
    return;
  }
  var html = '';
  for (var j = 0; j < results.length; j++) {
    var it = results[j].item;
    var fname = it.path.split('/').pop();
    var meta = it.game + ' \u00b7 ' + it.category + ' \u00b7 ' + it.entity;
    html += '<div class="ro2-item">' +
      '<span class="ro2-name" title="' + esc(it.path) + '">' + esc(fname) + '</span>' +
      '<span class="ro2-meta">' + esc(meta) + '</span>' +
      '<audio controls preload="none" onplay="playClip(this)"><source src="/ro2audio/' + encodeURIComponent(it.path) + '" type="audio/ogg"></audio>' +
      '<button onclick="doSwapRO2(\'' + escAttr(it.path) + '\')">Use</button>' +
      '</div>';
  }
  container.innerHTML = html;
}

async function doSwapRO2(ro2Path) {
  await fetch('/api/swap/ro2', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ original: _swapCtx.original, ro2_path: ro2Path })
  });
  closeModal();
  await reloadAll();
}

async function doSwapFile() {
  var fileInput = document.getElementById('swap-file');
  if (!fileInput.files.length) return;
  var formData = new FormData();
  formData.append('original', _swapCtx.original);
  formData.append('file', fileInput.files[0]);
  await fetch('/api/swap', { method: 'POST', body: formData });
  closeModal();
  await reloadAll();
}

async function revertSwap(original) {
  await fetch('/api/swap/revert', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ original: original })
  });
  await reloadAll();
}

async function showProfiles() {
  var profiles = await (await fetch('/api/profiles')).json();
  var swapCount = Object.keys(swaps).length;
  var root = document.getElementById('modal-root');

  var listHtml = '';
  if (profiles.length === 0) {
    listHtml = '<p style="color:#888;padding:8px;">No saved profiles yet.</p>';
  } else {
    for (var i = 0; i < profiles.length; i++) {
      listHtml += '<div style="display:flex;justify-content:space-between;align-items:center;padding:6px 8px;border-bottom:1px solid #0f3460;">' +
        '<span style="font-size:13px;">' + esc(profiles[i]) + '</span>' +
        '<div style="display:flex;gap:4px;">' +
        '<button class="btn-ok" style="padding:3px 10px;font-size:11px;" onclick="doLoadProfile(\'' + escAttr(profiles[i]) + '\')">Load</button>' +
        '<button class="btn-cancel" style="padding:3px 10px;font-size:11px;" onclick="doDeleteProfile(\'' + escAttr(profiles[i]) + '\')">Delete</button>' +
        '</div></div>';
    }
  }

  root.innerHTML = '<div class="modal-bg" onclick="if(event.target===this)closeModal()">' +
    '<div class="modal" style="max-width:500px;">' +
    '<h3>Profiles</h3>' +
    '<p style="font-size:12px;color:#888;margin-bottom:12px;">Current session: ' + swapCount + ' swaps</p>' +
    '<div style="display:flex;gap:8px;margin-bottom:16px;">' +
    '<input type="text" id="profile-name" placeholder="Profile name..." style="flex:1;">' +
    '<button class="btn-ok" onclick="doSaveProfile()">Save Current</button>' +
    '</div>' +
    '<div style="max-height:300px;overflow-y:auto;">' + listHtml + '</div>' +
    '<div class="btn-row" style="margin-top:12px;"><button class="btn-cancel" onclick="closeModal()">Close</button></div>' +
    '</div></div>';
}

async function doSaveProfile() {
  var name = document.getElementById('profile-name').value.trim();
  if (!name) return;
  var res = await fetch('/api/profile/save', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({name: name})
  });
  var result = await res.json();
  if (result.ok) showProfiles();
}

async function doLoadProfile(name) {
  await fetch('/api/profile/load', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({name: name})
  });
  closeModal();
  await reloadAll();
}

async function doDeleteProfile(name) {
  await fetch('/api/profile/delete', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({name: name})
  });
  showProfiles();
}

async function showSettings() {
  var config = await (await fetch('/api/config')).json();
  var root = document.getElementById('modal-root');
  var validHtml = config.game_path_valid ?
    '<span style="color:#2ecc71;font-size:12px;">Valid</span>' :
    '<span style="color:#e94560;font-size:12px;">' + esc((config.game_path_issues||[]).join(', ')) + '</span>';

  root.innerHTML = '<div class="modal-bg" onclick="if(event.target===this)closeModal()">' +
    '<div class="modal" style="max-width:500px;">' +
    '<h3>Settings</h3>' +
    '<label>Easy Red 2 install path</label>' +
    '<input type="text" id="settings-path" value="' + esc(config.game_path || '') + '">' +
    '<div style="margin-bottom:16px;">' + validHtml + '</div>' +
    '<div class="btn-row">' +
    '<button class="btn-cancel" onclick="closeModal()">Cancel</button>' +
    '<button class="btn-ok" onclick="saveSettings()">Save</button>' +
    '</div></div></div>';
}

async function saveSettings() {
  var path = document.getElementById('settings-path').value.trim();
  var res = await fetch('/api/config', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({game_path: path})
  });
  var config = await res.json();
  if (!config.game_path_valid) {
    showSettings();
    return;
  }
  closeModal();
}

async function exportMod() {
  var res = await fetch('/api/export', { method: 'POST' });
  var result = await res.json();
  var root = document.getElementById('modal-root');
  if (result.ok) {
    root.innerHTML = '<div class="modal-bg" onclick="if(event.target===this)closeModal()">' +
      '<div class="modal">' +
      '<h3>Mod Exported</h3>' +
      '<p style="margin-bottom:12px;">' + result.swaps + ' swaps exported, ' + result.files + ' audio files' + (result.equalized ? ', ' + result.equalized + ' volume-equalized' : '') + '.</p>' +
      '<p style="font-size:12px;color:#888;margin-bottom:8px;">Output: <code>' + esc(result.path) + '</code></p>' +
      '<p style="font-size:12px;color:#888;margin-bottom:8px;">1. Build: <code>cd src &amp;&amp; dotnet build -c Release</code></p>' +
      '<p style="font-size:12px;color:#888;margin-bottom:8px;">2. Copy the built <code>ER2AudioMod.dll</code> into the export folder alongside <code>manifest.json</code> + <code>audio/</code></p>' +
      '<p style="font-size:12px;color:#888;margin-bottom:16px;">3. Copy the entire <code>ER2AudioMod/</code> folder into your game\'s <code>BepInEx/plugins/</code></p>' +
      '<div class="btn-row"><button class="btn-ok" onclick="closeModal()">OK</button></div>' +
      '</div></div>';
  } else {
    root.innerHTML = '<div class="modal-bg" onclick="if(event.target===this)closeModal()">' +
      '<div class="modal">' +
      '<h3>Export Failed</h3>' +
      '<p style="color:#e94560;">' + esc(result.error || 'Unknown error') + '</p>' +
      '<div class="btn-row"><button class="btn-ok" onclick="closeModal()">OK</button></div>' +
      '</div></div>';
  }
}

async function rescan() {
  data = await (await fetch('/api/rescan')).json();
  refs = await (await fetch('/api/refs')).json();
  renderTabs();
  renderSidebar();
  updateStats();
  if (currentEntity) renderEntity(currentEntity);
}

init();
</script>
</body>
</html>"""

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import time

    print(f"[1/4] Building ER2 mapping from prefabs...")
    t0 = time.time()
    mapping_data = load_mapping()
    voices = mapping_data.get("voices", {})
    total = sum(len(clips) for ent in voices.values() for clips in ent.values())
    uncat = len(voices.get("Uncategorised", {}).get("misc", []))
    print(f"      {len(voices)} entities, {total} clips ({time.time()-t0:.1f}s)")

    print(f"[2/4] Building cross-references...")
    t0 = time.time()
    _refs = build_refs(mapping_data)
    shared = sum(1 for v in _refs.values() if len(v) > 1)
    print(f"      {len(_refs)} clips, {shared} shared ({time.time()-t0:.1f}s)")

    print(f"[3/4] Scanning RO2/RS catalogue...")
    t0 = time.time()
    _ro2 = load_ro2_catalogue()
    ro2_total = sum(
        sum(len(c) for c in item.get("actions", {}).values()) + len(item.get("clips", []))
        for cats in _ro2.values() for items in cats.values() for item in items.values()
    )
    print(f"      {ro2_total} RO2/RS clips ({time.time()-t0:.1f}s)")

    print(f"[4/4] Building audio info cache...")
    t0 = time.time()
    _audio_info_cache = build_audio_info_cache()
    print(f"      {len(_audio_info_cache)} files scanned ({time.time()-t0:.1f}s)")

    print(f"\nStarting server at http://localhost:{PORT}")

    class ReusableHTTPServer(HTTPServer):
        allow_reuse_address = True

    server = ReusableHTTPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()
