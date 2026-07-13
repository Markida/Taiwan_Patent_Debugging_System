\# Patent Number OCR



A desktop OCR tool for detecting and comparing patent drawing reference labels using YOLO, EasyOCR, PySide6, PDF-to-image conversion, image rotation, and automated label checklist verification.



\## Overview



Patent Number OCR is a desktop application designed to assist with patent figure review. It can detect reference labels in patent drawings, process PDF files into images, rotate incorrect image orientations, and compare detected labels against a user-provided reference checklist.



The project is currently built around a modular Python architecture, making it easier to extend with additional patent-related tools in the future.



\## Features



\* Patent drawing reference-label detection

\* Batch image recognition

\* PDF import and page-to-image conversion

\* Image rotation by 90 degrees

\* Reference label checklist comparison

\* TXT result export

\* CPU/GPU auto-detection

\* Legacy YOLO + EasyOCR recognition mode

\* Future YOLO character-class model support through `class\_map.json`

\* Modular project structure for future feature expansion



\## Current Recognition Modes



\### Legacy Mode



Uses YOLO to detect label regions and EasyOCR to recognize numeric characters.



\### YOLO Character Model Mode



Designed for future models that directly classify characters such as:



\* Numbers: `0-9`

\* Letters: `A-Z`

\* Prime mark: `'`



This allows labels such as `7'`, `8'`, `A`, and `10A` to be supported with newer models.



\## Project Structure



```text

PatentNumberOCR/

├── main.py

├── app/

│   ├── config.py

│   ├── paths.py

│   ├── styles.py

│   └── main\_window.py

├── ui/

│   ├── home\_page.py

│   └── recognition\_page.py

├── features/

│   ├── registry.py

│   └── patent\_ocr/

│       ├── class\_map.py

│       ├── easyocr\_loader.py

│       ├── image\_tools.py

│       ├── label\_parser.py

│       ├── label\_matcher.py

│       ├── pdf\_tools.py

│       ├── ocr\_engine.py

│       └── ocr\_worker.py

├── models/

├── easyocr\_models/

├── sample/

├── README.md

├── requirements.txt

└── .gitignore

```



\## How to Run



```bash

python main.py

```



\## Required External Files



The following files are not included in the repository because they may be large:



```text

models/\*.pt

easyocr\_models/\*.pth

```



Please place the YOLO model file inside:



```text

models/

```



Please place EasyOCR model files inside:



```text

easyocr\_models/

```



Expected EasyOCR files include:



```text

craft\_mlt\_25k.pth

english\_g2.pth

```



\## Roadmap



\* Add support for alphabetic reference labels

\* Add support for prime labels such as `7'`, `8'`, and `9'`

\* Improve recognition accuracy toward 97%

\* Reduce dependency on EasyOCR by using YOLO character-class recognition

\* Add more patent-related utility tools under the same desktop UI framework



\## Disclaimer



This tool is designed to assist patent figure review and reference-label checking. It should be used as a supporting tool, not as a replacement for professional review.



