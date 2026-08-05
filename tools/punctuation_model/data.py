from __future__ import annotations

import json
import random
import re
import unicodedata
import zlib
from collections import Counter, deque
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

import torch
from torch.utils.data import IterableDataset, get_worker_info

from .labels import (
    AMBIGUOUS_QUOTES,
    CLOSE_QUOTES,
    OPEN_QUOTES,
    PUNCTUATION_BY_GLYPH,
    PunctuationLabel,
    QuoteLabel,
)

PAD_ID = 0
UNK_ID = 1
BOS_ID = 2
HASH_PAD_ID = 0
HASH_BOS_ID = 1
TYPE_PAD = 0
TYPE_BOS = 1
BMES_PAD = 0
BMES_B = 1
BMES_M = 2
BMES_E = 3
BMES_S = 4
BMES_UNKNOWN = 5
BMES_BOS = 6
WORD_LENGTH_PAD = 0
WORD_LENGTH_UNKNOWN = 6
WORD_LENGTH_BOS = 7
IGNORE_INDEX = -100

_URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)[^\s，。；：！？、「」『』“”]+")
_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_ACRONYM_RE = re.compile(r"\b(?:[A-Za-z]\.){2,}")
_NUMBER_RE = re.compile(r"(?<!\w)\d{1,3}(?:[,，]\d{3})+(?:[.．]\d+)*|\d+(?:[.．]\d+)+")
_URL_TRAILING = frozenset(",.;:!?，。；：！？、」』”")


@dataclass(frozen=True)
class LabeledText:
    text: str
    units: tuple[str, ...]
    punctuation: tuple[int, ...]
    quotes: tuple[int, ...]
    bmes: tuple[int, ...]
    word_lengths: tuple[int, ...]
    punctuation_allowed: tuple[bool, ...]


