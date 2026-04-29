import os
import re
import cv2
import uuid
import base64
import requests
import numpy as np

from io import BytesIO
from PIL import Image, ImageEnhance, ImageFilter
from django.conf import settings
from django.shortcuts import render
from django.core.files.storage import default_storage

# ==========================================
# SAFE PADDLE OCR IMPORT (OPTIONAL)
# ==========================================
PADDLE_AVAILABLE = False
ocr = None

try:
    from paddleocr import PaddleOCR
    ocr = PaddleOCR(
        use_angle_cls=True,
        lang='en',
        show_log=False
    )
    PADDLE_AVAILABLE = True
    print("✅ PaddleOCR loaded successfully")
except Exception as e:
    print("⚠️ PaddleOCR not available, fallback to OCR.Space:", e)
    PADDLE_AVAILABLE = False


# ==========================================
# FILE SAVE HELPERS
# ==========================================
def save_uploaded_file(file_obj, folder="uploads"):
    try:
        ext = os.path.splitext(file_obj.name)[1].lower() or ".jpg"
        unique_name = f"{uuid.uuid4().hex}{ext}"
        relative_path = f"{folder}/{unique_name}"
        saved_path = default_storage.save(relative_path, file_obj)
        return os.path.join(settings.MEDIA_ROOT, saved_path)
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
        elif "image/jpeg" in header or "image/jpg" in header:
            ext = "jpg"

        final_name = f"{uuid.uuid4().hex}.{ext}"
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


def normalize_word(word):
    return re.sub(r"[^a-z0-9]", "", word.lower()) if word else ""


def normalize_text_for_match(text):
    return re.sub(r"[^a-z0-9]", "", text.lower()) if text else ""


# ==========================================
# IMAGE PREPROCESSING
# ==========================================
def enhance_image_for_ocr(image_path):
    """
    Create multiple enhanced versions for better OCR accuracy
    """
    try:
        if not image_path or not os.path.exists(image_path):
            return []

        img = Image.open(image_path).convert("RGB")

        variants = []

        # Original
        variants.append(("original", img.copy()))

        # Grayscale + contrast
        gray = img.convert("L")
        gray = ImageEnhance.Contrast(gray).enhance(2.2)
        variants.append(("gray_contrast", gray.convert("RGB")))

        # Sharpen
        sharp = gray.filter(ImageFilter.SHARPEN)
        variants.append(("sharp", sharp.convert("RGB")))

        # Bigger image
        w, h = img.size
        upscale = img.resize((int(w * 1.8), int(h * 1.8)), Image.LANCZOS)
        upscale_gray = upscale.convert("L")
        upscale_gray = ImageEnhance.Contrast(upscale_gray).enhance(2.5)
        variants.append(("upscale", upscale_gray.convert("RGB")))

        # Threshold (good for printed strip text)
        arr = np.array(upscale_gray)
        _, thresh = cv2.threshold(arr, 160, 255, cv2.THRESH_BINARY)
        thresh_img = Image.fromarray(thresh).convert("RGB")
        variants.append(("threshold", thresh_img))

        return variants

    except Exception as e:
        print("Enhance image error:", e)
        return []


# ==========================================
# PADDLE OCR
# ==========================================
def extract_text_with_paddleocr(image_path):
    """
    Run OCR on multiple enhanced variants and return best text
    """
    if not PADDLE_AVAILABLE or ocr is None:
        return "", []

    try:
        variants = enhance_image_for_ocr(image_path)
        if not variants:
            return "", []

        best_text = ""
        best_lines = []
        best_score = -1

        for variant_name, pil_img in variants:
            try:
                img_np = np.array(pil_img)
                result = ocr.ocr(img_np, cls=True)

                lines = []
                total_score = 0.0

                if result and result[0]:
                    for item in result[0]:
                        try:
                            text = item[1][0].strip()
                            conf = float(item[1][1])

                            if text:
                                lines.append(text)
                                total_score += conf
                        except Exception:
                            continue

                joined_text = "\n".join(lines).strip()
                score = total_score + (len(joined_text) * 0.01)

                if score > best_score and joined_text:
                    best_score = score
                    best_text = joined_text
                    best_lines = lines

                print(f"PaddleOCR [{variant_name}] => {joined_text}")

            except Exception as inner_e:
                print(f"PaddleOCR variant error [{variant_name}]:", inner_e)
                continue

        return clean_text(best_text), best_lines

    except Exception as e:
        print("PaddleOCR extraction error:", e)
        return "", []


# ==========================================
# OCR.SPACE FALLBACK
# ==========================================
def prepare_image_for_ocr_space(image_path, max_size_bytes=1400000):
    try:
        if not image_path or not os.path.exists(image_path):
            return image_path

        original_size = os.path.getsize(image_path)

        if original_size <= max_size_bytes:
            return image_path

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
                        return compressed_path

        if os.path.exists(compressed_path):
            return compressed_path

        return image_path

    except Exception as e:
        print("OCR.Space prepare error:", e)
        return image_path


