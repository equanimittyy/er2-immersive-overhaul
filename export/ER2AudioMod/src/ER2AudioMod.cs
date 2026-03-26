using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
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

        public override void Load()
        {
            Log = base.Log;
            PluginDir = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
            LoadManifest();
            LoadAudioClips();

            var harmony = new Harmony("com.er2.audiomod");
            harmony.PatchAll();

            Log.LogInfo($"ER2 Audio Mod loaded: {ReplacementClips.Count} replacements");
        }

        private void LoadManifest()
        {
            var manifestPath = Path.Combine(PluginDir, "manifest.json");
            if (!File.Exists(manifestPath)) return;

            // Simple JSON parse for {"original": {"replacement": "file.wav", ...}}
            var json = File.ReadAllText(manifestPath);
            // Using Il2Cpp-safe parsing - split by entries
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
                    var il2cppStream = new Il2CppSystem.IO.MemoryStream(bytes);
                    var originalKey = kvp.Key;

                    AudioClipLoader.LoadAudioClipFromStreamAsync(
                        il2cppStream.Cast<Il2CppSystem.IO.Stream>(),
                        kvp.Value,
                        (Il2CppSystem.Action<AudioClip>)new System.Action<AudioClip>(clip =>
                        {
                            if (clip != null)
                            {
                                ReplacementClips[originalKey] = clip;
                            }
                        })
                    );
                }
                catch (Exception ex)
                {
                    Log.LogError($"Failed to load {kvp.Value}: {ex.Message}");
                }
            }
        }
    }

    // Patch weapon sounds
    [HarmonyPatch(typeof(GenericGun), nameof(GenericGun.PlayFireSound))]
    class Patch_PlayFireSound
    {
        static void Prefix(GenericGun __instance)
        {
            var clips = AudioModPlugin.ReplacementClips;

            if (__instance.fireSound != null && clips.TryGetValue(__instance.fireSound.name + ".wav", out var c1))
                __instance.fireSound = c1;
            if (__instance.fireSound_loop != null && clips.TryGetValue(__instance.fireSound_loop.name + ".wav", out var c2))
                __instance.fireSound_loop = c2;
            if (__instance.fireSound_tail != null && clips.TryGetValue(__instance.fireSound_tail.name + ".wav", out var c3))
                __instance.fireSound_tail = c3;
            if (__instance.fireSound_start != null && clips.TryGetValue(__instance.fireSound_start.name + ".wav", out var c4))
                __instance.fireSound_start = c4;
            if (__instance.fireSound_distance != null && clips.TryGetValue(__instance.fireSound_distance.name + ".wav", out var c5))
                __instance.fireSound_distance = c5;
            if (__instance.fireSound_distance_loop != null && clips.TryGetValue(__instance.fireSound_distance_loop.name + ".wav", out var c6))
                __instance.fireSound_distance_loop = c6;
            if (__instance.fireSound_distance_tail != null && clips.TryGetValue(__instance.fireSound_distance_tail.name + ".wav", out var c7))
                __instance.fireSound_distance_tail = c7;
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
