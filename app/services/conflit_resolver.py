def resolve_conflict(local, remote):
    if local["updated_at"] > remote["updated_at"]:
        return local
    return remote
