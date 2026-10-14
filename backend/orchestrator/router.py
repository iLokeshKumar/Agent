ROUTES = {
    # Purchase suite
    "purchase_request": "P1",
    "sourcing_rfq": "P2",
    "invoice_matching": "P4",
    "payment_proposal": "P4",
    # Stores suite
    "grn_request": "S1",
    "grn": "S1",           # alias: tasks already in DB use this name
    "reorder": "S3",
    "reorder_review": "S3",
    "stock_health": "S2",
    # Finance suite
    "collections": "F1",
    "collections_cohort_scan": "F1",
    "books_to_tally": "F3",
    # Campaign suite
    "campaign_brief": "K1",
    "campaign_audience": "K2",
    "campaign_broadcast": "K3",
    "campaign_dealer_promo": "K4",
    "campaign_response": "K5",
    "campaign_analytics": "K6",
    "campaign_scheduler_tick": "K3",  # fires due scheduled campaigns
    # GSM/ISM command
    "gsm_morning_brief": "M2",
    "gsm_command": "M2",
    # Dashboards / misc
    "business_query": "D1",
    "md_digest": "D1",
    "test_failure": "D1",
    # Service
    "service_request": "V1",
}


class RoutingError(Exception):
    pass


def route_task(task_type: str) -> str:
    agent_code = ROUTES.get(task_type)

    if agent_code is None:
        raise RoutingError(
            f"No deterministic route for task type: {task_type}"
        )

    return agent_code