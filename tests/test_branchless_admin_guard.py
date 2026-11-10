# Test autonome (stubs DB/starlette) : python tests/test_branchless_admin_guard.py  (depuis la racine du projet)
import sys, types, uuid, json

import asyncio, json as _json
st = types.ModuleType("starlette"); sc = types.ModuleType("starlette.concurrency"); sr = types.ModuleType("starlette.responses")
async def run_in_threadpool(f, *a): return f(*a)
class JSONResponse:
    def __init__(s, status_code=200, content=None): s.status, s.content = status_code, content
    async def __call__(s, scope, receive, send):
        await send({"type": "http.response.start", "status": s.status, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": _json.dumps(s.content).encode()})
sc.run_in_threadpool = run_in_threadpool; sr.JSONResponse = JSONResponse
sys.modules.update({"starlette": st, "starlette.concurrency": sc, "starlette.responses": sr})
import sys, types, uuid, json
from datetime import datetime, timedelta
sys.path.insert(0, __import__("os").getcwd() + "")

# --- Stubs des modules DB/modèles (évite Postgres) ---
T1, T2 = str(uuid.uuid4()), str(uuid.uuid4())
B_SUB, B_EXP, B_NONE, B_OTHER = (uuid.uuid4() for _ in range(4))
class Sub:
    def __init__(s, active): s.a = active
    def is_active(s): return s.a
class Br:
    def __init__(s, id, t): s.id, s.tenant_id, s.is_active = id, t, True
branches = {B_SUB: Br(B_SUB, T1), B_EXP: Br(B_EXP, T1), B_NONE: Br(B_NONE, T1), B_OTHER: Br(B_OTHER, T2)}
subs = {B_SUB: Sub(True), B_EXP: Sub(False)}
U_ADMIN, U_BR = str(uuid.uuid4()), str(uuid.uuid4())
class U:
    def __init__(s, id, role, branch): s.id, s.role, s.branch_id, s.tenant_id = id, role, branch, T1
users = {U_ADMIN: U(U_ADMIN, "admin", None), U_BR: U(U_BR, "admin", B_SUB)}

class Col:
    def __init__(s, n): s.n = n
    def __eq__(s, v): return (s.n, v)
class BranchM: id = Col("id")
class SubM: branch_id = Col("branch_id")
class UserM: id = Col("id")
class Q:
    def __init__(s, m): s.m = m
    def filter(s, c): s.c = c; return s
    def first(s):
        n, v = s.c
        if s.m is BranchM: return branches.get(v if isinstance(v, uuid.UUID) else uuid.UUID(str(v)))
        if s.m is SubM: return subs.get(v)
        return users.get(str(v))
class DB:
    def query(s, m): return Q(m)
    def close(s): pass
for name, attrs in {
    "app.db.session": {"SessionLocal": lambda: DB()},
    "app.models.branch": {"Branch": BranchM},
    "app.models.branch_subscription": {"BranchSubscription": SubM},
    "app.models.user": {"User": UserM},
    "app.core.security": {"decode_token": lambda t: json.loads(t)},
}.items():
    m = types.ModuleType(name); m.__dict__.update(attrs); sys.modules[name] = m
for pkg in ("app", "app.db", "app.models", "app.core"):
    sys.modules.setdefault(pkg, types.ModuleType(pkg))
sys.modules["app"].__path__ = [__import__("os").getcwd() + "/app"]
sys.modules["app.core"].__path__ = [__import__("os").getcwd() + "/app/core"]
sys.modules["app.middleware"] = types.ModuleType("app.middleware"); sys.modules["app.middleware"].__path__ = [__import__("os").getcwd() + "/app/middleware"]
# branch_scope réel
import importlib.util
def load(name, path):
    sp = importlib.util.spec_from_file_location(name, path); m = importlib.util.module_from_spec(sp); sys.modules[name] = m; sp.loader.exec_module(m)
load("app.core.branch_scope", "app/core/branch_scope.py")
load("app.middleware.branchless_admin_guard", "app/middleware/branchless_admin_guard.py")
from app.middleware.branchless_admin_guard import BranchlessAdminGuardMiddleware


received = {}
async def inner(scope, receive, send):
    body = b""; more = True
    while more:
        m = await receive(); body += m.get("body", b""); more = m.get("more_body", False)
    received["body"] = body
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"{}"})
from app.middleware.branchless_admin_guard import BranchlessAdminGuardMiddleware
mw = BranchlessAdminGuardMiddleware(inner)

