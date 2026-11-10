# Test autonome (stubs) : python tests/test_user_branch_assignment.py  (depuis la racine du projet)
import importlib.util, os, sys, types, uuid

ROOT = os.getcwd()
T1, T2 = str(uuid.uuid4()), str(uuid.uuid4())
B1, B2, B_OTHER, B_INACTIVE, B_FULL = (uuid.uuid4() for _ in range(5))

class Col:
    def __init__(s, n): s.n = n
    def __eq__(s, v): return ("eq", s.n, v)
    def __ne__(s, v): return ("ne", s.n, v)
class Model:
    def __init__(s, **kw): s.__dict__.update(kw)
def mk(name, cols):
    return type(name, (Model,), {c: Col(c) for c in cols})
Branch = mk("Branch", ["id"]); User = mk("User", ["branch_id", "actif"]); UserBranch = mk("UserBranch", ["user_id", "branch_id", "is_primary"])
Sub = mk("Sub", ["branch_id"])
class Obj(Model): pass
branches = {
    B1: Obj(id=B1, tenant_id=T1, is_active=True, name="A"), B2: Obj(id=B2, tenant_id=T1, is_active=True, name="B"),
    B_OTHER: Obj(id=B_OTHER, tenant_id=T2, is_active=True, name="X"), B_INACTIVE: Obj(id=B_INACTIVE, tenant_id=T1, is_active=False, name="I"),
    B_FULL: Obj(id=B_FULL, tenant_id=T1, is_active=True, name="F"),
}
subs = {B_FULL: Obj(max_users=2)}
links, added, user_count = [], [], {B_FULL: 2}
class Q:
    def __init__(s, m): s.m, s.f = m, []
    def filter(s, *c): s.f += c; return s
    def first(s):
        if s.m is Branch: return branches.get(s.f[0][2])
        if s.m is Sub: return subs.get(s.f[0][2])
        if s.m is UserBranch:
            return next((l for l in links if l.user_id == s.f[0][2] and l.branch_id == s.f[1][2]), None)
    def count(s): return user_count.get(s.f[0][2], 0)
    def update(s, vals, synchronize_session=False):
        for l in links:
            if l.user_id == s.f[0][2] and l.branch_id != s.f[1][2]: l.__dict__.update(vals)
class DB:
    def query(s, m): return Q(m)
    def add(s, o): links.append(o)
for name, attrs in {"app.models.branch": {"Branch": Branch}, "app.models.user": {"User": User},
                    "app.models.user_branch": {"UserBranch": UserBranch}, "app.models.branch_subscription": {"BranchSubscription": Sub},
                    "sqlalchemy": {}, "sqlalchemy.orm": {"Session": object}}.items():
    m = types.ModuleType(name); m.__dict__.update(attrs); sys.modules[name] = m
for pkg, path in (("app", "app"), ("app.core", "app/core"), ("app.services", "app/services")):
    m = types.ModuleType(pkg); m.__path__ = [os.path.join(ROOT, path)]; sys.modules[pkg] = m
def load(name, path):
    sp = importlib.util.spec_from_file_location(name, os.path.join(ROOT, path)); m = importlib.util.module_from_spec(sp); sys.modules[name] = m; sp.loader.exec_module(m); return m
load("app.core.branch_scope", "app/core/branch_scope.py")
svc = load("app.services.user_branch_service", "app/services/user_branch_service.py")

def expect_err(code, fn):
    try: fn()
    except svc.BranchAssignmentError as e: assert e.code == code, (e.code, code); print("PASS", code); return
    raise AssertionError("aucune erreur levée pour " + code)

db = DB()
expect_err("branch_required", lambda: svc.resolve_required_branch(db, T1, None))
expect_err("branch_required", lambda: svc.resolve_required_branch(db, T1, "  "))
expect_err("invalid_branch_id", lambda: svc.resolve_required_branch(db, T1, "pas-un-uuid"))
expect_err("branch_not_found", lambda: svc.resolve_required_branch(db, T1, str(uuid.uuid4())))
expect_err("branch_not_found", lambda: svc.resolve_required_branch(db, T1, str(B_INACTIVE)))
expect_err("branch_not_in_tenant", lambda: svc.resolve_required_branch(db, T1, str(B_OTHER)))
expect_err("branch_user_limit_reached", lambda: svc.check_branch_user_quota(db, branches[B_FULL]))
svc.check_branch_user_quota(db, branches[B1]); print("PASS quota sans abonnement/limite")
assert svc.resolve_required_branch(db, T1, str(B1)) is branches[B1]; print("PASS branche valide")

u = Obj(id=uuid.uuid4(), role="vendeur", branch_id=None, active_branch_id=None)
svc.assign_user_to_branch(db, u, branches[B1], role="vendeur")
assert u.branch_id == B1 and u.active_branch_id == B1 and len(links) == 1 and links[0].is_primary
assert links[0].permissions["can_sell"] is True and links[0].role_in_branch == "vendeur"
svc.assign_user_to_branch(db, u, branches[B2], role="vendeur")  # changement de branche
assert u.branch_id == B2 and len(links) == 2
assert [l.is_primary for l in links] == [False, True]
print("PASS affectation + changement de branche (une seule branche primaire)")
print("ALL OK")
