"""Model price table: load (packaged or user override), normalize model ids, compute USD cost."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

PRICE_FIELDS = ("input", "output", "cache_read", "cache_write")
CACHE_WRITE_1H_FIELD = "cache_write_1h"
CACHE_WRITE_1H_INPUT_MULTIPLIER = 2.0
# Transcript usage key -> price field. Cache writes are split by TTL in cost_usd.
TOKEN_PRICE_FIELD = MappingProxyType({
    "input_tokens": "input",
    "cache_read_input_tokens": "cache_read",
    "output_tokens": "output",
})
CACHE_WRITE_TOTAL_KEY = "cache_creation_input_tokens"
CACHE_WRITE_1H_KEY = "cache_creation_1h_input_tokens"
TOKENS_PER_UNIT = 1_000_000
PACKAGED_SOURCE = "builtin"

_DATE_SUFFIX = re.compile(r"-\d{8}$")
_BRACKET_SUFFIX = re.compile(r"\[[^\]]*\]$")
_MODEL_ID = re.compile(r"^claude-([a-z]+)((?:-\d+)*)$")
_BARE_ALIAS = re.compile(r"^claude-([a-z]+)$")


@dataclass(frozen=True)
class PriceTable:
    as_of: str
    note: str
    source: str
    models: Mapping[str, Mapping[str, float]]

    def resolve(self, model: str) -> str | None:
        """Return the table key for a raw model id, or None when unpriced."""
        return resolve_model(model, self.models)

    def rate(self, model: str) -> Mapping[str, float] | None:
        key = self.resolve(model)
        return None if key is None else self.models[key]


def normalize_model_id(raw: str) -> str:
    """Lower-case, drop bracket suffixes like [1m] and date suffixes, ensure the claude- prefix."""
    name = _BRACKET_SUFFIX.sub("", raw.strip().lower())
    name = _DATE_SUFFIX.sub("", name)
    return name if name.startswith("claude-") else f"claude-{name}"


def _version(key: str) -> tuple[int, ...] | None:
    match = _MODEL_ID.match(key)
    if match is None:
        return None
    return tuple(int(part) for part in match.group(2).split("-") if part)


def _family(key: str) -> str | None:
    match = _MODEL_ID.match(key)
    return None if match is None else match.group(1)


def resolve_model(raw: str, models: Mapping[str, Mapping[str, float]]) -> str | None:
    """Exact match after normalization. Only a version-less alias (`opus`, `claude-sonnet`)
    falls back to the newest model of that family; a versioned id that is not in the table
    stays unpriced rather than borrowing another version's price."""
    if not raw:
        return None
    name = normalize_model_id(raw)
    if name in models:
        return name
    alias = _BARE_ALIAS.match(name)
    if alias is None:
        return None
    candidates = [key for key in models if _family(key) == alias.group(1)]
    return max(candidates, key=lambda key: _version(key) or ()) if candidates else None


def _validated_models(raw_models: Mapping[str, object], origin: str) -> dict[str, Mapping[str, float]]:
    result: dict[str, Mapping[str, float]] = {}
    for key, entry in raw_models.items():
        if not isinstance(entry, Mapping):
            raise ValueError(f"{origin}: model '{key}' must be an object")
        missing = [field for field in PRICE_FIELDS if not isinstance(entry.get(field), (int, float))]
        if missing:
            raise ValueError(f"{origin}: model '{key}' is missing numeric fields {missing}")
        prices = {field: float(entry[field]) for field in PRICE_FIELDS}
        one_hour = entry.get(CACHE_WRITE_1H_FIELD)
        if one_hour is not None and not isinstance(one_hour, (int, float)):
            raise ValueError(f"{origin}: model '{key}' field '{CACHE_WRITE_1H_FIELD}' must be numeric")
        derived = prices["input"] * CACHE_WRITE_1H_INPUT_MULTIPLIER
        prices = {**prices, CACHE_WRITE_1H_FIELD: float(one_hour) if one_hour is not None else derived}
        result[normalize_model_id(key)] = MappingProxyType(prices)
    return result


def _read_packaged() -> dict:
    return json.loads(resources.files("lapbar").joinpath("prices.json").read_text(encoding="utf-8"))


def load_prices(override_path: str | None = None) -> PriceTable:
    """Load the packaged table; a user file adds or replaces models key by key."""
    base = _read_packaged()
    models = _validated_models(base.get("models", {}), PACKAGED_SOURCE)
    as_of, note, source = base.get("as_of", ""), base.get("note", ""), PACKAGED_SOURCE
    if override_path:
        path = Path(override_path).expanduser()
        user = json.loads(path.read_text(encoding="utf-8"))
        models = {**models, **_validated_models(user.get("models", {}), str(path))}
        as_of = user.get("as_of", as_of)
        note = user.get("note", note)
        source = str(path)
    return PriceTable(as_of=as_of, note=note, source=source, models=MappingProxyType(models))


def cost_usd(tokens: Mapping[str, int], rate: Mapping[str, float] | None) -> float:
    """USD cost of a usage dict (transcript usage keys) at a per-million-token rate; None rate = 0.

    `cache_creation_1h_input_tokens` (part of the cache_creation total) is priced at the 1-hour
    rate; the rest of the cache_creation total at the 5-minute rate."""
    if rate is None:
        return 0.0
    plain = sum(int(tokens.get(key, 0) or 0) * rate[field] for key, field in TOKEN_PRICE_FIELD.items())
    write_total = int(tokens.get(CACHE_WRITE_TOTAL_KEY, 0) or 0)
    write_1h = min(int(tokens.get(CACHE_WRITE_1H_KEY, 0) or 0), write_total)
    one_hour_rate = rate.get(CACHE_WRITE_1H_FIELD, rate["input"] * CACHE_WRITE_1H_INPUT_MULTIPLIER)
    writes = (write_total - write_1h) * rate["cache_write"] + write_1h * one_hour_rate
    return (plain + writes) / TOKENS_PER_UNIT
