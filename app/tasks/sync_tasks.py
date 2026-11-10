from app.services.sync_engine import full_sync

async def scheduled_sync(tenant_id: str):
    return await full_sync(tenant_id, last_sync=None)
