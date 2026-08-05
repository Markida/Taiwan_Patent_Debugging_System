# Offline Windows release

The production deliverable is a portable Windows 10/11 x64 CPU folder. The
whole folder must be transferred together; `Saint-Island_Patent_MDS.exe` is only the
launcher and is not a self-contained one-file application.

```text
Saint-Island_Patent_MDS/
  Saint-Island_Patent_MDS.exe
  Offline_Check.bat
  Saint-IslandPatentOCR_啟動.bat
  custom_text_rules.json        optional user-created rules, preserved by updates
  app/
    main.py
    app/
    features/
    ui/
    models/patent_label_group_v1.onnx
    easyocr_models/english_g2.pth
  runtime/
    python.exe
    pythonw.exe
    ...complete validated CPU environment...
```

`packaging/Saint-IslandPatentOCR.Launcher.cs` builds the small Windows launcher. It
starts the bundled `runtime/pythonw.exe` with `app/main.py`, sets the portable
runtime paths, disables user-site packages, and forwards command-line options.

The release intentionally keeps the complete validated CPU environment instead
of pruning imports. This makes the archive larger but avoids missing native
DLLs or packages on an offline company computer.

Before distribution:

1. Run `Saint-Island_Patent_MDS.exe --offline-self-test <report.json>` and require a
   `PASS` report.
2. Run `Saint-Island_Patent_MDS.exe --gui-smoke-test` and require exit code 0.
3. Run the unit tests with the bundled Python environment.
4. Create the transfer ZIP, extract it to a separate short path, and repeat the
   offline self-test from the extracted copy.
5. Publish a SHA-256 checksum beside the ZIP.

The application uses `numpy.fromfile` plus `cv2.imdecode` for image input so PDF
names and generated page paths containing Chinese or other Unicode characters
work on Windows OpenCV builds that cannot handle those paths through
`cv2.imread`.
