import re
from datetime import datetime
from typing import Optional, Dict


def parse_ukrainian_address(full_address: str) -> Dict[str, str]:
    """Parse Ukrainian address into components"""
    result = {"city": "N/A", "street": "N/A", "building_flat": "N/A"}

    if not full_address:
        return result

    address = full_address.lower()
    address = re.sub(r"\s+", " ", address.replace(".", "")).strip()

    # City
    city_match = re.search(r"місто\s+([а-яіїєґʼ\- ]+)", address)
    if city_match:
        result["city"] = city_match.group(1).strip().title()

    # Street
    street_match = re.search(r"вул[.,]?\s+([а-яіїєґʼ0-9\- ]+)", address)
    if street_match:
        result["street"] = street_match.group(1).strip().title()

    # Building and flat
    building_match = re.search(r"(буд\s*\S+,\s*кв\s*\S+)", address)
    if building_match:
        result["building_flat"] = building_match.group(1).strip()

    return result


def normalize_date(date_text: str) -> Optional[str]:
    """Normalize date to DD.MM.YYYY format"""
    if not date_text:
        return None

    m = re.match(r"^(\d{1,2})[\.\-/](\d{1,2})[\.\-/](\d{4})$", date_text.strip())
    if not m:
        return None

    day, month, year = m.groups()
    try:
        dt = datetime.strptime(f"{int(day):02d}.{int(month):02d}.{year}", "%d.%m.%Y")
        return dt.strftime("%d.%m.%Y")
    except ValueError:
        return None


def is_valid_lpnu_email(email: str) -> bool:
    """Validate LPNU email format"""
    if not email:
        return False
    email = email.strip().lower()
    return bool(re.match(r"^[A-Za-z0-9._%+-]+@lpnu\.ua$", email))