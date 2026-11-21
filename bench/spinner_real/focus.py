#!/usr/bin/env python3
"""Gives the headless GNOME Shell a keyboard and focuses its only window. Usage: focus.py <shell-pid>

A headless shell has no input devices. GPUI marks a Wayland window active only
when it gets keyboard focus, and throttles inactive windows to 30 fps. A Mutter
RemoteDesktop session adds a virtual keyboard while it runs, so this keeps the
session open until killed. Alt+Tab focuses the window without pointer moves,
which pointer acceleration makes land unpredictably.
"""
import sys
import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

ALT_L, TAB = 0xFFE9, 0xFF09
pid = sys.argv[1]
with open(f"/proc/{pid}/environ", "rb") as file:
    environment = dict(
        entry.split(b"=", 1) for entry in file.read().split(b"\0") if b"=" in entry
    )
bus = Gio.DBusConnection.new_for_address_sync(
    environment[b"DBUS_SESSION_BUS_ADDRESS"].decode(),
    Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
    | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
    None,
    None,
)


def call(path, interface, method, parameters=None):
    return bus.call_sync(
        "org.gnome.Mutter.RemoteDesktop", path, interface, method, parameters,
        None, Gio.DBusCallFlags.NONE, -1, None,
    )


(session,) = call("/org/gnome/Mutter/RemoteDesktop", "org.gnome.Mutter.RemoteDesktop",
                  "CreateSession").unpack()
interface = "org.gnome.Mutter.RemoteDesktop.Session"
call(session, interface, "Start")
for keysym, pressed in [(ALT_L, True), (TAB, True), (TAB, False), (ALT_L, False)]:
    call(session, interface, "NotifyKeyboardKeysym", GLib.Variant("(ub)", (keysym, pressed)))
    GLib.usleep(100_000)
print("focused", flush=True)
GLib.MainLoop().run()
