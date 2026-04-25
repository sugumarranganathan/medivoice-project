import os
import re
import base64
import requests
from io import BytesIO
from datetime import datetime
from difflib import SequenceMatcher

from PIL import Image
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
# IMAGE COMPRESSION FOR OCR SPACE FREE PLAN
# ==========================================

def prepare_image_for_ocr(image_path, max_size_bytes=1400000):
    try:
        if not image_path or not os.path.exists(image_path):
            return image_path, "Image file not found"

        original_size = os.path.getsize(image_path)

        if original_size <= max_size_bytes:
            return image_path, f"Original size OK ({original_size} bytes)"

        img = Image.open(image_path)

        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        elif img.mode != "RGB":
            img = img.convert("RGB")

        width, height = img.size
        compressed_path = os.path.splitext(image_path)[0] + "_ocr.jpg"

        for scale in [1.0, 0.9, 0.8, 0.7, 0.6]:
            new_w = max(800, int(width * scale))
            new_h = max(800, int(height * scale))

            resized = img.resize((new_w, new_h), Image.LANCZOS)

            for quality in [85, 75, 65, 55, 45, 35]:
                resized.save(
                    compressed_path,
                    format="JPEG",
                    quality=quality,
                    optimize=True
                )

                if os.path.exists(compressed_path):
                    new_size = os.path.getsize(compressed_path)
                    if new_size <= max_size_bytes:
                        return compressed_path, f"Compressed from {original_size} to {new_size} bytes"

        if os.path.exists(compressed_path):
            new_size = os.path.getsize(compressed_path)
            return compressed_path, f"Compressed best effort: {original_size} -> {new_size} bytes"

        return image_path, f"Compression failed, original size {original_size} bytes"

    except Exception as e:
        return image_path, f"Compression error: {str(e)}"


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


def normalize_text(text):
    return re.sub(r"[^a-z0-9]", "", text.lower()) if text else ""


def normalize_word(word):
    return re.sub(r"[^a-z0-9]", "", word.lower()) if word else ""


# ==========================================
# OCR
# ==========================================

def extract_text_from_image(image_path):
    try:
        api_key = os.getenv("OCR_SPACE_API_KEY", "").strip()

        if not api_key:
            return "", "OCR_SPACE_API_KEY missing"

        if not image_path or not os.path.exists(image_path):
            return "", "Image file not found"

        prepared_path, prep_debug = prepare_image_for_ocr(image_path)

        with open(prepared_path, "rb") as f:
            response = requests.post(
                "https://api.ocr.space/parse/image",
                files={"file": f},
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
            return "", f"{prep_debug} | OCR HTTP error: {response.status_code} | {response.text[:300]}"

        try:
            data = response.json()
        except Exception:
            return "", f"{prep_debug} | OCR invalid JSON: {response.text[:300]}"

        if data.get("IsErroredOnProcessing"):
            return "", f"{prep_debug} | OCR processing error: {data.get('ErrorMessage', 'Unknown error')}"

        parsed_results = data.get("ParsedResults", [])
        if not parsed_results:
            return "", f"{prep_debug} | No ParsedResults from OCR"

        text = "\n".join(
            item.get("ParsedText", "")
            for item in parsed_results
            if isinstance(item, dict)
        ).strip()

        if not text:
            return "", f"{prep_debug} | OCR returned empty text"

        return clean_text(text), f"{prep_debug} | OCR success"

    except Exception as e:
        return "", f"OCR exception: {str(e)}"


# ==========================================
# MEDICINE MATCHING (SMART)
# ==========================================

def extract_prescription_medicine_candidates(text):
    if not text:
        return []

    candidates = []

    lines = text.splitlines()
    for line in lines:
        line_clean = line.strip()
        if not line_clean:
            continue

        # likely medicine lines
        if re.search(r'\b(tab|cap|syr|tablet|capsule|inj|drop)\b', line_clean, re.I):
            words = re.findall(r'[A-Za-z0-9]+', line_clean)
            filtered = []
            skip_words = {
                'tab', 'tablet', 'tablets', 'cap', 'capsule', 'capsules',
                'syr', 'syrup', 'inj', 'injection', 'drop', 'drops',
                'mg', 'ml', 'days', 'day'
            }

            for w in words:
                nw = normalize_word(w)
                if len(nw) >= 4 and nw not in skip_words and not nw.isdigit():
                    filtered.append(w)

            if filtered:
                candidates.extend(filtered)

    # fallback: all possible words
    if not candidates:
        words = re.findall(r'[A-Za-z]{4,}', text)
        candidates = words

    # unique
    unique = []
    seen = set()
    for c in candidates:
        n = normalize_word(c)
        if n and n not in seen:
            seen.add(n)
            unique.append(c)

    return unique[:20]


def extract_medicine_brand_candidates(text):
    if not text:
        return []

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    candidates = []

    # first 8 lines usually contain brand name
    for line in lines[:8]:
        words = re.findall(r'[A-Za-z0-9]+', line)
        for w in words:
            nw = normalize_word(w)
            if len(nw) >= 4 and not nw.isdigit():
                candidates.append(w)

    unique = []
    seen = set()
    for c in candidates:
        n = normalize_word(c)
        if n and n not in seen:
            seen.add(n)
            unique.append(c)

    return unique[:20]


def fuzzy_word_match(a, b):
    na = normalize_word(a)
    nb = normalize_word(b)

    if not na or not nb:
        return 0.0

    if na == nb:
        return 1.0

    # remove common OCR confusion endings
    # gudcet vs gudcef
    if len(na) >= 5 and len(nb) >= 5:
        if na[:-1] == nb[:-1]:
            return 0.95
        if na[:-2] == nb[:-2]:
            return 0.90

    return SequenceMatcher(None, na, nb).ratio()


def find_best_medicine_match(prescription_text, medicine_text):
    p_candidates = extract_prescription_medicine_candidates(prescription_text)
    m_candidates = extract_medicine_brand_candidates(medicine_text)

    best_score = 0.0
    best_p = "Not found"
    best_m = "Not found"

    for p in p_candidates:
        for m in m_candidates:
            score = fuzzy_word_match(p, m)
            if score > best_score:
                best_score = score
                best_p = p
                best_m = m

    # also compare full text fallback
    p_norm = normalize_text(prescription_text)
    m_norm = normalize_text(medicine_text)
    full_score = 0.0
    if p_norm and m_norm:
        full_score = SequenceMatcher(None, p_norm[:400], m_norm[:400]).ratio()

    final_score = max(best_score, full_score * 0.4)

    matched = final_score >= 0.72

    return {
        "matched": matched,
        "similarity": round(final_score * 100, 2),
        "matched_prescription": best_p,
        "matched_medicine": best_m,
        "prescription_candidates": p_candidates,
        "medicine_candidates": m_candidates,
    }


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
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1).replace("-", "/")

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

    cleaned = []
    seen = set()
    for item in results:
        item = re.sub(r"\s+", " ", item).strip()
        if item not in seen:
            seen.add(item)
            cleaned.append(item)

    return cleaned


