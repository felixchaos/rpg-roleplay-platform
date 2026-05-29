//! auth —— 用户/会话/速率限制/密码
//!
//! 对应 Python: `rpg/platform_app/auth.py` (209 行) + `rpg/platform_app/security.py`。
//!
//! 完成度: **完整**。所有公开函数已翻译,只有 login_audit 表的 DDL 改成
//! 在 migration 中预建(rpg-db 接管)而非 inline create。

pub mod password;
pub mod rate_limit;
pub mod sessions;

pub use password::{hash_password, normalize_username, public_user, verify_password, PublicUser};
pub use rate_limit::{admin_unlock, RateLimitConfig, RateLimiter, RateLimited};
pub use sessions::{
    get_user, login, logout, register, update_profile, user_from_token, AuthService, User,
    SESSION_DAYS,
};
