# Starts the Yexis ngrok tunnel as its OWN agent, separate from your always-on
# Twilio ngrok agent.
#
# WHY SEPARATE: a reserved ngrok domain belongs to ONE account, and an agent
# authenticates with ONE authtoken. Your two domains are on two different ngrok
# accounts:
#   brayan-misshapen-lu.ngrok-free.dev      -> account X (Twilio, port 6060)
#   arrange-coagulant-garnet.ngrok-free.dev -> account Y (this app, port 8000)
# so they CANNOT run under one agent / `ngrok start --all`. The free "1 session"
# limit is PER ACCOUNT, so two agents (one per account) run fine side by side.
#
# This agent uses scripts/ngrok-yexis.yml, which holds account Y's authtoken,
# web_addr localhost:4041 (so it doesn't clash with the Twilio agent's 4040),
# and the 'yexis' tunnel (localhost:8000 -> arrange-coagulant-garnet.ngrok-free.dev).
#
# Your Twilio ngrok keeps running EXACTLY as before. Run this one IN ADDITION,
# in its own terminal.

$ErrorActionPreference = "Stop"
$cfg = Join-Path $PSScriptRoot "ngrok-yexis.yml"

if ((Get-Content $cfg -Raw) -match "PASTE_ACCOUNT_Y_AUTHTOKEN_HERE") {
    Write-Error ("Set account Y's authtoken in $cfg first (replace PASTE_ACCOUNT_Y_AUTHTOKEN_HERE). " +
        "Get it at https://dashboard.ngrok.com/get-started/your-authtoken while logged into the account " +
        "that owns arrange-coagulant-garnet.ngrok-free.dev.")
    exit 1
}

Write-Host "Starting Yexis ngrok tunnel (account Y): http://localhost:8000 -> https://arrange-coagulant-garnet.ngrok-free.dev" -ForegroundColor Green
& ngrok start yexis --config $cfg
