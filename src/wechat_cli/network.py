"""Use macOS trust settings with uv-managed Python, without changing global SSL behavior."""

import ssl

import truststore


def ssl_context():
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
