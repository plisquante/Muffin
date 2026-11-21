"""
Muffin Native I/O & zlib Compression Bridge (Mojo 1.2).

Zero-JNI direct C-ABI calls to system `libz.so.1` and POSIX file descriptors
for reading Minecraft region `.mca` chunks, resource packs, and assets with
byte-for-byte HotSpot compatibility.
"""

from std.ffi import OwnedDLHandle
from std.memory import Pointer
from std.origin import MutUntrackedOrigin


struct ZlibCodec:
    """Direct libz.so.1 wrapper (crc32, adler32, compress2, uncompress)."""

    var handle: OwnedDLHandle
    var available: Bool

    def __init__(out self) raises:
        self.handle = OwnedDLHandle("libz.so.1")
        self.available = True

    def crc32(self, data: Pointer[UInt8, MutUntrackedOrigin], length: Int, seed: UInt64 = 0) raises -> UInt64:
        return self.handle.get_function[UInt64]("crc32")(seed, data, UInt32(length))

    def adler32(self, data: Pointer[UInt8, MutUntrackedOrigin], length: Int, seed: UInt64 = 1) raises -> UInt64:
        return self.handle.get_function[UInt64]("adler32")(seed, data, UInt32(length))

    def compress_bound(self, src_len: Int) raises -> Int:
        return Int(self.handle.get_function[UInt64]("compressBound")(UInt64(src_len)))

    def compress(
        self,
        dst: Pointer[UInt8, MutUntrackedOrigin],
        mut dst_len: UInt64,
        src: Pointer[UInt8, MutUntrackedOrigin],
        src_len: Int,
        level: Int32 = 6,
    ) raises -> Int32:
        return self.handle.get_function[Int32]("compress2")(
            dst,
            Pointer(to=dst_len).unsafe_origin_cast[MutUntrackedOrigin](),
            src,
            UInt64(src_len),
            level,
        )

    def uncompress(
        self,
        dst: Pointer[UInt8, MutUntrackedOrigin],
        mut dst_len: UInt64,
        src: Pointer[UInt8, MutUntrackedOrigin],
        src_len: Int,
    ) raises -> Int32:
        return self.handle.get_function[Int32]("uncompress")(
            dst,
            Pointer(to=dst_len).unsafe_origin_cast[MutUntrackedOrigin](),
            src,
            UInt64(src_len),
        )
