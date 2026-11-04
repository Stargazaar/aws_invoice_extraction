# AWS Invoice Extraction

Extract structured data from mock PDF and scanned invoices with
Amazon Nova on Amazon Bedrock, validate it with plain-Python rules, and store it in
Aurora PostgreSQL. Infrastructure is defined in CloudFormation.

## Synthetic invoices

```powershell
uv sync
uv run scripts/generate_invoices.py --count 30 --seed 42
```

Writes to `data/generated/` (not committed):

- `pdf/doc_NNN.pdf`: text-layer PDF in one of three layouts (different labels, date
  formats and money formats)
- `scanned/doc_NNN.jpg`: the same invoice rendered at 150 dpi and degraded with skew,
  blur, noise and JPEG compression
- `ground_truth.jsonl`: the values exactly as printed, plus `planted_errors`

About 30% of invoices carry one planted error, for testing the validation rules:

| Error | What's wrong on the invoice |
|---|---|
| `gst_wrong` | GST charged at 8% while labelled 9% |
| `subtotal_mismatch` | Subtotal doesn't equal the sum of the line items |
| `total_mismatch` | Total doesn't equal subtotal + GST |
| `duplicate` | Same vendor and invoice number as an earlier invoice |
| `near_duplicate` | Same vendor, date and amounts, different invoice number |

All vendors, addresses and GST registration numbers are fictional.
