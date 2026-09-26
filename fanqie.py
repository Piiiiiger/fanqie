#!/usr/bin/env python3
"""Fanqie — a small countdown / stopwatch timer with history, made for niri.

Runs as a single instance: launching it again raises the existing window,
and `fanqie --toggle` starts/pauses the current timer (handy for a keybind).
"""

import json
import math
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

APP_ID = "dev.pigger.Fanqie"
PRESETS = (5, 15, 25, 45)  # minutes
DEFAULT_MINUTES = 25
MIN_SAVE_SECONDS = 5  # shorter runs are not worth recording
STATUS_PATH = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "fanqie-status.json"

CSS = b"""
.timer-big {
  font-size: 42px;
  font-weight: 300;
  font-feature-settings: "tnum";
}
.timer-done { color: @success_color; }
.circular-big { min-width: 64px; min-height: 64px; }
"""


def fmt_clock(seconds, tenths=False):
    seconds = max(0.0, seconds)
    whole = int(seconds)
    h, rem = divmod(whole, 3600)
    m, s = divmod(rem, 60)
    text = f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
    if tenths:
        text += f".{int((seconds - whole) * 10)}"
    return text


def fmt_duration(seconds):
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


# ---------------------------------------------------------------- model


class Clock:
    """Pausable monotonic clock."""

    def __init__(self):
        self.reset()

    def reset(self):
        self._acc = 0.0
        self._since = None
        self.started_at = None

    @property
    def running(self):
        return self._since is not None

    @property
    def touched(self):
        return self.started_at is not None

    def start(self):
        if self.started_at is None:
            self.started_at = datetime.now()
        if self._since is None:
            self._since = time.monotonic()

    def pause(self):
        if self._since is not None:
            self._acc += time.monotonic() - self._since
            self._since = None

    def elapsed(self):
        live = time.monotonic() - self._since if self._since is not None else 0.0
        return self._acc + live


