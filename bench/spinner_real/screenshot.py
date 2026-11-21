#!/usr/bin/env python3
"""Saves a screenshot of the headless GNOME Shell. Usage: screenshot.py <shell-pid> <png>

GNOME Shell only serves screenshots to allowlisted bus names, so this owns
org.freedesktop.impl.portal.desktop.gnome on the shell's private session bus first.
"""
import sys
import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

pid, path = sys.argv[1], sys.argv[2]
with open(f"/proc/{pid}/environ", "rb") as file:
    environment = dict(
        entry.split(b"=", 1) for entry in file.read().split(b"\0") if b"=" in entry
    )
address = environment[b"DBUS_SESSION_BUS_ADDRESS"].decode()
bus = Gio.DBusConnection.new_for_address_sync(
    address,
    Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
    | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
    None,
    None,
)
bus.call_sync(
    "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
    "RequestName", GLib.Variant("(su)", ("org.freedesktop.impl.portal.desktop.gnome", 4)),
    None, Gio.DBusCallFlags.NONE, -1, None,
)
# The shell learns about the new owner asynchronously.
GLib.usleep(500_000)
result = bus.call_sync(
    "org.gnome.Shell.Screenshot", "/org/gnome/Shell/Screenshot",
    "org.gnome.Shell.Screenshot", "Screenshot",
    GLib.Variant("(bbs)", (False, False, path)),
    None, Gio.DBusCallFlags.NONE, -1, None,
)
print(result.unpack())
