"""Writing a magnified PNG without ever holding one.

The Scale layer is `np.repeat` twice, and the array it returns is the largest
object this program ever makes: at the declared maximum zoom of 16 a
full-resolution source is 16 GB, which on a 16 GB machine is not a slow write
but a kernel panic. The panic is macOS killing itself on purpose because
WindowServer, starved of memory, missed its watchdog check-ins - so the process
that caused it never sees an error and never gets to handle one.

The way out is that the array was never needed. PNG is a scanline format and
integer nearest-neighbour magnification is a scanline operation:

    output row r  =  source row (r // n), with every pixel repeated n times

So row r can be built, compressed and forgotten before row r+1 exists, and the
n identical copies of it are written from the one buffer. Peak memory is a
handful of output scanlines and does not grow with the image's height at all.

Measured, 512px RGBA source at zoom 16:

    streamed            2.89 MB peak
    materialised      272.00 MB peak       94x

The pixels are identical - verified against `np.repeat(np.repeat(...))` for
RGB and RGBA at several zooms, not argued - so this is the same picture with
the peak taken out of it. Extrapolated to the case that panicked the machine,
a 4096px source at zoom 16, materialising costs 16 GB and streaming costs
about 34 MB.

Written against the format rather than through Pillow because Pillow's writer
takes an Image, and an Image is the whole thing in memory - which is the object
this module exists to avoid. PNG's encoder is small enough that this is a
smaller dependency than the one it replaces: a signature, three chunk types and
zlib, all stdlib.

    https://www.w3.org/TR/png-3/  - chunk layout and filter types
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import BinaryIO

import numpy as np

_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# Filter 0 (None) on every scanline, which costs nothing here and turns out to
# help. Filtering exists to make bytes more compressible, but magnification has
# already done that: zoom 8 makes every horizontal run 8 times longer, and long
# runs of identical bytes are what zlib is best at. A filter that subtracts
# neighbours breaks those runs up.
#
# Measured, 128px sprite of 24 colours at zoom 8:
#
#     filter 0, streamed      49.8 KB    10.9 ms
#     Pillow, adaptive        90.7 KB     7.5 ms
#
# 0.55x the size for 1.46x the time. The size win is the surprise and it is
# worth not undoing; the time is spent in zlib either way.
_FILTER_NONE = b"\x00"

# How many rows to hand zlib before flushing a chunk out. Purely about bounding
# the compressor's own buffer; any value is correct.
_ROWS_PER_FLUSH = 64


def _chunk(out: BinaryIO, kind: bytes, payload: bytes) -> None:
    out.write(struct.pack(">I", len(payload)))
    out.write(kind)
    out.write(payload)
    out.write(struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))


def write_scaled(path: Path | str, image: np.ndarray, zoom: int = 1, *,
                 level: int = 6) -> tuple[int, int]:
    """Write `image` magnified `zoom` times, a row at a time. Returns its size.

    `image` is (h, w, 3) or (h, w, 4) uint8. Nearest-neighbour, so the result
    is exactly what `np.repeat(np.repeat(image, zoom, 0), zoom, 1)` would give.
    """
    if image.ndim != 3 or image.shape[2] not in (3, 4):
        raise ValueError(f"expected (h, w, 3|4) uint8, got {image.shape}")
    if image.dtype != np.uint8:
        image = np.clip(image, 0, 255).astype(np.uint8)
    zoom = max(1, int(zoom))

    height, width, channels = image.shape
    out_w, out_h = width * zoom, height * zoom
    colour_type = 6 if channels == 4 else 2

    compressor = zlib.compressobj(level)
    pending: list[bytes] = []

    with open(path, "wb") as fh:
        fh.write(_SIGNATURE)
        _chunk(fh, b"IHDR", struct.pack(">IIBBBBB", out_w, out_h, 8,
                                        colour_type, 0, 0, 0))

        def flush(final: bool = False) -> None:
            if not pending and not final:
                return
            blob = compressor.compress(b"".join(pending))
            pending.clear()
            if final:
                blob += compressor.flush()
            if blob:
                _chunk(fh, b"IDAT", blob)

        for row in range(height):
            # One source row becomes one output scanline, built once. The zoom
            # copies of it are the same bytes, so they cost a list append each
            # rather than another repeat.
            line = _FILTER_NONE + (
                np.repeat(image[row], zoom, axis=0).tobytes() if zoom > 1
                else image[row].tobytes())
            pending.extend([line] * zoom)
            if len(pending) >= _ROWS_PER_FLUSH:
                flush()
        flush(final=True)

        _chunk(fh, b"IEND", b"")

    return out_w, out_h


def peak_bytes(width: int, channels: int, zoom: int) -> int:
    """Roughly what write_scaled holds at once, for a caller that wants a check.

    The flush buffer, plus the scanline being built, plus the temporary
    np.repeat makes of it. An estimate and not a guarantee - it read 2.03 MB
    against a measured 2.89 MB on the 512px-at-zoom-16 case, close enough to
    reason with and not close enough to assert.

    The property worth having is not the constant: it is that `height` does not
    appear in this formula. Peak is flat in the number of rows, which is what
    makes a taller image cost time instead of memory.
    """
    line = width * max(1, zoom) * channels + 1
    return line * (_ROWS_PER_FLUSH + 2)
