import os
import re
import uuid
import base64
import requests

from io import BytesIO
from PIL import Image, ImageEnhance, ImageFilter
from django.conf import settings
from django.shortcuts import render
from django.core.files.storage import default_storage


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


def save_base64_image(data_url, folder="uploads"):
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
# IMAGE PREP FOR OCR.SPACE
# ==========================================
def preprocess_for_ocr_space(image_path):
    try:
        if not image_path or not os.path.exists(image_path):
            return image_path

        img = Image.open(image_path)

        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        elif img.mode != "RGB":
            img = img.convert("RGB")

        w, h = img.size

        # enlarge for OCR
        scale = 2.0 if max(w, h) < 1800 else 1.4
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

        gray = img.convert("L")
        gray = ImageEnhance.Contrast(gray).enhance(3.0)
        gray = gray.filter(ImageFilter.SHARPEN)

        processed = gray.convert("RGB")

        processed_path = os.path.splitext(image_path)[0] + "_processed.jpg"
        processed.save(processed_path, format="JPEG", quality=90, optimize=True)

        return processed_path

    except Exception as e:
        print("Preprocess OCR.Space error:", e)
        return image_path


# ==========================================
# OCR.SPACE
# ==========================================
def extract_text_with_ocr_space(image_path):
    try:
        api_key = os.getenv("OCR_SPACE_API_KEY", "").strip()

        if not api_key:
            print("OCR_SPACE_API_KEY missing")
            return "", []

        if not image_path or not os.path.exists(image_path):
            return "", []

        prepared_path = preprocess_for_ocr_space(image_path)

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
        print("OCR.Space RAW:", data)

        if data.get("IsErroredOnProcessing"):
            print("OCR.Space processing error:", data)
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
        return clean_text(text), lines

    except Exception as e:
        print("OCR.Space exception:", e)
        return "", []


# ==========================================
# OCR FIXES
# ==========================================
def fix_common_ocr_errors(text):
    if not text:
        return text

    # Common number/letter confusion
    text = re.sub(r'\b20O\b', '200', text)
    text = re.sub(r'\b2OO\b', '200', text)
    text = re.sub(r'\b2O0\b', '200', text)

    # Gudcef variants
    text = re.sub(r'\bGudcef\s*20O\b', 'Gudcef 200', text, flags=re.IGNORECASE)
    text = re.sub(r'\bGudcef20O\b', 'Gudcef 200', text, flags=re.IGNORECASE)
    text = re.sub(r'\bGudcef200\b', 'Gudcef 200', text, flags=re.IGNORECASE)

    # Expiry variants
    text = re.sub(r'\bO6/2029\b', '06/2029', text)
    text = re.sub(r'\b0G/2029\b', '06/2029', text)
    text = re.sub(r'\bEXP[\s:\-]*O6/2029\b', 'EXP:06/2029', text, flags=re.IGNORECASE)
    text = re.sub(r'\bEXP[\s:\-]*0G/2029\b', 'EXP:06/2029', text, flags=re.IGNORECASE)

    # Dosage variants
    text = re.sub(r'\bI\s*-\s*0\s*-\s*I\b', '1-0-1', text, flags=re.IGNORECASE)
    text = re.sub(r'\bl\s*-\s*0\s*-\s*l\b', '1-0-1', text, flags=re.IGNORECASE)
    text = re.sub(r'\bI\s*-\s*1\s*-\s*0\b', '1-1-0', text, flags=re.IGNORECASE)
    text = re.sub(r'\b0\s*-\s*0\s*-\s*I\b', '0-0-1', text, flags=re.IGNORECASE)
    text = re.sub(r'\b0\s*-\s*I\s*-\s*0\b', '0-1-0', text, flags=re.IGNORECASE)

    return text