def call(method, path, headers=None, body=None, query=""):
    h = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    raw = b""
    if body is not None:
        raw = _json.dumps(body).encode()
        h += [(b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode())]
    scope = {"type": "http", "method": method, "path": path, "query_string": query.encode(), "headers": h}
    out = {"status": None, "headers": {}, "body": b""}
    sent = False
    async def receive():
        nonlocal sent
        if not sent: sent = True; return {"type": "http.request", "body": raw, "more_body": False}
        return {"type": "http.disconnect"}
    async def send(m):
        if m["type"] == "http.response.start":
            out["status"] = m["status"]; out["headers"] = {k.decode(): v.decode() for k, v in m["headers"]}
        else: out["body"] += m.get("body", b"")
    received.clear()
    asyncio.run(mw(scope, receive, send))
    return out

def tok(uid, role="admin"): return {"Authorization": "Bearer " + _json.dumps({"sub": uid, "role": role, "type": "access"})}
A = tok(U_ADMIN)
def chk(label, o, code, err=None):
    e = _json.loads(o["body"]).get("error", "") if o["status"] == 403 else ""
    ok = o["status"] == code and (err is None or e == err)
    print("PASS" if ok else "FAIL", label, o["status"], e); assert ok

chk("écriture branche abonnée", call("POST", "/api/v1/stock/", {**A, "X-Branch-ID": str(B_SUB)}, {"a": 1}), 200)
assert _json.loads(received["body"]) == {"a": 1}; print("PASS body rejoué intact")
chk("écriture branche expirée", call("POST", "/api/v1/stock/", {**A, "X-Branch-ID": str(B_EXP)}, {}), 403, "branch_subscription_expired")
chk("écriture branche sans abonnement", call("POST", "/api/v1/stock/", {**A, "X-Branch-ID": str(B_NONE)}, {}), 403, "branch_subscription_required")
chk("écriture sans branche", call("POST", "/api/v1/stock/", A, {}), 403, "branch_required")
chk("branch_id en query sans abo", call("DELETE", "/api/v1/stock/x", A, None, f"branch_id={B_NONE}"), 403, "branch_subscription_required")
chk("branch_id dans le body sans abo", call("PUT", "/api/v1/stock/x", {**A, "X-Branch-ID": str(B_SUB)}, {"branch_id": str(B_NONE)}), 403, "branch_subscription_required")
chk("branche autre tenant", call("POST", "/api/v1/stock/", {**A, "X-Branch-ID": str(B_OTHER)}, {}), 403, "branch_not_in_tenant")
r1 = call("GET", "/api/v1/stock/", {**A, "X-Branch-ID": str(B_NONE)}); chk("lecture branche sans abo", r1, 200)
assert r1["headers"].get("x-read-only-mode") == "true"; print("PASS header read-only")
r2 = call("GET", "/api/v1/stock/", {**A, "X-Branch-ID": str(B_SUB)}); chk("lecture branche abonnée", r2, 200)
assert "x-read-only-mode" not in r2["headers"]
chk("lecture autre tenant", call("GET", "/api/v1/stock/", {**A, "X-Branch-ID": str(B_OTHER)}), 403, "branch_not_in_tenant")
chk("lecture sans branche", call("GET", "/api/v1/stock/", A), 200)
chk("route abonnement exemptée", call("POST", "/api/v1/subscriptions/pay", A, {}), 200)
chk("admin rattaché non concerné", call("POST", "/api/v1/stock/", tok(U_BR), {}), 200)
chk("non-admin non concerné", call("POST", "/api/v1/stock/", tok(U_ADMIN, "vendeur"), {}), 200)
chk("sans token non concerné", call("POST", "/api/v1/stock/", {}, {}), 200)
print("ALL OK")
