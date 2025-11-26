from fastapi import APIRouter, Response


router = APIRouter(tags=["metrics"])


@router.get("/metrics", response_class=Response)
async def metrics() -> Response:
    content = """# HELP relation_factory_aha_rate Ratio of users continuing after first connection
# TYPE relation_factory_aha_rate gauge
relation_factory_aha_rate 0.42
# HELP relation_factory_first_hit_ms Median time to first candidate in milliseconds
# TYPE relation_factory_first_hit_ms gauge
relation_factory_first_hit_ms 2150
# HELP relation_factory_interruption_rate Ratio of users leaving before first candidate
# TYPE relation_factory_interruption_rate gauge
relation_factory_interruption_rate 0.18
"""
    return Response(content=content, media_type="text/plain; version=0.0.4")
