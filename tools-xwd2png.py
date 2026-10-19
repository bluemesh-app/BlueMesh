#!/usr/bin/env python3
"""Convert a 24/32-bit ZPixmap XWD dump (xwd -root) to PNG using GdkPixbuf."""
import struct, sys
import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib
d = open(sys.argv[1], "rb").read()
h = struct.unpack(">25I", d[:100])
hsize, fmt, depth, w, ht = h[0], h[2], h[3], h[4], h[5]
byte_order, bpp, bpl, ncolors = h[7], h[11], h[12], h[19]
off = hsize + ncolors * 12
assert bpp in (24, 32), bpp
n = bpp // 8
rgb = bytearray(w * ht * 3)
for y in range(ht):
    row = d[off + y * bpl: off + y * bpl + w * n]
    if byte_order == 0:   # LSB first: B G R [X]
        rgb[y*w*3:(y+1)*w*3] = bytes(b for i in range(0, len(row), n) for b in (row[i+2], row[i+1], row[i]))
    elif n == 4:          # X R G B
        rgb[y*w*3:(y+1)*w*3] = bytes(b for i in range(0, len(row), 4) for b in (row[i+1], row[i+2], row[i+3]))
    else:                 # R G B
        rgb[y*w*3:(y+1)*w*3] = row
pb = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(bytes(rgb)), GdkPixbuf.Colorspace.RGB, False, 8, w, ht, w * 3)
pb.savev(sys.argv[2], "png", [], [])
