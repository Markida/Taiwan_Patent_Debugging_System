"""Fine-tune the EasyOCR recognition head on gold complete-label crops."""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
from collections import Counter, OrderedDict
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import cv2
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset

from app.config import OCR_ALLOWLIST
from features.patent_ocr.compact_ocr_reader import (
    ENGLISH_CHARACTERS,
    MODEL_HEIGHT,
    EnglishRecognitionModel,
    _decode_greedy,
    _normalized_tensor,
    _resize_initial_crop,
)
from features.patent_ocr.image_io import read_image
from training.first_stage.sweep_group_locator import sha256


DEFAULT_DATASET = SCRIPT_DIR / "ocr_recognizer_v2_dataset"
DEFAULT_BASE = PROJECT_ROOT / "easyocr_models" / "english_g2.pth"
DEFAULT_OUTPUT = PROJECT_ROOT / "easyocr_models" / "patent_english_g2_gold_ft.pth"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--base-model", type=Path, default=DEFAULT_BASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch", type=int, default=24)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--patience", type=int, default=6)
    parser.add_argument("--device")
    parser.add_argument("--seed", type=int, default=20260826)
    parser.add_argument(
        "--digits-only",
        action="store_true",
        help="Train only 0-9 rows using digit-only gold labels.",
    )
    parser.add_argument(
        "--preserve-blank",
        action="store_true",
        help="Keep the original CTC blank row frozen as well.",
    )
    return parser.parse_args()


def load_state(path: Path) -> OrderedDict:
    raw = torch.load(str(path), map_location="cpu", weights_only=False)
    return OrderedDict(
        (key[7:] if key.startswith("module.") else key, value)
        for key, value in raw.items()
    )


def augment_gray(gray: np.ndarray, rng: random.Random) -> np.ndarray:
    image = gray.astype(np.float32)
    contrast = rng.uniform(0.90, 1.10)
    brightness = rng.uniform(-10.0, 10.0)
    image = np.clip(image * contrast + brightness, 0, 255).astype(np.uint8)
    if rng.random() < 0.12:
        image = cv2.GaussianBlur(image, (3, 3), rng.uniform(0.2, 0.7))
    return image


def prepare_variant(image: np.ndarray, training: bool, rng: random.Random) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    if training:
        gray = augment_gray(gray, rng)
    if training and rng.random() < 0.35:
        processed = cv2.adaptiveThreshold(
            gray,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            11,
            2,
        )
        return cv2.resize(
            processed,
            None,
            fx=4,
            fy=4,
            interpolation=cv2.INTER_CUBIC,
        )
    resized = cv2.resize(
        gray,
        None,
        fx=4,
        fy=4,
        interpolation=cv2.INTER_CUBIC,
    )
    return cv2.copyMakeBorder(
        resized,
        30,
        30,
        30,
        30,
        cv2.BORDER_CONSTANT,
        value=255,
    )


class GoldCropDataset(Dataset):
    def __init__(self, root: Path, entries: list[dict], training: bool, seed: int):
        self.root = root
        self.entries = entries
        self.training = training
        self.seed = seed
        self.epoch = 0

    def __len__(self):
        return len(self.entries)

    def __getitem__(self, index):
        entry = self.entries[index]
        image = read_image(self.root / entry["crop"])
        if image is None:
            raise ValueError(f"Cannot read OCR crop: {self.root / entry['crop']}")
        rng = random.Random(self.seed + self.epoch * 1000003 + index)
        return prepare_variant(image, self.training, rng), entry["text"]


def collate_samples(samples):
    prepared = []
    ratios = []
    labels = []
    for image, label in samples:
        resized, ratio = _resize_initial_crop(image)
        prepared.append(resized)
        ratios.append(ratio)
        labels.append(label)
    maximum_width = min(
        768,
        max(MODEL_HEIGHT, math.ceil(max(ratios)) * MODEL_HEIGHT),
    )
    tensors = [
        _normalized_tensor(image, maximum_width).squeeze(0)
        for image in prepared
    ]
    return torch.stack(tensors), labels


def encode_targets(labels: list[str], character_to_index: dict[str, int]):
    lengths = torch.tensor([len(label) for label in labels], dtype=torch.long)
    flattened = torch.tensor(
        [character_to_index[character] for label in labels for character in label],
        dtype=torch.long,
    )
    return flattened, lengths


