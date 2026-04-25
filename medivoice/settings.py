import os
import re
import base64
import requests
from difflib import SequenceMatcher
from datetime import datetime
from io import BytesIO

from django.conf import settings
from django.shortcuts import render
from django.core.files.storage import default_storage


# =========================
# IMAGE SAVE HELPERS
# =========================

def save_uploaded_file(file_obj, folder="uploads"):
    """
    Save uploaded file to media folder and return full path.
    """
    try:
        file_name = default_storage.save(f"{folder}/{file_obj.name}", file_obj)
        return os.path.join(settings.MEDIA_ROOT, file_name)
    except Exception:
        return None


def save_base64_image(data_url, file_name="camera_capture.png", folder="uploads"):
    """
    Save base64 image (from camera capture) to media folder.
    """
    try:
        if not data_url or "," not in data_url:
            return None

        format_part, imgstr = data_url.split(",", 1)
        ext = "png"

        if "image/jpeg" in format_part:
            ext = "jpg"
        elif "image/webp" in format_part:
            ext = "webp"

        file_name = f"{os.path.splitext(file_name)[0]}.{ext}"
        binary_data = base64.b64decode(imgstr)

        relative_path = f"{folder}/{file_name}"
        saved_path = default_storage.save(relative_path, BytesIO(binary_data))

        return os.path.join(settings.MEDIA_ROOT, saved_path)
    except Exception:
        return None


# =========================
# OCR.SPACE API HELPERS
# =========================

