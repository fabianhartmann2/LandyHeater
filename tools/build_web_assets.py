"""Build the deterministic frozen Phase-11 asset module.

The source assets stay readable under ``web/``.  This tool converts them to
immutable byte constants so a later explicitly approved firmware build can
host the UI without a VFS upload or external dependency.
"""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT = PROJECT_ROOT / "app" / "web_assets.py"
MAXIMUM_ASSET_BYTES = 16 * 1024
ASSETS = (
    ("/index.html", "text/html; charset=utf-8", "index.html"),
    ("/assets/base.css", "text/css; charset=utf-8", "base.css"),
    ("/assets/components.css", "text/css; charset=utf-8", "components.css"),
    ("/assets/session.css", "text/css; charset=utf-8", "session.css"),
    ("/assets/setup.css", "text/css; charset=utf-8", "setup.css"),
    ("/assets/diagnostics.css", "text/css; charset=utf-8", "diagnostics.css"),
    ("/assets/diagnostics.html", "text/html; charset=utf-8", "diagnostics.html"),
    ("/assets/i18n.js", "application/javascript; charset=utf-8", "i18n.js"),
    ("/assets/app.js", "application/javascript; charset=utf-8", "app.js"),
    ("/assets/home.js", "application/javascript; charset=utf-8", "home.js"),
    ("/assets/timers.js", "application/javascript; charset=utf-8", "timers.js"),
    ("/assets/settings.js", "application/javascript; charset=utf-8", "settings.js"),
    ("/assets/setup.js", "application/javascript; charset=utf-8", "setup.js"),
    ("/assets/diagnostics.js", "application/javascript; charset=utf-8", "diagnostics.js"),
)


def render():
    rows = []
    for route, content_type, relative_path in ASSETS:
        payload = (PROJECT_ROOT / "web" / relative_path).read_bytes()
        if not payload or len(payload) > MAXIMUM_ASSET_BYTES:
            raise ValueError("web asset size is outside the frozen bound")
        rows.append((route, content_type, payload))
    lines = [
        '"""Generated immutable Phase-11 Web-UI assets; do not edit by hand."""',
        "",
        "ASSETS = (",
    ]
    for route, content_type, payload in rows:
        lines.append("    ({!r}, {!r}, {!r}),".format(
            route, content_type, payload
        ))
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
    ))
    return "\n".join(lines).encode("utf-8")


def main():
    OUTPUT.write_bytes(render())


if __name__ == "__main__":
    main()
