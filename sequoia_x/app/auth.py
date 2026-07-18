"""管理员与普通用户认证、服务端会话、CSRF 与登录限流。"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, HTTPException, Request, Response, status

from sequoia_x.app.db import AppDatabase, utc_now

SESSION_COOKIE = "sequoia_session"
SESSION_HOURS = 12
_hasher = PasswordHasher()


class AuthService:
    def __init__(self, db: AppDatabase, cookie_secure: bool = False) -> None:
        self.db = db
        self.cookie_secure = cookie_secure

    def bootstrap(self, username: str, password: str) -> None:
        if not password:
            return
        existing = self.db.query_one("SELECT id FROM admin_user WHERE role='ADMIN' LIMIT 1")
        if existing:
            return
        now = utc_now()
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO admin_user(username,password_hash,role,created_at,updated_at) "
                "VALUES (?,?,?,?,?)",
                (username, _hasher.hash(password), "ADMIN", now, now),
            )

    def register(self, username: str, password: str) -> dict[str, Any]:
        username = username.strip()
        if not 3 <= len(username) <= 32:
            raise HTTPException(status_code=422, detail="账号 ID 需要 3 至 32 个字符")
        if not all(char.isalnum() or char in "_-" for char in username):
            raise HTTPException(status_code=422, detail="账号 ID 仅支持字母、数字、下划线和短横线")
        if len(password) < 8:
            raise HTTPException(status_code=422, detail="密码至少需要 8 个字符")
        now = utc_now()
        try:
            with self.db.transaction() as conn:
                cursor = conn.execute(
                    "INSERT INTO admin_user(username,password_hash,role,created_at,updated_at) "
                    "VALUES (?,?,?,?,?)",
                    (username, _hasher.hash(password), "MEMBER", now, now),
                )
                user_id = int(cursor.lastrowid)
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                raise HTTPException(status_code=409, detail="账号 ID 已存在") from exc
            raise
        self.db.audit(username, "REGISTER", "admin_user", user_id)
        return {"username": username, "role": "MEMBER"}

    def login(
        self, username: str, password: str, request: Request, response: Response
    ) -> dict[str, Any]:
        username = username.strip()
        ip = request.client.host if request.client else "unknown"
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat(
            timespec="seconds"
        )
        recent = self.db.query_one(
            "SELECT COUNT(*) count FROM login_attempt WHERE ip_address=? AND succeeded=0 AND created_at>=?",
            (ip, cutoff),
        )
        if recent and int(recent["count"]) >= 5:
            raise HTTPException(status_code=429, detail="登录失败次数过多，请 15 分钟后重试")
        user = self.db.query_one(
            "SELECT * FROM admin_user WHERE username=? COLLATE NOCASE", (username,)
        )
        valid = False
        if user:
            try:
                valid = _hasher.verify(str(user["password_hash"]), password)
            except VerifyMismatchError:
                valid = False
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO login_attempt(ip_address,succeeded,created_at) VALUES (?,?,?)",
                (ip, int(valid), utc_now()),
            )
        if not valid or user is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")

        raw_token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        expires = datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM auth_session WHERE expires_at<?", (utc_now(),))
            conn.execute(
                "INSERT INTO auth_session(user_id,token_hash,csrf_token,ip_address,user_agent,"
                "expires_at,created_at) VALUES (?,?,?,?,?,?,?)",
                (
                    user["id"], _token_hash(raw_token), csrf, ip,
                    request.headers.get("user-agent", "")[:500],
                    expires.isoformat(timespec="seconds"), utc_now(),
                ),
            )
        response.set_cookie(
            SESSION_COOKIE,
            raw_token,
            max_age=SESSION_HOURS * 3600,
            httponly=True,
            secure=self.cookie_secure,
            samesite="lax",
            path="/",
        )
        canonical_username = str(user["username"])
        self.db.audit(canonical_username, "LOGIN", detail={"ip": ip})
        return {
            "username": canonical_username,
            "role": user["role"],
            "csrf_token": csrf,
        }

    def logout(self, request: Request, response: Response) -> None:
        raw_token = request.cookies.get(SESSION_COOKIE)
        if raw_token:
            with self.db.transaction() as conn:
                conn.execute("DELETE FROM auth_session WHERE token_hash=?", (_token_hash(raw_token),))
        response.delete_cookie(SESSION_COOKIE, path="/")

    def change_password(self, user_id: int, current_password: str, new_password: str) -> None:
        if len(new_password) < 10:
            raise HTTPException(status_code=422, detail="新密码至少需要 10 个字符")
        user = self.db.query_one("SELECT * FROM admin_user WHERE id=?", (user_id,))
        if user is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        try:
            valid = _hasher.verify(str(user["password_hash"]), current_password)
        except VerifyMismatchError:
            valid = False
        if not valid:
            raise HTTPException(status_code=422, detail="当前密码不正确")
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE admin_user SET password_hash=?,updated_at=? WHERE id=?",
                (_hasher.hash(new_password), utc_now(), user_id),
            )
            conn.execute("DELETE FROM auth_session WHERE user_id=?", (user_id,))
        self.db.audit(str(user["username"]), "CHANGE_PASSWORD", "admin_user", user_id)

    def session(self, request: Request) -> dict[str, Any]:
        raw_token = request.cookies.get(SESSION_COOKIE)
        if not raw_token:
            raise HTTPException(status_code=401, detail="未登录")
        row = self.db.query_one(
            "SELECT s.*,u.username,u.role FROM auth_session s JOIN admin_user u ON u.id=s.user_id "
            "WHERE s.token_hash=? AND s.expires_at>?",
            (_token_hash(raw_token), utc_now()),
        )
        if row is None:
            raise HTTPException(status_code=401, detail="会话已过期")
        return row


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def auth_service(request: Request) -> AuthService:
    return request.app.state.auth


def require_user(
    request: Request, service: AuthService = Depends(auth_service)
) -> dict[str, Any]:
    return service.session(request)


def optional_user(
    request: Request, service: AuthService = Depends(auth_service)
) -> dict[str, Any] | None:
    if not request.cookies.get(SESSION_COOKIE):
        return None
    try:
        return service.session(request)
    except HTTPException:
        return None


def require_admin(
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    if user.get("role") != "ADMIN":
        raise HTTPException(status_code=403, detail="仅管理员可以执行此操作")
    return user


def require_csrf(
    request: Request,
    user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    supplied = request.headers.get("x-csrf-token")
    if not supplied or not secrets.compare_digest(supplied, str(user["csrf_token"])):
        raise HTTPException(status_code=403, detail="CSRF 校验失败")
    return user


def require_admin_csrf(
    user: dict[str, Any] = Depends(require_csrf),
) -> dict[str, Any]:
    if user.get("role") != "ADMIN":
        raise HTTPException(status_code=403, detail="仅管理员可以执行此操作")
    return user
