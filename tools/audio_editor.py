#!/usr/bin/env python3
"""ER2 Audio Editor — maps and manages audio clip assignments.

Zero-dependency version using Python stdlib. If Flask is available,
run with: flask --app audio_editor run -p 8420
Otherwise just: python3 audio_editor.py
"""

import json
import mimetypes
import re
from functools import partial
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import unquote

AUDIO_DIR = Path(__file__).resolve().parent.parent / "data" / "Assets" / "AudioClip"
MAPPING_FILE = Path(__file__).resolve().parent.parent / "data" / "audio_mapping.json"
PORT = 8420

# ---------------------------------------------------------------------------
# Filename Parsing
# ---------------------------------------------------------------------------

OLD_STYLE_NATIONS = {
    "ger": "German",
    "usa": "American",
    "rus": "Russian",
    "ita": "Italian",
    "fr": "French",
    "jap": "Japanese",
    "eng": "English",
}

NEW_STYLE_NATIONS = {
    "AUS": "Australian",
    "CN": "Chinese",
    "POL": "Polish",
    "IND": "Indian",
    "GB": "British",
    "JP": "Japanese",
    "CH": "CH",
}

MID_STYLE_NATIONS = {
    "Eng": "English Tank",
    "sgoti": "SGOTI",
}

RE_OLD = re.compile(
    r"^(?P<nation>" + "|".join(OLD_STYLE_NATIONS) + r")"
    r"(?P<voice>\d+)_(?P<action>.+?)(?P<variant>\d+)?\.\w+$",
    re.IGNORECASE,
)

# New-style: AUS_2_ActionName001.wav, CH_4_coveringfire1.wav, JP_4_Follow_Me_2A.wav
# Also handles CH4_Action_001.wav (no underscore between nation and voice number)
RE_NEW = re.compile(
    r"^(?P<nation>" + "|".join(NEW_STYLE_NATIONS) + r")"
    r"[_]?(?P<voice>\d+)_(?P<action>[A-Za-z0-9][A-Za-z_ ]*?)_?(?P<variant>\d+[A-Za-z]?(?:-\d+)?)?\.\w+$",
)

RE_MID = re.compile(
    r"^(?P<nation>" + "|".join(MID_STYLE_NATIONS) + r")"
    r"_(?P<action>[A-Za-z0-9_ ]+?)(?P<variant>\d+)?\.\w+$",
    re.IGNORECASE,
)


# Suffix-based categories for generic files
SUFFIX_CATEGORIES = {
    "_tank": "Tank Crew (Generic)",
    "_com": "Commander",
    "_radio": "Radio",
    "_radioNPC": "Radio",
}

