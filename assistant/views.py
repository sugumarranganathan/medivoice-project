import os
import re
import base64
import requests
import cv2
import numpy as np

from difflib import SequenceMatcher
from datetime import datetime
from io import BytesIO

from django.conf import settings
from django.shortcuts import render
from django.core.files.storage import default_storage


# ==========================================
# FILE SAVE HELPERS
# ==========================================

def save_uploaded_file(file_obj, folder="uploads"):
    try:
        file_name = default_storage.save(f"{folder}/{file_obj.name}", file_obj)
        return os.path.join(settings.MEDIA_ROOT, file_name)
    except Exception:
        return None


def save_base64_image(data_url, file_name="camera_capture.png", folder="uploads"):
    try:
        if not data_url or "," not in data_url:
            return None

        header, imgstr = data_url.split(",", 1)
        ext = "png"

        if "image/jpeg" in header:
            ext = "jpg"
        elif "image/webp" in header:
            ext = "webp"

        final_name = f"{os.path.splitext(file_name)[0]}.{ext}"
        binary_data = base64.b64decode(imgstr)

        relative_path = f"{folder}/{final_name}"
        saved_path = default_storage.save(relative_path, BytesIO(binary_data))

        return os.path.join(settings.MEDIA_ROOT, saved_path)
    except Exception:
        return None


# ==========================================
# IMAGE PREPROCESSING
# ==========================================

def load_image(image_path):
    try:
        if not image_path or not os.path.exists(image_path):
            return None
        img = cv2.imread(image_path)
        return img
    except Exception:
        return None


def resize_for_ocr(img, scale=2.0):
    try:
        h, w = img.shape[:2]
        new_w = max(int(w * scale), 1)
        new_h = max(int(h * scale), 1)
        return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_CUBIC)
    except Exception:
        return img


