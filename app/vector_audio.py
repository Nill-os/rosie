# board #789 quirk-ogg-or-raw-pcm
import ctypes
import ctypes.util
import struct

import numpy as np

RATE = 16000
FRAME = 320


class OpusUnavailable(RuntimeError):
    pass


_lib = None


def _opus():
    global _lib
    if _lib is None:
        name = ctypes.util.find_library("opus")
        if not name:
            raise OpusUnavailable("libopus is not installed (apt install libopus0)")
        lib = ctypes.CDLL(name)
        lib.opus_decoder_create.restype = ctypes.c_void_p
        lib.opus_decoder_create.argtypes = [ctypes.c_int32, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        lib.opus_decode.restype = ctypes.c_int
        lib.opus_decode.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int32, ctypes.POINTER(ctypes.c_int16), ctypes.c_int, ctypes.c_int]
        lib.opus_decoder_destroy.argtypes = [ctypes.c_void_p]
        _lib = lib
    return _lib


class OggOpusDecoder:
    MAX_FRAME = 5760

    def __init__(self):
        lib = _opus()
        err = ctypes.c_int()
        self._dec = lib.opus_decoder_create(RATE, 1, ctypes.byref(err))
        if err.value != 0 or not self._dec:
            raise OpusUnavailable(f"opus_decoder_create failed ({err.value})")
        self._buf = b""
        self._packet = b""  # board #789 quirk-ogg-packet-continues
        self._out = (ctypes.c_int16 * self.MAX_FRAME)()

    def __del__(self):
        if getattr(self, "_dec", None) and _lib:
            _lib.opus_decoder_destroy(self._dec)
            self._dec = None

    def feed(self, data: bytes) -> bytes:
        self._buf += data
        pcm = []
        while True:
            start = self._buf.find(b"OggS")
            if start < 0:
                self._buf = self._buf[-3:]
                break
            if start:
                self._buf = self._buf[start:]
            if len(self._buf) < 27:
                break
            nseg = self._buf[26]
            if len(self._buf) < 27 + nseg:
                break
            lacing = self._buf[27:27 + nseg]
            body = 27 + nseg
            size = sum(lacing)
            if len(self._buf) < body + size:
                break
            pos = body
            for lace in lacing:
                self._packet += self._buf[pos:pos + lace]
                pos += lace
                if lace < 255:
                    pcm.append(self._decode(self._packet))
                    self._packet = b""
            self._buf = self._buf[body + size:]
        return b"".join(pcm)

    def _decode(self, packet: bytes) -> bytes:
        if not packet or packet.startswith((b"OpusHead", b"OpusTags")):
            return b""
        n = _lib.opus_decode(self._dec, packet, len(packet), self._out, self.MAX_FRAME, 0)
        if n <= 0:
            return b""
        return bytes(memoryview(self._out).cast("B")[: n * 2])


class MicStream:

    def __init__(self, first_chunk: bytes):
        self.opus = first_chunk[:4] == b"OggS"
        self._dec = OggOpusDecoder() if self.opus else None
        self.pcm = bytearray()

    def add(self, chunk: bytes) -> bytes:
        new = self._dec.feed(chunk) if self.opus else chunk
        self.pcm += new
        return new

    def float32(self) -> np.ndarray:
        n = len(self.pcm) // 2
        return np.frombuffer(bytes(self.pcm[: n * 2]), dtype="<i2").astype(np.float32) / 32768.0


class Endpointer:

    def __init__(self, silence_ms=500, min_speech_ms=200, no_speech_ms=3500, max_ms=7000):
        self.silence_frames = silence_ms // 20
        self.min_speech = min_speech_ms // 20
        self.no_speech = no_speech_ms // 20
        self.max_frames = max_ms // 20
        self._rest = b""
        self.frames = 0
        self.speech = 0
        self.quiet = 0
        self._levels: list[float] = []
        self.done = False
        self.heard = False

    def _threshold(self) -> float:
        floor = float(np.percentile(self._levels, 10)) if self._levels else 0.0
        return max(floor * 3.0, 250.0)

    def add(self, pcm: bytes) -> bool:
        data = self._rest + pcm
        whole = len(data) // (FRAME * 2) * FRAME * 2
        self._rest = data[whole:]
        for i in range(0, whole, FRAME * 2):
            frame = np.frombuffer(data[i:i + FRAME * 2], dtype="<i2").astype(np.float32)
            level = float(np.sqrt(np.mean(frame * frame)))
            self._levels.append(level)
            self.frames += 1
            if level > self._threshold():
                self.speech += 1
                self.quiet = 0
                if self.speech >= self.min_speech:
                    self.heard = True
            else:
                self.quiet += 1
            if (self.heard and self.quiet >= self.silence_frames) or (not self.heard and self.frames >= self.no_speech) \
                    or self.frames >= self.max_frames:
                self.done = True
                break
        return self.done


_SMALL = {"zero": 0, "one": 1, "a": 1, "an": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
          "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
          "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
          "ninety": 90, "half": 0.5}
_UNIT = {"second": 1, "seconds": 1, "sec": 1, "secs": 1, "minute": 60, "minutes": 60, "min": 60, "mins": 60,
         "hour": 3600, "hours": 3600}


def duration_seconds(text: str) -> int:
    total, num = 0.0, None
    for w in text.lower().replace("-", " ").replace(",", " ").split():
        if w.replace(".", "", 1).isdigit():
            num = (num or 0) + float(w)
        elif w in _SMALL:
            if w == "half" and num:
                num += 0.5
            elif w in ("a", "an") and num:
                continue
            else:
                num = (num or 0) + _SMALL[w]
        elif w in _UNIT and num is not None:
            total += num * _UNIT[w]
            num = None
        elif w == "and":
            continue
        else:
            num = None
    return int(round(total))


def wav_bytes(pcm: bytes, rate=RATE) -> bytes:
    return b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16) \
        + b"data" + struct.pack("<I", len(pcm)) + pcm


def to_16k_pcm(audio, sr: int) -> bytes:
    a = np.asarray(audio, dtype=np.float32).reshape(-1)
    if sr != RATE:
        if sr > RATE:
            n = np.arange(-32, 33)
            fc = 0.45 * RATE / sr
            taps = 2 * fc * np.sinc(2 * fc * n) * np.hamming(len(n))
            a = np.convolve(a, taps / taps.sum(), mode="same")
        t = np.arange(0, len(a) * RATE / sr) * sr / RATE
        a = np.interp(t, np.arange(len(a)), a)
    peak = float(np.max(np.abs(a))) if len(a) else 0.0
    if peak > 0.99:
        a = a * (0.99 / peak)
    return (a * 32767).astype("<i2").tobytes()
