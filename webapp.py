# -*- coding: utf-8 -*-
"""
Solace Web — a touch-friendly web front-end for the Solace music companion.
=============================================================================
This is a SEPARATE web layer. It does NOT modify solace.py — it imports it and
reuses the exact same emotion model, weight-based playlist generator, feedback
learning and lyrics database. The original Tkinter desktop app is untouched.

Why a stub tkinter?  solace.py does `from tkinter import *` at module load (for
its desktop GUI). On a headless cloud server tkinter may be absent, so we slot
in a tiny stub BEFORE importing solace. None of the GUI ever runs here — only
the pure-Python logic (build_playlist, emotion_weights, Song, load_feedback…).

Run locally:   python webapp.py        (or: gunicorn webapp:app)
"""
import os, re, io, json, mimetypes, sys, types

# ── tkinter stub (only used if the real one is missing, e.g. on the server) ──
try:
    import tkinter  # noqa: F401  — real tkinter present (e.g. on Windows)
except Exception:                                              # pragma: no cover
    class _Dummy:
        """Subclassable no-op stand-in for any tkinter widget/constant."""
        def __init__(self, *a, **k): pass
        def __getattr__(self, n): return lambda *a, **k: None
    _tk = types.ModuleType("tkinter")
    # Names solace may touch at import time (class bases / constants).
    _NAMES = ["Frame", "Label", "Button", "Canvas", "Text", "Entry", "Scale",
              "Menu", "Toplevel", "OptionMenu", "StringVar", "DoubleVar",
              "IntVar", "BooleanVar", "Scrollbar", "PhotoImage", "Tk",
              "Listbox", "Spinbox", "Checkbutton", "Radiobutton", "PanedWindow",
              "LabelFrame", "Message", "END", "BOTH", "X", "Y", "LEFT", "RIGHT",
              "TOP", "BOTTOM", "W", "E", "N", "S", "NW", "NE", "SW", "SE",
              "CENTER", "NONE", "WORD", "CHAR", "HORIZONTAL", "VERTICAL",
              "DISABLED", "NORMAL", "ACTIVE", "FLAT", "RAISED", "SUNKEN",
              "GROOVE", "RIDGE", "SOLID", "INSERT", "SEL", "SEL_FIRST",
              "SEL_LAST", "TRUE", "FALSE", "YES", "NO", "ALL", "ANCHOR"]
    for _n in _NAMES:
        setattr(_tk, _n, _Dummy)
    _tk.__all__ = _NAMES
    _tk.__getattr__ = lambda name: _Dummy          # PEP 562 fallback
    for _sub in ("ttk", "filedialog", "messagebox", "simpledialog",
                 "colorchooser", "font", "scrolledtext"):
        _m = types.ModuleType("tkinter." + _sub)
        _m.__getattr__ = lambda name: _Dummy
        setattr(_tk, _sub, _m)
        sys.modules["tkinter." + _sub] = _m
    sys.modules["tkinter"] = _tk

import solace  # noqa: E402  — the existing desktop app, reused as a library
from flask import (Flask, jsonify, request, render_template,  # noqa: E402
                   send_file, Response, abort)

app = Flask(__name__)

HERE        = os.path.dirname(os.path.abspath(__file__))
SAMPLE_DIR  = os.path.join(HERE, "sample_music")
os.makedirs(SAMPLE_DIR, exist_ok=True)


# ──────────────────────────────────────────────────────────────────────────────
#  LIBRARY LOADING
#  solace.load_library() drops songs whose local Windows path no longer exists,
#  which would empty the library on the server. So we read the JSON ourselves
#  via Song.from_dict (which does NOT check existence) to keep every tagged song.
# ──────────────────────────────────────────────────────────────────────────────
def _basename(p):
    """Split a path on both / and \\ so Windows paths work on Linux."""
    return re.split(r"[\\/]", p or "")[-1]


def _build_audio_index():
    """Map a lowercased filename -> actual file on disk in sample_music/."""
    idx = {}
    try:
        for fn in os.listdir(SAMPLE_DIR):
            full = os.path.join(SAMPLE_DIR, fn)
            if os.path.isfile(full):
                idx[fn.lower()] = fn
    except Exception:
        pass
    return idx


