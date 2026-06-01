# Solace

A desktop app that analyzes your music library using audio feature extraction 
(librosa) and builds personalized playlists based on your emotional state.

## How it works
1. Upload your MP3 files
2. The app extracts tempo, energy, and spectral features from each song
3. A classifier assigns each song an emotion tag (energetic, calm, sad, etc.)
4. You describe how you're feeling and pick a goal
5. Solace builds a playlist from your library that arcs toward your goal

## Tech
- Python, tkinter, librosa, numpy

## Run it
pip install librosa numpy
python solace.py
