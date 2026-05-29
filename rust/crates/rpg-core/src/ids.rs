//! ids.rs — 强类型 id newtype,防止 `user_id` / `save_id` / `run_id` 等
//! 裸 `i64` 在函数边界互相串用(典型 bug:把 save_id 当 user_id 传进去,
//! 编译器无法拦截)。
//!
//! 设计要点:
//! - `#[serde(transparent)]` + `#[sqlx(transparent)]`:线上(JSON)与库里(Postgres
//!   `bigint`/`i8`)的表示与裸 `i64` 完全一致,**零迁移成本**。前端 / DB schema 不变。
//! - `#[derive(sqlx::Type)] #[sqlx(transparent)]` 自动给出 `Type/Encode/Decode`,
//!   所以可以直接 `query.bind(user_id)` 以及 `row.try_get::<UserId,_>("id")`。
//! - `Copy`:id 是廉价标量,按值传递,不用到处 `&`。
//! - `From<i64>` / `Into<i64>` + `Display`:边界处仍可 `.0` 或 `.into()` 与裸 i64
//!   互转,务实地让"暂留 i64"的 crate 在接缝处低成本桥接。

use serde::{Deserialize, Serialize};

/// 生成一个透明包装 `i64` 的强类型 id newtype,统一实现:
/// `Serialize/Deserialize(transparent)`、`sqlx::Type(transparent, Postgres)`、
/// `From<i64>` / `From<Self> for i64`、`Display`、`From<&Self> for i64`。
macro_rules! define_id {
    ($(#[$meta:meta])* $name:ident) => {
        $(#[$meta])*
        #[derive(
            Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord,
            Serialize, Deserialize, sqlx::Type,
        )]
        #[serde(transparent)]
        #[sqlx(transparent)]
        pub struct $name(pub i64);

        impl $name {
            /// 取出底层 `i64`(等价 `self.0`,可读性更好)。
            #[inline]
            pub const fn get(self) -> i64 {
                self.0
            }
        }

        impl From<i64> for $name {
            #[inline]
            fn from(v: i64) -> Self {
                $name(v)
            }
        }

        impl From<$name> for i64 {
            #[inline]
            fn from(v: $name) -> i64 {
                v.0
            }
        }

        impl From<&$name> for i64 {
            #[inline]
            fn from(v: &$name) -> i64 {
                v.0
            }
        }

        impl std::fmt::Display for $name {
            #[inline]
            fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
                std::fmt::Display::fmt(&self.0, f)
            }
        }
    };
}

define_id! {
    /// 用户主键(`users.id`,Postgres `bigint`)。
    ///
    /// 注意:匿名访问路径(`user_id_or_anon` → `"anonymous"`)用 `String` key,
    /// 不属于 `UserId` 域;只有"已登录、确有 DB 行"的 id 才是 `UserId`。
    UserId
}

define_id! {
    /// 存档主键(`saves.id`)。
    SaveId
}

define_id! {
    /// 单次运行 / run 计数主键(`run_id`)。
    RunId
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roundtrip_i64_conversions() {
        let u = UserId::from(42);
        assert_eq!(u.0, 42);
        assert_eq!(u.get(), 42);
        let back: i64 = u.into();
        assert_eq!(back, 42);
        let back_ref: i64 = (&u).into();
        assert_eq!(back_ref, 42);
    }

    #[test]
    fn display_matches_inner() {
        assert_eq!(UserId(7).to_string(), "7");
        assert_eq!(SaveId(-1).to_string(), "-1");
        assert_eq!(RunId(0).to_string(), "0");
    }

    #[test]
    fn serde_is_transparent() {
        // transparent:序列化成裸数字,反序列化也吃裸数字(与 i64 线上表示一致)。
        let u = UserId(123);
        let json = serde_json::to_string(&u).unwrap();
        assert_eq!(json, "123");
        let parsed: UserId = serde_json::from_str("123").unwrap();
        assert_eq!(parsed, u);
    }

    #[test]
    fn distinct_types_dont_mix() {
        // 编译期保证:UserId 与 SaveId 是不同类型,不能直接赋值/比较。
        // (此处仅作 sanity，真正的价值是误用代码无法通过编译。)
        let u = UserId(1);
        let s = SaveId(1);
        assert_eq!(i64::from(u), i64::from(s));
    }
}
