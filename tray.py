#!/usr/bin/env python3
"""StatusNotifier tray icon for the GTK4 Fanqie process."""

import json
import os
import sys
from pathlib import Path

from gi.repository import Gio, GLib


INTERFACE = "org.kde.StatusNotifierItem"
OBJECT_PATH = "/StatusNotifierItem"
WATCHER = "org.kde.StatusNotifierWatcher"
STATUS_PATH = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "fanqie-status.json"
XML = f"""
<node>
  <interface name="{INTERFACE}">
    <method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="SecondaryActivate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="ContextMenu"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
    <method name="Scroll"><arg type="i" direction="in"/><arg type="s" direction="in"/></method>
    <signal name="NewTitle"/>
    <signal name="NewIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus"><arg type="s"/></signal>
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="i" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
  </interface>
</node>
"""


class Tray:
    def __init__(self, parent_pid):
        self.parent_pid = parent_pid
        self.title = "Fanqie"
        self.loop = GLib.MainLoop()
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        info = Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0]
        self.registration = self.bus.register_object(
            OBJECT_PATH, info, self.on_method, self.on_property, None
        )
        self.name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self.owner = Gio.bus_own_name_on_connection(
            self.bus, self.name, Gio.BusNameOwnerFlags.NONE, None, None
        )
        self.watcher_subscription = self.bus.signal_subscribe(
            "org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged",
            "/org/freedesktop/DBus", WATCHER, Gio.DBusSignalFlags.NONE,
            self.on_watcher_changed,
        )
        self.register_with_watcher()
        GLib.timeout_add_seconds(1, self.poll)

    def register_with_watcher(self):
        try:
            self.bus.call_sync(
                WATCHER, "/StatusNotifierWatcher", WATCHER,
                "RegisterStatusNotifierItem", GLib.Variant("(s)", (self.name,)),
                None, Gio.DBusCallFlags.NONE, 2000, None,
            )
        except GLib.Error:
            pass  # Retry when the tray host appears.

    def on_watcher_changed(self, _bus, _sender, _path, _interface, _signal, parameters):
        _name, _old_owner, new_owner = parameters.unpack()
        if new_owner:
            self.register_with_watcher()

    def on_method(self, _bus, _sender, _path, _interface, method, _parameters, invocation):
        if method in ("Activate", "ContextMenu"):
            self.launch()
        elif method == "SecondaryActivate":
            self.launch("--toggle")
        invocation.return_value(GLib.Variant("()", ()))

    def on_property(self, _bus, _sender, _path, _interface, name):
        strings = {
            "Category": "ApplicationStatus", "Id": "fanqie", "Title": self.title,
            "Status": "Active", "IconName": "alarm-symbolic", "IconThemePath": "",
            "AttentionIconName": "", "OverlayIconName": "",
        }
        if name in strings:
            return GLib.Variant("s", strings[name])
        if name == "WindowId":
            return GLib.Variant("i", 0)
        if name in ("IconPixmap", "AttentionIconPixmap", "OverlayIconPixmap"):
            return GLib.Variant("a(iiay)", [])
        if name == "ToolTip":
            return GLib.Variant("(sa(iiay)ss)", ("alarm-symbolic", [], "Fanqie", self.title))
        if name == "ItemIsMenu":
            return GLib.Variant("b", False)
        if name == "Menu":
            return GLib.Variant("o", "/")
        return None

    def launch(self, *args):
        command = [str(Path.home() / ".local/bin/fanqie"), *args]
        try:
            Gio.Subprocess.new(command, Gio.SubprocessFlags.NONE)
        except GLib.Error:
            pass

    def poll(self):
        if not Path(f"/proc/{self.parent_pid}").exists():
            self.loop.quit()
            return False
        try:
            status = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
            if status.get("pid") != self.parent_pid or not status.get("active"):
                status = {}
        except (OSError, ValueError):
            status = {}
        title = f"{status['task']} · {status['time']}" if status else "Fanqie"
        if title != self.title:
            self.title = title
            for signal in ("NewTitle", "NewToolTip"):
                self.bus.emit_signal(None, OBJECT_PATH, INTERFACE, signal, GLib.Variant("()", ()))
        return True

    def run(self):
        self.loop.run()
        self.bus.signal_unsubscribe(self.watcher_subscription)
        Gio.bus_unown_name(self.owner)
        self.bus.unregister_object(self.registration)


if __name__ == "__main__":
    Tray(int(sys.argv[1])).run()
