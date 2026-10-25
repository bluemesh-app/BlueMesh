#!/usr/bin/env python3
"""BlueMesh — a GTK4/libadwaita Bluetooth mesh chat client (BitChat wire protocol compatible).

The radio and protocol live in `bluemeshd` (a fork of bitmancer, the
Rust mesh client), which this window drives over line-delimited JSON on
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
MODERN = (Adw.get_major_version(), Adw.get_minor_version()) >= (1, 4) and not os.environ.get("BLUEMESH_FORCE_COMPAT")


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

APP_ID = "io.github.bluemesh.BlueMesh"
APP_NAME = "BlueMesh"
VERSION = "0.1.0"
PUBLIC = "public"
# Mirrors bitmancer::mesh::PRIVATE_FILE_MAX_BYTES: Android's 256-fragment
# budget less the worst-case file name, MIME type and encryption overhead.
# 256 * 213 = 54,528 bytes of reassembled packet; the worst case spends 340
# bytes on framing + a 255-byte name + a 16-byte MIME type (69 + 255 + 16),
# leaving 54,528 - 340 = 54,188 for content. The daemon reports the
# authoritative value in its ready event; this fallback keeps the advertised
# limit honest if an older daemon does not.
MAX_FILE_BYTES = 54188
MAX_PREVIEW_BYTES = 1024 * 1024   # received files can be larger than private sends
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
SLAP_TEXT = "* {me} slaps {them} around a bit with a large trout 🐟 *"   # same wording as the phone app's /slap
CHANNEL_LEVELS = [("2", "region (e.g. #r3)"), ("4", "province / state (#r3gx)"), ("5", "city (#r3gx2)"),
                  ("6", "neighbourhood (#r3gx2f)"), ("7", "block (#r3gx2f9)"), ("8", "building (#r3gx2f9t)")]

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
.offline-fav { opacity: 0.55; }
.fav-star.on { color: #e5a50a; }
.status-dot.err { background-color: @error_color; }
.unread { background-color: @accent_bg_color; color: @accent_fg_color; border-radius: 9px;
          padding: 0 6px; font-size: 0.8em; font-weight: bold; min-width: 10px; }
.composer { padding: 8px; }
.empty-hint { opacity: 0.6; }
.file-name { font-weight: bold; }
.file-actions button { padding: 2px 8px; min-height: 24px; }
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


def human_size(n):
    try:
        n = int(n)
    except (TypeError, ValueError):
        return ""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KiB"
    return f"{n / (1024 * 1024):.2f} MiB"


def find_backend():
    here = os.path.dirname(os.path.realpath(__file__))
    for cand in (os.environ.get("BLUEMESHD"),
                 os.path.join(here, "bluemeshd"),
                 os.path.expanduser("~/.local/bin/bluemeshd"),
                 shutil.which("bluemeshd"),
                 os.path.join(here, "..", "backend", "target", "release", "bluemeshd")):
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


def license_text():
    """The shipped LICENSE (MIT + preserved third-party notices), if installed."""
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, "LICENSE"), os.path.join(here, "..", "LICENSE")):
        try:
            with open(cand, encoding="utf-8") as f:
                return f.read()
        except OSError:
            pass
    return None


class BlueMeshWindow(Adw.ApplicationWindow):
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
        self.max_file_bytes = MAX_FILE_BYTES
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
        menu.append("Browse location channels…", "win.browse")
        menu.append("Send file to this contact…", "win.sendfile")
        menu.append("Blocked people…", "win.blocks")
        menu.append("Reconnect", "win.reconnect")
        danger = Gio.Menu()
        danger.append("Emergency wipe…", "win.wipe")
        menu.append_section(None, danger)
        about = Gio.Menu()
        about.append("About BlueMesh", "win.about")
        menu.append_section(None, about)
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
        self.fav_btn = Gtk.Button(icon_name="non-starred-symbolic", tooltip_text="Add to favourites",
                                  visible=False, css_classes=["flat", "fav-star"])
        self.fav_btn.connect("clicked", lambda *_: self.current and self.current.startswith("dm:")
                             and self.toggle_favorite(self.current[3:]))
        ch.pack_end(self.fav_btn)
        dm_menu = Gio.Menu()
        dm_menu.append("Send file…", "win.sendfile")
        dm_menu.append("Slap with a trout", "win.slap-current")
        dm_menu.append("Block…", "win.block-current")
        self.dm_menu_btn = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=dm_menu,
                                          tooltip_text="More", visible=False)
        ch.pack_end(self.dm_menu_btn)
        self.content_view.add_top_bar(ch)
        self.banner = Banner(title="", revealed=False)
        self.content_view.add_top_bar(self.banner)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.msg_scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.msg_clamp = Adw.Clamp(maximum_size=820, tightening_threshold=600)
        self.msg_scroll.set_child(self.msg_clamp)
        body.append(self.msg_scroll)

        composer = Gtk.Box(spacing=8, css_classes=["composer"])
        self.attach_btn = Gtk.Button(icon_name="mail-attachment-symbolic", css_classes=["flat", "circular"],
                                     valign=Gtk.Align.CENTER,
                                     tooltip_text="Send a file to this contact (mutual favourites only, encrypted)")
        self.attach_btn.connect("clicked", lambda *_: self.choose_file())
        composer.append(self.attach_btn)
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
                         ("reconnect", lambda *_: self.restart_backend()), ("about", lambda *_: self.about()),
                         ("sendfile", lambda *_: self.choose_file()),
                         ("browse", lambda *_: self.browse_channels()),
                         ("blocks", lambda *_: self.show_blocks()),
                         ("wipe", lambda *_: self.ask_wipe()),
                         ("block-current", lambda *_: self.current and self.current.startswith("dm:")
                          and self.ask_block(self.current[3:])),
                         ("slap-current", lambda *_: self.current and self.current.startswith("dm:")
                          and self.slap(self.current[3:]))):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", cb)
            self.add_action(a)
        for name, cb in (("block-peer", lambda pid: self.ask_block(pid)), ("slap-peer", lambda pid: self.slap(pid))):
            a = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            a.connect("activate", lambda _a, v, cb=cb: cb(v.get_string()))
            self.add_action(a)
        self.blocks_dialog = None
        self.blocks_box = None
        self.channels_dialog = None
        self.channels_box = None
        self.connect("close-request", self._on_close)
        self.connect("notify::is-active", lambda *_: self._maybe_mark_read())

        # breakpoint for narrow windows
        if MODERN:
            bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 600sp"))
            bp.add_setter(self.split, "collapsed", True)
            self.add_breakpoint(bp)

    def _add_sidebar_row(self, listbox, key, title, subtitle, icon=None, dot=None, star=None):
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
        if star is not None:
            pid, on = star
            row.add_suffix(self._star_button(pid, on))
        listbox.append(row)
        self.sidebar_rows[key] = (row, unread, dotw)
        if key.startswith("dm:"):
            self._attach_peer_menu(row, key[3:])
        return row

    def _attach_peer_menu(self, row, pid):
        """Right-click (or long-press) a person for Slap / Block."""
        m = Gio.Menu()
        m.append("Slap with a trout", f"win.slap-peer('{pid}')")
        m.append("Block…", f"win.block-peer('{pid}')")
        pop = Gtk.PopoverMenu.new_from_model(m)
        pop.set_parent(row)
        pop.set_has_arrow(False)
        row.connect("destroy", lambda *_: pop.unparent())

        def show(_g, _n, x, y):
            if pid not in self.peers or self.peers[pid].get("offline"):
                return
            r = Gdk.Rectangle()
            r.x, r.y, r.width, r.height = int(x), int(y), 1, 1
            pop.set_pointing_to(r)
            pop.popup()
        g = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        g.connect("pressed", show)
        row.add_controller(g)
        lp = Gtk.GestureLongPress()
        lp.connect("pressed", lambda _g, x, y: show(_g, 1, x, y))
        row.add_controller(lp)

    # ---------- favourites ----------
    def _star_button(self, pid, on):
        b = Gtk.Button(icon_name="starred-symbolic" if on else "non-starred-symbolic",
                       css_classes=["flat", "circular", "fav-star"] + (["on"] if on else []),
                       valign=Gtk.Align.CENTER,
                       tooltip_text="Remove from favourites" if on else "Add to favourites")
        b.connect("clicked", lambda *_: self.toggle_favorite(pid))
        return b

    def toggle_favorite(self, pid):
        p = self.peers.get(pid, {})
        on = not p.get("favorite", False)
        self.send_cmd({"cmd": "favorite", "peer_id": pid, "fingerprint": p.get("fingerprint", ""), "on": on})

    def _update_fav_header(self):
        key = self.current or ""
        if not key.startswith("dm:"):
            self.fav_btn.set_visible(False)
            return
        p = self.peers.get(key[3:])
        self.fav_btn.set_visible(bool(p and p.get("fingerprint")))
        on = bool(p and p.get("favorite"))
        self.fav_btn.set_icon_name("starred-symbolic" if on else "non-starred-symbolic")
        self.fav_btn.set_tooltip_text("Remove from favourites" if on else "Add to favourites")
        if on:
            self.fav_btn.add_css_class("on")
        else:
            self.fav_btn.remove_css_class("on")

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
            c.empty.set_label("No messages yet.\nPublic messages reach every nearby mesh peer in Bluetooth range — "
                              "including strangers — and are not encrypted.\n"
                              "Files can't be sent here: open a chat with a mutual favourite to send one.")
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
        self.attach_btn.set_visible(c.kind == "dm")
        self.dm_menu_btn.set_visible(c.kind == "dm")
        self._update_fav_header()
        if c.kind == "public":
            n = sum(1 for p in self.peers.values() if not p.get("offline"))
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
        if p.get("offline"):
            return "★ Favourite · not in range"
        bits = ["Online" if p.get("online") else "Away"]
        if p.get("favorite") and p.get("favorited_us"):
            bits.insert(0, "★ Mutual favourite")
        elif p.get("favorite"):
            bits.insert(0, "★ Favourite")
        elif p.get("favorited_us"):
            bits.insert(0, "Favourited you")
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

    def add_file_row(self, key, sender, name, size, ts, path=None, mine=False, status=None, kind=None):
        """A chat bubble for a file: name + size, and for received files
        Preview (images) / Open / Show in folder buttons. Nothing is ever opened automatically."""
        bubble, st = self.add_message(key, sender, f"📎 {name}", ts, mine=mine, status=status)
        body = bubble.get_first_child()
        body.add_css_class("file-name")
        info = Gtk.Label(label=human_size(size) + ("" if mine else " · saved to received files (quarantine)"),
                         xalign=0, css_classes=["meta"])
        bubble.insert_child_after(info, body)
        if path and not mine:
            row = Gtk.Box(spacing=6, css_classes=["file-actions"], margin_top=2)
            if kind == "image" or name.lower().endswith(IMAGE_EXTS):
                pb = Gtk.Button(label="Preview", tooltip_text=f"View {name} inside BlueMesh")
                pb.connect("clicked", lambda *_: self.preview_image(path, name))
                row.append(pb)
            ob = Gtk.Button(label="Open", tooltip_text=f"Open {name} with its default app")
            ob.connect("clicked", lambda *_: self.open_file(path))
            fb = Gtk.Button(label="Show in folder", tooltip_text=path)
            fb.connect("clicked", lambda *_: self.reveal_file(path))
            row.append(ob)
            row.append(fb)
            bubble.insert_child_after(row, info)
        return bubble, st

    def preview_image(self, path, name):
        """Shows a received image in its own window. Decoded only when asked."""
        if not os.path.isfile(path):
            self.toast("That file is no longer there")
            return
        try:
            if os.path.getsize(path) > MAX_PREVIEW_BYTES:
                self.toast("That image is too large to preview")
                return
            tex = Gdk.Texture.new_from_filename(path)
        except (GLib.Error, OSError) as e:
            self.toast(f"Couldn't show it as an image: {getattr(e, 'message', None) or e}")
            return
        w, h = tex.get_width(), tex.get_height()
        scale = min(1.0, 900 / max(w, 1), 700 / max(h, 1))
        win = Adw.Window(transient_for=self, modal=False, title=name,
                         default_width=max(240, int(w * scale)), default_height=max(180, int(h * scale) + 46))
        view = ToolbarView()
        hb = Adw.HeaderBar()
        hb.set_title_widget(Adw.WindowTitle(title=name, subtitle=f"{w}×{h}"))
        view.add_top_bar(hb)
        pic = Gtk.Picture.new_for_paintable(tex)
        pic.set_can_shrink(True)
        if hasattr(pic, "set_content_fit"):
            pic.set_content_fit(Gtk.ContentFit.CONTAIN)
        view.set_content(pic)
        win.set_content(view)
        esc = Gtk.ShortcutController()
        esc.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"),
                                          Gtk.CallbackAction.new(lambda *_: (win.close(), True)[1])))
        win.add_controller(esc)
        win.present()

    def open_file(self, path):
        if not os.path.isfile(path):
            self.toast("That file is no longer there")
            return
        f = Gio.File.new_for_path(path)
        if hasattr(Gtk, "FileLauncher"):
            Gtk.FileLauncher.new(f).launch(self, None, self._launch_done)
        else:
            try:
                Gio.AppInfo.launch_default_for_uri(f.get_uri(), None)
            except GLib.Error as e:
                self.toast(f"Couldn't open it: {e.message}")

    def reveal_file(self, path):
        f = Gio.File.new_for_path(path)
        if hasattr(Gtk, "FileLauncher") and os.path.exists(path):
            Gtk.FileLauncher.new(f).open_containing_folder(self, None, self._reveal_done)
        else:
            try:
                Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(os.path.dirname(path)).get_uri(), None)
            except GLib.Error as e:
                self.toast(f"Couldn't open the folder: {e.message}")

    def _launch_done(self, launcher, res):
        try:
            launcher.launch_finish(res)
        except GLib.Error as e:
            if "dismiss" not in (e.message or "").lower():
                self.toast(f"Couldn't open it: {e.message}")

    def _reveal_done(self, launcher, res):
        try:
            launcher.open_containing_folder_finish(res)
        except GLib.Error as e:
            if "dismiss" not in (e.message or "").lower():
                self.toast(f"Couldn't open the folder: {e.message}")

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
            self.set_status("err", "Backend missing", "bluemeshd not found")
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
        log = os.environ.get("BLUEMESH_EVENTLOG")
        if log:
            with open(log, "a") as f:
                f.write(json.dumps(ev) + "\n")
        if kind == "ready":
            self.my_nick = ev.get("nickname", "")
            self.my_peer_id = ev.get("peer_id", "")
            self.nick_set = ev.get("nickname_set", True)
            limit = ev.get("max_private_file_bytes")
            if isinstance(limit, int) and limit > 0:
                self.max_file_bytes = limit
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
        elif kind == "file_sending":
            w = self.pending.get(ev.get("req"))
            if w and w[1]:
                w[1].set_label(f"Sending {ev.get('parts', 0)} fragments…")
        elif kind == "file":
            sender = ev.get("sender", "?")
            name = ev.get("name", "file")
            key = PUBLIC
            if ev.get("private") and ev.get("peer_id"):
                key = f"dm:{ev['peer_id']}"
                self._conv(key, sender, "dm")
                self._ensure_dm_row(ev["peer_id"], sender)
            if ev.get("path"):
                self.add_file_row(key, sender, name, ev.get("size", 0), ev.get("ts", time.time()),
                                  path=ev.get("path"), kind=ev.get("kind"))
            else:
                self.add_system(key, f"{sender} sent {name} ({human_size(ev.get('size', 0))}) "
                                     f"but it couldn't be saved: {ev.get('error', 'unknown error')}")
            self.bump_unread(key)
            self.notify(f"{sender} ({'private' if key != PUBLIC else 'Public'})", f"Sent a file: {name}", key)
        elif kind == "file_ignored":
            sender = ev.get("sender", "?")
            key = f"dm:{ev.get('peer_id')}" if ev.get("private") and f"dm:{ev.get('peer_id')}" in self.convs else PUBLIC
            self.add_system(key, f"Ignored file “{ev.get('name', 'file')}” from non-contact {sender} "
                                 f"(only mutual favourites can send you files). Nothing was saved.")
        elif kind == "blocked":
            if ev.get("ok"):
                nick = ev.get("nickname", "")
                self.toast(f"Blocked {nick}. Their messages are dropped.")
                key = f"dm:{ev.get('peer_id')}"
                if key in self.convs:
                    self.add_system(key, f"You blocked {nick}.")
                if not ev.get("saved", True):
                    self.toast("Blocked for now, but couldn't save the block list")
            else:
                self.toast(f"Couldn't block: {ev.get('error', 'error')}")
        elif kind == "unblocked":
            if ev.get("ok"):
                self.toast(f"Unblocked {ev.get('nickname', '')}")
            else:
                self.toast(f"Couldn't unblock: {ev.get('error', 'error')}")
        elif kind == "blocks":
            self._fill_blocks(ev.get("blocked", []))
        elif kind == "channels":
            self._fill_channels(ev)
        elif kind == "wiped":
            self._on_wiped(ev)
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
        elif kind == "favorite":
            pid = ev.get("peer_id", "")
            if pid in self.peers:
                self.peers[pid]["favorite"] = bool(ev.get("on"))
            self._update_fav_header()
            if not ev.get("saved", True):
                self.toast("Couldn't save favourites")
        elif kind == "favorite_update":
            # Android shows this inside the private chat too
            key = f"dm:{ev.get('peer_id')}"
            nick = ev.get("nickname", "")
            fav = ev.get("favorited_us")
            mine = self.peers.get(ev.get("peer_id"), {}).get("favorite")
            if key in self.convs:
                extra = (" — mutual!" if mine else " — star them back to make it mutual") if fav else ""
                self.add_system(key, f"{nick} {'favourited' if fav else 'unfavourited'} you{extra}")
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
        if k == "file":
            st.set_label("Sent 🔒 ✓")
        elif k == "public":
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
        def order(kv):
            p = kv[1]
            return (not p.get("favorite"), not p.get("online"), p["nickname"].lower())
        for pid, p in sorted(self.peers.items(), key=order):
            key = f"dm:{pid}"
            if p.get("offline"):
                sub = "Not in range"
            else:
                sub = ("Online" if p.get("online") else "Away") + (" · 🔒" if p.get("encrypted") else "")
            if p.get("favorite") and p.get("favorited_us"):
                sub = "Mutual · " + sub
            elif p.get("favorited_us"):
                sub = "Favourited you · " + sub
            online += 1 if p.get("online") else 0
            row = self._add_sidebar_row(self.people_list, key, p["nickname"], sub,
                                        dot="on" if p.get("online") else "off",
                                        star=(pid, bool(p.get("favorite"))) if p.get("fingerprint") else None)
            if p.get("offline"):
                row.add_css_class("offline-fav")
            shown.add(key)
            if key in self.convs:
                self.convs[key].title = p["nickname"]
                self._update_unread(key)
        # DMs with people who went out of range stay reachable in the list
        for key, conv in self.convs.items():
            if conv.kind == "dm" and key not in shown:
                self._add_sidebar_row(self.people_list, key, conv.title, "Out of range", dot="off")
                self._update_unread(key)
        nearby = sum(1 for p in self.peers.values() if not p.get("offline"))
        self.people_heading.set_label(f"People nearby ({nearby})" if nearby else "People nearby")
        self._update_fav_header()
        if self.current in self.sidebar_rows:
            row = self.sidebar_rows[self.current][0]
            if row.get_parent() is self.people_list:
                self.people_list.select_row(row)
        if self.current == PUBLIC or (self.current or "").startswith("dm:"):
            c = self.convs.get(self.current)
            if c and c.kind == "public":
                n = sum(1 for p in self.peers.values() if not p.get("offline"))
                self.conv_title.set_subtitle(f"Bluetooth mesh · {n} {'person' if n == 1 else 'people'} nearby")
            elif c:
                self.conv_title.set_subtitle(self._dm_subtitle(self.peers.get(self.current[3:])))

    def _update_me(self):
        vis = "  ·  visible" if getattr(self, "visible", False) else ""
        self.me_label.set_label(f"You: {self.my_nick}  ·  ID {self.my_peer_id[:8]}{vis}")
        self.me_label.set_tooltip_text("Other mesh devices can connect to this computer" if vis else
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

    def _file_target_problem(self, pid):
        """Why a file can't go to this person right now, or None if it can."""
        p = self.peers.get(pid)
        if not p or p.get("offline"):
            return "They aren't in range right now"
        if not (p.get("mutual") or (p.get("favorite") and p.get("favorited_us"))):
            return ("Files only go to mutual favourites. Star them, and they need to star you back")
        if not p.get("encrypted"):
            return "No encrypted session yet. Send them a message first, then try again"
        return None

    def choose_file(self):
        key = self.current or ""
        if not key.startswith("dm:"):
            self.toast("Open a chat with a mutual favourite to send them a file")
            return
        pid = key[3:]
        problem = self._file_target_problem(pid)
        if problem:
            self.toast(problem)
            return
        nick = self.peers.get(pid, {}).get("nickname", "them")
        title = f"Send a file to {nick} (max {self.max_file_bytes / 1024:.1f} KiB)"
        if hasattr(Gtk, "FileDialog"):
            dlg = Gtk.FileDialog(title=title, modal=True, accept_label="Send")
            dlg.open(self, None, lambda d, r: self._file_chosen(d, r, pid))
        else:
            dlg = Gtk.FileChooserNative(title=title, transient_for=self, modal=True,
                                        action=Gtk.FileChooserAction.OPEN, accept_label="Send")
            def resp(d, r):
                if r == Gtk.ResponseType.ACCEPT and d.get_file():
                    self.send_file(d.get_file().get_path(), pid)
                d.destroy()
            dlg.connect("response", resp)
            self._chooser = dlg  # keep a reference while it is open
            dlg.show()

    def _file_chosen(self, dlg, res, pid):
        try:
            f = dlg.open_finish(res)
        except GLib.Error:
            return  # cancelled
        if f and f.get_path():
            self.send_file(f.get_path(), pid)
        elif f:
            self.toast("Only local files can be sent")

    def send_file(self, path, pid):
        """Sends a file to one mutual favourite, sealed in the Noise session.
        There is no broadcast path: bluemeshd refuses a send without a peer."""
        name = os.path.basename(path)
        problem = self._file_target_problem(pid)
        if problem:
            self.toast(problem)
            return
        try:
            size = os.path.getsize(path)
        except OSError as e:
            self.toast(f"Couldn't read {name}: {e.strerror}")
            return
        if size > self.max_file_bytes:
            self.toast(f"{name} is {human_size(size)}; private file transfers are limited to "
                       f"{self.max_file_bytes / 1024:.1f} KiB ({self.max_file_bytes:,} bytes)")
            return
        if size == 0:
            self.toast(f"{name} is empty")
            return
        key = f"dm:{pid}"
        self._conv(key, self.peers.get(pid, {}).get("nickname", pid[:8]), "dm")
        self.req_seq += 1
        req = self.req_seq
        bubble, st = self.add_file_row(key, self.my_nick, name, size, time.time(), mine=True, status="Sending…")
        self.pending[req] = (bubble, st)
        if not self.send_cmd({"cmd": "send_file", "path": path, "peer_id": pid, "req": req}):
            bubble.add_css_class("failed")
            st.set_label("Not sent")
            self.pending.pop(req, None)

    # ---------- block / slap ----------
    def ask_block(self, pid):
        p = self.peers.get(pid)
        if not p or p.get("offline"):
            self.toast("You can only block someone who is in range")
            return
        nick = p.get("nickname", pid[:8])
        dlg = alert(self, f"Block {nick}?",
                    f"You won't see anything {nick} sends, they disappear from your people list, and any "
                    "encrypted session with them is closed. You can undo this from Menu → Blocked people.")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("block", "Block")
        dlg.set_response_appearance("block", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")
        dlg.connect("response", lambda d, r: r == "block" and self.send_cmd({"cmd": "block", "peer": pid}))
        present_dialog(dlg, self)

    def show_blocks(self):
        dlg = alert(self, "Blocked people", "People you've blocked. Unblocking lets their messages through again.")
        self.blocks_box = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        self.blocks_box.set_placeholder(Gtk.Label(label="Nobody is blocked", margin_top=12, margin_bottom=12,
                                                  css_classes=["dim-label"]))
        sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
                                max_content_height=320, child=self.blocks_box)
        dlg.set_extra_child(sc)
        dlg.add_response("close", "Close")
        dlg.set_default_response("close")
        dlg.set_close_response("close")

        def closed(*_):
            self.blocks_dialog = None
            self.blocks_box = None
        dlg.connect("response", closed)
        self.blocks_dialog = dlg
        present_dialog(dlg, self)
        self.send_cmd({"cmd": "blocks"})

    def _fill_blocks(self, blocked):
        if not self.blocks_box:
            return
        while (child := self.blocks_box.get_first_child()) is not None:
            self.blocks_box.remove(child)
        for b in blocked:
            row = Adw.ActionRow(title=GLib.markup_escape_text(b.get("nickname", "")),
                                subtitle=GLib.markup_escape_text(f"ID {b.get('peer_id', '')[:8]}"))
            btn = Gtk.Button(label="Unblock", valign=Gtk.Align.CENTER)
            fp = b.get("fingerprint", "")
            btn.connect("clicked", lambda *_, fp=fp: self.send_cmd({"cmd": "unblock", "peer": fp}))
            row.add_suffix(btn)
            self.blocks_box.append(row)

    def slap(self, pid):
        p = self.peers.get(pid)
        if not p or p.get("offline"):
            self.toast("They aren't in range")
            return
        text = SLAP_TEXT.format(me=self.my_nick or "someone", them=p.get("nickname", pid[:8]))
        self.select_conv(PUBLIC)
        self.entry.set_text(text)
        self.send_current()

    # ---------- emergency wipe ----------
    def ask_wipe(self):
        dlg = alert(self, "Emergency wipe",
                    "This permanently destroys this computer's BlueMesh identity: your keys, nickname, "
                    "favourites, verified contacts, block list and recent location channels. "
                    "People will no longer recognise you. This can't be undone.\n\n"
                    "BlueMesh closes afterwards; the next launch starts with a brand-new identity.\n\n"
                    "Type CONFIRM to continue.")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        entry = Gtk.Entry(placeholder_text="CONFIRM")
        files = Gtk.CheckButton(label="Also delete received files")
        box.append(entry)
        box.append(files)
        dlg.set_extra_child(box)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("wipe", "Wipe everything")
        dlg.set_response_appearance("wipe", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_response_enabled("wipe", False)
        dlg.set_default_response("cancel")
        entry.connect("changed", lambda e: dlg.set_response_enabled("wipe", e.get_text().strip() == "CONFIRM"))

        def done(d, r):
            if r == "wipe" and entry.get_text().strip() == "CONFIRM":
                self.send_cmd({"cmd": "wipe", "confirm": True, "files": files.get_active()})
        dlg.connect("response", done)
        present_dialog(dlg, self)

    def _on_wiped(self, ev):
        if not ev.get("ok"):
            self.toast(ev.get("error", "Wipe failed"))
            return
        self.stopping = True
        self.peers = {}
        for key in [k for k in self.convs if k != PUBLIC]:
            self.convs.pop(key, None)
        self.my_nick = ""
        self._refresh_people()
        msg = "Your identity, keys, favourites and block list are gone."
        if ev.get("files_removed"):
            msg += " Received files were deleted."
        dlg = alert(self, "Identity destroyed", msg + " BlueMesh will now close.")
        dlg.add_response("ok", "Close BlueMesh")
        dlg.connect("response", lambda *_: self.app.quit())
        present_dialog(dlg, self)
        GLib.timeout_add_seconds(10, lambda: (self.app.quit(), False)[1])

    # ---------- location channels ----------
    def browse_channels(self):
        levels = "\n".join(f"  {n} characters: {label}" for n, label in CHANNEL_LEVELS)
        dlg = alert(self, "Location channels",
                    "Channels are named after a place (a geohash). Longer = smaller area:\n" + levels +
                    "\n\nThey travel over the internet (Nostr relays), not Bluetooth, and anyone can read them.")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        entry_row = Gtk.Box(spacing=6)
        entry = Gtk.Entry(placeholder_text="#geohash", hexpand=True)
        join = Gtk.Button(label="Join", css_classes=["suggested-action"])

        def do_join(*_):
            gh = entry.get_text().strip().lstrip("#").lower()
            if gh:
                self.send_cmd({"cmd": "join", "channel": gh})
                dlg.close() if hasattr(dlg, "close") else None
        join.connect("clicked", do_join)
        entry.connect("activate", do_join)
        entry_row.append(entry)
        entry_row.append(join)
        box.append(entry_row)
        self.channels_box = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        self.channels_box.set_placeholder(Gtk.Label(label="No recent channels", margin_top=10, margin_bottom=10,
                                                    css_classes=["dim-label"]))
        box.append(Gtk.Label(label="Recent", xalign=0, css_classes=["caption-heading", "dim-label"]))
        box.append(Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
                                      max_content_height=260, child=self.channels_box))
        dlg.set_extra_child(box)
        dlg.add_response("close", "Close")
        dlg.set_default_response("close")
        dlg.set_close_response("close")

        def closed(*_):
            self.channels_dialog = None
            self.channels_box = None
        dlg.connect("response", closed)
        self.channels_dialog = dlg
        present_dialog(dlg, self)
        self.send_cmd({"cmd": "channels"})

    def _fill_channels(self, ev):
        if not self.channels_box:
            return
        while (child := self.channels_box.get_first_child()) is not None:
            self.channels_box.remove(child)
        people = {j.get("channel"): j.get("people", 0) for j in ev.get("joined", [])}
        for r in ev.get("recent", []):
            ch = r.get("channel", "")
            sub = f"≈ {r.get('lat')}, {r.get('lon')}"
            if ch in people:
                sub = f"Joined · {people[ch]} active · " + sub
            row = Adw.ActionRow(title=GLib.markup_escape_text(ch), subtitle=GLib.markup_escape_text(sub))
            btn = Gtk.Button(label="Open" if ch in people else "Join", valign=Gtk.Align.CENTER)

            def go(*_, ch=ch):
                if ch in self.convs:
                    self.select_conv(ch)
                else:
                    self.send_cmd({"cmd": "join", "channel": ch.lstrip("#")})
                if self.channels_dialog is not None:
                    self.channels_dialog.close()
            btn.connect("clicked", go)
            row.add_suffix(btn)
            self.channels_box.append(row)

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
                          placeholder_text="e.g. sam")
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
                    "Location channels are named after a place (a geohash, e.g. #r3gx for a city-sized area). "
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
        about = (Adw.AboutDialog if MODERN else Adw.AboutWindow)(application_name="BlueMesh", application_icon="bluemesh",
                                version=VERSION, developer_name="BlueMesh contributors",
                                comments="Off-grid chat over Bluetooth mesh with nearby devices.\nWorks with nearby BlueMesh / BitChat-compatible devices.\n"
                                         "Backend: bluemeshd, a fork of bitmancer (MIT). See License for full notices.",
                                license_type=Gtk.License.MIT_X11)
        lic = license_text()
        if lic:
            about.set_license(lic)  # full LICENSE incl. bitmancer / BitChat TUI notices
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
        try:
            node = snap.to_node()
        except TypeError as e:  # PyGObject on GTK 4.8 can't wrap the node type
            print("screenshot unavailable on this GTK:", e, file=sys.stderr)
            return
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


