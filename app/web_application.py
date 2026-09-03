"""Static Phase-9 Web UI composed in front of the versioned REST API.

Construction and import are inert.  The wrapper serves only allowlisted,
frozen assets and delegates every ``/api/v1`` request unchanged to the Phase-8
runtime.  Static reads reuse the same Host/Origin policy as API reads.
"""

from app.web_assets import asset_for_path
from services.rest_security import RestSecurityDenied, RestSecurityUnavailable


API_PREFIX = "/api/v1"
INDEX_PATH = "/index.html"
_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "connect-src 'self'; img-src 'self' data:; object-src 'none'; "
        "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}
_CAPTIVE_PROBE_PATHS = (
    "/generate_204",
    "/gen_204",
    "/hotspot-detect.html",
    "/library/test/success.html",
    "/connecttest.txt",
    "/ncsi.txt",
    "/canonical.html",
    "/success.txt",
)


class WebResponse:
    __slots__ = ("status", "body", "content_type", "headers")

    def __init__(self, status, body, content_type, headers=None):
        self.status = status
        self.body = body
        self.content_type = content_type
        self.headers = {} if headers is None else headers


def _text_response(status, message, headers=None):
    response_headers = dict(_SECURITY_HEADERS)
    if headers is not None:
        response_headers.update(headers)
    return WebResponse(
        status,
        message.encode("utf-8"),
        "text/plain; charset=utf-8",
        response_headers,
    )


class Phase9WebApplication:
    """Serve frozen UI assets and preserve the existing REST boundary."""

    __slots__ = ("__rest_runtime", "__security", "__ap_address")

    def __init__(self, rest_runtime, ap_address="192.168.4.1"):
        handler = getattr(rest_runtime, "handle", None)
        security = getattr(rest_runtime, "security_policy", None)
        if not callable(handler):
            raise ValueError("rest_runtime must provide handle(request, peer_ip)")
        if not callable(getattr(security, "validate_read", None)):
            raise ValueError("rest_runtime must expose its read security policy")
        self.__rest_runtime = rest_runtime
        self.__security = security
        self.__ap_address = ap_address

    def handle(self, request, peer_ip, ingress=None, local_ip=None):
        path = getattr(request, "path", None)
        if (
            ingress == "ap"
            and path in _CAPTIVE_PROBE_PATHS
            and getattr(request, "method", None) == "GET"
            and getattr(request, "query", None) is None
            and getattr(request, "body", None) == b""
            and request.headers.get("origin") is None
        ):
            return _text_response(
                302,
                "Open Landy Heater",
                {
                    "Location": "http://{}/".format(self.__ap_address),
                    "Cache-Control": "no-store",
                },
            )
        if path == API_PREFIX or (
            type(path) is str and path.startswith(API_PREFIX + "/")
        ):
            if ingress is None and local_ip is None:
                return self.__rest_runtime.handle(request, peer_ip)
            return self.__rest_runtime.handle(
                request, peer_ip, ingress, local_ip
            )
        try:
            if ingress is None and local_ip is None:
                self.__security.validate_read(request.headers)
            else:
                self.__security.validate_read(
                    request.headers, ingress, local_ip
                )
        except RestSecurityDenied:
            return _text_response(403, "Request denied")
        except RestSecurityUnavailable:
            return _text_response(503, "Service unavailable")
        if getattr(request, "query", None) is not None:
            return _text_response(400, "Query is not supported")
        if getattr(request, "method", None) != "GET":
            return _text_response(
                405, "Method not allowed", {"Allow": "GET"}
            )
        if path == "/":
            path = INDEX_PATH
        if path == "/favicon.ico":
            return WebResponse(
                204, b"", "text/plain; charset=utf-8", _SECURITY_HEADERS
            )
        asset = asset_for_path(path)
        if asset is None:
            return _text_response(404, "Not found", _SECURITY_HEADERS)
        content_type, payload = asset
        return WebResponse(
            200, payload, content_type, _SECURITY_HEADERS
        )