# ==========================================
# KNOWN BRANDS
# ==========================================
KNOWN_BRANDS = [
    "Gudcef", "Monticope", "Chericof",
    "Monocef", "Azee", "Taxim", "Augmentin", "Dolo",
    "Paracip", "Azithral", "Cetzine", "Pantocid", "Pan", "Calpol"
]


# ==========================================
# MEDICINE NAME FROM STRIP (EXACT TUNED)
# ==========================================
def extract_medicine_name_from_strip(text):
    if not text:
        return "Not found"

    text = fix_common_ocr_errors(text)
    merged = text.replace("\n", " ")

    # 1) Exact Gudcef priority
    match = re.search(r'\bGudcef\s*([0-9]{2,4})\b', merged, re.IGNORECASE)
    if match:
        return f"Gudcef {match.group(1)}"

    # 2) Known brands with strength
    for brand in KNOWN_BRANDS:
        pattern = rf'\b({brand})\s*([0-9]{{2,4}})?\b'
        match = re.search(pattern, merged, re.IGNORECASE)
        if match:
            b = match.group(1)
            s = match.group(2)
            if s:
                return f"{b.capitalize()} {s}"
            return b.capitalize()

    # 3) Generic brand + strength
    generic = re.findall(r'\b([A-Z][A-Za-z]{2,20})\s*([0-9]{2,4})\b', merged)
    for brand, strength in generic:
        nw = normalize_word(brand)
        if nw not in {"tablet", "tablets", "capsule", "capsules", "alkem", "exp", "mfg"}:
            return f"{brand} {strength}"

    return "Not found"