class BlueMeshApp(Adw.Application):
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
            self.win = BlueMeshWindow(self)
        self.win.present()
        # Hidden developer hooks, used for automated testing only.
        shot = os.environ.get("BLUEMESH_SCREENSHOT")
        if shot:
            GLib.timeout_add_seconds(int(os.environ.get("BLUEMESH_SCREENSHOT_DELAY", "20")),
                                     lambda: (self.win.save_screenshot(shot), False)[1])
        dmtest = os.environ.get("BLUEMESH_SELFTEST_DM")
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
            GLib.timeout_add_seconds(int(os.environ.get("BLUEMESH_SELFTEST_DM_DELAY", "15")), try_dm)
        for spec in filter(None, os.environ.get("BLUEMESH_SHOTS", "").split(";")):
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
        fav = os.environ.get("BLUEMESH_SELFTEST_FAV")
        if fav:
            # "seconds|nick": open that private chat and click the header star
            secs, _, nick = fav.partition("|")

            def try_fav():
                pid = next((k for k, p in self.win.peers.items() if p.get("nickname") == nick), None)
                row = self.win.sidebar_rows.get(f"dm:{pid}", (None,))[0] if pid else None
                if row is None:
                    return True
                self.win._row_activated(self.win.people_list, row)
                self.win.fav_btn.emit("clicked")
                return False
            GLib.timeout_add_seconds(int(secs), try_fav)
        sf = os.environ.get("BLUEMESH_SELFTEST_FILE")
        if sf:
            # "seconds|nick|path": send that file to a mutual favourite
            secs, nick, fpath = (sf.split("|", 2) + ["", ""])[:3]

            def try_file():
                pid = next((k for k, p in self.win.peers.items() if p.get("nickname") == nick), None)
                if pid:
                    self.win.send_file(fpath, pid)
                return False
            GLib.timeout_add_seconds(int(secs), try_file)
        rc = os.environ.get("BLUEMESH_SELFTEST_RECONNECT")
        if rc:
            GLib.timeout_add_seconds(int(rc), lambda: (self.win.restart_backend(), False)[1])
        test = os.environ.get("BLUEMESH_SELFTEST")
        if test:
            def try_send():
                need = os.environ.get("BLUEMESH_SELFTEST_WAITFOR")
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
        hints = {"desktop-entry": GLib.Variant("s", "bluemesh"),
                 "category": GLib.Variant("s", "im.received")}
        try:
            self.notifier.call("Notify", GLib.Variant("(susssasa{sv}i)", (
                APP_NAME, 0, "bluemesh", title, body[:300], [], hints, -1)),
                Gio.DBusCallFlags.NONE, -1, None, None, None)
        except Exception as e:
            print("notify failed:", e, file=sys.stderr)


def main():
    return BlueMeshApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
