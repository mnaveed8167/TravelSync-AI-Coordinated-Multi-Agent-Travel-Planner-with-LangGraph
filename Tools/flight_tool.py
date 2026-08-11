"""
flight_tool.py
================
Resolves natural-language travel queries ("Plan a 7 day New Zealand trip from
Islamabad") into IATA airport codes, then queries the AviationStack
`/flights` endpoint for live/scheduled flight data on that route.

IMPORTANT LIMITATION (read this before assuming "no results" = "bug"):
AviationStack's free `/flights` endpoint only returns flights that are
CURRENTLY active or scheduled *right now* for the exact departure/arrival
airport pair you ask for. It is NOT a historical or "does this route exist"
database. Two consequences:

  1. Most airport pairs will have zero live nonstop flights at any given
     moment -- especially routes between secondary cities that have no
     nonstop service in reality (e.g. Islamabad -> Auckland has no nonstop
     flight at all; you'd need a connection).
  2. An empty `data` list from the API is often the CORRECT answer, not a
     parsing failure.

This module compensates by progressively broadening the search (full route
-> departure only -> arrival only -> tell the user clearly) instead of just
reporting failure once.
"""

import os
import re
import logging
from typing import Optional, Tuple, List

import certifi
import airportsdata
import pycountry
import requests
from dotenv import load_dotenv

# --------------------------------------------------------------------------
# Setup
# --------------------------------------------------------------------------

load_dotenv()

# Make sure requests uses a modern CA bundle (avoids SSL errors on some
# Windows/conda setups).
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("flight_tool")

API_KEY = os.getenv("AVIATIONSTACK_API_KEY")

# Used when the user only names a destination (e.g. "Pakistan trip").
DEFAULT_ORIGIN_IATA = os.getenv("DEFAULT_ORIGIN_IATA", "ISB")

# AviationStack's free tier only works over plain HTTP, not HTTPS.
BASE_URL = "http://api.aviationstack.com/v1/flights"

# Full IATA airport database, keyed by IATA code.
AIRPORTS = airportsdata.load("IATA")


# --------------------------------------------------------------------------
# Lookup tables
# --------------------------------------------------------------------------

COUNTRY_ALIASES = {
    # Common abbreviations
    "usa": "United States",
    "us": "United States",
    "uk": "United Kingdom",
    "au": "Australia",
    "pak": "Pakistan",
    "uae": "United Arab Emirates",
    "ksa": "Saudi Arabia",
    "fr": "France",
    "de": "Germany",
    "in": "India",
    "ca": "Canada",
    "jp": "Japan",
    "br": "Brazil",
    "ru": "Russia",
    "es": "Spain",
    "it": "Italy",
    "nl": "Netherlands",
    "ch": "Switzerland",
    "se": "Sweden",
    "no": "Norway",
    "fi": "Finland",
    "dk": "Denmark",
    "be": "Belgium",
    "at": "Austria",
    "ie": "Ireland",
    "pt": "Portugal",
    "gr": "Greece",
    "pl": "Poland",

    # Frequently misspelled / alternate country names
    "new zealand": "New Zealand",
    "newzeland": "New Zealand",
    "nz": "New Zealand",

    "saudi": "Saudi Arabia",
    "america": "United States",
    "england": "United Kingdom",
    "ua emirates": "United Arab Emirates",
}

COUNTRY_MAIN_AIRPORTS = {
    # ISO Alpha-2 Code -> Main Airport
    "US": "JFK", "GB": "LHR", "AU": "SYD", "PK": "ISB", "AE": "DXB",
    "SA": "RUH", "FR": "CDG", "DE": "FRA", "IN": "DEL", "CA": "YYZ",
    "JP": "NRT", "BR": "GRU", "RU": "SVO", "ES": "MAD", "IT": "FCO",
    "NL": "AMS", "CH": "ZRH", "SE": "ARN", "NO": "OSL", "FI": "HEL",
    "DK": "CPH", "BE": "BRU", "AT": "VIE", "IE": "DUB", "PT": "LIS",
    "GR": "ATH", "PL": "WAW", "NZ": "AKL", "CN": "PEK", "KR": "ICN",
    "SG": "SIN", "TH": "BKK", "MY": "KUL", "TR": "IST", "QA": "DOH",
    "EG": "CAI", "ZA": "JNB",
}

