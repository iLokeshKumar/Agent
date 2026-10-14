# Skill: F3 Books→Tally Sync

## Identity
Nightly Zoho Books → Tally alignment via stage-and-approve. Suite:
Finance. Runtime: hybrid. The statutory ledger is never written
directly by this agent.

## Procedure
1. Fetch day-book delta from Zoho (or accept payload entries when
   Zoho is unconfigured).
2. Map accounts through the mapping table; unmapped accounts are
   prefixed `UNMAPPED:` — flagged, not dropped.
3. Check balance: debit total == credit total; unbalanced input gets
   a loud warning in output.
4. Render Tally XML import envelope.
5. **Stage voucher lines** into `tally_staging_voucher` as STAGED.
6. STOP. Posting happens only after accountant approval through the
   Tally Gateway.

## Tool bindings
zoho client · tally_xml builder · tally_gateway (stage only)

## Autonomy & HITL
- A1 for posting — always. Accountant approves staged vouchers via
  `/mcp/tally/approve`, then `post_approved` is the sole write path.

## Never
- Post unapproved vouchers (structurally impossible by design).
- Drop an unmapped account to make the batch balance.
