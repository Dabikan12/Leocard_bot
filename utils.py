import re
from datetime import datetime


def parse_ukrainian_address(full_address: str) -> dict:
    result = {
        "city": "N/A",
        "street": "N/A",
        "building_flat": "N/A"
    }

    if not full_address:
        return result

    # --- Pre-clean the address ---
    address = full_address.lower()
    address = address.replace(".", "")  # remove periods
    address = re.sub(r"\s+", " ", address)  # collapse multiple spaces
    address = re.sub(r",\s*,", ",", address)  # remove duplicate commas
    address = address.replace(" ,", ",")  # fix spaces before commas
    address = address.strip()

    # --- Parse city ---
    city_match = re.search(r"місто\s+([а-яіїєґʼ\- ]+)", address)
    if city_match:
        result["city"] = city_match.group(1).strip().title()

    # --- Parse street ---
    street_match = re.search(r"вул[.,]?\s+([а-яіїєґʼ0-9\- ]+)", address)
    if street_match:
        result["street"] = street_match.group(1).strip().title()

    # --- Parse building and flat ---
    building_flat_match = re.search(r"(буд\s*\S+,\s*кв\s*\S+)", address)
    if building_flat_match:
        result["building_flat"] = building_flat_match.group(1).strip()

    return result


def normalize_dd_mm_yyyy_date(date_text: str):
    """
    Validate and normalize a date string to DD.MM.YYYY.

    Accepts separators '.', '/', '-'. Returns normalized 'DD.MM.YYYY' or None if invalid.
    """
    if not date_text:
        return None
    text = date_text.strip()
    m = re.match(r"^(\d{1,2})[\.\-/](\d{1,2})[\.\-/](\d{4})$", text)
    if not m:
        return None
    day, month, year = m.groups()
    try:
        dt = datetime.strptime(f"{int(day):02d}.{int(month):02d}.{year}", "%d.%m.%Y")
        return dt.strftime("%d.%m.%Y")
    except ValueError:
        return None
