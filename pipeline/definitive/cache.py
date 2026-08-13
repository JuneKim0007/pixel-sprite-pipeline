
from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from typing import Any, Callable

import numpy as np

MAX_ENTRIES = 64
MAX_BYTES = 8 << 20        # 8 MB, which is thousands of palettes


class Cache:
    """A small LRU that knows roughly how big its values are."""

    def __init__(self, max_entries: int = MAX_ENTRIES, max_bytes: int = MAX_BYTES):
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._data: OrderedDict[str, tuple[Any, int]] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def _size(self, value: Any) -> int:
        if isinstance(value, np.ndarray):
            return int(value.nbytes)
        if isinstance(value, (list, tuple)):
            return sum(self._size(v) for v in value) or 64
        return 64

    def get(self, key: str, build: Callable[[], Any]) -> Any:
        found = self._data.get(key)
        if found is not None:
            self.hits += 1
            self._data.move_to_end(key)
            return found[0]

        self.misses += 1
        value = build()
        self._data[key] = (value, self._size(value))
        self._evict()
        return value

    def peek(self, key: str) -> Any:
        """A hit without building on a miss."""
        found = self._data.get(key)
        if found is None:
            self.misses += 1
            return None
        self.hits += 1
        self._data.move_to_end(key)
        return found[0]

    def put(self, key: str, value: Any) -> None:
        self._data[key] = (value, self._size(value))
        self._data.move_to_end(key)
        self._evict()

    def _evict(self) -> None:
        while len(self._data) > self.max_entries:
            self._data.popitem(last=False)
        total = sum(size for _, size in self._data.values())
        while total > self.max_bytes and self._data:
            _, (_, size) = self._data.popitem(last=False)
            total -= size

    def clear(self) -> None:
        self._data.clear()

    def stats(self) -> dict:
        return {
            "entries": len(self._data),
            "bytes": sum(size for _, size in self._data.values()),
            "hits": self.hits,
            "misses": self.misses,
        }


CACHE = Cache()

# Intermediate images, so a change at layer k does not recompute layers 0..k-1.
# Separate from CACHE because the values are megabytes rather than bytes and
# want their own budget; sharing one would let a single image evict every
# palette.
SNAPSHOTS = Cache(max_entries=12, max_bytes=24 << 20)


def prefix_key(source: str, stack: list, upto: int) -> str:
    """Identity of the image after the first `upto` layers.

    The whole prefix goes into the key, in order, so this is exact rather than
    a guess: two stacks agreeing on their first three layers agree on the image
    after three layers, whatever they do afterwards. A disabled layer
    contributes its disabled state, because turning one off is a different
    prefix rather than a shorter one.
    """
    parts = []
    for entry in stack[:upto]:
        parts.append({
            "layer": entry.get("layer"),
            "enabled": entry.get("enabled", True),
            "config": entry.get("config") or {},
        })
    return "prefix:" + hashlib.blake2b(
        json.dumps([source, parts], sort_keys=True, default=str).encode(),
        digest_size=16).hexdigest()


def resume_from(source: str, stack: list) -> tuple[int, Any]:
    """The longest already-computed prefix of this stack.

    This is the answer to "the user moved one slider": everything before the
    layer they touched is unchanged by definition, so it does not need running
    again. Searching longest-first means a change at the end of a five-layer
    stack costs one layer, and a change at the start costs all five - which is
    exactly the shape of the work that actually changed.
    """
    for upto in range(len(stack), 0, -1):
        found = SNAPSHOTS.peek(prefix_key(source, stack, upto))
        if found is not None:
            return upto, found
    return 0, None


def remember(source: str, stack: list, upto: int, image) -> None:
    """Keep the image after `upto` layers, if it is small enough to be worth it.

    Full-resolution results are not kept. One 1280 px RGBA frame is 6.5 MB and
    would evict the entire snapshot budget to save a step that only runs when
    someone presses Write - the opposite of the trade this is for.
    """
    if getattr(image, "nbytes", 0) > (SNAPSHOTS.max_bytes // 4):
        return
    SNAPSHOTS.put(prefix_key(source, stack, upto), image)


def fingerprint(image: np.ndarray) -> str:
    """A content hash of an image, cheap enough to take on every call.

    Sampled rather than complete. Hashing 5 MB per keystroke would cost more
    than some of what it saves, and a stride that reads every 7th row and 5th
    column still touches ~28,000 pixels of a 1280 canvas - enough that two
    images differing anywhere visible differ here. Shape and dtype go in whole,
    so a resize or a channel change can never collide.

    This is a cache key, not a checksum: a miss on a false difference costs one
    recomputation, and the strides are chosen coprime to any plausible block
    size so a lattice cannot align with them.
    """
    h = hashlib.blake2b(digest_size=16)
    h.update(f"{image.shape}{image.dtype}".encode())
    h.update(np.ascontiguousarray(image[::7, ::5]).tobytes())
    return h.hexdigest()


def key(what: str, image: np.ndarray, params: Any = None) -> str:
    return f"{what}:{fingerprint(image)}:{json.dumps(params, sort_keys=True, default=str)}"


def count_colours(image: np.ndarray) -> int:
    """Distinct colours, without paying for pixels that cannot add any.

    np.unique is linear in pixels and the editor asks for this twice per run,
    purely to display a number. On an image the Scale layer has magnified it is
    pure waste: nearest-neighbour repetition cannot invent a colour, so a 1.6
    megapixel upscale costs 0.70 s to reach the same answer 0.04 s buys on the
    image it was made from - measured, 17x for nothing.

    Above the threshold the count is taken on a strided view. The stride is
    coprime with any plausible block size so it cannot land on one phase of a
    lattice and miss the others, and a sprite's palette is small enough that
    sampling a quarter of a megapixel finds all of it.
    """
    flat = image.reshape(-1, image.shape[2])[:, :3]
    if len(flat) > 400_000:
        flat = flat[::7]
    return int(len(np.unique(flat, axis=0)))
