"""
Muffin Frame / Escape-Analysis Bump Arena (Mojo 1.2).

Zero-GC, zero-atomic, cache-friendly linear bump allocator for:
1. Non-escaping method allocations proven by Escape Analysis (`arena_mark` /
   `arena_restore` on method exit — zero `malloc`/`free` per object).
2. Per-tick / per-frame transient allocations (`begin_frame` / `end_frame`).
"""

from std.ffi import external_call
from std.memory import OptionalPointer, Pointer
from std.origin import MutUntrackedOrigin
from std.sys import size_of

comptime ARENA_CHUNK_BYTES: Int = 262144  # 256 KiB per slab


struct _ArenaChunk:
    var buf: Pointer[UInt8, MutUntrackedOrigin]
    var cap: Int
    var used: Int
    var next: OptionalPointer[_ArenaChunk, MutUntrackedOrigin]

    def __init__(out self, cap: Int):
        var raw = external_call["malloc", OptionalPointer[UInt8, MutUntrackedOrigin]](cap)
        self.buf = raw.unsafe_value()
        self.cap = cap
        self.used = 0
        self.next = {}


@fieldwise_init
struct ArenaMark(Copyable, Defaultable):
    var chunk: OptionalPointer[_ArenaChunk, MutUntrackedOrigin]
    var used: Int


struct FrameArena:
    """Thread-local bump allocator with O(1) mark/restore."""

    var head: OptionalPointer[_ArenaChunk, MutUntrackedOrigin]
    var current: OptionalPointer[_ArenaChunk, MutUntrackedOrigin]
    var total_allocated: Int

    def __init__(out self):
        self.head = {}
        self.current = {}
        self.total_allocated = 0

    def __deinit__(deinit self):
        var cur = self.head
        while cur:
            var cp = cur.unsafe_value()
            var nxt = cp[].next
            external_call["free", NoneType](cp[].buf.unsafe_bitcast[NoneType]())
            external_call["free", NoneType](cp.unsafe_bitcast[NoneType]())
            cur = nxt

    def _new_chunk(mut self, min_bytes: Int) -> Pointer[_ArenaChunk, MutUntrackedOrigin]:
        var cap = ARENA_CHUNK_BYTES if min_bytes <= ARENA_CHUNK_BYTES else min_bytes
        var cp = external_call["malloc", OptionalPointer[_ArenaChunk, MutUntrackedOrigin]](
            Int(size_of[_ArenaChunk]())
        ).unsafe_value()
        cp.unsafe_write(_ArenaChunk(cap))
        self.total_allocated += cap
        return cp

    @always_inline
    def mark(self) -> ArenaMark:
        if not self.current:
            return ArenaMark({}, 0)
        return ArenaMark(self.current, self.current.unsafe_value()[].used)

    @always_inline
    def restore(mut self, m: ArenaMark):
        if not m.chunk:
            var cur = self.head
            while cur:
                cur.unsafe_value()[].used = 0
                cur = cur.unsafe_value()[].next
            self.current = self.head
            return
        var cp = m.chunk.unsafe_value()
        cp[].used = m.used
        if cp[].next:
            var nxt = cp[].next
            while nxt:
                nxt.unsafe_value()[].used = 0
                nxt = nxt.unsafe_value()[].next
            self.current = m.chunk

    @always_inline
    def reset(mut self):
        self.restore(ArenaMark({}, 0))

    @always_inline
    def alloc_bytes(mut self, size: Int, align: Int = 8) -> Pointer[UInt8, MutUntrackedOrigin]:
        var mask = align - 1
        if self.current:
            var cp = self.current.unsafe_value()
            var aligned = (cp[].used + mask) & ~mask
            if aligned + size <= cp[].cap:
                var ptr = cp[].buf.unsafe_offset(aligned)
                cp[].used = aligned + size
                return ptr
            # Walk pre-allocated warm chunks before calling malloc
            var nxt = cp[].next
            while nxt:
                var np = nxt.unsafe_value()
                np[].used = 0
                if size <= np[].cap:
                    self.current = nxt
                    np[].used = size
                    return np[].buf
                nxt = np[].next

        var fresh = self._new_chunk(size + align)
        if not self.head:
            self.head = fresh
        else:
            var tail = self.current.unsafe_value()
            while tail[].next:
                tail = tail[].next.unsafe_value()
            tail[].next = fresh
        self.current = fresh
        fresh[].used = size
        return fresh[].buf
