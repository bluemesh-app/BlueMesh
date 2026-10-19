#!/usr/bin/env python3
"""BitChat Desktop — a GTK4/libadwaita front end for the BitChat Bluetooth mesh.

The radio and protocol live in `bitchat-desktopd` (a fork of bitmancer, the
Rust BitChat client), which this window drives over line-delimited JSON on
stdin/stdout. No terminal, pty or screen-scraping is involved.
"""
import json
import os
import shutil
import sys
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

# libadwaita >= 1.4 gets the native adaptive widgets; older releases (Debian
# Bookworm ships 1.2) get small stand-ins with the same method names.
MODERN = (Adw.get_major_version(), Adw.get_minor_version()) >= (1, 4) and not os.environ.get("BITCHAT_DESKTOP_FORCE_COMPAT")


class ToolbarViewCompat(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._content = None

    def add_top_bar(self, w):
        if self._content is not None:
            self.insert_child_after(w, self._last_bar) if getattr(self, "_last_bar", None) else self.prepend(w)
        else:
            self.append(w)
        self._last_bar = w

    def set_content(self, w):
        w.set_vexpand(True)
        self._content = w
        self.append(w)


class PageCompat:
    def __init__(self, title="", child=None):
        self.child = child

    def set_title(self, _t):
        pass


class SplitCompat(Gtk.Box):
    def __init__(self, **_kw):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)

    def set_sidebar(self, page):
        page.child.set_size_request(270, -1)
        self.append(page.child)
        self.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

    def set_content(self, page):
        page.child.set_hexpand(True)
        self.append(page.child)

    def set_show_content(self, _v):
        pass


class BannerCompat(Gtk.Revealer):
    __gsignals__ = {"button-clicked": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, title="", revealed=False):
        super().__init__(reveal_child=revealed)
        box = Gtk.Box(spacing=12, margin_start=12, margin_end=12, margin_top=6, margin_bottom=6)
        self._label = Gtk.Label(label=title, hexpand=True, xalign=0, wrap=True)
        self._btn = Gtk.Button(label="Reconnect")
        self._btn.connect("clicked", lambda *_: self.emit("button-clicked"))
        box.append(self._label)
        box.append(self._btn)
        frame = Gtk.Box(css_classes=["banner-compat"])
        box.set_hexpand(True)
        frame.append(box)
        self.set_child(frame)

    def set_title(self, t):
        self._label.set_label(t)

    def set_button_label(self, t):
        self._btn.set_label(t)

    def set_revealed(self, v):
        self.set_reveal_child(v)


ToolbarView = Adw.ToolbarView if MODERN else ToolbarViewCompat
NavigationPage = Adw.NavigationPage if MODERN else PageCompat
SplitView = Adw.NavigationSplitView if MODERN else SplitCompat
Banner = Adw.Banner if MODERN else BannerCompat


def alert(parent, heading, body):
    if MODERN:
        return Adw.AlertDialog(heading=heading, body=body)
    return Adw.MessageDialog(transient_for=parent, modal=True, heading=heading, body=body)


def present_dialog(dlg, parent):
    if MODERN:
        dlg.present(parent)
    else:
        dlg.present()

APP_ID = "com.dyl.BitchatDesktop"
APP_NAME = "BitChat"
VERSION = "0.1.0"
PUBLIC = "public"

CSS = """
.bubble { padding: 7px 11px; border-radius: 14px; }
.bubble.theirs { background-color: alpha(currentColor, 0.08); }
.bubble.mine { background-color: @accent_bg_color; color: @accent_fg_color; }
.bubble.mine .meta { color: alpha(@accent_fg_color, 0.75); }
.bubble.failed { background-color: @error_bg_color; color: @error_fg_color; }
.sender { font-weight: bold; font-size: 0.9em; }
.meta { font-size: 0.78em; opacity: 0.7; }
.system-line { font-size: 0.85em; opacity: 0.65; }
.banner-compat { background-color: alpha(@accent_bg_color, 0.25); }
.status-dot { min-width: 10px; min-height: 10px; border-radius: 5px; }
.status-dot.on { background-color: @success_color; }
.status-dot.wait { background-color: @warning_color; }
.status-dot.off { background-color: alpha(currentColor, 0.3); }
.status-dot.err { background-color: @error_color; }
.unread { background-color: @accent_bg_color; color: @accent_fg_color; border-radius: 9px;
          padding: 0 6px; font-size: 0.8em; font-weight: bold; min-width: 10px; }
.composer { padding: 8px; }
.empty-hint { opacity: 0.6; }
"""

SENDER_COLOURS = ["#3584e4", "#2ec27e", "#e66100", "#9141ac", "#c64600",
                  "#1c71d8", "#26a269", "#a51d2d", "#865e3c", "#c061cb"]


def colour_for(name):
    return SENDER_COLOURS[sum(map(ord, name or "?")) % len(SENDER_COLOURS)]