@dataclass(frozen=True)
class CharVocabulary:
    id_to_char: tuple[str, ...]

    @cached_property
    def char_to_id(self) -> dict[str, int]:
        return {char: index for index, char in enumerate(self.id_to_char)}

    def to_dict(self) -> dict[str, Any]:
        return {"id_to_char": list(self.id_to_char)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CharVocabulary":
        chars = tuple(str(char) for char in value["id_to_char"])
        if chars[:3] != ("<PAD>", "<UNK>", "<BOS>"):
            raise ValueError("invalid vocabulary special-token order")
        return cls(chars)


def _protected_positions(text: str) -> list[bool]:
    protected = [False] * len(text)
    for pattern in (_URL_RE, _EMAIL_RE, _ACRONYM_RE, _NUMBER_RE):
        for match in pattern.finditer(text):
            start, end = match.span()
            if pattern is _URL_RE:
                while end > start and text[end - 1] in _URL_TRAILING:
                    end -= 1
            for index in range(start, end):
                protected[index] = True
    return protected


def _set_single_label(labels: list[int], value: int, kind: str) -> None:
    previous = labels[-1]
    if previous not in (0, value):
        raise ValueError(f"multiple {kind} labels occupy one boundary")
    labels[-1] = value


def _is_ascii_word_unit(value: str) -> bool:
    return len(value) == 1 and (value.isascii() and (value.isalnum() or value == "_"))


def _char_type(value: str) -> int:
    if "\u3400" <= value <= "\u9fff" or "\uf900" <= value <= "\ufaff":
        return 2
    if value.isascii() and value.islower():
        return 3
    if value.isascii() and value.isupper():
        return 4
    if value.isdigit():
        return 5
    if value.isspace():
        return 6
    category = unicodedata.category(value)
    if category.startswith("M"):
        return 7
    if category.startswith(("P", "S")):
        return 8
    return 9


def _strip_restorable_symbols(text: str) -> list[str]:
    protected = _protected_positions(text)
    units: list[str] = []
    for index, char in enumerate(text):
        if not protected[index] and (
            char in PUNCTUATION_BY_GLYPH
            or char in OPEN_QUOTES
            or char in CLOSE_QUOTES
            or char in AMBIGUOUS_QUOTES
        ):
            continue
        units.append(char)
    return units


def _token_features(record: Mapping[str, Any], units: Sequence[str]) -> tuple[list[int], list[int]]:
    review = record.get("review")
    tokens = review.get("tokens") if isinstance(review, Mapping) else record.get("tokens")
    if not isinstance(tokens, list):
        return [BMES_UNKNOWN] * len(units), [WORD_LENGTH_UNKNOWN] * len(units)

    reconstructed: list[str] = []
    bmes: list[int] = []
    lengths: list[int] = []
    for token in tokens:
        token_text = token.get("text") if isinstance(token, Mapping) else token
        if not isinstance(token_text, str):
            return [BMES_UNKNOWN] * len(units), [WORD_LENGTH_UNKNOWN] * len(units)
        token_units = _strip_restorable_symbols(unicodedata.normalize("NFC", token_text))
        reconstructed.extend(token_units)
        length = len(token_units)
        if not length:
            continue
        is_word = any(char.isalnum() or "\u3400" <= char <= "\u9fff" for char in token_units)
        if not is_word:
            bmes.extend([BMES_UNKNOWN] * length)
            lengths.extend([WORD_LENGTH_UNKNOWN] * length)
        elif length == 1:
            bmes.append(BMES_S)
            lengths.append(1)
        else:
            bmes.extend([BMES_B, *([BMES_M] * (length - 2)), BMES_E])
            lengths.extend([min(length, 5)] * length)
    if reconstructed != list(units):
        return [BMES_UNKNOWN] * len(units), [WORD_LENGTH_UNKNOWN] * len(units)
    return bmes, lengths


def extract_labeled_text(record: Mapping[str, Any]) -> LabeledText:
    raw_text = record.get("text")
    if not isinstance(raw_text, str) or not raw_text:
        raise ValueError("record.text must be a non-empty string")
    text = unicodedata.normalize("NFC", raw_text)
    protected = _protected_positions(text)
    units: list[str] = []
    unit_protected: list[bool] = []
    punctuation = [int(PunctuationLabel.NONE)]
    quotes = [int(QuoteLabel.NONE)]
    quote_depth = 0

    for index, char in enumerate(text):
        if not protected[index] and char in PUNCTUATION_BY_GLYPH:
            _set_single_label(
                punctuation, int(PUNCTUATION_BY_GLYPH[char]), "punctuation"
            )
            continue
        if not protected[index] and char in OPEN_QUOTES:
            if quote_depth != 0:
                raise ValueError("nested quotes are not supported by the depth-1 schema")
            _set_single_label(quotes, int(QuoteLabel.OPEN), "quote")
            quote_depth = 1
            continue
        if not protected[index] and char in CLOSE_QUOTES:
            if quote_depth != 1:
                raise ValueError("closing quote has no matching opening quote")
            _set_single_label(quotes, int(QuoteLabel.CLOSE), "quote")
            quote_depth = 0
            continue
        if not protected[index] and char in AMBIGUOUS_QUOTES:
            action = QuoteLabel.OPEN if quote_depth == 0 else QuoteLabel.CLOSE
            _set_single_label(quotes, int(action), "quote")
            quote_depth = 1 - quote_depth
            continue
        units.append(char)
        unit_protected.append(protected[index])
        punctuation.append(int(PunctuationLabel.NONE))
        quotes.append(int(QuoteLabel.NONE))

    if quote_depth:
        raise ValueError("opening quote is not closed")
    if not units:
        raise ValueError("record contains no model input units")

    punctuation_allowed = [False]
    for index, current in enumerate(units):
        next_unit = units[index + 1] if index + 1 < len(units) else None
        protected_boundary = unit_protected[index] or (
            index + 1 < len(units) and unit_protected[index + 1]
        )
        inside_ascii_word = next_unit is not None and _is_ascii_word_unit(current) and _is_ascii_word_unit(next_unit)
        punctuation_allowed.append(
            not protected_boundary and not inside_ascii_word and not current.isspace()
        )

    bmes, word_lengths = _token_features(record, units)
    return LabeledText(
        text=text,
        units=tuple(units),
        punctuation=tuple(punctuation),
        quotes=tuple(quotes),
        bmes=tuple(bmes),
        word_lengths=tuple(word_lengths),
        punctuation_allowed=tuple(punctuation_allowed),
    )


def iter_records(
    paths: Sequence[Path], split: str | None, require_accepted: bool
) -> Iterator[Mapping[str, Any]]:
    for path in paths:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ValueError(f"{path}:{line_number}: invalid JSON: {error}") from error
                if not isinstance(record, Mapping):
                    raise ValueError(f"{path}:{line_number}: JSON value must be an object")
                record_split = record.get("split")
                if split is not None and record_split != split:
                    continue
                if require_accepted:
                    review = record.get("review")
                    if not isinstance(review, Mapping) or review.get("status") != "accepted":
                        continue
                yield record


def build_vocabulary(
    paths: Sequence[Path],
    *,
    split: str | None,
    require_accepted: bool,
    max_size: int,
    max_records: int | None = None,
) -> tuple[CharVocabulary, dict[str, int]]:
    if max_size < 4:
        raise ValueError("max_size must leave room for special tokens and characters")
    counts: Counter[str] = Counter()
    accepted = rejected = 0
    for record in iter_records(paths, split, require_accepted):
        if max_records is not None and accepted >= max_records:
            break
        try:
            example = extract_labeled_text(record)
        except ValueError:
            rejected += 1
            continue
        counts.update(example.units)
        accepted += 1
    most_common = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[
        : max_size - 3
    ]
    vocabulary = CharVocabulary(("<PAD>", "<UNK>", "<BOS>", *(char for char, _ in most_common)))
    return vocabulary, {
        "accepted_records": accepted,
        "rejected_records": rejected,
        "unique_characters": len(counts),
        "vocabulary_size": len(vocabulary.id_to_char),
    }


def _hash_id(value: str, bucket_count: int) -> int:
    return 2 + (zlib.crc32(value.encode("utf-8")) % bucket_count)


def encode_example(
    example: LabeledText, vocabulary: CharVocabulary, hash_buckets: int
) -> dict[str, Any]:
    char_to_id = vocabulary.char_to_id
    return {
        "char_ids": [BOS_ID, *(char_to_id.get(char, UNK_ID) for char in example.units)],
        "hash_ids": [HASH_BOS_ID, *(_hash_id(char, hash_buckets) for char in example.units)],
        "type_ids": [TYPE_BOS, *(_char_type(char) for char in example.units)],
        "bmes_ids": [BMES_BOS, *example.bmes],
        "word_length_ids": [WORD_LENGTH_BOS, *example.word_lengths],
        "punctuation": list(example.punctuation),
        "quotes": list(example.quotes),
        "punctuation_allowed": list(example.punctuation_allowed),
        "units": list(example.units),
        "text": example.text,
    }


class JsonlPunctuationDataset(IterableDataset[dict[str, Any]]):
    def __init__(
        self,
        paths: Sequence[Path],
        vocabulary: CharVocabulary,
        *,
        split: str | None,
        require_accepted: bool,
        hash_buckets: int,
        max_chars: int,
        shuffle_buffer: int = 0,
        seed: int = 13,
    ) -> None:
        super().__init__()
        self.paths = tuple(paths)
        self.vocabulary = vocabulary
        self.split = split
        self.require_accepted = require_accepted
        self.hash_buckets = hash_buckets
        self.max_chars = max_chars
        self.shuffle_buffer = shuffle_buffer
        self.seed = seed
        self.epoch = 0

    def set_epoch(self, epoch: int) -> None:
        self.epoch = epoch

    def _examples(self) -> Iterator[dict[str, Any]]:
        worker = get_worker_info()
        for record_index, record in enumerate(
            iter_records(self.paths, self.split, self.require_accepted)
        ):
            if worker is not None and record_index % worker.num_workers != worker.id:
                continue
            try:
                example = extract_labeled_text(record)
            except ValueError:
                continue
            if len(example.units) > self.max_chars:
                continue
            yield encode_example(example, self.vocabulary, self.hash_buckets)

    def __iter__(self) -> Iterator[dict[str, Any]]:
        if self.shuffle_buffer <= 1:
            yield from self._examples()
            return
        worker = get_worker_info()
        worker_id = worker.id if worker else 0
        rng = random.Random(self.seed + self.epoch * 1009 + worker_id)
        buffer: list[dict[str, Any]] = []
        for example in self._examples():
            if len(buffer) < self.shuffle_buffer:
                buffer.append(example)
                continue
            index = rng.randrange(len(buffer))
            yield buffer[index]
            buffer[index] = example
        rng.shuffle(buffer)
        yield from buffer


_HAN_WHITESPACE_RE = re.compile(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])")


