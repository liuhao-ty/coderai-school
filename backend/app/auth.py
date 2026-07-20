import hashlib
import hmac
import re
import secrets
from datetime import timedelta

from fastapi import Depends, Header, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.app.db import get_db
from backend.app.models import AppSetting, StudentSession, TeacherSession, User, now


DEFAULT_TEACHER_PASSWORD = "123456"
DEFAULT_ADMIN_USERNAME = "admin"
PASSWORD_SETTING_KEY = "teacher_password_hash"
PASSWORD_CHANGE_REQUIRED_KEY = "teacher_password_change_required"
SECRET_SETTING_KEY = "app_auth_secret"
TEACHER_ACCESS_TTL = timedelta(hours=2)
TEACHER_REFRESH_TTL = timedelta(days=7)
STUDENT_SESSION_TTL = timedelta(days=7)
PASSWORD_HASH_ITERATIONS = 600_000
LOGIN_WINDOW = timedelta(minutes=10)
LOGIN_LOCK_TTL = timedelta(minutes=15)
LOGIN_MAX_FAILURES = 5
USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{3,39}$")
TEACHER_ROLES = {"teacher", "admin"}
DEFAULT_STUDENT_PASSWORD = "bcm123456"


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), PASSWORD_HASH_ITERATIONS).hex()
    return f"pbkdf2_sha256${PASSWORD_HASH_ITERATIONS}${salt}${digest}"


def _legacy_password_hash(password: str) -> str:
    return hashlib.sha256(f"coderai-teacher:{password}".encode("utf-8")).hexdigest()


def _verify_password_hash(stored_hash: str, password: str) -> bool:
    if stored_hash.startswith("pbkdf2_sha256$"):
        try:
            _, iterations, salt, expected = stored_hash.split("$", 3)
            actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations)).hex()
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(expected, actual)
    return bool(stored_hash) and hmac.compare_digest(stored_hash, _legacy_password_hash(password))


def verify_account_password(db: Session, user: User, password: str) -> bool:
    valid = _verify_password_hash(user.password_hash or "", password)
    if valid and not user.password_hash.startswith("pbkdf2_sha256$"):
        user.password_hash = hash_password(password)
        db.commit()
    return valid


def get_setting(db: Session, key: str) -> str | None:
    setting = db.query(AppSetting).filter(AppSetting.key == key).first()
    return setting.value if setting else None


def set_setting(db: Session, key: str, value: str):
    setting = db.query(AppSetting).filter(AppSetting.key == key).first()
    if setting:
        setting.value = value
    else:
        db.add(AppSetting(key=key, value=value))
    db.commit()


def normalize_username(username: str) -> str:
    return username.strip().lower()


def validate_username(username: str) -> str:
    normalized = normalize_username(username)
    if not USERNAME_PATTERN.fullmatch(normalized):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "USERNAME_INVALID",
                "message": "用户名需为 4-40 位小写字母、数字、点、下划线或短横线，并以字母或数字开头。",
            },
        )
    return normalized


def validate_teacher_password(password: str) -> None:
    if len(password) < 8:
        raise HTTPException(
            status_code=400,
            detail={"code": "PASSWORD_WEAK", "message": "教师密码至少 8 位。"},
        )


def validate_admin_password(password: str) -> None:
    if len(password) < 10 or not any(char.isalpha() for char in password) or not any(char.isdigit() for char in password) or password.isalnum():
        raise HTTPException(
            status_code=400,
            detail={"code": "PASSWORD_WEAK", "message": "管理员密码至少 10 位，并同时包含字母、数字和特殊字符。"},
        )


def teacher_password_is_weak(password: str) -> bool:
    return len(password) < 10 or not any(char.isalpha() for char in password) or not any(char.isdigit() for char in password)


def validate_student_password(password: str) -> None:
    if len(password) < 8 or not any(char.isalpha() for char in password) or not any(char.isdigit() for char in password):
        raise HTTPException(
            status_code=400,
            detail={"code": "PASSWORD_WEAK", "message": "学生密码至少 8 位，并同时包含字母和数字。"},
        )


