"""
services/user_agent.py — User-Agent parsing and caching
"""

_UA_CACHE: dict[str, tuple[str, str, str, str]] = {}


def _extract_version(fragment: str, token: str) -> str:
    start = fragment.find(token)
    if start == -1:
        return ""
    start += len(token)
    end = start
    while end < len(fragment) and (fragment[end].isdigit() or fragment[end] in {'.', '_'}):
        end += 1
    version = fragment[start:end].strip().strip('_')
    return version.replace('_', '.')


def parse_user_agent(ua: str | None) -> tuple[str, str, str, str]:
    """Parse a User-Agent string into (browser, browser_ver, os_name, device)."""
    key = ua or ""
    ua_lower = (ua or "").lower()
    cached = _UA_CACHE.get(key)
    if cached and ua_lower:
        _, _, cached_os, cached_device = cached
        iphone_hint = "iphone" in ua_lower or "ipod" in ua_lower
        ipad_hint = (
            "ipad" in ua_lower
            or ("macintosh" in ua_lower and "mobile/" in ua_lower and not iphone_hint)
        )
        cached_os_lower = (cached_os or "").lower()
        if (
            (iphone_hint and not cached_os_lower.startswith("ios"))
            or (ipad_hint and not (cached_os_lower.startswith("ipados") or cached_os_lower.startswith("ios")))
            or (iphone_hint and (cached_device or "").lower() != "phone")
            or (ipad_hint and (cached_device or "").lower() != "tablet")
        ):
            _UA_CACHE.pop(key, None)
        else:
            return cached

    browser = "Unknown"
    browser_ver = ""
    os_name = "Unknown"
    device = "Unknown"

    if not ua:
        result = (browser, browser_ver, os_name, device)
        _UA_CACHE[key] = result
        return result

    ua_plain = ua

    def set_browser(name, token):
        nonlocal browser, browser_ver
        browser = name
        version = _extract_version(ua_plain, token)
        if version:
            parts = version.split('.')
            if len(parts) > 2:
                version = '.'.join(parts[:2])
        browser_ver = version

    if "edg/" in ua_lower:
        set_browser("Edge", "Edg/")
    elif "edgio" in ua_lower:
        set_browser("Edge", "EdgiOS/")
    elif "crios/" in ua_lower:
        set_browser("Chrome", "CriOS/")
    elif "fxios/" in ua_lower:
        set_browser("Firefox", "FxiOS/")
    elif "opr/" in ua_lower or "opera" in ua_lower:
        set_browser("Opera", "OPR/")
    elif "samsungbrowser" in ua_lower:
        set_browser("Samsung Internet", "SamsungBrowser/")
    elif "chrome/" in ua_lower and "chromium" not in ua_lower:
        set_browser("Chrome", "Chrome/")
    elif "firefox/" in ua_lower:
        set_browser("Firefox", "Firefox/")
    elif "version/" in ua_lower and "safari/" in ua_lower:
        set_browser("Safari", "Version/")
    elif "safari" in ua_lower:
        set_browser("Safari", "Safari/")

    is_iphone = "iphone" in ua_lower or "ipod" in ua_lower
    is_ipad_keyword = "ipad" in ua_lower
    is_ipad_desktop_mode = (
        "macintosh" in ua_lower and "mobile/" in ua_lower and not is_iphone
    )
    is_ipad = is_ipad_keyword or is_ipad_desktop_mode

    if is_iphone:
        ver = _extract_version(ua_plain, "OS ")
        os_name = f"iOS {ver}" if ver else "iOS"
        device = "Phone"
    elif is_ipad:
        ver = _extract_version(ua_plain, "OS ")
        os_name = f"iPadOS {ver}" if ver else "iPadOS"
        device = "Tablet"
    elif "mac os x" in ua_lower or "macintosh" in ua_lower:
        ver = _extract_version(ua_plain, "Mac OS X ")
        os_name = f"macOS {ver}" if ver else "macOS"
        device = "Desktop"
    elif "ios" in ua_lower:
        ver = _extract_version(ua_plain, "OS ")
        os_name = f"iOS {ver}" if ver else "iOS"
        device = "Phone"
    elif "windows nt 11" in ua_lower:
        os_name = "Windows 11"
        device = "Desktop"
    elif "windows nt 10" in ua_lower:
        os_name = "Windows 10"
        device = "Desktop"
    elif "windows nt" in ua_lower:
        os_name = "Windows"
        device = "Desktop"
    elif "android" in ua_lower:
        ver = _extract_version(ua_plain, "Android ")
        os_name = f"Android {ver}" if ver else "Android"
        if "tablet" in ua_lower:
            device = "Tablet"
        else:
            device = "Phone"
    elif "linux" in ua_lower:
        os_name = "Linux"
        device = "Desktop"
    elif "bot" in ua_lower or "spider" in ua_lower:
        device = "Bot"

    if device == "Unknown":
        if "mobile" in ua_lower or "phone" in ua_lower:
            device = "Phone"
        elif "tablet" in ua_lower:
            device = "Tablet"
        elif any(keyword in ua_lower for keyword in ["windows", "mac os", "linux"]):
            device = "Desktop"
        elif "bot" in ua_lower or "spider" in ua_lower:
            device = "Bot"
        else:
            device = "Desktop"

    if is_iphone and not os_name.lower().startswith("ios"):
        ver = _extract_version(ua_plain, "OS ")
        os_name = f"iOS {ver}" if ver else "iOS"
        device = "Phone"
    elif is_ipad and not (os_name.lower().startswith("ipados") or os_name.lower().startswith("ios")):
        ver = _extract_version(ua_plain, "OS ")
        os_name = f"iPadOS {ver}" if ver else "iPadOS"
        device = "Tablet"

    result = (browser, browser_ver, os_name, device)
    if len(_UA_CACHE) > 5000:
        _UA_CACHE.clear()
    _UA_CACHE[key] = result
    return result
