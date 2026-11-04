"""Generate synthetic invoices with ground-truth labels and planted errors.

Every invoice is written three times: as a text-layer PDF, as a degraded JPG that
imitates a phone or office scan, and as that same scan wrapped in a PDF with no
text layer (how scanned invoices usually arrive). Ground truth records the values
exactly as printed, so planted errors (wrong GST, sums that don't add up,
duplicates) are part of the labels and the validation rules can be scored against
them.

Outputs (under --out, default data/generated/):
  pdf/<doc_id>.pdf
  scanned/<doc_id>.jpg
  scanned_pdf/<doc_id>.pdf
  ground_truth.jsonl

Usage:
  uv run scripts/generate_invoices.py --count 30 --seed 42
"""

import argparse
import copy
import json
import random
import shutil
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageEnhance, ImageFilter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

GST_RATE = Decimal("0.09")
CURRENCY = "SGD"
CENTS = Decimal("0.01")

CUSTOMER = {
    "name": "Acme Analytics Pte Ltd",
    "address": ["80 Robinson Road #10-01", "Singapore 068898"],
}

# Fictional vendors. GST numbers are made up and use a format no real registration uses.
VENDORS = [
    {
        "name": "Lion City Office Supplies Pte Ltd",
        "gst_reg_no": "M9-0000101-A",
        "address": ["12 Ubi Crescent #03-02", "Singapore 408564"],
        "inv_fmt": "LCO-{year}-{seq:04d}",
        "items": [("A4 copier paper (box of 5 reams)", 28, 45), ("Ballpoint pens (pack of 50)", 12, 20),
                  ("Toner cartridge, black", 85, 160), ("Lever arch files (pack of 10)", 18, 30),
                  ("Desk organiser", 15, 40)],
    },
    {
        "name": "Harbourfront Catering Pte Ltd",
        "gst_reg_no": "M9-0000202-B",
        "address": ["3 Telok Blangah Way #01-15", "Singapore 098561"],
        "inv_fmt": "HC{seq:05d}",
        "items": [("Buffet lunch (per pax)", 18, 32), ("Tea break set (per pax)", 6, 12),
                  ("Delivery and setup", 40, 80), ("Bottled water (carton of 24)", 9, 15)],
    },
    {
        "name": "Kallang Tech Services Pte Ltd",
        "gst_reg_no": "M9-0000303-C",
        "address": ["50 Kallang Avenue #07-01", "Singapore 339505"],
        "inv_fmt": "INV/{year}/{seq:03d}",
        "items": [("Laptop repair (labour, per hour)", 60, 120), ("SSD 1TB replacement", 110, 190),
                  ("On-site network troubleshooting", 150, 300), ("Monthly IT support retainer", 400, 900)],
    },
    {
        "name": "Merlion Print & Design",
        "gst_reg_no": "M9-0000404-D",
        "address": ["21 Bukit Batok Street 22 #02-08", "Singapore 659589"],
        "inv_fmt": "MPD-{seq:04d}",
        "items": [("Business cards (box of 200)", 25, 45), ("A1 poster printing", 18, 35),
                  ("Roll-up banner with stand", 90, 160), ("Graphic design (per hour)", 50, 95)],
    },
    {
        "name": "Orchid Facilities Management Pte Ltd",
        "gst_reg_no": "M9-0000505-E",
        "address": ["8 Admiralty Street #05-30", "Singapore 757438"],
        "inv_fmt": "OFM{year}{seq:04d}",
        "items": [("Office cleaning (monthly)", 600, 1200), ("Aircon servicing (per unit)", 35, 60),
                  ("Pest control treatment", 120, 250), ("Carpet shampooing (per sqm)", 3, 6)],
    },
    {
        "name": "Tanjong Logistics Pte Ltd",
        "gst_reg_no": "M9-0000606-F",
        "address": ["2 Jurong Port Road #04-11", "Singapore 619087"],
        "inv_fmt": "TL-{year}-{seq:05d}",
        "items": [("Same-day courier (per trip)", 25, 55), ("Pallet storage (per pallet, monthly)", 30, 60),
                  ("Van rental with driver (half day)", 140, 220), ("Packing materials", 15, 60)],
    },
]

