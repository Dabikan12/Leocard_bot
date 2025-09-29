import io
import re
import cv2
import numpy as np
import pytesseract


# ---------- 1. IMAGE PREPROCESSING ----------
def read_image_from_bytesio(file_buffer: io.BytesIO):
    file_buffer.seek(0)
    file_bytes = np.frombuffer(file_buffer.read(), np.uint8)
    return cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)


def preprocess_passport_image(file_buffer: io.BytesIO, C: int):
    img = read_image_from_bytesio(file_buffer)
    if img is None:
        raise FileNotFoundError(file_buffer)

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    blur = cv2.GaussianBlur(gray, (0, 0), 1.0)
    sharp = cv2.addWeighted(gray, 1.5, blur, -0.5, 0)
    thr = cv2.adaptiveThreshold(
        sharp, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        35, C
    )
    return thr


# ---------- 2. OCR TOKENIZATION ----------
def ocr_tokens_and_text(thr):
    cfg = r'--oem 1 --psm 6'
    raw_text = pytesseract.image_to_string(thr, config=cfg, lang='eng+ukr')

    data = pytesseract.image_to_data(
        thr, output_type=pytesseract.Output.DICT, config=cfg, lang='eng+ukr'
    )

    tokens = []
    for i in range(len(data["text"])):
        t = data["text"][i].strip()
        if not t:
            continue
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        tokens.append({"text": t, "x": x, "y": y, "w": w, "h": h, "cx": x + w / 2, "cy": y + h / 2})
    return raw_text, tokens


# ---------- 3a. GENDER DETECTION ----------
def detect_gender(raw_text, tokens):
    def norm(s):
        table = str.maketrans({
            "O": "0", "o": "0",
            "I": "1", "l": "1", "|": "1",
            "S": "5",
            "B": "8",
            "І": "I",  # normalize Ukrainian І to Latin I
        })
        return s.translate(table)

    txtU = norm(raw_text).upper()

    if re.search(r'\bЧ[ІI/]?M\b', txtU):
        return "Ч/M"
    if re.search(r'\bЖ[ІI/]?F\b', txtU):
        return "Ж/F"

    m = re.search(r'\b(Ж\s*/?\s*F|Ч\s*/?\s*M|F\s*/?\s*Ж|M\s*/?\s*Ч)\b', txtU)
    if m:
        return m.group(1).replace(' ', '').replace('//', '/')

    # Search in lines with "UKR" or "УКР"
    y_values = sorted({t["y"] for t in tokens})
    for y in y_values:
        line_tokens = [t for t in tokens if abs(t["y"] - y) < 15]
        line_text = " ".join(t["text"] for t in line_tokens).upper()
        if "UKR" in line_text or "УКР" in line_text:
            joined = norm(line_text).upper()
            if "Ч" in joined and ("M" in joined or "М" in joined):
                return "Ч/M"
            if "Ж" in joined:
                return "Ж/F"

    return None


# ---------- 3b. DATE OF BIRTH DETECTION ----------
def detect_date_of_birth(tokens, raw_text):
    def norm(s: str) -> str:
        return re.sub(r'\s+', ' ', s).strip()

    txtU = norm(raw_text).upper()

    dob_label_idxs = [i for i, t in enumerate(tokens)
                      if re.search(r'(?i)дата\s*народженн|date\s*of\s*birth', t["text"])]
    if dob_label_idxs:
        li = dob_label_idxs[0]
        ly = tokens[li]["y"]
        band = [t for t in tokens if abs(t["y"] - ly) < 30 and t["x"] > tokens[li]["x"] - 2]
        band.sort(key=lambda z: z["x"])
        band_text = norm(" ".join(t["text"] for t in band))
        m = re.search(r'\b(\d{2})[ ./-](\d{2})[ ./-](\d{4})\b', band_text)
        if m:
            return f"{m.group(1)}.{m.group(2)}.{m.group(3)}"

    m = re.search(r'\b(\d{2})[ ./-](\d{2})[ ./-](\d{4})\b', txtU)
    if m:
        return f"{m.group(1)}.{m.group(2)}.{m.group(3)}"
    return None


# ---------- 3c. RECORD NUMBER DETECTION ----------
def detect_record_no(tokens, raw_text):
    def norm(s: str) -> str:
        return re.sub(r'\s+', ' ', s).strip()

    txtU = norm(raw_text).upper()
    rec_label_idxs = [i for i, t in enumerate(tokens)
                      if re.search(r'(?i)запис\s*№|record\s*no', t["text"])]
    if rec_label_idxs:
        li = rec_label_idxs[0]
        ly = tokens[li]["y"]
        band = [t for t in tokens if abs(t["y"] - ly) < 30 and t["x"] > tokens[li]["x"] - 2]
        band.sort(key=lambda z: z["x"])
        band_text = norm(" ".join(t["text"] for t in band))
        m = re.search(r'\b(\d{8}-\d{5})\b', band_text)
        if m:
            return m.group(1)

    m = re.search(r'\b(\d{8}-\d{5})\b', txtU)
    if m:
        return m.group(1)
    return None


def fix_dob_from_record(results):
    """
    If date_of_birth is missing or implausible, derive it from record_no.
    """

    def plausible_date(y, m, d):
        try:
            yy, mm, dd = int(y), int(m), int(d)
            return 1900 <= yy <= 2100 and 1 <= mm <= 12 and 1 <= dd <= 31
        except:
            return False

    if results.get("record_no"):
        m = re.match(r"(\d{8})-\d{5}", results["record_no"])
        if m:
            yyyymmdd = m.group(1)
            yyyy, mm, dd = yyyymmdd[:4], yyyymmdd[4:6], yyyymmdd[6:8]
            if plausible_date(yyyy, mm, dd):
                dob_from_record = f"{dd}.{mm}.{yyyy}"
                if not results.get("date_of_birth") or not plausible_date(
                        results["date_of_birth"].split('.')[2],
                        results["date_of_birth"].split('.')[1],
                        results["date_of_birth"].split('.')[0],
                ):
                    results["date_of_birth"] = dob_from_record
    return results


# ---------- 4. MAIN WRAPPER ----------
def extract_passport_front_data(image_path: io.BytesIO, C: int):
    thr = preprocess_passport_image(image_path, C)
    raw_text, tokens = ocr_tokens_and_text(thr)

    results = {
        "gender": detect_gender(raw_text, tokens),
        "date_of_birth": detect_date_of_birth(tokens, raw_text),
        "record_no": detect_record_no(tokens, raw_text)
    }

    return fix_dob_from_record(results)


def extract_passport_front_data_auto_c(file_buffer: io.BytesIO):
    for C in [8, 10, 12]:
        print(f"Trying C={C}")
        results = extract_passport_front_data(file_buffer, C=C)

        if all(results.get(k) for k in ("gender", "date_of_birth", "record_no")):
            print(f"All found with C={C}")
            return results

    print("Could not find all fields with tested C values")
    return results
