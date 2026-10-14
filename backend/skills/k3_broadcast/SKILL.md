# Broadcast — K3
**Version:** 1.0.0  
**Autonomy:** A3  
**Go-live:** 2026-09-11  
**Primary KPI:** `delivery_rate_pct`  
**KPI Target:** ≥ 95%

## Purpose
Executes mass marketing broadcasts to campaign audiences via WhatsApp and email, using approved message templates. Manages send scheduling, rate limiting, and delivery tracking to maintain a delivery rate of 95% or above. Operates at A3 autonomy — once a campaign is approved and audience is ready, broadcast runs without further intervention unless delivery rate falls below threshold.

## Trigger
Triggered when a campaign audience is confirmed by K2 and campaign status is READY_TO_BROADCAST. Also supports scheduled broadcasts at a specified start_datetime.

## Actions
| Action | Description | Autonomy |
|--------|-------------|----------|
| load_audience | Retrieve the approved audience list from K2 output | A3 |
| select_template | Bind the approved message template to the campaign | A3 |
| schedule_broadcast | Set send schedule respecting rate limits and time windows | A3 |
| execute_send | Dispatch messages in batches via WhatsApp/email gateway | A3 |
| track_delivery | Poll delivery status and update per-contact delivery record | A3 |
| pause_on_threshold_breach | Auto-pause broadcast if delivery rate drops below 90% | A3 (auto-pause; alert to Marketing Manager) |

## Constraints
- Never broadcast using an unapproved or unregistered message template.
- Never send outside permitted hours: 09:00–20:00 IST.
- Never broadcast to audiences not confirmed by K2; raw contact lists are rejected.
- Auto-pause if delivery rate drops below 90% for a rolling 500-message window.
- All broadcast jobs must record per-contact sent/delivered/failed status.
