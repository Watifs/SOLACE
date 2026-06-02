# -*- coding: utf-8 -*-
"""
Solace  —  Emotion-Aware Music Companion  (v5)
================================================
pip install librosa soundfile numpy pygame-ce mutagen pillow

New in v5:
  • Fixed angry over-tagging — rebalanced classifier uses chroma (key/mode),
    spectral rolloff, and harmonic/percussive ratio
  • Fixed energy/ZCR normalization (was clamping everything to 1.0)
  • All 13 MFCC coefficients now used in KNN learning model (was only 4)
  • Lyrics fetching — lrclib.net (primary) + lyrics.ovh (fallback)
  • Lyrics stored in solace_lyrics.json, displayed in full-screen player
  • Lyrics sentiment feeds into emotion tagger (15% weight)
  • Cover art — embedded ID3 art or iTunes API thumbnail (requires Pillow)
  • Full-screen now-playing player (click ⤢ button or double-click player bar)
  • Internet lookup on startup for all un-looked-up saved songs
  • Duration fixed — parsed from features or filename; no more 0:00 everywhere
  • artist/title parsed from "Artist - Title" filenames for better lookups
  • Permanent user edits (unchanged) + confirmed tag badge
"""

import os, re, io, json, time, uuid, queue, ctypes, random, threading, datetime, math
import urllib.request, urllib.parse
from pathlib import Path
from tkinter import *
from tkinter import ttk, filedialog, messagebox, simpledialog, colorchooser
import numpy as np

# ── DPI awareness ─────────────────────────────────────────────────────────────
try:    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    try: ctypes.windll.user32.SetProcessDPIAware()
    except Exception: pass

# ── Optional audio libraries ──────────────────────────────────────────────────
try:
    import librosa as _lib;  _LIBROSA = True
except ImportError:
    _lib = None; _LIBROSA = False

try:
    import pygame
    pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=2048)
    _PYGAME = True
except Exception:
    pygame = None; _PYGAME = False

try:
    from mutagen import File as _MutagenFile; _MUTAGEN = True
except Exception:
    _MutagenFile = None; _MUTAGEN = False

try:
    from PIL import Image, ImageTk; _PIL = True
except ImportError:
    Image = ImageTk = None; _PIL = False

# ── Data files ────────────────────────────────────────────────────────────────
_DIR           = Path(__file__).parent
LIBRARY_FILE   = _DIR / "solace_library.json"
PLAYLISTS_FILE = _DIR / "solace_playlists.json"
LEARNING_FILE  = _DIR / "solace_learning.json"
LYRICS_FILE    = _DIR / "solace_lyrics.json"
FEEDBACK_FILE  = _DIR / "solace_feedback.json"

WORKER_THREADS    = 4
ANALYSIS_DURATION = 45.0   # seconds to analyse per song (was 30)


# ==============================================================================
#  PALETTE
# ==============================================================================
BG        = "#0d0d0d"
BG2       = "#111111"
BG3       = "#191919"
BG4       = "#161616"
ACCENT    = "#1D9E75"
ACCENT_DK = "#14704F"
TXT       = "#e8e8e8"
TXT_MID   = "#909090"
TXT_DIM   = "#484848"
BORDER    = "#252525"
WHITE     = "#ffffff"
FF        = "Segoe UI"


# ==============================================================================
#  EMOTION SYSTEM
# ==============================================================================
EMOTIONS = ["energetic","happy","calm","melancholic","sad","angry","focused"]

EM_COL = {
    "energetic":"#FF6B35","happy":"#F5C518","calm":"#4FC3F7",
    "melancholic":"#A98FE0","sad":"#6B9FEF","angry":"#EF5350","focused":"#1D9E75",
}
EM_BG = {
    "energetic":"#22130a","happy":"#221e05","calm":"#071722",
    "melancholic":"#130e22","sad":"#0a1220","angry":"#200808","focused":"#071813",
}
EM_ICON = {
    "energetic":"⚡","happy":"✦","calm":"◌",
    "melancholic":"◎","sad":"▿","angry":"◈","focused":"◉",
}

SRC_LABEL = {"auto":"AI","user":"✏","internet":"🌐","learned":"🧠",
             "confirmed":"✓","lyrics":"♪"}

_KW = {
    "energetic":  ["pumped","excited","hyped","alive","electric","amped","vibrant",
                   "buzzing","wired","fired","rush","adrenaline","unstoppable","run",
                   "jump","go","power","force","charge","ignite"],
    "happy":      ["happy","joyful","great","good","wonderful","cheerful","glad",
                   "upbeat","positive","elated","content","bright","pleased","light",
                   "smile","laugh","celebrate","love","sunshine","beautiful","amazing",
                   "paradise","perfect","dream","dance","free"],
    "calm":       ["calm","peaceful","relaxed","serene","chill","tranquil","easy",
                   "settled","mellow","still","composed","gentle","soothed","breathe",
                   "quiet","slow","soft","float","drift","rest","sleep","warm","safe"],
    "melancholic":["nostalgic","wistful","bittersweet","reflective","pensive",
                   "longing","thoughtful","reminiscent","bored","numb","flat",
                   "distant","hollow","remember","miss","used to","once","fading",
                   "yesterday","ago","memory","memories","lost","gone","left behind"],
    "sad":        ["sad","unhappy","miserable","depressed","hopeless","lonely",
                   "empty","lost","down","blue","crying","heartbroken","cry","tears",
                   "exhausted","drained","tired","burnt","devastated","broken","weary",
                   "pain","hurt","alone","goodbye","leave","never","forever gone"],
    "angry":      ["angry","furious","frustrated","stressed","tense","irritated",
                   "annoyed","mad","rage","anxious","overwhelmed","nervous","hate",
                   "restless","worried","scared","panicked","wound","agitated","fight",
                   "scream","destroy","kill","burn","war","enemy","attack","crush"],
    "focused":    ["focused","productive","studying","working","motivated",
                   "concentrated","determined","driven","sharp","locked","grind",
                   "push","hustle","build","create","mission","purpose","clear"],
}

_ARCS = {
    "lift my mood":    ["melancholic","calm","calm","happy","happy","energetic","happy"],
    "calm me down":    ["calm","calm","calm","melancholic","calm","calm","calm"],
    "energise me":     ["calm","focused","focused","happy","happy","energetic","energetic"],
    "help me sleep":   ["calm","calm","melancholic","melancholic","sad","calm","calm"],
    "feel melancholic":["melancholic","melancholic","sad","melancholic","calm","melancholic","calm"],
    "let me feel it":  ["sad","melancholic","melancholic","sad","melancholic","melancholic","calm"],
    "help me focus":   ["calm","focused","focused","focused","focused","focused","calm"],
}
GOALS = list(_ARCS.keys())

_NEARBY = {
    "energetic":  ["happy","angry","focused"],
    "happy":      ["energetic","calm","focused"],
    "calm":       ["focused","melancholic","happy"],
    "melancholic":["sad","calm","focused"],
    "sad":        ["melancholic","calm"],
    "angry":      ["energetic","focused"],
    "focused":    ["calm","energetic","happy"],
}

GENRE_MAP = {
    "electronic":"energetic","dance":"energetic","hip-hop/rap":"energetic",
    "hip-hop":"energetic","rap":"energetic","edm":"energetic",
    "fitness & workout":"energetic","workout":"energetic","drum and bass":"energetic",
    "pop":"happy","country":"happy","k-pop":"happy","gospel":"happy",
    "children's music":"happy","holiday":"happy","latin":"happy","j-pop":"happy",
    "jazz":"calm","classical":"calm","new age":"calm","ambient":"calm",
    "folk":"calm","singer/songwriter":"calm","easy listening":"calm","bossa nova":"calm",
    "instrumental":"focused","soundtrack":"focused","score":"focused","post-rock":"focused",
    "blues":"melancholic","soul":"melancholic","r&b/soul":"melancholic",
    "alternative":"melancholic","indie pop":"melancholic","indie":"melancholic",
    "rock":"angry","metal":"angry","hard rock":"angry","punk":"angry",
    "alternative rock":"angry","heavy metal":"angry","metalcore":"angry",
}

# ── Research-informed goal → emotion targeting ────────────────────────────────
# Grounded in music mood-regulation research (mood-management theory + a light
# "iso-principle" bridge): to lift a low mood you do NOT dwell on sad music —
# you move toward higher-valence / higher-energy tracks. `prefer` lists the
# emotions to fill the playlist with (ranked); `avoid` are actively kept out;
# `bridge` allows AT MOST one opening song matching how the user feels now,
# only when that isn't an avoided emotion.
GOAL_PROFILE = {
    "lift my mood":     {"prefer":["happy","energetic","calm","focused"],
                         "avoid":["sad","angry","melancholic"],     "bridge":False},
    "calm me down":     {"prefer":["calm","focused","melancholic"],
                         "avoid":["angry","energetic"],             "bridge":True},
    "energise me":      {"prefer":["energetic","happy","focused"],
                         "avoid":["sad","melancholic"],             "bridge":False},
    "help me sleep":    {"prefer":["calm","melancholic"],
                         "avoid":["energetic","angry","happy"],     "bridge":True},
    "feel melancholic": {"prefer":["melancholic","calm","sad"],
                         "avoid":["energetic","angry"],             "bridge":True},
    "let me feel it":   {"prefer":["sad","melancholic","calm"],
                         "avoid":["energetic","angry"],             "bridge":True},
    "help me focus":    {"prefer":["focused","calm"],
                         "avoid":["angry","sad","happy"],           "bridge":False},
}

# Energy ranking used to order a playlist into a gentle build-up / wind-down
ENERGY_RANK = {"sad":0,"melancholic":1,"calm":2,"focused":3,
               "happy":4,"energetic":5,"angry":5}

# Search terms used to teach emotion affinity from online metadata (iTunes)
GOAL_QUERY = {
    "lift my mood":     "uplifting feel good happy",
    "calm me down":     "calm relaxing soothing",
    "energise me":      "energetic workout hype",
    "help me sleep":    "sleep ambient peaceful",
    "feel melancholic": "melancholic nostalgic wistful",
    "let me feel it":   "sad emotional ballad",
    "help me focus":    "focus study instrumental concentration",
}


# ==============================================================================
#  AUDIO ANALYSIS
# ==============================================================================
def _parse_artist_title(name: str) -> tuple:
    """Parse 'Artist - Title' from a filename string. Returns (artist, title)."""
    # Remove leading [Genre] / (Type) tags
    clean = re.sub(r'^\s*[\[\(][^\]\)]{1,30}[\]\)]\s*[-–]\s*', '', name)
    # Remove trailing tags like (Official), [Lyrics], (Audio)
    clean = re.sub(r'\s*[\[\(](?:official|lyrics?|audio|video|mv|hd|4k|full)[^\]\)]*[\]\)]\s*$',
                   '', clean, flags=re.I).strip()
    if ' - ' in clean:
        idx = clean.index(' - ')
        return clean[:idx].strip(), clean[idx+3:].strip()
    if ' – ' in clean:
        idx = clean.index(' – ')
        return clean[:idx].strip(), clean[idx+3:].strip()
    return '', name.strip()


def get_meta(path: str) -> dict:
    meta = {"duration": 0.0, "title": "", "artist": ""}
    if _MUTAGEN:
        try:
            f = _MutagenFile(path)
            if f:
                if hasattr(f.info, "length"):
                    meta["duration"] = float(f.info.length)
                tags = f.tags or {}
                for key in ("TIT2","©nam","title"):
                    if key in tags:
                        meta["title"] = str(tags[key][0] if isinstance(tags[key],list) else tags[key])
                        break
                for key in ("TPE1","©ART","artist"):
                    if key in tags:
                        meta["artist"] = str(tags[key][0] if isinstance(tags[key],list) else tags[key])
                        break
        except Exception:
            pass
    return meta


def get_duration_fast(path: str) -> float:
    """Return duration in seconds using fast header-only reads (no decode)."""
    if _MUTAGEN:
        try:
            f = _MutagenFile(path)
            if f and hasattr(f.info, "length") and f.info.length > 0:
                return float(f.info.length)
        except Exception:
            pass
    if _LIBROSA:
        try:
            return float(_lib.get_duration(path=path))
        except Exception:
            try:
                return float(_lib.get_duration(filename=path))
            except Exception:
                pass
    return 0.0


def get_embedded_art_data(path: str) -> bytes:
    """Return raw bytes of embedded album art, or b'' if none."""
    if not _MUTAGEN: return b''
    try:
        f = _MutagenFile(path)
        if f and f.tags:
            for key in list(f.tags.keys()):
                if key.startswith("APIC"):
                    return f.tags[key].data
            # M4A / AAC
            if "covr" in f.tags:
                covers = f.tags["covr"]
                if covers:
                    return bytes(covers[0])
    except Exception:
        pass
    return b''


def extract_features(path: str) -> dict:
    if not _LIBROSA:
        raise RuntimeError("librosa not installed")
    try:
        full_dur = float(_lib.get_duration(path=path))
    except Exception:
        try:
            full_dur = float(_lib.get_duration(filename=path))
        except Exception:
            full_dur = 0.0

    y, sr = _lib.load(path, sr=22050, duration=ANALYSIS_DURATION, mono=True)
    tempo_arr, _ = _lib.beat.beat_track(y=y, sr=sr)
    tempo = float(np.asarray(tempo_arr).ravel()[0])

    rms  = float(np.mean(_lib.feature.rms(y=y)))
    zcr  = float(np.mean(_lib.feature.zero_crossing_rate(y)))
    sc   = float(np.mean(_lib.feature.spectral_centroid(y=y, sr=sr)))
    ro   = float(np.mean(_lib.feature.spectral_rolloff(y=y, sr=sr, roll_percent=0.85)))
    mfcc = np.mean(_lib.feature.mfcc(y=y, sr=sr, n_mfcc=13), axis=1).tolist()

    # Harmonic / percussive separation
    try:
        y_h, y_p = _lib.effects.hpss(y)
        h_rms = float(np.mean(_lib.feature.rms(y=y_h)))
        p_rms = float(np.mean(_lib.feature.rms(y=y_p)))
        hpr   = h_rms / (p_rms + 1e-9)   # > 1 = more harmonic, < 1 = more percussive
    except Exception:
        hpr = 1.0

    # Key/mode detection via chroma (Krumhansl-Kessler profiles)
    try:
        chroma = np.mean(_lib.feature.chroma_stft(y=y, sr=sr), axis=1)
        major_p = np.array([6.35,2.23,3.48,2.33,4.38,4.09,2.52,5.19,2.39,3.66,2.29,2.88])
        minor_p = np.array([6.33,2.68,3.52,5.38,2.60,3.53,2.54,4.75,3.98,2.69,3.34,3.17])
        best_maj = max(float(np.dot(np.roll(chroma,-k), major_p)) for k in range(12))
        best_min = max(float(np.dot(np.roll(chroma,-k), minor_p)) for k in range(12))
        maj = float(best_maj / (best_maj + best_min + 1e-9))
        chroma_list = chroma.tolist()
    except Exception:
        maj = 0.5; chroma_list = []

    def _n(v, lo, hi): return float(max(0.0, min(1.0, (v-lo)/(hi-lo+1e-9))))

    return {
        "tempo_bpm":    round(tempo, 1),
        "full_duration": round(full_dur, 1),
        "mfcc":   [round(v, 2) for v in mfcc],
        "chroma": [round(v, 4) for v in chroma_list],
        # normalised scalars
        "t":   _n(tempo, 60, 190),
        "e":   _n(rms,  0.0, 0.06),   # FIXED: was 0.14 — everything clamped to 1
        "z":   _n(zcr,  0.0, 0.15),   # FIXED: was 0.11
        "s":   _n(sc,   800, 4200),
        "ro":  _n(ro,   1000, 10000),
        "hpr": _n(min(hpr, 4.0), 0.0, 4.0),   # 0=percussive, 1=balanced, 1=harmonic
        "maj": round(maj, 4),
    }


# ==============================================================================
#  CLASSIFIER
# ==============================================================================
def _bump(x, c, w):
    return float(math.exp(-0.5 * ((x-c)/max(w, 1e-6))**2))


def _base_scores(f: dict) -> dict:
    t   = f.get("t",  0.5)
    e   = f.get("e",  0.5)
    z   = f.get("z",  0.5)
    s   = f.get("s",  0.5)
    ro  = f.get("ro", 0.5)
    hpr = f.get("hpr",0.5)
    maj = f.get("maj",0.5)
    min_ = 1.0 - maj          # minor-key score

    return {
        # high BPM + energy. ZCR adds character but doesn't gate
        "energetic":   t * e * (0.55 + 0.30*z + 0.15*ro),
        # major key is required for happiness — minor-key songs can't be "happy"
        "happy":       maj * (0.4 + 0.35*t + 0.25*e),
        # slow + harmonic + low energy + low ZCR
        "calm":        (1-t) * hpr * (1-e) * (0.5 + 0.5*(1-z)),
        # medium tempo, minor key, moderate energy
        "melancholic": _bump(t,0.30,0.22) * min_ * (0.5 + 0.5*(1-e)),
        # very slow + very low energy + minor
        "sad":         (1-t)**2 * (1-e)**1.5 * min_,
        # FIXED: now requires percussive AND high rolloff AND high ZCR — much stricter
        "angry":       e * z * ro * (1-hpr) * (0.35 + 0.65*t),
        # mid-tempo, harmonic, low ZCR
        "focused":     _bump(t,0.42,0.22) * hpr * (0.5 + 0.5*(1-z)),
    }


def classify_emotion(f: dict) -> str:
    scores = _base_scores(f)
    return max(scores, key=scores.get)


# ==============================================================================
#  LEARNING ENGINE  (KNN — now uses 20 features instead of 4)
# ==============================================================================
# Normalisation constants derived from observed library data
_MFCC_SCALE = [150.0, 70.0, 35.0, 25.0, 18.0, 18.0,
               15.0,  15.0, 15.0, 15.0, 15.0, 15.0, 15.0]
_MFCC_OFF   = [150.0,-80.0,  0.0,  0.0,  0.0,  0.0,
                0.0,   0.0,  0.0,  0.0,  0.0,  0.0,  0.0]

def _normalize_mfcc(mfcc: list) -> list:
    out = []
    for i, v in enumerate(mfcc[:13]):
        sc  = _MFCC_SCALE[i] if i < len(_MFCC_SCALE) else 15.0
        off = _MFCC_OFF[i]   if i < len(_MFCC_OFF)   else  0.0
        out.append(max(-2.5, min(2.5, (v + off) / sc)))
    return out


def _feat_vec(f: dict) -> list:
    """20-dimensional feature vector: 7 acoustic scalars + 13 normalised MFCCs."""
    base = [
        f.get("t",  0.5),
        f.get("e",  0.5),
        f.get("z",  0.5),
        f.get("s",  0.5),
        f.get("ro", 0.5),
        f.get("hpr",0.5),
        f.get("maj",0.5),
    ]
    return base + _normalize_mfcc(f.get("mfcc", []))