# French-language voice lines — map French text to action names
FRENCH_ACTION_MAP = {
    "aaaah": "scream",
    "allez": "charge",
    "argh": "scream",
    "artillerie ennemi": "artilleryIncoming",
    "artillerie ennemie": "artilleryIncoming",
    "attaquez ce véhicule": "attackVehicle",
    "attaquez cette position": "attackPosition",
    "avec moi": "followMe",
    "bien reçu": "radioConfirm",
    "canon prêt": "cannonReady",
    "canon rechargé": "gunReloaded",
    "chaaarg": "charge",
    "char ennemie": "enemyTankSpotted",
    "chargez": "charge",
    "cible détruite": "targetDestroyed",
    "cible intacte": "targetIntact",
    "cible manquée": "targetMissed",
    "commandant tué": "commanderDead",
    "conducteur tué": "driverDead",
    "contact": "enemySpotted",
    "d'accord": "yes",
    "détruisez ce char": "attackTank",
    "défendez": "holdPosition",
    "déplacement": "imMoving",
    "déplacez": "moveThere",
    "désolé monsieur": "radioRequestDenied",
    "en avant": "charge",
    "en colonne": "columnFormation",
    "en ligne": "lineFormation",
    "enemirepérés": "enemySpotted",
    "ennemi détruit": "enemyDestroyed",
    "ennemi juste là": "enemySpotted",
    "ennemi neutralisé": "enemyDown",
    "ennemi repéré": "enemySpotted",
    "ennemi touché": "enemyHit",
    "ennemie détruit": "enemyDestroyed",
    "ennemie juste là": "enemySpotted",
    "ennemie neutralisé": "enemyDown",
    "ennemie touché": "enemyHit",
    "entrez": "getIn",
    "feu": "fire",
    "formez une colonne": "columnFormation",
    "formez une ligne": "lineFormation",
    "formation": "formation",
    "grenade": "grenade",
    "grenaaad": "grenade",
    "il est mort": "enemyDown",
    "il est à terre": "enemyDown",
    "il faut sortir": "getOut",
    "infanterie ennemi": "enemyInfantrySpotted",
    "infanterieennemie": "enemyInfantrySpotted",
    "j'abandonne": "surrender",
    "j'ai besoin d'un médecin": "medic",
    "j'ai mal": "gotHit",
    "j'aibesoind'unmédecin": "medic",
    "j'aimal": "gotHit",
    "j'y vais": "imMoving",
    "je bouge": "imMoving",
    "je me rend": "surrender",
    "je prend le commandement": "takingCommand",
    "je prend sa place": "replaceSeat",
    "je prends le commandement": "takingCommand",
    "je prends sa place": "replaceSeat",
    "je recharge": "reloading",
    "je suis sous le feu": "underFire",
    "je suis touché": "gotHit",
    "jechargeunmunition": "reloading",
    "jerecharge": "reloading",
    "jesuissouslefeu": "underFire",
    "jesuistouché": "gotHit",
    "jolie tir": "niceShot",
    "là-bas": "moveThere",
    "maintenant": "fire",
    "merci": "thanks",
    "mitrailleur tué": "gunnerDead",
    "médecin": "medic",
    "ne tirez": "friendlyFire",
    "notre conducteur est mort": "driverDead",
    "notre radio est hs": "radiomanDead",
    "notre tank est en feu": "tankOnFire",
    "notre tank est hors": "tankDestroyed",
    "notre tireur est mort": "gunnerDead",
    "notre véhicule est hs": "vehicleDestroyed",
    "ok": "yes",
    "on bouge": "imMoving",
    "on doit revenir": "retreat",
    "on essuie des tirs": "underFire",
    "on est touché": "gotHit",
    "on n'a pas": "armorNotPenetrated",
    "on se disperse": "spreadOut",
    "opérateur radio tué": "radiomanDead",
    "oui monsieur": "yesSir",
    "oui": "yes",
    "ouimonsieur": "yesSir",
    "pilote tué": "driverDead",
    "qg": "radioRequest",
    "raté": "targetMissed",
    "reçu": "radioConfirm",
    "regardez où vous tirez": "friendlyFire",
    "regardezouvoustirez": "friendlyFire",
    "regroupement": "regroup",
    "repli": "retreat",
    "reddition": "surrender",
    "sortez": "getOut",
    "suivez-moi": "followMe",
    "tenez cette position": "holdPosition",
    "tirez": "fire",
    "tirs de suppression": "coveringFire",
    "touché": "enemyHit",
    "tous en ligne": "lineFormation",
    "un char ennemi": "enemyTankSpotted",
    "woooah": "scream",
    "à couvert": "takeCover",
}

