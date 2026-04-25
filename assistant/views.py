import base64
import io
import re
from datetime import datetime
from difflib import SequenceMatcher

import requests
from PIL import Image
from django.shortcuts import render


OCR_SPACE_API_KEY = "helloworld"  # Free demo key
OCR_SPACE_URL = "https://api.ocr.space/parse/image"


# -----------------------------
# Helpers
# -----------------------------
def compress_image_for_ocr(uploaded_file_or_bytes, max_size=(1600, 1600), quality=70):
    """
    Compress image to reduce file size for OCR API.
    Accepts Django UploadedFile OR raw bytes.
    Returns compressed JPEG bytes + debug string.
    """
    try:
        if isinstance(uploaded_file_or_bytes, bytes):
            image = Image.open(io.BytesIO(uploaded_file_or_bytes))
            original_size = len(uploaded_file_or_bytes)
        else:
            raw = uploaded_file_or_bytes.read()
            image = Image.open(io.BytesIO(raw))
            original_size = len(raw)

        if image.mode != "RGB":
            image = image.convert("RGB")

        image.thumbnail(max_size)

        output = io.BytesIO()
        image.save(output, format="JPEG", quality=quality, optimize=True)
        compressed_bytes = output.getvalue()

        debug = f"Compressed from {original_size} to {len(compressed_bytes)} bytes"
        return compressed_bytes, debug

    except Exception as e:
        return None, f"Compression failed: {str(e)}"


def extract_text_from_image(uploaded_file_or_bytes):
    """
    OCR using OCR.Space with automatic compression.
    """
    compressed_bytes, debug_msg = compress_image_for_ocr(uploaded_file_or_bytes)

    if not compressed_bytes:
        return "", debug_msg

    files = {
        "filename": ("image.jpg", compressed_bytes, "image/jpeg")
    }
    data = {
        "apikey": OCR_SPACE_API_KEY,
        "language": "eng",
        "isOverlayRequired": False,
        "OCREngine": 2,
        "scale": True,
    }

    try:
        response = requests.post(OCR_SPACE_URL, files=files, data=data, timeout=45)

        if response.status_code != 200:
            return "", f"OCR HTTP error: {response.status_code} | {response.text[:200]}"

        result = response.json()

        if result.get("IsErroredOnProcessing"):
            return "", f"OCR error: {result.get('ErrorMessage', ['Unknown error'])}"

        parsed_results = result.get("ParsedResults")
        if not parsed_results:
            return "", f"{debug_msg} | No ParsedResults"

        text = parsed_results[0].get("ParsedText", "").strip()
        if not text:
            return "", f"{debug_msg} | OCR success but no text"

        return text, f"{debug_msg} | OCR success"

    except Exception as e:
        return "", f"OCR exception: {str(e)}"


