"""Glitchy pixel-octopus overlay and the click-and-hold orb. Runs with the SYSTEM python (GTK3 + Cairo), as its own small program.

Talks to the voice daemon over a unix socket (JSON lines):
  daemon -> overlay  {"state": "idle|listening|thinking|speaking", "level": 0..1, "caption": "text"}
  overlay -> daemon  {"orb": "down"} / {"orb": "up"}
The big window is transparent, always on top and click-through (it never takes a click or the keyboard focus).
"""
import json
import math
import os
import random
import socket
import sys
import time
from pathlib import Path

import ctypes

import cairo  # noqa: E402  (must come before Gtk)
import gi

# PyGObject needs its small cairo bridge (python3-gi-cairo) to hand a drawing context to Python. If the system lacks it, use the copy unpacked in ~/assistant/vendor.
try:
    gi.require_foreign("cairo")
except ImportError:
    gi.__path__.append(str(Path(__file__).resolve().parent.parent / "vendor" / "gi"))
    gi.require_foreign("cairo")
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
from gi.repository import Gdk, GLib, Gtk, GdkX11  # noqa: E402,F401

sys.path.insert(0, str(Path(__file__).resolve().parent))
import octopus_sprite  # noqa: E402

SOCK = Path(os.environ.get("ASSISTANT_OVERLAY_SOCK") or Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "assistant-overlay.sock")
CONFIG = Path(__file__).resolve().parent.parent / "data" / "config.json"

# ghost colours (the RGB-split layers) and the caption colour for each state
PALETTE = {"listening": ((0.0, 0.9, 1.0), (0.62, 0.3, 1.0)), "thinking": ((0.62, 0.3, 1.0), (0.0, 0.9, 1.0)), "speaking": ((0.75, 0.3, 1.0), (0.1, 0.9, 0.8))}
LABEL = {"listening": "listening", "thinking": "thinking", "speaking": "speaking"}


def hexrgb(h):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255


def xshape_click_through(widget):
    """Give the window an EMPTY input area straight through the X Shape extension, so every click and key goes to whatever is underneath.
    (GTK's own input_shape call did not take effect in testing, this does.)"""
    try:
        x11, xext = ctypes.CDLL("libX11.so.6"), ctypes.CDLL("libXext.so.6")
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        xext.XShapeCombineRectangles.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        dpy = x11.XOpenDisplay(None)
        xext.XShapeCombineRectangles(dpy, widget.get_window().get_xid(), 2, 0, 0, None, 0, 0, 0)       # kind ShapeInput, operation ShapeSet, zero rectangles
        x11.XSync(dpy, 0)
        x11.XCloseDisplay(dpy)
        return True
    except Exception as e:
        print("click-through not applied:", e, flush=True)
        return False


def primary_geometry():
    d = Gdk.Display.get_default()
    m = d.get_primary_monitor() or d.get_monitor(0)
    return m.get_geometry()


class Art:
    """The sprite pre-drawn once per size: full colour plus two flat-coloured silhouettes used as the glitch 'ghosts'."""

    def __init__(self, sprite, width):
        self.scale = width / sprite["w"]
        self.w = int(sprite["w"] * self.scale) + 1
        self.h = int(sprite["h"] * self.scale) + 1
        self.sprite = sprite
        self.main = self._draw(None)
        self.sil = self._draw((1, 1, 1))

    def _draw(self, flat):
        s = cairo.ImageSurface(cairo.FORMAT_ARGB32, self.w, self.h)
        cr = cairo.Context(s)
        cr.set_antialias(cairo.ANTIALIAS_NONE)
        for x, y, w, h, c in self.sprite["rects"]:
            cr.set_source_rgb(*(flat or hexrgb(c)))
            cr.rectangle(int(x * self.scale), int(y * self.scale), int(w * self.scale) + 1, int(h * self.scale) + 1)
            cr.fill()
        return s


def tinted(art, rgb):
    """A coloured copy of the silhouette (cached by the caller)."""
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, art.w, art.h)
    cr = cairo.Context(s)
    cr.set_source_rgb(*rgb)
    cr.mask_surface(art.sil, 0, 0)
    return s