def load_corrections() -> list:
    if not LEARNING_FILE.exists(): return []
    try:
        with open(LEARNING_FILE, encoding="utf-8") as f: return json.load(f)
    except Exception: return []


def save_corrections(corrections: list):
    try:
        with open(LEARNING_FILE, "w", encoding="utf-8") as f:
            json.dump(corrections, f, ensure_ascii=False, indent=2)
    except Exception: pass


def record_correction(corrections: list, features: dict,
                      auto_em: str, user_em: str) -> list:
    corrections.append({
        "features": _feat_vec(features),
        "auto":     auto_em,
        "user":     user_em,
        "ts":       datetime.datetime.now().isoformat(),
    })
    save_corrections(corrections)
    return corrections


def knn_emotion(features: dict, corrections: list,
                internet_em: str = "", lyrics_em: str = "", k: int = 7) -> tuple:
    """
    Returns (emotion, source). Blends:
      45% acoustic base scores
      25% KNN learned corrections
      15% internet genre hint
      15% lyrics sentiment
    """
    base  = _base_scores(features)
    total = sum(base.values()) or 1
    norm  = {em: v/total for em, v in base.items()}

    # KNN bias
    bias = {em: 0.0 for em in EMOTIONS}
    if corrections:
        vec   = _feat_vec(features)
        dists = []
        for c in corrections:
            cf = c["features"]
            # Handle old 4-feature corrections gracefully
            if len(cf) < len(vec):
                cf = cf + [0.0] * (len(vec) - len(cf))
            d = float(np.sqrt(sum((a-b)**2 for a,b in zip(vec, cf))))
            dists.append((d, c["user"]))
        dists.sort(key=lambda x: x[0])
        total_w = 0.0
        for dist, em in dists[:k]:
            if dist < 0.55 and em in bias:
                w = 1.0 / (dist + 0.04)
                bias[em] += w; total_w += w
        if total_w:
            for em in bias: bias[em] /= total_w

    inet = {em: 0.0 for em in EMOTIONS}
    if internet_em and internet_em in EMOTIONS:
        inet[internet_em] = 1.0

    lyr = {em: 0.0 for em in EMOTIONS}
    if lyrics_em and lyrics_em in EMOTIONS:
        lyr[lyrics_em] = 1.0

    has_bias = max(bias.values()) > 0.05
    has_inet = bool(internet_em)
    has_lyr  = bool(lyrics_em)

    combined = {}
    for em in EMOTIONS:
        combined[em] = (0.45 * norm.get(em, 0)
                      + 0.25 * bias.get(em, 0)
                      + 0.15 * inet.get(em, 0)
                      + 0.15 * lyr.get(em,  0))

    best = max(combined, key=combined.get)
    if has_bias and bias.get(best, 0) > 0.15:
        source = "learned"
    elif has_lyr and lyr.get(best, 0) > 0:
        source = "lyrics"
    elif has_inet and inet.get(best, 0) > 0:
        source = "internet"
    else:
        source = "auto"
    return best, source


