import json

from fastapi import FastAPI
from pydantic import BaseModel, Field

from .services.container import container

app = FastAPI()


class RecommendRequest(BaseModel):
    allocated: list[str]
    max_points: int = 10
    desired_stats: dict[str, float] = Field(default_factory=dict)

    def parsed_desired_stats(self) -> dict[tuple[str, str], float]:
        parsed = {}

        for raw_key, weight in self.desired_stats.items():
            try:
                stat_type, modifier_type = json.loads(raw_key)
            except (TypeError, ValueError):
                continue

            parsed[(stat_type, modifier_type)] = weight

        return parsed


@app.post("/api/recommend-paths")
def recommend_paths(request: RecommendRequest):
    recommendations = container.tree_optimizer.recommend_paths(
        allocated=set(request.allocated),
        desired_stats=request.parsed_desired_stats(),
        max_points=request.max_points,
    )

    return {
        "recommendations": recommendations,
    }
