"""Isolated optional SILK codec process (bounded by the parent process timeout)."""

import sys

import pilk

if __name__ == "__main__":
    action, source, destination, rate = sys.argv[1:]
    if action == "decode":
        pilk.decode(source, destination, pcm_rate=int(rate))
    elif action == "encode":
        pilk.encode(source, destination, pcm_rate=int(rate), tencent=True)
    else:
        raise SystemExit(2)
