import pytesseract
import re
import io
import cv2

import numpy as np


def _imread_from_bytes(data: bytes):
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return img


def extract_passport_data_from_bytes(buf_or_bytes: io.BytesIO | bytes) -> dict:
    """Decode image bytes and run the same pipeline, returning the same dict structure."""
    if isinstance(buf_or_bytes, io.BytesIO):
        data = buf_or_bytes.getvalue()
    else:
        data = buf_or_bytes

    img = _imread_from_bytes(data)
    if img is None:
        return {"authority_code": None, "date_of_issue": None, "tax_id": None}

    # Write to a temp in-memory file path alternative is messy;
    # instead, lightly adapt your pipeline to accept ndarray.
    # If you prefer to keep the 'path' API, you can save to a temp file and pass the path.

    # Minimal adaptation: call the core steps directly by copying
    # your extract_passport_data's body into a new function that accepts an ndarray.
    # But if you want to reuse as-is, do the temp-file approach:
    import tempfile, os
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        cv2.imwrite(tmp.name, img)
        temp_path = tmp.name
    try:
        out = extract_passport_data(temp_path)  # returns {"authority_code","date_of_issue","tax_id"}
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass

    return out


def extract_passport_data(image_path: str):
    def _norm_digits(s: str) -> str:
        table = str.maketrans({
            "O": "0", "o": "0",
            "I": "1", "l": "1", "|": "1",
            "S": "5",
            "B": "8",
        })
        return s.translate(table)

    def plausible_date(dd, mm, yyyy):
        try:
            d, m, y = int(dd), int(mm), int(yyyy)
            return 1 <= d <= 31 and 1 <= m <= 12 and 1900 <= y <= 2100
        except:
            return False

    def best_orientation_horizontal(img):
        if img.shape[1] >= img.shape[0]:
            candidates = [img, cv2.rotate(img, cv2.ROTATE_180)]
        else:
            candidates = [
                cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE),
                cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE),
            ]

        def mrz_score(gray):
            if len(gray.shape) == 3:
                gray = cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)
            g = cv2.resize(gray, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
            bh = int(g.shape[0] * 0.22)
            top = g[:bh, :]
            bot = g[-bh:, :]

            cfg = r'--oem 1 --psm 6 -c tessedit_char_blacklist=abcdefghijklmnopqrstuvwxyz'
            top_txt = pytesseract.image_to_string(top, config=cfg, lang='eng')
            bot_txt = pytesseract.image_to_string(bot, config=cfg, lang='eng')

            def cues(t):
                t = t.upper()
                return (
                        t.count('<') * 2 +
                        len(re.findall(r'IDUKR', t)) * 8 +
                        len(re.findall(r'\bUKR\b', t)) * 4 +
                        len(re.findall(r'\d', t))
                )

            return cues(bot_txt) - cues(top_txt), bot_txt + "\n" + top_txt

        best_img, best_val = None, -1e9
        for r in candidates:
            val, _ = mrz_score(r)
            if val > best_val:
                best_val, best_img = val, r

        return best_img

    def preprocess_image(img):
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        blur = cv2.GaussianBlur(gray, (0, 0), 1.0)
        sharp = cv2.addWeighted(gray, 1.5, blur, -0.5, 0)
        thr = cv2.adaptiveThreshold(
            sharp, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            35, 10
        )
        return thr

    def extract_tokens_and_text(thr):
        cfg = r'--oem 1 --psm 6'
        raw_text = pytesseract.image_to_string(thr, config=cfg, lang='eng+ukr')
        data = pytesseract.image_to_data(
            thr, output_type=pytesseract.Output.DICT, config=cfg, lang='eng+ukr'
        )

        tokens = []
        for i in range(len(data["text"])):
            raw = data["text"][i].strip()
            if not raw:
                continue
            norm = _norm_digits(raw)
            x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
            tokens.append({"text": norm, "x": x, "y": y, "w": w, "h": h})
        return raw_text, tokens

    def extract_tax_id(raw_text, tokens):
        TAX_WORDS = ("РНОКПП", "ІПН", "IPN", "RNOKPP", "ІДЕНТИФІКАЦІЙНИЙ", "ПОДАТК")
        ten_digits = re.findall(r'(?<!\d)(\d{10})(?!\d)', raw_text)
        ten_digits = list(dict.fromkeys(ten_digits))
        if not ten_digits:
            return None

        kw_positions = [
            (t["x"] + t["w"] / 2, t["y"] + t["h"] / 2)
            for t in tokens
            if any(w.lower() in t["text"].lower() for w in TAX_WORDS)
        ]

        def centers_for_candidate(cand):
            return [
                (t["x"] + t["w"] / 2, t["y"] + t["h"] / 2)
                for t in tokens if cand in re.sub(r'\D', '', t["text"])
            ]

        if kw_positions:
            bestd, chosen = None, None
            for cand in ten_digits:
                centers = centers_for_candidate(cand) or [(0, 0)]
                d = min(
                    abs(cx - kx) + abs(cy - ky)
                    for (cx, cy) in centers
                    for (kx, ky) in kw_positions
                )
                if bestd is None or d < bestd:
                    bestd, chosen = d, cand
            return chosen
        else:
            return ten_digits[0]

    def extract_date_and_code(tokens):
        for line_y in sorted({t["y"] for t in tokens}):
            line_tokens = [t for t in tokens if abs(t["y"] - line_y) < 15]
            line_tokens.sort(key=lambda t: t["x"])
            line_text = " ".join(t["text"] for t in line_tokens)
            digits_only = re.sub(r"[^0-9]", "", line_text)

            for i in range(0, max(0, len(digits_only) - 12) + 1):
                chunk = digits_only[i:i + 12]
                if len(chunk) < 12:
                    continue
                dd, mm, yyyy, code = chunk[:2], chunk[2:4], chunk[4:8], chunk[8:12]
                if plausible_date(dd, mm, yyyy) and re.fullmatch(r"\d{4}", code):
                    return f"{dd}.{mm}.{yyyy}", code

            four_digit_tokens = [t for t in line_tokens if re.fullmatch(r"\d{4}", t["text"])]
            if len(four_digit_tokens) >= 1:
                authority_token = four_digit_tokens[-1]
                code = authority_token["text"]
                idx = line_tokens.index(authority_token)

                if idx >= 3:
                    t1, t2, t3 = line_tokens[idx - 3], line_tokens[idx - 2], line_tokens[idx - 1]
                    if (re.fullmatch(r"\d{2}", t1["text"]) and
                            re.fullmatch(r"\d{2}", t2["text"]) and
                            re.fullmatch(r"\d{4}", t3["text"]) and
                            plausible_date(t1["text"], t2["text"], t3["text"])):
                        return f"{t1['text']}.{t2['text']}.{t3['text']}", code

                if idx >= 2:
                    t1, t2 = line_tokens[idx - 2], line_tokens[idx - 1]
                    if re.fullmatch(r"\d{4}", t1["text"]) and re.fullmatch(r"\d{4}", t2["text"]):
                        dd, mm = t1["text"][:2], t1["text"][2:]
                        yyyy = t2["text"]
                        if plausible_date(dd, mm, yyyy):
                            return f"{dd}.{mm}.{yyyy}", code

                if idx >= 1:
                    t1 = line_tokens[idx - 1]
                    if re.fullmatch(r"\d{2}[-./]\d{2}[-./]\d{4}", t1["text"]):
                        dd, mm, yyyy = re.split(r"[-./]", t1["text"])
                        if plausible_date(dd, mm, yyyy):
                            return f"{dd}.{mm}.{yyyy}", code

                return None, code
        return None, None

    img = cv2.imread(image_path)
    if img is None:
        raise FileNotFoundError(image_path)

    img = best_orientation_horizontal(img)
    thr = preprocess_image(img)
    raw_text, tokens = extract_tokens_and_text(thr)

    results = {
        "authority_code": None,
        "date_of_issue": None,
        "tax_id": extract_tax_id(raw_text, tokens)
    }

    date, code = extract_date_and_code(tokens)
    if date:
        results["date_of_issue"] = date
    if code:
        results["authority_code"] = code

    return results