# Visual variety: labels, date formats and money formats differ by layout,
# so the extractor can't rely on one fixed template.
LAYOUTS = {
    "classic": {
        "font": "Helvetica", "bold": "Helvetica-Bold", "title": "TAX INVOICE",
        "labels": ("Invoice No:", "Invoice Date:", "Due Date:"), "date_fmt": "%d/%m/%Y",
        "money": "{:,.2f}", "totals": ("Subtotal", "GST 9%", "Total"), "band": None, "grid": True,
    },
    "modern": {
        "font": "Helvetica", "bold": "Helvetica-Bold", "title": "Invoice",
        "labels": ("Invoice #", "Issued", "Due"), "date_fmt": "%d %b %Y",
        "money": "S${:,.2f}", "totals": ("Subtotal", "GST (9%)", "Amount due"),
        "band": colors.HexColor("#1f4e79"), "grid": False,
    },
    "compact": {
        "font": "Courier", "bold": "Courier-Bold", "title": "TAX INVOICE / RECEIPT",
        "labels": ("Bill No.", "Date", "Payment due by"), "date_fmt": "%Y-%m-%d",
        "money": "SGD {:,.2f}", "totals": ("Sub-total", "GST @ 9%", "TOTAL DUE"), "band": None, "grid": False,
    },
}

ARITHMETIC_ERRORS = ["gst_wrong", "subtotal_mismatch", "total_mismatch"]
DUPLICATE_ERRORS = ["duplicate", "near_duplicate"]


def money(x) -> Decimal:
    return Decimal(str(x)).quantize(CENTS, rounding=ROUND_HALF_UP)


def next_invoice_number(vendor: dict, seqs: dict, year: int) -> str:
    seqs[vendor["name"]] += 1
    return vendor["inv_fmt"].format(year=year, seq=seqs[vendor["name"]])


def make_invoice(rng: random.Random, doc_id: str, vendor: dict, seqs: dict) -> dict:
    lines = []
    for desc, lo, hi in rng.sample(vendor["items"], k=rng.randint(1, len(vendor["items"]))):
        qty = rng.randint(1, 12)
        unit = money(rng.uniform(lo, hi))
        lines.append({"description": desc, "quantity": qty, "unit_price": unit, "amount": money(qty * unit)})

    invoice_date = date(2026, 1, 1) + timedelta(days=rng.randint(0, 300))
    subtotal = sum((li["amount"] for li in lines), Decimal("0"))
    gst = money(subtotal * GST_RATE)
    return {
        "doc_id": doc_id,
        "layout": rng.choice(list(LAYOUTS)),
        "vendor_name": vendor["name"],
        "vendor_gst_reg_no": vendor["gst_reg_no"],
        "invoice_number": next_invoice_number(vendor, seqs, invoice_date.year),
        "invoice_date": invoice_date,
        "due_date": invoice_date + timedelta(days=rng.choice([14, 30, 45])),
        "currency": CURRENCY,
        "line_items": lines,
        "subtotal": subtotal,
        "gst_amount": gst,
        "total": subtotal + gst,
        "planted_errors": [],
    }


def plant_arithmetic_error(rng: random.Random, inv: dict, kind: str) -> None:
    if kind == "gst_wrong":
        # Old 8% rate, while the label still says 9%.
        inv["gst_amount"] = money(inv["subtotal"] * Decimal("0.08"))
        inv["total"] = inv["subtotal"] + inv["gst_amount"]
    elif kind == "subtotal_mismatch":
        # Subtotal doesn't equal the sum of the lines; GST and total follow the wrong subtotal.
        inv["subtotal"] += money(rng.uniform(5, 50))
        inv["gst_amount"] = money(inv["subtotal"] * GST_RATE)
        inv["total"] = inv["subtotal"] + inv["gst_amount"]
    elif kind == "total_mismatch":
        inv["total"] += money(rng.uniform(1, 100)) * rng.choice([-1, 1])
    inv["planted_errors"].append(kind)