class Scene:
    """Everything that is drawn. Pure function of (time, state, level, mode) so it can be rendered to a PNG and checked."""

    def __init__(self, sprite, width=620):
        self.art = Art(sprite, width)
        self.ghosts: dict = {}
        self.sprite = sprite

    def ghost(self, state, i):
        key = (state, i)
        if key not in self.ghosts:
            self.ghosts[key] = tinted(self.art, PALETTE[state][i])
        return self.ghosts[key]

    def glitch_amount(self, t, level, mode):
        """0..1: how hard the picture is tearing right now. Bursts of ~0.2 s about once a second; louder voice = stronger."""
        if mode == "reduced":
            return 0.0
        phase = (t * 1.1) % 1.0
        burst = 1.0 if phase < 0.2 else 0.0
        burst *= 0.6 + 0.4 * math.sin(t * 37.0) ** 2
        return min(1.0, 0.12 + 0.5 * level + 0.7 * burst)

    def draw(self, cr, W, H, t, state, level, mode="full", caption="", alpha=1.0, seed=None):
        if state not in PALETTE or mode == "off" or alpha <= 0.01:
            return
        rnd = random.Random(seed if seed is not None else int(t * 15))      # the tear pattern changes 15 times a second, not every frame
        art = self.art
        g = self.glitch_amount(t, level, mode)
        big = mode != "subtle"
        k = (1.0 if big else 0.42) * (1.0 + 0.05 * level)
        ow, oh = art.w * k, art.h * k
        cx, cy = (W / 2, H / 2 - 20) if big else (W - ow / 2 - 40, H - oh / 2 - 130)
        x0, y0 = cx - ow / 2, cy - oh / 2 + 8 * math.sin(t * 1.6)
        pal = PALETTE[state]
        # soft tinted wash behind (not a flash: constant strength), so the octopus reads on any background
        if big:
            wash = cairo.RadialGradient(cx, cy, oh * 0.1, cx, cy, max(W, H) * 0.55)
            wash.add_color_stop_rgba(0, *pal[0], 0.16 * alpha)
            wash.add_color_stop_rgba(1, *pal[0], 0.0)
            cr.set_source(wash)
            cr.paint()
        # scanlines over the whole screen (very faint) and a few torn bars
        if big and mode != "reduced":
            cr.set_source_rgba(*pal[0], 0.035 * alpha)
            for yy in range(0, H, 6):
                cr.rectangle(0, yy, W, 1)
            cr.fill()
            for _ in range(int(2 + 7 * g)):
                bw, bh = rnd.randint(120, 900), rnd.choice((2, 3, 4, 6, 10))
                bx, by = rnd.randint(-100, W - 50), rnd.randint(0, H)
                cr.set_source_rgba(*pal[rnd.randint(0, 1)], rnd.uniform(0.05, 0.22) * alpha)
                cr.rectangle(bx, by, bw, bh)
                cr.fill()
        # the octopus in slices: each horizontal strip is shifted sideways by a random amount while it is tearing
        slices = []
        y = 0.0
        while y < art.h:
            sh = rnd.choice((6, 10, 14, 22, 40)) if g > 0.2 else 60
            slices.append((y, min(sh, art.h - y)))
            y += sh
        split = (3 + 22 * g) * k if mode != "reduced" else 3 * k
        for sy, sh in slices:
            dx = rnd.uniform(-1, 1) * 46 * g * k if rnd.random() < 0.15 + 0.5 * g else 0.0
            for layer, (surf, ox, a) in enumerate(((self.ghost(state, 0), -split, 0.55), (self.ghost(state, 1), split, 0.55), (art.main, 0, 1.0))):
                cr.save()
                cr.rectangle(x0 + (dx if layer == 2 else dx * 0.8), y0 + sy * k, ow + 200, sh * k + 1)
                cr.clip()
                cr.translate(x0 + dx + ox, y0)
                cr.scale(k, k)
                cr.set_source_surface(surf, 0, 0)
                cr.set_antialias(cairo.ANTIALIAS_NONE)
                cr.paint_with_alpha(a * alpha * (0.78 + 0.22 * math.sin(t * 9) ** 2 if mode != "reduced" else 0.9) if layer < 2 else (alpha if mode == "reduced" else alpha * (0.88 + 0.12 * math.sin(t * 9 + 1) ** 2)))
                cr.restore()
        # a few loose pixel blocks that drift off the octopus
        if mode != "reduced":
            for _ in range(int(3 + 14 * g)):
                bx = x0 + rnd.uniform(-60, ow + 60)
                by = y0 + rnd.uniform(-30, oh + 30)
                s = rnd.choice((6, 9, 12, 18))
                cr.set_source_rgba(*pal[rnd.randint(0, 1)], rnd.uniform(0.3, 0.8) * alpha)
                cr.rectangle(bx, by, s, s)
                cr.fill()
        # caption
        text = caption or LABEL[state]
        cr.select_font_face("DejaVu Sans Mono", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(30 if big else 18)
        dots = "." * (int(t * 2.5) % 4) if not caption and state != "speaking" else ""
        label = (text[:90] + ("…" if len(text) > 90 else "")) + dots
        ext = cr.text_extents(label)
        tx = cx - ext.width / 2 if big else x0 + ow / 2 - ext.width / 2
        ty = y0 + oh + (50 if big else 28)
        cr.set_source_rgba(0, 0, 0, 0.55 * alpha)
        cr.rectangle(tx - 14, ty - ext.height - 12, ext.width + 28, ext.height + 24)
        cr.fill()
        cr.set_source_rgba(*pal[0], alpha)
        cr.move_to(tx, ty)
        cr.show_text(label)
        # level meter under the caption while listening or speaking
        if state in ("listening", "speaking") and big:
            bars = 24
            for i in range(bars):
                on = (i / bars) < level * 1.4
                cr.set_source_rgba(*pal[1 if i % 2 else 0], (0.9 if on else 0.15) * alpha)
                cr.rectangle(cx - bars * 7 + i * 14, ty + 22, 9, 8)
                cr.fill()


class Orb(Gtk.Window):
    """Small always-visible circle: press and hold it to talk to the AI, let go to send."""
    SIZE = 76

    def __init__(self, app):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.app = app
        self.set_type_hint(Gdk.WindowTypeHint.DOCK)
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)
        self.set_focus_on_map(False)
        self.set_app_paintable(True)
        self.set_default_size(self.SIZE, self.SIZE)
        self.set_visual(self.get_screen().get_rgba_visual())
        self.stick()
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK | Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.connect("draw", self.on_draw)
        self.connect("button-press-event", lambda w, e: self.app.orb_event("down") if e.button == 1 else None)
        self.connect("button-release-event", lambda w, e: self.app.orb_event("up") if e.button == 1 else None)
        self.connect("enter-notify-event", lambda *a: setattr(self, "hover", True) or self.queue_draw())
        self.connect("leave-notify-event", lambda *a: setattr(self, "hover", False) or self.queue_draw())
        self.hover = False
        self.small = Art(app.sprite, 56)

    def place(self, corner):
        geo = primary_geometry()
        m = 24
        x = geo.x + (geo.width - self.SIZE - m if "right" in corner else m)
        y = geo.y + (geo.height - self.SIZE - m - 40 if "bottom" in corner else m + 40)
        self.move(x, y)

    def on_draw(self, w, cr):
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        s = self.SIZE
        state = self.app.state
        pal = PALETTE.get(state, ((0.55, 0.6, 0.7), (0.3, 0.3, 0.4)))
        cr.arc(s / 2, s / 2, s / 2 - 3, 0, 2 * math.pi)
        cr.set_source_rgba(0.04, 0.05, 0.09, 0.88)
        cr.fill_preserve()
        cr.set_source_rgba(*pal[0], 1.0 if state != "idle" or self.hover else 0.55)
        cr.set_line_width(3)
        cr.stroke()
        cr.save()
        cr.translate((s - self.small.w) / 2, (s - self.small.h) / 2)
        cr.set_source_surface(self.small.main, 0, 0)
        cr.paint_with_alpha(1.0 if state != "idle" or self.hover else 0.8)
        cr.restore()
        return False


