from __future__ import annotations

import random
from collections.abc import Mapping

from .labels import (
    AMBIGUOUS_QUOTES,
    CLOSE_QUOTES,
    OPEN_QUOTES,
    PUNCTUATION_BY_GLYPH,
)


class TridentTextNoiseMixer:
    """Select at most one Trident text transform while preserving label markup."""

    def __init__(
        self,
        *,
        weights: Mapping[str, float],
        bopomofo_convert_ratio: float = 0.15,
        homophonic_convert_ratio: float = 0.10,
        homomorphic_convert_ratio: float = 0.04,
    ) -> None:
        from trident.data.text_transforms import (
            BopomofoConvert,
            ChineseConvert,
            RandomHomomorphicTypo,
            RandomHomophonicTypo,
        )

        supported = {
            "clean",
            "homophonic",
            "chinese",
            "bopomofo",
            "homomorphic",
        }
        unknown = set(weights) - supported
        if unknown:
            raise ValueError(f"unsupported Trident noise branches: {sorted(unknown)}")
        if any(value < 0.0 for value in weights.values()):
            raise ValueError("Trident noise weights must be non-negative")
        total = sum(float(weights.get(name, 0.0)) for name in supported)
        if total <= 0.0:
            raise ValueError("at least one Trident noise weight must be positive")

        self.names = tuple(sorted(supported))
        self.weights = tuple(float(weights.get(name, 0.0)) / total for name in self.names)
        self.transforms = {
            "bopomofo": BopomofoConvert(convert_ratio=bopomofo_convert_ratio),
            "homophonic": RandomHomophonicTypo(
                convert_ratio=homophonic_convert_ratio
            ),
            "homomorphic": RandomHomomorphicTypo(
                convert_ratio=homomorphic_convert_ratio
            ),
        }
        self.chinese_transforms = (
            ChineseConvert(convert_to="simplified", convert_ratio=1.0),
            ChineseConvert(convert_to="traditional", convert_ratio=1.0),
        )
        self.protected = frozenset(PUNCTUATION_BY_GLYPH) | frozenset(
            OPEN_QUOTES | CLOSE_QUOTES | AMBIGUOUS_QUOTES
        )

    @staticmethod
    def _seeded_call(transform: object, text: str, seed: int) -> str:
        state = random.getstate()
        random.seed(seed)
        try:
            output = transform(text)  # type: ignore[operator]
        finally:
            random.setstate(state)
        if not isinstance(output, str):
            raise TypeError("Trident text transform did not return a string")
        return output

    def _transform_preserving_labels(
        self, transform: object, text: str, rng: random.Random
    ) -> str:
        output: list[str] = []
        segment: list[str] = []

        def flush() -> None:
            if segment:
                output.append(
                    self._seeded_call(
                        transform, "".join(segment), rng.randrange(2**31)
                    )
                )
                segment.clear()

        for char in text:
            if char in self.protected:
                flush()
                output.append(char)
            else:
                segment.append(char)
        flush()
        return "".join(output)

    def apply(self, text: str, rng: random.Random) -> tuple[str, str]:
        branch = rng.choices(self.names, weights=self.weights, k=1)[0]
        if branch == "clean":
            return text, branch
        if branch == "chinese":
            candidates = [
                self._transform_preserving_labels(transform, text, rng)
                for transform in self.chinese_transforms
            ]
            changed = [candidate for candidate in candidates if candidate != text]
            return (rng.choice(changed) if changed else text), branch
        return self._transform_preserving_labels(
            self.transforms[branch], text, rng
        ), branch
