//! Usage: bench_edit_empty list
//!        bench_edit_empty <workload>
//!
//! Times `SumTree::edit` with batches that only insert distinct keys, into an
//! empty tree or a tree of one item. Runs one workload per process, because
//! one workload's allocations change the next one's time.
//!
//! The 1M batches run twice: once per process, where every allocation gets
//! memory the process has not touched, and nine times per process, where
//! later edits reuse the memory earlier ones freed. The small edits also run
//! twice: with each tree dropped before the next edit, and with every tree
//! kept until the measurement ends.
//!
//! Prints the median over the workload's repetitions: nanoseconds per item
//! for the 1M batches, nanoseconds per edit for the small ones.
//!
//! The workflow copies this file into `crates/sum_tree/examples` and builds
//! it once per `sum_tree.rs` variant.

use std::{hint::black_box, sync::Arc, time::Instant};
use sum_tree::{ContextLessSummary, Dimension, Edit, Item, KeyedItem, SumTree};

const BULK: u64 = 1_000_000;
const SMALL_SIZES: [u64; 5] = [1, 4, 12, 13, 48];

macro_rules! benchmark {
    ($module:ident, $key:ty, $zero:expr, $make_key:expr) => {
        mod $module {
            use super::*;

            #[derive(Clone, Debug)]
            pub struct Entry {
                key: $key,
                #[expect(dead_code, reason = "pads the item to the size of a small entry")]
                payload: [u64; 4],
            }

            #[derive(Clone, Debug)]
            pub struct EntrySummary {
                max_key: $key,
                count: usize,
            }

            impl Item for Entry {
                type Summary = EntrySummary;

                fn summary(&self, _: ()) -> EntrySummary {
                    EntrySummary {
                        max_key: self.key.clone(),
                        count: 1,
                    }
                }
            }

            impl KeyedItem for Entry {
                type Key = $key;

                fn key(&self) -> $key {
                    self.key.clone()
                }
            }

            impl ContextLessSummary for EntrySummary {
                fn zero() -> Self {
                    EntrySummary {
                        max_key: $zero,
                        count: 0,
                    }
                }

                fn add_summary(&mut self, other: &Self) {
                    self.max_key = other.max_key.clone();
                    self.count += other.count;
                }
            }

            impl<'a> Dimension<'a, EntrySummary> for $key {
                fn zero(_: ()) -> Self {
                    $zero
                }

                fn add_summary(&mut self, summary: &'a EntrySummary, _: ()) {
                    *self = summary.max_key.clone();
                }
            }

            fn entry(index: u64) -> Entry {
                let make_key: fn(u64) -> $key = $make_key;
                Entry {
                    key: make_key(index),
                    payload: [index; 4],
                }
            }

            /// One edit of `BULK` items into a tree of `initial` items. Nanoseconds per item.
            pub fn bulk(initial: u64) -> f64 {
                let mut tree = SumTree::from_iter((0..initial).map(entry), ());
                let edits: Vec<Edit<Entry>> = (initial..initial + BULK)
                    .map(|index| Edit::Insert(entry(index)))
                    .collect();
                let start = Instant::now();
                black_box(tree.edit(edits, ()));
                let elapsed = start.elapsed();
                assert_eq!(tree.summary().count as u64, initial + BULK);
                elapsed.as_nanos() as f64 / BULK as f64
            }

            /// Edits of `size` items, each into an empty tree. Nanoseconds per edit. With
            /// `keep`, every tree outlives the measurement, so no edit reuses the memory
            /// that an earlier tree freed.
            pub fn small(size: u64, keep: bool) -> f64 {
                let count = (BULK / size).min(200_000);
                let batches: Vec<Vec<Edit<Entry>>> = (0..count)
                    .map(|_| (0..size).map(|index| Edit::Insert(entry(index))).collect())
                    .collect();
                let mut kept = Vec::with_capacity(if keep { batches.len() } else { 0 });
                let mut items = 0;
                let start = Instant::now();
                for batch in batches {
                    let mut tree = SumTree::default();
                    black_box(tree.edit(batch, ()));
                    items += tree.summary().count as u64;
                    if keep {
                        kept.push(tree);
                    }
                }
                let elapsed = start.elapsed();
                drop(kept);
                assert_eq!(items, count * size);
                elapsed.as_nanos() as f64 / count as f64
            }
        }
    };
}

benchmark!(integer, u64, 0, |index| index);
benchmark!(string, Arc<str>, Arc::from(""), |index| {
    format!("crates/c{:04}/src/f{:08}.rs", index / 1000, index).into()
});

/// A name, the repetitions per process, and the measurement.
type Workload = (String, usize, Box<dyn Fn() -> f64>);

fn workloads() -> Vec<Workload> {
    let mut workloads: Vec<Workload> = Vec::new();
    type Functions = (&'static str, fn(u64) -> f64, fn(u64, bool) -> f64);
    let key_types: [Functions; 2] = [
        ("u64", integer::bulk, integer::small),
        ("Arc<str>", string::bulk, string::small),
    ];
    for (keys, bulk, small) in key_types {
        for (initial, tree) in [(0, "an empty tree"), (1, "a tree of 1 item")] {
            for (repetitions, edits) in [(1, "first edit"), (9, "median of 9 edits")] {
                workloads.push((
                    format!("1M {keys} keys into {tree}, {edits}, per item"),
                    repetitions,
                    Box::new(move || bulk(initial)),
                ));
            }
        }
        for (keep, fate) in [(false, "dropped"), (true, "kept")] {
            for size in SMALL_SIZES {
                workloads.push((
                    format!("{size} {keys} keys into an empty tree, tree {fate}, per edit"),
                    3,
                    Box::new(move || small(size, keep)),
                ));
            }
        }
    }
    workloads
}

fn main() {
    let name = std::env::args().nth(1).expect("a workload name, or `list`");
    let workloads = workloads();
    if name == "list" {
        for (name, _, _) in &workloads {
            println!("{name}");
        }
        return;
    }
    let (_, repetitions, workload) = workloads
        .iter()
        .find(|(candidate, _, _)| *candidate == name)
        .expect("a known workload");
    let mut samples: Vec<f64> = (0..*repetitions).map(|_| workload()).collect();
    samples.sort_by(f64::total_cmp);
    println!("{:.2}", samples[samples.len() / 2]);
}