# Generic English voice lines — map to action names
GENERIC_VOICE_MAP = {
    "checkYourFire": "friendlyFire",
    "enemyDown": "enemyDown",
    "enemyInfantry": "enemyInfantrySpotted",
    "enemySpotted": "enemySpotted",
    "enemyTank": "enemyTankSpotted",
    "grenade": "grenade",
    "hit": "gotHit",
    "imgoing": "imMoving",
    "incomingEnemyArtillery": "artilleryIncoming",
    "longScream": "scream",
    "medicine": "medic",
    "reload": "reloading",
    "scream": "scream",
    "suppressing": "coveringFire",
    "surrender": "surrender",
    "underfire": "underFire",
    "yes": "yes",
    "enemydown": "enemyDown",
    "medic": "medic",
    "regroup": "regroup",
    "werehit": "gotHit",
    "screams": "scream",
    "infantryspotted": "enemyInfantrySpotted",
    "onfire": "onFire",
    "miss": "targetMissed",
    "enemyhit": "enemyHit",
    "attack": "attack",
    "thanks": "thanks",
    "fire": "fire",
    "targetdestroyed": "targetDestroyed",
    "targetintact": "targetIntact",
    "retreat": "retreat",
    "num": "number",
}


def parse_voice_file(filename):
    """Try to parse a voice file. Returns (entity, action, filename) or None."""
    # New style first (more specific)
    m = RE_NEW.match(filename)
    if m:
        nation = NEW_STYLE_NATIONS.get(m.group("nation"), m.group("nation"))
        return f"{nation} Voice {m.group('voice')}", m.group("action"), filename

    # Old style
    m = RE_OLD.match(filename)
    if m:
        nation = OLD_STYLE_NATIONS.get(m.group("nation").lower(), m.group("nation"))
        return f"{nation} Voice {m.group('voice')}", m.group("action"), filename

    # Mid style (no voice number)
    m = RE_MID.match(filename)
    if m:
        key = m.group("nation")
        nation = next((v for k, v in MID_STYLE_NATIONS.items()
                       if k.lower() == key.lower()), key)
        return nation, m.group("action"), filename

    stem = Path(filename).stem

    # Bare numbers: 0.ogg, 0_0.ogg, 0_radio.ogg etc
    if re.match(r"^\d+(_\d+)?$", stem) or re.match(r"^\d+_radio$", stem):
        return "Numbers (Generic)", "number", filename

    # Suffix-based: _tank.ogg, _com.ogg, _radio.ogg, _radioNPC.ogg
    for suffix, entity in SUFFIX_CATEGORIES.items():
        if stem.endswith(suffix) or stem.endswith(suffix.replace("_", "")):
            action = stem[:stem.rfind(suffix.replace("_", "")) if "_" not in stem[:stem.rfind(".")] else stem.rfind(suffix)]
            # Clean action: remove trailing digits
            action = re.sub(r"\d+$", "", stem.rsplit(suffix.lstrip("_"))[0].rstrip("_"))
            if not action:
                action = "misc"
            return entity, action, filename

    # French-language voice lines
    lower_stem = stem.lower()
    for french_key, action in FRENCH_ACTION_MAP.items():
        if lower_stem.startswith(french_key.lower()):
            return "French (Legacy)", action, filename

    # Generic English voice lines (no nation prefix)
    for pattern, action in GENERIC_VOICE_MAP.items():
        if lower_stem.startswith(pattern.lower()):
            return "Generic Voice", action, filename

    return None


def scan_audio_files():
    """Scan audio directory and build entity -> action -> [files] mapping."""
    mapping = {}

    if not AUDIO_DIR.exists():
        return mapping

    for f in sorted(AUDIO_DIR.iterdir()):
        if not f.is_file():
            continue
        result = parse_voice_file(f.name)
        if result:
            entity, action, fname = result
            mapping.setdefault(entity, {}).setdefault(action, []).append(fname)
        else:
            mapping.setdefault("Uncategorised", {}).setdefault("misc", []).append(f.name)

    return mapping


def load_mapping():
    if MAPPING_FILE.exists():
        with open(MAPPING_FILE) as f:
            return json.load(f)
    mapping = scan_audio_files()
    data = {"voices": mapping}
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
            mapping = scan_audio_files()
            data = {"voices": mapping}
            save_mapping(data)
            self._send_json(data)
        elif path.startswith("/audio/"):
            self._serve_audio(path[7:])
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
        pass  # quiet


