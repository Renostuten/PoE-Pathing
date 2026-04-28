import math

def get_node_position(tree: dict, node_id: str) -> tuple[float, float]:
    node = tree["nodes"][node_id]

    if "group" not in node:
        raise ValueError(f"Node {node_id} has no group and cannot be positioned")

    group_id = node["group"]
    group = tree["groups"][str(group_id)]

    group_x = group["x"]
    group_y = group["y"]

    orbit = node["orbit"]
    orbit_index = node["orbitIndex"]

    radius = tree["constants"]["orbitRadii"][orbit]
    skills_in_orbit = tree["constants"]["skillsPerOrbit"][orbit]

    angle = 2 * math.pi * orbit_index / skills_in_orbit

    x = group_x + radius * math.cos(angle)
    y = group_y + radius * math.sin(angle)

    return x, y