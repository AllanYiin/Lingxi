from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

import torch
from torch import nn
from torch.nn import functional as F
from torch.nn.utils.rnn import pack_padded_sequence, pad_packed_sequence

from .data import IGNORE_INDEX
from .labels import PUNCTUATION_NAMES, QUOTE_NAMES


@dataclass(frozen=True)
class ModelConfig:
    vocab_size: int
    hash_buckets: int = 1024
    char_embedding_dim: int = 24
    hash_embedding_dim: int = 8
    type_embedding_dim: int = 8
    bmes_embedding_dim: int = 4
    word_length_embedding_dim: int = 4
    model_width: int = 48
    conv_dilations: tuple[int, ...] = (1, 2, 4, 8)
    conv_kernel_size: int = 3
    gru_hidden: int = 64
    dropout: float = 0.1
    punctuation_classes: int = len(PUNCTUATION_NAMES)
    quote_classes: int = len(QUOTE_NAMES)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["conv_dilations"] = list(self.conv_dilations)
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ModelConfig":
        payload = dict(value)
        payload["conv_dilations"] = tuple(int(item) for item in payload["conv_dilations"])
        return cls(**payload)


class RmsNorm(nn.Module):
    def __init__(self, width: int, epsilon: float = 1e-6) -> None:
        super().__init__()
        self.weight = nn.Parameter(torch.ones(width))
        self.epsilon = epsilon

    def forward(self, value: torch.Tensor) -> torch.Tensor:
        scale = value.pow(2).mean(dim=-1, keepdim=True).add(self.epsilon).rsqrt()
        return value * scale * self.weight


