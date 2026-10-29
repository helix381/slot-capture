"""Read-only decoder for observed BLSocket DataUtils values; no network IO.

Input to decode() excludes the four-byte big-endian frame length.
Unsupported tags and trailing bytes are errors, never silently skipped.
"""
import struct

class DecodeError(ValueError):
    pass

class Reader:
    def __init__(self, data):
        self.data = memoryview(data)
        self.pos = 0
        self.nodes = 0

    def take(self, n):
        if n < 0 or self.pos + n > len(self.data):
            raise DecodeError(f'truncated value at offset {self.pos}, need {n}')
        out = self.data[self.pos:self.pos+n].tobytes()
        self.pos += n
        return out

    def number(self, fmt):
        return struct.unpack(fmt, self.take(struct.calcsize(fmt)))[0]

    def value(self, depth=0):
        self.nodes += 1
        if depth > 100 or self.nodes > 1000000:
            raise DecodeError('value nesting or node limit exceeded')
        at = self.pos
        tag = self.number('<B')
        if tag in (1, 2, 3, 12, 15):
            return self.number({1:'<b', 2:'<h', 3:'<i', 12:'<q', 15:'<d'}[tag])
        if tag in (4, 5):
            n = self.number('<B' if tag == 4 else '<H')
            return [self.value(depth+1) for _ in range(n)]
        if tag in (6, 7, 8):
            n = self.number({6:'<B', 7:'<H', 8:'<I'}[tag])
            raw = self.take(n)
            try:
                return raw.decode('utf-8')
            except UnicodeDecodeError:
                return raw
        if tag == 10:
            n = self.number('<H')
            raw = self.take(2*n)
            # Native dump(std::string) zero-extends each original UTF-8 byte.
            # Its native reader uses only even bytes; this is not UTF-16 text.
            if any(raw[1::2]):
                raise DecodeError(f'nonzero interleaved padding at offset {at}')
            raw = raw[::2]
            try:
                return raw.decode('utf-8')
            except UnicodeDecodeError:
                return raw
        if tag in (13, 14):
            n = self.number('<B' if tag == 13 else '<H')
            result = {}
            for _ in range(n):
                key = self.value(depth+1)
                val = self.value(depth+1)
                if not isinstance(key, (str, int, float, bytes)):
                    raise DecodeError(f'unhashable map key at offset {at}')
                if key in result:
                    raise DecodeError(f'duplicate map key at offset {at}')
                result[key] = val
            return result
        raise DecodeError(f'unsupported tag 0x{tag:02x} at offset {at}')

def decode(data: bytes):
    reader = Reader(data)
    result = reader.value()
    if reader.pos != len(reader.data):
        raise DecodeError(f'trailing bytes: consumed {reader.pos} of {len(reader.data)}')
    return result