def extract_text_with_ocr_space(image_path):
    try:
        api_key = os.getenv("OCR_SPACE_API_KEY", "").strip()

        if not api_key:
            print("OCR_SPACE_API_KEY missing")
            return "", []

        if not image_path or not os.path.exists(image_path):
            return "", []

        prepared_path = prepare_image_for_ocr_space(image_path)

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
            print("OCR.Space HTTP error:", response.status_code)
            return "", []

        data = response.json()

        if data.get("IsErroredOnProcessing"):
            print("OCR.Space processing error")
            return "", []

        parsed_results = data.get("ParsedResults", [])
        if not parsed_results:
            return "", []

        text = "\n".join(
            item.get("ParsedText", "")
            for item in parsed_results
            if isinstance(item, dict)
        ).strip()

        lines = [line.strip() for line in text.splitlines() if line.strip()]

        print("OCR.Space =>", text)

        return clean_text(text), lines

    except Exception as e:
        print("OCR.Space exception:", e)
        return "", []


# ==========================================
# HYBRID OCR (PADDLE FIRST, FALLBACK OCR.SPACE)
# ==========================================
def extract_text_from_image(image_path):
    # 1) Try PaddleOCR first
    if PADDLE_AVAILABLE:
        text, lines = extract_text_with_paddleocr(image_path)
        if text:
            print("✅ Using PaddleOCR result")
            return text, lines

    # 2) Fallback to OCR.Space
    text, lines = extract_text_with_ocr_space(image_path)
    if text:
        print("✅ Using OCR.Space fallback result")
        return text, lines

    return "", []


# ==========================================
# MEDICINE NAME EXTRACTION
# ==========================================
BAD_MED_WORDS = {
    "tablet", "tablets", "capsule", "capsules", "ip", "rx", "batch", "mfg",
    "exp", "expiry", "alkem", "composition", "contains", "each", "film",
    "coated", "cefpodoxime", "proxetil", "mg", "ml", "tab", "cap", "syr",
    "syrup", "dr", "clinic", "hospital", "doctor", "consultant"
}


def extract_medicine_name_from_prescription(text):
    if not text:
        return "Not found"

    lines = [line.strip() for line in text.splitlines() if line.strip()]

    patterns = [
        r'(?:tab|tablet|cap|capsule|syr|syrup)\s+([A-Za-z]{3,20}(?:\s+\d{1,4})?)',
        r'([A-Za-z]{3,20}(?:\s+\d{1,4})?)\s*[-:]?\s*[01]\s*-\s*[01]\s*-\s*[01]',
    ]

    for line in lines:
        for pattern in patterns:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                candidate = match.group(1).strip()
                if candidate and normalize_word(candidate) not in {"tab", "tablet", "cap"}:
                    return candidate

    # fallback
    for line in lines:
        if re.search(r'\b[01]\s*-\s*[01]\s*-\s*[01]\b', line):
            words = re.findall(r'[A-Za-z0-9]+', line)
            filtered = []

            for w in words:
                nw = normalize_word(w)
                if nw in BAD_MED_WORDS:
                    continue
                if len(w) >= 3 or re.fullmatch(r'\d+', w):
                    filtered.append(w)
                if len(filtered) >= 2:
                    break

            if filtered:
                return " ".join(filtered)

    return "Not found"


def extract_medicine_name_from_strip(text):
    if not text:
        return "Not found"

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    full_text = " ".join(lines)

    # Strong pattern: Gudcef 200
    strong_matches = re.findall(r'\b([A-Z][A-Za-z]{2,20}\s?\d{1,4})\b', full_text)
    for match in strong_matches:
        candidate = match.strip()
        nw = normalize_word(candidate)

        if any(bad in nw for bad in ["tablet", "tablets", "capsule", "capsules", "alkem", "exp", "mfg"]):
            continue
        if len(candidate) >= 4:
            return candidate

    # Fallback from top lines
    for line in lines[:5]:
        words = re.findall(r'[A-Za-z0-9]+', line)
        filtered = []

        for w in words:
            nw = normalize_word(w)
            if nw in BAD_MED_WORDS:
                continue
            if len(w) >= 3 or re.fullmatch(r'\d+', w):
                filtered.append(w)
            if len(filtered) >= 2:
                break

        if filtered:
            return " ".join(filtered)

    return "Not found"


