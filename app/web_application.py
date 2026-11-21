"""Static Phase-9 Web UI composed in front of the versioned REST API.

Construction and import are inert.  The wrapper serves only allowlisted,
frozen assets and delegates every ``/api/v1`` request unchanged to the Phase-8
runtime.  Static reads reuse the same Host/Origin policy as API reads.
"""

from app.web_assets import (
    asset_for_path,
    canonical_asset_path,
    etag_for_path,
    gzip_asset_for_path,
)
from services.rest_security import RestSecurityDenied, RestSecurityUnavailable


API_PREFIX = "/api/v1"
INDEX_PATH = "/index.html"
IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"
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
    __slots__ = (
        "status",
        "body",
        "content_type",
        "headers",
        "cache_control",
        "content_encoding",
    )

    def __init__(
        self,
        status,
        body,
        content_type,
        headers=None,
        cache_control="no-store",
        content_encoding=None,
    ):
        self.status = status
        self.body = body
        self.content_type = content_type
        self.headers = {} if headers is None else headers
        self.cache_control = cache_control
        self.content_encoding = content_encoding


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


def _quality(value):
    if value == "0" or value == "0.":
        return 0
    if value == "1" or value == "1.":
        return 1000
    if len(value) < 3 or value[1] != ".":
        return None
    digits = value[2:]
    if not digits or len(digits) > 3 or not digits.isdigit():
        return None
    if value[0] == "0":
        return int(digits) * (10 ** (3 - len(digits)))
    if value[0] == "1" and digits.strip("0") == "":
        return 1000
    return None


def _content_encoding(value):
    if type(value) is not str or not value.strip():
        return None
    qualities = {}
    for item in value.split(","):
        parts = item.split(";")
        coding = parts[0].strip().lower()
        if coding not in ("gzip", "identity", "*"):
            continue
        quality = 1000
        valid = True
        seen_quality = False
        for parameter in parts[1:]:
            parameter = parameter.strip()
            if not parameter or "=" not in parameter:
                continue
            name, parameter_value = parameter.split("=", 1)
            if name.strip().lower() != "q":
                continue
            if seen_quality:
                valid = False
                break
            seen_quality = True
            quality = _quality(parameter_value.strip())
            if quality is None:
                valid = False
                break
        if valid:
            previous = qualities.get(coding)
            if previous is None or quality > previous:
                qualities[coding] = quality
    wildcard = qualities.get("*")
    gzip_quality = qualities.get("gzip")
    if gzip_quality is None:
        gzip_quality = 0 if wildcard is None else wildcard
    identity_quality = qualities.get("identity")
    if identity_quality is None:
        identity_quality = 0 if wildcard == 0 else 1000
    if gzip_quality > 0 and gzip_quality >= identity_quality:
        return "gzip"
    if identity_quality > 0:
        return None
    return False


class Phase9WebApplication:
    """Serve frozen UI assets and preserve the existing REST boundary."""

    __slots__ = (
        "__rest_runtime",
        "__security",
        "__ap_address",
        "__captive_portal_enabled",
    )

    def __init__(
        self,
        rest_runtime,
        ap_address="192.168.4.1",
        captive_portal_enabled=True,
    ):
        handler = getattr(rest_runtime, "handle", None)
        security = getattr(rest_runtime, "security_policy", None)
        if not callable(handler):
            raise ValueError("rest_runtime must provide handle(request, peer_ip)")
        if not callable(getattr(security, "validate_read", None)):
            raise ValueError("rest_runtime must expose its read security policy")
        if type(captive_portal_enabled) is not bool:
            raise ValueError("captive_portal_enabled must be boolean")
        self.__rest_runtime = rest_runtime
        self.__security = security
        self.__ap_address = ap_address
        self.__captive_portal_enabled = captive_portal_enabled

    def handle(self, request, peer_ip, ingress=None, local_ip=None):
        path = getattr(request, "path", None)
        if (
            self.__captive_portal_enabled
            and ingress == "ap"
            and path in _CAPTIVE_PROBE_PATHS
            and getattr(request, "method", None) == "GET"
            and getattr(request, "body", None) == b""
            and request.headers.get("origin") is None
        ):
            return _text_response(
                302,
                "Open Landy Heater",
                {
                    "Location": "http://{}/".format(self.__ap_address),
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
        immutable = False
        canonical_path = path
        if type(path) is str and path.startswith("/assets/v-"):
            canonical_path = canonical_asset_path(path)
            if canonical_path is None:
                return _text_response(404, "Not found", _SECURITY_HEADERS)
            immutable = True
        asset = asset_for_path(canonical_path)
        if asset is None:
            return _text_response(404, "Not found", _SECURITY_HEADERS)
        content_encoding = _content_encoding(
            request.headers.get("accept-encoding")
        )
        if content_encoding is False:
            return _text_response(
                406,
                "No acceptable representation",
                {"Vary": "Accept-Encoding"},
            )
        if content_encoding == "gzip":
            asset = gzip_asset_for_path(canonical_path)
            if asset is None:
                return _text_response(503, "Service unavailable")
        content_type, payload = asset
        etag = etag_for_path(canonical_path, content_encoding)
        if type(etag) is not str:
            return _text_response(503, "Service unavailable")
        response_headers = dict(_SECURITY_HEADERS)
        response_headers["ETag"] = etag
        response_headers["Vary"] = "Accept-Encoding"
        cache_control = IMMUTABLE_CACHE_CONTROL if immutable else "no-cache"
        if request.headers.get("if-none-match") == etag:
            return WebResponse(
                304,
                b"",
                content_type,
                response_headers,
                cache_control,
                content_encoding,
            )
        return WebResponse(
            200,
            payload,
            content_type,
            response_headers,
            cache_control,
            content_encoding,
        )
