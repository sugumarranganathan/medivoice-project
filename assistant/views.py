import re
from difflib import SequenceMatcher

import cv2
import numpy as np
import pytesseract
from django.shortcuts import render
from PIL import Image

# --------------------------------------------------
# IMPORTANT: Set Tesseract path ONLY if needed
# Windows example:
# pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
# Linux / Render / Railway example:
# pytesseract.pytesseract.tesseract_cmd = "/usr/bin/tesseract"
# --------------------------------------------------


# =========================
# HOME PAGE
# =========================
def home(request):
    if request.method == "POST":
        print("\n===== POST REQUEST RECEIVED =====")

        prescription_file = request.FILES.get("prescription")
        medicine_file = request.FILES.get("medicine")

        print("Prescription file:", prescription_file)
        print("Medicine file:", medicine_file)

        if not prescription_file or not medicine_file:
            return render(request, "home.html", {
                "error": "Please capture both Prescription and Medicine images."
            })

        try:
            # Step 1: OCR for prescription
            print("Starting OCR for prescription...")
            prescription_text = extract_text_fast(prescription_file, image_type="prescription")
            print("Prescription OCR done.")

            # Step 2: OCR for medicine strip
            print("Starting OCR for medicine strip...")
            medicine_text = extract_text_fast(medicine_file, image_type="medicine")
            print("Medicine OCR done.")

            print("Prescription text:", prescription_text[:500] if prescription_text else "No text")
            print("Medicine text:", medicine_text[:500] if medicine_text else "No text")

            # Step 3: Analyze
            result = analyze_medicine_safety(prescription_text, medicine_text)
            print("Analysis complete:", result)

            return render(request, "result.html", {
                "prescription_text": prescription_text or "No text detected from prescription.",
                "medicine_text": medicine_text or "No text detected from medicine strip.",
                "result": result
            })

        except Exception as e:
            print("ERROR IN POST:", str(e))
            return render(request, "result.html", {
                "prescription_text": "",
                "medicine_text": "",
                "result": f"❌ Processing failed: {str(e)}"
            })

    return render(request, "home.html")


# =========================
# FAST IMAGE PREPROCESSING
# =========================
def preprocess_image_fast(uploaded_file, image_type="general"):
    """
    Fast preprocessing to avoid long waiting.
    Returns processed OpenCV image ready for OCR.
    """
    # Read image bytes
    file_bytes = np.asarray(bytearray(uploaded_file.read()), dtype=np.uint8)
    img = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)

    if img is None:
        raise ValueError("Invalid image uploaded.")

    # Reset file pointer after reading
    uploaded_file.seek(0)

    # Resize large images for faster OCR
    h, w = img.shape[:2]
    max_dim = 1400  # slightly lower = faster
    if max(h, w) > max_dim:
        scale = max_dim / max(h, w)
        new_w = int(w * scale)
        new_h = int(h * scale)
        img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)

    # Convert to grayscale
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Light denoise
    gray = cv2.GaussianBlur(gray, (3, 3), 0)

    # Threshold based on image type
    if image_type == "medicine":
        # Medicine strip often has printed text
        processed = cv2.threshold(
            gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )[1]
    else:
        # Prescription may have handwritten / mixed text
        processed = cv2.adaptiveThreshold(
            gray,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            31,
            11
        )

    return processed


# =========================
# FAST OCR WITH TIMEOUT
# =========================
def extract_text_fast(uploaded_file, image_type="general"):
    """
    Fast OCR with timeout so it doesn't hang forever.
    """
    processed = preprocess_image_fast(uploaded_file, image_type=image_type)

    # Convert OpenCV image to PIL
    pil_img = Image.fromarray(processed)

    try:
        # psm 6 = block of text
        text = pytesseract.image_to_string(
            pil_img,
            lang="eng",
            config="--oem 3 --psm 6",
            timeout=12
        )
    except RuntimeError:
        text = "OCR timeout - text extraction took too long."
    except Exception as e:
        print("OCR ERROR:", str(e))
        text = ""

    return clean_text(text)


# =========================
# CLEAN TEXT
# =========================
def clean_text(text):
    if not text:
        return ""

    # Keep only useful characters
    text = re.sub(r"[^\w\s\-/.,()]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# =========================
# EXTRACT MEDICINE NAMES
# =========================
def extract_medicine_names(text):
    """
    Simple heuristic extraction of possible medicine names.
    """
    if not text:
        return []

    words = text.split()

    stop_words = {
        "tablet", "tablets", "capsule", "capsules", "syrup", "mg", "ml",
        "take", "after", "before", "food", "daily", "morning", "night",
        "once", "twice", "thrice", "for", "days", "day", "tab", "cap",
        "prescription", "medicine", "strip", "one", "two", "three"
    }

    candidates = []
    for w in words:
        word = w.strip(".,()[]{}-_/").lower()

        # Ignore short words and stop words
        if len(word) < 4 or word in stop_words:
            continue

        # Must contain at least one letter
        if not any(c.isalpha() for c in word):
            continue

        candidates.append(word)

    # Remove duplicates while preserving order
    unique = []
    for c in candidates:
        if c not in unique:
            unique.append(c)

    return unique[:20]


# =========================
# FUZZY MATCH
# =========================
def similar(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


# =========================
# ANALYSIS LOGIC
# =========================
def analyze_medicine_safety(prescription_text, medicine_text):
    """
    Compare extracted medicine names from prescription and medicine strip.
    """
    if "OCR timeout" in prescription_text:
        return "⚠️ Prescription OCR took too long. Please retake a clearer prescription image."

    if "OCR timeout" in medicine_text:
        return "⚠️ Medicine strip OCR took too long. Please retake a clearer medicine strip image."

    prescription_meds = extract_medicine_names(prescription_text)
    medicine_strip_words = extract_medicine_names(medicine_text)

    print("Prescription medicine candidates:", prescription_meds)
    print("Medicine strip candidates:", medicine_strip_words)

    if not prescription_meds:
        return "⚠️ Could not detect medicine name clearly in the prescription. Please retake the prescription image in better light."

    if not medicine_strip_words:
        return "⚠️ Could not detect medicine name clearly in the medicine strip. Please retake the medicine strip image closer and clearly."

    best_match = None
    best_score = 0

    for p in prescription_meds:
        for m in medicine_strip_words:
            score = similar(p, m)
            if score > best_score:
                best_score = score
                best_match = (p, m)

    print("Best match:", best_match, "Score:", best_score)

    if not best_match:
        return "⚠️ Could not compare medicine names properly. Please retake clearer images."

    if best_score >= 0.75:
        return (
            f"✅ Safe to use. The medicine strip appears to match the prescription. "
            f"(Matched: '{best_match[0]}' ↔ '{best_match[1]}', Similarity: {best_score:.2f})"
        )
    elif best_score >= 0.55:
        return (
            f"⚠️ Partial match found. Please verify manually before use. "
            f"(Closest match: '{best_match[0]}' ↔ '{best_match[1]}', Similarity: {best_score:.2f})"
        )
    else:
        return (
            f"❌ Warning: The medicine strip may NOT match the prescription. "
            f"(Closest match: '{best_match[0]}' ↔ '{best_match[1]}', Similarity: {best_score:.2f})"
        )
