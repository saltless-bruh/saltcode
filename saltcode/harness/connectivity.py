import httpx


def check_connectivity(timeout: float = 2.0) -> bool:
    """Probes internet connectivity by making lightweight HEAD requests to reliable services.

    Checks DeepSeek API and Google. Returns True if online, False if offline.
    """
    urls = [
        "https://api.deepseek.com",
        "https://www.google.com",
    ]

    for url in urls:
        try:
            # We use a HEAD request with a short timeout to check if we can connect
            response = httpx.head(url, timeout=timeout)
            # If we got any response (even if it's 401/404), the host is reachable
            if response.status_code:
                return True
        except httpx.RequestError:
            # Try next URL
            continue

    return False
