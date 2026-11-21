"""
Muffin Multi-Core Runtime (Mojo 1.2).

Replaces Java's single-threaded tick / chunk-meshing / lighting loops and heavy
`ForkJoinPool` / `CompletableFuture` machinery with lightweight OS threads and
lock-free atomic work distribution.
"""

from std.atomic import Atomic
from std.benchmark import keep
from std.ffi import external_call
from std.memory import MutOpaquePointer, OptionalPointer, Pointer
from std.origin import MutUntrackedOrigin
from std.sys import size_of

comptime RangeTaskFn = def(Int, Int, MutOpaquePointer[MutUntrackedOrigin]) thin -> None


def cpu_count() -> Int:
    """Returns online hardware thread count (POSIX _SC_NPROCESSORS_ONLN = 84)."""
    var n = Int(external_call["sysconf", Int64](Int32(84)))
    return n if n > 0 else 1


struct _DispatchCtx:
    var total: Int
    var grain: Int
    var next_idx: Atomic[Int64]
    var func: RangeTaskFn
    var user_data: MutOpaquePointer[MutUntrackedOrigin]

    def __init__(
        out self,
        total: Int,
        grain: Int,
        func: RangeTaskFn,
        user_data: MutOpaquePointer[MutUntrackedOrigin],
    ):
        self.total = total
        self.grain = grain if grain > 0 else 1
        self.next_idx = Atomic[Int64](0)
        self.func = func
        self.user_data = user_data


def _worker_entry(
    raw: MutOpaquePointer[MutUntrackedOrigin],
) abi("C") -> OptionalPointer[NoneType, MutUntrackedOrigin]:
    var ctx = raw.unsafe_bitcast[_DispatchCtx]()
    var total = ctx[].total
    var grain = ctx[].grain
    while True:
        var start = Int(ctx[].next_idx.fetch_add(Int64(grain)))
        if start >= total:
            break
        var end = start + grain
        if end > total:
            end = total
        ctx[].func(start, end, ctx[].user_data)
    return {}


def parallel_for_range(
    total: Int,
    func: RangeTaskFn,
    user_data: MutOpaquePointer[_],
    *,
    num_threads: Int = 0,
    grain: Int = 64,
):
    """Executes `func(start, end, user_data)` across `num_threads` OS threads.

    Uses dynamic atomic chunking (`fetch_add(grain)`) to keep cores balanced
    even when per-item cost varies (e.g., dirty chunk sections or entity ticks).
    """
    var raw_ud = user_data.unsafe_origin_cast[MutUntrackedOrigin]()
    if total <= 0:
        return
    var threads = num_threads if num_threads > 0 else cpu_count()
    if threads <= 1 or total <= grain:
        func(0, total, raw_ud)
        return
    if threads > total:
        threads = total

    var ctx = _DispatchCtx(total, grain, func, raw_ud)
    var ctx_ptr = Pointer(to=ctx).unsafe_bitcast[NoneType]().unsafe_origin_cast[MutUntrackedOrigin]()
    var tids_opt = external_call["malloc", OptionalPointer[UInt64, MutUntrackedOrigin]](
        threads * Int(size_of[UInt64]())
    )
    if not tids_opt:
        func(0, total, raw_ud)
        return
    var tids = tids_opt.unsafe_value()
    var spawned = 0
    for i in range(threads - 1):
        var rc = external_call["pthread_create", Int32](
            tids.unsafe_offset(i),
            OptionalPointer[NoneType, MutUntrackedOrigin](),
            _worker_entry,
            ctx_ptr,
        )
        if rc == 0:
            spawned += 1

    # Calling thread also participates as a worker (zero wasted thread).
    _ = _worker_entry(ctx_ptr)

    for i in range(spawned):
        _ = external_call["pthread_join", Int32](
            tids.unsafe_offset(i)[],
            OptionalPointer[NoneType, MutUntrackedOrigin](),
        )
    keep(ctx.total)
    external_call["free", NoneType](tids.unsafe_bitcast[NoneType]())
