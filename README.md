# Taiwan_Patent_Debugging_System

GitHub repository: [Markida/Taiwan_Patent_Debugging_System](https://github.com/Markida/Taiwan_Patent_Debugging_System). The desktop product and company executable remain named **Saint-Island_Patent_MDS**.

Saint-Island Patent Mistake Detection System. The desktop application combines patent DOCX text checks with YOLO and offline EasyOCR drawing-label review. Document symbols can be handed directly to the image-recognition page for cross-checking.

Current version: **v2.2.07** / **v2.2.07c**. Source synchronized through **2026-09-24**, including the September 22 converter rollback. See [current source status](docs/SOURCE_STATUS_20260924.md), [release verification](docs/RELEASE_2.2.07.md), and [optional figure-heading trial package](docs/RELEASE_2.2.07_HEADING_TRIAL.md). Previously built ZIPs are historical artifacts and do not include later source changes.

## Current capabilities

- Detect complete patent labels and recognize `0-9`, `A-Z`, and the prime mark `'`
- Support labels such as `A`, `B`, `10A`, `IV`, `VIII`, and `7'`
- Import multiple images or convert a PDF into page images; PDF files can also be dragged directly onto the OCR page
- Rotate individual pages by 90 degrees before recognition
- Compare recognized labels with the complete or representative-drawing list handed off by the DOCX review page
- Review a patent DOCX on a dedicated feature page without modifying the source file; DOCX files can be dragged directly onto the page
- Show the exact error type, section, paragraph, character range, and original text location
- Add persistent exact-text review rules such as `的的` or `個個`; saved rules load automatically on later runs
- Extract complete and representative-drawing symbol lists independently
- Switch the OCR comparison between the two document-derived lists
- Report labels missing from the image and labels found only in the image
- Separate results by image and provide a persistent result scrollbar
- Preserve reviewed OCR results, figure mappings, image orientation and manual edits locally for subsequent sessions
- Continue from the next OCR label automatically after deleting a reviewed label
- Convert Taiwan patent specifications into the Beijing Taiji or Shanghai Yipin mainland-China DOCX template
- Load the ordered Taiwan-to-China terminology TXT from the company share in the background, fall back to a validated local cache or bundled dictionary, and let the user edit, sync, or upload the two-column snapshot before conversion
- Provide a reusable `features/demo_tool/` feature-page template
- Offer a lightweight, draggable pixel-cat workflow assistant with local-time greetings, page-aware guidance, and dismiss/recall controls; animation pauses while the application is inactive.

## Recognition architecture

The current default is `models/patent_label_group_v2_gold_ft.onnx`, with older compatible models retained as fallbacks. It detects an entire reference-label group as one `patent_label` box. The compact English recognizer then reads the contents of that box using the bundled EasyOCR generation-2 recognition weight. The group locator uses its separately calibrated confidence and IoU settings in `app/config.py`.

The optional `figure_heading_pilot_v1.onnx` trial is independent of component-label OCR. With its verified names and experimental manifest installed, the first-step button can orient drawing pages before recognition. Ambiguous pages remain unchanged and original images are not overwritten. This is not a formally approved model; letter/prime figure identifiers and negative-page coverage still need further validation.

The group-level YOLO model already provides each complete text region, so the production reader runs in recognizer-only mode and does not need the separate CRAFT detector weight. The compact runtime loads `english_g2.pth` directly and contains no model-download path, so an offline company computer fails with a clear missing-file message instead of attempting network access.

## Source setup

Python 3.10 or 3.11 on Windows is recommended. Install the dependencies, place the external model files described below, and run:

```powershell
python -m pip install -r requirements.txt
python main.py
```

The existing Conda helper can also be used:

```powershell
.\run_app.bat --check
.\run_app.bat
```

## Required external model files

Model weights are intentionally excluded from Git because they are large binary artifacts.

```text
models/
  patent_label_group_v2_gold_ft.onnx
easyocr_models/
  english_g2.pth
```

The recognition page automatically selects the preferred available model; it no longer exposes a model-selection field. The optional orientation model additionally requires `figure_heading_pilot_v1.names.json` and `figure_heading_pilot_v1.experimental.json`. Model artifacts and dataset-specific manifests are supplied separately, not downloaded automatically. Company package builders require the complete external artifact list declared in `tools/build_company_update.py`.

## Project structure

```text
app/                         configuration, paths, styles, main window
features/patent_ocr/         OCR, PDF, image, parsing, and comparison logic
features/patent_review/      DOCX parsing, text rules, symbol handoff, cross-checking
features/taiwan_china_spec/  Taiwan-to-China DOCX conversion and shared TXT terminology sync/upload
features/demo_tool/          reusable feature-module template
ui/                          application pages
models/                      class map, metrics, external YOLO weights
easyocr_models/              external offline EasyOCR weights
training/                    annotation, dataset, training, and evaluation tools
tests/                       automated regression tests
main.py                      application entry point
```

## Verification

```powershell
python tools/run_regression.py
.\run_app.bat --check
```

The regression runner isolates settings and shared-storage paths. Four dataset-specific integration checks and one local company-sample check are skipped in a fresh checkout without those private inputs; they run normally when the inputs are present. Historical sanitized evaluation summaries are in `models/`.

## Offline Windows release

The release workflow builds a standalone CPU package containing the executable, Python runtime components, required native libraries, the production YOLO model, and the English EasyOCR recognizer weight. The resulting folder can run without Python, Conda, administrator installation, or internet access. See `OFFLINE_RELEASE.md` for the release layout and verification procedure.

## Privacy and repository policy

Training documents, generated datasets, model weights, OCR weights, chat/profile state, output files, build folders and local QA evidence are excluded from version control. Source code, tests, synthetic fixtures, application templates/assets, generic training utilities, class mappings and selected development notes are published. Company-storage defaults in the application should be configured for your own environment.

For existing company installations, `tools/build_company_update.py` and `tools/build_clean_company_update.py` produce runtime-preserving updates. Add `--include-figure-heading-trial` only when explicitly including the verified trial artifacts. The standard builder physically excludes internal-only modules and checks package isolation. Updates retain the installed runtime; they are not standalone installers for an empty computer.

## Disclaimer

This tool assists patent-document and figure review. The review and OCR pages never modify the source document; the Taiwan-to-China converter creates a separate review draft. Recognition, rule, and converted-document results should still be reviewed by a person before formal use.
