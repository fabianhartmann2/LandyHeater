"""Small hardware-free schema boundary for Phase-7 Wi-Fi configuration."""


NETWORK_HOSTNAME = "heater"
NETWORK_MDNS_NAME = "heater.local"
NETWORK_AP_SSID = "Landy Heater"

MAX_KNOWN_NETWORKS = 8
MAX_NETWORK_ID_BYTES = 32
MAX_SSID_BYTES = 32
MIN_WIFI_PASSWORD_BYTES = 8
MAX_WIFI_PASSWORD_BYTES = 63
MAX_STA_PSK_BYTES = 64

_NETWORK_FIELDS = frozenset(("hostname", "access_point", "known_networks"))
_ACCESS_POINT_FIELDS = frozenset(("ssid", "password"))
_KNOWN_NETWORK_FIELDS = frozenset(("id", "ssid", "password"))


def _require_exact_dict(name, value, fields):
    if type(value) is not dict or frozenset(value) != fields:
        raise ValueError("{} has an invalid shape".format(name))
    for key in value:
        if type(key) is not str:
            raise ValueError("{} keys must be strings".format(name))
    return value


def _bounded_utf8(name, value, maximum, strip=False):
    if type(value) is not str:
        raise ValueError("{} must be a string".format(name))
    # Reject obviously unbounded input before strip/encode allocations.  The
    # UTF-8 byte check below remains authoritative for non-ASCII text.
    if len(value) > maximum:
        raise ValueError("{} exceeds its character bound".format(name))
    if strip:
        value = value.strip()
    if not value:
        raise ValueError("{} must not be empty".format(name))
    try:
        encoded = value.encode("utf-8")
    except (UnicodeError, ValueError):
        raise ValueError("{} must be UTF-8 encodable".format(name))
    if len(encoded) > maximum or b"\x00" in encoded:
        raise ValueError("{} exceeds its byte bound".format(name))
    return value


def _printable_ascii_password(name, value, allow_open, station=False):
    if value is None:
        if allow_open:
            return None
        raise ValueError("{} is required".format(name))
    if type(value) is not str:
        raise ValueError("{} must be a string".format(name))
    if len(value) > MAX_STA_PSK_BYTES:
        raise ValueError("{} has an invalid length".format(name))
    try:
        encoded = value.encode("ascii")
    except (UnicodeError, ValueError):
        raise ValueError("{} must use printable ASCII".format(name))
    length = len(encoded)
    valid_length = MIN_WIFI_PASSWORD_BYTES <= length <= MAX_WIFI_PASSWORD_BYTES
    if station and length == MAX_STA_PSK_BYTES:
        valid_length = all(
            character in "0123456789abcdefABCDEF" for character in value
        )
    if not valid_length:
        raise ValueError("{} has an invalid length".format(name))
    for byte in encoded:
        if byte < 32 or byte > 126:
            raise ValueError("{} must use printable ASCII".format(name))
    return value


def default_network_configuration():
    """Return unprovisioned defaults without a universal AP secret."""

    return {
        "hostname": NETWORK_HOSTNAME,
        "access_point": {
            "ssid": NETWORK_AP_SSID,
            "password": None,
        },
        "known_networks": [],
    }


def validate_network_configuration(candidate, require_ap_password=False):
    """Return a detached, canonical and bounded network configuration."""

    candidate = _require_exact_dict(
        "network configuration", candidate, _NETWORK_FIELDS
    )
    hostname = _bounded_utf8(
        "network hostname", candidate["hostname"], 32, True
    )
    if hostname != NETWORK_HOSTNAME:
        raise ValueError("network hostname must be heater")
    access_point = _require_exact_dict(
        "access point", candidate["access_point"], _ACCESS_POINT_FIELDS
    )
    ssid = _bounded_utf8(
        "access point SSID", access_point["ssid"], MAX_SSID_BYTES
    )
    if ssid != NETWORK_AP_SSID:
        raise ValueError("access point SSID must be Landy Heater")
    ap_password = access_point["password"]
    if ap_password is not None or require_ap_password:
        ap_password = _printable_ascii_password(
            "access point password", ap_password, False
        )

    profiles = candidate["known_networks"]
    if type(profiles) is not list or len(profiles) > MAX_KNOWN_NETWORKS:
        raise ValueError("known networks must be a bounded list")
    normalized = []
    profile_ids = set()
    ssids = set()
    for profile in profiles:
        profile = _require_exact_dict(
            "known network", profile, _KNOWN_NETWORK_FIELDS
        )
        profile_id = _bounded_utf8(
            "network profile id", profile["id"], MAX_NETWORK_ID_BYTES, True
        )
        if "|" in profile_id:
            raise ValueError("network profile id contains a reserved delimiter")
        profile_ssid = _bounded_utf8(
            "station SSID", profile["ssid"], MAX_SSID_BYTES
        )
        password = _printable_ascii_password(
            "station password", profile["password"], True, True
        )
        if profile_id in profile_ids:
            raise ValueError("network profile ids must be unique")
        if profile_ssid in ssids:
            raise ValueError("station SSIDs must be unique")
        profile_ids.add(profile_id)
        ssids.add(profile_ssid)
        normalized.append({
            "id": profile_id,
            "ssid": profile_ssid,
            "password": password,
        })
    return {
        "hostname": hostname,
        "access_point": {"ssid": ssid, "password": ap_password},
        "known_networks": normalized,
    }