def clean_text(text):
    """
    Normalize OCR text for better matching.
    """
    text = text.lower()
    text = re.sub(r'[^a-z0-9\s\-\/\.]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def normalize_brand(word):
    """
    Common OCR confusion fixes for medicine brand names.
    """
    word = word.strip()
    fixes = {
        "gudcef": "gudcef",
        "gudcet": "gudcet",
        "gudcer": "gudcef",
        "gudcef.": "gudcef",
        "gudcet.": "gudcet",
        "gudcef200": "gudcef",
        "gudcet200": "gudcet",
    }
    return fixes.get(word.lower(), word)


def get_prescription_medicine_candidates(prescription_text):
    """
    Extract possible prescribed medicine names from prescription.
    Focus on lines starting with tab/cap/syr/inj.
    """
    candidates = []
    lines = [line.strip() for line in prescription_text.splitlines() if line.strip()]

    for line in lines:
        low = line.lower()

        if any(low.startswith(prefix) for prefix in ["tab ", "cap ", "syr ", "inj ", "tablet ", "capsule "]):
            words = re.findall(r'[A-Za-z][A-Za-z0-9\-]{2,}', line)
            if words:
                # usually second word is medicine name after Tab/Cap/Syr
                if len(words) >= 2:
                    med = normalize_brand(words[1])
                    candidates.append(med)
                else:
                    candidates.append(normalize_brand(words[0]))

    # fallback: find strong medicine-like words
    if not candidates:
        words = re.findall(r'\b[a-zA-Z][a-zA-Z0-9\-]{4,}\b', prescription_text)
        stop_words = {
            "doctor", "clinic", "chennai", "hours", "family", "physician",
            "phone", "consulting", "register", "regn", "patient"
        }
        for w in words:
            wl = w.lower()
            if wl not in stop_words:
                candidates.append(normalize_brand(w))

    # unique preserve order
    seen = set()
    final = []
    for c in candidates:
        cl = c.lower()
        if cl not in seen:
            seen.add(cl)
            final.append(c)
    return final[:8]


def get_medicine_pack_candidates(medicine_text):
    """
    Extract possible medicine names from strip/package text.
    Prefer first few strong brand lines.
    """
    candidates = []
    lines = [line.strip() for line in medicine_text.splitlines() if line.strip()]

    # Strong first lines often contain brand
    for line in lines[:8]:
        words = re.findall(r'[A-Za-z][A-Za-z0-9\-]{2,}', line)
        for w in words:
            wl = w.lower()
            if wl in {
                "tablet", "tablets", "capsule", "capsules", "schedule",
                "prescription", "drug", "caution", "equivalent", "contains"
            }:
                continue
            candidates.append(normalize_brand(w))

    # unique preserve order
    seen = set()
    final = []
    for c in candidates:
        cl = c.lower()
        if cl not in seen:
            seen.add(cl)
            final.append(c)
    return final[:15]


def similarity(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() * 100


def smart_match_medicines(prescription_text, medicine_text):
    """
    Compare best candidate from prescription vs medicine strip.
    """
    prescription_candidates = get_prescription_medicine_candidates(prescription_text)
    medicine_candidates = get_medicine_pack_candidates(medicine_text)

    best_score = 0
    best_p = "Not found"
    best_m = "Not found"

    for p in prescription_candidates:
        for m in medicine_candidates:
            score = similarity(p, m)

            # allow near OCR confusion like gudcet vs gudcef
            if len(p) >= 5 and len(m) >= 5:
                if p[:4].lower() == m[:4].lower():
                    score = max(score, 95.0)

            if score > best_score:
                best_score = score
                best_p = p
                best_m = m

    matched = best_score >= 75

    return {
        "matched": matched,
        "matched_prescription": best_p,
        "matched_medicine": best_m,
        "similarity": round(best_score, 1),
        "prescription_candidates": prescription_candidates,
        "medicine_candidates": medicine_candidates,
    }


def extract_expiry_date(medicine_text):
    """
    Extract expiry dates like:
    EXP: 06/2029
    EXP 06/29
    06/2029
    """
    patterns = [
        r'(?:exp|expiry|exp\.)[:\s\-]*([0-1]?\d[\/\-]\d{2,4})',
        r'\b([0-1]?\d[\/\-]\d{4})\b',
        r'\b([0-1]?\d[\/\-]\d{2})\b',
    ]

    for pattern in patterns:
        match = re.search(pattern, medicine_text, re.IGNORECASE)
        if match:
            return match.group(1)

    return None


def is_expired(expiry_str):
    """
    Check expiry date against current month/year.
    """
    if not expiry_str:
        return None

    try:
        parts = re.split(r'[\/\-]', expiry_str)
        if len(parts) != 2:
            return None

        month = int(parts[0])
        year = int(parts[1])

        if year < 100:
            year += 2000

        now = datetime.now()
        if year < now.year:
            return True
        if year == now.year and month < now.month:
            return True
        return False

    except Exception:
        return None


def convert_dosage_code_to_words(code):
    """
    Convert dosage patterns to human-readable text.
    Example:
    1-0-1 -> Morning + Night
    7ml-0-7ml -> Morning 7ml + Night 7ml
    """
    parts = [p.strip() for p in code.split("-")]
    if len(parts) != 3:
        return None

    labels = ["Morning", "Afternoon", "Night"]
    schedule = []

    for i, part in enumerate(parts):
        low = part.lower().strip()

        if low in ["0", "0.0", "zero", "nil"]:
            continue

        # numeric tablet style
        if low in ["1", "1.0"]:
            schedule.append(labels[i])
        else:
            # syrup style like 7ml
            schedule.append(f"{labels[i]} {part}")

    if not schedule:
        return "No dosage info"

    return " + ".join(schedule)


def extract_dosage_instructions(prescription_text):
    """
    Extract dosage instructions from prescription:
    1-0-1, 0-0-1, 7ml-0-7ml, etc.
    """
    patterns = [
        r'\b\d+\s*-\s*\d+\s*-\s*\d+\b',
        r'\b\d+\s*ml\s*-\s*\d+\s*-\s*\d+\s*ml\b',
        r'\b\d+\s*ml\s*-\s*\d+\s*ml\s*-\s*\d+\s*ml\b',
    ]

    found = []
    for pattern in patterns:
        matches = re.findall(pattern, prescription_text, re.IGNORECASE)
        for m in matches:
            cleaned = re.sub(r'\s+', '', m)
            found.append(cleaned)

    # unique
    unique = []
    for d in found:
        if d not in unique:
            unique.append(d)

    # convert to readable format
    final_dosage = []
    for d in unique:
        readable = convert_dosage_code_to_words(d)
        if readable:
            final_dosage.append(f"{d} ({readable})")
        else:
            final_dosage.append(d)

    return final_dosage


def build_voice_message(result):
    if result.get("error"):
        return "There was an error while analyzing the images. Please try again."

    if result["matched"]:
        match_msg = (
            f"Good news. The prescription medicine {result['matched_prescription']} "
            f"matches the strip medicine {result['matched_medicine']}."
        )
    else:
        match_msg = (
            f"Warning. The prescription and medicine may not match."
        )

    if result["expiry_found"]:
        if result["expired"]:
            expiry_msg = f"The medicine appears expired. Expiry date detected as {result['expiry_found']}."
        else:
            expiry_msg = f"The medicine does not appear expired. Expiry date detected as {result['expiry_found']}."
    else:
        expiry_msg = "Expiry date could not be detected clearly."

    if result["dosage"]:
        dosage_msg = "Dosage instructions found: " + ", ".join(result["dosage"]) + "."
    else:
        dosage_msg = "No clear dosage instructions were found."

    return f"{match_msg} {expiry_msg} {dosage_msg} Please confirm with a doctor or pharmacist before consuming."


def decode_base64_image(data_url):
    """
    Convert base64 camera image to raw bytes.
    """
    if not data_url or "," not in data_url:
        return None
    try:
        header, encoded = data_url.split(",", 1)
        return base64.b64decode(encoded)
    except Exception:
        return None


# -----------------------------
# Main View
# -----------------------------
def home(request):
    result = None

    if request.method == "POST":
        try:
            prescription_file = request.FILES.get("prescription_image")
            medicine_file = request.FILES.get("medicine_image")

            prescription_camera_data = request.POST.get("prescription_camera_data")
            medicine_camera_data = request.POST.get("medicine_camera_data")

            prescription_input = prescription_file
            medicine_input = medicine_file

            if not prescription_input and prescription_camera_data:
                prescription_input = decode_base64_image(prescription_camera_data)

            if not medicine_input and medicine_camera_data:
                medicine_input = decode_base64_image(medicine_camera_data)

            if not prescription_input or not medicine_input:
                result = {
                    "error": "Please upload or capture both prescription and medicine images."
                }
                return render(request, "home.html", {"result": result})

            # OCR extraction
            prescription_text, prescription_debug = extract_text_from_image(prescription_input)
            medicine_text, medicine_debug = extract_text_from_image(medicine_input)

            # Matching
            match_result = smart_match_medicines(prescription_text, medicine_text)

            # Expiry
            expiry_found = extract_expiry_date(medicine_text)
            expired = is_expired(expiry_found)

            # Dosage
            dosage = extract_dosage_instructions(prescription_text)

            result = {
                "matched": match_result["matched"],
                "matched_prescription": match_result["matched_prescription"],
                "matched_medicine": match_result["matched_medicine"],
                "similarity": match_result["similarity"],
                "expiry_found": expiry_found,
                "expired": expired,
                "dosage": dosage,
                "voice_message": "",
            }

            result["voice_message"] = build_voice_message(result)

        except Exception as e:
            result = {
                "error": f"Something went wrong: {str(e)}"
            }

    return render(request, "home.html", {"result": result})
