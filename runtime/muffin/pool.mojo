"""
Muffin Persistent Multi-Core WorkerPool (Mojo 1.2).

Replaces Java's single-threaded tick / chunk-meshing / lighting loops and heavy
`ForkJoinPool` / `CompletableFuture` machinery with persistent OS worker threads:
- Workers are spawned ONCE and sleep on a POSIX condition variable (`0%` idle CPU).
- Batch submission sets the task range + atomically wakes all workers via
  `pthread_cond_broadcast` (no per-batch `pthread_create` overhead).
- Dynamic atomic chunk stealing (`next_idx.fetch_add(grain)`) keeps all cores
  balanced even on irregular Minecraft workloads (e.g. dirty chunk sections).
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


struct _PoolState:
    var mutex: Pointer[UInt8, MutUntrackedOrigin]  # 64B (covers pthread_mutex_t on x86_64/arm64)
    var cond_work: Pointer[UInt8, MutUntrackedOrigin]  # 64B
    var cond_done: Pointer[UInt8, MutUntrackedOrigin]  # 64B
    var epoch: Int64
    var shutdown: Bool
    var active_workers: Int32
    var total: Int
    var grain: Int
    var next_idx: Atomic[Int64]
    var func: OptionalPointer[RangeTaskFn, MutUntrackedOrigin]
    var func_val: RangeTaskFn
    var user_data: MutOpaquePointer[MutUntrackedOrigin]

    def __init__(out self, func_init: RangeTaskFn):
        self.mutex = external_call["calloc", OptionalPointer[UInt8, MutUntrackedOrigin]](1, 64).unsafe_value()
        self.cond_work = external_call["calloc", OptionalPointer[UInt8, MutUntrackedOrigin]](1, 64).unsafe_value()
        self.cond_done = external_call["calloc", OptionalPointer[UInt8, MutUntrackedOrigin]](1, 64).unsafe_value()
        var null_attr = OptionalPointer[NoneType, MutUntrackedOrigin]()
        _ = external_call["pthread_mutex_init", Int32](self.mutex.unsafe_bitcast[NoneType](), null_attr)
        _ = external_call["pthread_cond_init", Int32](self.cond_work.unsafe_bitcast[NoneType](), null_attr)
        _ = external_call["pthread_cond_init", Int32](self.cond_done.unsafe_bitcast[NoneType](), null_attr)
        self.epoch = 0
        self.shutdown = False
        self.active_workers = 0
        self.total = 0
        self.grain = 64
        self.next_idx = Atomic[Int64](0)
        self.func = {}
        self.func_val = func_init
        self.user_data = MutOpaquePointer[MutUntrackedOrigin].unsafe_dangling()

    def destroy(mut self):
        _ = external_call["pthread_cond_destroy", Int32](self.cond_work.unsafe_bitcast[NoneType]())
        _ = external_call["pthread_cond_destroy", Int32](self.cond_done.unsafe_bitcast[NoneType]())
        _ = external_call["pthread_mutex_destroy", Int32](self.mutex.unsafe_bitcast[NoneType]())
        external_call["free", NoneType](self.mutex.unsafe_bitcast[NoneType]())
        external_call["free", NoneType](self.cond_work.unsafe_bitcast[NoneType]())
        external_call["free", NoneType](self.cond_done.unsafe_bitcast[NoneType]())


def _noop_task(s: Int, e: Int, p: MutOpaquePointer[MutUntrackedOrigin]):
    pass


def _run_work(st: Pointer[_PoolState, MutUntrackedOrigin]):
    var total = st[].total
    var grain = st[].grain
    var f = st[].func_val
    var ud = st[].user_data
    while True:
        var start = Int(st[].next_idx.fetch_add(Int64(grain)))
        if start >= total:
            break
        var end = start + grain
        if end > total:
            end = total
        f(start, end, ud)


def _persistent_worker(
    raw: MutOpaquePointer[MutUntrackedOrigin],
) abi("C") -> OptionalPointer[NoneType, MutUntrackedOrigin]:
    var st = raw.unsafe_bitcast[_PoolState]()
    var seen_epoch: Int64 = 0
    while True:
        _ = external_call["pthread_mutex_lock", Int32](st[].mutex.unsafe_bitcast[NoneType]())
        while st[].epoch == seen_epoch and not st[].shutdown:
            _ = external_call["pthread_cond_wait", Int32](
                st[].cond_work.unsafe_bitcast[NoneType](),
                st[].mutex.unsafe_bitcast[NoneType](),
            )
        if st[].shutdown:
            _ = external_call["pthread_mutex_unlock", Int32](st[].mutex.unsafe_bitcast[NoneType]())
            break
        seen_epoch = st[].epoch
        _ = external_call["pthread_mutex_unlock", Int32](st[].mutex.unsafe_bitcast[NoneType]())

        _run_work(st)

        _ = external_call["pthread_mutex_lock", Int32](st[].mutex.unsafe_bitcast[NoneType]())
        st[].active_workers -= 1
        if st[].active_workers == 0:
            _ = external_call["pthread_cond_signal", Int32](st[].cond_done.unsafe_bitcast[NoneType]())
        _ = external_call["pthread_mutex_unlock", Int32](st[].mutex.unsafe_bitcast[NoneType]())
    return {}


struct WorkerPool:
    """Persistent multi-core worker pool with zero idle spin."""

    var state: Pointer[_PoolState, MutUntrackedOrigin]
    var tids: OptionalPointer[UInt64, MutUntrackedOrigin]
    var worker_count: Int

    def __init__(out self, num_threads: Int = 0):
        var total_threads = num_threads if num_threads > 0 else cpu_count()
        var w_count = total_threads - 1 if total_threads > 1 else 0
        self.worker_count = 0
        self.state = external_call["malloc", OptionalPointer[_PoolState, MutUntrackedOrigin]](
            Int(size_of[_PoolState]())
        ).unsafe_value()
        self.state.unsafe_write(_PoolState(_noop_task))
        self.tids = {}
        if w_count > 0:
            var tids_opt = external_call["malloc", OptionalPointer[UInt64, MutUntrackedOrigin]](
                w_count * Int(size_of[UInt64]())
            )
            if tids_opt:
                var tids_p = tids_opt.unsafe_value()
                var st_raw = self.state.unsafe_bitcast[NoneType]()
                for i in range(w_count):
                    var rc = external_call["pthread_create", Int32](
                        tids_p.unsafe_offset(i),
                        OptionalPointer[NoneType, MutUntrackedOrigin](),
                        _persistent_worker,
                        st_raw,
                    )
                    if rc == 0:
                        self.worker_count += 1
                self.tids = tids_p

    def __deinit__(deinit self):
        if self.worker_count > 0:
            _ = external_call["pthread_mutex_lock", Int32](self.state[].mutex.unsafe_bitcast[NoneType]())
            self.state[].shutdown = True
            _ = external_call["pthread_cond_broadcast", Int32](self.state[].cond_work.unsafe_bitcast[NoneType]())
            _ = external_call["pthread_mutex_unlock", Int32](self.state[].mutex.unsafe_bitcast[NoneType]())
            var tids_p = self.tids.unsafe_value()
            for i in range(self.worker_count):
                _ = external_call["pthread_join", Int32](
                    tids_p.unsafe_offset(i)[],
                    OptionalPointer[NoneType, MutUntrackedOrigin](),
                )
            external_call["free", NoneType](tids_p.unsafe_bitcast[NoneType]())
        self.state[].destroy()
        external_call["free", NoneType](self.state.unsafe_bitcast[NoneType]())

    def parallel_for(
        mut self,
        total: Int,
        func: RangeTaskFn,
        user_data: MutOpaquePointer[_],
        *,
        grain: Int = 64,
    ):
        var raw_ud = user_data.unsafe_origin_cast[MutUntrackedOrigin]()
        if total <= 0:
            return
        if self.worker_count == 0 or total <= grain:
            func(0, total, raw_ud)
            return

        _ = external_call["pthread_mutex_lock", Int32](self.state[].mutex.unsafe_bitcast[NoneType]())
        self.state[].total = total
        self.state[].grain = grain if grain > 0 else 1
        self.state[].next_idx = Atomic[Int64](0)
        self.state[].func_val = func
        self.state[].user_data = raw_ud
        self.state[].active_workers = Int32(self.worker_count)
        self.state[].epoch += 1
        _ = external_call["pthread_cond_broadcast", Int32](self.state[].cond_work.unsafe_bitcast[NoneType]())
        _ = external_call["pthread_mutex_unlock", Int32](self.state[].mutex.unsafe_bitcast[NoneType]())

        # Caller participates as worker 0
        _run_work(self.state)

        _ = external_call["pthread_mutex_lock", Int32](self.state[].mutex.unsafe_bitcast[NoneType]())
        while self.state[].active_workers > 0:
            _ = external_call["pthread_cond_wait", Int32](
                self.state[].cond_done.unsafe_bitcast[NoneType](),
                self.state[].mutex.unsafe_bitcast[NoneType](),
            )
        _ = external_call["pthread_mutex_unlock", Int32](self.state[].mutex.unsafe_bitcast[NoneType]())


def parallel_for_range(
    total: Int,
    func: RangeTaskFn,
    user_data: MutOpaquePointer[_],
    *,
    num_threads: Int = 0,
    grain: Int = 64,
):
    """One-shot parallel range helper (use `WorkerPool` directly for repeated ticks)."""
    var raw_ud = user_data.unsafe_origin_cast[MutUntrackedOrigin]()
    if total <= 0:
        return
    var threads = num_threads if num_threads > 0 else cpu_count()
    if threads <= 1 or total <= grain:
        func(0, total, raw_ud)
        return
    var pool = WorkerPool(threads)
    pool.parallel_for(total, func, raw_ud, grain=grain)
