"""Recognizer-only English OCR runtime for the portable release.

The network layout and preprocessing follow EasyOCR's generation-2 English
recognizer (Apache-2.0).  Detection, multilingual models, downloading, beam
search, SciPy, and Torchvision are intentionally omitted because YOLO already
provides each complete text region.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from pathlib import Path

import cv2
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as functional
from PIL import Image


ENGLISH_CHARACTERS = (
    "0123456789!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~ €"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
)
MODEL_HEIGHT = 64


class BidirectionalLSTM(nn.Module):
    def __init__(self, input_size, hidden_size, output_size):
        super().__init__()
        self.rnn = nn.LSTM(
            input_size,
            hidden_size,
            bidirectional=True,
            batch_first=True,
        )
        self.linear = nn.Linear(hidden_size * 2, output_size)

    def forward(self, inputs):
        try:
            self.rnn.flatten_parameters()
        except Exception:
            pass
        recurrent, _ = self.rnn(inputs)
        return self.linear(recurrent)


class VGGFeatureExtractor(nn.Module):
    def __init__(self, input_channel=1, output_channel=256):
        super().__init__()
        channels = [
            output_channel // 8,
            output_channel // 4,
            output_channel // 2,
            output_channel,
        ]
        self.ConvNet = nn.Sequential(
            nn.Conv2d(input_channel, channels[0], 3, 1, 1),
            nn.ReLU(True),
            nn.MaxPool2d(2, 2),
            nn.Conv2d(channels[0], channels[1], 3, 1, 1),
            nn.ReLU(True),
            nn.MaxPool2d(2, 2),
            nn.Conv2d(channels[1], channels[2], 3, 1, 1),
            nn.ReLU(True),
            nn.Conv2d(channels[2], channels[2], 3, 1, 1),
            nn.ReLU(True),
            nn.MaxPool2d((2, 1), (2, 1)),
            nn.Conv2d(channels[2], channels[3], 3, 1, 1, bias=False),
            nn.BatchNorm2d(channels[3]),
            nn.ReLU(True),
            nn.Conv2d(channels[3], channels[3], 3, 1, 1, bias=False),
            nn.BatchNorm2d(channels[3]),
            nn.ReLU(True),
            nn.MaxPool2d((2, 1), (2, 1)),
            nn.Conv2d(channels[3], channels[3], 2, 1, 0),
            nn.ReLU(True),
        )

    def forward(self, inputs):
        return self.ConvNet(inputs)


class EnglishRecognitionModel(nn.Module):
    def __init__(self, number_of_classes):
        super().__init__()
        self.FeatureExtraction = VGGFeatureExtractor(1, 256)
        self.AdaptiveAvgPool = nn.AdaptiveAvgPool2d((None, 1))
        self.SequenceModeling = nn.Sequential(
            BidirectionalLSTM(256, 256, 256),
            BidirectionalLSTM(256, 256, 256),
        )
        self.Prediction = nn.Linear(256, number_of_classes)

    def forward(self, inputs, text=None):
        del text
        visual = self.FeatureExtraction(inputs)
        visual = self.AdaptiveAvgPool(visual.permute(0, 3, 1, 2))
        visual = visual.squeeze(3)
        contextual = self.SequenceModeling(visual)
        return self.Prediction(contextual.contiguous())


def _adjust_contrast(image, target=0.4):
    high = np.percentile(image, 90)
    low = np.percentile(image, 10)
    contrast = (high - low) / np.maximum(10, high + low)
    if contrast >= target:
        return image
    ratio = 200.0 / np.maximum(10, high - low)
    adjusted = (image.astype(int) - low + 25) * ratio
    return np.clip(adjusted, 0, 255).astype(np.uint8)


def _resize_initial_crop(crop):
    height, width = crop.shape[:2]
    ratio = width / float(height)
    if ratio < 1.0:
        normalized_ratio = 1.0 / ratio
        target_size = (MODEL_HEIGHT, int(MODEL_HEIGHT * normalized_ratio))
    else:
        normalized_ratio = ratio
        target_size = (int(MODEL_HEIGHT * normalized_ratio), MODEL_HEIGHT)
    # EasyOCR passes PIL's LANCZOS enum (1) to OpenCV, which is INTER_LINEAR.
    return cv2.resize(crop, target_size, interpolation=cv2.INTER_LINEAR), normalized_ratio


def _normalized_tensor(image, maximum_width, adjust_contrast=0.0):
    pil_image = Image.fromarray(image, "L")
    if adjust_contrast > 0:
        pil_image = Image.fromarray(
            _adjust_contrast(np.array(pil_image), target=adjust_contrast),
            "L",
        )

    width, height = pil_image.size
    resized_width = min(maximum_width, math.ceil(MODEL_HEIGHT * width / height))
    resized = pil_image.resize(
        (resized_width, MODEL_HEIGHT),
        Image.Resampling.BICUBIC,
    )
    array = np.asarray(resized, dtype=np.float32) / 255.0
    tensor = torch.from_numpy(array).unsqueeze(0)
    tensor.sub_(0.5).div_(0.5)

    padded = torch.zeros((1, MODEL_HEIGHT, maximum_width), dtype=torch.float32)
    padded[:, :, :resized_width] = tensor
    if resized_width < maximum_width:
        padded[:, :, resized_width:] = tensor[:, :, resized_width - 1].unsqueeze(2)
    return padded.unsqueeze(0)


def _decode_greedy(indexes, characters):
    decoded = []
    previous = None
    for index in indexes:
        index = int(index)
        if index != previous and index != 0:
            decoded.append(characters[index])
        previous = index
    return "".join(decoded)


def _confidence(probabilities, indexes):
    selected = probabilities[np.arange(len(indexes)), indexes]
    selected = selected[indexes != 0]
    if not len(selected):
        selected = np.array([0.0])
    return float(selected.prod() ** (2.0 / np.sqrt(len(selected))))


class CompactEnglishReader:
    """EasyOCR-compatible recognizer interface used by the OCR engine."""

    patent_detector_fallback_enabled = False

    def __init__(self, model_path, gpu=False):
        self.model_path = Path(model_path)
        self.character = ENGLISH_CHARACTERS
        self.converter_characters = ["[blank]"] + list(self.character)
        self.device = torch.device(
            "cuda:0" if gpu and torch.cuda.is_available() else "cpu"
        )
        model = EnglishRecognitionModel(len(self.converter_characters))
        state_dict = torch.load(
            str(self.model_path),
            map_location=self.device,
            weights_only=False,
        )
        normalized_state = OrderedDict()
        for key, value in state_dict.items():
            normalized_state[key[7:] if key.startswith("module.") else key] = value
        model.load_state_dict(normalized_state)

        self.model = model.to(self.device).eval()

    def _predict(self, tensor, allowlist, adjust_contrast=0.0):
        if adjust_contrast:
            raise ValueError("Contrast must be applied before tensor creation.")
        with torch.inference_mode():
            logits = self.model(tensor.to(self.device))
            probabilities = functional.softmax(logits, dim=2).cpu().numpy()[0]

        allowed = set(allowlist or self.character)
        ignored = [
            index
            for index, character in enumerate(self.converter_characters)
            if index and character not in allowed
        ]
        probabilities[:, ignored] = 0.0
        totals = probabilities.sum(axis=1, keepdims=True)
        probabilities = probabilities / np.maximum(totals, 1e-12)
        indexes = probabilities.argmax(axis=1)
        return (
            _decode_greedy(indexes, self.converter_characters),
            _confidence(probabilities, indexes),
        )

    def _recognize_crop(self, crop, allowlist, contrast_ths, adjust_contrast):
        resized, ratio = _resize_initial_crop(crop)
        maximum_width = math.ceil(max(1.0, ratio)) * MODEL_HEIGHT
        normal_tensor = _normalized_tensor(resized, maximum_width)
        first = self._predict(normal_tensor, allowlist)
        if first[1] >= contrast_ths:
            return first

        contrast_tensor = _normalized_tensor(
            resized,
            maximum_width,
            adjust_contrast=adjust_contrast,
        )
        second = self._predict(contrast_tensor, allowlist)
        return first if first[1] > second[1] else second

    def recognize(
        self,
        image,
        horizontal_list=None,
        free_list=None,
        decoder="greedy",
        beamWidth=5,
        batch_size=1,
        workers=0,
        allowlist=None,
        blocklist=None,
        detail=1,
        rotation_info=None,
        paragraph=False,
        contrast_ths=0.1,
        adjust_contrast=0.5,
        filter_ths=0.003,
        y_ths=0.5,
        x_ths=1.0,
        reformat=True,
        output_format="standard",
    ):
        del (
            beamWidth,
            batch_size,
            workers,
            blocklist,
            rotation_info,
            filter_ths,
            y_ths,
            x_ths,
            reformat,
            output_format,
        )
        if decoder != "greedy" or paragraph or free_list:
            raise ValueError("Compact OCR only supports full-ROI greedy recognition.")

        array = np.asarray(image)
        if array.ndim == 3:
            array = cv2.cvtColor(array, cv2.COLOR_BGR2GRAY)
        if horizontal_list is None:
            horizontal_list = [[0, array.shape[1], 0, array.shape[0]]]

        results = []
        for x_min, x_max, y_min, y_max in horizontal_list:
            x_min = max(0, int(x_min))
            y_min = max(0, int(y_min))
            x_max = min(array.shape[1], int(x_max))
            y_max = min(array.shape[0], int(y_max))
            crop = array[y_min:y_max, x_min:x_max]
            if not crop.size:
                continue
            text, confidence = self._recognize_crop(
                crop,
                allowlist,
                contrast_ths,
                adjust_contrast,
            )
            box = [
                [x_min, y_min],
                [x_max, y_min],
                [x_max, y_max],
                [x_min, y_max],
            ]
            results.append((box, text, confidence))

        if detail == 0:
            return [item[1] for item in results]
        return results
