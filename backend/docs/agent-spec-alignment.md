# Agent-to-spec alignment: decisions required

The code contains 31 registered agents, but code presence does not establish
that the delivered behavior matches the July 2026 functional specifications.
Resolve the following scope differences against one owner-approved, versioned
agent catalogue before treating the affected agents as complete or promoting
them to production.

| Code | Blueprint / WBS responsibility | Current implementation name | Alignment |
| --- | --- | --- | --- |
| C1 | Order Promise & Allocation | Indent Planning | Different business responsibility; decide whether this is a rename, a separate agent, or an additional scope. |
| C2 | Dispatch Planning & Tracking | Dispatch Tracker | Likely overlap; verify that planning, documents, carrier milestones, and proof of delivery are all covered. |
| C3 | Customer & Site Communication | SCM Claims Tracker | Different responsibility; identify the claims capability's intended owner and whether the communication agent is missing. |
| H3 | Attendance, Leave & Field-Force Support | Attrition Risk | Different responsibility; confirm whether attendance processing or attrition analysis is intended. |
| H4 | Performance & Retention Insights (WBS) | Payroll Check | Different responsibility; reconcile this with the HR functional specification and payroll requirements. |
| S4 | Project Material & Demo Stock Custody | Physical Verification | Related inventory area, but not equivalent; decide whether custody workflows are implemented separately. |

For every code, the owner-approved catalogue should capture: canonical purpose,
in-scope workflows, authoritative specification/version, input/output contract,
system-of-record writes, human approval boundary, responsible business owner,
and acceptance-test IDs. Keep code, route, skill, gate config, dashboards, and
runbooks mapped to that catalogue. Where the specs conflict, record the
business decision instead of silently renaming code or declaring an agent
complete.

This is a requirements-traceability gap, not proof that the listed code is
defective. Resolve it through product/business-owner sign-off and executable
acceptance tests before changing production routes.