def mask_disallowed_logits(logits: torch.Tensor, allowed_indexes: set[int]):
    disallowed = [index for index in range(logits.shape[2]) if index not in allowed_indexes]
    if disallowed:
        logits = logits.clone()
        logits[:, :, disallowed] = -20.0
    return logits


def category_names(text: str) -> set[str]:
    categories = {"all"}
    if text.isdigit():
        categories.add("digits_only")
    if any(character.isalpha() for character in text):
        categories.add("contains_letter")
    if any(character.islower() for character in text):
        categories.add("contains_lowercase")
    if "'" in text:
        categories.add("contains_prime")
    if len(text) > 1 and set(text) <= set("IVX"):
        categories.add("roman_multi_character")
    return categories


def evaluate(model, loader, device, allowed_indexes, converter_characters):
    model.eval()
    counts = Counter()
    category_counts = {}
    confusions = Counter()
    with torch.inference_mode():
        for images, labels in loader:
            logits = model(images.to(device))
            logits = mask_disallowed_logits(logits, allowed_indexes)
            indexes = logits.argmax(dim=2).cpu().numpy()
            predictions = [
                _decode_greedy(row, converter_characters) for row in indexes
            ]
            for expected, predicted in zip(labels, predictions, strict=True):
                correct = expected == predicted
                counts["total"] += 1
                counts["correct"] += int(correct)
                if not correct:
                    confusions[(expected, predicted)] += 1
                for category in category_names(expected):
                    category_counts.setdefault(category, Counter())["total"] += 1
                    category_counts[category]["correct"] += int(correct)
    return {
        "total": counts["total"],
        "correct": counts["correct"],
        "exact_accuracy": counts["correct"] / counts["total"] if counts["total"] else 0.0,
        "categories": {
            name: {
                "total": values["total"],
                "correct": values["correct"],
                "accuracy": values["correct"] / values["total"] if values["total"] else None,
            }
            for name, values in sorted(category_counts.items())
        },
        "top_confusions": [
            {"expected": expected, "predicted": predicted, "count": count}
            for (expected, predicted), count in confusions.most_common(30)
        ],
    }


