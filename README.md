# Saint-Island_Patent_MDS

Saint-Island Patent Mistake Detection System. The desktop application combines patent DOCX text checks with YOLO and offline EasyOCR drawing-label review. Document symbols can be handed directly to the image-recognition page for cross-checking.

## Current capabilities

- Detect complete patent labels and recognize `0-9`, `A-Z`, and the prime mark `'`
- Support labels such as `A`, `B`, `10A`, `IV`, `VIII`, and `7'`
- Import multiple images or convert a PDF into page images; PDF files can also be dragged directly onto the OCR page
- Rotate individual pages by 90 degrees before recognition
- Compare recognized labels with a manually entered checklist
- Review a patent DOCX on a dedicated feature page without modifying the source file; DOCX files can be dragged directly onto the page
- Show the exact error type, section, paragraph, character range, and original text location
- Extract complete and representative-drawing symbol lists independently
- Switch the OCR comparison between the two document-derived lists
- Report labels missing from the image and labels found only in the image
- Separate results by image and provide a persistent result scrollbar
- Export OCR correction packages for model improvement
- Provide a reusable `features/demo_tool/` feature-page template

## Recognition architecture

The recommended training/source model is `models/patent_label_group_v1.pt`; the portable release uses its exported `models/patent_label_group_v1.onnx`. It detects an entire reference-label group as one `patent_label` box. The compact English recognizer then reads the contents of that box using the bundled EasyOCR generation-2 recognition weight. This approach is more reliable for narrow Roman numerals and prime marks than detecting every character as a separate YOLO object.

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
  patent_label_group_v1.pt
easyocr_models/
  english_g2.pth
```

When the recommended YOLO model is present, the recognition page selects it automatically. A different compatible `.pt` model can still be selected manually.

## Project structure

```text
app/                         configuration, paths, styles, main window
features/patent_ocr/         OCR, PDF, image, parsing, and comparison logic
features/patent_review/      DOCX parsing, text rules, symbol handoff, cross-checking
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
python -m unittest discover -s tests
.\run_app.bat --check
```

The checked-in evaluation summaries for the recommended model are in `models/`. They record fixed-threshold locator metrics and end-to-end OCR results without including private source documents or model weights.

## Offline Windows release

The release workflow builds a standalone CPU package containing the executable, Python runtime components, required native libraries, the production YOLO model, and the English EasyOCR recognizer weight. The resulting folder can run without Python, Conda, administrator installation, or internet access. See `OFFLINE_RELEASE.md` for the release layout and verification procedure.

## Privacy and repository policy

Training documents, generated datasets, model weights, OCR weights, output files, build folders, and local machine paths are excluded from version control. Only source code, tests, generic training utilities, class mappings, and sanitized metric summaries are published.

## Disclaimer

This tool assists patent-document and figure review. It never creates or modifies a corrected Word document; users apply all formal revisions manually in the original Word file. Recognition and rule results should still be reviewed by a person before formal use.