class App:
    def __init__(self):
        self.sprite = octopus_sprite.load()
        self.state, self.level, self.caption = "idle", 0.0, ""
        self.alpha, self.target_alpha = 0.0, 0.0
        self.cfg = {}
        self.client = None
        self.buf = b""
        self.read_cfg()
        geo = primary_geometry()
        self.W, self.H = geo.width, geo.height
        self.scene = Scene(self.sprite, int(min(self.W * 0.34, self.H * 0.62 * self.sprite["w"] / self.sprite["h"], 720)))
        self.win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        w = self.win
        w.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
        w.set_decorated(False)
        w.set_keep_above(True)
        w.set_skip_taskbar_hint(True)
        w.set_skip_pager_hint(True)
        w.set_accept_focus(False)
        w.set_focus_on_map(False)
        w.set_app_paintable(True)
        w.set_visual(w.get_screen().get_rgba_visual())
        w.set_default_size(self.W, self.H)
        w.move(geo.x, geo.y)
        w.stick()
        w.connect("draw", self.on_draw)
        w.connect("realize", self.make_click_through)
        w.connect("map", self.make_click_through)
        self.t0 = time.time()
        self.orb = Orb(self)
        self.apply_orb()
        self.start_server()
        GLib.timeout_add(66, self.tick)
        GLib.timeout_add_seconds(5, self.read_cfg_tick)

    # ---------- settings ----------
    def read_cfg(self):
        try:
            self.cfg = json.loads(CONFIG.read_text())
        except (OSError, ValueError):
            self.cfg = {}

    def read_cfg_tick(self):
        self.read_cfg()
        self.apply_orb()
        return True

    def apply_orb(self):
        if self.cfg.get("ai_orb_on", True):
            self.orb.place(self.cfg.get("ai_orb_corner", "bottom-right"))
            if not self.orb.get_visible():
                self.orb.show_all()
        elif self.orb.get_visible():
            self.orb.hide()

    # ---------- window plumbing ----------
    def make_click_through(self, widget):
        widget.input_shape_combine_region(cairo.Region())           # empty input area: every click and key goes to the window underneath
        xshape_click_through(widget)

    def on_draw(self, w, cr):
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        mode = self.cfg.get("ai_overlay_mode", "full")
        self.scene.draw(cr, self.W, self.H, time.time() - self.t0, self.state if self.state != "idle" else self.fading_state, self.level, mode, self.caption, self.alpha)
        return False

    fading_state = "listening"

    def tick(self):
        if self.state != "idle":
            self.fading_state = self.state
            self.target_alpha = 1.0
        else:
            self.target_alpha = 0.0
        self.alpha += (self.target_alpha - self.alpha) * 0.35
        if abs(self.alpha - self.target_alpha) < 0.02:
            self.alpha = self.target_alpha
        mode = self.cfg.get("ai_overlay_mode", "full")
        if self.alpha > 0 and mode != "off":
            if not self.win.get_visible():
                self.win.show_all()
            self.win.queue_draw()
        elif self.win.get_visible():
            self.win.hide()
        self.orb.queue_draw()
        return True

    # ---------- socket ----------
    def start_server(self):
        try:
            SOCK.unlink()
        except FileNotFoundError:
            pass
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(str(SOCK))
        os.chmod(SOCK, 0o600)
        self.srv.listen(1)
        self.srv.setblocking(False)
        GLib.io_add_watch(self.srv.fileno(), GLib.IO_IN, self.on_accept)

    def on_accept(self, fd, cond):
        try:
            c, _ = self.srv.accept()
        except OSError:
            return True
        if self.client:
            try:
                self.client.close()
            except OSError:
                pass
        c.setblocking(False)
        self.client, self.buf = c, b""
        GLib.io_add_watch(c.fileno(), GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, self.on_data, c)
        return True

    def on_data(self, fd, cond, c):
        try:
            data = c.recv(4096)
        except BlockingIOError:
            return True
        except OSError:
            data = b""
        if not data:                                    # the voice daemon went away: never leave the octopus stuck on screen
            if c is self.client:
                self.client = None
                self.state = "idle"
            return False
        self.buf += data
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if "state" in m and m["state"] in ("idle", "listening", "thinking", "speaking"):
                self.state = m["state"]
                if self.state == "idle":
                    self.caption = ""
            if "level" in m:
                try:
                    self.level = max(0.0, min(1.0, float(m["level"])))
                except (TypeError, ValueError):
                    pass
            if "caption" in m:
                self.caption = str(m["caption"])[:200]
        return True

    def orb_event(self, kind):
        if self.client:
            try:
                self.client.send((json.dumps({"orb": kind}) + "\n").encode())
            except OSError:
                pass