class DepthwiseDilatedBlock(nn.Module):
    def __init__(self, width: int, kernel_size: int, dilation: int, dropout: float) -> None:
        super().__init__()
        if kernel_size % 2 != 1:
            raise ValueError("conv_kernel_size must be odd for same padding")
        self.norm = RmsNorm(width)
        self.depthwise = nn.Conv1d(
            width,
            width,
            kernel_size,
            padding=dilation * (kernel_size - 1) // 2,
            dilation=dilation,
            groups=width,
        )
        self.pointwise = nn.Conv1d(width, width, 1)
        self.dropout = nn.Dropout(dropout)

    def forward(self, value: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        residual = value
        hidden = self.norm(value).transpose(1, 2)
        hidden = self.depthwise(hidden)
        hidden = self.pointwise(hidden).transpose(1, 2)
        hidden = self.dropout(F.relu(hidden))
        return (residual + hidden) * mask.unsqueeze(-1)


class TinyPunctuationModel(nn.Module):
    def __init__(self, config: ModelConfig) -> None:
        super().__init__()
        self.config = config
        self.char_embedding = nn.Embedding(
            config.vocab_size, config.char_embedding_dim, padding_idx=0
        )
        self.hash_embedding = nn.Embedding(
            config.hash_buckets + 2, config.hash_embedding_dim, padding_idx=0
        )
        self.type_embedding = nn.Embedding(10, config.type_embedding_dim, padding_idx=0)
        self.bmes_embedding = nn.Embedding(7, config.bmes_embedding_dim, padding_idx=0)
        self.word_length_embedding = nn.Embedding(
            8, config.word_length_embedding_dim, padding_idx=0
        )
        feature_width = (
            config.char_embedding_dim
            + config.hash_embedding_dim
            + config.type_embedding_dim
            + config.bmes_embedding_dim
            + config.word_length_embedding_dim
        )
        self.input_projection = nn.Linear(feature_width, config.model_width)
        self.blocks = nn.ModuleList(
            DepthwiseDilatedBlock(
                config.model_width,
                config.conv_kernel_size,
                dilation,
                config.dropout,
            )
            for dilation in config.conv_dilations
        )
        self.pre_gru_norm = RmsNorm(config.model_width)
        self.gru = nn.GRU(
            input_size=config.model_width,
            hidden_size=config.gru_hidden,
            num_layers=1,
            batch_first=True,
            bidirectional=True,
        )
        output_width = 2 * config.gru_hidden
        self.punctuation_head = nn.Linear(output_width, config.punctuation_classes)
        self.quote_head = nn.Linear(output_width, config.quote_classes)
        self.pair_compatibility = nn.Parameter(
            torch.zeros(config.punctuation_classes, config.quote_classes)
        )

    def forward(self, batch: Mapping[str, torch.Tensor]) -> dict[str, torch.Tensor]:
        mask = batch["mask"]
        features = torch.cat(
            (
                self.char_embedding(batch["char_ids"]),
                self.hash_embedding(batch["hash_ids"]),
                self.type_embedding(batch["type_ids"]),
                self.bmes_embedding(batch["bmes_ids"]),
                self.word_length_embedding(batch["word_length_ids"]),
            ),
            dim=-1,
        )
        hidden = self.input_projection(features) * mask.unsqueeze(-1)
        for block in self.blocks:
            hidden = block(hidden, mask)
        hidden = self.pre_gru_norm(hidden)
        packed = pack_padded_sequence(
            hidden,
            batch["lengths"].detach().cpu(),
            batch_first=True,
            enforce_sorted=False,
        )
        packed_output, _ = self.gru(packed)
        hidden, _ = pad_packed_sequence(
            packed_output, batch_first=True, total_length=hidden.size(1)
        )
        return {
            "punctuation_logits": self.punctuation_head(hidden),
            "quote_logits": self.quote_head(hidden),
        }

    def loss(
        self,
        outputs: Mapping[str, torch.Tensor],
        punctuation_targets: torch.Tensor,
        quote_targets: torch.Tensor,
        *,
        punctuation_weights: torch.Tensor | None = None,
        quote_weights: torch.Tensor | None = None,
        quote_loss_weight: float = 1.0,
        pair_loss_weight: float = 0.1,
        event_loss_weight: float = 1.0,
        positive_pair_loss_weight: float = 1.0,
        event_pos_weight: float = 4.0,
    ) -> dict[str, torch.Tensor]:
        punctuation_logits = outputs["punctuation_logits"]
        quote_logits = outputs["quote_logits"]
        punctuation_loss = F.cross_entropy(
            punctuation_logits.transpose(1, 2),
            punctuation_targets,
            weight=punctuation_weights,
            ignore_index=IGNORE_INDEX,
        )
        quote_loss = F.cross_entropy(
            quote_logits.transpose(1, 2),
            quote_targets,
            weight=quote_weights,
            ignore_index=IGNORE_INDEX,
        )
        joint_logits = (
            punctuation_logits.unsqueeze(-1)
            + quote_logits.unsqueeze(-2)
            + self.pair_compatibility.view(
                1, 1, self.config.punctuation_classes, self.config.quote_classes
            )
        )
        valid = (punctuation_targets != IGNORE_INDEX) & (quote_targets != IGNORE_INDEX)
        joint_targets = torch.full_like(punctuation_targets, IGNORE_INDEX)
        joint_targets[valid] = (
            punctuation_targets[valid] * self.config.quote_classes + quote_targets[valid]
        )
        flat_joint_logits = joint_logits.flatten(2)
        pair_loss = F.cross_entropy(
            flat_joint_logits.transpose(1, 2),
            joint_targets,
            ignore_index=IGNORE_INDEX,
        )
        gold_event = valid & (
            (punctuation_targets != 0) | (quote_targets != 0)
        )
        if gold_event.any():
            positive_pair_loss = F.cross_entropy(
                flat_joint_logits[gold_event], joint_targets[gold_event]
            )
        else:
            positive_pair_loss = flat_joint_logits.sum() * 0.0

        none_score = flat_joint_logits[..., 0]
        event_score = torch.logsumexp(flat_joint_logits[..., 1:], dim=-1)
        event_logits = event_score - none_score
        positive_weight = torch.as_tensor(
            event_pos_weight,
            dtype=event_logits.dtype,
            device=event_logits.device,
        )
        event_loss = F.binary_cross_entropy_with_logits(
            event_logits[valid],
            gold_event[valid].to(event_logits.dtype),
            pos_weight=positive_weight,
        )
        total = (
            punctuation_loss
            + quote_loss_weight * quote_loss
            + pair_loss_weight * pair_loss
            + event_loss_weight * event_loss
            + positive_pair_loss_weight * positive_pair_loss
        )
        return {
            "loss": total,
            "punctuation_loss": punctuation_loss.detach(),
            "quote_loss": quote_loss.detach(),
            "pair_loss": pair_loss.detach(),
            "event_loss": event_loss.detach(),
            "positive_pair_loss": positive_pair_loss.detach(),
        }

    def parameter_count(self) -> int:
        return sum(parameter.numel() for parameter in self.parameters())