class TranscriptAugmentedDataset(IterableDataset[dict[str, Any]]):
    """Keep originals and add random multi-utterance transcript-like sequences."""

    def __init__(
        self,
        base: JsonlPunctuationDataset,
        *,
        probability: float,
        min_records: int = 2,
        max_records: int = 4,
        whitespace_compaction_probability: float = 0.5,
        text_noise: Any | None = None,
        seed: int = 13,
    ) -> None:
        super().__init__()
        if not 0.0 <= probability <= 1.0:
            raise ValueError("augmentation probability must be between 0 and 1")
        if min_records < 2 or max_records < min_records:
            raise ValueError("transcript record range must satisfy 2 <= min <= max")
        if not 0.0 <= whitespace_compaction_probability <= 1.0:
            raise ValueError("whitespace compaction probability must be between 0 and 1")
        self.base = base
        self.probability = probability
        self.min_records = min_records
        self.max_records = max_records
        self.whitespace_compaction_probability = whitespace_compaction_probability
        self.text_noise = text_noise
        self.seed = seed
        self.epoch = 0
        self.augmentation_counts: Counter[str] = Counter()

    def set_epoch(self, epoch: int) -> None:
        self.epoch = epoch
        self.base.set_epoch(epoch)
        self.augmentation_counts.clear()

    def __iter__(self) -> Iterator[dict[str, Any]]:
        worker = get_worker_info()
        worker_id = worker.id if worker else 0
        rng = random.Random(self.seed + self.epoch * 2029 + worker_id)
        history: deque[str] = deque(maxlen=self.max_records)
        for encoded in self.base:
            self.augmentation_counts["original"] += 1
            yield encoded
            history.append(str(encoded["text"]))
            if len(history) < self.min_records or rng.random() >= self.probability:
                continue
            record_count = rng.randint(
                self.min_records, min(self.max_records, len(history))
            )
            transcript = "".join(list(history)[-record_count:])
            if rng.random() < self.whitespace_compaction_probability:
                transcript = _HAN_WHITESPACE_RE.sub("", transcript)
                self.augmentation_counts["whitespace_compacted"] += 1
            noise_branch = "clean"
            if self.text_noise is not None:
                try:
                    transcript, noise_branch = self.text_noise.apply(transcript, rng)
                except Exception:
                    self.augmentation_counts["noise_failed"] += 1
                    continue
            try:
                augmented = extract_labeled_text({"text": transcript})
            except ValueError:
                self.augmentation_counts["label_rejected"] += 1
                continue
            if len(augmented.units) > self.base.max_chars:
                self.augmentation_counts["length_rejected"] += 1
                continue
            self.augmentation_counts[f"augmented_{noise_branch}"] += 1
            yield encode_example(
                augmented, self.base.vocabulary, self.base.hash_buckets
            )


