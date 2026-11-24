# Changes

## 2.3.0 (24/11/2026)

- **Filter the takes by part**: with takes of two or more parts, buttons above the list (All, Rhythm, Bass…) show only the takes of one part, with how many each one has and the color of the part in the band.
  - The choice is kept for each song.
  - A new take of another part (just recorded, or back with Undo) switches the filter to its part, so it never arrives hidden.
  - Selecting works on the takes shown: the box at the top, "All but ★", Shift+click and Delete. Changing the filter clears the selection of the takes that are no longer shown.
  - The band keeps offering every take of each part, with any filter.

## 2.2.0 (24/11/2026)

- **Delete many takes at once**:
  - each take has a box on the left. Once one is ticked, the top of the panel shows how many are selected, with **All but ★** (everything except the favorites), **Delete** and ✕;
  - Shift+click ticks a range, and Ctrl+click on a row starts the selection. The Delete key deletes the selected takes, and Esc clears the selection;
  - they go to the trash folder of the takes, and one **Undo** brings them all back.
- MCP: `delete_take` also takes a list (`take_ids`), and the app shows one Undo for all of them.
- If the file of a take is open in another program (OneDrive uploading it, a player), that take stays where it was, audio and record together, and the app says which one could not move.

## 2.1.0 (24/11/2026)

- **Lyrics** (`app/lyrics.py`), optional, in a panel next to the band (button or key K):
  - **Follow**: the line being sung lights up a moment early and the lyrics scroll by themselves. Off: just the text, to read. Click a line to play from it.
  - Paste them (plain text or LRC), or leave an `.lrc` in the song folder: the first time, Riffroom lines it up with the separated voice and keeps its copy as `lyrics.lrc`. The original file is not changed.
  - Without times: mark them while the song plays (Enter at the start of each line). The −0.1 s and +0.1 s buttons fine-tune.
  - MCP: `get_lyrics` (each line with its second, bar and section), `set_lyrics` and `shift_lyrics`.
- Clipping tip with the BOSS GT-100: it now says "the patch level on the GT-100" instead of "the PATCH LEVEL". The PATCH LEVEL only reaches the USB recording when the USB block is the last of the effects chain.

## 2.0.1 (23/11/2026)

- Installer: it stopped while creating the shortcuts (two PowerShell variables differed only by case). Fixed.

## 2.0.0 (23/11/2026): Riffroom

The app gets a new name, **Riffroom** (it was "Sala de Ensaio"), and a new license, **GPL-3.0-or-later** (1.0.0 was MIT).

- **Made for AI assistants**: an MCP server (`app/mcp_server.py`, official MCP SDK 2.3) with 25 tools and a `coach_session` prompt.
  - With it, Claude Desktop, Claude Code or any MCP client can open songs, loop sections, record, analyze and compare takes, write chord charts and import songs.
  - The installer registers it in Claude Code and Claude Desktop.
- **Timing analysis of the takes** (`app/analysis.py`):
  - finds every strum, to about 1 ms, even on distorted guitar;
  - compares the strums with the original part or the beat;
  - reports early or late, consistency, the bars that strayed from the player's own average, and the bars left silent;
  - a per-bar chart in the app.
- **Import a song with the local AI** (`install.bat -AI`), all on the user's computer:
  - Demucs separates the instruments;
  - beat-this finds the beats and bars (12/8 or 6/8 is decided with the help of the chord changes);
  - chord suggestions come from chroma, chord templates and Viterbi smoothing;
  - a count-in pre-roll is added.
- **Three languages**: English (default), Portuguese and Spanish. This covers the app, the server messages, the installer and the README.
  - Note names and the count change with the language ("1-and-a", "1-na-na", "1-y-a").
- **English code and file formats**: `riffroom.json`, the take records and `config.json`.
  - Version 1 files (`ensaio.json`, take records and the "Sala de Ensaio" configuration) are read and converted.
  - Takes folders follow the language (`Takes`, `Gravações/Tentativas`, `Grabaciones/Tomas`); an existing folder is kept.
- The count-in at the start of a song now knows whether the drum track already has one (`count_in_in_drums`).
- Guitar tuning per song (`tuning`): chords are named by the shape played, and the app shows "half step down".

## 1.0.0 (22/11/2026): Sala de Ensaio

First release. Its features:
- the separated band;
- the NOW panel and the timeline;
- the count-in and the metronome;
- takes aligned with the band, "Fix the timing", MP3;
- any audio interface;
- music folders, the chord chart in plain text;
- the demo song and the Windows installer.