# ==========================================
# VOICE MESSAGE
# ==========================================

def build_voice_message(result):
    if result.get("error"):
        return "Unable to analyze the medicine safely. Please try again with clearer images."

    parts = []

    if result.get("matched"):
        parts.append(
            f"Good news. The prescription medicine {result.get('matched_prescription')} matches the strip medicine {result.get('matched_medicine')}."
        )
    else:
        parts.append("Warning. The prescription and medicine may not match.")

    if result.get("expiry_found"):
        if result.get("expired") is True:
            parts.append(f"The medicine appears expired. Expiry date detected as {result['expiry_found']}.")
        elif result.get("expired") is False:
            parts.append(f"The medicine does not appear expired. Expiry date detected as {result['expiry_found']}.")
        else:
            parts.append(f"Expiry date detected as {result['expiry_found']}, but it could not be fully verified.")
    else:
        parts.append("Expiry date could not be detected clearly.")

    if result.get("dosage"):
        parts.append(f"Dosage instructions found: {', '.join(result['dosage'])}.")
    else:
        parts.append("No clear dosage instructions were found.")

    parts.append("Please confirm with a doctor or pharmacist before consuming.")
    return " ".join(parts)


# ==========================================
# OPTIONAL SERVAM TTS
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

        data = response.json()
        return data.get("audio_url") or data.get("url")

    except Exception:
        return None


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

            # Smart medicine matching
            match_info = find_best_medicine_match(prescription_text, medicine_text)

            expiry_found = extract_expiry_date(medicine_text)
            expired = is_expired(expiry_found)
            dosage = extract_dosage_info(prescription_text)

            result = {
                "error": None,
                "prescription_text": prescription_text if prescription_text else "No text extracted",
                "medicine_text": medicine_text if medicine_text else "No text extracted",
                "prescription_debug": prescription_debug,
                "medicine_debug": medicine_debug,
                "matched_prescription": match_info["matched_prescription"],
                "matched_medicine": match_info["matched_medicine"],
                "similarity": match_info["similarity"],
                "matched": match_info["matched"],
                "expiry_found": expiry_found,
                "expired": expired,
                "dosage": dosage,
                "prescription_candidates": match_info["prescription_candidates"],
                "medicine_candidates": match_info["medicine_candidates"],
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
                "matched_prescription": "Not found",
                "matched_medicine": "Not found",
                "similarity": 0,
                "matched": False,
                "expiry_found": None,
                "expired": None,
                "dosage": [],
                "prescription_candidates": [],
                "medicine_candidates": [],
                "voice_message": "Unable to analyze the medicine safely. Please try again.",
                "servam_audio_url": None,
            }

    return render(request, "home.html", {"result": result})
