# Cropped character classifier dataset

This workflow uses the existing, manually boxed real patent pages.  Each YOLO
box becomes one crop.  EasyOCR may suggest a label, but a human-approved
manifest remains the source of truth for formal classifier training.

Runtime recognition remains single-angle.  `--rotation-search` only normalizes
legacy training pages that had not been rotated in the UI before annotation.

## 1. Extract and suggest digit labels

```powershell
conda run -n patent_pack_cpu python prepare_crops.py --auto-label --rotation-search
```

## 2. Review suggestions

```powershell
conda run -n patent_pack_cpu python review_crops.py
```

To establish a trustworthy benchmark first, review the 611 validation crops:

```powershell
review_char_crops.bat --split val
```

After refreshing alphanumeric suggestions, review only likely letters with:

```powershell
review_char_crops.bat --split train --kind letters
```

Before retraining the locator, review low-confidence boxes that are likely to
be parentheses or drawing lines:

```powershell
review_char_crops.bat --split train --max-confidence 0.995
```

Shortcuts: `Space` accepts the suggestion, `Enter` accepts the typed label,
`Ctrl+S` skips, and `Ctrl+Q`/`Ctrl+E` rotates the training crop.  Plain letter
keys remain available for the later A-Z labeling pass.

After the digit labels are reviewed, add real A-Z/prime boxes to the manifest
before training the 37-class model.  Do not treat unreviewed OCR suggestions as
ground truth.
