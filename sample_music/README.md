# sample_music/

Drop a handful of audio files (`.mp3`, `.m4a`, `.ogg`, `.wav`) here to make them
**playable in the deployed web app** on your iPad/iPhone/any browser.

## How matching works
The web app matches a file here to a song in your library by **filename**.
Your library stores each song's original filename (e.g. `Artist - Title.mp3`).
If you put a file with that **exact same filename** into this folder, that song
lights up with a working play button in the web UI. Songs without a matching
file here still appear (with their emotion, lyrics and in generated playlists) —
they just show a ◌ instead of a play button.

### Example
Library song path:  `C:\Users\Prakash\Music\mega mix\ODESZA - Bloom.mp3`
→ copy that file here as:  `sample_music/ODESZA - Bloom.mp3`

## Notes
- Keep it small — free hosting (Render free tier) gives ~512 MB and the whole
  repo is shipped in the image. 5–15 songs is a good demo size.
- Only use music you have the right to host. Royalty-free / your own tracks are
  safest for a public URL.
- After adding files, `git add sample_music/ && git commit && git push` — Render
  redeploys automatically and the new songs become playable.
