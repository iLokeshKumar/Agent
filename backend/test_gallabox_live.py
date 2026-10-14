import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_servers.whatsapp.server import send_session_message
from app.core.config import settings
print("config", settings.waba_provider, settings.waba_api_key[:8]+"...", settings.waba_api_url, settings.waba_phone_number_id)
try:
    result = send_session_message(to="+15554092635", body="Hello from Yexis Agent Platform - live Gallabox test. If you see this, WhatsApp BSP is live.")
    print("SUCCESS", result)
except Exception as e:
    print("FAILED", e)
    import traceback
    traceback.print_exc()
