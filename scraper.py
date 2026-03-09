import re
import time
import threading
import requests
from bs4 import BeautifulSoup

# ── Rate Cache (60-second TTL) ───────────────────────────
_cache_lock = threading.Lock()
_cached_rates = None
_cache_time = 0
_CACHE_TTL = 60  # seconds


def get_cached_rates(force_refresh=False):
    """Return cached IBJA rates if fresh, otherwise scrape and cache."""
    global _cached_rates, _cache_time
    now = time.time()
    if not force_refresh and _cached_rates and (now - _cache_time) < _CACHE_TTL:
        return _cached_rates
    with _cache_lock:
        # Double-check after acquiring lock
        if not force_refresh and _cached_rates and (time.time() - _cache_time) < _CACHE_TTL:
            return _cached_rates
        rates = scrape_ibja_rates()
        _cached_rates = rates
        _cache_time = time.time()
        return rates

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
}


def scrape_ibjarates_750():
    """Scrape 750 purity gold rate (per gram) from https://ibjarates.com/

    Returns the 750 purity rate as an integer (₹ per gram).
    """
    resp = requests.get("https://ibjarates.com/", headers=_HEADERS, timeout=20)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    # Find "750 Purity" label → the rate number is adjacent
    for tag in soup.find_all(string=re.compile(r"750\s*Purity")):
        container = tag.find_parent()
        # Walk up to find the block that also contains the rate
        for _ in range(5):
            container = container.parent
            if container is None:
                break
            text = container.get_text()
            m = re.search(r"750\s*Purity\s*(\d[\d,]*)", text)
            if m:
                return int(m.group(1).replace(",", ""))

    raise ValueError("Could not find 750 purity rate on ibjarates.com")


def calculate_9kt_rate(fine_gold_999, kt18, purity_750):
    """Derive 9KT rate from IBJA Fine Gold 999, 18KT, and ibjarates 750 purity.

    Formula:
      premium  = 18KT (ibja.co) − 750 purity (ibjarates.com)
      base     = Fine Gold 999 (ibja.co) × 0.375
      9KT rate = round(base + premium)
    """
    premium = kt18 - purity_750
    base = fine_gold_999 * 0.375
    return round(base + premium)


def scrape_ibja_rates():
    """
    Scrape current gold rates from https://ibja.co/ and 750 purity from
    https://ibjarates.com/.

    Returns dict with 9kt, 14kt, 18kt, 20kt, 22kt rates (per gram),
    fine_gold (999), purity_750, session (AM/PM), and date.

    9KT = round(Fine Gold 999 × 0.375 + (18KT − 750 purity))
    """
    url = "https://ibja.co/"
    resp = requests.get(url, headers=_HEADERS, timeout=20)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    raw_text = soup.get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", raw_text)

    # Pull session/date near the "Retail selling Rates" label if available.
    context_match = re.search(r"Retail\s*selling\s*Rates.*?(\d{2}/\d{2}/\d{4}).*?\((AM|PM)\)", text, re.IGNORECASE)
    if not context_match:
        # Fallback: session/date may appear in reverse order on layout changes.
        context_match = re.search(r"Retail\s*selling\s*Rates.*?\((AM|PM)\).*?(\d{2}/\d{2}/\d{4})", text, re.IGNORECASE)

    session = "Unknown"
    rate_date = "Unknown"
    if context_match:
        g1, g2 = context_match.group(1), context_match.group(2)
        if re.fullmatch(r"\d{2}/\d{2}/\d{4}", g1):
            rate_date, session = g1, g2.upper()
        else:
            session, rate_date = g1.upper(), g2

    # Extract individual rates from the full normalized page text.
    def _extract(patterns):
        for pattern in patterns:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                return int(m.group(1).replace(",", ""))
        return None

    fine_gold = _extract([
        r"Fine\s*Gold\s*\(\s*999\s*\)\s*[:\-]?\s*₹?\s*(\d[\d,]+)",
        r"999\s*Fine\s*Gold\s*[:\-]?\s*₹?\s*(\d[\d,]+)",
    ])
    kt22 = _extract([r"22\s*KT\s*[:\-]?\s*₹?\s*(\d[\d,]+)"])
    kt20 = _extract([r"20\s*KT\s*[:\-]?\s*₹?\s*(\d[\d,]+)"])
    kt18 = _extract([r"18\s*KT\s*[:\-]?\s*₹?\s*(\d[\d,]+)"])
    kt14 = _extract([r"14\s*KT\s*[:\-]?\s*₹?\s*(\d[\d,]+)"])

    if kt14 is None or kt18 is None or fine_gold is None:
        raise ValueError(
            "Failed to extract required rates from IBJA page. "
            "The page structure may have changed."
        )

    # Scrape 750 purity from ibjarates.com
    purity_750 = scrape_ibjarates_750()

    # Calculate 9KT using the new formula
    kt9 = calculate_9kt_rate(fine_gold, kt18, purity_750)

    return {
        "fine_gold": fine_gold,
        "22kt": kt22,
        "20kt": kt20,
        "18kt": kt18,
        "14kt": kt14,
        "9kt": kt9,
        "purity_750": purity_750,
        "session": session,
        "date": rate_date,
    }


if __name__ == "__main__":
    rates = scrape_ibja_rates()
    print("IBJA Gold Rates:")
    for k, v in rates.items():
        print(f"  {k}: {v}")
    print(f"\n9KT formula: round({rates['fine_gold']} × 0.375 + ({rates['18kt']} − {rates['purity_750']}))")
    print(f"           = round({rates['fine_gold'] * 0.375} + {rates['18kt'] - rates['purity_750']})")
    print(f"           = {rates['9kt']}")
