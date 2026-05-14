from fastapi import FastAPI
from pydantic import BaseModel

from services.container import container

app = FastAPI()


class RecommendRequest(BaseModel):
    allocated: list[str]
    max_points: int = 10


def parse_desired_stats(raw: dict[str, float]) -> dict[tuple[str, str], float]:
    desired_stats = {}

    for key, value in raw.items():
        stat_type, modifier_type = key.split(":", 1)
        desired_stats[(stat_type, modifier_type)] = value

    return desired_stats


@app.post("/api/recommend-paths")
def recommend_paths(request: RecommendRequest):
    recommendations = container.tree_optimizer.recommend_paths(
        allocated=set(request.allocated),
        max_points=request.max_points,
    )

    return {
        "recommendations": recommendations,
    }