CITY_MAIN_AIRPORTS = {
    # Pakistan
    "islamabad": "ISB", "lahore": "LHE", "karachi": "KHI", "peshawar": "PEW",
    "quetta": "UET", "multan": "MUX", "faisalabad": "LYP", "sialkot": "SKT",
    # New Zealand
    "auckland": "AKL", "wellington": "WLG", "christchurch": "CHC",
    "queenstown": "ZQN",
    # Australia
    "sydney": "SYD", "melbourne": "MEL", "brisbane": "BNE", "perth": "PER",
    "adelaide": "ADL", "canberra": "CBR",
    # United States
    "new york": "JFK", "los angeles": "LAX", "chicago": "ORD",
    "san francisco": "SFO", "washington": "IAD", "miami": "MIA",
    "dallas": "DFW", "las vegas": "LAS", "seattle": "SEA", "boston": "BOS",
    # Canada
    "toronto": "YYZ", "vancouver": "YVR", "montreal": "YUL",
    "calgary": "YYC", "ottawa": "YOW",
    # United Kingdom
    "london": "LHR", "manchester": "MAN", "birmingham": "BHX",
    "edinburgh": "EDI", "glasgow": "GLA",
    # France
    "paris": "CDG", "nice": "NCE", "lyon": "LYS",
    # Germany
    "frankfurt": "FRA", "berlin": "BER", "munich": "MUC", "hamburg": "HAM",
    # Italy
    "rome": "FCO", "milan": "MXP", "venice": "VCE",
    # Spain
    "madrid": "MAD", "barcelona": "BCN", "valencia": "VLC",
    # Netherlands
    "amsterdam": "AMS", "rotterdam": "RTM",
    # Switzerland
    "zurich": "ZRH", "geneva": "GVA",
    # Austria
    "vienna": "VIE",
    # Belgium
    "brussels": "BRU",
    # Denmark
    "copenhagen": "CPH",
    # Sweden
    "stockholm": "ARN",
    # Norway
    "oslo": "OSL",
    # Finland
    "helsinki": "HEL",
    # Ireland
    "dublin": "DUB",
    # Portugal
    "lisbon": "LIS", "porto": "OPO",
    # Greece
    "athens": "ATH",
    # Poland
    "warsaw": "WAW", "krakow": "KRK",
    # UAE
    "dubai": "DXB", "abu dhabi": "AUH", "sharjah": "SHJ",
    # Saudi Arabia
    "riyadh": "RUH", "jeddah": "JED", "madinah": "MED", "mecca": "JED",
    # India
    "delhi": "DEL", "mumbai": "BOM", "bangalore": "BLR", "chennai": "MAA",
    "kolkata": "CCU", "hyderabad": "HYD",
    # Japan
    "tokyo": "NRT", "osaka": "KIX", "kyoto": "KIX", "nagoya": "NGO",
    "sapporo": "CTS",
    # China
    "beijing": "PEK", "shanghai": "PVG", "guangzhou": "CAN",
    "shenzhen": "SZX",
    # South Korea
    "seoul": "ICN", "busan": "PUS",
    # Singapore
    "singapore": "SIN",
    # Thailand
    "bangkok": "BKK", "phuket": "HKT", "chiang mai": "CNX",
    # Malaysia
    "kuala lumpur": "KUL",
    # Turkey
    "istanbul": "IST", "ankara": "ESB",
    # Qatar
    "doha": "DOH",
    # Egypt
    "cairo": "CAI",
    # South Africa
    "johannesburg": "JNB", "cape town": "CPT",
    # Brazil
    "sao paulo": "GRU", "rio de janeiro": "GIG",
    # Russia
    "moscow": "SVO", "saint petersburg": "LED",
}

# Words that add no locational information but commonly surround a location
# mention in a travel query ("flight to Paris" -> "paris").
STOPWORDS = {
    "flight", "flights", "trip", "trips", "travel", "travels", "booking",
    "bookings", "ticket", "tickets", "airline", "airlines", "airport",
    "airports", "plane", "planes", "departure", "arrivals", "arrival",
    "from", "to", "in", "on", "at", "the", "a", "an", "and", "or", "of",
    "for", "with", "by",
}


# --------------------------------------------------------------------------
# Text / location resolution helpers
# --------------------------------------------------------------------------