class History:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS sessions (
                   id         INTEGER PRIMARY KEY,
                   mode       TEXT NOT NULL,      -- 'countdown' | 'stopwatch'
                   label      TEXT NOT NULL DEFAULT '',
                   started_at TEXT NOT NULL,      -- ISO local time
                   duration   REAL NOT NULL,      -- seconds actually timed
                   target     REAL,               -- countdown length, seconds
                   completed  INTEGER NOT NULL DEFAULT 1
               )"""
        )
        self.db.commit()

    def add(self, mode, label, started_at, duration, target=None, completed=True):
        self.db.execute(
            "INSERT INTO sessions (mode, label, started_at, duration, target, completed)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (mode, label, started_at.isoformat(timespec="seconds"), duration, target, int(completed)),
        )
        self.db.commit()

    def recent(self, limit=500):
        return self.db.execute(
            "SELECT id, mode, label, started_at, duration, target, completed"
            " FROM sessions ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()

    def total_since(self, since=None):
        if since is None:
            row = self.db.execute("SELECT COALESCE(SUM(duration), 0) FROM sessions").fetchone()
        else:
            row = self.db.execute(
                "SELECT COALESCE(SUM(duration), 0) FROM sessions WHERE started_at >= ?",
                (since.isoformat(timespec="seconds"),),
            ).fetchone()
        return row[0]

    def daily_totals(self, since):
        """{'YYYY-MM-DD': (seconds, sessions)} for every day on or after `since` (a date)."""
        rows = self.db.execute(
            "SELECT substr(started_at, 1, 10) AS day, SUM(duration), COUNT(*)"
            " FROM sessions WHERE started_at >= ? GROUP BY day",
            (since.isoformat(),),
        ).fetchall()
        return {day: (secs, count) for day, secs, count in rows}

    def on_day(self, day):
        return self.db.execute(
            "SELECT id, mode, label, started_at, duration, target, completed"
            " FROM sessions WHERE substr(started_at, 1, 10) = ? ORDER BY started_at DESC",
            (day,),
        ).fetchall()

    def delete(self, session_id):
        self.db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        self.db.commit()

    def clear(self):
        self.db.execute("DELETE FROM sessions")
        self.db.commit()


# ---------------------------------------------------------------- UI


class TimerPage(Gtk.Box):
    """Shared layout for countdown and stopwatch: label entry, big display, controls."""

    mode = ""

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.win = win
        self.clock = Clock()
        for side in ("top", "bottom", "start", "end"):
            getattr(self, f"set_margin_{side}")(12)

        self.entry = Gtk.Entry(placeholder_text="What are you working on?")
        self.entry.connect("activate", lambda *_: self.win.set_focus(None))
        self.entry.connect("changed", lambda *_: self.win.tick())
        self.append(self.entry)

        self.display = Gtk.Label(css_classes=["timer-big"], vexpand=True, valign=Gtk.Align.CENTER)
        self.append(self.display)

        self.build_middle()

        controls = Gtk.Box(spacing=18, halign=Gtk.Align.CENTER, margin_top=2)
        self.discard_btn = Gtk.Button(
            icon_name="edit-undo-symbolic",
            tooltip_text="Discard (reset without saving)",
            css_classes=["circular"],
            valign=Gtk.Align.CENTER,
        )
        self.discard_btn.connect("clicked", lambda *_: self.discard())
        self.toggle_btn = Gtk.Button(
            icon_name="media-playback-start-symbolic",
            tooltip_text="Start / pause (Space)",
            css_classes=["circular", "suggested-action", "circular-big"],
        )
        self.toggle_btn.connect("clicked", lambda *_: self.toggle())
        self.stop_btn = Gtk.Button(
            icon_name="media-playback-stop-symbolic",
            tooltip_text="Stop and save to history",
            css_classes=["circular"],
            valign=Gtk.Align.CENTER,
        )
        self.stop_btn.connect("clicked", lambda *_: self.stop_and_save())
        for b in (self.discard_btn, self.toggle_btn, self.stop_btn):
            controls.append(b)
        self.append(controls)

        self.refresh()

    # hooks for subclasses
    def build_middle(self):
        pass

    def refresh(self):
        pass

    def save(self):
        pass

    # common behaviour
    def toggle(self):
        if self.clock.running:
            self.clock.pause()
        else:
            self.clock.start()
            self.win.ensure_ticking()
        self.sync_controls()
        self.refresh()
        self.win.timer_state_changed(hide_if_running=self.clock.running)

    def discard(self):
        self.clock.reset()
        self.sync_controls()
        self.refresh()
        self.win.timer_state_changed()

    def stop_and_save(self):
        if self.clock.touched:
            self.clock.pause()
            self.save()
        self.discard()

    def sync_controls(self):
        running = self.clock.running
        self.toggle_btn.set_icon_name(
            "media-playback-pause-symbolic" if running else "media-playback-start-symbolic"
        )
        self.discard_btn.set_sensitive(self.clock.touched)
        self.stop_btn.set_sensitive(self.clock.touched)

    def label_text(self):
        return self.entry.get_text().strip()

    def task_name(self):
        return self.label_text() or self.mode.capitalize()


class CountdownPage(TimerPage):
    mode = "countdown"

    def build_middle(self):
        self.target = DEFAULT_MINUTES * 60

        self.progress = Gtk.ProgressBar()
        self.append(self.progress)

        row = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        presets = Gtk.Box(css_classes=["linked"])
        self.preset_btns = {}
        group = None
        for minutes in PRESETS:
            b = Gtk.ToggleButton(label=f"{minutes}m", group=group)
            group = group or b
            b.connect("toggled", self.on_preset, minutes)
            presets.append(b)
            self.preset_btns[minutes] = b
        row.append(presets)

        self.spin = Gtk.SpinButton.new_with_range(1, 600, 1)
        self.spin.set_value(DEFAULT_MINUTES)
        self.spin.set_tooltip_text("Custom length in minutes")
        self.spin.connect("value-changed", self.on_spin)
        row.append(self.spin)
        self.append(row)

        self.preset_btns.get(DEFAULT_MINUTES, group).set_active(True)

    def on_preset(self, button, minutes):
        if button.get_active() and self.spin.get_value_as_int() != minutes:
            self.spin.set_value(minutes)

    def on_spin(self, spin):
        minutes = spin.get_value_as_int()
        self.target = minutes * 60
        if minutes in self.preset_btns:
            self.preset_btns[minutes].set_active(True)
        else:
            for b in self.preset_btns.values():
                b.set_active(False)
        self.refresh()

    def remaining(self):
        return self.target - self.clock.elapsed()

    def refresh(self):
        remaining = self.remaining()
        # ceil so "00:00" only shows once the time is actually up
        self.display.set_label(fmt_clock(float(int(remaining + 0.999))))
        self.progress.set_fraction(min(1.0, self.clock.elapsed() / self.target))
        locked = self.clock.touched
        self.spin.set_sensitive(not locked)
        for b in self.preset_btns.values():
            b.set_sensitive(not locked)

    def tick(self):
        if self.clock.running and self.remaining() <= 0:
            self.finish()
        self.refresh()

    def finish(self):
        self.clock.pause()
        label = self.label_text()
        self.win.history.add(self.mode, label, self.clock.started_at, self.target, self.target, True)
        self.clock.reset()
        self.sync_controls()
        self.win.on_history_changed()
        self.win.alert("Time's up!", f"{label or 'Countdown'} — {fmt_duration(self.target)} done")

    def save(self):
        elapsed = min(self.clock.elapsed(), self.target)
        if elapsed >= MIN_SAVE_SECONDS:
            self.win.history.add(
                self.mode, self.label_text(), self.clock.started_at, elapsed, self.target, False
            )
            self.win.on_history_changed()

    def title(self):
        return f"{self.task_name()} · {fmt_clock(float(int(self.remaining() + 0.999)))}"

    def time_text(self):
        return fmt_clock(float(int(self.remaining() + 0.999)))


class StopwatchPage(TimerPage):
    mode = "stopwatch"

    def refresh(self):
        self.display.set_label(fmt_clock(self.clock.elapsed(), tenths=True))

    def tick(self):
        self.refresh()

    def save(self):
        elapsed = self.clock.elapsed()
        if elapsed >= MIN_SAVE_SECONDS:
            self.win.history.add(self.mode, self.label_text(), self.clock.started_at, elapsed)
            self.win.on_history_changed()

    def title(self):
        return f"{self.task_name()} · {fmt_clock(self.clock.elapsed())}"

    def time_text(self):
        return fmt_clock(self.clock.elapsed())


HEAT_WEEKS = 53
HEAT_CELL = 11
HEAT_STEP = HEAT_CELL + 3  # cell + gap
HEAT_TOP = 16  # room for month labels
HEAT_HEIGHT = HEAT_TOP + 7 * HEAT_STEP
# A day reaches level 1..4 once its total passes these many seconds.
HEAT_THRESHOLDS = (0, 25 * 60, 60 * 60, 2 * 3600)
HEAT_LIGHT = ("#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39")
HEAT_DARK = ("#383838", "#0e4429", "#006d32", "#26a641", "#39d353")


def heat_level(seconds):
    return sum(seconds > t for t in HEAT_THRESHOLDS)


def rounded_rect(cr, x, y, size, r=2):
    cr.new_sub_path()
    cr.arc(x + size - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + size - r, y + size - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + size - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


class Heatmap(Gtk.Box):
    """GitHub-style year of daily totals: columns are weeks (Mon–Sun), newest on the right."""

    def __init__(self, on_select):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.on_select = on_select
        self.days = {}
        self.selected = None
        self.style = Adw.StyleManager.get_default()
        self.style.connect("notify::dark", lambda *_: self.redraw())

        grid_row = Gtk.Box(spacing=4)
        self.weekdays = Gtk.DrawingArea(content_width=26, content_height=HEAT_HEIGHT, valign=Gtk.Align.START)
        self.weekdays.set_draw_func(self.draw_weekdays)
        grid_row.append(self.weekdays)

        self.grid = Gtk.DrawingArea(
            content_width=HEAT_WEEKS * HEAT_STEP, content_height=HEAT_HEIGHT, margin_bottom=8,
            has_tooltip=True,
        )
        self.grid.set_draw_func(self.draw_grid)
        self.grid.connect("query-tooltip", self.on_tooltip)
        click = Gtk.GestureClick()
        click.connect("released", self.on_click)
        self.grid.add_controller(click)

        scroller = Gtk.ScrolledWindow(
            child=self.grid, hexpand=True,
            hscrollbar_policy=Gtk.PolicyType.AUTOMATIC, vscrollbar_policy=Gtk.PolicyType.NEVER,
        )
        # Keep the most recent weeks in view, like GitHub on a narrow screen.
        scroller.get_hadjustment().connect(
            "changed", lambda adj: adj.set_value(adj.get_upper() - adj.get_page_size())
        )
        grid_row.append(scroller)
        self.append(grid_row)

        footer = Gtk.Box(spacing=4)
        self.total_label = Gtk.Label(xalign=0, hexpand=True, css_classes=["dim-label", "caption"])
        footer.append(self.total_label)
        footer.append(Gtk.Label(label="Less", css_classes=["dim-label", "caption"]))
        legend = Gtk.DrawingArea(content_width=5 * HEAT_STEP, content_height=HEAT_CELL, valign=Gtk.Align.CENTER)
        legend.set_draw_func(self.draw_legend)
        self.legend = legend
        footer.append(legend)
        footer.append(Gtk.Label(label="More", css_classes=["dim-label", "caption"]))
        self.append(footer)

        self.set_range()

    def set_range(self):
        self.today = datetime.now().date()
        self.start = self.today - timedelta(days=self.today.weekday(), weeks=HEAT_WEEKS - 1)

    def set_data(self, days, selected):
        self.set_range()
        self.days = days
        self.selected = selected
        total = sum(secs for secs, _ in days.values())
        active = sum(1 for secs, _ in days.values() if secs > 0)
        self.total_label.set_label(f"{fmt_duration(total)} over {active} days in the last year")
        self.redraw()

    def redraw(self):
        for area in (self.grid, self.weekdays, self.legend):
            area.queue_draw()

    def palette(self):
        colors = HEAT_DARK if self.style.get_dark() else HEAT_LIGHT
        out = []
        for c in colors:
            rgba = Gdk.RGBA()
            rgba.parse(c)
            out.append(rgba)
        return out

    def day_at(self, x, y):
        col, row = int(x // HEAT_STEP), int((y - HEAT_TOP) // HEAT_STEP)
        if y < HEAT_TOP or not (0 <= col < HEAT_WEEKS and 0 <= row < 7):
            return None
        day = self.start + timedelta(days=col * 7 + row)
        return day if day <= self.today else None

    def draw_grid(self, area, cr, _w, _h):
        colors = self.palette()
        fg = area.get_color()
        for col in range(HEAT_WEEKS):
            for row in range(7):
                day = self.start + timedelta(days=col * 7 + row)
                if day > self.today:
                    break
                x, y = col * HEAT_STEP, HEAT_TOP + row * HEAT_STEP
                secs = self.days.get(day.isoformat(), (0, 0))[0]
                Gdk.cairo_set_source_rgba(cr, colors[heat_level(secs)])
                rounded_rect(cr, x, y, HEAT_CELL)
                cr.fill()
                if day.isoformat() == self.selected:
                    cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.9)
                    cr.set_line_width(1.5)
                    rounded_rect(cr, x + 0.75, y + 0.75, HEAT_CELL - 1.5)
                    cr.stroke()

        # Month names above the first column of each month; drop the very first
        # one if the next month starts too soon for both to fit.
        marks = []
        for col in range(HEAT_WEEKS):
            monday = self.start + timedelta(weeks=col)
            if col == 0 or monday.month != (monday - timedelta(weeks=1)).month:
                marks.append((col, monday.strftime("%b")))
        if len(marks) > 1 and marks[1][0] - marks[0][0] < 3:
            marks.pop(0)
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.6)
        cr.set_font_size(10)
        for col, name in marks:
            cr.move_to(col * HEAT_STEP, 10)
            cr.show_text(name)

    def draw_weekdays(self, area, cr, _w, _h):
        fg = area.get_color()
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.6)
        cr.set_font_size(10)
        for row, name in ((0, "Mon"), (2, "Wed"), (4, "Fri")):
            cr.move_to(0, HEAT_TOP + row * HEAT_STEP + HEAT_CELL - 2)
            cr.show_text(name)

    def draw_legend(self, _area, cr, _w, _h):
        for i, color in enumerate(self.palette()):
            Gdk.cairo_set_source_rgba(cr, color)
            rounded_rect(cr, i * HEAT_STEP, 0, HEAT_CELL)
            cr.fill()

    def on_tooltip(self, _area, x, y, _keyboard, tooltip):
        day = self.day_at(x, y)
        if day is None:
            return False
        secs, count = self.days.get(day.isoformat(), (0, 0))
        when = day.strftime("%a, %b %d %Y")
        tooltip.set_text(
            f"{fmt_duration(secs)} · {count} session{'s' if count != 1 else ''}\n{when}"
            if count else f"No sessions\n{when}"
        )
        return True

    def on_click(self, _gesture, _n, x, y):
        day = self.day_at(x, y)
        if day is not None:
            key = day.isoformat()
            self.on_select(None if key == self.selected else key)


class HistoryPage(Adw.PreferencesPage):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.groups = []
        self.selected_day = None

        activity = Adw.PreferencesGroup(title="Activity")
        self.heatmap = Heatmap(self.select_day)
        activity.add(self.heatmap)
        self.add(activity)

        self.summary = Adw.PreferencesGroup(title="Summary")
        clear = Gtk.Button(label="Clear all", css_classes=["flat", "destructive-action"])
        clear.connect("clicked", self.on_clear)
        self.summary.set_header_suffix(clear)
        self.today_row = Adw.ActionRow(title="Today")
        self.week_row = Adw.ActionRow(title="This week")
        self.all_row = Adw.ActionRow(title="All time")
        self.totals = {}
        for row in (self.today_row, self.week_row, self.all_row):
            value = Gtk.Label(css_classes=["dim-label", "numeric"])
            row.add_suffix(value)
            self.totals[row] = value
            self.summary.add(row)
        self.add(self.summary)

        self.empty = Adw.StatusPage(
            icon_name="alarm-symbolic",
            title="No history yet",
            description="Finished countdowns and saved stopwatches show up here.",
        )

        self.rebuild()

    def rebuild(self):
        for g in self.groups:
            self.remove(g)
        self.groups.clear()

        now = datetime.now()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week = today - timedelta(days=today.weekday())
        h = self.win.history
        self.totals[self.today_row].set_label(fmt_duration(h.total_since(today)))
        self.totals[self.week_row].set_label(fmt_duration(h.total_since(week)))
        self.totals[self.all_row].set_label(fmt_duration(h.total_since()))

        self.heatmap.set_data(h.daily_totals(self.heatmap.start), self.selected_day)

        if self.selected_day:
            rows = h.on_day(self.selected_day)
        else:
            rows = h.recent()
            if not rows:
                group = Adw.PreferencesGroup()
                group.add(self.empty)
                self.add(group)
                self.groups.append(group)
                return

        by_day = {}
        for r in rows:
            by_day.setdefault(r[3][:10], []).append(r)
        if self.selected_day and not rows:
            by_day[self.selected_day] = []

        for day, sessions in by_day.items():
            date = datetime.fromisoformat(day)
            if date.date() == today.date():
                title = "Today"
            elif date.date() == (today - timedelta(days=1)).date():
                title = "Yesterday"
            else:
                title = date.strftime("%a, %b %d %Y")
            total = sum(s[4] for s in sessions)
            group = Adw.PreferencesGroup(
                title=title,
                description=f"{len(sessions)} sessions · {fmt_duration(total)}" if sessions else "No sessions",
            )
            if self.selected_day:
                show_all = Gtk.Button(label="Show all", css_classes=["flat"], valign=Gtk.Align.CENTER)
                show_all.connect("clicked", lambda *_: self.select_day(None))
                group.set_header_suffix(show_all)
            for s in sessions:
                group.add(self.make_row(*s))
            self.add(group)
            self.groups.append(group)

    def select_day(self, day):
        self.selected_day = day
        self.rebuild()

    def make_row(self, sid, mode, label, started_at, duration, target, completed):
        start = datetime.fromisoformat(started_at).strftime("%H:%M")
        if mode == "countdown":
            kind = f"Countdown {fmt_duration(target)}"
            kind += "" if completed else " · stopped early"
        else:
            kind = "Stopwatch"
        row = Adw.ActionRow(
            title=label or ("Countdown" if mode == "countdown" else "Stopwatch"),
            subtitle=f"{start} · {kind}",
            use_markup=False,
        )
        row.add_prefix(
            Gtk.Image(
                icon_name="alarm-symbolic" if mode == "countdown" else "preferences-system-time-symbolic"
            )
        )
        row.add_suffix(Gtk.Label(label=fmt_duration(duration), css_classes=["numeric"]))
        delete = Gtk.Button(
            icon_name="user-trash-symbolic",
            css_classes=["flat"],
            valign=Gtk.Align.CENTER,
            tooltip_text="Delete",
        )
        delete.connect("clicked", lambda *_: (self.win.history.delete(sid), self.rebuild()))
        row.add_suffix(delete)
        return row

    def on_clear(self, _btn):
        dialog = Adw.AlertDialog(
            heading="Clear all history?", body="This permanently deletes every recorded session."
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("clear", "Clear")
        dialog.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self.on_clear_response)
        dialog.present(self.win)

    def on_clear_response(self, _dialog, response):
        if response == "clear":
            self.win.history.clear()
            self.selected_day = None
            self.rebuild()


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Fanqie", default_width=360, default_height=340)
        self.history = app.history
        self._timer_source = 0
        self._held = False
        self._last_status = None

        self.countdown = CountdownPage(self)
        self.stopwatch = StopwatchPage(self)
        self.history_page = HistoryPage(self)

        self.stack = Adw.ViewStack()
        self.stack.add_titled_with_icon(self.countdown, "countdown", "Countdown", "alarm-symbolic")
        self.stack.add_titled_with_icon(
            self.stopwatch, "stopwatch", "Stopwatch", "preferences-system-time-symbolic"
        )
        self.stack.add_titled_with_icon(
            self.history_page, "history", "History", "document-open-recent-symbolic"
        )
        self.stack.set_visible_child_name("countdown")
        self.stack.connect("notify::visible-child", lambda *_: self.set_focus(None))

        view = Adw.ToolbarView(content=self.stack)
        view.add_top_bar(Adw.HeaderBar())
        view.add_bottom_bar(Adw.ViewSwitcherBar(stack=self.stack, reveal=True))
        self.set_content(view)

        # Space toggles the visible timer. Bubble phase, so typing in the entry still works.
        shortcuts = Gtk.ShortcutController(propagation_phase=Gtk.PropagationPhase.BUBBLE)
        shortcuts.add_shortcut(
            Gtk.Shortcut(
                trigger=Gtk.ShortcutTrigger.parse_string("space"),
                action=Gtk.CallbackAction.new(lambda *_: (self.toggle_current(), True)[1]),
            )
        )
        self.add_controller(shortcuts)

        for i, name in enumerate(("countdown", "stopwatch", "history"), start=1):
            action = Gio.SimpleAction(name=f"page{i}")
            action.connect("activate", lambda *_, n=name: self.stack.set_visible_child_name(n))
            self.add_action(action)

        self.connect("close-request", self.on_close)

        for page in (self.countdown, self.stopwatch):
            page.sync_controls()

    def current_timer(self):
        child = self.stack.get_visible_child()
        return child if isinstance(child, TimerPage) else self.countdown

    def toggle_current(self):
        self.current_timer().toggle()

    def ensure_ticking(self):
        if not self._timer_source:
            self._timer_source = GLib.timeout_add(100, self.on_timeout)

    def on_timeout(self):
        running = self.tick()
        if not running:
            self._timer_source = 0
        return running

    def timer_state_changed(self, hide_if_running=False):
        self.tick()
        if hide_if_running and self.get_visible():
            self.hide_to_tray()

    def hide_to_tray(self):
        if not self._held:
            self.get_application().hold()
            self._held = True
        self.set_visible(False)

    def update_status(self, active):
        if not active:
            STATUS_PATH.unlink(missing_ok=True)
            self._last_status = None
            return
        page = self.current_timer()
        if not page.clock.touched:
            page = active[0]
        status = {
            "active": True,
            "running": page.clock.running,
            "task": page.task_name(),
            "time": page.time_text(),
            "pid": os.getpid(),
        }
        payload = json.dumps(status, ensure_ascii=False)
        if payload == self._last_status:
            return
        temporary = STATUS_PATH.with_suffix(".tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(STATUS_PATH)
        self._last_status = payload

    def tick(self):
        for page in (self.countdown, self.stopwatch):
            if page.clock.running:
                page.tick()
        active = [p for p in (self.countdown, self.stopwatch) if p.clock.touched]
        running = [p for p in active if p.clock.running]
        self.set_title(" · ".join(p.title() for p in active) or "Fanqie")
        self.update_status(active)
        return bool(running)

    def on_history_changed(self):
        self.history_page.rebuild()

    def alert(self, title, body):
        n = Gio.Notification.new(title)
        n.set_body(body)
        n.set_priority(Gio.NotificationPriority.URGENT)
        self.get_application().send_notification("fanqie-done", n)
        play_sound()
        if self.get_visible():
            self.present()

    def on_close(self, *_):
        if not self.get_application().quitting:
            self.hide_to_tray()
            return True
        # Don't lose in-progress work: record it before quitting.
        for page in (self.countdown, self.stopwatch):
            if page.clock.touched:
                page.clock.pause()
                page.save()
        self.update_status([])
        if self._held:
            self.get_application().release()
            self._held = False
        return False


def play_sound():
    oga = "/usr/share/sounds/freedesktop/stereo/complete.oga"
    candidates = [["canberra-gtk-play", "-i", "complete"]]
    if Path(oga).exists():
        candidates += [["pw-play", oga], ["paplay", oga]]
    for argv in candidates:
        if GLib.find_program_in_path(argv[0]):
            try:
                Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE)
                return
            except GLib.Error:
                continue
    display = Gdk.Display.get_default()
    if display:
        display.beep()


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.window = None
        self.quitting = False
        self.tray_process = None
        self.add_main_option(
            "toggle", ord("t"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
            "Start/pause the visible timer (launches Fanqie if needed)", None,
        )
        self.add_main_option(
            "quit", ord("q"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
            "Save the current run and quit Fanqie", None,
        )
        self.history = History(Path(GLib.get_user_data_dir()) / "fanqie" / "history.db")

    def ensure_tray(self):
        if self.tray_process:
            return
        helper = Path(__file__).resolve().with_name("fanqie-tray")
        if not helper.exists():
            helper = Path(__file__).resolve().with_name("tray.py")
        try:
            self.tray_process = Gio.Subprocess.new(
                [sys.executable, str(helper), str(os.getpid())],
                Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE,
            )
        except GLib.Error as error:
            print(f"Fanqie: tray icon could not start: {error}", file=sys.stderr)

    def quit_window(self):
        self.quitting = True
        if self.window:
            self.window.close()
        else:
            self.quit()

    def do_startup(self):
        Adw.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        quit_action = Gio.SimpleAction(name="quit")
        quit_action.connect("activate", lambda *_: self.quit_window())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Ctrl>q"])
        for i in (1, 2, 3):
            self.set_accels_for_action(f"win.page{i}", [f"<Ctrl>{i}", f"<Alt>{i}"])

    def do_command_line(self, command_line):
        options = command_line.get_options_dict().end().unpack()
        if options.get("quit"):
            self.quit_window()
            return 0
        win = self.window
        fresh = win is None
        if fresh:
            win = Window(self)
            self.window = win
            self.ensure_tray()
        if options.get("toggle"):
            win.toggle_current()
        if fresh or not options.get("toggle"):
            win.present()
            win.set_focus(None)
        return 0


if __name__ == "__main__":
    sys.exit(App().run(sys.argv))
