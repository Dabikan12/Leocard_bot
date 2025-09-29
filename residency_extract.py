import io
import fitz
import re


def extract_text_from_pdf_buffer(pdf_buffer: io.BytesIO) -> str:
    """
    Extracts all text from a PDF stored in an in-memory BytesIO buffer.

    :param pdf_buffer: io.BytesIO object containing the PDF data.
    :return: Full extracted text as a string.
    """
    try:
        pdf_buffer.seek(0)
        doc = fitz.open(stream=pdf_buffer.read(), filetype="pdf")
        full_text = ""
        for page in doc:
            full_text += page.get_text()
        return full_text.strip()
    except Exception as e:
        print(f"❌ Failed to extract text from PDF: {e}")
        return ""


def parse_residency_extract_text(text: str) -> dict:
    result = {}

    # Normalize and split lines
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    # 1. Дата народження
    dob_match = re.search(r"(Дата народження|date of birth)[^\d]*(\d{2}\.\d{2}\.\d{4})", text, re.IGNORECASE)
    if dob_match:
        result["date_of_birth"] = dob_match.group(2)

    # 2. УНЗР (record number)
    for i, line in enumerate(lines):
        if "УНЗР" in line and i + 1 < len(lines):
            unzr_match = re.search(r"\d{4,}-\d{4,}", lines[i + 1])
            if unzr_match:
                result["record_no"] = unzr_match.group()
                break

    # 3. ІПН (tax ID)
    for i, line in enumerate(lines):
        if "РНОКПП" in line and i + 1 < len(lines):
            ipn_match = re.search(r"\d{8,10}", lines[i + 1])
            if ipn_match:
                result["tax_id"] = ipn_match.group()
                break

    # 4. Адреса проживання
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

    # 5. ПІБ (Прізвище, Ім’я, По батькові)
    last_name = first_name = patronymic = ""
    for i, line in enumerate(lines):
        if "Прізвище" in line and i + 1 < len(lines):
            last_name = lines[i + 1]
        if "Власне ім’я" in line and i + 1 < len(lines):
            first_name = lines[i + 1]
        if "По батькові" in line and i + 1 < len(lines):
            patronymic = lines[i + 1]

    if first_name and last_name:
        result["full_name"] = f"{last_name} {first_name} {patronymic}".strip()

    return result