def make_duplicate(rng: random.Random, original: dict, doc_id: str, kind: str, seqs: dict) -> dict:
    dup = copy.deepcopy(original)
    dup["doc_id"] = doc_id
    dup["layout"] = rng.choice(list(LAYOUTS))
    if kind == "near_duplicate":
        # Same vendor, date and amounts, but a new invoice number: the unique constraint won't catch it.
        vendor = next(v for v in VENDORS if v["name"] == original["vendor_name"])
        dup["invoice_number"] = next_invoice_number(vendor, seqs, original["invoice_date"].year)
    dup["planted_errors"] = [kind]
    dup["duplicate_of"] = original["doc_id"]
    return dup


def draw_invoice(path: Path, inv: dict) -> None:
    style = LAYOUTS[inv["layout"]]
    font, bold, fmt = style["font"], style["bold"], style["money"]
    width, height = A4
    left, right = 20 * mm, width - 20 * mm
    c = canvas.Canvas(str(path), pagesize=A4)

    # Header: vendor block on the left, title on the right.
    y = height - 25 * mm
    if style["band"] is not None:
        c.setFillColor(style["band"])
        c.rect(0, height - 42 * mm, width, 42 * mm, stroke=0, fill=1)
        c.setFillColor(colors.white)
    c.setFont(bold, 14)
    c.drawString(left, y, inv["vendor_name"])
    c.setFont(font, 9)
    vendor = next(v for v in VENDORS if v["name"] == inv["vendor_name"])
    for i, line in enumerate(vendor["address"] + [f"GST Reg No: {inv['vendor_gst_reg_no']}"]):
        c.drawString(left, y - (5 + 4.5 * i) * mm, line)
    c.setFont(bold, 16)
    c.drawRightString(right, y + 10 * mm, style["title"])  # own line, so long vendor names can't overlap
    c.setFillColor(colors.black)

    # Bill-to on the left, invoice details on the right.
    y = height - 55 * mm
    c.setFont(bold, 10)
    c.drawString(left, y, "Bill To:")
    c.setFont(font, 10)
    for i, line in enumerate([CUSTOMER["name"]] + CUSTOMER["address"]):
        c.drawString(left, y - (5 + 5 * i) * mm, line)
    no_label, date_label, due_label = style["labels"]
    details = [
        (no_label, inv["invoice_number"]),
        (date_label, inv["invoice_date"].strftime(style["date_fmt"])),
        (due_label, inv["due_date"].strftime(style["date_fmt"])),
        ("Currency", inv["currency"]),
    ]
    for i, (label, value) in enumerate(details):
        c.setFont(bold, 10)
        c.drawString(right - 80 * mm, y - 5 * i * mm, label)
        c.setFont(font, 10)
        c.drawRightString(right, y - 5 * i * mm, value)

    # Line items table.
    cols = [left, right - 78 * mm, right - 40 * mm, right]  # description, qty, unit price, amount
    y = height - 90 * mm
    c.setFont(bold, 10)
    c.drawString(cols[0], y, "Description")
    c.drawRightString(cols[1], y, "Qty")
    c.drawRightString(cols[2], y, "Unit Price")
    c.drawRightString(cols[3], y, "Amount")
    c.line(left, y - 2 * mm, right, y - 2 * mm)
    c.setFont(font, 10)
    for li in inv["line_items"]:
        y -= 7 * mm
        c.drawString(cols[0], y, li["description"])
        c.drawRightString(cols[1], y, str(li["quantity"]))
        c.drawRightString(cols[2], y, fmt.format(li["unit_price"]))
        c.drawRightString(cols[3], y, fmt.format(li["amount"]))
        if style["grid"]:
            c.setStrokeColor(colors.lightgrey)
            c.line(left, y - 2.5 * mm, right, y - 2.5 * mm)
            c.setStrokeColor(colors.black)
    c.line(left, y - 4 * mm, right, y - 4 * mm)

    # Totals.
    y -= 12 * mm
    for i, (label, value) in enumerate(zip(style["totals"], (inv["subtotal"], inv["gst_amount"], inv["total"]))):
        c.setFont(bold if i == 2 else font, 11 if i == 2 else 10)
        c.drawString(right - 80 * mm, y - 6 * i * mm, label)
        c.drawRightString(right, y - 6 * i * mm, fmt.format(value))

    c.setFont(font, 8)
    c.drawString(left, 20 * mm, "Payment by bank transfer or PayNow. Thank you for your business.")
    c.showPage()
    c.save()


