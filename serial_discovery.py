"""Reserve Mega USB adapters and follow their physical USB port after renumbering."""
import os
import threading
from serial.tools import list_ports

class MegaPortDiscovery:
    # Genuine Mega USB bridges and supported CH340/FTDI clones.
    bridges = {(0x1A86, 0x7523), (0x1A86, 0x5523), (0x0403, 0x6001),
               (0x2341, 0x0042), (0x2A03, 0x0042), (0x2341, 0x0010), (0x2A03, 0x0010)}
    auto_slots = {'auto-mega'}

    def __init__(self):
        self._lock = threading.Lock()
        self._bindings = {}
        self._claimed = {}

    @staticmethod
    def _identity(info):
        # CH340 clones often have NO serial number: topology distinguishes them.
        location = getattr(info, 'location', None)
        if location:
            return ('usb', info.vid, info.pid, location)
        return ('path', os.path.realpath(info.device))

    def acquire(self, slot):
        """Return one unclaimed path; never reuse a stale tty number after binding."""
        try:
            ports = sorted(list_ports.comports(), key=lambda p: p.device)
        except (OSError, ValueError):
            return None
        with self._lock:
            binding = self._bindings.get(slot)
            if binding is not None:
                candidates = [p for p in ports if self._identity(p) == binding]
            else:
                candidates = [p for p in ports if os.path.realpath(p.device) == os.path.realpath(slot)]
                if slot in self.auto_slots:
                    candidates += [p for p in ports if p not in candidates
                                   and (p.vid, p.pid) in self.bridges]
            for info in candidates:
                identity = self._identity(info)
                if identity in self._claimed or any(
                    key != slot and value == identity for key, value in self._bindings.items()
                ):
                    continue
                self._bindings[slot] = identity
                self._claimed[identity] = slot
                return info.device
            # Explicit by-path links, COM ports, or custom paths with no metadata.
            # Never fall back to a missing default ttyUSB number.
            if binding is None and slot not in self.auto_slots and not candidates and os.path.exists(slot):
                identity = ('path', os.path.realpath(slot))
                if identity not in self._claimed:
                    self._claimed[identity] = slot
                    return slot
            return None

    def release(self, slot):
        with self._lock:
            for key, owner in list(self._claimed.items()):
                if owner == slot:
                    del self._claimed[key]