def hhmm(ts):
    try:
        lt = time.localtime(int(ts))
    except Exception:
        lt = time.localtime()
    today = time.localtime()
    if lt.tm_yday != today.tm_yday or lt.tm_year != today.tm_year:
        return time.strftime("%d %b %H:%M", lt)
    return time.strftime("%H:%M", lt)


def find_backend():
    here = os.path.dirname(os.path.realpath(__file__))
    for cand in (os.environ.get("BITCHAT_DESKTOPD"),
                 os.path.join(here, "bitchat-desktopd"),
                 os.path.expanduser("~/.local/bin/bitchat-desktopd"),
                 shutil.which("bitchat-desktopd"),
                 os.path.join(here, "..", "backend", "target", "release", "bitchat-desktopd")):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


class Conversation:
    """One chat: the public mesh room, a location channel, or a DM."""

    def __init__(self, key, title, kind):
        self.key = key            # "public", "#geohash", or "dm:<peer_id>"
        self.title = title
        self.kind = kind          # public | channel | dm
        self.unread = 0
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                           margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        self.empty = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, vexpand=True,
                               valign=Gtk.Align.CENTER, css_classes=["empty-hint"])
        self.box.append(self.empty)
        self.count = 0
        self.last_sender = None
        self.unreceipted = []     # DM message ids received but not yet reported read
        self.people = []          # for channels


class BitchatWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME, default_width=980, default_height=680,
                         width_request=360, height_request=420)
        self.app = app
        self.proc = None
        self.stdin = None
        self.reader = None
        self.my_nick = ""
        self.my_peer_id = ""
        self.nick_set = True
        self.conn_state = "starting"
        self.links = 0
        self.peers = {}               # peer_id -> dict
        self.convs = {}               # key -> Conversation
        self.current = None
        self.pending = {}             # req -> widgets for own messages awaiting "sent"
        self.by_msgid = {}            # message_id -> status label
        self.req_seq = 0
        self.stopping = False
        self.sidebar_rows = {}        # key -> (row, unread label, dot)
        self._build()
        self._conv(PUBLIC, "Public", "public")
        self.select_conv(PUBLIC)
        self.start_backend()

    # ---------- UI construction ----------
    def _build(self):
        self.toasts = Adw.ToastOverlay()
        self.split = SplitView(min_sidebar_width=250, max_sidebar_width=320)
        self.toasts.set_child(self.split)
        self.set_content(self.toasts)

        # Sidebar
        sb_view = ToolbarView()
        sb_header = Adw.HeaderBar()
        if not MODERN:
            sb_header.set_show_end_title_buttons(False)
        sb_header.set_title_widget(Adw.WindowTitle(title=APP_NAME, subtitle="Bluetooth mesh chat"))
        menu = Gio.Menu()
        menu.append("Change nickname…", "win.nick")
        menu.append("Join location channel…", "win.join")
        menu.append("Reconnect", "win.reconnect")
        menu.append("About BitChat Desktop", "win.about")
        mb = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Menu")
        sb_header.pack_end(mb)
        sb_view.add_top_bar(sb_header)

        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        # connection status card
        status = Gtk.Box(spacing=10, margin_start=12, margin_end=12, margin_top=6, margin_bottom=6)
        self.status_dot = Gtk.Box(css_classes=["status-dot", "wait"], valign=Gtk.Align.CENTER)
        status.append(self.status_dot)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        self.status_title = Gtk.Label(label="Starting…", xalign=0, css_classes=["heading"])
        self.status_sub = Gtk.Label(label="", xalign=0, css_classes=["caption", "dim-label"],
                                    ellipsize=Pango.EllipsizeMode.END)
        col.append(self.status_title)
        col.append(self.status_sub)
        status.append(col)
        self.reconnect_btn = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Reconnect",
                                        valign=Gtk.Align.CENTER, css_classes=["flat", "circular"])
        self.reconnect_btn.connect("clicked", lambda *_: self.restart_backend())
        status.append(self.reconnect_btn)
        side.append(status)
        self.me_label = Gtk.Label(xalign=0, margin_start=12, margin_end=12, margin_bottom=6,
                                  css_classes=["caption", "dim-label"], ellipsize=Pango.EllipsizeMode.END)
        side.append(self.me_label)
        side.append(Gtk.Separator())

        scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6, margin_bottom=6)
        self.rooms_list = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.rooms_list.connect("row-activated", self._row_activated)
        inner.append(self.rooms_list)

        ch_head = Gtk.Box(margin_start=14, margin_end=8, margin_top=8)
        ch_head.append(Gtk.Label(label="Location channels", xalign=0, hexpand=True,
                                 css_classes=["caption-heading", "dim-label"]))
        addb = Gtk.Button(icon_name="list-add-symbolic", css_classes=["flat", "circular"],
                          tooltip_text="Join a location channel")
        addb.connect("clicked", lambda *_: self.ask_join())
        ch_head.append(addb)
        inner.append(ch_head)
        self.channels_list = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.channels_list.connect("row-activated", self._row_activated)
        self.channels_list.set_placeholder(Gtk.Label(label="None joined", css_classes=["dim-label", "caption"],
                                                     margin_top=4, margin_bottom=4))
        inner.append(self.channels_list)

        ppl_head = Gtk.Box(margin_start=14, margin_end=8, margin_top=8)
        self.people_heading = Gtk.Label(label="People nearby", xalign=0, hexpand=True,
                                        css_classes=["caption-heading", "dim-label"])
        ppl_head.append(self.people_heading)
        inner.append(ppl_head)
        self.people_list = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.people_list.connect("row-activated", self._row_activated)
        self.people_list.set_placeholder(Gtk.Label(label="Nobody in range yet", css_classes=["dim-label", "caption"],
                                                   margin_top=4, margin_bottom=4))
        inner.append(self.people_list)
        scroller.set_child(inner)
        side.append(scroller)
        sb_view.set_content(side)
        self.split.set_sidebar(NavigationPage(title=APP_NAME, child=sb_view))

        # Content
        self.content_view = ToolbarView()
        ch = Adw.HeaderBar()
        if not MODERN:
            ch.set_show_start_title_buttons(False)
        self.conv_title = Adw.WindowTitle(title="Public", subtitle="")
        ch.set_title_widget(self.conv_title)
        self.leave_btn = Gtk.Button(icon_name="application-exit-symbolic", tooltip_text="Leave channel",
                                    visible=False)
        self.leave_btn.connect("clicked", lambda *_: self.leave_current())
        ch.pack_end(self.leave_btn)
        self.content_view.add_top_bar(ch)
        self.banner = Banner(title="", revealed=False)
        self.content_view.add_top_bar(self.banner)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.msg_scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.msg_clamp = Adw.Clamp(maximum_size=820, tightening_threshold=600)
        self.msg_scroll.set_child(self.msg_clamp)
        body.append(self.msg_scroll)

        composer = Gtk.Box(spacing=8, css_classes=["composer"])
        self.entry = Gtk.Entry(hexpand=True, placeholder_text="Message everyone nearby…")
        self.entry.connect("activate", lambda *_: self.send_current())
        self.send_btn = Gtk.Button(icon_name="paper-plane-symbolic", tooltip_text="Send (Enter)",
                                   css_classes=["suggested-action", "circular"])
        if not Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).has_icon("paper-plane-symbolic"):
            self.send_btn.set_icon_name("mail-send-symbolic")
        self.send_btn.connect("clicked", lambda *_: self.send_current())
        composer.append(self.entry)
        composer.append(self.send_btn)
        clampc = Adw.Clamp(maximum_size=820, tightening_threshold=600, child=composer)
        body.append(clampc)
        self.content_view.set_content(body)
        self.content_page = NavigationPage(title="Public", child=self.content_view)
        self.split.set_content(self.content_page)

        # public room row
        self._add_sidebar_row(self.rooms_list, PUBLIC, "Public", "Everyone nearby", "user-available-symbolic")

        for name, cb in (("nick", lambda *_: self.ask_nick()), ("join", lambda *_: self.ask_join()),
                         ("reconnect", lambda *_: self.restart_backend()), ("about", lambda *_: self.about())):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", cb)
            self.add_action(a)
        self.connect("close-request", self._on_close)
        self.connect("notify::is-active", lambda *_: self._maybe_mark_read())

        # breakpoint for narrow windows
        if MODERN:
            bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 600sp"))
            bp.add_setter(self.split, "collapsed", True)
            self.add_breakpoint(bp)

    def _add_sidebar_row(self, listbox, key, title, subtitle, icon=None, dot=None):
        row = Adw.ActionRow(title=GLib.markup_escape_text(title), activatable=True)
        if subtitle:
            row.set_subtitle(GLib.markup_escape_text(subtitle))
        row.key = key
        dotw = None
        if dot is not None:
            dotw = Gtk.Box(css_classes=["status-dot", dot], valign=Gtk.Align.CENTER)
            row.add_prefix(dotw)
        elif icon:
            row.add_prefix(Gtk.Image(icon_name=icon))
        unread = Gtk.Label(css_classes=["unread"], valign=Gtk.Align.CENTER, visible=False)
        row.add_suffix(unread)
        listbox.append(row)
        self.sidebar_rows[key] = (row, unread, dotw)
        return row

    def _row_activated(self, listbox, row):
        key = getattr(row, "key", None)
        if key:
            if key.startswith("dm:") and key not in self.convs:
                pid = key[3:]
                self._conv(key, self.peers.get(pid, {}).get("nickname", pid[:8]), "dm")
            self.select_conv(key)
            self.split.set_show_content(True)

    # ---------- conversations ----------
    def _conv(self, key, title, kind):
        if key in self.convs:
            return self.convs[key]
        c = Conversation(key, title, kind)
        if kind == "public":
            c.empty.set_label("No messages yet.\nPublic messages reach every BitChat user in Bluetooth range — "
                              "including strangers — and are not encrypted.")
        elif kind == "channel":
            c.empty.set_label(f"Location channel {key}.\nThis room is carried over the internet (Nostr relays), "
                              "not Bluetooth. Anyone in the world can read it.")
        else:
            c.empty.set_label(f"Private conversation with {title}.\nEnd-to-end encrypted (Noise). "
                              "They must be in Bluetooth range.")
        self.convs[key] = c
        return c

    def select_conv(self, key):
        c = self.convs[key]
        self.current = key
        if self.msg_clamp.get_child() is not c.box:
            self.msg_clamp.set_child(c.box)
        self.conv_title.set_title(c.title)
        self.content_page.set_title(c.title)
        self.leave_btn.set_visible(c.kind == "channel")
        if c.kind == "public":
            n = len(self.peers)
            self.conv_title.set_subtitle(f"Bluetooth mesh · {n} {'person' if n == 1 else 'people'} nearby")
            self.entry.set_placeholder_text("Message everyone nearby…")
        elif c.kind == "channel":
            self.conv_title.set_subtitle(f"Location channel over the internet · {len(c.people)} here")
            self.entry.set_placeholder_text(f"Message {key}…")
        else:
            p = self.peers.get(key[3:])
            self.conv_title.set_subtitle(self._dm_subtitle(p))
            self.entry.set_placeholder_text(f"Private message to {c.title}…")
        c.unread = 0
        self._update_unread(key)
        for lb in (self.rooms_list, self.channels_list, self.people_list):
            lb.unselect_all()
        if key in self.sidebar_rows:
            row = self.sidebar_rows[key][0]
            row.get_parent().select_row(row) if row.get_parent() else None
        self._scroll_bottom()
        self._maybe_mark_read()
        self.entry.grab_focus()

    def _dm_subtitle(self, p):
        if not p:
            return "Not in range"
        bits = ["Online" if p.get("online") else "Away"]
        bits.append("🔒 Encrypted session up" if p.get("encrypted") else "Encrypts on first message")
        return " · ".join(bits)

    def _update_unread(self, key):
        if key not in self.sidebar_rows:
            return
        _, lab, _ = self.sidebar_rows[key]
        n = self.convs[key].unread if key in self.convs else 0
        lab.set_label(str(n))
        lab.set_visible(n > 0)

    def _scroll_bottom(self):
        def go():
            adj = self.msg_scroll.get_vadjustment()
            adj.set_value(adj.get_upper())
            return False
        GLib.timeout_add(60, go)

    def add_system(self, key, text):
        c = self.convs.get(key)
        if not c:
            return
        if c.count == 0 and c.empty.get_parent():
            c.box.remove(c.empty)
        lab = Gtk.Label(label=text, wrap=True, justify=Gtk.Justification.CENTER,
                        css_classes=["system-line"], margin_top=4, margin_bottom=4)
        c.box.append(lab)
        c.count += 1
        c.last_sender = None
        if key == self.current:
            self._scroll_bottom()

    def add_message(self, key, sender, text, ts, mine=False, status=None):
        c = self.convs[key]
        if c.count == 0 and c.empty.get_parent():
            c.box.remove(c.empty)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                        halign=Gtk.Align.END if mine else Gtk.Align.START)
        if not mine and c.kind != "dm" and c.last_sender != sender:
            name = Gtk.Label(xalign=0, css_classes=["sender"], margin_start=6)
            name.set_markup(f'<span foreground="{colour_for(sender)}">{GLib.markup_escape_text(sender)}</span>')
            outer.append(name)
        bubble = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                         css_classes=["bubble", "mine" if mine else "theirs"])
        body = Gtk.Label(label=text, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, xalign=0,
                         selectable=True, max_width_chars=60)
        bubble.append(body)
        meta = Gtk.Box(spacing=6, halign=Gtk.Align.END)
        meta.append(Gtk.Label(label=hhmm(ts), css_classes=["meta"]))
        st = None
        if mine:
            st = Gtk.Label(label=status or "", css_classes=["meta"])
            meta.append(st)
        bubble.append(meta)
        outer.append(bubble)
        c.box.append(outer)
        c.count += 1
        c.last_sender = None if mine else sender
        if key == self.current:
            self._scroll_bottom()
        return bubble, st

    def bump_unread(self, key):
        if key == self.current and self.is_active():
            return
        if key != self.current:
            self.convs[key].unread += 1
            self._update_unread(key)

    # ---------- backend ----------
    def start_backend(self):
        path = find_backend()
        if not path:
            self.set_status("err", "Backend missing", "bitchat-desktopd not found")
            return
        self.stopping = False
        try:
            self.proc = Gio.Subprocess.new([path], Gio.SubprocessFlags.STDIN_PIPE | Gio.SubprocessFlags.STDOUT_PIPE)
        except GLib.Error as e:
            self.set_status("err", "Couldn't start", e.message)
            return
        self.stdin = self.proc.get_stdin_pipe()
        self.reader = Gio.DataInputStream.new(self.proc.get_stdout_pipe())
        self.set_status("wait", "Starting…", "")
        self._read_next(self.proc, self.reader)
        self.proc.wait_async(None, self._on_backend_exit, self.proc)

    def _on_backend_exit(self, proc, res, which):
        try:
            proc.wait_finish(res)
        except GLib.Error:
            pass
        if which is self.proc and not self.stopping:
            self.set_status("err", "Disconnected", "The Bluetooth service stopped. Press reconnect.")

    def restart_backend(self):
        self.toast("Reconnecting…")
        old = self.proc
        self.stopping = True
        self.proc = None
        if old:
            try:
                self._write_to(old.get_stdin_pipe(), {"cmd": "quit"})
            except Exception:
                pass
            GLib.timeout_add(1500, lambda: (old.force_exit() if old.get_identifier() else None) and False)
        self.peers = {}
        self._refresh_people()
        GLib.timeout_add(1800, lambda: (self.start_backend(), False)[1])

    def _write_to(self, stream, obj):
        data = (json.dumps(obj) + "\n").encode()
        stream.write_all(data, None)
        stream.flush(None)

    def send_cmd(self, obj):
        if not self.proc or not self.stdin:
            self.toast("Not connected to the Bluetooth service")
            return False
        try:
            self._write_to(self.stdin, obj)
            return True
        except GLib.Error as e:
            self.toast(f"Couldn't talk to the backend: {e.message}")
            return False

    def _read_next(self, proc, reader):
        reader.read_line_async(GLib.PRIORITY_DEFAULT, None, self._on_line, proc)

    def _on_line(self, reader, res, proc):
        try:
            line, _ = reader.read_line_finish_utf8(res)
        except GLib.Error:
            return
        if line is None:
            return
        if proc is self.proc:
            try:
                self.handle_event(json.loads(line))
            except Exception as e:  # never let one bad line kill the reader
                print("event error:", e, line, file=sys.stderr)
        self._read_next(proc, reader)

    # ---------- events from backend ----------
    def handle_event(self, ev):
        kind = ev.get("ev")
        if kind == "ready":
            self.my_nick = ev.get("nickname", "")
            self.my_peer_id = ev.get("peer_id", "")
            self.nick_set = ev.get("nickname_set", True)
            self._update_me()
            if not self.nick_set:
                GLib.idle_add(lambda: (self.ask_nick(first=True), False)[1])
        elif kind == "status":
            st = ev.get("state")
            self.links = ev.get("links", 0)
            text = (ev.get("text") or "").replace("» ", "").strip()
            if st == "connected":
                self.set_status("on", "Connected", f"{self.links} Bluetooth link{'s' if self.links != 1 else ''}", text)
            elif st == "reconnecting":
                self.set_status("wait", "Reconnecting…", text)
            elif st == "error":
                self.set_status("err", "Bluetooth problem", text)
            else:
                self.set_status("wait", "Looking for peers…", text)
        elif kind == "peers":
            self.peers = {p["peer_id"]: p for p in ev.get("peers", [])}
            self._refresh_people()
        elif kind == "peer_joined":
            self.add_system(PUBLIC, f"{ev.get('nickname')} is nearby")
        elif kind == "peer_left":
            self.add_system(PUBLIC, f"{ev.get('nickname')} left")
        elif kind == "peer_renamed":
            pass
        elif kind == "public":
            sender = ev.get("sender", "?")
            self.add_message(PUBLIC, sender, ev.get("content", ""), ev.get("ts", time.time()))
            self.bump_unread(PUBLIC)
            self.notify(f"{sender} (Public)", ev.get("content", ""), PUBLIC)
        elif kind == "dm":
            pid = ev.get("peer_id")
            key = f"dm:{pid}"
            sender = ev.get("sender", pid[:8])
            c = self._conv(key, sender, "dm")
            self._ensure_dm_row(pid, sender)
            self.add_message(key, sender, ev.get("content", ""), ev.get("ts", time.time()))
            c.unreceipted.append(ev.get("message_id"))
            self.bump_unread(key)
            self.notify(f"{sender} (private)", ev.get("content", ""), key)
            self._maybe_mark_read()
        elif kind == "sent":
            self._on_sent(ev)
        elif kind == "delivery":
            lab = self.by_msgid.get(ev.get("message_id"))
            if lab:
                lab.set_label("Read ✓✓" if ev.get("status") == "read" else "Delivered ✓")
        elif kind == "session_up":
            pid = ev.get("peer_id")
            key = f"dm:{pid}"
            if key in self.convs:
                self.add_system(key, f"🔒 Encrypted session with {ev.get('nickname')} is up\n"
                                     f"Fingerprint {ev.get('fingerprint', '')[:16]}…")
            if pid in self.peers:
                self.peers[pid]["encrypted"] = True
            if self.current == key:
                self.conv_title.set_subtitle(self._dm_subtitle(self.peers.get(pid)))
        elif kind == "nick":
            self.my_nick = ev.get("nickname", self.my_nick)
            self._update_me()
            self.toast(f"You are now “{self.my_nick}”")
        elif kind == "channel_joined":
            ch = ev.get("channel")
            self._conv(ch, ch, "channel")
            if ch not in self.sidebar_rows:
                self._add_sidebar_row(self.channels_list, ch, ch, "Location channel · internet", "mark-location-symbolic")
            if not ev.get("already"):
                self.add_system(ch, f"Joined {ch} via {ev.get('relays')} relays. Loading recent history…")
            self.select_conv(ch)
        elif kind == "channel_left":
            ch = ev.get("channel")
            if ch in self.sidebar_rows:
                row = self.sidebar_rows.pop(ch)[0]
                self.channels_list.remove(row)
            self.convs.pop(ch, None)
            if self.current == ch:
                self.select_conv(PUBLIC)
        elif kind == "channel_msg":
            ch = ev.get("channel")
            if ch in self.convs:
                s = ev.get("sender", "anon") + ("*" if ev.get("teleported") else "")
                self.add_message(ch, s, ev.get("content", ""), ev.get("ts", time.time()))
                self.bump_unread(ch)
        elif kind == "channel_live":
            ch = ev.get("channel")
            if ch in self.convs:
                self.add_system(ch, "─── live ───")
        elif kind == "channel_people":
            ch = ev.get("channel")
            if ch in self.convs:
                self.convs[ch].people = ev.get("people", [])
                if self.current == ch:
                    self.select_conv(ch)
        elif kind == "peripheral":
            self.visible = bool(ev.get("ok"))
            self._update_me()
            if not self.visible:
                self.toast(ev.get("text", ""))
        elif kind == "notice":
            self.toast(ev.get("text", ""))
        elif kind == "error":
            self.toast(ev.get("text", "Error"))
        elif kind == "log":
            print("backend:", ev.get("text"), file=sys.stderr)

    def _on_sent(self, ev):
        req = ev.get("req")
        w = self.pending.pop(req, None)
        if not w:
            return
        bubble, st = w
        if not ev.get("ok"):
            bubble.add_css_class("failed")
            st.set_label("Not sent — " + ev.get("error", "error"))
            return
        k = ev.get("kind")
        if k == "public":
            n = ev.get("links", 0)
            st.set_label(f"Sent ✓ ({n} link{'s' if n != 1 else ''})")
        elif k == "channel":
            st.set_label("Sent ✓")
        else:
            st.set_label("Securing…" if ev.get("pending_session") else "Sent")
            for mid in ev.get("message_ids", []):
                self.by_msgid[mid] = st

    def _ensure_dm_row(self, pid, nick):
        key = f"dm:{pid}"
        if key not in self.sidebar_rows:
            self._refresh_people()

    def _refresh_people(self):
        # rebuild the people list in-place
        keep = {}
        for key in list(self.sidebar_rows):
            if key.startswith("dm:"):
                row = self.sidebar_rows.pop(key)[0]
                keep[key] = row
                self.people_list.remove(row)
        shown = set()
        online = 0
        for pid, p in sorted(self.peers.items(), key=lambda kv: (not kv[1].get("online"), kv[1]["nickname"].lower())):
            key = f"dm:{pid}"
            sub = ("Online" if p.get("online") else "Away") + (" · 🔒" if p.get("encrypted") else "")
            online += 1 if p.get("online") else 0
            self._add_sidebar_row(self.people_list, key, p["nickname"], sub,
                                  dot="on" if p.get("online") else "off")
            shown.add(key)
            if key in self.convs:
                self.convs[key].title = p["nickname"]
                self._update_unread(key)
        # DMs with people who went out of range stay reachable in the list
        for key, conv in self.convs.items():
            if conv.kind == "dm" and key not in shown:
                self._add_sidebar_row(self.people_list, key, conv.title, "Out of range", dot="off")
                self._update_unread(key)
        self.people_heading.set_label(f"People nearby ({len(self.peers)})" if self.peers else "People nearby")
        if self.current in self.sidebar_rows:
            row = self.sidebar_rows[self.current][0]
            if row.get_parent() is self.people_list:
                self.people_list.select_row(row)
        if self.current == PUBLIC or (self.current or "").startswith("dm:"):
            c = self.convs.get(self.current)
            if c and c.kind == "public":
                n = len(self.peers)
                self.conv_title.set_subtitle(f"Bluetooth mesh · {n} {'person' if n == 1 else 'people'} nearby")
            elif c:
                self.conv_title.set_subtitle(self._dm_subtitle(self.peers.get(self.current[3:])))

    def _update_me(self):
        vis = "  ·  visible" if getattr(self, "visible", False) else ""
        self.me_label.set_label(f"You: {self.my_nick}  ·  ID {self.my_peer_id[:8]}{vis}")
        self.me_label.set_tooltip_text("Other BitChat devices can connect to this computer" if vis else
                                       "Only outgoing connections (to phones) are possible")

    def set_status(self, dot, title, sub, detail=None):
        self.conn_state = dot
        for cls in ("on", "wait", "off", "err"):
            self.status_dot.remove_css_class(cls)
        self.status_dot.add_css_class(dot)
        self.status_title.set_label(title)
        self.status_sub.set_label(sub)
        self.status_sub.set_tooltip_text(detail or sub)
        if dot == "err":
            self.banner.set_title(f"{title}: {sub}")
            self.banner.set_button_label("Reconnect")
            try:
                self.banner.disconnect_by_func(self._banner_clicked)
            except TypeError:
                pass
            self.banner.connect("button-clicked", self._banner_clicked)
            self.banner.set_revealed(True)
        else:
            self.banner.set_revealed(False)

    def _banner_clicked(self, *_):
        self.restart_backend()

    # ---------- actions ----------
    def send_current(self):
        text = self.entry.get_text().strip()
        if not text or not self.current:
            return
        c = self.convs[self.current]
        self.req_seq += 1
        req = self.req_seq
        if c.kind == "public":
            cmd = {"cmd": "send", "text": text, "req": req}
        elif c.kind == "channel":
            cmd = {"cmd": "channel_send", "channel": c.key, "text": text, "req": req}
        else:
            cmd = {"cmd": "dm", "peer_id": c.key[3:], "text": text, "req": req}
        bubble, st = self.add_message(c.key, self.my_nick, text, time.time(), mine=True, status="Sending…")
        self.pending[req] = (bubble, st)
        if self.send_cmd(cmd):
            self.entry.set_text("")
        else:
            bubble.add_css_class("failed")
            st.set_label("Not sent")
            self.pending.pop(req, None)

    def _maybe_mark_read(self):
        if not self.is_active() or not self.current or not self.current.startswith("dm:"):
            return
        c = self.convs.get(self.current)
        if c and c.unreceipted:
            ids = [i for i in c.unreceipted if i]
            c.unreceipted = []
            if ids:
                self.send_cmd({"cmd": "read", "peer_id": self.current[3:], "ids": ids})

    def leave_current(self):
        c = self.convs.get(self.current)
        if c and c.kind == "channel":
            self.send_cmd({"cmd": "leave", "channel": c.key})

    def ask_nick(self, first=False):
        dlg = alert(self, "Choose a nickname" if first else "Change nickname",
                    "This is the name people nearby see. Anyone in Bluetooth range can see it, "
                                   "so you may not want to use your full name.")
        entry = Gtk.Entry(text=self.my_nick if self.my_nick != "anonymous" else "", activates_default=True,
                          placeholder_text="e.g. dyl")
        dlg.set_extra_child(entry)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("ok", "Save")
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("ok")

        def done(d, resp):
            name = entry.get_text().strip()
            if resp == "ok" and name:
                self.send_cmd({"cmd": "nick", "name": name})
        dlg.connect("response", done)
        present_dialog(dlg, self)

    def ask_join(self):
        dlg = alert(self, "Join a location channel",
                    "BitChat channels are named after a place (a geohash, e.g. #r3gx for a city-sized area). "
                                   "Unlike the Bluetooth mesh, they travel over the internet via Nostr relays and anyone can read them.")
        entry = Gtk.Entry(placeholder_text="#geohash", activates_default=True)
        dlg.set_extra_child(entry)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("ok", "Join")
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dlg.set_default_response("ok")

        def done(d, resp):
            gh = entry.get_text().strip().lstrip("#").lower()
            if resp == "ok" and gh:
                self.send_cmd({"cmd": "join", "channel": gh})
        dlg.connect("response", done)
        present_dialog(dlg, self)

    def about(self):
        about = (Adw.AboutDialog if MODERN else Adw.AboutWindow)(application_name="BitChat Desktop", application_icon="bitchat-desktop",
                                version=VERSION, developer_name="Built for Dyl",
                                comments="Off-grid chat over Bluetooth with nearby BitChat users.\n"
                                         "Backend: bitchat-desktopd, a fork of bitmancer (MIT).",
                                license_type=Gtk.License.MIT_X11,
                                website="https://github.com/permissionlesstech/bitchat")
        if MODERN:
            about.present(self)
        else:
            about.set_transient_for(self)
            about.present()

    def save_screenshot(self, path):
        """Render this window to a PNG without any desktop screenshot permission."""
        w, h = self.get_width(), self.get_height()
        paintable = Gtk.WidgetPaintable(widget=self)
        snap = Gtk.Snapshot()
        paintable.snapshot(snap, w, h)
        node = snap.to_node()
        if node is None:
            print("screenshot: nothing to render", file=sys.stderr)
            return
        tex = self.get_native().get_renderer().render_texture(node, None)
        tex.save_to_png(path)
        print("screenshot saved", path, file=sys.stderr)

    def toast(self, text):
        if text:
            self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=3))

    def notify(self, title, body, key):
        if self.is_active() and self.current == key:
            return
        self.app.desktop_notify(title, body)

    def _on_close(self, *_):
        self.stopping = True
        if self.proc:
            try:
                self._write_to(self.stdin, {"cmd": "quit"})
            except Exception:
                pass
            p = self.proc
            GLib.timeout_add(800, lambda: (p.force_exit() if p.get_identifier() else None) and False)
        return False


class BitchatApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        GLib.set_application_name(APP_NAME)
        self.win = None
        self.notifier = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        provider = Gtk.CssProvider()
        if hasattr(provider, "load_from_string"):
            provider.load_from_string(CSS)
        else:
            try:
                provider.load_from_data(CSS, -1)
            except TypeError:
                provider.load_from_data(CSS.encode())
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        q = Gio.SimpleAction.new("quit", None)
        q.connect("activate", lambda *_: self.win.close() if self.win else self.quit())
        self.add_action(q)
        self.set_accels_for_action("app.quit", ["<Control>q"])
        try:
            self.notifier = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION, Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES, None,
                "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications", None)
        except GLib.Error:
            self.notifier = None

    def do_activate(self):
        if not self.win:
            self.win = BitchatWindow(self)
        self.win.present()
        # Hidden developer hooks, used for automated testing only.
        shot = os.environ.get("BITCHAT_DESKTOP_SCREENSHOT")
        if shot:
            GLib.timeout_add_seconds(int(os.environ.get("BITCHAT_DESKTOP_SCREENSHOT_DELAY", "20")),
                                     lambda: (self.win.save_screenshot(shot), False)[1])
        dmtest = os.environ.get("BITCHAT_DESKTOP_SELFTEST_DM")
        if dmtest:
            nick, _, text = dmtest.partition("|")

            def try_dm():
                pid = next((k for k, p in self.win.peers.items() if p.get("nickname") == nick), None)
                if not pid:
                    return True
                key = f"dm:{pid}"
                row = self.win.sidebar_rows.get(key, (None,))[0]
                if row is not None:
                    self.win._row_activated(self.win.people_list, row)
                self.win.entry.set_text(text)
                self.win.send_current()
                return False
            GLib.timeout_add_seconds(int(os.environ.get("BITCHAT_DESKTOP_SELFTEST_DM_DELAY", "15")), try_dm)
        for spec in filter(None, os.environ.get("BITCHAT_DESKTOP_SHOTS", "").split(";")):
            # "seconds|conversation-nick-or-public|path"
            secs, conv, path = spec.split("|")

            def shot(conv=conv, path=path):
                if conv == "public":
                    self.win.select_conv(PUBLIC)
                else:
                    pid = next((k for k, p in self.win.peers.items() if p.get("nickname") == conv), None)
                    if pid and f"dm:{pid}" in self.win.convs:
                        self.win.select_conv(f"dm:{pid}")
                GLib.timeout_add(700, lambda: (self.win.save_screenshot(path), False)[1])
                return False
            GLib.timeout_add_seconds(int(secs), shot)
        rc = os.environ.get("BITCHAT_DESKTOP_SELFTEST_RECONNECT")
        if rc:
            GLib.timeout_add_seconds(int(rc), lambda: (self.win.restart_backend(), False)[1])
        test = os.environ.get("BITCHAT_DESKTOP_SELFTEST")
        if test:
            def try_send():
                need = os.environ.get("BITCHAT_DESKTOP_SELFTEST_WAITFOR")
                ready = any(p.get("nickname") == need for p in self.win.peers.values()) if need else bool(self.win.peers)
                if self.win.conn_state == "on" and ready:
                    self.win.entry.set_text(test)
                    self.win.send_current()
                    return False
                return True
            GLib.timeout_add_seconds(2, try_send)

    def desktop_notify(self, title, body):
        if not self.notifier:
            return
        hints = {"desktop-entry": GLib.Variant("s", "bitchat-desktop"),
                 "category": GLib.Variant("s", "im.received")}
        try:
            self.notifier.call("Notify", GLib.Variant("(susssasa{sv}i)", (
                "BitChat", 0, "bitchat-desktop", title, body[:300], [], hints, -1)),
                Gio.DBusCallFlags.NONE, -1, None, None, None)
        except Exception as e:
            print("notify failed:", e, file=sys.stderr)


def main():
    return BitchatApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
