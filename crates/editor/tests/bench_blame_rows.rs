//! Scratch harness, not for upstream. Timing build: the system allocator, no counting.

fn allocation_count() -> u64 {
    0
}

include!("bench_blame_rows/harness.rs");
