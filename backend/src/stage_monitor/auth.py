import asyncio
import hashlib
import hmac
import json
import secrets
from datetime import timedelta

from pydantic import Field

from .contracts import Model, new_id, now

ROLES = {"viewer", "operator", "audio", "video", "network", "admin"}


class UserCreate(Model):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=12, max_length=256)
    roles: list[str] = Field(default_factory=lambda: ["viewer"], min_length=1)
    device_ids: list[str] | None = None


class Login(Model):
    username: str = Field(max_length=80)
    password: str = Field(max_length=256)


class UserUpdate(Model):
    roles: list[str] | None = Field(default=None, min_length=1)
    device_ids: list[str] | None = None
    enabled: bool | None = None
    password: str | None = Field(default=None, min_length=12, max_length=256)


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
    return f"scrypt${salt}${digest.hex()}"


def verify(password, encoded):
    _, salt, _ = encoded.split("$")
    return hmac.compare_digest(password_hash(password, salt), encoded)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def public_user(row):
    return {
        "id": row["id"], "username": row["username"], "roles": json.loads(row["roles"]),
        "device_ids": json.loads(row["device_ids"]) if row["device_ids"] else None,
        "enabled": bool(row["enabled"]),
    }


def can_access(user, device_id):
    return "admin" in user["roles"] or user["device_ids"] is None or device_id in user["device_ids"]


def can_command(user, device_id, capability):
    if not can_access(user, device_id):
        return False
    roles = set(user["roles"])
    if capability.startswith('configuration.'):
        return 'admin' in roles
    if roles & {"admin", "operator"}:
        return True
    domain = capability.split(".")[0]
    return (
        ("audio" in roles and domain in {"audio", "rf"})
        or ("video" in roles and domain in {"camera", "video"})
        or ("network" in roles and domain == "network")
    )


class AuthService:
    def __init__(self, storage, settings):
        self.storage = storage
        self.settings = settings
        self.dummy_hash = password_hash(secrets.token_urlsafe(24))
        self.attempts = {}

    async def bootstrap(self):
        if await self.storage.rows("SELECT id FROM users LIMIT 1"):
            return
        password = self.settings.bootstrap_password
        if not password or len(password) < 12:
            raise RuntimeError("Set STAGE_ADMIN_PASSWORD (at least 12 characters) for first startup")
        await self.create_user(UserCreate(
            username=self.settings.bootstrap_username, password=password, roles=["admin"]
        ))

    async def create_user(self, data, actor_id=None):
        if not set(data.roles) <= ROLES:
            raise ValueError("Unknown role")
        encoded = await asyncio.to_thread(password_hash, data.password)
        user_id = new_id()
        async with self.storage.lock:
            try:
                await self.storage.db.execute(
                    "INSERT INTO users(id,username,password_hash,roles,device_ids) VALUES (?,?,?,?,?)",
                    (user_id, data.username, encoded, json.dumps(data.roles),
                     json.dumps(data.device_ids) if data.device_ids is not None else None),
                )
                if actor_id:
                    await self.storage._audit(actor_id, "user.create", user_id,
                                              data.model_dump(exclude={"password"}))
                await self.storage.db.commit()
            except BaseException:
                await self.storage.db.rollback()
                raise
        rows = await self.storage.rows("SELECT * FROM users WHERE id=?", (user_id,))
        return public_user(rows[0])

    async def update_user(self, user_id, data, actor_id):
        if data.roles is not None and not set(data.roles) <= ROLES:
            raise ValueError("Unknown role")
        encoded = await asyncio.to_thread(password_hash, data.password) if data.password else None
        async with self.storage.lock:
            try:
                async with self.storage.db.execute("SELECT * FROM users") as cursor:
                    rows = await cursor.fetchall()
                row = next((r for r in rows if r["id"] == user_id), None)
                if row is None:
                    raise KeyError(user_id)
                previous = public_user(row)
                updated = {**previous}
                if data.roles is not None:
                    updated["roles"] = data.roles
                if data.enabled is not None:
                    updated["enabled"] = data.enabled
                if "device_ids" in data.model_fields_set:
                    updated["device_ids"] = data.device_ids
                others = [public_user(r) for r in rows if r["id"] != user_id]
                if not any(u["enabled"] and "admin" in u["roles"] for u in [updated, *others]):
                    raise ValueError("At least one enabled admin is required")
                await self.storage.db.execute(
                    "UPDATE users SET roles=?,device_ids=?,enabled=?,password_hash=? WHERE id=?",
                    (json.dumps(updated["roles"]),
                     json.dumps(updated["device_ids"]) if updated["device_ids"] is not None else None,
                     int(updated["enabled"]), encoded or row["password_hash"], user_id),
                )
                if encoded or not updated["enabled"]:
                    await self.storage.db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
                await self.storage._audit(actor_id, "user.update", user_id,
                                          {"previous": previous, "new": updated,
                                           "password_changed": bool(encoded)})
                await self.storage.db.commit()
                return updated
            except BaseException:
                await self.storage.db.rollback()
                raise

    async def login(self, data, client):
        clock = asyncio.get_running_loop().time()
        # Bound the rate-limit cache; limits are per client, not per invented username.
        self.attempts = {k: v for k, v in self.attempts.items() if clock - v[0] < 60}
        started, count = self.attempts.get(client, (clock, 0))
        if count >= 10 or len(self.attempts) >= 10000:
            raise PermissionError("rate_limited")
        self.attempts[client] = (started, count + 1)
        rows = await self.storage.rows("SELECT * FROM users WHERE username=?", (data.username,))
        encoded = rows[0]["password_hash"] if rows else self.dummy_hash
        valid = await asyncio.to_thread(verify, data.password, encoded)
        if not rows or not valid or not rows[0]["enabled"]:
            raise PermissionError("invalid_credentials")
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        await self.storage.write("INSERT INTO sessions VALUES (?,?,?,?)", (
            token_hash(token), rows[0]["id"], csrf,
            (now() + timedelta(seconds=self.settings.session_seconds)).isoformat(),
        ))
        return token, csrf, public_user(rows[0])

    async def session(self, token):
        if not token:
            return None
        rows = await self.storage.rows(
            "SELECT users.*, sessions.csrf FROM sessions JOIN users ON users.id=sessions.user_id "
            "WHERE token_hash=? AND expires_at>? AND enabled=1", (token_hash(token), now().isoformat()),
        )
        return (public_user(rows[0]), rows[0]["csrf"]) if rows else None

    async def logout(self, token):
        await self.storage.write("DELETE FROM sessions WHERE token_hash=?", (token_hash(token),))
