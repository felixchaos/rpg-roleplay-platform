//! rpg-platform —— `rpg/platform_app/` 主体。
//!
//! 对应 Python: `rpg/platform_app/` (69 文件 ~13k 行,这一版只翻关键路径)。
//!
//! 子模块完成度:
//! - `auth`         **完整**(register / login / logout / user_from_token / get_user / update_profile + 速率限制 + 密码 hash)
//! - `users`        **骨架 + 主路径** (User CRUD re-export + persona list + credential set/list/resolve)
//! - `branches`     **骨架 + 关键路径**(helpers 完整 / commits hash 完整 / tree / activation / seed)
//! - `runtime`      **完整** file backend + **主路径** db backend
//! - `knowledge`    **骨架** (embedding 流水线 + Vertex client stub)
//! - `script_import`**骨架** (Job 状态机 + DB CRUD)
//! - `error`        **完整** (PlatformError + PlatformResult)
//!
//! API / 路由 / library 文件管理 / cluster / save_io 等大头模块由 rpg-routes 接管。

pub mod auth;
pub mod branches;
pub mod cluster;
pub mod crypto;
pub mod error;
pub mod knowledge;
pub mod library;
pub mod runtime;
pub mod save_io;
pub mod script_import;
pub mod tavern_cards;
pub mod usage;
pub mod user_cards;
pub mod users;

pub use error::{PlatformError, PlatformResult};

pub use library::{Script, LibraryEntry, LibraryListing};
pub use save_io::{Save, SaveExport, ImportResult};
pub use tavern_cards::{TavernCard, TavernData};
pub use user_cards::{PersonaRow, UserCardRow};
pub use usage::{UsageAggregate, UsageBreakdown, UsageByModel, UsageRecent, UsageTotals};