def clean_text(text: str) -> str:
    """Lowercase, strip punctuation, and remove filler stopwords."""
    text = text.lower().strip()
    text = re.sub(r"[^a-zA-Z0-9\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    words = [w for w in text.split() if w not in STOPWORDS]
    return " ".join(words).strip()


def country_name_to_code(country_name: str) -> Optional[str]:
    """Convert a (possibly messy) country name/alias to its ISO alpha-2 code."""
    text = clean_text(country_name)
    if not text:
        return None

    if text in COUNTRY_ALIASES:
        text = clean_text(COUNTRY_ALIASES[text])

    try:
        return pycountry.countries.lookup(text).alpha_2
    except LookupError:
        pass

    # Substring fallback: does any known country name appear inside the text?
    for country in pycountry.countries:
        if country.name.lower() in text:
            return country.alpha_2

    return None


def get_best_airport_for_country(country_code: str) -> Optional[str]:
    """Return the best-guess "main" IATA airport for a country code."""
    preferred = COUNTRY_MAIN_AIRPORTS.get(country_code)
    if preferred and preferred in AIRPORTS:
        return preferred

    candidates: List[Tuple[int, str]] = []
    for iata, airport in AIRPORTS.items():
        if not iata or airport.get("country") != country_code:
            continue

        name = str(airport.get("name", "")).lower()
        score = 0
        if "international" in name:
            score += 50
        if "intl" in name:
            score += 40
        if "capital" in name:
            score += 20
        if airport.get("city"):
            score += 5

        candidates.append((score, iata))

    if not candidates:
        return None

    candidates.sort(reverse=True)
    return candidates[0][1]


def resolve_location_to_iata(location: str) -> Optional[str]:
    """
    Resolve free-text (a city, country, or raw IATA code) to a single IATA
    airport code. Tries, in order: literal 3-letter code -> known city ->
    known country -> fuzzy match against the full airport database.
    """
    if not location:
        return None

    raw_location = location.strip()

    # Already a valid IATA code?
    if re.fullmatch(r"[A-Za-z]{3}", raw_location):
        code = raw_location.upper()
        if code in AIRPORTS:
            return code

    location_clean = clean_text(raw_location)
    if not location_clean:
        return None

    if location_clean in CITY_MAIN_AIRPORTS:
        return CITY_MAIN_AIRPORTS[location_clean]

    country_code = country_name_to_code(location_clean)
    if country_code:
        airport = get_best_airport_for_country(country_code)
        if airport:
            return airport

    # Last resort: fuzzy-match against the whole airport database by city
    # or airport name.
    city_matches: List[Tuple[int, str]] = []
    for iata, airport in AIRPORTS.items():
        city = str(airport.get("city", "")).lower().strip()
        name = str(airport.get("name", "")).lower().strip()

        score = 0
        if city == location_clean:
            score += 100
        elif location_clean in city:
            score += 70
        if location_clean in name:
            score += 50
        if "international" in name:
            score += 10

        if score > 0:
            city_matches.append((score, iata))

    if city_matches:
        city_matches.sort(reverse=True)
        return city_matches[0][1]

    return None


def find_location_mentions(query_lower: str) -> List[str]:
    """
    Find every country-alias / country-name / known-city mention in a query,
    ordered by where they first appear in the text (left to right). Order
    matters downstream: it's used to infer "origin mentioned before
    destination" when no explicit from/to wording is present.
    """
    positions = {}  # mention text -> earliest character index found

    def record(term: str, idx: int):
        if term not in positions or idx < positions[term]:
            positions[term] = idx

    for alias in COUNTRY_ALIASES:
        m = re.search(rf"\b{re.escape(alias)}\b", query_lower)
        if m:
            record(alias, m.start())

    for country in pycountry.countries:
        name = country.name.lower()
        if len(name) >= 4:
            m = re.search(rf"\b{re.escape(name)}\b", query_lower)
            if m:
                record(name, m.start())

    for city in CITY_MAIN_AIRPORTS:
        m = re.search(rf"\b{re.escape(city)}\b", query_lower)
        if m:
            record(city, m.start())

    # Sort mentions by their position in the original text.
    return [term for term, _ in sorted(positions.items(), key=lambda kv: kv[1])]


# --------------------------------------------------------------------------
# Route parsing
# --------------------------------------------------------------------------

GLOBAL_KEYWORDS = [
    "global", "worldwide", "all countries", "all flights", "global flights",
    "golbal flight", "all country", "international flights",
    "international flight", "world flights", "world flight",
    "worldwide flights", "worldwide flight",
]


def parse_route(query: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Parse a natural-language query into (departure_iata, arrival_iata).

    Return values:
        (None, None)  -> global/unfiltered live flights
        (DEP, ARR)     -> a specific route
        (DEP, None)    -> all flights departing DEP
        (None, ARR)    -> all flights arriving at ARR
    """
    q_lower = query.strip().lower()

    if any(keyword in q_lower for keyword in GLOBAL_KEYWORDS):
        return None, None

    # Two raw IATA codes typed directly, e.g. "ISB to NRT".
    codes = re.findall(r"\b([A-Za-z]{3})\b", query)
    if len(codes) >= 2:
        return codes[0].upper(), codes[1].upper()

    # "from X to Y"
    match = re.search(r"from\s+([a-zA-Z\s]+?)\s+to\s+([a-zA-Z\s]+)", q_lower)
    if match:
        dep = resolve_location_to_iata(match.group(1).strip())
        arr = resolve_location_to_iata(match.group(2).strip())
        return dep, arr

    # "to Y from X"
    match = re.search(r"to\s+([a-zA-Z\s]+?)\s+from\s+([a-zA-Z\s]+)", q_lower)
    if match:
        arr = resolve_location_to_iata(match.group(1).strip())
        dep = resolve_location_to_iata(match.group(2).strip())
        return dep, arr

    # "flight(s) from X" (no destination given)
    match = re.search(r"flights?\s+from\s+([a-zA-Z\s]+)", q_lower)
    if match:
        return resolve_location_to_iata(match.group(1).strip()), None

    # "flight(s) to Y" (no origin given)
    match = re.search(r"flights?\s+to\s+([a-zA-Z\s]+)", q_lower)
    if match:
        return None, resolve_location_to_iata(match.group(1).strip())

    # ---- Fallback: no clean from/to pattern matched. Scan for any known
    # ---- location names anywhere in the query and infer roles.
    mentions = find_location_mentions(q_lower)
    logger.debug("Location mentions found: %s", mentions)

    if not mentions:
        return None, None

    from_match = re.search(r"from\s+([a-zA-Z\s,]+)", q_lower)
    if from_match:
        origin_text = from_match.group(1)
        dep_iata = resolve_location_to_iata(origin_text)

        # The destination is whichever mention is NOT inside the "from ..."
        # phrase (e.g. "... NewZeland trip from Islamabad, Pakistan" ->
        # origin_text = "islamabad, pakistan", so "newzeland" is the arrival).
        for item in mentions:
            if item not in origin_text:
                arr_iata = resolve_location_to_iata(item)
                if arr_iata:
                    return dep_iata, arr_iata

        return dep_iata, None

    # No "from" at all: assume the first mention (by position in the text)
    # is the origin and the second is the destination.
    if len(mentions) >= 2:
        return (
            resolve_location_to_iata(mentions[0]),
            resolve_location_to_iata(mentions[1]),
        )

    # Only one location mentioned with no from/to context -- treat it as
    # the destination and let the caller decide whether to assume a default
    # origin (see DEFAULT_ORIGIN_IATA).
    return None, resolve_location_to_iata(mentions[0])


# --------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------

def format_flight(flight: dict) -> str:
    """Format a single AviationStack flight record as human-readable text."""
    airline = flight.get("airline", {}).get("name", "Unknown Airline")
    flight_number = flight.get("flight", {}).get("iata", "Unknown Flight Number")
    departure = flight.get("departure", {})
    arrival = flight.get("arrival", {})
    status = flight.get("flight_status", "Unknown Status")

    # AviationStack often returns these keys present but explicitly `null`
    # (e.g. arrival time isn't known yet for a not-yet-landed flight), so
    # `.get(key, default)` alone won't catch it -- `or` is needed too.
    dep_time = departure.get("estimated") or "Not yet available"
    arr_time = arrival.get("estimated") or "Not yet available"

    return (
        f"Airline: {airline}\n"
        f"Flight Number: {flight_number}\n"
        f"Departure: {departure.get('airport', 'Unknown')} "
        f"({departure.get('iata', '???')}) at {dep_time}\n"
        f"Arrival: {arrival.get('airport', 'Unknown')} "
        f"({arrival.get('iata', '???')}) at {arr_time}\n"
        f"Status: {status}\n"
    )


# --------------------------------------------------------------------------
# AviationStack API
# --------------------------------------------------------------------------

def _fetch_flights(dep_iata: Optional[str], arr_iata: Optional[str],
                    limit: int) -> dict:
    """Single call to AviationStack's /flights endpoint. Raises on network error."""
    params = {"access_key": API_KEY, "limit": min(limit, 100)}
    if dep_iata:
        params["dep_iata"] = dep_iata
    if arr_iata:
        params["arr_iata"] = arr_iata

    response = requests.get(BASE_URL, params=params, timeout=30)
    return response.json()


def search_flights(query: str, limit: int = 10) -> str:
    """
    Resolve a natural-language query to airports, query AviationStack, and
    return a formatted string of results. If the exact route has no live
    flights, progressively broadens the search (route -> departure-only ->
    arrival-only) rather than failing immediately, since exact nonstop
    matches are frequently empty even for perfectly valid routes.
    """
    if not API_KEY:
        return (
            "AviationStack API key is not set. Please set the "
            "AVIATIONSTACK_API_KEY environment variable.\n"
            "Add this to your .env file:\n"
            "AVIATIONSTACK_API_KEY=your_api_key_here\n"
        )

    dep_iata, arr_iata = parse_route(query)
    logger.info("Query: %r -> dep=%s arr=%s", query, dep_iata, arr_iata)

    try:
        data = _fetch_flights(dep_iata, arr_iata, limit)
    except requests.exceptions.RequestException as e:
        return f"Error occurred while fetching flight data: {e}"
    except ValueError:
        return "Error occurred while parsing flight data: Flight API returned invalid JSON."

    if "error" in data:
        error = data["error"]
        return (
            "Flight API returned an error:\n"
            f"Code: {error.get('code', 'Unknown')}\n"
            f"Message: {error.get('message', 'Unknown')}\n"
        )

    flight_data = data.get("data", [])
    broadened_note = ""

    # Track which dep/arr combo actually produced the results being shown,
    # since this can differ from the originally-parsed route once we
    # broaden the search below. route_info (the header line) MUST be built
    # from this, not from the original dep_iata/arr_iata, or the header
    # will claim a route that isn't what's actually listed.
    shown_dep, shown_arr = dep_iata, arr_iata

    # If an exact route search came up empty, try loosening the filter
    # instead of giving up. This is the fix for the "always empty" problem:
    # AviationStack rarely has *both* dep and arr matching for less common
    # nonstop routes at any given moment.
    if not flight_data and dep_iata and arr_iata:
        try:
            data = _fetch_flights(dep_iata, None, limit)
            flight_data = data.get("data", [])
            if flight_data:
                shown_dep, shown_arr = dep_iata, None
                broadened_note = (
                    f"No live nonstop flights found for {dep_iata} -> {arr_iata} "
                    f"right now, so showing other live flights departing {dep_iata} "
                    "instead (you likely need a connecting flight).\n\n"
                )
        except (requests.exceptions.RequestException, ValueError):
            pass

    if not flight_data and arr_iata:
        try:
            data = _fetch_flights(None, arr_iata, limit)
            flight_data = data.get("data", [])
            if flight_data:
                shown_dep, shown_arr = None, arr_iata
                broadened_note = (
                    f"No live flights found matching that exact route, so showing "
                    f"other live flights arriving at {arr_iata} instead.\n\n"
                )
        except (requests.exceptions.RequestException, ValueError):
            pass

    if not flight_data:
        route_text = ""
        if dep_iata and arr_iata:
            route_text = f" for route {dep_iata} to {arr_iata}"
        elif dep_iata:
            route_text = f" from {dep_iata}"
        elif arr_iata:
            route_text = f" to {arr_iata}"

        return (
            f"No live flight data found{route_text}. This usually means there's "
            "no flight currently airborne or scheduled for that exact pair right "
            "now -- AviationStack's free tier only shows live/near-term flights, "
            "not a full schedule database.\n\n"
            "Note: AviationStack provides live/status flight data, not ticket "
            "prices. For fares, use a flight-pricing API such as Skyscanner, "
            "Kiwi, or Amadeus, or the Tavily search tool.\n"
        )

    if shown_dep and shown_arr:
        route_info = f"Live flights from {shown_dep} to {shown_arr}"
    elif shown_dep:
        route_info = f"Live flights from {shown_dep}"
    elif shown_arr:
        route_info = f"Live flights to {shown_arr}"
    else:
        route_info = "Global live flights"

    formatted_flights = [format_flight(f) for f in flight_data[:limit]]
    return f"{broadened_note}{route_info}\n\n" + "\n\n---\n\n".join(formatted_flights)


if __name__ == "__main__":
    print(search_flights("Plan a 7 days UAE trip from Islamabad, Pakistan"))
    print("\n" + "=" * 80 + "\n")
    print(search_flights("All country Flights Info"))