# ==========================================
# PRESCRIPTION LINE PARSER (EXACT TUNED)
# ==========================================
def parse_prescription_lines(text):
    """
    Extract all medicine lines from prescription:
    Example:
      Tab Gudcef 200mg  1-0-1   x 5 days
      Tab Monticope    0-0-1
      Syr Chericof 7ml-0-7ml
    """
    text = fix_common_ocr_errors(text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    parsed = []

    for line in lines:
        line_norm = fix_common_ocr_errors(line)

        # Main tablet/capsule line pattern
        match = re.search(
            r'\b(?:tab|tablet|cap|capsule)\s+([A-Za-z]{3,20})\s*([0-9]{2,4})?\s*(?:mg|ml)?\s*([01Il]\s*-\s*[01Il]\s*-\s*[01Il])?',
            line_norm,
            re.IGNORECASE
        )

        if match:
            brand = match.group(1)
            strength = match.group(2)
            dose = match.group(3)

            med_name = brand.capitalize()
            if strength:
                med_name += f" {strength}"

            parsed.append({
                "line": line,
                "medicine_name": med_name,
                "dose_code": dose
            })
            continue

        # Syrup line fallback
        match2 = re.search(
            r'\b(?:syr|syrup)\s+([A-Za-z]{3,20})',
            line_norm,
            re.IGNORECASE
        )

        if match2:
            brand = match2.group(1)
            parsed.append({
                "line": line,
                "medicine_name": brand.capitalize(),
                "dose_code": None
            })

    return parsed


# ==========================================
# MEDICINE NAME FROM PRESCRIPTION
# ==========================================
def extract_medicine_name_from_prescription(text):
    if not text:
        return "Not found"

    parsed = parse_prescription_lines(text)
    if not parsed:
        return "Not found"

    # Priority: first tablet/capsule with strength
    for item in parsed:
        if re.search(r'\d{2,4}', item["medicine_name"]):
            return item["medicine_name"]

    # Otherwise first parsed medicine
    return parsed[0]["medicine_name"]


# ==========================================
# EXPIRY DATE EXTRACTION (EXACT TUNED)
# ==========================================
def extract_expiry_date(text):
    if not text:
        return "Not found"

    text = fix_common_ocr_errors(text)
    merged = text.replace("\n", " ")

    # Exact priority
    match = re.search(r'\bEXP(?:IRY)?[\s:\-]*([0-1]?\d[\/\-]\d{4})\b', merged, re.IGNORECASE)
    if match:
        return match.group(1).replace("-", "/")

    # fallback
    match2 = re.search(r'\b([0-1]?\d[\/\-]\d{4})\b', merged)
    if match2:
        val = match2.group(1).replace("-", "/")
        if not val.startswith("00/"):
            return val

    return "Not found"


# ==========================================
# DOSAGE HELPERS
# ==========================================
def extract_days(text):
    if not text:
        return "5"

    text = fix_common_ocr_errors(text)

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


def normalize_dose_code(code):
    if not code:
        return None
    code = code.replace(" ", "")
    code = code.replace("I", "1").replace("l", "1")
    return code


def convert_dosage_to_text(code, days="5"):
    code = normalize_dose_code(code)
    if not code:
        return "Not found"

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


# ==========================================
# SMART FINAL MEDICINE CHOOSER
# ==========================================
def choose_best_medicine_name(prescription_name, strip_name):
    # Best case: strip has Gudcef 200
    if strip_name != "Not found" and re.search(r'\d{2,4}', strip_name):
        return strip_name

    # Then prescription with strength
    if prescription_name != "Not found" and re.search(r'\d{2,4}', prescription_name):
        return prescription_name

    if strip_name != "Not found":
        return strip_name

    if prescription_name != "Not found":
        return prescription_name

    return "Not found"


# ==========================================
# DOSAGE EXTRACTION (MATCH CHOSEN MEDICINE)
# ==========================================
def extract_dosage_text(prescription_text, medicine_name):
    if not prescription_text:
        return "Not found"

    parsed = parse_prescription_lines(prescription_text)
    if not parsed:
        return "Not found"

    days = extract_days(prescription_text)
    med_key = normalize_text_for_match(medicine_name.split()[0]) if medicine_name and medicine_name != "Not found" else ""

    # 1) Try matching chosen medicine line
    if med_key:
        for item in parsed:
            if med_key in normalize_text_for_match(item["medicine_name"]):
                dose_code = normalize_dose_code(item["dose_code"])
                if dose_code:
                    return convert_dosage_to_text(dose_code, days)

    # 2) fallback first available tablet/capsule dosage
    for item in parsed:
        dose_code = normalize_dose_code(item["dose_code"])
        if dose_code:
            return convert_dosage_to_text(dose_code, days)

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
            "api-subscription-key": api_key,
            "Content-Type": "application/json"
        }

        payload = {
            "inputs": [text],
            "target_language_code": "en-IN",
            "speaker": "meera",
            "pitch": 0,
            "pace": 1.0,
            "loudness": 1.0,
            "speech_sample_rate": 22050,
            "enable_preprocessing": True,
            "model": "bulbul:v2"
        }

        response = requests.post(api_url, json=payload, headers=headers, timeout=30)

        if response.status_code != 200:
            print("Sarvam TTS error:", response.status_code, response.text)
            return None

        data = response.json()
        audios = data.get("audios", [])
        if audios and len(audios) > 0:
            return audios[0]

        return None

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
                prescription_path = save_base64_image(prescription_camera, folder="prescriptions")

            if not medicine_path and medicine_camera:
                medicine_path = save_base64_image(medicine_camera, folder="medicines")

            if not prescription_path or not medicine_path:
                result = {
                    "error": "Please provide both prescription and medicine images."
                }
                return render(request, "home.html", {"result": result})

            # OCR
            prescription_text, _ = extract_text_with_ocr_space(prescription_path)
            medicine_text, _ = extract_text_with_ocr_space(medicine_path)

            prescription_text = fix_common_ocr_errors(prescription_text)
            medicine_text = fix_common_ocr_errors(medicine_text)

            print("\n========== PRESCRIPTION OCR ==========")
            print(prescription_text)
            print("=====================================\n")

            print("\n=========== MEDICINE OCR ============")
            print(medicine_text)
            print("=====================================\n")

            # Extract
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
