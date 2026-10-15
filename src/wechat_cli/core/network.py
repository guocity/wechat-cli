"""Small shared HTTP helpers using OS trust without changing global SSL behavior."""

import ssl
import urllib.request

import truststore


def ssl_context():
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None
