using System;
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

    // Patch voice sounds (placeholder)
    [HarmonyPatch(typeof(VoiceManager), nameof(VoiceManager.GetVoice))]
    class Patch_GetVoice
    {
        static bool Prefix(VoiceManager.VoiceClip clip, int index, ref AudioClip __result)
        {
            return true; // run original for now
        }
    }
}
