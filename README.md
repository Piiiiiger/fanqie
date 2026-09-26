# Fanqie 🍅

A small floating countdown / stopwatch timer for the niri Wayland compositor, with a time history.
Built with Python + GTK4 + libadwaita. History is stored in SQLite at `~/.local/share/fanqie/history.db`.

## Dependencies (Arch)

    sudo pacman -S python-gobject gtk4 libadwaita
    # optional, for the finish sound:
    sudo pacman -S libcanberra sound-theme-freedesktop

## Install

    ./install.sh

Then merge `niri.kdl` into `~/.config/niri/config.kdl` (niri reloads its config automatically).
If you already have a `binds {}` block, put the two binds inside it and don't add a second block.
The sample uses `Mod+Alt+T` to open Fanqie and `Mod+Alt+Shift+T` to start or pause it.
If DankMaterialShell is installed, the installer also copies the optional `fanqie` bar widget. Add `fanqie` to a
bar's widget list in DankMaterialShell settings to show active tasks at the top of the screen.

## Usage

- **Countdown**: pick a preset (5/15/25/45 min) or type a length, then press Start. When it finishes you get a
  notification, a sound, and the session is saved.
- **Stopwatch**: Start/pause as often as you like. Press ■ to save the session.
- Buttons: ↶ discards the run without saving, ▶/⏸ starts/pauses, ■ stops and saves. A countdown stopped early is
  saved with "stopped early". Runs shorter than 5 s are not saved.
- Give each session a label in the entry box ("What are you working on?").
- **History**: shows totals for today, this week and all time, plus sessions grouped by day. You can delete single
  entries or clear everything.
- Starting a timer or closing the editor hides it in the system tray. Click the tray icon, or open Fanqie again, to
  change or stop the timer. The tray tooltip shows the task and time.
- Ctrl+Q or `fanqie --quit` saves any run in progress and exits completely.
- While a timer is active, the window title shows the task name and time. On DankMaterialShell, the optional
  `dms-plugin/fanqie` widget shows them in the top bar even when another app has focus.

| Key | Action |
| --- | --- |
| Space | Start / pause |
| Ctrl+1 / 2 / 3 | Countdown / Stopwatch / History |
| Ctrl+Q | Save and quit |
| `fanqie --toggle` | Start/pause from anywhere (bind it in niri) |
