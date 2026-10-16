"""AES decoding through Windows CryptoAPI, without compiled Python dependencies."""

import ctypes as ct
import struct


def decrypt(data, key, iv=None):
    """Decrypt PKCS#7 AES-128 ECB (iv=None) or CBC; reject invalid padding."""
    api = ct.WinDLL("advapi32", use_last_error=True)
    handle, pointer, uint = ct.c_size_t, ct.c_void_p, ct.c_uint32
    shapes = {
        "CryptAcquireContextW": [pointer, ct.c_wchar_p, ct.c_wchar_p, uint, uint],
        "CryptImportKey": [handle, pointer, uint, handle, uint, pointer],
        "CryptSetKeyParam": [handle, uint, pointer, uint],
        "CryptDecrypt": [handle, handle, ct.c_int, uint, pointer, pointer],
        "CryptDestroyKey": [handle],
        "CryptReleaseContext": [handle, uint],
    }
    for name, arguments in shapes.items():
        method = getattr(api, name)
        method.restype, method.argtypes = ct.c_int, arguments
    provider, secret = handle(), handle()
    try:
        if len(key) != 16 or not data or len(data) % 16 or len(data) > 32 * 1024**2:
            return None
        if not api.CryptAcquireContextW(ct.byref(provider), None, None, 24, 0xF0000000):
            return None
        blob = ct.create_string_buffer(struct.pack("<BBHII", 8, 2, 0, 0x660E, 16) + key)
        if not api.CryptImportKey(provider, blob, 28, 0, 0, ct.byref(secret)):
            return None
        mode = uint(1 if iv is not None else 2)
        if not api.CryptSetKeyParam(secret, 4, ct.byref(mode), 0):
            return None
        if iv is not None:
            if len(iv) != 16 or not api.CryptSetKeyParam(secret, 1, ct.create_string_buffer(iv), 0):
                return None
        buffer, size = ct.create_string_buffer(data), uint(len(data))
        if not api.CryptDecrypt(secret, 0, True, 0, buffer, ct.byref(size)):
            return None
        return buffer.raw[: size.value]
    finally:
        if secret:
            api.CryptDestroyKey(secret)
        if provider:
            api.CryptReleaseContext(provider, 0)
