#app/core/policies.py
def enforce_tenant_isolation(user_tenant_id, resource_tenant_id):
    if user_tenant_id != resource_tenant_id:
        raise PermissionError("Accès interdit : isolation multi-tenant")
