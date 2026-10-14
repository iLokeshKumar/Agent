# K1 Policy
## Approval Gates
| Condition | Gate |
|-----------|------|
| All campaign briefs | Marketing Manager approval required before activation |
| Budget > ₹2,00,000 | Additional Business Head countersign required |
| Brief references unverified product claims | Legal/Compliance review gate |
| Campaign targets a new/untested segment | Marketing Manager must confirm segment validity |
| Brief amendments after approval | New version required; re-approval mandatory |

## Constraints
1. The agent creates briefs only; it has no authority to activate campaigns or allocate budgets.
2. Mandatory fields — objective, target_segment, channel, budget, start_date — must all be present.
3. Budget figures must be within the current quarter's marketing budget envelope as defined in finance_config.
4. Product references must be validated against the active SKU catalogue; discontinued SKUs are rejected.
5. All submitted briefs are logged with submitter ID, timestamp, and version number.

## Data
- Source: campaign_brief_submissions, sku_catalogue, marketing_budget_config, segment_master
- Output: campaigns table (status=DRAFT), brief_approval_queue, marketing_notifications