def make_scan(pdf_path: Path, jpg_path: Path, scanned_pdf_path: Path, rng: random.Random) -> None:
    """Render the PDF and degrade it: slight skew, blur, noise, low contrast, JPEG artefacts.

    The JPG is then wrapped in an image-only PDF, so both scan files hold the same pixels.
    """
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        img = pdf[0].render(scale=150 / 72).to_pil().convert("L")
    finally:
        pdf.close()
    img = img.rotate(rng.uniform(-2.5, 2.5), resample=Image.Resampling.BICUBIC, expand=True, fillcolor=255)
    img = img.filter(ImageFilter.GaussianBlur(rng.uniform(0.4, 1.1)))
    img = Image.blend(img, Image.effect_noise(img.size, rng.uniform(20, 40)), rng.uniform(0.06, 0.12))
    img = ImageEnhance.Contrast(img).enhance(rng.uniform(0.75, 1.0))
    img.save(jpg_path, "JPEG", quality=rng.randint(45, 70))
    with Image.open(jpg_path) as jpg:
        jpg.save(scanned_pdf_path, "PDF", resolution=150)


def to_json(inv: dict) -> str:
    def default(o):
        if isinstance(o, (Decimal, date)):
            return str(o)
        raise TypeError(type(o))

    return json.dumps(inv, default=default)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", type=int, default=30, help="total invoices, including duplicates")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=Path("data/generated"))
    parser.add_argument("--error-rate", type=float, default=0.3, help="share of invoices with a planted error")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    for sub in ("pdf", "scanned", "scanned_pdf"):
        shutil.rmtree(args.out / sub, ignore_errors=True)
        (args.out / sub).mkdir(parents=True)

    # Plan which invoices get which error. Duplicates go last so they always have an original to copy.
    n_errors = round(args.count * args.error_rate)
    kinds = [(ARITHMETIC_ERRORS + DUPLICATE_ERRORS)[i % 5] for i in range(n_errors)]
    n_dupes = sum(k in DUPLICATE_ERRORS for k in kinds)
    base_kinds = [k for k in kinds if k in ARITHMETIC_ERRORS] + ["clean"] * (args.count - n_errors)
    rng.shuffle(base_kinds)

    seqs = {v["name"]: rng.randint(100, 900) for v in VENDORS}
    invoices = []
    for i, kind in enumerate(base_kinds, start=1):
        inv = make_invoice(rng, f"doc_{i:03d}", rng.choice(VENDORS), seqs)
        if kind != "clean":
            plant_arithmetic_error(rng, inv, kind)
        invoices.append(inv)

    clean = [inv for inv in invoices if not inv["planted_errors"]]
    dupe_kinds = [k for k in kinds if k in DUPLICATE_ERRORS]
    for j, kind in enumerate(dupe_kinds, start=len(invoices) + 1):
        invoices.append(make_duplicate(rng, rng.choice(clean), f"doc_{j:03d}", kind, seqs))

    with open(args.out / "ground_truth.jsonl", "w", encoding="utf-8") as f:
        for inv in invoices:
            pdf_path = args.out / "pdf" / f"{inv['doc_id']}.pdf"
            draw_invoice(pdf_path, inv)
            make_scan(
                pdf_path,
                args.out / "scanned" / f"{inv['doc_id']}.jpg",
                args.out / "scanned_pdf" / f"{inv['doc_id']}.pdf",
                rng,
            )
            f.write(to_json(inv) + "\n")

    summary = {}
    for inv in invoices:
        key = inv["planted_errors"][0] if inv["planted_errors"] else "clean"
        summary[key] = summary.get(key, 0) + 1
    print(f"Wrote {len(invoices)} invoices ({n_dupes} duplicates) to {args.out}: {summary}")


if __name__ == "__main__":
    main()
