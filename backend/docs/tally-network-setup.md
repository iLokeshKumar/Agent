# Tally Integration — Network Setup & Entity Mapping

Status as of 2026-09-16. Port 9000 was down (WinError 10061) because
tally.exe was closed — only tallyscheduler.exe was running. Restarted
the app on this machine; port 9000 is LISTENING again (verified via
netstat + TCP probe + preflight `tally.ok=true`).

## Startup dependency (root cause & fix)

The XML service lives inside the running Tally app. Two failure modes:

1. **tally.exe not running** → port 9000 refused (10061). Fix: start
   `C:\Program Files\TallyPrime (1)\tally.exe` (installed path is the
   "(1)" folder, despite the docroot `C:\Program Files\TallyPrime`
   holding only appdata + tallyhttp.log). From a non-interactive
   shell: `powershell -Command "Start-Process 'C:\Program Files\TallyPrime (1)\tally.exe'"`
2. **App running, no company open** → requests fail with
   `Could not find Company ''`. The XML channel cannot force a company
   open remotely; someone must open the company(s) in the Tally GUI.

Permanent option (pending owner approval): add `Load=<company name>`
lines to `tally.ini` so both companies auto-open at startup.

## Protocol sanity check (2026-09-16, verified live)

- `POST http://127.0.0.1:9000` with an Export Data envelope → HTTP 200
  with a Tally `<RESPONSE>` (not a connection error) — protocol OK.
- Trial Balance scoped to a company name fails while no company is
  open (`Could not find Company ''`), regardless of the name sent.
- `List of Companies` report name is rejected by TallyPrime 7
  (`Could not find Report 'List of Companies'`) — company discovery
  stays via data folders / GUI, as below.
- Exact company display names (from `.env`, to confirm in GUI):
  - `YEXIS ELECTRONICS - K - (from 1-Apr-24)` (YEXIS-EL)
  - `YEXIS ELECTRONICS - (from 1-Apr-24) - (from 1-Apr-25)` (YEXIS-SL)

## Topology (verified by probes)

- TallyPrime 7 **Gold** runs as a client app on the **workstation**
  (this machine). The XML/ODBC service lives *inside* the running
  Tally app — `TALLY_HOST=127.0.0.1:9000` works only while Tally is
  open. Tally closed → port 9000 disappears (confirmed twice).
- Data + license live on the **server** `Desktop-4c9ms85`:
  - Data share: `\\Desktop-4c9ms85\d\Tally Data 2026-2027\YE`
  - License server: port **9999** (reachable, OPEN)
  - Port 9000 on the server is CLOSED — the XML gateway is per-app,
    not per-server.
- Hostname resolves to a link-local IPv6 (`fe80::...%6`); SMB works,
  plain IPv4 HTTP to the server does not. Irrelevant while TALLY_HOST
  stays 127.0.0.1.
- If Tally ever moves to the server permanently, change only
  `TALLY_HOST` in `backend/.env` (and open port 9000 there).

## Entity mapping (authoritative, from the owner)

| Platform entity | Tally company | Data folder |
|---|---|---|
| YEXIS-EL | **Yexis Electronics - Kerala** | `010005` |
| YEXIS-SL | **Yexis Electronics - Tamil Nadu** | `100000` |

Stored in `backend/.env` as `TALLY_COMPANY_YEXIS_EL` /
`TALLY_COMPANY_YEXIS_SL` (exact Tally display names still to be
confirmed against the loaded companies — folders only give the
folder numbers, not the exact display string).

## Data folders

- `010005`, `100000` — the live company folders. The accountant
  quoted a **start date & end date** for each (financial periods);
  owner will confirm tomorrow. Needed for F3 period postings and any
  balance pulls.
- `FULL/` (100001, 100002) — **out of scope**, created by the
  accounts team for their own purposes, incomplete. Safe to skip.

## TallyPrime 7 protocol notes (verified live)

- TallyPrime 7 answers **"Unknown Request, cannot be processed"** to
  the ERP-9-era verbs. Correct verbs:
  - Export: `TALLYREQUEST=Export Data` (Trial Balance etc.)
  - Import: `TALLYREQUEST=Import Data` (Vouchers, Masters)
- Platform code already fixed: `tally_xml.py` (Import Data),
  `tally_gateway.py` (Export Data + Unknown-Request detection).
- Company scoping needs a company **open in the Tally session** —
  otherwise `Could not set 'SVCurrentCompany' to '...'` or
  `Could not find Company ''`.
- TDL collections over HTTP need `ISINITIALIZE="Yes"` on the
  collection, and returned zero companies in our probes — do not rely
  on them for company discovery; use the data folders / GUI instead.

## Open items

1. Owner opens both companies in Tally on this machine → re-probe
   Trial Balance for both entities.
2. Confirm exact display names of the two companies.
3. Get period start/end dates from the accountant (tomorrow).
4. Then F3 staging→approval→post dry-run against the live companies.
