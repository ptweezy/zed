//! Usage: bench_edit [repetitions]
//!
//! Times keyed inserts into a `SumTree`: `edit` with batches that only insert,
//! and `insert_or_replace`. Items have a small `Copy` key, which is the
//! cheapest case for slicing the tree. Prints each workload's median
//! nanoseconds per inserted item.
//!
//! The workflow copies this file into `crates/sum_tree/examples` and builds
//! it once per `sum_tree.rs` variant.

use std::{hint::black_box, time::Instant};
use sum_tree::{ContextLessSummary, Dimension, Edit, Item, KeyedItem, SumTree};

#[derive(Clone, Debug)]
struct Entry {
    key: u64,
    #[expect(dead_code, reason = "pads the item to the size of a small entry")]
    payload: [u64; 4],
}

#[derive(Clone, Debug, Default)]
struct EntrySummary {
    max_key: u64,
    count: usize,
}

impl Item for Entry {
    type Summary = EntrySummary;

    fn summary(&self, _: ()) -> EntrySummary {
        EntrySummary {
            max_key: self.key,
            count: 1,
        }
    }
}

impl KeyedItem for Entry {
    type Key = u64;

    fn key(&self) -> u64 {
        self.key
    }
}

impl ContextLessSummary for EntrySummary {
    fn zero() -> Self {
        Self::default()
    }

    fn add_summary(&mut self, other: &Self) {
        self.max_key = other.max_key;
        self.count += other.count;
    }
}

impl<'a> Dimension<'a, EntrySummary> for u64 {
    fn zero(_: ()) -> Self {
        0
    }

    fn add_summary(&mut self, summary: &'a EntrySummary, _: ()) {
        *self = summary.max_key;
    }
}

struct Random(u64);

impl Random {
    fn below(&mut self, bound: u64) -> u64 {
        self.0 ^= self.0 >> 12;
        self.0 ^= self.0 << 25;
        self.0 ^= self.0 >> 27;
        self.0.wrapping_mul(0x2545_F491_4F6C_DD1D) % bound
    }
}

fn entry(key: u64) -> Entry {
    Entry {
        key,
        payload: [key; 4],
    }
}

/// A tree of `count` items whose keys leave room for 999 more after each.
fn tree(count: u64) -> SumTree<Entry> {
    SumTree::from_iter((0..count).map(|index| entry(index * 1000)), ())
}

/// Applies `batches` to the tree and returns nanoseconds per inserted item.
/// With `shared`, another tree shares every node during each edit.
fn time(mut tree: SumTree<Entry>, batches: Vec<Vec<u64>>, shared: bool) -> f64 {
    time_with(&mut tree, batches, shared, false)
}

/// Like `time`, but can insert single items with `insert_or_replace`.
fn time_with(
    tree: &mut SumTree<Entry>,
    batches: Vec<Vec<u64>>,
    shared: bool,
    insert_or_replace: bool,
) -> f64 {
    let items: usize = batches.iter().map(Vec::len).sum();
    let batches: Vec<Vec<Edit<Entry>>> = batches
        .into_iter()
        .map(|batch| {
            batch
                .into_iter()
                .map(|key| Edit::Insert(entry(key)))
                .collect()
        })
        .collect();
    let start = Instant::now();
    for mut batch in batches {
        let snapshot = shared.then(|| tree.clone());
        match batch.pop() {
            Some(Edit::Insert(item)) if insert_or_replace && batch.is_empty() => {
                black_box(tree.insert_or_replace(item, ()));
            }
            last => {
                batch.extend(last);
                black_box(tree.edit(batch, ()));
            }
        }
        drop(snapshot);
    }
    let elapsed = start.elapsed();
    black_box(tree.summary().count);
    elapsed.as_nanos() as f64 / items as f64
}

fn main() {
    let repetitions: usize = std::env::args()
        .nth(1)
        .map_or(5, |argument| argument.parse().expect("repetitions"));
    let mut random = Random(0x9E37_79B9_7F4A_7C15);
    let mut runs = |tree_size: u64, batch_count: u64, batch_size: u64| -> Vec<Vec<u64>> {
        (0..batch_count)
            .map(|batch| {
                // Distinct bases keep every key new: each batch has its own offset range.
                let base = random.below(tree_size) * 1000 + 1 + (batch % 40) * 20;
                (0..batch_size).map(|index| base + index).collect()
            })
            .collect()
    };

    type Workload = (&'static str, Box<dyn FnMut() -> f64>);
    let mut workloads: Vec<Workload> = Vec::new();
    for shared in [false, true] {
        let suffix = if shared { ", shared" } else { "" };
        let scan = runs(120_000, 6_500, 19);
        workloads.push((
            Box::leak(format!("runs of 19 into 120k{suffix}").into_boxed_str()),
            Box::new(move || time(tree(120_000), scan.clone(), shared)),
        ));
        for tree_size in [1_000, 100_000, 1_000_000] {
            let singles = runs(tree_size, 100_000, 1);
            workloads.push((
                Box::leak(format!("one item into {tree_size}{suffix}").into_boxed_str()),
                Box::new(move || time(tree(tree_size), singles.clone(), shared)),
            ));
        }
        for tree_size in [1_000, 1_000_000] {
            let singles = runs(tree_size, 100_000, 1);
            workloads.push((
                Box::leak(format!("insert_or_replace into {tree_size}{suffix}").into_boxed_str()),
                Box::new(move || time_with(&mut tree(tree_size), singles.clone(), shared, true)),
            ));
        }
        let appends: Vec<Vec<u64>> = (0..200_000).map(|key| vec![key]).collect();
        workloads.push((
            Box::leak(format!("append one item, 200k times{suffix}").into_boxed_str()),
            Box::new(move || time(SumTree::default(), appends.clone(), shared)),
        ));
        let replacements: Vec<Vec<u64>> = runs(120_000, 6_500, 1)
            .into_iter()
            .map(|batch| {
                let first = batch[0] / 1000;
                (first..(first + 19).min(120_000))
                    .map(|index| index * 1000)
                    .collect()
            })
            .collect();
        workloads.push((
            Box::leak(format!("replace 19 in 120k{suffix}").into_boxed_str()),
            Box::new(move || time(tree(120_000), replacements.clone(), shared)),
        ));
    }
    let bulk: Vec<Vec<u64>> = vec![(0..1_000_000).collect()];
    workloads.push((
        "one batch of 1M into an empty tree",
        Box::new(move || time(SumTree::default(), bulk.clone(), false)),
    ));
    let mut scattered: Vec<u64> = runs(1_000_000, 100_000, 1).into_iter().flatten().collect();
    scattered.sort_unstable();
    scattered.dedup();
    workloads.push((
        "one batch of 100k scattered into 1M",
        Box::new(move || time(tree(1_000_000), vec![scattered.clone()], false)),
    ));

    let mut samples = vec![Vec::new(); workloads.len()];
    for _ in 0..repetitions {
        for ((_, workload), values) in workloads.iter_mut().zip(&mut samples) {
            values.push(workload());
        }
    }
    for ((name, _), values) in workloads.iter().zip(&mut samples) {
        values.sort_by(f64::total_cmp);
        println!("{name}\t{:.1}", values[values.len() / 2]);
    }
}