def main() -> int:
    args = parse_args()
    dataset = args.dataset.resolve()
    base_model = args.base_model.resolve()
    output = args.output.resolve()
    manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
    if not manifest.get("complete") or not manifest.get("gold_only"):
        raise RuntimeError("Gold OCR dataset is incomplete or not gold-only.")
    if manifest.get("leakage_checks", {}).get("sealed_holdout_used_for_training"):
        raise RuntimeError("Sealed OCR holdout leakage detected.")
    if not base_model.exists():
        raise FileNotFoundError(f"Base OCR model is missing: {base_model}")

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)
    device = torch.device(
        args.device or ("cuda:0" if torch.cuda.is_available() else "cpu")
    )
    converter_characters = ["[blank]"] + list(ENGLISH_CHARACTERS)
    character_to_index = {
        character: index for index, character in enumerate(converter_characters)
    }
    missing = sorted(
        {
            character
            for entry in manifest["entries"]
            for character in entry["text"]
            if character not in character_to_index
        }
    )
    if missing:
        raise RuntimeError(f"OCR labels contain unsupported characters: {missing}")
    allowed_indexes = {0} | {
        character_to_index[character] for character in OCR_ALLOWLIST
    }

    entries = manifest["entries"]
    train_entries = [entry for entry in entries if entry["split"] == "train"]
    val_entries = [entry for entry in entries if entry["split"] == "val"]
    if args.digits_only:
        train_entries = [entry for entry in train_entries if entry["text"].isdigit()]
        val_entries = [entry for entry in val_entries if entry["text"].isdigit()]
    train_dataset = GoldCropDataset(dataset, train_entries, True, args.seed)
    val_dataset = GoldCropDataset(dataset, val_entries, False, args.seed)
    train_loader = DataLoader(
        train_dataset,
        batch_size=args.batch,
        shuffle=True,
        num_workers=0,
        collate_fn=collate_samples,
        generator=torch.Generator().manual_seed(args.seed),
    )
    val_loader = DataLoader(
        val_dataset,
        batch_size=args.batch,
        shuffle=False,
        num_workers=0,
        collate_fn=collate_samples,
    )

    model = EnglishRecognitionModel(len(converter_characters))
    model.load_state_dict(load_state(base_model))
    model.to(device)
    for parameter in model.parameters():
        parameter.requires_grad = False
    model.Prediction.weight.requires_grad = True
    model.Prediction.bias.requires_grad = True

    present_indexes = {
        character_to_index[character]
        for entry in train_entries
        for character in entry["text"]
    }
    if not args.preserve_blank:
        present_indexes.add(0)
    row_mask = torch.zeros_like(model.Prediction.weight)
    row_mask[list(present_indexes)] = 1.0
    bias_mask = torch.zeros_like(model.Prediction.bias)
    bias_mask[list(present_indexes)] = 1.0
    model.Prediction.weight.register_hook(lambda gradient: gradient * row_mask)
    model.Prediction.bias.register_hook(lambda gradient: gradient * bias_mask)

    optimizer = torch.optim.AdamW(
        [model.Prediction.weight, model.Prediction.bias],
        lr=args.learning_rate,
        weight_decay=1e-4,
    )
    criterion = nn.CTCLoss(blank=0, zero_infinity=True)
    base_validation = evaluate(
        model,
        val_loader,
        device,
        allowed_indexes,
        converter_characters,
    )
    best_accuracy = base_validation["exact_accuracy"]
    best_state = OrderedDict(
        (key, value.detach().cpu().clone()) for key, value in model.state_dict().items()
    )
    best_epoch = 0
    epochs_without_improvement = 0
    history = []

    print(
        f"Base internal validation: {base_validation['correct']}/"
        f"{base_validation['total']} = {best_accuracy:.4f}",
        flush=True,
    )
    for epoch in range(1, args.epochs + 1):
        train_dataset.epoch = epoch
        model.train()
        loss_total = 0.0
        sample_total = 0
        for images, labels in train_loader:
            images = images.to(device)
            targets, target_lengths = encode_targets(labels, character_to_index)
            targets = targets.to(device)
            target_lengths = target_lengths.to(device)
            optimizer.zero_grad(set_to_none=True)
            logits = mask_disallowed_logits(model(images), allowed_indexes)
            log_probs = logits.log_softmax(2).permute(1, 0, 2)
            input_lengths = torch.full(
                (len(labels),),
                log_probs.shape[0],
                dtype=torch.long,
                device=device,
            )
            loss = criterion(log_probs, targets, input_lengths, target_lengths)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(
                [model.Prediction.weight, model.Prediction.bias], 5.0
            )
            optimizer.step()
            loss_total += float(loss.detach()) * len(labels)
            sample_total += len(labels)

        validation = evaluate(
            model,
            val_loader,
            device,
            allowed_indexes,
            converter_characters,
        )
        average_loss = loss_total / max(1, sample_total)
        history.append(
            {
                "epoch": epoch,
                "train_loss": average_loss,
                "val_exact_accuracy": validation["exact_accuracy"],
                "val_correct": validation["correct"],
            }
        )
        print(
            f"Epoch {epoch:02d}: loss={average_loss:.5f}, "
            f"val={validation['correct']}/{validation['total']} "
            f"({validation['exact_accuracy']:.4f})",
            flush=True,
        )
        if validation["exact_accuracy"] > best_accuracy + 1e-12:
            best_accuracy = validation["exact_accuracy"]
            best_epoch = epoch
            best_state = OrderedDict(
                (key, value.detach().cpu().clone())
                for key, value in model.state_dict().items()
            )
            epochs_without_improvement = 0
        else:
            epochs_without_improvement += 1
        if epochs_without_improvement >= args.patience:
            print(f"Early stopping at epoch {epoch}.", flush=True)
            break

    model.load_state_dict(best_state)
    output.parent.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), output)
    best_validation = evaluate(
        model,
        val_loader,
        device,
        allowed_indexes,
        converter_characters,
    )
    report = {
        "format_version": 1,
        "model": str(output),
        "base_model": str(base_model),
        "base_model_sha256": sha256(base_model),
        "gold_only": True,
        "pseudo_labels_used": False,
        "additional_manual_review_required": False,
        "training_scope": (
            "Prediction head digit rows only"
            if args.digits_only
            else "Prediction head rows present in gold train only"
        ),
        "ctc_blank_row_preserved": bool(args.preserve_blank),
        "frozen_backbone": True,
        "missing_character_rows_preserved": True,
        "device": str(device),
        "epochs_requested": args.epochs,
        "best_epoch": best_epoch,
        "train_crops": len(train_entries),
        "internal_validation_crops": len(val_entries),
        "sealed_holdout_crops_used_for_training": 0,
        "base_internal_validation": base_validation,
        "best_internal_validation": best_validation,
        "history": history,
    }
    report_path = output.with_name(f"{output.stem}_metrics.json")
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
