from dataclasses import dataclass

@dataclass(frozen=True)
class PassiveNode:
    id: int
    name: str
    is_keystone: bool
    is_notable: bool
    stats: list[str]