# ==========================================
# EXPIRY DATE EXTRACTION
# ==========================================
def extract_expiry_date(text):
    if not text:
        return "Not found"

    merged = text.replace("\n", " ")

    patterns = [
        r'EXP(?:IRY)?[:\s\-]*([0-1]?\d[\/\-]\d{4})',
        r'EXP(?:IRY)?[:\s\-]*([0-1]?\d[\/\-]\d{2})',
        r'\b([0-1]?\d[\/\-]\d{4})\b',
        r'\b([0-1]?\d[\/\-]\d{2})\b',
    ]

    for pattern in patterns:
        match = re.search(pattern, merged, re.IGNORECASE)
        if match:
            return match.group(1).replace("-", "/").strip()

    return "Not found"


# ==========================================
# DOSAGE EXTRACTION
# ==========================================
def extract_days(text):
    if not text:
        return None

    patterns = [
        r'x\s*(\d+)\s*days?',
        r'for\s*(\d+)\s*days?',
        r'(\d+)\s*days?'
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)

    return "5"


def convert_dosage_to_text(code, days=None):
    code = code.replace(" ", "")
    days = days or "5"

    dosage_map = {
        "1-0-1": f"Morning and Night for {days} days",
        "1-1-0": f"Morning and Afternoon for {days} days",
        "0-0-1": f"Night for {days} days",
        "0-1-0": f"Afternoon for {days} days",
        "1-0-0": f"Morning for {days} days",
        "0-1-1": f"Afternoon and Night for {days} days",
        "1-1-1": f"Morning, Afternoon and Night for {days} days",
    }

    return dosage_map.get(code, f"Dosage code {code} for {days} days")


def extract_dosage_text(prescription_text, medicine_name):
    if not prescription_text:
        return "Not found"

    lines = [line.strip() for line in prescription_text.splitlines() if line.strip()]
    days = extract_days(prescription_text)
    med_key = normalize_text_for_match(medicine_name.split()[0]) if medicine_name and medicine_name != "Not found" else ""

    # Try matching medicine line first
    if med_key:
        for line in lines:
            if med_key in normalize_text_for_match(line):
                dose_match = re.search(r'\b([01]\s*-\s*[01]\s*-\s*[01])\b', line)
                if dose_match:
                    code = dose_match.group(1).replace(" ", "")
                    return convert_dosage_to_text(code, days)

    # Fallback: any dosage pattern
    for line in lines:
        dose_match = re.search(r'\b([01]\s*-\s*[01]\s*-\s*[01])\b', line)
        if dose_match:
            code = dose_match.group(1).replace(" ", "")
            return convert_dosage_to_text(code, days)

    return "Not found"


# ==========================================
# FINAL MEDICINE NAME CHOOSER
# ==========================================
def choose_best_medicine_name(prescription_name, strip_name):
    if strip_name != "Not found":
        return strip_name
    if prescription_name != "Not found":
        return prescription_name
    return "Not found"


# ==========================================
# VOICE MESSAGE
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
# OPTIONAL SARVAM TTS
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
            print("Sarvam TTS error:", response.status_code, response.text)
            return None

        data = response.json()
        return data.get("audio_url") or data.get("url")

    except Exception as e:
        print("Sarvam TTS exception:", e)
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

            # Uploaded files
            prescription_file = request.FILES.get("prescription_image")
            medicine_file = request.FILES.get("medicine_image")

            if prescription_file:
                prescription_path = save_uploaded_file(prescription_file, folder="prescriptions")

            if medicine_file:
                medicine_path = save_uploaded_file(medicine_file, folder="medicines")

            # Camera base64 images
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

            # OCR (Hybrid)
            prescription_text, _ = extract_text_from_image(prescription_path)
            medicine_text, _ = extract_text_from_image(medicine_path)

            print("\n========== PRESCRIPTION OCR ==========")
            print(prescription_text)
            print("=====================================\n")

            print("\n=========== MEDICINE OCR ============")
            print(medicine_text)
            print("=====================================\n")

            # Extract details
            prescription_name = extract_medicine_name_from_prescription(prescription_text)
            strip_name = extract_medicine_name_from_strip(medicine_text)
            medicine_name = choose_best_medicine_name(prescription_name, strip_name)

            expiry_date = extract_expiry_date(medicine_text)
            dosage_text = extract_dosage_text(prescription_text, medicine_name)

            result = {
                "error": None,
                "medicine_name": medicine_name or "Not found",
                "expiry_date": expiry_date or "Not found",
                "dosage_text": dosage_text or "Not found",
            }

            result["voice_message"] = build_voice_message(result)
            result["servam_audio_url"] = generate_servam_tts(result["voice_message"])

        except Exception as e:
            print("Main processing exception:", e)
            result = {
                "error": f"An error occurred: {str(e)}",
                "medicine_name": "Not found",
                "expiry_date": "Not found",
                "dosage_text": "Not found",
                "voice_message": "Unable to analyze. Please try again.",
                "servam_audio_url": None,
            }

    return render(request, "home.html", {"result": result})
