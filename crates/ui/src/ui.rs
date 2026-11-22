//! # UI – Zed UI Primitives & Components
//!
//! This crate provides a set of UI primitives and components that are used to build all of the elements in Zed's UI.
//!
//! ## Related Crates:
//!
//! - [`ui_macros`] - proc_macros support for this crate
//! - `ui_input` - the single line input component

pub mod component_prelude;
mod components;
pub mod prelude;
mod styles;
mod traits;
pub mod utils;

pub use components::*;
pub use prelude::*;
pub use styles::*;
pub use traits::animation_ext::*;

#[doc(hidden)]
pub fn bench_uncapped() -> bool {
    static UNCAPPED: std::sync::LazyLock<bool> =
        std::sync::LazyLock::new(|| std::env::var_os("ZED_BENCH_UNCAPPED").is_some());
    *UNCAPPED
}
