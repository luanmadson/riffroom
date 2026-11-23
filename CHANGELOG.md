# Changes

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
