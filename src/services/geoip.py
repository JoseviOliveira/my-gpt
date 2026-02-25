"""
services/geoip.py — GeoIP lookup and IP classification
"""
import logging
import pathlib
import ipaddress

try:
    import geoip2.database
except Exception:
    geoip2 = None

from src.core.config import GEOIP_DB_PATH

_geoip_reader = None
_geoip_cache: dict[str, str] = {}


def init_geoip_reader():
    """Initialize the GeoIP database reader."""
    global _geoip_reader
    if not GEOIP_DB_PATH or not geoip2:
        return
    db_path = pathlib.Path(GEOIP_DB_PATH)
    if not db_path.exists():
        logging.getLogger(__name__).warning("[geoip] database not found at %s", db_path)
        return
    try:
        _geoip_reader = geoip2.database.Reader(str(db_path))
        logging.getLogger(__name__).info("[geoip] loaded %s", db_path)
    except Exception as exc:
        logging.getLogger(__name__).warning("[geoip] failed to load %s: %s", db_path, exc)


def lookup_country(ip: str) -> str:
    """Look up the country code for an IP address."""
    if not ip:
        return ""
    if _geoip_reader is None:
        logging.getLogger(__name__).debug("[geoip] reader unavailable; set GEOIP_DB to enable country lookup")
        return ""
    if ip in _geoip_cache:
        return _geoip_cache[ip]
    try:
        response = _geoip_reader.country(ip)
        country = (response.country.iso_code or "").upper()
    except Exception:
        country = ""
    if len(_geoip_cache) > 5000:
        _geoip_cache.clear()
    _geoip_cache[ip] = country
    logging.getLogger(__name__).debug("[geoip] lookup ip=%s country=%s", ip, country or "-")
    return country


def is_private_ip(ip: str) -> bool:
    """Check if an IP address is private/local."""
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.is_private or addr.is_loopback or addr.is_link_local


def country_override_for_ip(ip: str) -> str | None:
    """Return 'LOCAL' for private IPs, None otherwise."""
    if not ip:
        return None
    if is_private_ip(ip):
        return "LOCAL"
    return None


def resolve_country(ip: str) -> str:
    """Resolve country for an IP, with LOCAL override for private IPs."""
    override = country_override_for_ip(ip)
    if override is not None:
        return override
    return lookup_country(ip)


# Initialize on module load
init_geoip_reader()