def load_state():
    """Load songs + custom tags + feedback + lyrics. Returns a dict."""
    songs, custom_tags = [], {}
    try:
        with open(solace.LIBRARY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        custom_tags = data.get("custom_tags", {})
        songs = [solace.Song.from_dict(d) for d in data.get("songs", [])]
    except Exception as e:
        print("library load failed:", e)

    audio_idx = _build_audio_index()
    try:
        lyrics_db = solace.load_lyrics_db()
    except Exception:
        lyrics_db = {}
    try:
        feedback = solace.load_feedback()
    except Exception:
        feedback = {"biases": {}, "mood_biases": {}, "affinity": {}, "log": []}

    # Resolve which songs have a playable file in sample_music/
    audio_file = {}
    for i, s in enumerate(songs):
        fn = _basename(s.path).lower()
        if fn in audio_idx:
            audio_file[i] = audio_idx[fn]

    return {
        "songs": songs,
        "custom_tags": custom_tags,
        "lyrics_db": lyrics_db,
        "feedback": feedback,
        "audio_file": audio_file,
    }


# Loaded once at startup (the library is read-only on the server).
STATE = load_state()


def _song_json(i, s):
    return {
        "id": i,
        "title": s.title or s.name,
        "artist": s.artist or "",
        "emotion": s.emotion,
        "duration": s.duration or 0,
        "cover": s.cover_url or "",
        "has_audio": i in STATE["audio_file"],
    }


# ──────────────────────────────────────────────────────────────────────────────
#  ROUTES
# ──────────────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/data")
def api_data():
    songs = STATE["songs"]
    return jsonify({
        "emotions": solace.EMOTIONS,
        "goals":    list(solace.GOALS),
        "em_color": solace.EM_COL,
        "em_icon":  solace.EM_ICON,
        "songs":    [_song_json(i, s) for i, s in enumerate(songs)],
        "playable": sum(1 for i in range(len(songs)) if i in STATE["audio_file"]),
    })


@app.route("/api/playlist", methods=["POST"])
def api_playlist():
    body  = request.get_json(force=True, silent=True) or {}
    goal  = body.get("goal") or (solace.GOALS[0] if solace.GOALS else "calm me down")
    feel  = body.get("feel") or "calm"
    n     = int(body.get("n", 50) or 50)
    n     = max(1, min(n, 50))

    songs = STATE["songs"]
    fb    = STATE["feedback"]
    bias      = (fb.get("biases") or {}).get(goal)
    mood_bias = (fb.get("mood_biases") or {}).get(feel)
    affinity  = (fb.get("affinity") or {}).get(goal)

    try:
        result = solace.build_playlist(
            songs, feel, goal, STATE["custom_tags"], n=n,
            bias=bias, mood_bias=mood_bias, affinity=affinity)
    except Exception as e:
        print("playlist build failed:", e)
        result = []

    idx_of = {id(s): i for i, s in enumerate(songs)}
    ids    = [idx_of[id(s)] for s in result if id(s) in idx_of]
    return jsonify({
        "goal": goal, "feel": feel,
        "songs": [_song_json(i, songs[i]) for i in ids],
    })


@app.route("/api/lyrics/<int:sid>")
def api_lyrics(sid):
    songs = STATE["songs"]
    if sid < 0 or sid >= len(songs):
        abort(404)
    s   = songs[sid]
    txt = (STATE["lyrics_db"].get(s.path, {}) or {}).get("lyrics", "")
    if not txt:
        # Best-effort live fetch (multi-language: lrclib + lyrics.ovh).
        try:
            txt = solace.fetch_lyrics(s.title, s.artist) or ""
            if txt:
                STATE["lyrics_db"][s.path] = {"lyrics": txt, "source": "auto"}
        except Exception:
            txt = ""
    return jsonify({"id": sid, "lyrics": txt,
                    "title": s.title, "artist": s.artist})


@app.route("/api/audio/<int:sid>")
def api_audio(sid):
    songs = STATE["songs"]
    if sid < 0 or sid >= len(songs) or sid not in STATE["audio_file"]:
        abort(404)
    path = os.path.join(SAMPLE_DIR, STATE["audio_file"][sid])
    if not os.path.isfile(path):
        abort(404)
    return _send_with_range(path)


def _send_with_range(path):
    """Serve a file honouring HTTP Range requests — required for iOS Safari
    <audio> seeking. Falls back to a plain send when no Range header."""
    range_header = request.headers.get("Range", None)
    size = os.path.getsize(path)
    ctype = mimetypes.guess_type(path)[0] or "audio/mpeg"

    if not range_header:
        resp = send_file(path, mimetype=ctype, conditional=True)
        resp.headers["Accept-Ranges"] = "bytes"
        return resp

    m = re.search(r"bytes=(\d+)-(\d*)", range_header)
    if not m:
        return send_file(path, mimetype=ctype)
    start = int(m.group(1))
    end   = int(m.group(2)) if m.group(2) else size - 1
    end   = min(end, size - 1)
    start = min(start, end)
    length = end - start + 1

    with open(path, "rb") as f:
        f.seek(start)
        chunk = f.read(length)
    resp = Response(chunk, status=206, mimetype=ctype,
                    direct_passthrough=True)
    resp.headers["Content-Range"]  = f"bytes {start}-{end}/{size}"
    resp.headers["Accept-Ranges"]  = "bytes"
    resp.headers["Content-Length"] = str(length)
    return resp


@app.route("/healthz")
def healthz():
    return jsonify({"ok": True, "songs": len(STATE["songs"]),
                    "playable": len(STATE["audio_file"])})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"Solace Web — {len(STATE['songs'])} songs, "
          f"{len(STATE['audio_file'])} playable. http://127.0.0.1:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
