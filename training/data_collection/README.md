# TIPO patent drawing corpus builder

This folder contains the collection tool for the raw, pre-annotation patent
drawing corpus. It uses the Taiwan Intellectual Property Office (TIPO) official
open-data service rather than synthetic fonts or screenshots scraped from GPSS.

The builder:

- reads an official `InventionPubXML ... index_all.xml` batch index;
- downloads each specification's official XML and multi-page TIFF ZIP;
- uses the XML `<figure-drawings>` count to select drawing pages at the end of
  the TIFF;
- preserves the official source orientation;
- removes exact pixel duplicates;
- caps pages per patent to improve source diversity;
- writes exactly 5,000 training images and 1,000 validation images;
- groups the split by publication number so a patent cannot leak into both
  training and validation;
- does **not** create annotations and does **not** start model training.

The output includes `manifest.jsonl`, `manifest.csv`, `stats.json`, SHA-256
integrity hashes, rejection logs, resumable caches, and train/validation contact
sheets for visual review.

Official source: <https://cloud.tipo.gov.tw/S220/opdata/detail/PatentPub>

Open-data license: <https://data.gov.tw/license>
