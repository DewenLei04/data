# Series B Transaction Review

You are an investment analyst at Ardent Peak Ventures III. Review the transaction materials in the read-only `input/` directory as of the date stated in the corpus. Apply the fund's governing policies and conventions found in those materials.

Write all work products to the writable `output/` directory. Produce exactly these files:

- `output/term_sheet_extract.json`
- `output/capitalization.xlsx`
- `output/economics.json`
- `output/deviation_register.csv`
- `output/negotiation_memo.pdf`
- `output/redline_positions.csv`
- `output/ic_recommendation.json`
- `output/reconciliation.csv`
- `output/instrument_register.csv`

Structure each file according to the corresponding document in `input/00_task/output_schemas/`. Those documents define output structure only. Business definitions, evidence requirements, calculations, applicability, and decision standards come from the transaction documents and fund policies in the corpus.

## Available tools

- LibreOffice is available for spreadsheet and document work.
- OCRmyPDF and Tesseract are available for scanned materials.
- A PDF service is available on `127.0.0.1:8090`.
- The Ledgerline service is available on `127.0.0.1:8091`. Its API key is stored at `/etc/ledgerline_api_key`; read the API description in the corpus before using it.

The environment has no external network access. Loopback services remain available. Treat partial responses and application errors as recoverable: inspect documentation, retry appropriately, preserve completed work, and continue with unaffected deliverables rather than abandoning the entire task.

Do not modify `input/`. Do not place drafts or logs outside `output/`. Support every material conclusion with a file and section citation.