# ---------------------------------------------------------------------------
# HTML / JS — single page app
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
  #sidebar h2 { padding: 16px; font-size: 14px; color: #e94560;
                 text-transform: uppercase; letter-spacing: 1px; flex-shrink: 0; }
  .filter-bar { padding: 0 12px 8px; flex-shrink: 0; }
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
  <div class="filter-bar">
    <input type="text" id="entity-filter" placeholder="Filter entities..."
           oninput="filterEntities()">
  </div>
  <div id="entity-list"></div>
</div>

<div id="main">
  <div id="toolbar">
    <button class="secondary" onclick="rescan()">Rescan Files</button>
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

function esc(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function escAttr(s) { return s.replace(/\\/g,'\\\\').replace(/'/g,"\\'"); }

function entitySort(a, b) {
  // Parse "Faction Voice N" or standalone names
  var ma = a.match(/^(.+?)(?:\s+Voice\s+(\d+))?$/);
  var mb = b.match(/^(.+?)(?:\s+Voice\s+(\d+))?$/);
  var factionA = ma ? ma[1] : a;
  var factionB = mb ? mb[1] : b;
  if (factionA !== factionB) return factionA.localeCompare(factionB);
  var numA = ma && ma[2] ? parseInt(ma[2]) : 0;
  var numB = mb && mb[2] ? parseInt(mb[2]) : 0;
  return numA - numB;
}

async function init() {
  data = await (await fetch('/api/mapping')).json();
  renderSidebar();
  updateStats();
}

function renderSidebar() {
  const list = document.getElementById('entity-list');
  const filter = document.getElementById('entity-filter').value.toLowerCase();
  const entities = Object.keys(data.voices).sort(entitySort);

  list.innerHTML = entities
    .filter(e => e.toLowerCase().includes(filter))
    .map(e => {
      const count = Object.values(data.voices[e]).reduce((s, c) => s + c.length, 0);
      const cls = e === currentEntity ? 'entity-item active' : 'entity-item';
      return `<div class="${cls}" onclick="selectEntity('${escAttr(e)}')" data-entity="${esc(e)}">
        <span>${esc(e)}</span><span class="count">${count}</span></div>`;
    }).join('');

}

function filterEntities() { renderSidebar(); }

function updateStats() {
  const v = data.voices;
  const ents = Object.keys(v).length;
  const clips = Object.values(v).reduce((a, ent) =>
    a + Object.values(ent).reduce((b, c) => b + c.length, 0), 0);
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
  const content = document.getElementById('content');
  const actions = data.voices[entity];
  if (!actions) { content.innerHTML = '<p>Entity not found.</p>'; return; }

  const sorted = Object.keys(actions).sort();
  let html = '<div id="entity-title">' + esc(entity) + '</div>';

  for (const action of sorted) {
    const clips = actions[action];
    html += '<div class="action-group">';
    html += '<div class="action-header">' + esc(action) + ' (' + clips.length + ')</div>';
    html += '<div class="clip-list">';
    for (const clip of clips) {
      const ext = clip.split('.').pop().toLowerCase();
      const mime = ext === 'ogg' ? 'audio/ogg' : 'audio/wav';
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
  const entities = Object.keys(data.voices).sort(entitySort);
  const root = document.getElementById('modal-root');

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
  renderSidebar();
  updateStats();
  if (currentEntity) renderEntity(currentEntity);
}

async function rescan() {
  data = await (await fetch('/api/rescan')).json();
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
    print(f"Scanning audio files from {AUDIO_DIR}...")
    mapping_data = load_mapping()
    voices = mapping_data.get("voices", {})
    total = sum(len(clips) for ent in voices.values() for clips in ent.values())
    print(f"Found {len(voices)} entities, {total} clips")
    print(f"\nStarting server at http://localhost:{PORT}")
    import socketserver
    class ReusableHTTPServer(HTTPServer):
        allow_reuse_address = True
    server = ReusableHTTPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()
