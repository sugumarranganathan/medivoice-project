import os
import re
import base64
import requests
from io import BytesIO
from datetime import datetime

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
            return "", f"{prep_debug} | OCR HTTP error: {response.status_code}"

        try:
            data = response.json()
        except Exception:
            return "", f"{prep_debug} | OCR invalid JSON"

        if data.get("IsErroredOnProcessing"):
            return "", f"{prep_debug} | OCR processing error"

        parsed_results = data.get("ParsedResults", [])
        if not parsed_results:
            return "", f"{prep_debug} | No ParsedResults"

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
# MEDICINE NAME EXTRACTION
# ==========================================

def extract_prescription_medicine_name(text):
    if not text:
        return "Not found"

    lines = [line.strip() for line in text.splitlines() if line.strip()]

    for line in lines:
        # Example: Tab Gudcef 200mg 1-0-1
        if re.search(r'\b(tab|tablet|cap|capsule|syr|syrup)\b', line, re.I):
            line_clean = re.sub(r'^\s*(tab|tablet|cap|capsule|syr|syrup)\s+', '', line, flags=re.I)

            # remove dosage pattern and days
            line_clean = re.sub(r'\b\d+\s*-\s*\d+\s*-\s*\d+\b', '', line_clean, flags=re.I)
            line_clean = re.sub(r'\bx\s*\d+\s*days?\b', '', line_clean, flags=re.I)
            line_clean = re.sub(r'\b\d+\s*ml\s*-\s*\d+\s*-\s*\d+\s*ml\b', '', line_clean, flags=re.I)

            # take first medicine-like words
            words = re.findall(r'[A-Za-z0-9]+', line_clean)
            if words:
                medicine_words = []
                for w in words:
                    nw = normalize_word(w)
                    if nw in ['mg', 'ml', 'days', 'day']:
                        continue
                    medicine_words.append(w)
                    # Usually medicine name + strength
                    if len(medicine_words) >= 2:
                        break

                if medicine_words:
                    return " ".join(medicine_words)

    return "Not found"


# ==========================================
# EXPIRY DATE
# ==========================================

def extract_expiry_date(text):
    if not text:
        return "Not found"

    patterns = [
        r"EXP[:\s\-]*([0-1]?\d[/\-][0-9]{2,4})",
        r"EXPIRY[:\s\-]*([0-1]?\d[/\-][0-9]{2,4})",
        r"\b([0-1]?\d[/\-][0-9]{4})\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1).replace("-", "/")

    return "Not found"


# ==========================================
# DOSAGE EXTRACTION (ONLY SIMPLE OUTPUT)
# ==========================================

def convert_dosage_to_text(code, days=None):
    code = code.replace(" ", "")

    dosage_map = {
        "1-0-1": "Morning and Evening 1 tablet",
        "1-0-0": "Morning 1 tablet",
        "0-1-0": "Afternoon 1 tablet",
        "0-0-1": "Evening 1 tablet",
        "1-1-0": "Morning and Afternoon 1 tablet",
        "1-1-1": "Morning, Afternoon and Evening 1 tablet",
        "0-1-1": "Afternoon and Evening 1 tablet",
    }

    text = dosage_map.get(code, code)

    if days:
        text += f" for {days} days"

    return text


def extract_days(text):
    if not text:
        return None

    match = re.search(r'x\s*(\d+)\s*days?', text, re.I)
    if match:
        return match.group(1)

    match = re.search(r'(\d+)\s*days?', text, re.I)
    if match:
        return match.group(1)

    return None


def extract_dosage_text(text, medicine_name):
    if not text:
        return "Not found"

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    days = extract_days(text)

    for line in lines:
        if medicine_name.lower().split()[0] in line.lower():
            dose_match = re.search(r'\b(\d+\s*-\s*\d+\s*-\s*\d+)\b', line)
            if dose_match:
                return convert_dosage_to_text(dose_match.group(1), days)

    # fallback: first dosage pattern anywhere
    for line in lines:
        dose_match = re.search(r'\b(\d+\s*-\s*\d+\s*-\s*\d+)\b', line)
        if dose_match:
            return convert_dosage_to_text(dose_match.group(1), days)

    return "Not found"


# ==========================================
# SIMPLE VOICE MESSAGE (ONLY 3 DETAILS)
# ==========================================

def build_voice_message(result):
    if result.get("error"):
        return "Please provide both prescription and medicine images."

    medicine_name = result.get("medicine_name", "Not found")
    expiry_date = result.get("expiry_date", "Not found")
    dosage_text = result.get("dosage_text", "Not found")

    return (
        f"Medicine name {medicine_name}. "
        f"Expiry date {expiry_date}. "
        f"Dosage {dosage_text}."
    )


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
# MAIN VIEW (ONLY 3 DETAILS IN RESULT)
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
            prescription_text, _ = extract_text_from_image(prescription_path)
            medicine_text, _ = extract_text_from_image(medicine_path)

            # Only required details
            medicine_name = extract_prescription_medicine_name(prescription_text)
            expiry_date = extract_expiry_date(medicine_text)
            dosage_text = extract_dosage_text(prescription_text, medicine_name)

            result = {
                "error": None,
                "medicine_name": medicine_name,
                "expiry_date": expiry_date,
                "dosage_text": dosage_text,
            }

            result["voice_message"] = build_voice_message(result)
            result["servam_audio_url"] = generate_servam_tts(result["voice_message"])

        except Exception as e:
            result = {
                "error": f"An error occurred: {str(e)}",
                "medicine_name": "Not found",
                "expiry_date": "Not found",
                "dosage_text": "Not found",
                "voice_message": "Unable to analyze. Please try again.",
                "servam_audio_url": None,
            }

    return render(request, "home.html", {"result": result})
