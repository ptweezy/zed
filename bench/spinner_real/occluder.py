#!/usr/bin/env python3
"""Covers the screen with a fullscreen GTK window so the window below it is hidden."""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

Gtk.init()
window = Gtk.Window(title="Occluder")
window.fullscreen()
window.present()
GLib.MainLoop().run()
