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
MAPPING_FILE = DATA_DIR / "audio_mapping.json"
PORT = 8420

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


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = unquote(self.path.split("?")[0])

        if path == "/":
            self._send(PAGE_HTML, "text/html")
        elif path == "/api/mapping":
            self._send_json(load_mapping())
        elif path == "/api/rescan":
            data = scan_from_prefabs()
            save_mapping(data)
            self._send_json(data)
        elif path.startswith("/audio/"):
            self._serve_audio(unquote(path[7:]))
        else:
            self.send_error(404)

    def do_POST(self):
        path = unquote(self.path)
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        if path == "/api/move":
            self._send_json(self._handle_move(body))
        else:
            self.send_error(404)

    def _handle_move(self, body):
        data = load_mapping()
        voices = data["voices"]
        fn = body.get("filename")
        fe, fa = body.get("from_entity"), body.get("from_action")
        te, ta = body.get("to_entity"), body.get("to_action")

        if fe in voices and fa in voices[fe]:
            clips = voices[fe][fa]
            if fn in clips:
                clips.remove(fn)
            if not clips:
                del voices[fe][fa]
            if not voices[fe]:
                del voices[fe]

        voices.setdefault(te, {}).setdefault(ta, []).append(fn)
        save_mapping(data)
        return {"ok": True}

    def _serve_audio(self, filename):
        filepath = AUDIO_DIR / filename
        if not filepath.exists() or not filepath.is_file():
            self.send_error(404)
            return
        mime = mimetypes.guess_type(str(filepath))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(filepath.stat().st_size))
        self.end_headers()
        with open(filepath, "rb") as f:
            while chunk := f.read(65536):
                self.wfile.write(chunk)

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
        pass


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

  .modal-bg { position: fixed; inset: 0; background: rgba(0,0,0,0.6); display: flex;
              align-items: center; justify-content: center; z-index: 100; }
  .modal { background: #16213e; border: 1px solid #0f3460; border-radius: 8px;
           padding: 24px; min-width: 400px; }
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
    <button class="secondary" onclick="rescan()">Rescan Files</button>
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
  data = await (await fetch('/api/mapping')).json();
  renderTabs();
  renderSidebar();
  updateStats();
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
    var count = Object.values(data.voices[e]).reduce(function(s, c) { return s + c.length; }, 0);
    var cls = e === currentEntity ? 'entity-item active' : 'entity-item';
    html += '<div class="' + cls + '" onclick="selectEntity(\'' + escAttr(e) + '\')">' +
      '<span>' + esc(e) + '</span><span class="count">' + count + '</span></div>';
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
      html += '<div class="clip">' +
        '<span class="name" title="' + esc(clip) + '">' + esc(clip) + '</span>' +
        '<audio controls preload="none" onplay="playClip(this)">' +
        '<source src="/audio/' + encodeURIComponent(clip) + '" type="' + mime + '">' +
        '</audio>' +
        '<div class="actions">' +
        '<button onclick="moveClip(\'' + escAttr(entity) + '\',\'' + escAttr(action) + '\',\'' + escAttr(clip) + '\')">Move</button>' +
        '</div></div>';
    }
    html += '</div></div>';
  }

  content.innerHTML = html;
}

function moveClip(fromEntity, fromAction, filename) {
  var entities = Object.keys(data.voices).sort(entitySort);
  var root = document.getElementById('modal-root');

  root.innerHTML = '<div class="modal-bg" onclick="if(event.target===this)closeModal()">' +
    '<div class="modal">' +
    '<h3>Move Clip</h3>' +
    '<p style="font-size:12px;color:#888;margin-bottom:16px;">' + esc(filename) + '</p>' +
    '<label>Entity</label>' +
    '<input id="mv-entity" list="mv-entity-list" value="' + esc(fromEntity) + '">' +
    '<datalist id="mv-entity-list">' +
    entities.map(function(e) { return '<option value="' + esc(e) + '">'; }).join('') +
    '</datalist>' +
    '<label>Action</label>' +
    '<input id="mv-action" list="mv-action-list" value="' + esc(fromAction) + '">' +
    '<datalist id="mv-action-list"></datalist>' +
    '<div class="btn-row">' +
    '<button class="btn-cancel" onclick="closeModal()">Cancel</button>' +
    '<button class="btn-ok" onclick="doMove(\'' + escAttr(fromEntity) + '\',\'' + escAttr(fromAction) + '\',\'' + escAttr(filename) + '\')">Move</button>' +
    '</div></div></div>';

  var entInput = document.getElementById('mv-entity');
  entInput.addEventListener('input', function() {
    var ent = entInput.value;
    var dl = document.getElementById('mv-action-list');
    if (data.voices[ent]) {
      dl.innerHTML = Object.keys(data.voices[ent]).sort()
        .map(function(a) { return '<option value="' + esc(a) + '">'; }).join('');
    }
  });
  entInput.dispatchEvent(new Event('input'));
}

function closeModal() {
  document.getElementById('modal-root').innerHTML = '';
}

async function doMove(fromEntity, fromAction, filename) {
  var toEntity = document.getElementById('mv-entity').value.trim();
  var toAction = document.getElementById('mv-action').value.trim();
  if (!toEntity || !toAction) return;

  await fetch('/api/move', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      filename: filename,
      from_entity: fromEntity,
      from_action: fromAction,
      to_entity: toEntity,
      to_action: toAction
    })
  });

  closeModal();
  data = await (await fetch('/api/mapping')).json();
  renderTabs();
  renderSidebar();
  updateStats();
  if (currentEntity) renderEntity(currentEntity);
}

async function rescan() {
  data = await (await fetch('/api/rescan')).json();
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
    print(f"Building mapping from prefabs in {VOICES_DIR}...")
    print(f"Audio files in {AUDIO_DIR}")
    mapping_data = load_mapping()
    voices = mapping_data.get("voices", {})
    total = sum(len(clips) for ent in voices.values() for clips in ent.values())
    assigned = total - len(voices.get("Uncategorised", {}).get("misc", []))
    uncat = len(voices.get("Uncategorised", {}).get("misc", []))
    print(f"Found {len(voices) - (1 if 'Uncategorised' in voices else 0)} voice sets, "
          f"{assigned} assigned clips, {uncat} uncategorised")
    print(f"\nStarting server at http://localhost:{PORT}")

    class ReusableHTTPServer(HTTPServer):
        allow_reuse_address = True

    server = ReusableHTTPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()