def generate_temporary_password() -> str:
    return f"Ca9!{secrets.token_urlsafe(8)}"


def account_payload(user: User) -> dict:
    return {
        "id": user.id,
        "name": user.name,
        "username": user.username,
        "role": user.role,
        "active": user.active,
        "password_change_required": user.password_change_required,
        "registered_at": user.registered_at.isoformat() if user.registered_at else None,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "created_at": user.created_at.isoformat(),
    }


def find_account_by_username(db: Session, username: str) -> User | None:
    normalized = normalize_username(username)
    if not normalized:
        return None
    return db.query(User).filter(func.lower(User.username) == normalized).first()


def ensure_auth_settings(db: Session):
    if not get_setting(db, PASSWORD_SETTING_KEY):
        set_setting(db, PASSWORD_SETTING_KEY, hash_password(DEFAULT_TEACHER_PASSWORD))
    if get_setting(db, PASSWORD_CHANGE_REQUIRED_KEY) is None:
        password_hash = get_setting(db, PASSWORD_SETTING_KEY) or ""
        required = _verify_password_hash(password_hash, DEFAULT_TEACHER_PASSWORD)
        set_setting(db, PASSWORD_CHANGE_REQUIRED_KEY, "true" if required else "false")
    if not get_setting(db, SECRET_SETTING_KEY):
        set_setting(db, SECRET_SETTING_KEY, secrets.token_hex(32))

    teacher_accounts = db.query(User).filter(User.role.in_(TEACHER_ROLES)).order_by(User.id.asc()).all()
    if not teacher_accounts:
        password_hash = get_setting(db, PASSWORD_SETTING_KEY) or hash_password(DEFAULT_TEACHER_PASSWORD)
        admin = User(
            name="机构管理员",
            role="admin",
            username=DEFAULT_ADMIN_USERNAME,
            password_hash=password_hash,
            password_change_required=get_setting(db, PASSWORD_CHANGE_REQUIRED_KEY) == "true",
            credential_version=1,
            registered_at=now(),
            age_level="",
            active=True,
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
        teacher_accounts = [admin]

    primary = next((item for item in teacher_accounts if item.role == "admin" and item.active), teacher_accounts[0])
    changed = False
    for session in db.query(TeacherSession).filter(TeacherSession.user_id.is_(None)).all():
        session.user_id = primary.id
        changed = True
    if changed:
        db.commit()


def login_teacher_by_credentials(db: Session, username: str, password: str) -> User:
    ensure_auth_settings(db)
    user = find_account_by_username(db, username or DEFAULT_ADMIN_USERNAME)
    if not user or user.role not in TEACHER_ROLES or not user.active or not verify_account_password(db, user, password):
        raise HTTPException(status_code=403, detail={"code": "ACCOUNT_INVALID", "message": "教师用户名或密码不正确。"})
    user.last_login_at = now()
    db.commit()
    db.refresh(user)
    return user


def verify_teacher_password(db: Session, password: str, username: str = DEFAULT_ADMIN_USERNAME) -> bool:
    try:
        login_teacher_by_credentials(db, username, password)
        return True
    except HTTPException:
        return False


def teacher_password_change_required(db: Session, user: User | None = None) -> bool:
    ensure_auth_settings(db)
    account = user or find_account_by_username(db, DEFAULT_ADMIN_USERNAME)
    return bool(account and account.password_change_required)


def _login_identifier_hash(scope: str, identifier: str) -> str:
    return hashlib.sha256(f"{scope}:{identifier}".encode("utf-8")).hexdigest()


def check_login_allowed(db: Session, scope: str, identifier: str) -> None:
    from backend.app.models import AuthLoginAttempt

    attempt = db.query(AuthLoginAttempt).filter(
        AuthLoginAttempt.scope == scope,
        AuthLoginAttempt.identifier_hash == _login_identifier_hash(scope, identifier),
    ).first()
    current = now()
    if attempt and attempt.locked_until and attempt.locked_until > current:
        retry_after = max(1, int((attempt.locked_until - current).total_seconds()))
        raise HTTPException(
            status_code=429,
            detail={"code": "LOGIN_RATE_LIMITED", "message": f"登录失败次数过多，请在 {retry_after} 秒后重试。"},
            headers={"Retry-After": str(retry_after)},
        )


def record_login_failure(db: Session, scope: str, identifier: str) -> int:
    from backend.app.models import AuthLoginAttempt

    key_hash = _login_identifier_hash(scope, identifier)
    attempt = db.query(AuthLoginAttempt).filter(AuthLoginAttempt.scope == scope, AuthLoginAttempt.identifier_hash == key_hash).first()
    current = now()
    if not attempt:
        attempt = AuthLoginAttempt(scope=scope, identifier_hash=key_hash, failed_count=0, window_started_at=current)
        db.add(attempt)
    if current - attempt.window_started_at > LOGIN_WINDOW:
        attempt.failed_count = 0
        attempt.window_started_at = current
        attempt.locked_until = None
    attempt.failed_count += 1
    if attempt.failed_count >= LOGIN_MAX_FAILURES:
        attempt.locked_until = current + LOGIN_LOCK_TTL
    db.commit()
    return max(0, int((attempt.locked_until - current).total_seconds())) if attempt.locked_until else 0


def clear_login_failures(db: Session, scope: str, identifier: str) -> None:
    from backend.app.models import AuthLoginAttempt

    db.query(AuthLoginAttempt).filter(
        AuthLoginAttempt.scope == scope,
        AuthLoginAttempt.identifier_hash == _login_identifier_hash(scope, identifier),
    ).delete(synchronize_session=False)
    db.commit()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def teacher_session_payload(session: TeacherSession, access_token: str = "", refresh_token: str = "") -> dict:
    payload = {
        "session_id": session.id,
        "device_name": session.device_name,
        "created_at": session.created_at.isoformat(),
        "last_seen_at": session.last_seen_at.isoformat(),
        "access_expires_at": session.access_expires_at.isoformat(),
        "expires_at": session.expires_at.isoformat(),
        "revoked": session.revoked_at is not None,
        "user": account_payload(session.user) if session.user else None,
    }
    if access_token:
        payload["token"] = access_token
    if refresh_token:
        payload["refresh_token"] = refresh_token
    return payload


def issue_teacher_session(db: Session, user: User, device_name: str = "此设备") -> dict:
    access_token = "cta_" + secrets.token_urlsafe(32)
    refresh_token = "ctr_" + secrets.token_urlsafe(48)
    current = now()
    session = TeacherSession(
        user_id=user.id,
        token_hash=_token_hash(access_token),
        refresh_token_hash=_token_hash(refresh_token),
        device_name=(device_name.strip() or "此设备")[:160],
        created_at=current,
        last_seen_at=current,
        access_expires_at=current + TEACHER_ACCESS_TTL,
        expires_at=current + TEACHER_REFRESH_TTL,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return teacher_session_payload(session, access_token, refresh_token)


def get_teacher_session(db: Session, token: str, allow_expired_access: bool = False) -> TeacherSession | None:
    if not token:
        return None
    session = db.query(TeacherSession).filter(TeacherSession.token_hash == _token_hash(token)).first()
    if not session or session.revoked_at is not None or session.expires_at <= now():
        return None
    if not allow_expired_access and session.access_expires_at <= now():
        return None
    user = session.user
    if not user or user.role not in TEACHER_ROLES or not user.active:
        return None
    return session


def verify_teacher_token(db: Session, token: str) -> bool:
    return get_teacher_session(db, token) is not None


def refresh_teacher_session(db: Session, refresh_token: str) -> dict:
    session = db.query(TeacherSession).filter(TeacherSession.refresh_token_hash == _token_hash(refresh_token)).first()
    if not session or session.revoked_at is not None or session.expires_at <= now():
        raise HTTPException(status_code=403, detail={"code": "TEACHER_REFRESH_INVALID", "message": "教师会话已失效，请重新登录。"})
    user = session.user
    if not user or user.role not in TEACHER_ROLES or not user.active:
        raise HTTPException(status_code=403, detail={"code": "TEACHER_ACCOUNT_DISABLED", "message": "教师账号已停用。"})
    access_token = "cta_" + secrets.token_urlsafe(32)
    next_refresh_token = "ctr_" + secrets.token_urlsafe(48)
    current = now()
    session.token_hash = _token_hash(access_token)
    session.refresh_token_hash = _token_hash(next_refresh_token)
    session.last_seen_at = current
    session.access_expires_at = current + TEACHER_ACCESS_TTL
    session.expires_at = current + TEACHER_REFRESH_TTL
    db.commit()
    db.refresh(session)
    return teacher_session_payload(session, access_token, next_refresh_token)


def revoke_teacher_session(db: Session, session: TeacherSession) -> None:
    if session.revoked_at is None:
        session.revoked_at = now()
        db.commit()


def revoke_teacher_session_by_refresh_token(db: Session, refresh_token: str) -> None:
    if not refresh_token:
        return
    session = db.query(TeacherSession).filter(TeacherSession.refresh_token_hash == _token_hash(refresh_token)).first()
    if session:
        revoke_teacher_session(db, session)


def revoke_all_teacher_sessions(db: Session, user_id: int | None = None) -> None:
    query = db.query(TeacherSession).filter(TeacherSession.revoked_at.is_(None))
    if user_id is not None:
        query = query.filter(TeacherSession.user_id == user_id)
    query.update({TeacherSession.revoked_at: now()}, synchronize_session=False)
    db.commit()


def issue_student_session(db: Session, student: User, device_name: str = "此设备") -> dict:
    token = "csa_" + secrets.token_urlsafe(40)
    current = now()
    session = StudentSession(
        user_id=student.id,
        token_hash=_token_hash(token),
        device_name=(device_name.strip() or "此设备")[:160],
        created_at=current,
        last_seen_at=current,
        expires_at=current + STUDENT_SESSION_TTL,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return {
        "token": token,
        "session_id": session.id,
        "expires_at": session.expires_at.isoformat(),
        "password_change_required": student.password_change_required,
    }


def get_student_session(db: Session, token: str) -> StudentSession | None:
    if not token:
        return None
    session = db.query(StudentSession).filter(StudentSession.token_hash == _token_hash(token)).first()
    if not session or session.revoked_at is not None or session.expires_at <= now():
        return None
    student = session.user
    if not student or student.role != "student" or not student.active or student.archived_at is not None:
        return None
    return session


def get_student_from_token(db: Session, token: str) -> User | None:
    session = get_student_session(db, token)
    return session.user if session else None


def revoke_student_session_by_token(db: Session, token: str) -> None:
    if not token:
        return
    session = db.query(StudentSession).filter(StudentSession.token_hash == _token_hash(token)).first()
    if session and session.revoked_at is None:
        session.revoked_at = now()
        db.commit()


def revoke_all_student_sessions(db: Session, user_id: int) -> None:
    db.query(StudentSession).filter(
        StudentSession.user_id == user_id,
        StudentSession.revoked_at.is_(None),
    ).update({StudentSession.revoked_at: now()}, synchronize_session=False)
    db.commit()


def login_student_by_credentials(db: Session, username: str, password: str) -> User:
    student = find_account_by_username(db, username)
    if not student or student.role != "student" or not student.active or student.archived_at is not None:
        raise HTTPException(status_code=403, detail={"code": "ACCOUNT_INVALID", "message": "学生用户名或密码不正确。"})
    if not student.password_hash or not verify_account_password(db, student, password):
        raise HTTPException(status_code=403, detail={"code": "ACCOUNT_INVALID", "message": "学生用户名或密码不正确。"})
    student.last_login_at = now()
    db.commit()
    db.refresh(student)
    return student


def change_student_password(db: Session, student: User, current_password: str, next_password: str) -> None:
    validate_student_password(next_password)
    if not verify_account_password(db, student, current_password):
        raise HTTPException(status_code=403, detail={"code": "PASSWORD_INVALID", "message": "当前密码不正确。"})
    student.password_hash = hash_password(next_password)
    student.password_change_required = False
    student.credential_version = max(1, student.credential_version or 1) + 1
    db.commit()
    revoke_all_student_sessions(db, student.id)


def change_teacher_password(db: Session, user: User, current_password: str, next_password: str):
    if user.role == "admin":
        validate_admin_password(next_password)
    else:
        validate_teacher_password(next_password)
    if not verify_account_password(db, user, current_password):
        raise HTTPException(status_code=403, detail={"code": "PASSWORD_INVALID", "message": "当前教师密码不正确。"})
    next_hash = hash_password(next_password)
    user.password_hash = next_hash
    user.password_change_required = False
    user.credential_version = max(1, user.credential_version or 1) + 1
    db.commit()
    if user.username == DEFAULT_ADMIN_USERNAME:
        set_setting(db, PASSWORD_SETTING_KEY, next_hash)
        set_setting(db, PASSWORD_CHANGE_REQUIRED_KEY, "false")
    revoke_all_teacher_sessions(db, user.id)


def require_teacher_session(
    x_coderai_teacher_token: str = Header(default="", alias="X-CoderAI-Teacher-Token"),
    db: Session = Depends(get_db),
):
    session = get_teacher_session(db, x_coderai_teacher_token)
    if not session:
        expired = get_teacher_session(db, x_coderai_teacher_token, allow_expired_access=True)
        code = "TEACHER_SESSION_EXPIRED" if expired else "TEACHER_AUTH_REQUIRED"
        message = "教师访问令牌已过期，请刷新会话或重新登录。" if expired else "请先以教师身份登录。"
        raise HTTPException(status_code=403, detail={"code": code, "message": message})
    return session


def require_teacher(
    session: TeacherSession = Depends(require_teacher_session),
    db: Session = Depends(get_db),
):
    user = session.user or db.get(User, session.user_id)
    if not user or user.role not in TEACHER_ROLES or not user.active:
        raise HTTPException(status_code=403, detail={"code": "TEACHER_AUTH_REQUIRED", "message": "教师账号不可用。"})
    if user.password_change_required:
        raise HTTPException(
            status_code=403,
            detail={"code": "TEACHER_PASSWORD_CHANGE_REQUIRED", "message": "首次登录或密码重置后必须先修改教师密码。"},
        )
    return session


def require_admin(
    session: TeacherSession = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    user = session.user or db.get(User, session.user_id)
    if not user or user.role != "admin":
        raise HTTPException(status_code=403, detail={"code": "ADMIN_REQUIRED", "message": "只有管理员可以维护教师账号。"})
    return session


def require_student_account(
    x_coderai_student_token: str = Header(default="", alias="X-CoderAI-Student-Token"),
    db: Session = Depends(get_db),
):
    student = get_student_from_token(db, x_coderai_student_token)
    if not student:
        raise HTTPException(status_code=403, detail={"code": "STUDENT_AUTH_REQUIRED", "message": "学生会话已失效，请重新登录。"})
    return student


def optional_teacher_or_student(
    x_coderai_teacher_token: str = Header(default="", alias="X-CoderAI-Teacher-Token"),
    x_coderai_student_token: str = Header(default="", alias="X-CoderAI-Student-Token"),
    db: Session = Depends(get_db),
):
    teacher_session = get_teacher_session(db, x_coderai_teacher_token)
    if teacher_session:
        teacher = teacher_session.user
        if teacher and teacher.password_change_required:
            raise HTTPException(
                status_code=403,
                detail={"code": "TEACHER_PASSWORD_CHANGE_REQUIRED", "message": "首次登录或密码重置后必须先修改教师密码。"},
            )
        return {"role": "teacher", "teacher": teacher, "student": None}
    student = get_student_from_token(db, x_coderai_student_token)
    if student:
        if student.password_change_required:
            raise HTTPException(
                status_code=403,
                detail={"code": "STUDENT_PASSWORD_CHANGE_REQUIRED", "message": "密码已被重置，请先设置新密码。"},
            )
        return {"role": "student", "teacher": None, "student": student}
    return {"role": "anonymous", "teacher": None, "student": None}


def require_student_or_teacher(identity: dict = Depends(optional_teacher_or_student)):
    if identity["role"] == "anonymous":
        raise HTTPException(status_code=403, detail={"code": "AUTH_REQUIRED", "message": "请先登录学生端或教师端。"})
    return identity
