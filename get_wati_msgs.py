import asyncio
from agent_context_injector import fetch_wati_api_messages

msgs = fetch_wati_api_messages('50369262590', pages=8)
for msg in reversed(msgs):
    print(f"[{msg.get('created')}] {msg.get('operatorName', 'Bot')}: {msg.get('text')}")
