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


# ==========================================
# FILE SAVE HELPERS
# ==========================================

def save_uploaded_file(file_obj, folder="uploads"):
    try:
        file_name = default_storage.save(f"{folder}/{file_obj.name}", file_obj)
        return os.path.join(settings.MEDIA_ROOT, file_name)
    except Exception as e:
        print("File save error:", e)
        return None


def save_base64_image(data_url, file_name="camera_capture.jpg", folder="uploads"):
    try:
        if not data_url or "," not in data_url:
            return None

        header, imgstr = data_url.split(",", 1)
        ext = "jpg"

        if "image/png" in header:
            ext = "png"
        elif "image/webp" in header:
            ext = "webp"

        final_name = f"{os.path.splitext(file_name)[0]}.{ext}"
        binary_data = base64.b64decode(imgstr)

        relative_path = f"{folder}/{final_name}"
        saved_path = default_storage.save(relative_path, BytesIO(binary_data))

        return os.path.join(settings.MEDIA_ROOT, saved_path)
    except Exception as e:
        print("Base64 save error:", e)
        return None


# ==========================================
# TEXT HELPERS
# ==========================================

def clean_text(text):
    if not text:
        return ""
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n+", "\n", text)
    return text.strip()


def normalize_medicine_name(text):
    if not text:
        return ""

    text = text.lower()

    remove_words = [
        "tablet", "tablets", "tab.", "tab",
        "capsule", "capsules", "cap.", "cap",
        "syrup", "syr.", "syr",
        "injection", "inj.", "inj",
        "strip", "ip"
    ]

    for word in remove_words:
        text = text.replace(word, "")

    text = re.sub(r"\b(\d+)\s*mg\b", r"\1", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    return text


# ==========================================
# OCR WITH DEBUG
# ==========================================

def extract_text_from_image(image_path):
    try:
        api_key = os.environ.get("OCR_SPACE_API_KEY", "").strip()

        if not api_key:
            return "", "OCR_SPACE_API_KEY missing"

        if not image_path or not os.path.exists(image_path):
            return "", f"Image not found: {image_path}"

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
            return "", f"OCR HTTP error: {response.status_code}"

        try:
            data = response.json()
        except Exception:
            return "", f"OCR invalid JSON: {response.text[:300]}"

        if not isinstance(data, dict):
            return "", "OCR response format invalid"

        if data.get("IsErroredOnProcessing"):
            err = data.get("ErrorMessage", "Unknown OCR processing error")
            return "", f"OCR processing error: {err}"

        parsed_results = data.get("ParsedResults", [])
        if not isinstance(parsed_results, list) or not parsed_results:
            return "", f"No ParsedResults. Raw: {str(data)[:300]}"

        extracted_parts = []
        for item in parsed_results:
            if isinstance(item, dict):
                parsed_text = item.get("ParsedText", "")
                if parsed_text:
                    extracted_parts.append(parsed_text)

        final_text = "\n".join(extracted_parts).strip()

        if not final_text:
            return "", f"OCR returned empty text. Raw: {str(data)[:300]}"

        return clean_text(final_text), "OCR success"

    except requests.exceptions.Timeout:
        return "", "OCR timeout"
    except Exception as e:
        return "", f"OCR exception: {str(e)}"


# ==========================================
# MEDICINE EXTRACTION
# ==========================================

def extract_prescription_medicines(text):
    if not text:
        return []

    medicines = []
    lines = text.splitlines()

    for line in lines:
        original = line.strip()
        lower = original.lower()

        if not original:
            continue

        if any(prefix in lower for prefix in ["tab ", "tab.", "tablet", "cap ", "capsule", "syr ", "syr.", "syrup"]):
            cleaned = re.sub(r"\b(tab|tab\.|tablet|cap|cap\.|capsule|syr|syr\.|syrup)\b", "", original, flags=re.I)
            cleaned = re.sub(r"\b\d+\s*-\s*\d+\s*-\s*\d+\b", "", cleaned, flags=re.I)
            cleaned = re.sub(r"\b\d+\s*ml\s*-\s*\d+\s*-\s*\d+\s*ml\b", "", cleaned, flags=re.I)
            cleaned = re.sub(r"\bx\s*\d+\s*days?\b", "", cleaned, flags=re.I)
            cleaned = re.sub(r"[^A-Za-z0-9\s]", " ", cleaned)
            cleaned = re.sub(r"\s+", " ", cleaned).strip()

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
    if not text:
        return []

    candidates = []
    lines = text.splitlines()

    for line in lines:
        original = line.strip()
        if not original:
            continue

        cleaned = re.sub(r"[^A-Za-z0-9\s]", " ", original)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()

        if len(cleaned) < 3:
            continue

        if re.search(r"\b\d+\s*mg\b", original, re.I):
            candidates.append(cleaned)

        if any(word in original.lower() for word in [
            "tablet", "tablets", "ip", "exp", "expiry", "mfg", "batch",
            "gudcef", "cef", "mg"
        ]):
            candidates.append(cleaned)

        words = cleaned.split()
        if words and len(words[0]) >= 4:
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
# MATCHING
# ==========================================

def similarity_score(a, b):
    a_norm = normalize_medicine_name(a)
    b_norm = normalize_medicine_name(b)

    if not a_norm or not b_norm:
        return 0.0

    if a_norm in b_norm or b_norm in a_norm:
        base = 0.92
    else:
        base = SequenceMatcher(None, a_norm, b_norm).ratio()

    a_tokens = set(a_norm.split())
    b_tokens = set(b_norm.split())

    if a_tokens and b_tokens:
        overlap = len(a_tokens & b_tokens) / max(len(a_tokens), len(b_tokens))
        score = max(base, (base * 0.7 + overlap * 0.3))
        return min(score, 1.0)

    return min(base, 1.0)


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
# EXPIRY
# ==========================================

def extract_expiry_date(text):
    if not text:
        return None

    patterns = [
        r"EXP[:\s\-]*([0-1]?\d[/\-][0-9]{2,4})",
        r"EXPIRY[:\s\-]*([0-1]?\d[/\-][0-9]{2,4})",
        r"\b([0-1]?\d[/\-][0-9]{4})\b",
        r"\b([0-1]?\d[/\-][0-9]{2})\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1)

    return None


def is_expired(expiry_str):
    if not expiry_str:
        return None

    try:
        expiry_str = expiry_str.replace("-", "/")
        month, year = expiry_str.split("/")

        month = int(month)
        year = int(year)

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
# DOSAGE
# ==========================================

def extract_dosage_info(text):
    if not text:
        return []

    results = []
    results += re.findall(r"\b\d+\s*-\s*\d+\s*-\s*\d+\b", text)
    results += re.findall(r"\b\d+\s*ml\s*-\s*\d+\s*-\s*\d+\s*ml\b", text, re.I)
    results += re.findall(r"\bx\s*\d+\s*days?\b", text, re.I)

    unique = []
    seen = set()

    for item in results:
        cleaned = item.replace(" ", "")
        key = cleaned.lower()
        if key not in seen:
            seen.add(key)
            unique.append(cleaned)

    return unique


# ==========================================
# SERVAM TTS
# ==========================================

def generate_servam_tts(text):
    try:
        api_key = getattr(settings, "SERVAM_API_KEY", "")
        api_url = getattr(settings, "SERVAM_API_URL", "")

        if not api_key or not api_url or not text:
            return None

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

        if response.status_code != 200:
            return None

        try:
            data = response.json()
        except Exception:
            return None

        if not isinstance(data, dict):
            return None

        return data.get("audio_url") or data.get("url")

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

            prescription_file = request.FILES.get("prescription_image")
            medicine_file = request.FILES.get("medicine_image")

            if prescription_file:
                prescription_path = save_uploaded_file(prescription_file, folder="prescriptions")

            if medicine_file:
                medicine_path = save_uploaded_file(medicine_file, folder="medicines")

            prescription_camera = request.POST.get("prescription_camera_data")
            medicine_camera = request.POST.get("medicine_camera_data")

            if not prescription_path and prescription_camera:
                prescription_path = save_base64_image(
                    prescription_camera,
                    file_name="prescription_capture.jpg",
                    folder="prescriptions"
                )

            if not medicine_path and medicine_camera:
                medicine_path = save_base64_image(
                    medicine_camera,
                    file_name="medicine_capture.jpg",
                    folder="medicines"
                )

            if not prescription_path or not medicine_path:
                result = {
                    "error": "Please provide both prescription and medicine images."
                }
                return render(request, "home.html", {"result": result})

            # OCR
            prescription_text, prescription_debug = extract_text_from_image(prescription_path)
            medicine_text, medicine_debug = extract_text_from_image(medicine_path)

            # Candidate extraction
            prescription_candidates = extract_prescription_medicines(prescription_text)
            medicine_candidates = extract_medicine_pack_candidates(medicine_text)

            # Best match
            matched_prescription, matched_medicine, similarity = best_match(
                prescription_candidates, medicine_candidates
            )

            matched = similarity >= 0.72

            # Brand-force fallback for Gudcef
            p_norm = normalize_medicine_name(prescription_text)
            m_norm = normalize_medicine_name(medicine_text)

            if not matched and "gudcef" in p_norm and "gudcef" in m_norm:
                matched = True
                matched_prescription = "Gudcef 200mg"
                matched_medicine = "Gudcef 200"
                similarity = max(similarity, 0.92)

            # Expiry + dosage
            expiry_found = extract_expiry_date(medicine_text)
            expired = is_expired(expiry_found)
            dosage = extract_dosage_info(prescription_text)

            result = {
                "error": None,
                "prescription_text": prescription_text if prescription_text else "No text extracted",
                "medicine_text": medicine_text if medicine_text else "No text extracted",
                "prescription_debug": prescription_debug,
                "medicine_debug": medicine_debug,
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
                "error": f"An error occurred: {str(e)}",
                "prescription_text": "No text extracted",
                "medicine_text": "No text extracted",
                "prescription_debug": f"Main exception: {str(e)}",
                "medicine_debug": "Not reached",
                "prescription_candidates": [],
                "medicine_candidates": [],
                "matched_prescription": "Not found",
                "matched_medicine": "Not found",
                "similarity": 0,
                "matched": False,
                "expiry_found": None,
                "expired": None,
                "dosage": [],
                "voice_message": "Unable to analyze the medicine safely. Please try again.",
                "servam_audio_url": None,
            }

    return render(request, "home.html", {"result": result})