def render_preview(out_dir):
    """Offscreen check: write one PNG per state/mode so the look can be reviewed without a screen."""
    sprite = octopus_sprite.load()
    sc = Scene(sprite, 520)
    W, H = 1280, 800
    for name, state, level, mode, t in (("listening", "listening", 0.6, "full", 0.05), ("listening_quiet", "listening", 0.05, "full", 0.5), ("thinking", "thinking", 0.0, "full", 0.1),
                                        ("speaking", "speaking", 0.8, "full", 0.08), ("reduced", "speaking", 0.5, "reduced", 0.1), ("subtle", "listening", 0.4, "subtle", 0.1)):
        s = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
        cr = cairo.Context(s)
        cr.set_source_rgb(0.14, 0.16, 0.2)                     # a stand-in desktop
        cr.paint()
        cr.set_source_rgb(0.9, 0.9, 0.9)
        cr.select_font_face("DejaVu Sans", 0, 0)
        cr.set_font_size(18)
        for i in range(24):
            cr.move_to(60, 70 + i * 28)
            cr.show_text("The quick brown fox jumps over the lazy dog; this is a window underneath the overlay. " * 1)
        sc.draw(cr, W, H, t, state, level, mode, "", 1.0, seed=7)
        s.write_to_png(str(Path(out_dir) / f"overlay_{name}.png"))


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--preview":
        render_preview(sys.argv[2])
        sys.exit(0)
    App()
    Gtk.main()