def collate_batch(examples: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not examples:
        raise ValueError("cannot collate an empty batch")
    lengths = torch.tensor([len(example["char_ids"]) for example in examples], dtype=torch.long)
    width = int(lengths.max().item())
    batch_size = len(examples)

    def padded(key: str, fill: int, dtype: torch.dtype = torch.long) -> torch.Tensor:
        output = torch.full((batch_size, width), fill, dtype=dtype)
        for row, example in enumerate(examples):
            values = torch.tensor(example[key], dtype=dtype)
            output[row, : values.numel()] = values
        return output

    mask = torch.arange(width).unsqueeze(0) < lengths.unsqueeze(1)
    return {
        "char_ids": padded("char_ids", PAD_ID),
        "hash_ids": padded("hash_ids", HASH_PAD_ID),
        "type_ids": padded("type_ids", TYPE_PAD),
        "bmes_ids": padded("bmes_ids", BMES_PAD),
        "word_length_ids": padded("word_length_ids", WORD_LENGTH_PAD),
        "punctuation": padded("punctuation", IGNORE_INDEX),
        "quotes": padded("quotes", IGNORE_INDEX),
        "punctuation_allowed": padded("punctuation_allowed", 0, torch.bool),
        "mask": mask,
        "lengths": lengths,
        "units": [example["units"] for example in examples],
        "texts": [example["text"] for example in examples],
    }
