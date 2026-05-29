//! 对应 Python: rpg/core/security.py
//!
//! Python security.py 只是 re-export platform_app.auth 的符号,无纯函数逻辑。
//! Rust 等价实现将在 rpg-platform crate 中完成。
//!
//! TODO: 待 rpg-platform 实现后,在此 re-export:
//!   - RateLimited error variant
//!   - admin_unlock()
//!   - register() / login() / logout()
//!   - user_from_token()
//!   - get_user()
//!   - update_profile()
