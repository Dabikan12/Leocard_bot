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


def parse_residency_extract(text: str) -> dict:
    """Parse residency extract text into fields"""
    result = {}
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    # Date of birth
    dob_match = re.search(r"(Дата народження|date of birth)[^\d]*(\d{2}\.\d{2}\.\d{4})", text, re.IGNORECASE)
    if dob_match:
        result["date_of_birth"] = dob_match.group(2)

    # УНЗР (record number)
    for i, line in enumerate(lines):
        if "УНЗР" in line and i + 1 < len(lines):
            unzr_match = re.search(r"\d{4,}-\d{4,}", lines[i + 1])
            if unzr_match:
                result["record_no"] = unzr_match.group()
                break

    # Tax ID
    for i, line in enumerate(lines):
        if "РНОКПП" in line and i + 1 < len(lines):
            ipn_match = re.search(r"\d{8,10}", lines[i + 1])
            if ipn_match:
                result["tax_id"] = ipn_match.group()
                break

    # Address
    start_index = None
    for i, line in enumerate(lines):
        if "Адреса місця проживання" in line:
            start_index = i
            break

    if start_index is not None:
        address_candidates = []
        for line in lines[start_index + 1:]:
            if any(kw in line.lower() for kw in ["область", "район", "місто", "вул", "буд", "кв"]):
                address_candidates.append(line)
            elif address_candidates:
                break
        if address_candidates:
            result["residency_address"] = ", ".join(address_candidates)

    # Full name
    last_name = first_name = patronymic = ""
    for i, line in enumerate(lines):
        if "Прізвище" in line and i + 1 < len(lines):
            last_name = lines[i + 1]
        if "Власне ім'я" in line and i + 1 < len(lines):
            first_name = lines[i + 1]
        if "По батькові" in line and i + 1 < len(lines):
            patronymic = lines[i + 1]

    if first_name and last_name:
        result["full_name"] = f"{last_name} {first_name} {patronymic}".strip()

    return result