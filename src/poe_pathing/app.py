from fastapi import FastAPI
from pydantic import BaseModel

from services.container import container

app = FastAPI()


class RecommendRequest(BaseModel):
    allocated: list[str]
    max_points: int = 10
    desired_stats: dict[tuple[str, str], float] = {}


@app.post("/api/recommend-paths")
def recommend_paths(request: RecommendRequest):
    recommendations = container.tree_optimizer.recommend_paths(
        allocated=set(request.allocated),
        desired_stats=request.desired_stats,
        max_points=request.max_points,
    )

    return {
        "recommendations": recommendations,
    }