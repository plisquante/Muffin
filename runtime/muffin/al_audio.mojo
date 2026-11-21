"""
Muffin Direct OpenAL Audio Bridge (Mojo 1.2).

Probes `libopenal.so.1` via `OwnedDLHandle` and falls back cleanly when running
headless without an audio output device.
"""

from std.memory import OptionalPointer
from std.origin import MutUntrackedOrigin
from std.ffi import OwnedDLHandle


struct AudioEngine:
    var available: Bool
    var device: OptionalPointer[NoneType, MutUntrackedOrigin]
    var context: OptionalPointer[NoneType, MutUntrackedOrigin]

    def __init__(out self):
        self.available = False
        self.device = {}
        self.context = {}
        try:
            var h = OwnedDLHandle("libopenal.so.1")
            var dev = h.get_function[OptionalPointer[NoneType, MutUntrackedOrigin]]("alcOpenDevice")(
                OptionalPointer[UInt8, MutUntrackedOrigin]()
            )
            if dev:
                self.device = dev
                self.available = True
                _ = h.get_function[Int8]("alcCloseDevice")(dev)
                self.device = {}
        except:
            self.available = False
