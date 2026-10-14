from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.repository.agent_repository import AgentRepository
from app.agents import list_agents as registry_list_agents


router = APIRouter(
    prefix="/agents",
    tags=["agents"],
)


@router.get("")
def list_agents(db: Session = Depends(get_db)):
    repository = AgentRepository(db)

    agents = repository.list_enabled()

    return [
        {
            "agent_code": agent.agent_code,
            "name": agent.name,
            "suite": agent.suite,
            "autonomy_level": agent.autonomy_level,
            "version": agent.version,
            "enabled": agent.enabled,
        }
        for agent in agents
    ]


@router.get("/health")
def agent_health(db: Session = Depends(get_db)):
    """Show registered agents, DB metadata, task stats, and health."""
    repository = AgentRepository(db)
    registry = registry_list_agents()
    db_agents = {a.agent_code: a for a in repository.list_all()}

    agents_status = []
    healthy_count = 0
    degraded_count = 0
    down_count = 0

    for code, impl in sorted(registry.items()):
        db_agent = db_agents.get(code)
        stats = repository.get_task_stats(code)

        # Determine health
        if db_agent and db_agent.enabled and code in registry:
            health = "HEALTHY"
            healthy_count += 1
        elif db_agent and not db_agent.enabled:
            health = "DISABLED"
            degraded_count += 1
        elif code not in registry:
            health = "NO_IMPL"
            down_count += 1
        else:
            health = "UNKNOWN"
            degraded_count += 1

        agents_status.append({
            "agent_code": code,
            "name": db_agent.name if db_agent else impl.agent_name,
            "suite": db_agent.suite if db_agent else "unknown",
            "autonomy_level": db_agent.autonomy_level if db_agent else "unknown",
            "version": db_agent.version if db_agent else "unknown",
            "enabled": db_agent.enabled if db_agent else False,
            "description": db_agent.description if db_agent else impl.describe(),
            "has_implementation": code in registry,
            "health": health,
            "stats": stats,
        })

    # Check for DB agents not in registry
    for code, db_agent in db_agents.items():
        if code not in registry:
            stats = repository.get_task_stats(code)
            agents_status.append({
                "agent_code": code,
                "name": db_agent.name,
                "suite": db_agent.suite,
                "autonomy_level": db_agent.autonomy_level,
                "version": db_agent.version,
                "enabled": db_agent.enabled,
                "description": db_agent.description,
                "has_implementation": False,
                "health": "NO_IMPL",
                "stats": stats,
            })
            down_count += 1

    overall = "GREEN"
    if down_count > 0:
        overall = "RED"
    elif degraded_count > 0:
        overall = "YELLOW"

    return {
        "overall_health": overall,
        "summary": {
            "total_agents": len(agents_status),
            "healthy": healthy_count,
            "degraded": degraded_count,
            "down": down_count,
        },
        "agents": agents_status,
    }


@router.get("/{agent_code}/gate-report")
def agent_gate_report(agent_code: str):
    """Machine-generated promotion-gate report (WP-7).

    404 for unknown agents, 422 when the agent has no golden set yet —
    both cleanly refused; 200 with approved true/false otherwise.
    """
    from app.services.promotion_gate import (
        AGENT_CODE_TO_DIR,
        gate_report,
    )

    agent_dir = AGENT_CODE_TO_DIR.get(agent_code.upper())
    if agent_dir is None:
        raise HTTPException(
            status_code=404,
            detail=f"No golden-set registry entry for '{agent_code}'",
        )

    report = gate_report(agent_dir, agent_code.upper())
    if report["criteria"]["golden"]["total"] == 0:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Agent '{agent_code}' has no golden cases — "
                "author tests/golden/<agent>/cases.json first"
            ),
        )
    return report


@router.get("/{agent_code}")
def get_agent(
    agent_code: str,
    db: Session = Depends(get_db),
):
    repository = AgentRepository(db)
    registry = registry_list_agents()

    db_agent = repository.get_by_code(agent_code)
    impl = registry.get(agent_code)

    if db_agent is None and impl is None:
        raise HTTPException(
            status_code=404,
            detail=f"Agent '{agent_code}' not found",
        )

    stats = repository.get_task_stats(agent_code)

    return {
        "agent_code": agent_code,
        "name": db_agent.name if db_agent else impl.agent_name,
        "suite": db_agent.suite if db_agent else "unknown",
        "autonomy_level": db_agent.autonomy_level if db_agent else "unknown",
        "version": db_agent.version if db_agent else "unknown",
        "enabled": db_agent.enabled if db_agent else False,
        "description": db_agent.description if db_agent else impl.describe() if impl else None,
        "has_implementation": impl is not None,
        "health": "HEALTHY" if (db_agent and db_agent.enabled and impl) else "DISABLED" if db_agent and not db_agent.enabled else "NO_IMPL",
        "stats": stats,
    }