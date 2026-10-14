# K2 Policy
## Approval Gates
| Condition | Gate |
|-----------|------|
| Audience built for an approved brief | Auto-proceed; Marketing Manager notified of size |
| Audience size > 10,000 contacts | Marketing Manager review before broadcast trigger |
| Segment criteria includes sensitive attributes | Legal/Compliance review required |
| PII export requested | Marketing Manager approval + data-privacy log entry |
| Audience build for unapproved brief | VALIDATION_ERROR; brief must be approved first |

## Constraints
1. Opt-out status is checked at build time; opted-out contacts are excluded even if previously included.
2. Audience lists are campaign-scoped; they cannot be shared across campaigns without re-build.
3. The agent does not send messages; it only prepares the audience list for K3 (Broadcast).
4. Contact data freshness: source data must not be older than 7 days for the build to proceed.
5. All builds are logged in the audience_build_log with segment criteria, record count, and timestamp.

## Data
- Source: customer_master, leads_table, marketing_consent_register, campaign_briefs
- Output: campaign_audiences table, audience_build_log, marketing_notifications
