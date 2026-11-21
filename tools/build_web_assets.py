"""Build the deterministic frozen Phase-11 asset module.

The source assets stay readable under ``web/``.  This tool converts them to
immutable byte constants so a later explicitly approved firmware build can
host the UI without a VFS upload or external dependency.
"""

import hashlib
import struct
import zlib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT = PROJECT_ROOT / "app" / "web_assets.py"
MAXIMUM_ASSET_BYTES = 16 * 1024
ASSET_MANIFEST_SCHEMA = b"landy-web-assets-v2"
ASSET_VERSION_PLACEHOLDER = b"__LANDY_ASSET_VERSION__"
EXPECTED_VERSION_REFERENCES = 10
ASSETS = (
    ("/index.html", "text/html; charset=utf-8", "index.html"),
    (
        "/assets/ui.css",
        "text/css; charset=utf-8",
        (
            "base.css",
            "components.css",
            "session.css",
            "setup.css",
            "diagnostics.css",
        ),
    ),
    ("/assets/diagnostics.html", "text/html; charset=utf-8", "diagnostics.html"),
    ("/assets/boot.js", "application/javascript; charset=utf-8", "boot.js"),
    ("/assets/i18n.js", "application/javascript; charset=utf-8", "i18n.js"),
    ("/assets/app.js", "application/javascript; charset=utf-8", "app.js"),
    ("/assets/home.js", "application/javascript; charset=utf-8", "home.js"),
    ("/assets/timers.js", "application/javascript; charset=utf-8", "timers.js"),
    ("/assets/settings.js", "application/javascript; charset=utf-8", "settings.js"),
    ("/assets/setup.js", "application/javascript; charset=utf-8", "setup.js"),
    ("/assets/diagnostics.js", "application/javascript; charset=utf-8", "diagnostics.js"),
)


def _source_rows():
    rows = []
    for route, content_type, relative_path in ASSETS:
        if isinstance(relative_path, tuple):
            payload = b"\n".join(
                (PROJECT_ROOT / "web" / item).read_bytes()
                for item in relative_path
            )
        else:
            payload = (PROJECT_ROOT / "web" / relative_path).read_bytes()
        if not payload or len(payload) > MAXIMUM_ASSET_BYTES:
            raise ValueError("web asset size is outside the frozen bound")
        rows.append((route, content_type, payload))
    if sum(
        payload.count(ASSET_VERSION_PLACEHOLDER)
        for route, content_type, payload in rows
    ) != EXPECTED_VERSION_REFERENCES:
        raise ValueError("web asset version references are incomplete")
    return rows


def _hash_field(digest, value):
    digest.update(len(value).to_bytes(8, "big"))
    digest.update(value)


def _asset_version(rows):
    digest = hashlib.sha256()
    _hash_field(digest, ASSET_MANIFEST_SCHEMA)
    for route, content_type, payload in rows:
        _hash_field(digest, route.encode("utf-8"))
        _hash_field(digest, content_type.encode("utf-8"))
        _hash_field(digest, payload)
    return digest.hexdigest()


def _gzip_bytes(payload):
    compressor = zlib.compressobj(
        level=9,
        method=zlib.DEFLATED,
        wbits=-zlib.MAX_WBITS,
    )
    compressed = compressor.compress(payload) + compressor.flush()
    header = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\xff"
    trailer = struct.pack(
        "<II", zlib.crc32(payload) & 0xFFFFFFFF, len(payload) & 0xFFFFFFFF
    )
    result = header + compressed + trailer
    if (
        not result
        or len(result) >= len(payload)
        or len(result) > MAXIMUM_ASSET_BYTES
        or zlib.decompress(result, 16 + zlib.MAX_WBITS) != payload
    ):
        raise ValueError("web asset gzip representation is invalid")
    return result


def render():
    source_rows = _source_rows()
    asset_version = _asset_version(source_rows)
    version_bytes = asset_version.encode("ascii")
    rows = []
    for route, content_type, template in source_rows:
        payload = template.replace(ASSET_VERSION_PLACEHOLDER, version_bytes)
        if ASSET_VERSION_PLACEHOLDER in payload:
            raise ValueError("web asset version placeholder was not replaced")
        gzip_payload = _gzip_bytes(payload)
        etag = '"{}"'.format(hashlib.sha256(payload).hexdigest())
        gzip_etag = '"{}"'.format(
            hashlib.sha256(gzip_payload).hexdigest()
        )
        rows.append(
            (route, content_type, payload, gzip_payload, etag, gzip_etag)
        )
    versioned_prefix = "/assets/v-{}/".format(asset_version)
    lines = [
        '"""Generated immutable Phase-11 Web-UI assets; do not edit by hand."""',
        "",
        "ASSET_VERSION = {!r}".format(asset_version),
        "VERSIONED_ASSET_PREFIX = {!r}".format(versioned_prefix),
        "",
        "ASSETS = (",
    ]
    for route, content_type, payload, gzip_payload, etag, gzip_etag in rows:
        lines.append("    ({!r}, {!r}, {!r}),".format(
            route, content_type, payload
        ))
    lines.extend((
        ")",
        "",
        "GZIP_ASSETS = (",
    ))
    for route, content_type, payload, gzip_payload, etag, gzip_etag in rows:
        lines.append("    ({!r}, {!r}, {!r}),".format(
            route, content_type, gzip_payload
        ))
    lines.extend((
        ")",
        "",
        "ASSET_ETAGS = (",
    ))
    for route, content_type, payload, gzip_payload, etag, gzip_etag in rows:
        lines.append(
            "    ({!r}, {!r}, {!r}),".format(route, etag, gzip_etag)
        )
    lines.extend((
        ")",
        "",
        "",
        "def asset_for_path(path):",
        "    for route, content_type, payload in ASSETS:",
        "        if path == route:",
        "            return content_type, payload",
        "    return None",
        "",
        "",
        "def gzip_asset_for_path(path):",
        "    for route, content_type, payload in GZIP_ASSETS:",
        "        if path == route:",
        "            return content_type, payload",
        "    return None",
        "",
        "",
        "def etag_for_path(path, content_encoding=None):",
        "    for route, identity_etag, gzip_etag in ASSET_ETAGS:",
        "        if path == route:",
        "            if content_encoding is None:",
        "                return identity_etag",
        "            if content_encoding == 'gzip':",
        "                return gzip_etag",
        "            return None",
        "    return None",
        "",
        "",
        "def versioned_asset_path(path):",
        "    for route, content_type, payload in ASSETS:",
        "        if path == route and route.startswith('/assets/'):",
        "            return VERSIONED_ASSET_PREFIX + route[len('/assets/'):]",
        "    return None",
        "",
        "",
        "def canonical_asset_path(path):",
        "    if type(path) is not str or not path.startswith(VERSIONED_ASSET_PREFIX):",
        "        return None",
        "    candidate = '/assets/' + path[len(VERSIONED_ASSET_PREFIX):]",
        "    for route, content_type, payload in ASSETS:",
        "        if route == candidate and route.startswith('/assets/'):",
        "            return candidate",
        "    return None",
        "",
    ))
    return "\n".join(lines).encode("utf-8")


def main():
    OUTPUT.write_bytes(render())


if __name__ == "__main__":
    main()
