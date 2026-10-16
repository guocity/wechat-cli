"""Channels ISAAC64 stream: reverse result order, big-endian words, 128 KiB prefix.

The remainder is already clear compressed media; this does not transcode it.
"""

MASK = (1 << 64) - 1


def mix(v):
    a, b, c, d, e, f, g, h = v
    a = (a - e) & MASK
    f ^= h >> 9
    h = (h + a) & MASK
    b = (b - f) & MASK
    g ^= (a << 9) & MASK
    a = (a + b) & MASK
    c = (c - g) & MASK
    h ^= b >> 23
    b = (b + c) & MASK
    d = (d - h) & MASK
    a ^= (c << 15) & MASK
    c = (c + d) & MASK
    e = (e - a) & MASK
    b ^= d >> 14
    d = (d + e) & MASK
    f = (f - b) & MASK
    c ^= (e << 20) & MASK
    e = (e + f) & MASK
    g = (g - c) & MASK
    d ^= f >> 17
    f = (f + g) & MASK
    h = (h - d) & MASK
    e ^= (g << 14) & MASK
    g = (g + h) & MASK
    return [a, b, c, d, e, f, g, h]


def isaac(seed):
    r = [seed] + [0] * 255
    m = [0] * 256
    v = [0x9E3779B97F4A7C13] * 8
    for _ in range(4):
        v = mix(v)
    for source in (r, m):
        for i in range(0, 256, 8):
            v = mix([(x + y) & MASK for x, y in zip(v, source[i : i + 8])])
            m[i : i + 8] = v
    a = b = c = 0
    while True:
        c = (c + 1) & MASK
        b = (b + c) & MASK
        for i in range(256):
            x = m[i]
            if i % 4 == 0:
                a = ~(a ^ (a << 21)) & MASK
            elif i % 4 == 1:
                a ^= a >> 5
            elif i % 4 == 2:
                a ^= (a << 12) & MASK
            else:
                a ^= a >> 33
            a = (a + m[(i + 128) & 255]) & MASK
            y = (m[(x >> 3) & 255] + a + b) & MASK
            m[i] = y
            b = (m[(y >> 11) & 255] + x) & MASK
            r[i] = b
        for x in reversed(r):
            yield x


def decrypt(data, key, limit=131072):
    result = bytearray(data)
    g = isaac(key)
    for i in range(0, min(len(result), limit), 8):
        k = next(g).to_bytes(8, "big")
        for j in range(min(8, len(result) - i, limit - i)):
            result[i + j] ^= k[j]
    return bytes(result)
