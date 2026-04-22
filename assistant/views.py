import base64
import io
import re
from difflib import SequenceMatcher
from datetime import datetime

from django.shortcuts import render
from PIL import Image, ImageFilter, ImageOps
import pytesseract


# =========================================================
# OPTIONAL TESSERACT PATH (UNCOMMENT ONLY IF NEEDED)
# =========================================================
# Example for Windows:
# pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


# =========================================================
# KNOWN MEDICINES (PROJECT-SPECIFIC)
# Add more medicines here later if needed
# =========================================================
KNOWN_MEDICINES = [
    "gudcef",
    "monticope",
    "chericof",
]


# =========================================================
# HELPERS
# =========================================================
def similarity(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def normalize_text(text):
    if not text:
        return ""
    return re.sub(r"[^a-z0-9/\-\s]", " ", text.lower())


def decode_base64_image(data_url):
    """
    Convert base64 data URL from camera capture into PIL image
    """
    try:
        if not data_url or "," not in data_url:
            return None

        header, encoded = data_url.split(",", 1)
        image_data = base64.b64decode(encoded)
        return Image.open(io.BytesIO(image_data)).convert("RGB")
    except Exception:
        return None


def load_uploaded_image(uploaded_file):
    """
    Convert uploaded Django file into PIL image
    """
    try:
        return Image.open(uploaded_file).convert("RGB")
    except Exception:
        return None


def threshold_image(img, threshold=160):
    """
    Convert grayscale image into black/white threshold image
    """
    return img.point(lambda p: 255 if p > threshold else 0)


# =========================================================
# FAST OCR (STABLE + FASTER)
# =========================================================
def extract_text_from_pil_image(img):
    """
    Faster OCR version for Chromebook:
    - 2 image variants only
    - 2 Tesseract configs only
    - much faster than old version
    """
    try:
        if img is None:
            return ""

        # Resize (2x is enough and faster than 3x/4x)
        img = img.resize((img.width * 2, img.height * 2))

        # Auto contrast + sharpen
        gray = ImageOps.grayscale(img)
        gray = ImageOps.autocontrast(gray)
        gray = gray.filter(ImageFilter.SHARPEN)

        # Only 2 variants for speed
        variants = [
            gray,
            threshold_image(gray, 160),
        ]

        # Only 2 configs for speed
        configs = [
            r'--oem 3 --psm 6',
            r'--oem 3 --psm 11',
        ]

        best_text = ""
        best_score = 0

        for variant in variants:
            for config in configs:
                try:
                    text = pytesseract.image_to_string(variant, config=config).strip()
                    score = len(text)
                    if score > best_score:
                        best_text = text
                        best_score = score
                except Exception:
                    continue

        return best_text.strip()

    except Exception:
        return ""


# =========================================================
# MEDICINE DETECTION
# =========================================================
def detect_medicine_name(text):
    """
    Detect medicine name from OCR text using:
    - direct contains
    - fuzzy matching
    """
    if not text:
        return "Not detected"

    t = normalize_text(text)

    # Direct match first
    for med in KNOWN_MEDICINES:
        if med in t:
            return med.capitalize()

    # Fuzzy word-level match
    words = re.findall(r"[a-z0-9]+", t)

    best_match = None
    best_score = 0

    for word in words:
        for med in KNOWN_MEDICINES:
            score = similarity(word, med)
            if score > best_score:
                best_score = score
                best_match = med

    # Lower threshold for OCR mistakes
    if best_match and best_score >= 0.30:
        return best_match.capitalize()

    return "Not detected"


# =========================================================
# EXPIRY DATE EXTRACTION
# =========================================================
def extract_expiry_date(text):
    """
    Extract expiry date from medicine strip OCR text
    Supports:
    - 09/2028
    - 09-2028
    - EXP 09/2028
    - 09 2028
    """
    if not text:
        return "Not found"

    patterns = [
        r"(?:exp|expiry|exp date|expires)?\s*[:\-]?\s*(\b\d{2}[\/\-]\d{4}\b)",
        r"(?:exp|expiry|exp date|expires)?\s*[:\-]?\s*(\b\d{2}\s\d{4}\b)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text.lower())
        if match:
            value = match.group(1).strip().replace(" ", "/")
            return value

    return "Not found"


# =========================================================
# CHECK EXPIRY STATUS
# =========================================================
def is_expired(expiry_text):
    """
    Check if MM/YYYY is expired
    """
    try:
        if expiry_text == "Not found":
            return False

        cleaned = expiry_text.replace("-", "/").strip()
        month, year = cleaned.split("/")
        month = int(month)
        year = int(year)

        today = datetime.today()
        current_month = today.month
        current_year = today.year

        if year < current_year:
            return True
        if year == current_year and month < current_month:
            return True
        return False
    except Exception:
        return False


# =========================================================
# PRESCRIPTION MATCH
# =========================================================
def medicine_in_text(medicine_name, text):
    """
    Check whether medicine name exists in prescription OCR text
    """
    if not medicine_name or medicine_name == "Not detected" or not text:
        return False

    med = medicine_name.lower()
    t = normalize_text(text)

    if med in t:
        return True

    words = re.findall(r"[a-z0-9]+", t)
    for word in words:
        if similarity(word, med) >= 0.30:
            return True

    return False


# =========================================================
# DOSAGE EXTRACTION
# =========================================================
def extract_dosage_info(medicine_name, prescription_text):
    """
    Extract dosage line related to medicine name
    """
    if not prescription_text or medicine_name == "Not detected":
        return "Not found"

    lines = [line.strip() for line in prescription_text.splitlines() if line.strip()]
    med = medicine_name.lower()

    # First: find matching line
    for i, line in enumerate(lines):
        line_norm = normalize_text(line)

        if med in line_norm:
            # return current line + next line if useful
            combined = line
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                if len(next_line) < 80:
                    combined += " | " + next_line
            return combined

        # fuzzy match line words
        words = re.findall(r"[a-z0-9]+", line_norm)
        for word in words:
            if similarity(word, med) >= 0.30:
                combined = line
                if i + 1 < len(lines):
                    next_line = lines[i + 1].strip()
                    if len(next_line) < 80:
                        combined += " | " + next_line
                return combined

    # fallback: search dosage keywords
    dosage_keywords = ["once", "twice", "daily", "morning", "night", "after food", "before food"]
    for line in lines:
        low = line.lower()
        if any(keyword in low for keyword in dosage_keywords):
            return line

    return "Not found"


# =========================================================
# DURATION EXTRACTION
# =========================================================
def extract_duration(prescription_text):
    """
    Extract duration like:
    - 5 days
    - 7 days
    - for 3 days
    """
    if not prescription_text:
        return "Not found"

    patterns = [
        r"\bfor\s+(\d+\s+days?)\b",
        r"\b(\d+\s+days?)\b",
        r"\b(\d+\s+weeks?)\b",
    ]

    text = prescription_text.lower()

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()

    return "Not found"


# =========================================================
# REVERSED UPLOAD AUTO-DETECT
# =========================================================
def should_swap(medicine_name_from_img1, prescription_text_img1, medicine_name_from_img2, prescription_text_img2):
    """
    Detect if user uploaded medicine and prescription in reverse
    Logic:
    - If image1 looks like prescription and image2 looks like medicine, swap
    """
    img1_has_med = medicine_name_from_img1 != "Not detected"
    img2_has_med = medicine_name_from_img2 != "Not detected"

    img1_prescription_like = len(prescription_text_img1.split()) > 8
    img2_prescription_like = len(prescription_text_img2.split()) > 8

    if (not img1_has_med and img1_prescription_like) and (img2_has_med or not img2_prescription_like):
        return True

    return False


# =========================================================
# BUY LINKS
# =========================================================
def build_apollo_link(medicine_name):
    if not medicine_name or medicine_name == "Not detected":
        return "https://www.apollopharmacy.in/"
    query = medicine_name.replace(" ", "%20")
    return f"https://www.apollopharmacy.in/search-medicines/{query}"


def build_tata_1mg_link(medicine_name):
    if not medicine_name or medicine_name == "Not detected":
        return "https://www.1mg.com/"
    query = medicine_name.replace(" ", "%20")
    return f"https://www.1mg.com/search/all?name={query}"


# =========================================================
# RESULT MESSAGE
# =========================================================
def build_result(medicine_text, prescription_text, was_swapped=False):
    medicine_name = detect_medicine_name(medicine_text)
    expiry_date = extract_expiry_date(medicine_text)
    expired = is_expired(expiry_date)

    match_found = medicine_in_text(medicine_name, prescription_text)
    dosage_info = extract_dosage_info(medicine_name, prescription_text)
    duration = extract_duration(prescription_text)

    if medicine_name == "Not detected":
        match_status = "Medicine name could not be detected clearly from medicine image."
    elif match_found:
        match_status = "Medicine appears to match the prescription."
    else:
        match_status = "Medicine does NOT clearly match the prescription."

    if expired:
        status_banner = "⚠️ WARNING: Medicine appears expired. Do NOT use."
    elif medicine_name == "Not detected":
        status_banner = "⚠️ WARNING: Medicine name not detected clearly. Please verify manually."
    elif match_found:
        status_banner = "✅ SAFE: Medicine appears valid and matches the prescription."
    else:
        status_banner = "⚠️ WARNING: Medicine may not match the prescription."

    voice_message = (
        f"Medicine name is {medicine_name}. "
        f"Expiry date is {expiry_date}. "
        f"{match_status} "
        f"Dosage information is {dosage_info}. "
        f"Duration is {duration}. "
        f"{status_banner}"
    )

    return {
        "medicine_name": medicine_name,
        "expiry_date": expiry_date,
        "dosage_info": dosage_info,
        "match_status": match_status,
        "duration": duration,
        "status_banner": status_banner,
        "voice_message": voice_message,
        "apollo_link": build_apollo_link(medicine_name),
        "tata_1mg_link": build_tata_1mg_link(medicine_name),
        "medicine_text": medicine_text,
        "prescription_text": prescription_text,
        "was_swapped": was_swapped,
    }


# =========================================================
# MAIN VIEW
# =========================================================
def home(request):
    result = None

    if request.method == "POST":
        medicine_image_file = request.FILES.get("medicine_image")
        prescription_image_file = request.FILES.get("prescription_image")

        medicine_camera_data = request.POST.get("medicine_camera_data", "").strip()
        prescription_camera_data = request.POST.get("prescription_camera_data", "").strip()

        # Load images from camera OR file upload
        medicine_img = None
        prescription_img = None

        if medicine_camera_data:
            medicine_img = decode_base64_image(medicine_camera_data)
        elif medicine_image_file:
            medicine_img = load_uploaded_image(medicine_image_file)

        if prescription_camera_data:
            prescription_img = decode_base64_image(prescription_camera_data)
        elif prescription_image_file:
            prescription_img = load_uploaded_image(prescription_image_file)

        if medicine_img and prescription_img:
            # OCR both images
            text1 = extract_text_from_pil_image(medicine_img)
            text2 = extract_text_from_pil_image(prescription_img)

            # Detect swap possibility
            med1 = detect_medicine_name(text1)
            med2 = detect_medicine_name(text2)

            swap = should_swap(med1, text1, med2, text2)

            if swap:
                medicine_text = text2
                prescription_text = text1
                result = build_result(medicine_text, prescription_text, was_swapped=True)
            else:
                medicine_text = text1
                prescription_text = text2
                result = build_result(medicine_text, prescription_text, was_swapped=False)

        else:
            result = {
                "medicine_name": "Not detected",
                "expiry_date": "Not found",
                "dosage_info": "Not found",
                "match_status": "Both medicine and prescription images are required.",
                "duration": "Not found",
                "status_banner": "⚠️ WARNING: Please upload or capture both images.",
                "voice_message": "Please provide both medicine and prescription images.",
                "apollo_link": "https://www.apollopharmacy.in/",
                "tata_1mg_link": "https://www.1mg.com/",
                "medicine_text": "",
                "prescription_text": "",
                "was_swapped": False,
            }

    return render(request, "home.html", {"result": result})