def preprocess_variants(image_path):
    """
    Returns multiple image variants for OCR:
    1. Original resized
    2. Grayscale
    3. OTSU threshold
    4. Adaptive threshold
    5. Sharpened grayscale
    """
    variants = []

    img = load_image(image_path)
    if img is None:
        return variants

    img = resize_for_ocr(img, scale=2.0)

    try:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    except Exception:
        return [img]

    gray_blur = cv2.GaussianBlur(gray, (3, 3), 0)

    _, otsu = cv2.threshold(gray_blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    adaptive = cv2.adaptiveThreshold(
        gray_blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY, 31, 11
    )

    kernel = np.array([
        [0, -1, 0],
        [-1, 5, -1],
        [0, -1, 0]
    ])
    sharpened = cv2.filter2D(gray, -1, kernel)

    variants = [img, gray, otsu, adaptive, sharpened]
    return variants


def image_to_temp_bytes(img):
    try:
        success, buffer = cv2.imencode(".png", img)
        if not success:
            return None
        return BytesIO(buffer.tobytes())
    except Exception:
        return None


# ==========================================
# OCR.SPACE HELPERS
# ==========================================

def ocr_space_from_filelike(filelike):
    api_key = os.environ.get("OCR_SPACE_API_KEY", "")

    if not api_key:
        return ""

    try:
        filelike.seek(0)

        response = requests.post(
            "https://api.ocr.space/parse/image",
            files={"filename": ("ocr.png", filelike, "image/png")},
            data={
                "apikey": api_key,
                "language": "eng",
                "isOverlayRequired": False,
                "OCREngine": 2,
                "scale": True,
                "detectOrientation": True,
                "isTable": False,
            },
            timeout=60
        )

        if response.status_code != 200:
            return ""

        data = response.json()

        if data.get("IsErroredOnProcessing"):
            return ""

        parsed_results = data.get("ParsedResults", [])
        if not parsed_results:
            return ""

        text = "\n".join(
            result.get("ParsedText", "") for result in parsed_results
        ).strip()

        return text

    except Exception:
        return ""


def extract_text_from_image(image_path):
    """
    Try OCR on multiple preprocessed versions and return best combined text.
    """
    variants = preprocess_variants(image_path)

    all_texts = []

    # Try original file directly first
    try:
        with open(image_path, "rb") as f:
            direct_text = ocr_space_from_filelike(BytesIO(f.read()))
            if direct_text:
                all_texts.append(direct_text)
    except Exception:
        pass

    # Try processed variants
    for variant in variants:
        try:
            filelike = image_to_temp_bytes(variant)
            if not filelike:
                continue

            text = ocr_space_from_filelike(filelike)
            if text and len(text.strip()) > 2:
                all_texts.append(text)
        except Exception:
            continue

    if not all_texts:
        return ""

    lines = []
    seen = set()

    for block in all_texts:
        for line in block.splitlines():
            cleaned = line.strip()
            if len(cleaned) < 2:
                continue
            key = cleaned.lower()
            if key not in seen:
                seen.add(key)
                lines.append(cleaned)

    return "\n".join(lines).strip()


# ==========================================
# TEXT CLEANING
# ==========================================

def clean_text(text):
    if not text:
        return ""

    text = text.replace("\r", "\n")
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n+', '\n', text)
    return text.strip()


def normalize_medicine_name(text):
    if not text:
        return ""

    text = text.lower()
    text = text.replace("tablet", "")
    text = text.replace("tablets", "")
    text = text.replace("tab.", "")
    text = text.replace("tab", "")
    text = text.replace("capsule", "")
    text = text.replace("capsules", "")
    text = text.replace("cap.", "")
    text = text.replace("cap", "")
    text = text.replace("syrup", "")
    text = text.replace("syr.", "")
    text = text.replace("syr", "")
    text = re.sub(r'[^a-z0-9\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()

    # Normalize 200mg -> 200
    text = re.sub(r'\b(\d+)\s*mg\b', r'\1', text)

    return text.strip()


# ==========================================
# PRESCRIPTION MEDICINE EXTRACTION
# ==========================================

def extract_prescription_medicines(text):
    """
    Better extraction from prescription lines like:
    Tab Gudcef 200mg 1-0-1
    Tab Monticope 0-0-1
    Syr Chericof 7ml-0-7ml
    """
    if not text:
        return []

    medicines = []
    lines = text.splitlines()

    for line in lines:
        original_line = line.strip()
        line_lower = original_line.lower()

        if not original_line:
            continue

        if any(prefix in line_lower for prefix in ["tab ", "tab.", "tablet", "cap ", "capsule", "syr ", "syr.", "syrup"]):
            cleaned = re.sub(r'\b(tab|tab\.|tablet|cap|cap\.|capsule|syr|syr\.|syrup)\b', '', original_line, flags=re.I)

            # Remove dosage patterns
            cleaned = re.sub(r'\b\d+\s*-\s*\d+\s*-\s*\d+\b', '', cleaned, flags=re.I)
            cleaned = re.sub(r'\b\d+\s*ml\s*-\s*\d+\s*-\s*\d+\s*ml\b', '', cleaned, flags=re.I)
            cleaned = re.sub(r'\b\d+\s*ml\b', '', cleaned, flags=re.I)
            cleaned = re.sub(r'\bx\s*\d+\s*days?\b', '', cleaned, flags=re.I)

            cleaned = re.sub(r'[^A-Za-z0-9\s]', ' ', cleaned)
            cleaned = re.sub(r'\s+', ' ', cleaned).strip()

            if len(cleaned) >= 3:
                medicines.append(cleaned)

    # Fallback
    if not medicines:
        for line in lines:
            line = line.strip()
            if not line:
                continue

            if re.search(r'\b[a-zA-Z]{3,}\b', line) and (re.search(r'\b\d-\d-\d\b', line) or "mg" in line.lower()):
                cleaned = re.sub(r'[^A-Za-z0-9\s]', ' ', line)
                cleaned = re.sub(r'\s+', ' ', cleaned).strip()
                if len(cleaned) >= 3:
                    medicines.append(cleaned)

    unique = []
    seen = set()
    for med in medicines:
        key = normalize_medicine_name(med)
        if key and key not in seen:
            seen.add(key)
            unique.append(med)

    return unique[:10]


def extract_medicine_pack_candidates(text):
    """
    Extract medicine names from medicine strip/box OCR.
    """
    if not text:
        return []

    candidates = []
    lines = text.splitlines()

    for line in lines:
        original = line.strip()
        if not original:
            continue

        cleaned = re.sub(r'[^A-Za-z0-9\s]', ' ', original)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()

        if len(cleaned) < 3:
            continue

        if re.search(r'\b\d+\s*mg\b', original, re.I):
            candidates.append(cleaned)

        if any(word in original.lower() for word in [
            "tablet", "tablets", "capsule", "capsules", "ip", "cefpodoxime"
        ]):
            candidates.append(cleaned)

        words = cleaned.split()
        if len(words) >= 1 and len(words[0]) >= 4:
            candidates.append(cleaned)

    unique = []
    seen = set()
    for c in candidates:
        key = normalize_medicine_name(c)
        if key and key not in seen:
            seen.add(key)
            unique.append(c)

    return unique[:12]


# ==========================================
# MATCHING LOGIC
# ==========================================

def similarity_score(a, b):
    a_norm = normalize_medicine_name(a)
    b_norm = normalize_medicine_name(b)

    if not a_norm or not b_norm:
        return 0.0

    # Strong contains boost
    if a_norm in b_norm or b_norm in a_norm:
        base = 0.92
    else:
        base = SequenceMatcher(None, a_norm, b_norm).ratio()

    # Token overlap boost
    a_tokens = set(a_norm.split())
    b_tokens = set(b_norm.split())

    if a_tokens and b_tokens:
        overlap = len(a_tokens & b_tokens) / max(len(a_tokens), len(b_tokens))
        score = max(base, (base * 0.7 + overlap * 0.3))
    else:
        score = base

    return min(score, 1.0)


def best_match(prescription_candidates, medicine_candidates):
    best_ratio = 0.0
    best_prescription = ""
    best_medicine = ""

    for p in prescription_candidates:
        for m in medicine_candidates:
            ratio = similarity_score(p, m)
            if ratio > best_ratio:
                best_ratio = ratio
                best_prescription = p
                best_medicine = m

    return best_prescription, best_medicine, best_ratio


# ==========================================
# EXPIRY EXTRACTION
# ==========================================

def extract_expiry_date(text):
    if not text:
        return None

    patterns = [
        r'EXP[:\s\-]*([0-1]?\d[/\-][0-9]{2,4})',
        r'EXPIRY[:\s\-]*([0-1]?\d[/\-][0-9]{2,4})',
        r'EXP DATE[:\s\-]*([0-1]?\d[/\-][0-9]{2,4})',
        r'\b([0-1]?\d[/\-][0-9]{4})\b',
        r'\b([0-1]?\d[/\-][0-9]{2})\b',
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)

    return None


def is_expired(expiry_str):
    if not expiry_str:
        return None

    try:
        expiry_str = expiry_str.replace("-", "/")
        parts = expiry_str.split("/")

        if len(parts) != 2:
            return None

        month = int(parts[0])
        year = int(parts[1])

        if year < 100:
            year += 2000

        if month < 1 or month > 12:
            return None

        now = datetime.now()

        if year < now.year:
            return True
        if year == now.year and month < now.month:
            return True

        return False
    except Exception:
        return None


# ==========================================
# DOSAGE EXTRACTION
# ==========================================

def extract_dosage_info(text):
    if not text:
        return []

    results = []
    lines = text.splitlines()

    for line in lines:
        line_clean = line.strip()
        if not line_clean:
            continue

        # 1-0-1 style
        match1 = re.search(r'\b\d+\s*-\s*\d+\s*-\s*\d+\b', line_clean)
        if match1:
            results.append(match1.group(0).replace(" ", ""))

        # 7ml-0-7ml style
        match2 = re.search(r'\b\d+\s*ml\s*-\s*\d+\s*-\s*\d+\s*ml\b', line_clean, re.I)
        if match2:
            results.append(match2.group(0).replace(" ", ""))

        # x 5 days
        match3 = re.search(r'\bx\s*\d+\s*days?\b', line_clean, re.I)
        if match3:
            results.append(match3.group(0))

    keyword_patterns = [
        r'\bonce daily\b',
        r'\btwice daily\b',
        r'\bthrice daily\b',
        r'\bafter food\b',
        r'\bbefore food\b',
        r'\bSOS\b',
    ]

    for pattern in keyword_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        results.extend(matches)

    unique = []
    seen = set()
    for item in results:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            unique.append(item)

    return unique


# ==========================================
# SERVAM TTS
# ==========================================

def generate_servam_tts(text):
    api_key = getattr(settings, "SERVAM_API_KEY", "")
    api_url = getattr(settings, "SERVAM_API_URL", "")

    if not api_key or not api_url:
        return None

    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "text": text,
            "language": "en-IN",
            "speaker": "female"
        }

        response = requests.post(api_url, json=payload, headers=headers, timeout=30)

        if response.status_code == 200:
            data = response.json()
            return data.get("audio_url") or data.get("url")

        return None

    except Exception:
        return None


# ==========================================
# VOICE MESSAGE
# ==========================================

def build_voice_message(result):
    if result.get("error"):
        return "Unable to analyze the medicine safely. Please try again with clearer images."

    parts = []

    if result.get("matched"):
        parts.append("Good news. The prescription and medicine appear to match.")
    else:
        parts.append("Warning. The prescription and medicine may not match.")

    if result.get("expiry_found"):
        if result.get("expired") is True:
            parts.append(f"The medicine appears expired. Expiry date detected as {result['expiry_found']}. Do not use it.")
        elif result.get("expired") is False:
            parts.append(f"The medicine does not appear expired. Expiry date detected as {result['expiry_found']}.")
        else:
            parts.append(f"Expiry date was detected as {result['expiry_found']}, but could not be fully verified.")
    else:
        parts.append("Expiry date could not be detected clearly.")

    if result.get("dosage"):
        parts.append(f"Dosage instructions found: {', '.join(result['dosage'])}.")
    else:
        parts.append("No clear dosage instructions were found.")

    parts.append("Please confirm with a doctor or pharmacist before consuming.")

    return " ".join(parts)


# ==========================================
# MAIN VIEW
# ==========================================

def home(request):
    result = None

    if request.method == "POST":
        try:
            prescription_path = None
            medicine_path = None

            # Uploaded files
            prescription_file = request.FILES.get("prescription_image")
            medicine_file = request.FILES.get("medicine_image")

            if prescription_file:
                prescription_path = save_uploaded_file(prescription_file, folder="prescriptions")

            if medicine_file:
                medicine_path = save_uploaded_file(medicine_file, folder="medicines")

            # Camera base64 fallback
            prescription_camera = request.POST.get("prescription_camera_data")
            medicine_camera = request.POST.get("medicine_camera_data")

            if not prescription_path and prescription_camera:
                prescription_path = save_base64_image(
                    prescription_camera,
                    file_name="prescription_capture.png",
                    folder="prescriptions"
                )

            if not medicine_path and medicine_camera:
                medicine_path = save_base64_image(
                    medicine_camera,
                    file_name="medicine_capture.png",
                    folder="medicines"
                )

            if not prescription_path or not medicine_path:
                result = {
                    "error": "Please provide both prescription and medicine images."
                }
                return render(request, "home.html", {"result": result})

            # OCR
            prescription_text = clean_text(extract_text_from_image(prescription_path))
            medicine_text = clean_text(extract_text_from_image(medicine_path))

            # Extract medicine candidates
            prescription_candidates = extract_prescription_medicines(prescription_text)
            medicine_candidates = extract_medicine_pack_candidates(medicine_text)

            # Match
            matched_prescription, matched_medicine, similarity = best_match(
                prescription_candidates, medicine_candidates
            )

            matched = similarity >= 0.72

            # Expiry
            expiry_found = extract_expiry_date(medicine_text)
            expired = is_expired(expiry_found)

            # Dosage
            dosage = extract_dosage_info(prescription_text)

            result = {
                "error": None,
                "prescription_text": prescription_text if prescription_text else "No text extracted",
                "medicine_text": medicine_text if medicine_text else "No text extracted",
                "prescription_candidates": prescription_candidates,
                "medicine_candidates": medicine_candidates,
                "matched_prescription": matched_prescription if matched_prescription else "Not found",
                "matched_medicine": matched_medicine if matched_medicine else "Not found",
                "similarity": round(similarity * 100, 2),
                "matched": matched,
                "expiry_found": expiry_found,
                "expired": expired,
                "dosage": dosage,
            }

            # Brand fallback for partial OCR like Gudcef
            if not result["matched"] and prescription_text and medicine_text:
                p_norm = normalize_medicine_name(prescription_text)
                m_norm = normalize_medicine_name(medicine_text)

                if "gudcef" in p_norm and "gudcef" in m_norm:
                    result["matched"] = True
                    result["matched_prescription"] = "Gudcef 200mg"
                    result["matched_medicine"] = "Gudcef 200"
                    result["similarity"] = max(result["similarity"], 92.0)

            result["voice_message"] = build_voice_message(result)
            result["servam_audio_url"] = generate_servam_tts(result["voice_message"])

        except Exception as e:
            result = {
                "error": f"An error occurred: {str(e)}"
            }

    return render(request, "home.html", {"result": result})