# ==============================================================================
#  INTERNET META  (iTunes Search API — genre + cover art URL)
# ==============================================================================
def internet_meta(title: str, artist: str = "") -> tuple:
    """Returns (genre_str, cover_url_str). Both may be empty on failure."""
    try:
        term = f"{artist} {title}".strip() if artist else title
        q    = urllib.parse.quote(term)
        url  = (f"https://itunes.apple.com/search"
                f"?term={q}&media=music&entity=song&limit=5")
        req  = urllib.request.Request(url,
                   headers={"User-Agent":"Solace/5.0 (music tagger)"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        for result in data.get("results", []):
            genre = result.get("primaryGenreName", "").lower()
            art   = result.get("artworkUrl100", "")
            if art:
                art = art.replace("100x100bb", "600x600bb")
            if genre or art:
                return genre, art
    except Exception:
        pass
    return "", ""


def genre_to_emotion(genre: str) -> str:
    if not genre: return ""
    lower = genre.lower()
    for key, em in GENRE_MAP.items():
        if key in lower: return em
    return ""


# ==============================================================================
#  LYRICS
# ==============================================================================
def _clean_for_lyrics(s: str) -> str:
    """Strip feat./remaster/official-video noise that breaks lyric matching.
    Keeps non-ASCII characters intact so CJK / Cyrillic / etc. titles survive."""
    if not s: return ""
    s = re.sub(r'\s*[\(\[](?:feat|ft|featuring|prod|with|official|lyrics?|audio|'
               r'video|m/?v|hd|4k|full|remaster(?:ed)?|remix|live|cover|'
               r'visualizer|color\s*coded)[^\)\]]*[\)\]]', '', s, flags=re.I)
    s = re.sub(r'\s+', ' ', s)
    return s.strip()


def fetch_lyrics(title: str, artist: str = "") -> str:
    """Fetch plain lyrics for a song in ANY language.

    Order:
      1. lrclib /api/get   — exact artist+title (fast path)
      2. lrclib /api/search — fuzzy ranked search (best for non-English /
         romanised / loosely-tagged titles; this is the key foreign-language fix)
      3. lyrics.ovh        — last-resort fallback
    """
    title  = (title or "").strip()
    artist = (artist or "").strip()
    if not title: return ""
    ct, ca = _clean_for_lyrics(title), _clean_for_lyrics(artist)
    hdr = {"User-Agent": "Solace/5.0 (https://github.com/solace)"}

    def _get_json(url):
        req = urllib.request.Request(url, headers=hdr)
        with urllib.request.urlopen(req, timeout=9) as r:
            return json.loads(r.read())

    # 1. lrclib exact get — try cleaned, then raw tags
    for a, t in [(ca, ct), (artist, title)]:
        if not t: continue
        try:
            params = {"track_name": t}
            if a: params["artist_name"] = a
            data = _get_json("https://lrclib.net/api/get?" +
                             urllib.parse.urlencode(params))
            lyr = (data.get("plainLyrics") or "").strip()
            if len(lyr) > 20:
                return lyr
        except Exception:
            pass

    # 2. lrclib fuzzy search — handles other languages & messy titles
    queries = []
    if ca and ct: queries.append(f"{ct} {ca}")
    queries.append(ct)
    if title != ct: queries.append(title)
    seen_q = set()
    for q in queries:
        q = q.strip()
        if not q or q.lower() in seen_q: continue
        seen_q.add(q.lower())
        try:
            results = _get_json("https://lrclib.net/api/search?q=" +
                                urllib.parse.quote(q))
            if not isinstance(results, list): continue
            for res in results:
                lyr = (res.get("plainLyrics") or "").strip()
                if len(lyr) > 20:
                    return lyr
        except Exception:
            pass

    # 3. lyrics.ovh fallback
    if ca and ct:
        try:
            url = (f"https://api.lyrics.ovh/v1/"
                   f"{urllib.parse.quote(ca, safe='')}/"
                   f"{urllib.parse.quote(ct, safe='')}")
            lyr = (_get_json(url).get("lyrics") or "").strip()
            if len(lyr) > 20:
                return lyr
        except Exception:
            pass
    return ""


def _emotion_from_lyrics(lyrics: str) -> str:
    if not lyrics or len(lyrics) < 20: return ""
    return detect_emotion(lyrics)


# ==============================================================================
#  LYRICS PERSISTENCE  (separate file — keeps library.json small)
# ==============================================================================
def load_lyrics_db() -> dict:
    if not LYRICS_FILE.exists(): return {}
    try:
        with open(LYRICS_FILE, encoding="utf-8") as f: return json.load(f)
    except Exception: return {}


def save_lyrics_db(db: dict):
    try:
        with open(LYRICS_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
    except Exception: pass


# ==============================================================================
#  USER EMOTION DETECTION
# ==============================================================================
def detect_emotion(text: str) -> str:
    clean  = re.sub(r"[^\w\s]", " ", text.lower())
    tokens = set(clean.split())
    scores = {em: 0 for em in EMOTIONS}
    for em, words in _KW.items():
        for w in words:
            if w in tokens or (len(w) > 4 and w in clean):
                scores[em] += 1
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "melancholic"


# ==============================================================================
#  PLAYLIST FEEDBACK  (the app learns per-goal emotion preferences from comments)
# ==============================================================================
#  Persisted shape:
#    { "biases": { goal: { emotion: weight } }, "log": [ {ts,goal,comment,deltas} ] }
#  weight > 0  → want MORE of this emotion for that goal
#  weight <=-2 → effectively BANNED from that goal's playlists
BIAS_MIN, BIAS_MAX, BAN_THRESHOLD = -5, 5, -2

# Directional words: do we want MORE or LESS of the emotion mentioned nearby?
_FB_POS = {"more","want","wants","wanted","add","adding","prefer","like","liked",
           "love","loved","increase","extra","give","need","needs","include","please"}
_FB_NEG = {"less","fewer","few","only","no","not","without","remove","removing",
           "avoid","stop","hate","hated","dont","reduce","never","too","drop","skip"}

# Words → emotion (curated to avoid false positives)
_FB_EMO = {
    "happy":"happy","happier":"happy","upbeat":"happy","cheerful":"happy",
    "joyful":"happy","joy":"happy","fun":"happy","positive":"happy","bright":"happy",
    "energetic":"energetic","energy":"energetic","exciting":"energetic",
    "excited":"energetic","exited":"energetic","hype":"energetic","hyped":"energetic",
    "pumped":"energetic","lively":"energetic","party":"energetic","dance":"energetic",
    "upbeat ":"energetic","fast":"energetic","intense":"energetic",
    "calm":"calm","calmer":"calm","relaxing":"calm","relaxed":"calm","chill":"calm",
    "peaceful":"calm","mellow":"calm","soothing":"calm","soft":"calm","slow":"calm",
    "sad":"sad","sadder":"sad","depressing":"sad","gloomy":"sad","down":"sad",
    "crying":"sad","tearful":"sad","sorrow":"sad","miserable":"sad",
    "melancholic":"melancholic","melancholy":"melancholic","nostalgic":"melancholic",
    "wistful":"melancholic","bittersweet":"melancholic","moody":"melancholic",
    "angry":"angry","aggressive":"angry","rage":"angry","heavy":"angry","mad":"angry",
    "focused":"focused","focus":"focused","concentration":"focused","study":"focused",
    "studying":"focused","productive":"focused",
}


def parse_feedback(text: str) -> dict:
    """Read free-text feedback → {emotion: delta}.  + = want more, - = want less.

    Tracks a running 'sign' set by words like more/less and applies it to the
    emotion words that follow, resetting on contrast words (but/however) so
    'no sad but more happy' splits correctly."""
    if not text: return {}
    clean = re.sub(r"[^\w\s]", " ", text.lower())
    clean = re.sub(r"\b(but|however|though|although|whereas|instead)\b", " | ", clean)
    deltas = {em: 0 for em in EMOTIONS}
    sign = 0
    for tok in clean.split():
        if tok == "|":                       sign = 0;  continue
        if tok in _FB_NEG:                    sign = -1; continue
        if tok in _FB_POS:                    sign = +1; continue
        em = _FB_EMO.get(tok)
        if em:
            deltas[em] += (sign if sign != 0 else +1)   # bare mention = mild "more"
    return {em: d for em, d in deltas.items() if d != 0}


def load_feedback() -> dict:
    blank = {"biases": {}, "mood_biases": {}, "affinity": {}, "log": []}
    if not FEEDBACK_FILE.exists(): return blank
    try:
        with open(FEEDBACK_FILE, encoding="utf-8") as f: d = json.load(f)
        for k, v in blank.items(): d.setdefault(k, v)
        return d
    except Exception:
        return blank


def save_feedback(fb: dict):
    try:
        with open(FEEDBACK_FILE, "w", encoding="utf-8") as f:
            json.dump(fb, f, ensure_ascii=False, indent=2)
    except Exception: pass


# ==============================================================================
#  PLAYLIST BUILDER  (weight model: research profile + learned biases + online)
# ==============================================================================
def emotion_weights(goal, custom_tags, bias=None, mood_bias=None, affinity=None):
    """Combine every signal into one weight per emotion/tag for this goal.
      • research GOAL_PROFILE (prefer ranked +, avoid −)
      • online-learned affinity (small +)
      • learned per-goal feedback bias (×2)
      • learned 'when I feel X' mood→tag bias (×2)
    Returns {emotion: weight}. weight <= BAN_THRESHOLD means 'keep it out'."""
    bias = bias or {}; mood_bias = mood_bias or {}; affinity = affinity or {}
    prof = GOAL_PROFILE.get(goal, GOAL_PROFILE["calm me down"])
    w = {em: 0.0 for em in EMOTIONS + list(custom_tags.keys())}
    pref = prof["prefer"]
    for i, em in enumerate(pref):
        w[em] = w.get(em, 0) + (len(pref) - i) * 2.0      # ranked preference
    for em in prof["avoid"]:
        w[em] = w.get(em, 0) - 6.0                        # strong keep-out
    for em, a in affinity.items():
        w[em] = w.get(em, 0) + max(-2.0, min(2.0, a))     # online refinement
    for em, b in bias.items():
        w[em] = w.get(em, 0) + b * 2.0                    # learned goal feedback
    for tag, b in mood_bias.items():
        w[tag] = w.get(tag, 0) + b * 2.0                  # learned mood→tag pref
    return w


def _journey_order(goal, emotions):
    """Order chosen emotions into a gentle build-up (uplift/energise) or
    wind-down (calm/sleep); otherwise strongest preference first."""
    if goal in ("lift my mood", "energise me"):
        return sorted(emotions, key=lambda e: ENERGY_RANK.get(e, 3))
    if goal in ("calm me down", "help me sleep"):
        return sorted(emotions, key=lambda e: -ENERGY_RANK.get(e, 3))
    return emotions


def build_playlist(songs, user_em, goal, custom_tags, n=50,
                   bias=None, mood_bias=None, affinity=None):
    weights = emotion_weights(goal, custom_tags, bias, mood_bias, affinity)
    banned  = {em for em, x in weights.items() if x <= BAN_THRESHOLD}
    pos     = {em: x for em, x in weights.items() if x > 0}
    if not pos:   # nothing positive — fall back to the goal's preferred list
        pos = {em: 1.0 for em in GOAL_PROFILE.get(goal, GOAL_PROFILE["calm me down"])["prefer"]}

    pool = {em: [] for em in EMOTIONS + list(custom_tags.keys())}
    for s in songs:
        if s.emotion in pool: pool[s.emotion].append(s)
    for em in pool: random.shuffle(pool[em])

    n = min(n, len(songs))
    # Allocate slots proportional to positive weight
    total  = sum(pos.values())
    counts = {em: int(round(n * x / total)) for em, x in pos.items()}
    order  = _journey_order(goal, [em for em in pos if counts.get(em, 0) > 0] or list(pos))

    # Optional single "bridge" song matching how the user feels now (iso-principle),
    # but only when that emotion isn't one we're trying to avoid.
    prof = GOAL_PROFILE.get(goal, GOAL_PROFILE["calm me down"])
    targets = []
    if prof.get("bridge") and user_em not in banned and pool.get(user_em):
        targets.append(user_em)
    for em in order:
        targets += [em] * counts.get(em, 0)
    # pad / trim to exactly n, cycling through the preferred order
    idx = 0
    while len(targets) < n and order:
        targets.append(order[idx % len(order)]); idx += 1
    targets = targets[:n]

    playlist, used = [], set()
    for target in targets:
        candidates = [s for s in pool.get(target, []) if id(s) not in used]
        if not candidates:
            for nearby in _NEARBY.get(target, []):
                if nearby in banned: continue
                candidates = [s for s in pool.get(nearby, []) if id(s) not in used]
                if candidates: break
        if not candidates:
            candidates = [s for s in songs
                          if id(s) not in used and s.emotion not in banned]
        if not candidates:   # last resort — never stop short
            candidates = [s for s in songs if id(s) not in used]
        if not candidates: break
        playlist.append(candidates[0]); used.add(id(candidates[0]))
    return playlist


# ── Online learning: teach emotion affinity per goal from iTunes metadata ─────
def learn_affinity_online(goal: str) -> dict:
    """Search the iTunes catalogue for this goal's mood terms, tally the genres
    of the results, map them to emotions (GENRE_MAP) and return a small affinity
    weight per emotion. Best-effort; returns {} offline."""
    term = GOAL_QUERY.get(goal)
    if not term: return {}
    try:
        q   = urllib.parse.quote(term)
        url = (f"https://itunes.apple.com/search?term={q}"
               f"&media=music&entity=song&limit=80")
        req = urllib.request.Request(url, headers={"User-Agent": "Solace/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
    except Exception:
        return {}
    tally = {}
    for res in data.get("results", []):
        em = genre_to_emotion((res.get("primaryGenreName") or "").lower())
        if em: tally[em] = tally.get(em, 0) + 1
    if not tally: return {}
    top = max(tally.values())
    # Normalise to 0..2 so it only nudges, never overrides profile/feedback
    return {em: round(2.0 * c / top, 2) for em, c in tally.items()}


# ==============================================================================
#  HELPERS
# ==============================================================================
def _fmt_dur(sec):
    if not sec or sec <= 0: return "--:--"
    m, s = divmod(int(sec), 60); return f"{m}:{s:02d}"


# ==============================================================================
#  SONG DATA CLASS
# ==============================================================================
class Song:
    __slots__ = ("path","name","title","artist","features","emotion",
                 "duration","busy","failed","user_override","tag_source",
                 "internet_genre","internet_emotion","original_auto","cover_url")

    def __init__(self, path: str):
        self.path     = path
        meta          = get_meta(path)
        self.name     = Path(path).stem
        self.title    = meta["title"] or ""
        self.artist   = meta["artist"] or ""
        # If ID3 tags are empty, parse from filename
        if not self.artist or not self.title:
            pa, pt = _parse_artist_title(self.name)
            if pa and not self.artist:  self.artist = pa
            if pt and not self.title:   self.title  = pt
        if not self.title: self.title = self.name
        self.features: dict = {}
        self.emotion  = "unknown"
        self.duration = meta["duration"]
        self.busy     = True
        self.failed   = False
        self.user_override   = False
        self.tag_source      = "auto"
        self.internet_genre  = ""
        self.internet_emotion= ""
        self.original_auto   = ""
        self.cover_url       = ""

    def to_dict(self) -> dict:
        return {
            "path":self.path,"name":self.name,"title":self.title,
            "artist":self.artist,"emotion":self.emotion,
            "features":self.features,"duration":self.duration,
            "user_override":self.user_override,"tag_source":self.tag_source,
            "internet_genre":self.internet_genre,
            "internet_emotion":self.internet_emotion,
            "original_auto":self.original_auto,
            "cover_url":self.cover_url,
        }

    @staticmethod
    def from_dict(d: dict) -> "Song":
        s = Song.__new__(Song)
        s.path     = d["path"]
        s.name     = d.get("name", Path(d["path"]).stem)
        s.title    = d.get("title", s.name)
        s.artist   = d.get("artist", "")
        # Backfill artist/title from filename if missing
        if not s.artist or s.title == s.name:
            pa, pt = _parse_artist_title(s.name)
            if pa and not s.artist: s.artist = pa
            if pt and s.title == s.name: s.title = pt
        s.emotion  = d.get("emotion","unknown")
        s.features = d.get("features",{})
        s.duration = d.get("duration",0.0)
        # Backfill duration from stored features
        if s.duration <= 0 and s.features.get("full_duration",0) > 0:
            s.duration = s.features["full_duration"]
        s.busy     = False
        s.failed   = False
        s.user_override   = d.get("user_override",False)
        s.tag_source      = d.get("tag_source","auto")
        s.internet_genre  = d.get("internet_genre","")
        s.internet_emotion= d.get("internet_emotion","")
        s.original_auto   = d.get("original_auto","")
        s.cover_url       = d.get("cover_url","")
        return s


# ==============================================================================
#  PERSISTENCE
# ==============================================================================
def save_library(songs, custom_tags):
    try:
        data = {"custom_tags": custom_tags,
                "songs": [s.to_dict() for s in songs if not s.busy and not s.failed]}
        with open(LIBRARY_FILE,"w",encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception: pass


def load_library():
    if not LIBRARY_FILE.exists(): return [], {}
    try:
        with open(LIBRARY_FILE, encoding="utf-8") as f: data = json.load(f)
        custom_tags = data.get("custom_tags", {})
        songs = [Song.from_dict(d) for d in data.get("songs", [])
                 if os.path.exists(d.get("path",""))]
        return songs, custom_tags
    except Exception: return [], {}


def save_playlists(pls):
    try:
        with open(PLAYLISTS_FILE,"w",encoding="utf-8") as f:
            json.dump(pls, f, ensure_ascii=False, indent=2)
    except Exception: pass


def load_playlists():
    if not PLAYLISTS_FILE.exists(): return []
    try:
        with open(PLAYLISTS_FILE, encoding="utf-8") as f: return json.load(f)
    except Exception: return []


# ==============================================================================
#  NOW-PLAYING OVERLAY  (slide-up panel — no separate window)
# ==============================================================================
class NowPlayingOverlay(Frame):
    """
    Slides up over the main window when the user clicks the song name.
    No new Toplevel — lives as a placed Frame inside app.root.
    """
    def __init__(self, app: "SolaceApp"):
        super().__init__(app.root, bg=BG)
        self.app             = app
        self._visible        = False
        self._anim_id        = None
        self._cover_img      = None
        self._cover_cache: dict = {}
        self._last_song      = None
        self._lyrics_editing = False
        self._build_ui()

    # ── Slide animation ───────────────────────────────────────────────────────
    def show(self):
        if self._visible: return
        self._visible = True
        rh = self.app.root.winfo_height()
        rw = self.app.root.winfo_width()
        self.place(x=0, y=rh, width=rw, height=rh)
        self.lift()
        self._anim_step(rh, going_down=False)
        self._tick()

    def hide(self):
        if not self._visible: return
        try:   cur_y = self.winfo_y()
        except Exception: cur_y = 0
        self._anim_step(cur_y, going_down=True, done=self._finish_hide)

    def _finish_hide(self):
        self.place_forget()
        self._visible = False

    def _anim_step(self, cur_y, going_down: bool, done=None):
        if self._anim_id:
            self.app.root.after_cancel(self._anim_id); self._anim_id = None
        # Re-fetch dimensions every frame so window resize doesn't break layout
        rh = self.app.root.winfo_height()
        rw = self.app.root.winfo_width()
        target_y = rh if going_down else 0
        dist = abs(target_y - cur_y)
        step = max(28, dist // 5)
        if dist <= step:
            self.place(x=0, y=target_y, width=rw, height=rh)
            if done: done()
            return
        new_y = cur_y + (step if going_down else -step)
        self.place(x=0, y=new_y, width=rw, height=rh)
        self._anim_id = self.app.root.after(
            16, lambda: self._anim_step(new_y, going_down, done))

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        # Drag-handle / close bar at top
        hdr = Frame(self, bg="#0a0a0a", pady=10, padx=20, cursor="hand2")
        hdr.pack(fill=X)
        hdr.bind("<Button-1>", lambda _: self.hide())
        lbl = Label(hdr, text="⌄  Now Playing", font=(FF,11,"bold"),
                    bg="#0a0a0a", fg=TXT_DIM, cursor="hand2")
        lbl.pack(side=LEFT)
        lbl.bind("<Button-1>", lambda _: self.hide())
        tip = Label(hdr, text="click to close", font=(FF,8),
                    bg="#0a0a0a", fg=TXT_DIM, cursor="hand2")
        tip.pack(side=RIGHT)
        tip.bind("<Button-1>", lambda _: self.hide())
        Frame(self, bg=BORDER, height=1).pack(fill=X)

        # Two-column content area
        content = Frame(self, bg=BG)
        content.pack(fill=BOTH, expand=True)

        # Left: cover art
        left = Frame(content, bg=BG, width=320)
        left.pack(side=LEFT, fill=Y)
        left.pack_propagate(False)
        self._cover_lbl = Label(left, bg="#0a0a0a", text="♪",
                                font=(FF,60), fg="#2a2a2a")
        self._cover_lbl.pack(fill=BOTH, expand=True, padx=20, pady=20)

        # Right: info + controls + tabs
        right = Frame(content, bg=BG)
        right.pack(side=LEFT, fill=BOTH, expand=True)

        # Song info
        info = Frame(right, bg=BG, pady=16, padx=18)
        info.pack(fill=X)
        self._now_name2  = StringVar(value="Nothing playing")
        self._now_art2   = StringVar(value="")
        Label(info, textvariable=self._now_name2, font=(FF,17,"bold"),
              bg=BG, fg=TXT, anchor=W, wraplength=520).pack(fill=X)
        Label(info, textvariable=self._now_art2, font=(FF,11),
              bg=BG, fg=TXT_MID, anchor=W).pack(fill=X, pady=(2,0))
        em_row = Frame(info, bg=BG); em_row.pack(fill=X, pady=(4,0))
        self._em_lbl = Label(em_row, text="", font=(FF,10,"bold"),
                             bg=BG, fg=TXT_DIM, anchor=W, cursor="hand2")
        self._em_lbl.pack(side=LEFT)
        self._em_lbl.bind("<Button-1>", self._emotion_menu)
        self._em_edit_btn = Button(em_row, text="✏ change tag", font=(FF,8),
                                   bg=BG4, fg=TXT_MID, relief=FLAT, cursor="hand2",
                                   padx=8, pady=2, activebackground=BG3,
                                   activeforeground=TXT, command=self._emotion_menu)
        self._em_edit_btn.pack(side=LEFT, padx=(10,0))

        # Controls
        ctl = Frame(right, bg=BG, pady=8, padx=18)
        ctl.pack(fill=X)
        self._fs_shuf_btn = Button(ctl, text="🔀", font=(FF,14), bg=BG, fg=TXT_DIM,
               relief=FLAT, cursor="hand2", padx=8,
               activebackground=BG3, activeforeground=TXT,
               command=self.app._toggle_shuffle)
        self._fs_shuf_btn.pack(side=LEFT)
        Button(ctl, text="⏮", font=(FF,20), bg=BG, fg=TXT_MID,
               relief=FLAT, cursor="hand2", padx=10,
               activebackground=BG3, activeforeground=TXT,
               command=self.app._prev_song).pack(side=LEFT)
        self._fs_play_btn = Button(ctl, text="▶", font=(FF,28,"bold"),
               bg=BG, fg=ACCENT, relief=FLAT, cursor="hand2", padx=14,
               activebackground=BG3, activeforeground=ACCENT,
               command=self.app._toggle_play)
        self._fs_play_btn.pack(side=LEFT)
        Button(ctl, text="⏭", font=(FF,20), bg=BG, fg=TXT_MID,
               relief=FLAT, cursor="hand2", padx=10,
               activebackground=BG3, activeforeground=TXT,
               command=self.app._next_song).pack(side=LEFT)
        self._fs_rep_btn = Button(ctl, text="🔁", font=(FF,14), bg=BG, fg=TXT_DIM,
               relief=FLAT, cursor="hand2", padx=8,
               activebackground=BG3, activeforeground=TXT,
               command=self.app._toggle_repeat)
        self._fs_rep_btn.pack(side=LEFT)

        # Volume
        vol_f = Frame(right, bg=BG, padx=18, pady=4)
        vol_f.pack(fill=X)
        Label(vol_f, text="🔇", font=(FF,10), bg=BG, fg=TXT_DIM).pack(side=LEFT)
        Scale(vol_f, variable=self.app._vol_var, from_=0.0, to=1.0,
              resolution=0.01, orient=HORIZONTAL, length=160,
              bg=BG, fg=TXT_DIM, troughcolor=BORDER,
              highlightthickness=0, showvalue=False, sliderlength=14,
              command=self.app._on_volume).pack(side=LEFT, padx=6)
        Label(vol_f, text="🔊", font=(FF,10), bg=BG, fg=TXT_DIM).pack(side=LEFT)

        Frame(right, bg=BORDER, height=1).pack(fill=X, padx=8)

        # Progress bar
        prog = Frame(right, bg=BG4, pady=10, padx=18)
        prog.pack(fill=X)
        self._build_fs_progress(prog)

        Frame(right, bg=BORDER, height=1).pack(fill=X)

        # Tabs: Lyrics | Queue
        tab_bar = Frame(right, bg=BG2)
        tab_bar.pack(fill=X)
        self._fstab_btns = {}
        for t in ("Lyrics", "Queue"):
            b = Button(tab_bar, text=t, font=(FF,10,"bold"),
                       bg=BG2, fg=TXT_DIM, relief=FLAT, cursor="hand2",
                       padx=18, pady=8, bd=0,
                       activebackground=BG3, activeforeground=TXT,
                       command=lambda x=t: self._switch(x))
            b.pack(side=LEFT)
            self._fstab_btns[t] = b
        Frame(right, bg=BORDER, height=1).pack(fill=X)

        self._fs_content = Frame(right, bg=BG)
        self._fs_content.pack(fill=BOTH, expand=True)
        self._build_lyrics_panel()
        self._build_queue_panel()
        self._switch("Lyrics")

    def _build_lyrics_panel(self):
        f = Frame(self._fs_content, bg=BG)
        self._lyr_frame = f

        bar = Frame(f, bg=BG, pady=8, padx=14)
        bar.pack(fill=X)
        Label(bar, text="Lyrics", font=(FF,12,"bold"), bg=BG, fg=TXT).pack(side=LEFT)
        self._lyr_src_lbl = Label(bar, text="", font=(FF,9), bg=BG, fg=TXT_DIM)
        self._lyr_src_lbl.pack(side=LEFT, padx=10)
        self._lyr_save_btn = Button(bar, text="Save", font=(FF,9,"bold"),
                                    bg=ACCENT, fg=WHITE, relief=FLAT, cursor="hand2",
                                    padx=10, pady=4, state=DISABLED,
                                    activebackground=ACCENT_DK,
                                    command=self._save_lyrics)
        self._lyr_save_btn.pack(side=RIGHT, padx=(4,0))
        self._lyr_edit_btn = Button(bar, text="✏ Edit", font=(FF,9),
                                    bg=BG4, fg=TXT_MID, relief=FLAT,
                                    cursor="hand2", padx=10, pady=4,
                                    activebackground=BG3, activeforeground=TXT,
                                    command=self._toggle_edit)
        self._lyr_edit_btn.pack(side=RIGHT, padx=(0,4))
        self._lyr_fetch_btn = Button(bar, text="🔍 Fetch", font=(FF,9),
                                     bg=BG4, fg=TXT_MID, relief=FLAT,
                                     cursor="hand2", padx=10, pady=4,
                                     activebackground=BG3, activeforeground=TXT,
                                     command=self._fetch_lyrics_now)
        self._lyr_fetch_btn.pack(side=RIGHT, padx=(0,4))

        wrap = Frame(f, bg=BG)
        wrap.pack(fill=BOTH, expand=True, padx=14, pady=(0,12))
        sb = ttk.Scrollbar(wrap, orient=VERTICAL)
        self._lyr_txt = Text(wrap, font=(FF,11), bg=BG3, fg=TXT,
                             insertbackground=TXT, relief=FLAT,
                             wrap=WORD, state=DISABLED,
                             highlightthickness=0, padx=16, pady=12,
                             yscrollcommand=sb.set)
        sb.config(command=self._lyr_txt.yview)
        sb.pack(side=RIGHT, fill=Y)
        self._lyr_txt.pack(side=LEFT, fill=BOTH, expand=True)

    def _build_queue_panel(self):
        f = Frame(self._fs_content, bg=BG)
        self._queue_frame = f
        Label(f, text="Up Next", font=(FF,12,"bold"),
              bg=BG, fg=TXT, anchor=W, padx=14, pady=10).pack(fill=X)
        Frame(f, bg=BORDER, height=1).pack(fill=X)
        wrap = Frame(f, bg=BG); wrap.pack(fill=BOTH, expand=True)
        self._q_cv = Canvas(wrap, bg=BG, bd=0, highlightthickness=0)
        sb = ttk.Scrollbar(wrap, orient=VERTICAL, command=self._q_cv.yview)
        self._q_fr = Frame(self._q_cv, bg=BG)
        self._q_cv.configure(yscrollcommand=sb.set)
        sb.pack(side=RIGHT, fill=Y)
        self._q_cv.pack(side=LEFT, fill=BOTH, expand=True)
        qwin = self._q_cv.create_window((0,0), window=self._q_fr, anchor=NW)
        self._q_fr.bind("<Configure>",
                        lambda e: self._q_cv.configure(scrollregion=self._q_cv.bbox("all")))
        self._q_cv.bind("<Configure>",
                        lambda e: self._q_cv.itemconfig(qwin, width=e.width))
        self._q_win = qwin
        self.app._scroll_canvases.add(self._q_cv)
        self._render_queue()

    def _render_queue(self):
        for w in self._q_fr.winfo_children(): w.destroy()
        app = self.app
        manual = list(app._next_up)
        playlist_ahead = []
        if app._queue and 0 <= app._q_idx < len(app._queue):
            playlist_ahead = app._queue[app._q_idx+1:]
        all_ahead = manual + playlist_ahead
        if not all_ahead:
            Label(self._q_fr, text="Queue is empty.\n\nRight-click a song → Add to Queue",
                  font=(FF,10), bg=BG, fg=TXT_DIM, justify=CENTER, pady=24).pack()
            return
        n_manual = len(manual)
        shown = all_ahead[:20]
        for i, song in enumerate(shown):
            is_manual = i < n_manual
            row_bg = "#0f1a15" if is_manual else BG
            # Index used by the click handler to jump straight to this track
            if is_manual: jump_idx = i                          # index in _next_up
            else:         jump_idx = app._q_idx + 1 + (i-n_manual)  # index in _queue
            clickable = []   # widgets that should trigger "skip to this song"
            row = Frame(self._q_fr, bg=row_bg, pady=6, cursor="hand2"); row.pack(fill=X)
            clickable.append(row)
            if is_manual:
                lead = Label(row, text="▸", font=(FF,9), bg=row_bg,
                             fg=ACCENT, cursor="hand2"); lead.pack(side=LEFT, padx=(8,4))
            else:
                lead = Label(row, text=f"{i+1-n_manual}", font=(FF,9), bg=row_bg,
                             fg=TXT_DIM, width=3, anchor=E, cursor="hand2")
                lead.pack(side=LEFT, padx=(8,4))
            clickable.append(lead)
            em   = song.emotion
            col  = EM_COL.get(em,"#aaa"); bg_c = EM_BG.get(em,"#1a1a1a")
            icon = EM_ICON.get(em,"♪")
            Label(row, text=f" {icon} ", font=(FF,8), bg=bg_c, fg=col,
                  padx=3, pady=1).pack(side=RIGHT, padx=(0,8))
            Label(row, text=_fmt_dur(song.duration), font=(FF,9), bg=row_bg,
                  fg=TXT_DIM).pack(side=RIGHT, padx=(0,6))
            if is_manual:
                ri = i  # real index in _next_up
                Button(row, text="✕", font=(FF,8), bg=row_bg, fg="#EF5350",
                       relief=FLAT, cursor="hand2", padx=4, pady=1,
                       activebackground=row_bg, activeforeground="#EF5350",
                       command=lambda x=ri: self._q_remove(x)
                       ).pack(side=RIGHT, padx=(0,2))
                if ri < n_manual - 1:
                    Button(row, text="↓", font=(FF,9), bg=row_bg, fg=TXT_MID,
                           relief=FLAT, cursor="hand2", padx=4, pady=1,
                           activebackground=row_bg, activeforeground=TXT,
                           command=lambda x=ri: self._q_down(x)
                           ).pack(side=RIGHT, padx=(0,2))
                if ri > 0:
                    Button(row, text="↑", font=(FF,9), bg=row_bg, fg=TXT_MID,
                           relief=FLAT, cursor="hand2", padx=4, pady=1,
                           activebackground=row_bg, activeforeground=TXT,
                           command=lambda x=ri: self._q_up(x)
                           ).pack(side=RIGHT, padx=(0,2))
            info = Frame(row, bg=row_bg, cursor="hand2")
            info.pack(side=LEFT, fill=X, expand=True)
            clickable.append(info)
            disp = song.title or song.name
            name_lbl = Label(info, text=(disp[:42]+"…") if len(disp)>42 else disp,
                             font=(FF,10,"bold"), bg=row_bg, fg=TXT,
                             anchor=W, cursor="hand2")
            name_lbl.pack(fill=X); clickable.append(name_lbl)
            if song.artist:
                art_lbl = Label(info, text=song.artist, font=(FF,8), bg=row_bg,
                                fg=TXT_DIM, anchor=W, cursor="hand2")
                art_lbl.pack(fill=X); clickable.append(art_lbl)
            # Clicking anywhere on the row (except the ✕ ↑ ↓ buttons) skips to it
            for w in clickable:
                w.bind("<Button-1>",
                       lambda e, s=song, m=is_manual, j=jump_idx: self._q_jump(s, m, j))
            Frame(self._q_fr, bg=BORDER, height=1).pack(fill=X)
        if len(all_ahead) > len(shown):
            Label(self._q_fr, text=f"  + {len(all_ahead)-len(shown)} more…",
                  font=(FF,9), bg=BG, fg=TXT_DIM, anchor=W, pady=6).pack(fill=X)
        self._q_cv.update_idletasks()
        self._q_cv.configure(scrollregion=self._q_cv.bbox("all"))

    def _q_jump(self, song, is_manual: bool, idx: int):
        """Skip straight to a clicked queue item and start playing it."""
        app = self.app
        if is_manual:
            # Pull the song out of the manual 'play next' list and play it now
            if 0 <= idx < len(app._next_up):
                s = app._next_up.pop(idx)
                app._play_queued_song(s)
        else:
            # Song already sits in the main queue ahead of the current track
            if 0 <= idx < len(app._queue):
                app._q_idx = idx
                app._play_current()
        self._render_queue()

    def _q_remove(self, idx: int):
        if 0 <= idx < len(self.app._next_up):
            self.app._next_up.pop(idx); self._render_queue()

    def _q_up(self, idx: int):
        nu = self.app._next_up
        if 0 < idx < len(nu):
            nu[idx-1], nu[idx] = nu[idx], nu[idx-1]; self._render_queue()

    def _q_down(self, idx: int):
        nu = self.app._next_up
        if 0 <= idx < len(nu)-1:
            nu[idx], nu[idx+1] = nu[idx+1], nu[idx]; self._render_queue()

    def _build_fs_progress(self, parent):
        Label(parent, textvariable=self.app._time_cur,
              font=(FF,9), bg=BG4, fg=TXT_MID, width=5).pack(side=LEFT)
        cv = Canvas(parent, height=14, bg="#2a2a2a", highlightthickness=0, cursor="hand2")
        cv.pack(side=LEFT, fill=X, expand=True, padx=8)
        fill = cv.create_rectangle(0, 0, 0, 14, fill=ACCENT, width=0)
        dot  = cv.create_oval(-7,-3, 7,17, fill=WHITE, outline="", state=HIDDEN)
        cv.bind("<Button-1>",  lambda e: self._fs_seek(e, cv, fill, dot))
        cv.bind("<B1-Motion>", lambda e: self._fs_seek(e, cv, fill, dot))
        cv.bind("<Enter>",  lambda e: cv.itemconfig(dot, state=NORMAL))
        cv.bind("<Leave>",  lambda e: cv.itemconfig(dot, state=HIDDEN))
        self._fs_cv   = cv
        self._fs_fill = fill
        self._fs_dot  = dot
        Label(parent, textvariable=self.app._time_tot,
              font=(FF,9), bg=BG4, fg=TXT_MID, width=5).pack(side=LEFT)

    # ── Tab switching ──────────────────────────────────────────────────────────
    def _switch(self, tab):
        self._lyr_frame.pack_forget()
        self._queue_frame.pack_forget()
        if tab == "Lyrics":
            self._lyr_frame.pack(fill=BOTH, expand=True)
        else:
            self._render_queue()
            self._queue_frame.pack(fill=BOTH, expand=True)
        for t, b in self._fstab_btns.items():
            b.config(fg=ACCENT if t==tab else TXT_DIM,
                     bg=BG3   if t==tab else BG2)

    def _current_song(self):
        app = self.app
        if app._queue and 0 <= app._q_idx < len(app._queue):
            return app._queue[app._q_idx]
        return None

    # ── Change the playing song's emotion tag from the full player ─────────────
    def _emotion_menu(self, _event=None):
        song = self._current_song()
        if not song:
            self.app._status.set("Nothing playing — start a song to tag it"); return
        menu = Menu(self.app.root, tearoff=0, bg=BG3, fg=TXT,
                    activebackground=ACCENT, activeforeground=WHITE,
                    font=(FF,10), relief=FLAT, bd=0)
        menu.add_command(label="  Change emotion tag", state=DISABLED)
        menu.add_separator()
        for em in EMOTIONS + list(self.app.custom_tags.keys()):
            icon = EM_ICON.get(em) or self.app.custom_tags.get(em,{}).get("icon","♪")
            mark = "  ●" if em == song.emotion else "   "
            menu.add_command(label=f"{mark} {icon} {em}",
                             command=lambda e=em: self._change_emotion(e))
        try:   menu.tk_popup(self.app.root.winfo_pointerx(),
                             self.app.root.winfo_pointery())
        finally: menu.grab_release()

    def _change_emotion(self, em):
        song = self._current_song()
        if not song: return
        app = self.app
        old = song.emotion
        if old != em and song.features and not song.busy:
            app.corrections = record_correction(app.corrections, song.features, old, em)
        song.emotion       = em
        song.user_override = True
        song.tag_source    = "user"
        save_library(app.songs, app.custom_tags)
        app._uq.put(song)   # refresh the library row in place
        col  = EM_COL.get(em)  or app.custom_tags.get(em,{}).get("color", TXT_DIM)
        icon = EM_ICON.get(em) or app.custom_tags.get(em,{}).get("icon","♪")
        self._em_lbl.config(text=f"{icon} {em}", fg=col)
        n = len(app.corrections)
        app._status.set(f"✏ Tag changed to {em} for '{song.title or song.name}'  ·  "
                        f"model now has {n} correction{'s' if n!=1 else ''}")

    # ── Lyrics editing ─────────────────────────────────────────────────────────
    def _toggle_edit(self):
        self._lyrics_editing = not self._lyrics_editing
        self._lyr_txt.config(state=NORMAL if self._lyrics_editing else DISABLED)
        self._lyr_edit_btn.config(
            text="Cancel" if self._lyrics_editing else "✏ Edit",
            fg="#EF5350"  if self._lyrics_editing else TXT_MID)
        self._lyr_save_btn.config(state=NORMAL if self._lyrics_editing else DISABLED,
                                  bg=ACCENT if self._lyrics_editing else BG4)

    def _save_lyrics(self):
        song = self._current_song()
        if not song: return
        text = self._lyr_txt.get("1.0", END).strip()
        self.app.lyrics_db[song.path] = {"lyrics": text, "source": "user"}
        save_lyrics_db(self.app.lyrics_db)
        # Update emotion from new lyrics
        lyr_em = _emotion_from_lyrics(text)
        if lyr_em and not song.user_override and song.features:
            inet_em = genre_to_emotion(song.internet_emotion)
            new_em, src = knn_emotion(song.features, self.app.corrections,
                                       inet_em, lyr_em)
            song.emotion    = new_em
            song.tag_source = src
            save_library(self.app.songs, self.app.custom_tags)
        self.app._uq.put(song)   # refresh library row (lyrics ♪ / emotion)
        # Lock editing
        self._lyrics_editing = False
        self._lyr_txt.config(state=DISABLED)
        self._lyr_edit_btn.config(text="✏ Edit", fg=TXT_MID)
        self._lyr_save_btn.config(state=DISABLED, bg=BG4)
        self.app._status.set(f"✏ Lyrics saved for '{song.name}'")

    def _set_lyrics_text(self, text: str):
        self._lyr_txt.config(state=NORMAL)
        self._lyr_txt.delete("1.0", END)
        placeholder = ("No lyrics available.\n\n"
                       "Click  🔍 Fetch  to search again (works for other\n"
                       "languages too), or  ✏ Edit  to add them manually.")
        self._lyr_txt.insert("1.0", text if text else placeholder)
        if not self._lyrics_editing:
            self._lyr_txt.config(state=DISABLED)

    # ── Manual lyrics fetch (on-demand, multi-language) ────────────────────────
    def _fetch_lyrics_now(self):
        song = self._current_song()
        if not song:
            self.app._status.set("Nothing playing — start a song first"); return
        if self._lyrics_editing:   # don't clobber an in-progress edit
            return
        self._lyr_fetch_btn.config(text="… searching", state=DISABLED)
        self.app._status.set(f"🔍 Searching lyrics for '{song.title or song.name}'…")
        threading.Thread(target=self._fetch_lyrics_bg, args=(song,),
                         daemon=True).start()

    def _fetch_lyrics_bg(self, song):
        lyrics = fetch_lyrics(song.title or song.name, song.artist)
        self.app.root.after(0, lambda: self._fetch_lyrics_done(song, lyrics))

    def _fetch_lyrics_done(self, song, lyrics):
        try: self._lyr_fetch_btn.config(text="🔍 Fetch", state=NORMAL)
        except Exception: pass
        if lyrics:
            self.app.lyrics_db[song.path] = {"lyrics": lyrics, "source": "auto"}
            save_lyrics_db(self.app.lyrics_db)
            lyr_em = _emotion_from_lyrics(lyrics)
            if lyr_em and not song.user_override and song.features:
                inet_em = genre_to_emotion(song.internet_emotion)
                new_em, src = knn_emotion(song.features, self.app.corrections,
                                           inet_em, lyr_em)
                song.emotion    = new_em
                song.tag_source = src
                save_library(self.app.songs, self.app.custom_tags)
            self.app._uq.put(song)
            if song is self._current_song() and not self._lyrics_editing:
                self._set_lyrics_text(lyrics)
            self.app._status.set(f"♪ Lyrics found for '{song.title or song.name}'")
        else:
            self.app._status.set(
                f"No lyrics found for '{song.title or song.name}'  ·  "
                f"try  ✏ Edit  to add them manually")

    # ── Seeking ────────────────────────────────────────────────────────────────
    def _fs_seek(self, event, cv, fill, dot):
        app = self.app
        if not _PYGAME: return
        dur = app._song_dur
        if dur <= 0: return
        w = cv.winfo_width()
        if w <= 1: return
        pct = max(0.0, min(1.0, event.x / w))
        app._do_seek(pct * dur)
        cv.coords(fill, 0, 0, int(w*pct), 14)
        cv.coords(dot, int(w*pct)-7,-3, int(w*pct)+7,17)

    # ── Update tick ────────────────────────────────────────────────────────────
    def _tick(self):
        if not self._visible: return
        app  = self.app

        # Resize sync: keep overlay filling the root even after window resize
        if not self._anim_id:
            try:
                rh = self.app.root.winfo_height()
                rw = self.app.root.winfo_width()
                if self.winfo_width() != rw or self.winfo_height() != rh:
                    self.place(x=0, y=0, width=rw, height=rh)
            except Exception:
                pass

        song = self._current_song()

        # Song change
        if song is not self._last_song:
            self._last_song = song
            if song:
                self._now_name2.set((song.title or song.name)[:60])
                self._now_art2.set(song.artist or "")
                em   = song.emotion
                col  = EM_COL.get(em, TXT_DIM)
                icon = EM_ICON.get(em, "◎")
                self._em_lbl.config(text=f"{icon} {em}", fg=col)
                lyr = app.lyrics_db.get(song.path, {}).get("lyrics", "")
                self._set_lyrics_text(lyr)
                threading.Thread(target=self._load_cover_bg,
                                 args=(song,), daemon=True).start()
            else:
                self._now_name2.set("Nothing playing")
                self._now_art2.set("")
                self._em_lbl.config(text="", fg=TXT_DIM)
                self._set_lyrics_text("")
                self._cover_lbl.config(image="", text="♪", font=(FF,60), fg=TXT_DIM)

        # Refresh lyrics if worker fetched them
        if song and not self._lyrics_editing:
            new_lyr = app.lyrics_db.get(song.path, {}).get("lyrics", "")
            cur_txt = self._lyr_txt.get("1.0","end-1c")
            if new_lyr and "No lyrics" in cur_txt:
                self._set_lyrics_text(new_lyr)

        # Sync controls
        self._fs_play_btn.config(text="⏸" if app._playing else "▶")
        rep_icons = {0: ("🔁", TXT_DIM), 1: ("🔂", ACCENT), 2: ("🔁", ACCENT)}
        ri, rc = rep_icons[app._repeat]
        try: self._fs_rep_btn.config(text=ri, fg=rc)
        except Exception: pass
        try: self._fs_shuf_btn.config(fg=ACCENT if app._shuf_on else TXT_DIM)
        except Exception: pass

        # Sync overlay progress bar
        if app._playing and app._song_dur > 0:
            elapsed = time.time() - app._play_start
            pct = min(elapsed / app._song_dur, 1.0)
            try:
                fw = self._fs_cv.winfo_width()
                if fw > 1:
                    self._fs_cv.coords(self._fs_fill, 0, 0, int(fw*pct), 14)
                    self._fs_cv.coords(self._fs_dot,
                                       int(fw*pct)-7,-3, int(fw*pct)+7,17)
            except Exception: pass

        self.app.root.after(400, self._tick)

    # ── Cover art loading ──────────────────────────────────────────────────────
    def _load_cover_bg(self, song):
        """Background thread: load/download image, schedule main-thread update."""
        if not _PIL: return
        path = song.path

        if path in self._cover_cache:
            self.after(0, lambda p=path: self._apply_cover(self._cover_cache.get(p)))
            return

        img_data = get_embedded_art_data(path)

        if not img_data and song.cover_url:
            try:
                req = urllib.request.Request(song.cover_url,
                          headers={"User-Agent":"Solace/5.0"})
                with urllib.request.urlopen(req, timeout=6) as r:
                    img_data = r.read()
            except Exception:
                pass

        if img_data:
            try:
                pimg = Image.open(io.BytesIO(img_data))
                # Schedule PhotoImage creation on main thread
                self.after(0, lambda p=pimg, k=path: self._finish_cover(p, k))
            except Exception:
                pass
        else:
            self.after(0, lambda: self._cover_lbl.config(
                image="", text="♪", font=(FF,64), fg=TXT_DIM))

    def _finish_cover(self, pimg, path):
        try:
            w = max(200, self._cover_lbl.winfo_width())
            h = max(200, self._cover_lbl.winfo_height())
            sz = min(w, h, 400)
            pimg = pimg.resize((sz, sz), Image.LANCZOS)
            photo = ImageTk.PhotoImage(pimg)
            self._cover_cache[path] = photo
            self._apply_cover(photo)
        except Exception:
            pass

    def _apply_cover(self, photo):
        if not photo: return
        self._cover_img = photo
        self._cover_lbl.config(image=photo, text="", font=(FF,1))


# ==============================================================================
#  APPLICATION
# ==============================================================================
class SolaceApp:
    def __init__(self, root: Tk):
        self.root         = root
        self.songs:       list  = []
        self.custom_tags: dict  = {}
        self.playlists:   list  = []
        self.corrections: list  = load_corrections()
        self.lyrics_db:   dict  = load_lyrics_db()
        self.feedback:    dict  = load_feedback()
        self._overlay: "NowPlayingOverlay" = None  # set at end of _build_ui

        self._rq = queue.Queue()   # analysis results
        self._wq = queue.Queue()   # songs to analyse
        self._mq = queue.Queue()   # songs for metadata (internet + lyrics)
        self._uq = queue.Queue()   # songs whose library row needs an in-place refresh

        self._ph          = True
        self._scroll_tgt  = None
        self._scroll_canvases: set = set()   # registered scrollable canvases
        self._last_render = 0.0
        self._dirty       = False
        self._last_save   = 0.0
        self._active_tab  = "Library"
        self._lib_visible: list = []
        self._lib_rows:   dict = {}   # id(song) -> row Frame, for in-place updates

        # Player state
        self._queue:     list = []
        self._q_idx:     int  = -1
        self._playing         = False
        self._song_dur        = 0.0
        self._play_start      = 0.0
        self._paused_at       = 0.0
        self._next_up:   list = []   # manual "play next" queue
        self._repeat:    int  = 0    # 0=off 1=repeat-one 2=repeat-all
        self._shuf_on:   bool = False
        self._seeking:   bool = False  # guard during scrub drag

        self._setup_window()
        self._build_ui()
        self._start_workers()
        self._load_saved()
        self._poll()
        if _PYGAME: self._update_player()

    # ── Window ────────────────────────────────────────────────────────────────
    def _shortcut_ok(self):
        """True when keyboard shortcuts should fire (not focused on any text input)."""
        w = self.root.focus_get()
        return not isinstance(w, (Entry, Text))

    def _setup_window(self):
        self.root.title("Solace")
        self.root.configure(bg=BG)
        self.root.geometry("1500x920")
        self.root.minsize(1100, 720)
        self.root.bind("<MouseWheel>", self._on_wheel)
        # Keyboard shortcuts — blocked while typing in Entry/Text widgets
        ok = self._shortcut_ok
        self.root.bind("<space>", lambda e: ok() and self._toggle_play())
        self.root.bind("<Right>", lambda e: ok() and self._seek_relative(10))
        self.root.bind("<Left>",  lambda e: ok() and self._seek_relative(-10))
        self.root.bind("<n>",     lambda e: ok() and self._next_song())
        self.root.bind("<p>",     lambda e: ok() and self._prev_song())
        self.root.bind("<r>",     lambda e: ok() and self._toggle_repeat())
        self.root.bind("<s>",     lambda e: ok() and self._toggle_shuffle())
        self.root.bind("<f>",     lambda e: ok() and self._toggle_now_playing())
        self.root.bind("<Up>",    lambda e: ok() and self._change_volume(0.05))
        self.root.bind("<Down>",  lambda e: ok() and self._change_volume(-0.05))

    def _change_volume(self, delta: float):
        v = max(0.0, min(1.0, self._vol_var.get() + delta))
        self._vol_var.set(v)
        if _PYGAME: pygame.mixer.music.set_volume(v)

    # ── UI skeleton ───────────────────────────────────────────────────────────
    def _build_ui(self):
        hdr = Frame(self.root, bg=BG, pady=14); hdr.pack(fill=X, padx=28)
        Label(hdr, text="SOLACE", font=(FF,24,"bold"), bg=BG, fg=ACCENT).pack(side=LEFT)
        Label(hdr, text="  emotion-aware music companion", font=(FF,11),
              bg=BG, fg=TXT_DIM).pack(side=LEFT)
        Frame(self.root, bg=BORDER, height=1).pack(fill=X)

        tab_bar = Frame(self.root, bg=BG2); tab_bar.pack(fill=X)
        self._tab_frames: dict = {}
        self._tab_btns:   dict = {}
        for label in ("Library","Mix","Playlists"):
            btn = Button(tab_bar, text=label, font=(FF,11,"bold"),
                         bg=BG2, fg=TXT_DIM, relief=FLAT, cursor="hand2",
                         padx=24, pady=12, bd=0,
                         activebackground=BG3, activeforeground=TXT,
                         command=lambda l=label: self._switch_tab(l))
            btn.pack(side=LEFT)
            self._tab_btns[label] = btn
        Frame(self.root, bg=BORDER, height=1).pack(fill=X)

        self._content = Frame(self.root, bg=BG); self._content.pack(fill=BOTH, expand=True)
        for label in ("Library","Mix","Playlists"):
            f = Frame(self._content, bg=BG2 if label=="Library" else BG)
            self._tab_frames[label] = f

        self._build_library_tab(self._tab_frames["Library"])
        self._build_mix_tab(self._tab_frames["Mix"])
        self._build_playlists_tab(self._tab_frames["Playlists"])

        Frame(self.root, bg=BORDER, height=1).pack(fill=X)
        self._build_player_bar()
        Frame(self.root, bg=BORDER, height=1).pack(fill=X)
        self._status = StringVar(value="Ready")
        Label(self.root, textvariable=self._status, font=(FF,9), bg=BG,
              fg=TXT_DIM, anchor=W, pady=5).pack(fill=X, padx=20)

        self._switch_tab("Library")
        # Overlay created after all state vars exist
        self._overlay = NowPlayingOverlay(self)

    def _switch_tab(self, label):
        self._active_tab = label          # track which tab is visible
        for f in self._tab_frames.values(): f.pack_forget()
        self._tab_frames[label].pack(fill=BOTH, expand=True)
        for k, btn in self._tab_btns.items():
            btn.config(fg=ACCENT if k==label else TXT_DIM,
                       bg=BG3   if k==label else BG2)
        if label == "Library":   self._render_library()
        elif label == "Playlists": self._render_playlists_tab()

    # ──────────────────────────────────────────────────────────────────────────
    #  LIBRARY TAB
    # ──────────────────────────────────────────────────────────────────────────
    def _build_library_tab(self, parent):
        bar = Frame(parent, bg=BG2, pady=12, padx=16); bar.pack(fill=X)
        Label(bar, text="Library", font=(FF,14,"bold"), bg=BG2, fg=TXT).pack(side=LEFT)

        for txt, cmd in [("↺  Re-tag", self._retag_dialog),
                         ("＋  New Tag", self._new_tag_dialog),
                         ("＋  Add Songs", self._upload)]:
            bg = ACCENT if "Add" in txt else BG4
            fg = WHITE  if "Add" in txt else TXT_MID
            Button(bar, text=txt, font=(FF,10,"bold"), bg=bg, fg=fg,
                   relief=FLAT, cursor="hand2", padx=14, pady=7,
                   activebackground=ACCENT_DK if "Add" in txt else BG3,
                   activeforeground=WHITE if "Add" in txt else TXT,
                   command=cmd).pack(side=RIGHT, padx=(6,0))

        Frame(parent, bg=BORDER, height=1).pack(fill=X)

        ctrl = Frame(parent, bg=BG2, pady=8, padx=16); ctrl.pack(fill=X)
        self._search_var = StringVar()
        self._search_after_id = None
        self._search_var.trace_add("write", lambda *_: self._debounce_search())
        Entry(ctrl, textvariable=self._search_var, font=(FF,10),
              bg=BG3, fg=TXT, insertbackground=TXT, relief=FLAT,
              highlightthickness=1, highlightbackground=BORDER,
              highlightcolor=ACCENT, width=28).pack(side=LEFT, ipady=5, padx=(0,12))

        Label(ctrl, text="Sort:", font=(FF,9), bg=BG2, fg=TXT_DIM).pack(side=LEFT)
        self._sort_var = StringVar(value="Name")
        om = OptionMenu(ctrl, self._sort_var, "Name","Emotion","BPM","Duration","Source",
                        command=lambda _: self._render_library())
        om.config(bg=BG3, fg=TXT, relief=FLAT, font=(FF,9), padx=8, pady=4,
                  activebackground=BG4, highlightthickness=1, highlightbackground=BORDER)
        om["menu"].config(bg=BG3, fg=TXT, font=(FF,9),
                          activebackground=ACCENT, activeforeground=WHITE)
        om.pack(side=LEFT, padx=(4,16))

        self._lib_info = StringVar(value="No songs yet")
        Label(ctrl, textvariable=self._lib_info, font=(FF,9),
              bg=BG2, fg=TXT_DIM).pack(side=RIGHT)

        self._filter_frame = Frame(parent, bg=BG2, pady=8, padx=16)
        self._filter_frame.pack(fill=X)
        self._filter_var = StringVar(value="All")
        self._build_filter_chips()
        Frame(parent, bg=BORDER, height=1).pack(fill=X)

        wrap = Frame(parent, bg=BG2); wrap.pack(fill=BOTH, expand=True)
        self._lib_cv = Canvas(wrap, bg=BG2, bd=0, highlightthickness=0)
        sb = ttk.Scrollbar(wrap, orient=VERTICAL, command=self._lib_cv.yview)
        self._lib_fr = Frame(self._lib_cv, bg=BG2)
        self._lib_cv.configure(yscrollcommand=sb.set)
        sb.pack(side=RIGHT, fill=Y)
        self._lib_cv.pack(side=LEFT, fill=BOTH, expand=True)
        win = self._lib_cv.create_window((0,0), window=self._lib_fr, anchor=NW)
        self._lib_win = win
        self._lib_fr.bind("<Configure>", self._sync_lib_scrollregion)
        self._lib_cv.bind("<Configure>",
                          lambda e: self._lib_cv.itemconfig(self._lib_win, width=e.width))
        self._scroll_canvases.add(self._lib_cv)

    def _sync_lib_scrollregion(self, _event=None):
        try:
            self._lib_cv.configure(scrollregion=self._lib_cv.bbox("all"))
        except Exception:
            pass

    def _build_filter_chips(self):
        for w in self._filter_frame.winfo_children(): w.destroy()
        current = self._filter_var.get()
        for em in ["All"] + EMOTIONS + list(self.custom_tags.keys()):
            is_sel = (em == current)
            if em == "All":
                col, bg_c, lbl = TXT_MID, BG4, "  All  "
            elif em in EM_COL:
                col, bg_c = EM_COL[em], EM_BG[em]
                lbl = f"  {EM_ICON[em]} {em}  "
            else:
                ct  = self.custom_tags.get(em, {})
                col = ct.get("color","#aaa"); bg_c = ct.get("bg","#1a1a1a")
                lbl = f"  {ct.get('icon','♪')} {em}  "
            Button(self._filter_frame, text=lbl, font=(FF,9,"bold"),
                   bg=col if is_sel else bg_c,
                   fg=WHITE if is_sel else col,
                   relief=FLAT, cursor="hand2", padx=4, pady=4, bd=0,
                   activebackground=col, activeforeground=WHITE,
                   command=lambda e=em: self._set_filter(e)).pack(side=LEFT, padx=(0,5))

    def _debounce_search(self):
        if self._search_after_id:
            try: self.root.after_cancel(self._search_after_id)
            except Exception: pass
        self._search_after_id = self.root.after(250, self._render_library)

    def _set_filter(self, em):
        self._filter_var.set(em); self._build_filter_chips(); self._render_library()

    def _render_library(self):
        self._last_render = time.time(); self._dirty = False
        filt   = self._filter_var.get()
        search = self._search_var.get().lower().strip() if hasattr(self,"_search_var") else ""
        srt    = self._sort_var.get() if hasattr(self,"_sort_var") else "Name"

        songs = self.songs[:]
        if search:
            songs = [s for s in songs
                     if search in s.name.lower() or search in (s.artist or "").lower()
                     or search in (s.title or "").lower()]
        if filt != "All":
            songs = [s for s in songs if s.emotion == filt]
        if srt == "Name":      songs.sort(key=lambda s: s.name.lower())
        elif srt == "Emotion": songs.sort(key=lambda s: s.emotion)
        elif srt == "BPM":     songs.sort(key=lambda s: s.features.get("tempo_bpm",0), reverse=True)
        elif srt == "Duration":songs.sort(key=lambda s: s.duration, reverse=True)
        elif srt == "Source":  songs.sort(key=lambda s: s.tag_source)
        self._lib_visible = songs
        self._lib_rows = {}

        new_fr = Frame(self._lib_cv, bg=BG2)
        # Keep the scroll region in sync whenever this frame's height changes
        # (after layout settles, and when in-place row updates change a row's
        # height). Without this the canvas thinks it has nothing to scroll.
        new_fr.bind("<Configure>", self._sync_lib_scrollregion)
        if not songs:
            Label(new_fr,
                  text="No songs match." if self.songs else
                       "No songs yet.\nClick  + Add Songs  above.",
                  font=(FF,11), bg=BG2, fg=TXT_DIM,
                  justify=CENTER, pady=40).pack()
        else:
            for i, song in enumerate(songs):
                self._song_row_lib(i, song, new_fr)

        old_fr = self._lib_fr
        self._lib_fr = new_fr
        self._lib_cv.itemconfig(self._lib_win, window=new_fr)   # show new frame
        self._lib_cv.update_idletasks()                         # force layout now
        self._lib_cv.configure(scrollregion=self._lib_cv.bbox("all"))
        # Defer destroy to the NEXT event-loop tick so tkinter paints new_fr
        # (styled, dark) before the old one is torn down — eliminates FOUC.
        self.root.after(0, old_fr.destroy)

        total = len(self.songs); busy = sum(1 for s in self.songs if s.busy)
        done  = total - busy
        if total == 0:  self._lib_info.set("No songs yet")
        elif busy:      self._lib_info.set(f"{done}/{total} analysed  "
                                           f"({int(done/total*100) if total else 0}%)")
        else:           self._lib_info.set(f"{total} songs  ·  showing {len(songs)}")

    def _song_row_lib(self, idx, song, parent=None):
        if parent is None: parent = self._lib_fr
        row_bg = BG4 if idx%2==0 else BG3
        row = Frame(parent, bg=row_bg, pady=9); row.pack(fill=X)
        self._lib_rows[id(song)] = row
        self._populate_lib_row(row, idx, song, row_bg)
        return row

    def _update_song_row(self, song):
        """Refresh just one row's contents in place — no full-list rebuild, no
        scroll jump, no flicker.  Used when a song finishes analysis or its
        metadata/emotion changes in the background."""
        if self._active_tab != "Library": return
        row = self._lib_rows.get(id(song))
        if row is None: return
        try:
            if not row.winfo_exists():
                self._lib_rows.pop(id(song), None); return
        except Exception:
            return
        # Recover this row's index in the currently-visible list
        try:    idx = self._lib_visible.index(song)
        except ValueError:
            return
        row_bg = BG4 if idx%2==0 else BG3
        for w in row.winfo_children(): w.destroy()
        self._populate_lib_row(row, idx, song, row_bg)

    def _populate_lib_row(self, row, idx, song, row_bg):
        play_btn = Button(row, text="▶", font=(FF,11), bg=row_bg, fg=ACCENT,
                          relief=FLAT, cursor="hand2", padx=6, pady=2,
                          activebackground=BG3, activeforeground=ACCENT)
        play_btn.config(command=lambda i=idx: self._play_from_library(i))
        play_btn.pack(side=LEFT, padx=(8,4))
        # Right-click context menu
        row.bind("<Button-3>", lambda e, s=song, i=idx: self._lib_context_menu(e, s, i))
        play_btn.bind("<Button-3>", lambda e, s=song, i=idx: self._lib_context_menu(e, s, i))

        Label(row, text=f"{idx+1:03d}", font=(FF,9), bg=row_bg,
              fg=TXT_DIM, width=4, anchor=E).pack(side=LEFT, padx=(0,8))

        if not song.busy:
            chip = self._make_chip(row, song, row_bg, click=True)
            chip.pack(side=RIGHT, padx=(4,10))

            src_lbl = SRC_LABEL.get(song.tag_source, "AI")
            Label(row, text=src_lbl, font=(FF,8), bg=row_bg,
                  fg=TXT_DIM).pack(side=RIGHT, padx=(0,2))

            if not song.user_override:
                Button(row, text="✗", font=(FF,9,"bold"), bg=row_bg, fg="#EF5350",
                       relief=FLAT, cursor="hand2", padx=5, pady=2,
                       activebackground="#200808", activeforeground="#EF5350",
                       command=lambda s=song: self._ai_wrong(s)).pack(side=RIGHT, padx=(0,1))
                Button(row, text="✓", font=(FF,9,"bold"), bg=row_bg, fg=ACCENT,
                       relief=FLAT, cursor="hand2", padx=5, pady=2,
                       activebackground=EM_BG["focused"], activeforeground=ACCENT,
                       command=lambda s=song: self._ai_correct(s)).pack(side=RIGHT, padx=(0,1))
        else:
            Label(row, text="• • •", font=(FF,10),
                  bg=row_bg, fg=TXT_DIM).pack(side=RIGHT, padx=14)

        Button(row, text="🗑", font=(FF,9), bg=row_bg, fg=TXT_DIM,
               relief=FLAT, cursor="hand2", padx=5, pady=2,
               activebackground="#200808", activeforeground="#EF5350",
               command=lambda s=song: self._delete_song(s)).pack(side=RIGHT, padx=(0,4))

        meta = ""
        if song.features.get("tempo_bpm"): meta += f"{song.features['tempo_bpm']:.0f} bpm  "
        if song.duration > 0: meta += _fmt_dur(song.duration)
        if meta: Label(row, text=meta.strip(), font=(FF,9),
                       bg=row_bg, fg=TXT_DIM).pack(side=RIGHT, padx=(4,6))

        # Lyrics indicator
        if song.path in self.lyrics_db and self.lyrics_db[song.path].get("lyrics"):
            Label(row, text="♪", font=(FF,9), bg=row_bg,
                  fg=ACCENT).pack(side=RIGHT, padx=(0,2))

        info = Frame(row, bg=row_bg); info.pack(side=LEFT, fill=X, expand=True)
        display = song.title if song.title != song.name else song.name
        name_str = (display[:42]+"…") if len(display)>42 else display
        Label(info, text=name_str, font=(FF,11), bg=row_bg, fg=TXT,
              anchor=W).pack(fill=X)
        if song.artist:
            Label(info, text=song.artist, font=(FF,8), bg=row_bg,
                  fg=TXT_DIM, anchor=W).pack(fill=X)

    # ── Chip ──────────────────────────────────────────────────────────────────
    def _make_chip(self, parent, song, row_bg, click):
        em   = song.emotion if (song.emotion in EM_COL or song.emotion in self.custom_tags) else "calm"
        col  = EM_COL.get(em)  or self.custom_tags.get(em,{}).get("color","#aaa")
        bg_c = EM_BG.get(em)   or self.custom_tags.get(em,{}).get("bg","#1a1a1a")
        icon = EM_ICON.get(em) or self.custom_tags.get(em,{}).get("icon","♪")
        lbl  = f"  {icon} {em}  "
        if click:
            w = Button(parent, text=lbl, font=(FF,9,"bold"), bg=bg_c, fg=col,
                       relief=FLAT, cursor="hand2", activebackground=bg_c,
                       activeforeground=col, bd=0, pady=4)
            w.config(command=lambda s=song, b=w: self._emotion_menu(s, b))
        else:
            w = Label(parent, text=lbl, font=(FF,9,"bold"), bg=bg_c, fg=col,
                      padx=6, pady=4)
        return w

    def _emotion_menu(self, song, btn):
        menu = Menu(self.root, tearoff=0, bg=BG3, fg=TXT,
                    activebackground=ACCENT, activeforeground=WHITE,
                    font=(FF,10), relief=FLAT, bd=0)
        for em in EMOTIONS + list(self.custom_tags.keys()):
            icon = EM_ICON.get(em) or self.custom_tags.get(em,{}).get("icon","♪")
            menu.add_command(label=f"  {icon} {em}",
                             command=lambda e=em: self._set_song_emotion(song, btn, e))
        menu.add_separator()
        menu.add_command(label="  ＋  Create new tag…", command=self._new_tag_dialog)
        try:   menu.tk_popup(btn.winfo_rootx(), btn.winfo_rooty()+btn.winfo_height())
        finally: menu.grab_release()

    def _set_song_emotion(self, song, btn, em):
        old_em = song.emotion
        if old_em != em and song.features and not song.busy:
            self.corrections = record_correction(
                self.corrections, song.features, old_em, em)
        song.emotion       = em
        song.user_override = True
        song.tag_source    = "user"
        col  = EM_COL.get(em)  or self.custom_tags.get(em,{}).get("color","#aaa")
        bg_c = EM_BG.get(em)   or self.custom_tags.get(em,{}).get("bg","#1a1a1a")
        icon = EM_ICON.get(em) or self.custom_tags.get(em,{}).get("icon","♪")
        btn.config(text=f"  {icon} {em}  ", bg=bg_c, fg=col,
                   activebackground=bg_c, activeforeground=col)
        save_library(self.songs, self.custom_tags)
        self._status.set(f"✏ Saved: '{song.name}' → {em}  ·  correction recorded")

    def _ai_correct(self, song):
        if not song.features or song.busy: return
        em   = song.emotion
        auto = song.original_auto or em
        self.corrections = record_correction(self.corrections, song.features, auto, em)
        song.user_override = True
        song.tag_source    = "confirmed"
        save_library(self.songs, self.custom_tags)
        self._update_song_row(song)
        n = len(self.corrections)
        self._status.set(f"✓ Confirmed '{song.name}' is {em}  ·  "
                         f"model now has {n} correction{'s' if n!=1 else ''}")

    def _ai_wrong(self, song):
        menu = Menu(self.root, tearoff=0, bg=BG3, fg=TXT,
                    activebackground="#EF5350", activeforeground=WHITE,
                    font=(FF,10), relief=FLAT, bd=0)
        menu.add_command(label="  What should it be?", state=DISABLED)
        menu.add_separator()
        for em in EMOTIONS + list(self.custom_tags.keys()):
            icon = EM_ICON.get(em) or self.custom_tags.get(em,{}).get("icon","♪")
            menu.add_command(label=f"  {icon} {em}",
                             command=lambda e=em, s=song: self._ai_wrong_pick(s, e))
        try:   menu.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())
        finally: menu.grab_release()

    def _ai_wrong_pick(self, song, correct_em):
        auto = song.original_auto or song.emotion
        if song.features and not song.busy:
            self.corrections = record_correction(
                self.corrections, song.features, auto, correct_em)
        song.emotion       = correct_em
        song.user_override = True
        song.tag_source    = "user"
        save_library(self.songs, self.custom_tags)
        self._update_song_row(song)
        n = len(self.corrections)
        self._status.set(f"✗ Corrected '{song.name}': {auto} → {correct_em}  ·  "
                         f"model now has {n} correction{'s' if n!=1 else ''}")

    def _delete_song(self, song):
        name   = song.name
        choice = messagebox.askyesnocancel(
            "Remove Song",
            f"Remove  \"{name}\"  from library?\n\n"
            f"  Yes  = remove from library, keep file on disk\n"
            f"  No   = remove from library AND delete the file\n"
            f"  Cancel = do nothing")
        if choice is None: return
        self.songs = [s for s in self.songs if s is not song]
        if self._queue and 0 <= self._q_idx < len(self._queue):
            if self._queue[self._q_idx] is song:
                self._queue = [s for s in self._queue if s is not song]
                if self._queue:
                    self._q_idx = min(self._q_idx, len(self._queue)-1)
                    self._play_current()
                else:
                    if _PYGAME: pygame.mixer.music.stop()
                    self._playing = False
                    self._play_btn.config(text="▶")
                    self._now_name.set("Nothing playing"); self._now_em.set("")
        else:
            self._queue = [s for s in self._queue if s is not song]
        if choice is False:
            try:
                os.remove(song.path)
                self._status.set(f"🗑 Deleted '{name}' from library and disk")
            except Exception as ex:
                self._status.set(f"Removed from library; couldn't delete file: {ex}")
        else:
            self._status.set(f"🗑 Removed '{name}' from library  (file kept on disk)")
        save_library(self.songs, self.custom_tags)
        self._render_library()

    # ── Custom tags ────────────────────────────────────────────────────────────
    def _new_tag_dialog(self):
        name = simpledialog.askstring("New Tag","Tag name (e.g. workout, rainy day):",
                                      parent=self.root)
        if not name or not name.strip(): return
        name = name.strip().lower()
        if name in EM_COL or name in self.custom_tags:
            messagebox.showinfo("Solace", f"Tag '{name}' already exists."); return
        result = colorchooser.askcolor(title=f"Colour for '{name}'",
                                       initialcolor="#9B59B6", parent=self.root)
        if not result or not result[1]: return
        hex_col = result[1]
        r,g,b   = int(hex_col[1:3],16),int(hex_col[3:5],16),int(hex_col[5:7],16)
        bg_hex  = (f"#{max(0,int(r*.15)):02x}"
                   f"{max(0,int(g*.15)):02x}"
                   f"{max(0,int(b*.15)):02x}")
        icon = (simpledialog.askstring("Icon","Single emoji (blank = ♪):",
                                       parent=self.root) or "♪").strip() or "♪"
        self.custom_tags[name] = {"color":hex_col,"bg":bg_hex,"icon":icon}
        save_library(self.songs, self.custom_tags)
        self._build_filter_chips()
        messagebox.showinfo("Solace", f"Tag '{name}' created!  Click any song chip to assign it.")

    # ── Re-tag ────────────────────────────────────────────────────────────────
    def _retag_dialog(self):
        ready  = [s for s in self.songs if not s.busy and not s.user_override and s.features]
        locked = sum(1 for s in self.songs if s.user_override)
        n_corr = len(self.corrections)
        msg = (f"Re-tag {len(ready)} songs using:\n"
               f"  • {n_corr} learned correction{'s' if n_corr!=1 else ''}\n"
               f"  • Internet genre lookups\n"
               f"  • Lyrics sentiment\n"
               f"  • Improved acoustic classifier\n\n"
               f"  {locked} manually-tagged songs will NOT be touched.\n\nContinue?")
        if not messagebox.askyesno("Re-tag Library", msg): return
        threading.Thread(target=self._retag_worker, args=(ready,), daemon=True).start()
        self._status.set(f"Re-tagging {len(ready)} songs…")

    def _retag_worker(self, songs):
        for song in songs:
            if not song.features: continue
            inet_em = genre_to_emotion(song.internet_emotion or song.internet_genre)
            lyr_em  = _emotion_from_lyrics(
                self.lyrics_db.get(song.path, {}).get("lyrics", ""))
            new_em, src = knn_emotion(song.features, self.corrections,
                                       inet_em, lyr_em)
            song.emotion    = new_em
            song.tag_source = src
            self._uq.put(song)   # in-place row refresh, no full rebuild
        save_library(self.songs, self.custom_tags)
        self.root.after(0, lambda: self._status.set(
            f"Re-tagging complete  ·  {len(songs)} songs updated"))

    # ── Play from library ──────────────────────────────────────────────────────
    def _play_from_library(self, idx: int):
        if not _PYGAME:
            messagebox.showinfo("Solace",
                "Install pygame-ce for playback:\n\n    pip install pygame-ce"); return
        songs = self._lib_visible
        if not songs or idx < 0 or idx >= len(songs): return
        seed = songs[idx]
        # Keep whatever is visible from the clicked song onward (preserves the
        # order you're browsing), then auto-fill a "radio" continuation from the
        # rest of the library so playback never dead-ends — e.g. after a search
        # that only shows one result.
        head    = songs[idx:]
        exclude = {id(s) for s in head}
        tail    = self._autoplay_continuation(seed, exclude)
        self._next_up.clear()
        self._play_playlist(head + tail, 0)
        self._status.set(f"▶ Playing: {seed.title or seed.name}"
                         + (f"  ·  +{len(tail)} more queued from your library"
                            if tail else ""))

    def _autoplay_continuation(self, seed, exclude_ids):
        """Build an emotion-aware 'radio' queue from the library: songs sharing
        the seed's emotion first, then nearby emotions, then everything else."""
        pool = [s for s in self.songs
                if not s.busy and not s.failed and id(s) not in exclude_ids]
        if not pool: return []
        tiers: dict = {}
        for s in pool:
            tiers.setdefault(s.emotion, []).append(s)
        for em in tiers: random.shuffle(tiers[em])
        order, seen, result = ([seed.emotion]
                               + _NEARBY.get(seed.emotion, [])
                               + EMOTIONS), set(), []
        for em in order:
            for s in tiers.get(em, []):
                if id(s) not in seen:
                    seen.add(id(s)); result.append(s)
        # Sweep up anything left (custom tags / "unknown" / failed-to-rank)
        for s in pool:
            if id(s) not in seen:
                seen.add(id(s)); result.append(s)
        return result

    def _lib_context_menu(self, event, song, idx):
        menu = Menu(self.root, tearoff=0, bg=BG3, fg=TXT,
                    activebackground=ACCENT, activeforeground=WHITE,
                    font=(FF,10), relief=FLAT, bd=0)
        menu.add_command(label="  ▶  Play Now",
                         command=lambda: self._play_from_library(
                             self._lib_visible.index(song) if song in self._lib_visible else idx))
        menu.add_command(label="  ⏭  Play Next",
                         command=lambda: self._next_up.insert(0, song) or
                                         self._status.set(f"Playing next: {song.title or song.name}"))
        menu.add_command(label="  ＋  Add to Queue",
                         command=lambda: self._queue_song(song))
        # Add-to-playlist submenu
        sub = Menu(menu, tearoff=0, bg=BG3, fg=TXT,
                   activebackground=ACCENT, activeforeground=WHITE,
                   font=(FF,10), relief=FLAT, bd=0)
        for pl in self.playlists:
            sub.add_command(label=f"  {pl['name']}",
                            command=lambda p=pl, s=song: self._add_song_to_playlist(p, s))
        if self.playlists:
            sub.add_separator()
        sub.add_command(label="  ＋  New playlist…",
                        command=lambda s=song: self._new_playlist_with_song(s))
        menu.add_cascade(label="  ♫  Add to Playlist", menu=sub)
        menu.add_separator()
        menu.add_command(label="  🗑  Remove from Library",
                         command=lambda: self._delete_song(song))
        try:   menu.tk_popup(event.x_root, event.y_root)
        finally: menu.grab_release()

    # ──────────────────────────────────────────────────────────────────────────
    #  MIX TAB
    # ──────────────────────────────────────────────────────────────────────────
    def _build_mix_tab(self, parent):
        PAD = dict(padx=34)
        Label(parent, text="How are you feeling right now?",
              font=(FF,14,"bold"), bg=BG, fg=TXT).pack(anchor=W, pady=(24,3), **PAD)
        Label(parent, text="Type freely — Solace detects your emotional state.",
              font=(FF,10), bg=BG, fg=TXT_DIM).pack(anchor=W, pady=(0,10), **PAD)

        self._mood = Text(parent, height=3, font=(FF,12), bg=BG3, fg=TXT_DIM,
                          insertbackground=TXT, relief=FLAT, padx=14, pady=11,
                          wrap=WORD, highlightthickness=1,
                          highlightbackground=BORDER, highlightcolor=ACCENT)
        self._mood.pack(fill=X, **PAD)
        self._mood.insert("1.0","e.g. I'm exhausted and a bit stressed out…")
        self._mood.bind("<FocusIn>",  self._ph_clear)
        self._mood.bind("<FocusOut>", self._ph_restore)

        goal_row = Frame(parent, bg=BG); goal_row.pack(fill=X, pady=(18,0), **PAD)
        Label(goal_row, text="I want this music to", font=(FF,11),
              bg=BG, fg=TXT_MID).pack(side=LEFT)
        self._goal = StringVar(value=GOALS[0])
        opt = OptionMenu(goal_row, self._goal, *GOALS)
        opt.config(bg=BG3, fg=TXT, activebackground=BG4, activeforeground=TXT,
                   font=(FF,11), relief=FLAT, padx=12, pady=7, cursor="hand2",
                   highlightthickness=1, highlightbackground=BORDER)
        opt["menu"].config(bg=BG3, fg=TXT, font=(FF,11), relief=FLAT,
                           activebackground=ACCENT, activeforeground=WHITE)
        opt.pack(side=LEFT, padx=(12,0))

        btn_row = Frame(parent, bg=BG); btn_row.pack(anchor=W, pady=20, **PAD)
        Button(btn_row, text="  ▶  Generate  ", font=(FF,12,"bold"),
               bg=ACCENT, fg=WHITE, relief=FLAT, cursor="hand2", padx=20, pady=13,
               activebackground=ACCENT_DK, activeforeground=WHITE,
               command=self._generate).pack(side=LEFT)
        Button(btn_row, text="  🔀  Shuffle  ", font=(FF,12,"bold"),
               bg=BG4, fg=TXT_MID, relief=FLAT, cursor="hand2", padx=20, pady=13,
               activebackground=BG3, activeforeground=TXT,
               command=self._shuffle).pack(side=LEFT, padx=(10,0))
        self._save_pl_btn = Button(btn_row, text="  💾  Save  ", font=(FF,12,"bold"),
               bg=BG4, fg=TXT_DIM, relief=FLAT, cursor="hand2", padx=20, pady=13,
               activebackground=BG3, activeforeground=TXT,
               command=self._save_playlist_dialog, state=DISABLED)
        self._save_pl_btn.pack(side=LEFT, padx=(10,0))
        self._fb_btn = Button(btn_row, text="  💬  Feedback  ", font=(FF,12,"bold"),
               bg=BG4, fg=TXT_DIM, relief=FLAT, cursor="hand2", padx=20, pady=13,
               activebackground=BG3, activeforeground=TXT,
               command=self._open_feedback, state=DISABLED)
        self._fb_btn.pack(side=LEFT, padx=(10,0))

        Frame(parent, bg=BORDER, height=1).pack(fill=X, **PAD)

        pl_hdr = Frame(parent, bg=BG); pl_hdr.pack(fill=X, pady=(14,6), **PAD)
        Label(pl_hdr, text="Playlist", font=(FF,14,"bold"), bg=BG, fg=TXT).pack(side=LEFT)
        self._pl_meta = Label(pl_hdr, text="", font=(FF,10), bg=BG, fg=TXT_MID)
        self._pl_meta.pack(side=LEFT, padx=14)

        pl_wrap = Frame(parent, bg=BG)
        pl_wrap.pack(fill=BOTH, expand=True, pady=(0,16), **PAD)
        self._pl_cv = Canvas(pl_wrap, bg=BG, bd=0, highlightthickness=0)
        pl_sb = ttk.Scrollbar(pl_wrap, orient=VERTICAL, command=self._pl_cv.yview)
        self._pl_fr = Frame(self._pl_cv, bg=BG)
        self._pl_cv.configure(yscrollcommand=pl_sb.set)
        pl_sb.pack(side=RIGHT, fill=Y); self._pl_cv.pack(side=LEFT, fill=BOTH, expand=True)
        win = self._pl_cv.create_window((0,0), window=self._pl_fr, anchor=NW)
        self._pl_fr.bind("<Configure>",
                         lambda e: self._pl_cv.configure(
                             scrollregion=self._pl_cv.bbox("all")))
        self._pl_cv.bind("<Configure>",
                         lambda e: self._pl_cv.itemconfig(win, width=e.width))
        self._scroll_canvases.add(self._pl_cv)
        Label(self._pl_fr, text="Describe your mood, choose a goal, then hit Generate.",
              font=(FF,11), bg=BG, fg=TXT_DIM, justify=CENTER, pady=36).pack()
        self._current_playlist: list = []

    def _generate(self):
        ready = [s for s in self.songs if not s.busy]
        if not self.songs:   messagebox.showinfo("Solace","Add songs first."); return
        if not ready:        messagebox.showinfo("Solace","Still analysing — try again."); return
        raw     = self._mood.get("1.0", END).strip()
        text    = "" if self._ph else raw
        user_em = detect_emotion(text) if text else "melancholic"
        goal    = self._goal.get()
        bias      = self.feedback.get("biases", {}).get(goal, {})
        mood_bias = self.feedback.get("mood_biases", {}).get(user_em, {})
        affinity  = self.feedback.get("affinity", {}).get(goal, {})
        playlist = build_playlist(ready, user_em, goal, self.custom_tags,
                                  n=50, bias=bias, mood_bias=mood_bias,
                                  affinity=affinity)
        self._current_playlist = playlist
        self._render_playlist(playlist, user_em, goal)
        self._save_pl_btn.config(state=NORMAL, bg=ACCENT, fg=WHITE,
                                  activebackground=ACCENT_DK)
        self._fb_btn.config(state=NORMAL, fg=TXT_MID)
        notes = []
        if bias:      notes.append(self._bias_summary(bias))
        if mood_bias: notes.append(f"when {user_em}: more "
                                   + ", ".join(t for t,v in mood_bias.items() if v>0))
        if notes:
            self._status.set(f"Generated for '{goal}' · " + " · ".join(notes))

    def _shuffle(self):
        if not self._current_playlist:
            messagebox.showinfo("Solace","Generate a playlist first."); return
        random.shuffle(self._current_playlist)
        raw     = self._mood.get("1.0", END).strip()
        text    = "" if self._ph else raw
        user_em = detect_emotion(text) if text else "melancholic"
        self._render_playlist(self._current_playlist, user_em, self._goal.get())
        self._status.set("Playlist shuffled  🔀")

    def _render_playlist(self, playlist, user_em, goal):
        for w in self._pl_fr.winfo_children(): w.destroy()
        icon = EM_ICON.get(user_em,"◎")
        self._pl_meta.config(
            text=f"detected: {icon} {user_em}   ·   goal: {goal}   ·   {len(playlist)} songs")
        for i, song in enumerate(playlist):
            row = Frame(self._pl_fr, bg=BG, pady=13); row.pack(fill=X)
            pb = Button(row, text="▶", font=(FF,12), bg=BG4, fg=ACCENT,
                        relief=FLAT, cursor="hand2", padx=8, pady=4,
                        activebackground=BG3, activeforeground=ACCENT)
            pb.config(command=lambda pl=playlist, idx=i: self._play_playlist(pl, idx))
            pb.pack(side=LEFT, padx=(0,6))
            Label(row, text=str(i+1), font=(FF,15,"bold"), bg=BG, fg=ACCENT,
                  width=3, anchor=E).pack(side=LEFT, padx=(0,4))
            self._make_chip(row, song, BG, click=False).pack(side=RIGHT, padx=(8,4))
            info = Frame(row, bg=BG); info.pack(side=LEFT, fill=X, expand=True, padx=(12,8))
            display = song.title if song.title != song.name else song.name
            name_str = (display[:54]+"…") if len(display)>54 else display
            Label(info, text=name_str, font=(FF,12,"bold"),
                  bg=BG, fg=TXT, anchor=W).pack(fill=X)
            sub = f"{song.emotion}   ·   {_fmt_dur(song.duration)}"
            if song.tag_source != "auto":
                sub += f"   ·   {SRC_LABEL.get(song.tag_source,'')}"
            Label(info, text=sub, font=(FF,9), bg=BG, fg=TXT_DIM, anchor=W).pack(fill=X)
            Frame(self._pl_fr, bg=BORDER, height=1).pack(fill=X)
        self._pl_cv.update_idletasks()
        self._pl_cv.configure(scrollregion=self._pl_cv.bbox("all"))

    def _save_playlist_dialog(self):
        if not self._current_playlist:
            messagebox.showinfo("Solace","Generate a playlist first."); return
        name = simpledialog.askstring("Save Playlist","Name this playlist:",
                                       parent=self.root,
                                       initialvalue=f"My {self._goal.get().title()} Mix")
        if not name or not name.strip(): return
        pl = {"id":str(uuid.uuid4()), "name":name.strip(),
              "created":datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
              "goal":self._goal.get(),
              "songs":[{"path":s.path,"name":s.name,"emotion":s.emotion,
                        "duration":s.duration} for s in self._current_playlist]}
        self.playlists.append(pl); save_playlists(self.playlists)
        messagebox.showinfo("Solace", f"'{name}' saved!  Find it in the Playlists tab.")

    # ── Playlist feedback / learning ───────────────────────────────────────────
    def _bias_summary(self, bias: dict) -> str:
        more = [em for em, w in bias.items() if w >= 1]
        less = [em for em, w in bias.items() if w <= -1]
        parts = []
        if more: parts.append("more " + ", ".join(more))
        if less: parts.append("less " + ", ".join(less))
        return " · ".join(parts) if parts else "no strong preference yet"

    def _open_feedback(self):
        goal = self._goal.get()
        win  = Toplevel(self.root)
        win.title("Playlist Feedback");  win.configure(bg=BG)
        win.geometry("600x760");  win.transient(self.root); win.grab_set()

        Label(win, text="How was this playlist?", font=(FF,16,"bold"),
              bg=BG, fg=TXT).pack(anchor=W, padx=24, pady=(20,2))
        Label(win, text=f"Teaching Solace what “{goal}” should sound like for you.",
              font=(FF,10), bg=BG, fg=TXT_MID).pack(anchor=W, padx=24)

        cur = self.feedback.get("biases", {}).get(goal, {})
        Label(win, text="Currently learned:  " + self._bias_summary(cur),
              font=(FF,9), bg=BG, fg=ACCENT if cur else TXT_DIM,
              wraplength=520, justify=LEFT).pack(anchor=W, padx=24, pady=(8,0))

        Label(win, text="Tell me what to change, in your own words:",
              font=(FF,10,"bold"), bg=BG, fg=TXT).pack(anchor=W, padx=24, pady=(16,4))
        txt = Text(win, height=4, font=(FF,11), bg=BG3, fg=TXT,
                   insertbackground=TXT, relief=FLAT, padx=12, pady=10, wrap=WORD,
                   highlightthickness=1, highlightbackground=BORDER,
                   highlightcolor=ACCENT)
        txt.pack(fill=X, padx=24)
        txt.insert("1.0", "e.g. I wanted to feel better but it was all sad songs — "
                          "give me more happy and exciting tracks.")
        _ph = {"on": True}
        def _clr(_):
            if _ph["on"]: txt.delete("1.0", END); txt.config(fg=TXT); _ph["on"]=False
        txt.bind("<FocusIn>", _clr)

        # 3-state mood chips (neutral → more → less), auto-seeded from the comment
        chip_state = {em: 0 for em in EMOTIONS}
        chip_btns  = {}
        Label(win, text="…or tap moods:  (once = more,  twice = less)",
              font=(FF,9), bg=BG, fg=TXT_DIM).pack(anchor=W, padx=24, pady=(14,4))
        chip_wrap = Frame(win, bg=BG); chip_wrap.pack(anchor=W, padx=20)

        def _paint(em):
            st = chip_state[em]; b = chip_btns[em]
            if st > 0:   b.config(bg=EM_COL[em], fg=WHITE, text=f"  ＋ {em}  ")
            elif st < 0: b.config(bg="#3a1010", fg="#EF5350", text=f"  − {em}  ")
            else:        b.config(bg=EM_BG[em], fg=EM_COL[em], text=f"  {EM_ICON[em]} {em}  ")
        def _cycle(em):
            chip_state[em] = {0:1, 1:-1, -1:0}[chip_state[em]]; _paint(em)
        for em in EMOTIONS:
            b = Button(chip_wrap, font=(FF,9,"bold"), relief=FLAT, cursor="hand2",
                       bd=0, padx=4, pady=5, command=lambda e=em: _cycle(e))
            b.pack(side=LEFT, padx=3, pady=3); chip_btns[em] = b; _paint(em)

        # ── Mood → tags rule: "when I'm feeling X, recommend more [tags]" ───────
        Frame(win, bg=BORDER, height=1).pack(fill=X, padx=24, pady=(16,0))
        raw_mood  = self._mood.get("1.0", END).strip()
        detected  = detect_emotion(raw_mood) if (raw_mood and not self._ph) else "sad"
        feel_row  = Frame(win, bg=BG); feel_row.pack(anchor=W, padx=24, pady=(12,4))
        Label(feel_row, text="When I'm feeling", font=(FF,10,"bold"),
              bg=BG, fg=TXT).pack(side=LEFT)
        feel_var  = StringVar(value=detected)
        fopt = OptionMenu(feel_row, feel_var, *EMOTIONS)
        fopt.config(bg=BG3, fg=TXT, relief=FLAT, font=(FF,10), padx=8, pady=2,
                    activebackground=BG4, highlightthickness=1,
                    highlightbackground=BORDER, cursor="hand2")
        fopt["menu"].config(bg=BG3, fg=TXT, font=(FF,10),
                            activebackground=ACCENT, activeforeground=WHITE)
        fopt.pack(side=LEFT, padx=(8,0))
        Label(feel_row, text=", recommend more songs tagged:", font=(FF,10),
              bg=BG, fg=TXT_MID).pack(side=LEFT, padx=(8,0))

        mtag_state = {t: 0 for t in EMOTIONS + list(self.custom_tags.keys())}
        mtag_btns  = {}
        mtag_wrap  = Frame(win, bg=BG); mtag_wrap.pack(anchor=W, padx=20, pady=(2,0))
        def _mpaint(t):
            on = mtag_state[t] > 0; b = mtag_btns[t]
            col  = EM_COL.get(t) or self.custom_tags.get(t,{}).get("color","#aaa")
            bgc  = EM_BG.get(t)  or self.custom_tags.get(t,{}).get("bg","#1a1a1a")
            icon = EM_ICON.get(t) or self.custom_tags.get(t,{}).get("icon","♪")
            if on: b.config(bg=col, fg=WHITE, text=f"  ✓ {t}  ")
            else:  b.config(bg=bgc, fg=col,  text=f"  {icon} {t}  ")
        def _mtoggle(t):
            mtag_state[t] = 0 if mtag_state[t] else 1; _mpaint(t)
        for t in EMOTIONS + list(self.custom_tags.keys()):
            b = Button(mtag_wrap, font=(FF,9,"bold"), relief=FLAT, cursor="hand2",
                       bd=0, padx=4, pady=5, command=lambda x=t: _mtoggle(x))
            b.pack(side=LEFT, padx=3, pady=3); mtag_btns[t] = b; _mpaint(t)
        # Pre-seed with what's already learned for the detected feeling
        def _seed_mtags(*_):
            existing = self.feedback.get("mood_biases", {}).get(feel_var.get(), {})
            for t in mtag_state:
                mtag_state[t] = 1 if existing.get(t, 0) > 0 else 0; _mpaint(t)
        feel_var.trace_add("write", _seed_mtags); _seed_mtags()

        def _read_comment():
            comment = "" if _ph["on"] else txt.get("1.0", END).strip()
            parsed  = parse_feedback(comment)
            for em in EMOTIONS:
                chip_state[em] = max(-1, min(1, parsed.get(em, 0)))
                _paint(em)
            if not parsed:
                self._status.set("Couldn't read a clear preference — tap the mood chips.")

        info = Label(win, text="", font=(FF,9), bg=BG, fg=TXT_DIM,
                     wraplength=520, justify=LEFT)
        info.pack(anchor=W, padx=24, pady=(14,0))

        btns = Frame(win, bg=BG); btns.pack(fill=X, padx=24, pady=18, side=BOTTOM)
        Button(btns, text="Read my comment ↧", font=(FF,10), bg=BG4, fg=TXT_MID,
               relief=FLAT, cursor="hand2", padx=14, pady=9,
               activebackground=BG3, activeforeground=TXT,
               command=_read_comment).pack(side=LEFT)
        Button(btns, text="Cancel", font=(FF,10), bg=BG4, fg=TXT_MID,
               relief=FLAT, cursor="hand2", padx=14, pady=9,
               activebackground=BG3, activeforeground=TXT,
               command=win.destroy).pack(side=RIGHT)

        def _submit():
            comment = "" if _ph["on"] else txt.get("1.0", END).strip()
            deltas  = dict(parse_feedback(comment))
            for em, st in chip_state.items():        # manual chips override the parse
                if st != 0: deltas[em] = st
            feeling     = feel_var.get()
            mood_deltas = {t: 1 for t, st in mtag_state.items() if st > 0}
            if not deltas and not mood_deltas:
                info.config(text="Tell me what to change — type a comment, tap a mood, "
                                 "or set a 'when I'm feeling…' rule.", fg="#EF5350")
                return
            self._apply_feedback(goal, comment, deltas, feeling, mood_deltas)
            win.destroy()
        Button(btns, text="✓  Submit & Relearn", font=(FF,10,"bold"),
               bg=ACCENT, fg=WHITE, relief=FLAT, cursor="hand2", padx=16, pady=9,
               activebackground=ACCENT_DK, activeforeground=WHITE,
               command=_submit).pack(side=RIGHT, padx=(0,8))

    def _apply_feedback(self, goal, comment, deltas, feeling=None, mood_deltas=None):
        # Per-goal preference (e.g. "for 'lift my mood', less sad")
        g = self.feedback.setdefault("biases", {}).setdefault(goal, {})
        for em, d in deltas.items():
            g[em] = max(BIAS_MIN, min(BIAS_MAX, g.get(em, 0) + d))
            if g[em] == 0: g.pop(em, None)
        # Per-mood rule (e.g. "when I'm feeling sad, recommend more happy, energetic")
        mood_deltas = mood_deltas or {}
        if feeling and mood_deltas:
            m = self.feedback.setdefault("mood_biases", {}).setdefault(feeling, {})
            for t, d in mood_deltas.items():
                m[t] = max(BIAS_MIN, min(BIAS_MAX, m.get(t, 0) + d))
                if m[t] == 0: m.pop(t, None)
        self.feedback.setdefault("log", []).append({
            "ts": datetime.datetime.now().isoformat(), "goal": goal,
            "comment": comment, "deltas": deltas,
            "feeling": feeling, "mood_deltas": mood_deltas})
        save_feedback(self.feedback)
        more = [em for em, d in deltas.items() if d > 0]
        less = [em for em, d in deltas.items() if d < 0]
        summary = []
        if more: summary.append("more " + ", ".join(more))
        if less: summary.append("less " + ", ".join(less))
        if mood_deltas: summary.append(f"when {feeling}: more " + ", ".join(mood_deltas))
        self._status.set(f"💬 Learned for '{goal}': {' · '.join(summary) or 'noted'} "
                         f"— regenerating…")
        # Regenerate immediately so the change is visible right away
        self._switch_tab("Mix")
        self._generate()

    # ──────────────────────────────────────────────────────────────────────────
    #  PLAYLISTS TAB
    # ──────────────────────────────────────────────────────────────────────────
    def _build_playlists_tab(self, parent):
        hdr = Frame(parent, bg=BG, pady=14); hdr.pack(fill=X, padx=28)
        Label(hdr, text="Saved Playlists",
              font=(FF,14,"bold"), bg=BG, fg=TXT).pack(side=LEFT)
        Button(hdr, text="＋  New Playlist", font=(FF,10,"bold"),
               bg=ACCENT, fg=WHITE, relief=FLAT, cursor="hand2", padx=14, pady=7,
               activebackground=ACCENT_DK, activeforeground=WHITE,
               command=self._new_playlist_dialog).pack(side=RIGHT)
        Frame(parent, bg=BORDER, height=1).pack(fill=X)
        wrap = Frame(parent, bg=BG); wrap.pack(fill=BOTH, expand=True)
        self._pls_cv = Canvas(wrap, bg=BG, bd=0, highlightthickness=0)
        sb = ttk.Scrollbar(wrap, orient=VERTICAL, command=self._pls_cv.yview)
        self._pls_fr = Frame(self._pls_cv, bg=BG)
        self._pls_cv.configure(yscrollcommand=sb.set)
        sb.pack(side=RIGHT, fill=Y); self._pls_cv.pack(side=LEFT, fill=BOTH, expand=True)
        win = self._pls_cv.create_window((0,0), window=self._pls_fr, anchor=NW)
        self._pls_fr.bind("<Configure>",
                          lambda e: self._pls_cv.configure(
                              scrollregion=self._pls_cv.bbox("all")))
        self._pls_cv.bind("<Configure>",
                          lambda e: self._pls_cv.itemconfig(win, width=e.width))
        self._scroll_canvases.add(self._pls_cv)

    def _render_playlists_tab(self):
        for w in self._pls_fr.winfo_children(): w.destroy()
        if not self.playlists:
            Label(self._pls_fr,
                  text="No saved playlists yet.\nGenerate one in the Mix tab and hit 💾 Save.",
                  font=(FF,11), bg=BG, fg=TXT_DIM,
                  justify=CENTER, pady=40).pack(); return
        for pl in reversed(self.playlists):
            card = Frame(self._pls_fr, bg=BG3, pady=16, padx=20)
            card.pack(fill=X, pady=(0,2))
            top = Frame(card, bg=BG3); top.pack(fill=X)
            bf  = Frame(top,  bg=BG3); bf.pack(side=RIGHT)
            Button(bf, text="▶  Play", font=(FF,10,"bold"),
                   bg=ACCENT, fg=WHITE, relief=FLAT, cursor="hand2",
                   padx=12, pady=6, activebackground=ACCENT_DK, activeforeground=WHITE,
                   command=lambda p=pl: self._play_saved_playlist(p)).pack(side=LEFT, padx=(0,8))
            Button(bf, text="🗑", font=(FF,10), bg=BG4, fg=TXT_DIM,
                   relief=FLAT, cursor="hand2", padx=10, pady=6,
                   activebackground="#200808", activeforeground="#EF5350",
                   command=lambda p=pl: self._delete_playlist(p)).pack(side=LEFT)
            Label(top, text=pl["name"], font=(FF,13,"bold"), bg=BG3, fg=TXT).pack(side=LEFT)
            Label(card, text=f"{len(pl['songs'])} songs  ·  {pl['goal']}  ·  {pl['created']}",
                  font=(FF,9), bg=BG3, fg=TXT_DIM).pack(anchor=W, pady=(4,10))
            if not pl["songs"]:
                Label(card, text="Empty playlist — right-click a song in the Library "
                                 "→  Add to Playlist",
                      font=(FF,9), bg=BG3, fg=TXT_DIM, anchor=W).pack(anchor=W)
            for i, s in enumerate(pl["songs"]):
                row = Frame(card, bg=BG3); row.pack(fill=X, pady=2)
                Label(row, text=str(i+1), font=(FF,9), bg=BG3,
                      fg=TXT_DIM, width=3, anchor=E).pack(side=LEFT)
                Button(row, text="✕", font=(FF,8), bg=BG3, fg="#EF5350",
                       relief=FLAT, cursor="hand2", padx=4, pady=0,
                       activebackground=BG3, activeforeground="#EF5350",
                       command=lambda p=pl, idx=i: self._remove_from_playlist(p, idx)
                       ).pack(side=RIGHT, padx=(4,0))
                em   = s.get("emotion","unknown")
                col  = EM_COL.get(em,"#aaa"); bg_c = EM_BG.get(em,"#1a1a1a")
                icon = EM_ICON.get(em,"♪")
                Label(row, text=f" {icon} {em} ", font=(FF,8,"bold"),
                      bg=bg_c, fg=col, padx=4, pady=2).pack(side=RIGHT, padx=(4,0))
                Label(row, text=(s["name"][:48]+"…") if len(s["name"])>48 else s["name"],
                      font=(FF,10), bg=BG3, fg=TXT_MID).pack(side=LEFT, padx=8)
            Frame(self._pls_fr, bg=BORDER, height=1).pack(fill=X)
        self._pls_cv.update_idletasks()
        self._pls_cv.configure(scrollregion=self._pls_cv.bbox("all"))

    def _play_saved_playlist(self, pl):
        songs = []
        for d in pl["songs"]:
            if os.path.exists(d.get("path","")):
                s = Song.__new__(Song)
                s.path=d["path"]; s.name=d["name"]
                s.title=d["name"]; s.artist=""
                s.emotion=d.get("emotion","unknown"); s.features={}
                s.duration=d.get("duration",0.0); s.busy=False; s.failed=False
                s.user_override=False; s.tag_source="auto"
                s.internet_genre=""; s.internet_emotion=""; s.original_auto=""
                s.cover_url=""
                songs.append(s)
        if not songs:
            messagebox.showwarning("Solace","No files found on disk."); return
        self._play_playlist(songs, 0)
        self._status.set(f"▶ Playing: {pl['name']}")

    def _delete_playlist(self, pl):
        if messagebox.askyesno("Delete", f"Delete \"{pl['name']}\"?"):
            self.playlists = [p for p in self.playlists if p["id"] != pl["id"]]
            save_playlists(self.playlists); self._render_playlists_tab()

    # ── User-created playlists ─────────────────────────────────────────────────
    def _new_playlist_dialog(self):
        name = simpledialog.askstring("New Playlist", "Name your playlist:",
                                      parent=self.root)
        if not name or not name.strip(): return None
        pl = {"id":str(uuid.uuid4()), "name":name.strip(),
              "created":datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
              "goal":"custom", "songs":[]}
        self.playlists.append(pl); save_playlists(self.playlists)
        self._render_playlists_tab()
        self._status.set(f"Created playlist '{name.strip()}'  ·  "
                         f"right-click songs in the Library → Add to Playlist")
        return pl

    def _song_entry(self, song):
        return {"path":song.path, "name":song.title or song.name,
                "emotion":song.emotion, "duration":song.duration}

    def _add_song_to_playlist(self, pl, song):
        pl["songs"].append(self._song_entry(song))
        save_playlists(self.playlists)
        self._status.set(f"Added '{song.title or song.name}' → '{pl['name']}'  "
                         f"({len(pl['songs'])} songs)")
        if self._active_tab == "Playlists": self._render_playlists_tab()

    def _new_playlist_with_song(self, song):
        pl = self._new_playlist_dialog()
        if pl: self._add_song_to_playlist(pl, song)

    def _remove_from_playlist(self, pl, idx):
        if 0 <= idx < len(pl["songs"]):
            pl["songs"].pop(idx)
            save_playlists(self.playlists); self._render_playlists_tab()

    # ──────────────────────────────────────────────────────────────────────────
    #  PLAYER BAR
    # ──────────────────────────────────────────────────────────────────────────
    def _build_player_bar(self):
        bar = Frame(self.root, bg=BG4); bar.pack(fill=X)

        ctrl = Frame(bar, bg=BG4); ctrl.pack(side=LEFT, padx=20, pady=10)
        self._shuf_btn = Button(ctrl, text="🔀", font=(FF,11), bg=BG4, fg=TXT_DIM,
                                relief=FLAT, cursor="hand2", padx=5,
                                activebackground=BG3, activeforeground=TXT,
                                command=self._toggle_shuffle)
        self._shuf_btn.pack(side=LEFT, padx=(0,4))
        self._prev_btn = Button(ctrl, text="⏮", font=(FF,14), bg=BG4, fg=TXT_MID,
                                relief=FLAT, cursor="hand2", padx=6,
                                activebackground=BG3, activeforeground=TXT,
                                command=self._prev_song)
        self._prev_btn.pack(side=LEFT)
        self._play_btn = Button(ctrl, text="▶", font=(FF,16,"bold"),
                                bg=BG4, fg=ACCENT, relief=FLAT, cursor="hand2",
                                padx=10, activebackground=BG3, activeforeground=ACCENT,
                                command=self._toggle_play)
        self._play_btn.pack(side=LEFT)
        self._next_btn = Button(ctrl, text="⏭", font=(FF,14), bg=BG4, fg=TXT_MID,
                                relief=FLAT, cursor="hand2", padx=6,
                                activebackground=BG3, activeforeground=TXT,
                                command=self._next_song)
        self._next_btn.pack(side=LEFT)
        self._repeat_btn = Button(ctrl, text="🔁", font=(FF,11), bg=BG4, fg=TXT_DIM,
                                  relief=FLAT, cursor="hand2", padx=5,
                                  activebackground=BG3, activeforeground=TXT,
                                  command=self._toggle_repeat)
        self._repeat_btn.pack(side=LEFT, padx=(4,0))

        centre = Frame(bar, bg=BG4); centre.pack(side=LEFT, fill=X, expand=True, padx=10)
        self._now_name = StringVar(value="Nothing playing")
        self._now_em   = StringVar(value="")
        top_row = Frame(centre, bg=BG4); top_row.pack(fill=X)
        Label(top_row, textvariable=self._now_em, font=(FF,9),
              bg=BG4, fg=TXT_DIM).pack(side=LEFT, padx=(0,8))
        now_lbl = Label(top_row, textvariable=self._now_name, font=(FF,10,"bold"),
                        bg=BG4, fg=TXT, cursor="hand2")
        now_lbl.pack(side=LEFT)
        now_lbl.bind("<Button-1>", lambda _: self._toggle_now_playing())

        prog_row = Frame(centre, bg=BG4); prog_row.pack(fill=X, pady=(4,0))
        self._time_cur = StringVar(value="0:00")
        self._time_tot = StringVar(value="0:00")
        Label(prog_row, textvariable=self._time_cur, font=(FF,8),
              bg=BG4, fg=TXT_DIM, width=5).pack(side=LEFT)
        self._prog_cv = Canvas(prog_row, height=12, bg=BORDER,
                               highlightthickness=0, cursor="hand2")
        self._prog_cv.pack(side=LEFT, fill=X, expand=True, padx=6)
        self._prog_fill = self._prog_cv.create_rectangle(0,0,0,12, fill=ACCENT, width=0)
        self._prog_dot  = self._prog_cv.create_oval(-6,-2,6,14, fill=WHITE,
                                                    outline="", state=HIDDEN)
        self._prog_cv.bind("<Button-1>",  self._seek)
        self._prog_cv.bind("<B1-Motion>", self._seek)
        self._prog_cv.bind("<Enter>",
                           lambda e: self._prog_cv.itemconfig(self._prog_dot, state=NORMAL))
        self._prog_cv.bind("<Leave>",
                           lambda e: self._prog_cv.itemconfig(self._prog_dot, state=HIDDEN))
        Label(prog_row, textvariable=self._time_tot, font=(FF,8),
              bg=BG4, fg=TXT_DIM, width=5).pack(side=LEFT)

        vol_f = Frame(bar, bg=BG4); vol_f.pack(side=RIGHT, padx=20, pady=10)
        Label(vol_f, text="🔊", font=(FF,11), bg=BG4, fg=TXT_DIM).pack(side=LEFT)
        self._vol_var = DoubleVar(value=0.8)
        Scale(vol_f, variable=self._vol_var, from_=0.0, to=1.0,
              resolution=0.05, orient=HORIZONTAL, length=90,
              bg=BG4, fg=TXT_DIM, troughcolor=BORDER,
              highlightthickness=0, showvalue=False, sliderlength=14,
              command=self._on_volume).pack(side=LEFT, padx=(4,0))
        if not _PYGAME:
            Label(bar, text="⚠ pip install pygame-ce", font=(FF,9),
                  bg=BG4, fg="#EF5350").pack(side=RIGHT, padx=16)

    def _toggle_now_playing(self):
        if self._overlay and self._overlay._visible:
            self._overlay.hide()
        elif self._overlay:
            self._overlay.show()

    # ── Playback ───────────────────────────────────────────────────────────────
    def _play_playlist(self, songs, start_idx=0):
        self._queue = songs; self._q_idx = start_idx; self._play_current()

    def _play_current(self):
        if not _PYGAME or not self._queue: return
        if self._q_idx < 0 or self._q_idx >= len(self._queue): return
        song = self._queue[self._q_idx]
        try:
            pygame.mixer.music.load(song.path)
            pygame.mixer.music.set_volume(self._vol_var.get())
            pygame.mixer.music.play()
            self._playing    = True
            self._play_start = time.time()
            self._paused_at  = 0.0
            self._seeking    = False
            # ── Get real duration (fast header read — no audio decode) ─────────
            dur = song.duration
            if dur <= 0:
                dur = get_duration_fast(song.path)
                if dur > 0:
                    song.duration = dur    # cache for next time
            self._song_dur = dur
            # ─────────────────────────────────────────────────────────────────
            em = EM_ICON.get(song.emotion,"◎") + f" {song.emotion}"
            self._now_name.set(song.title or song.name)
            self._now_em.set(em)
            self._time_tot.set(_fmt_dur(self._song_dur) if self._song_dur > 0 else "--:--")
            self._play_btn.config(text="⏸")
            if hasattr(self, "_repeat_btn"): self._update_repeat_btn()
            if hasattr(self, "_shuf_btn"):   self._update_shuf_btn()
            self._status.set(f"▶  Now playing: {song.title or song.name}")
        except Exception as ex:
            self._status.set(f"Playback error: {ex}")

    def _toggle_play(self):
        if not _PYGAME: return
        if self._playing:
            pygame.mixer.music.pause()
            self._paused_at = time.time() - self._play_start
            self._playing   = False; self._play_btn.config(text="▶")
            if self._overlay and self._overlay._visible:
                try: self._overlay._fs_play_btn.config(text="▶")
                except Exception: pass
        else:
            if self._queue and self._q_idx >= 0:
                pygame.mixer.music.unpause()
                self._play_start = time.time() - self._paused_at
                self._playing    = True; self._play_btn.config(text="⏸")
                if self._overlay and self._overlay._visible:
                    try: self._overlay._fs_play_btn.config(text="⏸")
                    except Exception: pass

    def _next_song(self, auto: bool = False):
        """Skip forward. auto=True means triggered by song ending (respects repeat)."""
        if not _PYGAME: return
        if auto and self._repeat == 1:
            # Repeat one — restart current track
            pygame.mixer.music.play()
            self._play_start = time.time(); self._paused_at = 0.0
            return
        # Drain manual queue first
        if self._next_up:
            nxt = self._next_up.pop(0)
            self._queue.insert(self._q_idx + 1, nxt)
            self._q_idx += 1
            self._play_current()
            self._refresh_queue_display()
            return
        if not self._queue: return
        last = len(self._queue) - 1
        if auto and self._repeat == 0 and self._q_idx >= last:
            # No repeat — stop at end of queue
            self._playing = False
            self._play_btn.config(text="▶")
            if self._overlay and self._overlay._visible:
                try: self._overlay._fs_play_btn.config(text="▶")
                except Exception: pass
            self._status.set("Queue finished")
            return
        self._q_idx = (self._q_idx + 1) % len(self._queue)
        self._play_current()

    def _prev_song(self):
        if not _PYGAME: return
        # If more than 3 s played, restart current song; otherwise go back
        if self._playing and (time.time() - self._play_start) > 3:
            self._do_seek(0.0)
            return
        if not self._queue: return
        self._q_idx = (self._q_idx - 1) % len(self._queue)
        self._play_current()

    def _queue_song(self, song):
        """Add a song to the manual 'play next' queue."""
        self._next_up.append(song)
        self._refresh_queue_display()
        self._status.set(f"Added to queue: {song.title or song.name}  "
                         f"({len(self._next_up)} in queue)")

    def _play_queued_song(self, song):
        """Insert a song right after the current track and play it immediately."""
        if not _PYGAME: return
        if self._queue and 0 <= self._q_idx < len(self._queue):
            self._queue.insert(self._q_idx + 1, song)
            self._q_idx += 1
        else:
            self._queue = [song]; self._q_idx = 0
        self._play_current()
        self._refresh_queue_display()

    def _refresh_queue_display(self):
        if self._overlay:
            try: self._overlay._render_queue()
            except Exception: pass

    def _toggle_repeat(self):
        self._repeat = (self._repeat + 1) % 3
        self._update_repeat_btn()

    def _update_repeat_btn(self):
        if not hasattr(self, "_repeat_btn"): return
        labels = {0: ("🔁", TXT_DIM), 1: ("🔂", ACCENT), 2: ("🔁", ACCENT)}
        txt, col = labels[self._repeat]
        self._repeat_btn.config(text=txt, fg=col)

    def _toggle_shuffle(self):
        self._shuf_on = not self._shuf_on
        if self._shuf_on and self._queue:
            cur = self._queue[self._q_idx] if 0 <= self._q_idx < len(self._queue) else None
            rest = [s for s in self._queue if s is not cur]
            random.shuffle(rest)
            self._queue = ([cur] + rest) if cur else rest
            self._q_idx = 0
        self._update_shuf_btn()

    def _update_shuf_btn(self):
        if not hasattr(self, "_shuf_btn"): return
        self._shuf_btn.config(fg=ACCENT if self._shuf_on else TXT_DIM)

    def _on_volume(self, val):
        if _PYGAME: pygame.mixer.music.set_volume(float(val))

    def _do_seek(self, pos_secs: float):
        """Seek to an absolute position in seconds."""
        if not _PYGAME: return
        dur = self._song_dur
        if dur <= 0 and self._queue and 0 <= self._q_idx < len(self._queue):
            dur = get_duration_fast(self._queue[self._q_idx].path)
            if dur > 0:
                self._queue[self._q_idx].duration = dur
                self._song_dur = dur
                self._time_tot.set(_fmt_dur(dur))
        if dur <= 0: return
        pos_secs = max(0.0, min(pos_secs, dur - 0.5))
        try:
            pygame.mixer.music.play(0, float(pos_secs))
            self._play_start = time.time() - pos_secs
            self._paused_at  = 0.0
            self._playing    = True
            self._play_btn.config(text="⏸")
            if self._overlay and self._overlay._visible:
                try: self._overlay._fs_play_btn.config(text="⏸")
                except Exception: pass
        except Exception as ex:
            self._status.set(f"Seek error: {ex}")

    def _seek(self, event):
        """Handle click/drag on the main progress bar."""
        if not _PYGAME: return
        dur = self._song_dur
        if dur <= 0 and self._queue and 0 <= self._q_idx < len(self._queue):
            dur = get_duration_fast(self._queue[self._q_idx].path)
            if dur > 0:
                self._queue[self._q_idx].duration = dur
                self._song_dur = dur
                self._time_tot.set(_fmt_dur(dur))
        if dur <= 0: return
        w = self._prog_cv.winfo_width()
        if w <= 1: return
        self._seeking = True
        pct = max(0.0, min(1.0, event.x / w))
        # Update bar immediately for responsive feel (seek on mouse-up via B1-Motion end)
        self._prog_cv.coords(self._prog_fill, 0, 0, int(w*pct), 12)
        self._prog_cv.coords(self._prog_dot, int(w*pct)-6,-2, int(w*pct)+6,14)
        self._time_cur.set(_fmt_dur(pct * dur))
        self._do_seek(pct * dur)
        self._seeking = False

    def _seek_relative(self, delta_secs: float):
        """Seek forward/backward by delta seconds (keyboard shortcuts)."""
        if not _PYGAME or not self._playing: return
        elapsed = time.time() - self._play_start
        self._do_seek(elapsed + delta_secs)

    def _update_player(self):
        if _PYGAME and self._playing and not self._seeking:
            elapsed = time.time() - self._play_start
            self._time_cur.set(_fmt_dur(elapsed))

            if self._song_dur > 0:
                pct = min(elapsed / self._song_dur, 1.0)
                # Main player bar
                w = self._prog_cv.winfo_width()
                if w > 1:
                    self._prog_cv.coords(self._prog_fill, 0, 0, int(w*pct), 12)
                    self._prog_cv.coords(self._prog_dot,
                                         int(w*pct)-6,-2, int(w*pct)+6,14)
                # Auto-advance: song ended
                if not pygame.mixer.music.get_busy() and elapsed > 1.5:
                    self._next_song(auto=True)
            else:
                # Duration unknown — try to get it now, and still detect song end
                if not pygame.mixer.music.get_busy() and elapsed > 1.5:
                    self._next_song(auto=True)

        self.root.after(400, self._update_player)

    # ──────────────────────────────────────────────────────────────────────────
    #  WORKERS
    # ──────────────────────────────────────────────────────────────────────────
    def _upload(self):
        if not _LIBROSA:
            messagebox.showerror("librosa not found",
                                 "Run:\n\n    pip install librosa soundfile"); return
        paths = filedialog.askopenfilenames(
            title="Select audio files",
            filetypes=[("Audio","*.mp3 *.wav *.flac *.ogg"),
                       ("MP3","*.mp3"),("All","*.*")])
        if not paths: return
        existing = {s.path for s in self.songs}; added = 0
        for p in paths:
            if p not in existing:
                song = Song(p); self.songs.append(song)
                self._wq.put(song); added += 1
        if added:
            self._dirty = True; self._render_library()
            self._status.set(f"Queued {added} songs for analysis…")

    def _start_workers(self):
        for _ in range(WORKER_THREADS):
            threading.Thread(target=self._analysis_worker, daemon=True).start()
        threading.Thread(target=self._metadata_worker, daemon=True).start()
        threading.Thread(target=self._affinity_worker, daemon=True).start()

    def _affinity_worker(self):
        """Teach per-goal emotion affinity from online metadata (once a week).
        Refines — never overrides — the research profile and user feedback."""
        time.sleep(4)   # let startup settle
        aff   = self.feedback.setdefault("affinity", {})
        stamp = self.feedback.get("affinity_ts", 0)
        if time.time() - stamp < 7 * 86400 and aff:
            return   # fresh enough
        changed = False
        for goal in GOALS:
            data = learn_affinity_online(goal)
            if data:
                aff[goal] = data; changed = True
                time.sleep(0.5)   # be polite to the API
        if changed:
            self.feedback["affinity_ts"] = time.time()
            save_feedback(self.feedback)
            self.root.after(0, lambda: self._status.set(
                "🌐 Learned mood→music associations from online metadata"))

    def _analysis_worker(self):
        while True:
            song = self._wq.get()
            try:
                feats       = extract_features(song.path)
                inet_em     = genre_to_emotion(song.internet_emotion)
                lyr_em      = _emotion_from_lyrics(
                    self.lyrics_db.get(song.path,{}).get("lyrics",""))
                em, src     = knn_emotion(feats, self.corrections, inet_em, lyr_em)
                self._rq.put(("ok", song, feats, em, src))
                self._mq.put(song)
            except Exception as exc:
                self._rq.put(("err", song, str(exc)))

    def _metadata_worker(self):
        """Single thread: iTunes genre + cover art + lyrics (rate-limited)."""
        while True:
            song = self._mq.get()
            artist, title = song.artist, song.title or song.name

            # 1. Genre + cover art
            if not song.internet_genre:
                genre, cover = internet_meta(title, artist)
                if genre:
                    song.internet_genre   = genre
                    song.internet_emotion = genre_to_emotion(genre)
                if cover:
                    song.cover_url = cover
                if not song.user_override and song.internet_genre and song.features:
                    lyr_em  = _emotion_from_lyrics(
                        self.lyrics_db.get(song.path,{}).get("lyrics",""))
                    new_em, src = knn_emotion(song.features, self.corrections,
                                              song.internet_emotion, lyr_em)
                    song.emotion    = new_em
                    song.tag_source = src
                self._uq.put(song)   # refresh this row (cover/genre/emotion may have changed)

            # 2. Lyrics
            if song.path not in self.lyrics_db or not self.lyrics_db[song.path].get("lyrics"):
                lyrics = fetch_lyrics(title, artist)
                if lyrics:
                    self.lyrics_db[song.path] = {"lyrics": lyrics, "source": "auto"}
                    save_lyrics_db(self.lyrics_db)
                    lyr_em = _emotion_from_lyrics(lyrics)
                    if lyr_em and not song.user_override and song.features:
                        inet_em = genre_to_emotion(song.internet_emotion)
                        new_em, src = knn_emotion(song.features, self.corrections,
                                                   inet_em, lyr_em)
                        song.emotion    = new_em
                        song.tag_source = src
                    self._uq.put(song)   # refresh row — lyrics ♪ / emotion changed

            time.sleep(0.4)   # rate limiting — be polite to free APIs

    def _poll(self):
        changed_songs = []
        try:
            while True:
                msg = self._rq.get_nowait()
                if msg[0] == "ok":
                    _, song, feats, em, src = msg
                    song.features      = feats
                    song.original_auto = em
                    if song.duration <= 0 and feats.get("full_duration",0) > 0:
                        song.duration = feats["full_duration"]
                    if not song.user_override:
                        song.emotion    = em
                        song.tag_source = src
                    song.busy = False
                else:
                    _, song, err = msg
                    song.emotion = "calm"; song.busy = False; song.failed = True
                changed_songs.append(song)
        except queue.Empty:
            pass

        # Background metadata / emotion / lyrics updates → refresh only those rows
        try:
            while True:
                changed_songs.append(self._uq.get_nowait())
        except queue.Empty:
            pass

        if changed_songs:
            # ── In-place row refresh: NO full-list rebuild → no flicker, and the
            #    user's scroll position is preserved. Only the rows that actually
            #    changed are repainted. ──────────────────────────────────────────
            seen = set()
            for song in changed_songs:
                if id(song) in seen: continue
                seen.add(id(song))
                self._update_song_row(song)

            busy  = sum(1 for s in self.songs if s.busy)
            total = len(self.songs)
            done  = total - busy
            if total == 0:
                self._lib_info.set("No songs yet")
            elif busy:
                pct = int(done / total * 100) if total else 0
                self._lib_info.set(f"{done}/{total} analysed  ({pct}%)")
            else:
                self._lib_info.set(f"{total} songs  ·  showing {len(self._lib_visible)}")

            # Throttled save covers both analysis results and metadata updates
            if time.time() - self._last_save >= 8.0:
                save_library(self.songs, self.custom_tags)
                self._last_save = time.time()

        self.root.after(300, self._poll)

    def _load_saved(self):
        songs, custom_tags = load_library()
        self.custom_tags   = custom_tags
        if songs:
            existing = {s.path for s in self.songs}
            self.songs.extend([s for s in songs if s.path not in existing])
            self._render_library(); self._build_filter_chips()
            locked = sum(1 for s in self.songs if s.user_override)
            self._status.set(f"Loaded {len(songs)} songs  ·  {locked} user-tagged  ·  "
                             f"{len(self.corrections)} corrections in learning model")
            # Queue un-looked-up saved songs for metadata
            threading.Thread(target=self._backfill_metadata, daemon=True).start()
        self.playlists = load_playlists()

    def _backfill_metadata(self):
        """Queue saved songs that haven't been looked up yet."""
        time.sleep(2)   # let the UI settle
        for song in list(self.songs):
            if not song.internet_genre or song.path not in self.lyrics_db:
                self._mq.put(song)
                time.sleep(0.1)   # spread the load

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _on_wheel(self, event):
        cv = self._wheel_target()
        if cv is not None:
            try: cv.yview_scroll(int(-1*event.delta/120), "units")
            except Exception: pass

    def _wheel_target(self):
        """Find the scrollable canvas under the pointer by walking the widget tree.
        Robust against the Enter/Leave 'inferior' events that used to clear
        _scroll_tgt the moment the pointer moved onto a row inside the canvas."""
        try:
            w = self.root.winfo_containing(self.root.winfo_pointerx(),
                                           self.root.winfo_pointery())
        except Exception:
            return None
        while w is not None:
            if w in self._scroll_canvases:
                return w
            w = getattr(w, "master", None)
        return None

    def _ph_clear(self, _):
        if self._ph:
            self._mood.delete("1.0", END); self._mood.config(fg=TXT); self._ph = False

    def _ph_restore(self, _):
        if not self._mood.get("1.0", END).strip():
            self._mood.insert("1.0","e.g. I'm exhausted and a bit stressed out…")
            self._mood.config(fg=TXT_DIM); self._ph = True


# ==============================================================================
if __name__ == "__main__":
    root = Tk()
    SolaceApp(root)
    root.mainloop()
