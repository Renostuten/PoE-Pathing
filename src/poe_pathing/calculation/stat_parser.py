import re

from dataclasses import dataclass

@dataclass(frozen=True)
class ParsedStat:
    stat_type: str
    modifier_type: str
    value: float
    raw_text: str

class StatParser:
    def parse(self, raw_stat: str) -> ParsedStat | None:
        text = raw_stat.lower()

        value_match = re.search(r"[-+]?\d+(\.\d+)?", text)
        if not value_match:
            return None

        value = float(value_match.group())

        stat_type = self._detect_stat_type(text)
        modifier_type = self._detect_modifier_type(text)

        if stat_type is None or modifier_type is None:
            return None

        return ParsedStat(
            stat_type=stat_type,
            modifier_type=modifier_type,
            value=value,
            raw_text=raw_stat,
        )

    def _detect_stat_type(self, text: str) -> str | None:
        if "maximum life" in text:
            return "maximum_life"
        if "maximum energy shield" in text:
            return "maximum_energy_shield"
        if "maximum mana" in text:
            return "maximum_mana"
        if "attack speed" in text:
            return "attack_speed"
        if "cast speed" in text:
            return "cast_speed"
        if "fire resistance" in text:
            return "fire_resistance"
        if "cold resistance" in text:
            return "cold_resistance"
        if "lightning resistance" in text:
            return "lightning_resistance"
        if "chaos resistance" in text:
            return "chaos_resistance"
        if "physical damage" in text:
            return "physical_damage"
        if "fire damage" in text:
            return "fire_damage"
        if "cold damage" in text:
            return "cold_damage"
        if "lightning damage" in text:
            return "lightning_damage"
        if "chaos damage" in text:
            return "chaos_damage"
        if "intelligence" in text:
            return "intelligence"
        if "strength" in text:
            return "strength"
        if "dexterity" in text:
            return "dexterity"

        return None

    def _detect_modifier_type(self, text: str) -> str | None:
        if "increased" in text and "%" in text:
            return "increased_percent"

        if "reduced" in text and "%" in text:
            return "reduced_percent"

        if text.startswith("+") and "%" in text:
            return "flat_percent"

        if text.startswith("+"):
            return "flat"

        return "unknown"