def extract_text_from_image(image_path):
    """
    Extract text using OCR.Space API.
    Works on Render without needing local Tesseract.
    """
    api_key = os.environ.get("OCR_SPACE_API_KEY", "")

    if not api_key:
        return ""

    try:
        with open(image_path, "rb") as f:
            response = requests.post(
                "https://api.ocr.space/parse/image",
                files={"filename": f},
                data={
                    "apikey": api_key,
                    "language": "eng",
                    "isOverlayRequired": False,
                    "OCREngine": 2,
                    "scale": True,
                    "detectOrientation": True,
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

        extracted_text = " ".join(
            result.get("ParsedText", "") for result in parsed_results
        ).strip()

        return extracted_text

    except Exception:
        return ""


# =========================
# TEXT EXTRACTION HELPERS
# =========================

def clean_text(text):
    if not text:
        return ""
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def extract_medicine_candidates(text):
    """
    Simple medicine name extraction from OCR text.
    """
    if not text:
        return []

    # Split on punctuation / commas / line-like separators
    parts = re.split(r'[\n,;:]+', text)
    candidates = []

    for part in parts:
        cleaned = re.sub(r'[^A-Za-z0-9\s\-\+]', ' ', part)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()

        if len(cleaned) < 3:
            continue

        # Skip common noise words
        noise_words = {
            "tablet", "tablets", "capsule", "capsules", "syrup",
            "ml", "mg", "g", "dosage", "expiry", "exp", "manufactured"
        }

        if cleaned.lower() in noise_words:
            continue

        candidates.append(cleaned)

    # unique preserve order
    unique = []
    seen = set()
    for c in candidates:
        key = c.lower()
        if key not in seen:
            seen.add(key)
            unique.append(c)

    return unique[:10]


def best_match(prescription_candidates, medicine_candidates):
    """
    Find best medicine name match using SequenceMatcher.
    """
    best_ratio = 0
    best_prescription = ""
    best_medicine = ""

    for p in prescription_candidates:
        for m in medicine_candidates:
            ratio = SequenceMatcher(None, p.lower(), m.lower()).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_prescription = p
                best_medicine = m

    return best_prescription, best_medicine, best_ratio


def extract_expiry_date(text):
    """
    Detect expiry formats like:
    EXP 12/26, 12/2026, 2026-12, etc.
    """
    if not text:
        return None

    patterns = [
        r'EXP[:\s\-]*([0-1]?\d[/\-][0-9]{2,4})',
        r'Expiry[:\s\-]*([0-1]?\d[/\-][0-9]{2,4})',
        r'([0-1]?\d[/\-][0-9]{2,4})',
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)

    return None


def is_expired(expiry_str):
    """
    Checks if expiry is past current month.
    Supports MM/YY or MM/YYYY
    """
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

        now = datetime.now()

        if year < now.year:
            return True
        if year == now.year and month < now.month:
            return True

        return False
    except Exception:
        return None


def extract_dosage_info(text):
    """
    Simple dosage extraction examples:
    1-0-1, 0-1-0, once daily, twice daily, SOS, etc.
    """
    if not text:
        return []

    patterns = [
        r'\b\d-\d-\d\b',
        r'\bonce daily\b',
        r'\btwice daily\b',
        r'\bthrice daily\b',
        r'\bSOS\b',
        r'\bafter food\b',
        r'\bbefore food\b',
    ]

    found = []
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        found.extend(matches)

    # unique preserve order
    unique = []
    seen = set()
    for item in found:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            unique.append(item)

    return unique


# =========================
# SERVAM TTS
# =========================

def generate_servam_tts(text):
    """
    Sends text to Servam TTS API.
    IMPORTANT:
    You MUST set correct SERVAM_API_URL in Render env variables.

    Expected response examples:
    {
      "audio_url": "https://....mp3"
    }
    OR
    {
      "url": "https://....mp3"
    }
    """
    api_key = settings.SERVAM_API_KEY
    api_url = settings.SERVAM_API_URL

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

            if "audio_url" in data:
                return data["audio_url"]

            if "url" in data:
                return data["url"]

        return None

    except Exception:
        return None


# =========================
# RESULT / VOICE HELPERS
# =========================

def build_voice_message(result):
    """
    Build simple voice summary text.
    """
    if result.get("error"):
        return "Unable to analyze the medicine safely. Please try again with clearer images."

    parts = []

    if result.get("matched"):
        parts.append("Prescription and medicine appear to match.")
    else:
        parts.append("Warning. Prescription and medicine may not match.")

    if result.get("expiry_found"):
        if result.get("expired") is True:
            parts.append("The medicine appears expired. Do not use it.")
        elif result.get("expired") is False:
            parts.append("The medicine does not appear expired.")
        else:
            parts.append("Expiry date was found, but could not be fully verified.")
    else:
        parts.append("Expiry date could not be detected clearly.")

    if result.get("dosage"):
        parts.append(f"Dosage instructions found: {', '.join(result['dosage'])}.")
    else:
        parts.append("No clear dosage instructions were found.")

    parts.append("Please confirm with a doctor or pharmacist before consuming.")

    return " ".join(parts)


# =========================
# MAIN VIEW
# =========================

def home(request):
    result = None

    if request.method == "POST":
        try:
            prescription_path = None
            medicine_path = None

            # 1) Uploaded files
            prescription_file = request.FILES.get("prescription_image")
            medicine_file = request.FILES.get("medicine_image")

            if prescription_file:
                prescription_path = save_uploaded_file(prescription_file, folder="prescriptions")

            if medicine_file:
                medicine_path = save_uploaded_file(medicine_file, folder="medicines")

            # 2) Camera captures (base64)
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

            # OCR via OCR.Space API
            prescription_text = extract_text_from_image(prescription_path)
            medicine_text = extract_text_from_image(medicine_path)

            prescription_text = clean_text(prescription_text)
            medicine_text = clean_text(medicine_text)

            # Candidates
            prescription_candidates = extract_medicine_candidates(prescription_text)
            medicine_candidates = extract_medicine_candidates(medicine_text)

            matched_prescription, matched_medicine, similarity = best_match(
                prescription_candidates, medicine_candidates
            )

            matched = similarity >= 0.60

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

            result["voice_message"] = build_voice_message(result)
            result["servam_audio_url"] = generate_servam_tts(result["voice_message"])

        except Exception as e:
            result = {
                "error": f"An error occurred: {str(e)}"
            }

    return render(request, "home.html", {"result": result})
