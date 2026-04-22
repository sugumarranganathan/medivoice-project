if request.method == "POST":
    print("POST received")

    prescription = request.FILES.get("prescription")
    medicine = request.FILES.get("medicine")

    print("Prescription file:", prescription)
    print("Medicine file:", medicine)

    if not prescription or not medicine:
        return render(request, "home.html", {"error": "Both images are required."})

    try:
        # your OCR logic here
        print("Starting OCR...")
        
        # prescription_text = ...
        # medicine_text = ...
        # analysis = ...

        print("OCR completed")

        return render(request, "result.html", {
            "prescription_text": prescription_text,
            "medicine_text": medicine_text,
            "result": analysis
        })

    except Exception as e:
        print("ERROR:", str(e))
        return render(request, "home.html", {
            "error": f"Processing failed: {str(e)}"
        })
