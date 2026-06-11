from dataclasses import dataclass

@dataclass(frozen=True)
class PassiveNode:
    id: int
    name: str
    class_start_index: int | None
    is_keystone: bool
    is_notable: bool
    stats: list[str]
