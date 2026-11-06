"""Small HTTP helper shared by the free data sources (no API keys involved)."""

from __future__ import annotations

import logging
import time

import requests

logger = logging.getLogger(__name__)

# Some public endpoints reject unknown clients, and FRED does the opposite and stalls
# on browser-looking clients, so each source picks the identity that it responds to.
BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
PLAIN_UA = "market-signals-dashboard/1.0"


class FetchError(RuntimeError):
    """A source could not be reached or answered with an unexpected status."""


class NotFound(FetchError):
    """The source answered, and it has nothing for this request."""


def fetch(
    url: str,
    *,
    browser: bool = True,
    timeout: float = 30,
    retries: int = 2,
    not_found: tuple[int, ...] = (404,),
) -> requests.Response:
    headers = {"User-Agent": BROWSER_UA if browser else PLAIN_UA, "Accept": "*/*"}
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            last_error = exc
        else:
            if response.status_code == 200:
                return response
            if response.status_code in not_found:
                raise NotFound(f"{url} returned {response.status_code}")
            last_error = FetchError(f"{url} returned {response.status_code}")
            if response.status_code < 500 and response.status_code != 429:
                break
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    raise FetchError(str(last_error))
