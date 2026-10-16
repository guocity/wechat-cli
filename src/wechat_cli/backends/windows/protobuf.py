"""Bounded protobuf wire primitives for the local WeChat component protocols."""


def varint(value):
    if not 0 <= value < 2**64:
        raise ValueError("invalid varint")
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def field(number, value):
    if isinstance(value, int):
        return varint(number << 3) + varint(value)
    return varint(number << 3 | 2) + varint(len(value)) + value


def fields(data):
    if not isinstance(data, bytes) or len(data) > 8 * 1024**2:
        raise ValueError("oversized protobuf")
    cursor = 0

    def integer():
        nonlocal cursor
        value = 0
        for shift in range(0, 70, 7):
            if cursor >= len(data):
                raise ValueError("truncated varint")
            byte = data[cursor]
            cursor += 1
            if shift == 63 and byte > 1:
                raise ValueError("overflowing varint")
            value |= (byte & 127) << shift
            if byte < 128:
                return value
        raise ValueError("overflowing varint")

    count = 0
    while cursor < len(data):
        count += 1
        if count > 100000:
            raise ValueError("too many protobuf fields")
        tag = integer()
        number, wire = tag >> 3, tag & 7
        if not 1 <= number < 2**29:
            raise ValueError("invalid field number")
        if wire == 0:
            yield number, integer()
            continue
        length = integer() if wire == 2 else {1: 8, 5: 4}.get(wire)
        if length is None or cursor + length > len(data):
            raise ValueError("invalid protobuf field")
        value = data[cursor : cursor + length]
        cursor += length
        yield number, value
