//! Usage: bench_rel_path_cmp <paths.txt> <repetitions> <scan.pairs>
//!
//! Times `RelPath::cmp` on a list of paths, one per line, after checking it
//! against a component-wise comparison. Prints each workload's median and
//! minimum over the repetitions. `<scan.pairs>` holds the comparisons a scan
//! made: a path count, the paths, then one pair of path indices per line.
//!
//! The workflow copies this file into `crates/path/examples`.

use anyhow::{Context as _, Result, bail, ensure};
use path::rel_path::RelPath;
use std::{cmp::Ordering, hint::black_box, sync::Arc, time::Instant};

struct Random(u64);

impl Random {
    fn below(&mut self, bound: usize) -> usize {
        self.0 ^= self.0 >> 12;
        self.0 ^= self.0 << 25;
        self.0 ^= self.0 >> 27;
        (self.0.wrapping_mul(0x2545_F491_4F6C_DD1D) % bound as u64) as usize
    }

    fn shuffle<T>(&mut self, items: &mut [T]) {
        for index in (1..items.len()).rev() {
            items.swap(index, self.below(index + 1));
        }
    }
}

struct Data {
    sorted: Vec<Arc<RelPath>>,
    copies: Vec<Arc<RelPath>>,
    shuffled: Vec<Arc<RelPath>>,
    pairs: Vec<(Arc<RelPath>, Arc<RelPath>)>,
    passes: usize,
    scan_paths: Vec<Arc<RelPath>>,
    scan_pairs: Vec<(u32, u32)>,
}

fn read_scan_pairs(file: &str) -> Result<(Vec<Arc<RelPath>>, Vec<(u32, u32)>)> {
    let text = std::fs::read_to_string(file).with_context(|| format!("reading {file}"))?;
    let mut lines = text.lines();
    let count: usize = lines.next().context("empty pairs file")?.parse()?;
    let paths = lines
        .by_ref()
        .take(count)
        .map(|line| Ok(RelPath::from_unix_str(line)?.into_arc()))
        .collect::<Result<Vec<_>>>()?;
    let pairs = lines
        .map(|line| {
            let (left, right) = line.split_once(' ').context("malformed pair")?;
            let (left, right): (u32, u32) = (left.parse()?, right.parse()?);
            ensure!((left as usize) < count && (right as usize) < count, "index out of range");
            Ok((left, right))
        })
        .collect::<Result<Vec<_>>>()?;
    Ok((paths, pairs))
}

fn scan_pairs(data: &Data) -> impl Iterator<Item = (&Arc<RelPath>, &Arc<RelPath>)> + Clone {
    data.scan_pairs
        .iter()
        .map(|&(left, right)| (&data.scan_paths[left as usize], &data.scan_paths[right as usize]))
}

fn by_component(left: &RelPath, right: &RelPath) -> Ordering {
    left.components().cmp(right.components())
}

fn verify(data: &Data) -> Result<()> {
    let check = |left: &RelPath, right: &RelPath| {
        ensure!(
            left.cmp(right) == by_component(left, right)
                && right.cmp(left) == by_component(right, left),
            "wrong order for {left:?} and {right:?}"
        );
        Ok(())
    };
    for (left, right) in data.sorted.iter().zip(data.sorted.iter().skip(1)) {
        check(left, right)?;
    }
    for (path, copy) in data.sorted.iter().zip(&data.copies) {
        check(path, copy)?;
    }
    for (left, right) in &data.pairs {
        check(left, right)?;
    }
    for (left, right) in scan_pairs(data) {
        check(left, right)?;
    }
    for (index, path) in data.sorted.iter().enumerate() {
        if let Some(parent) = path.parent() {
            check(path, parent)?;
        }
        let found = data.sorted.binary_search(path);
        ensure!(found == Ok(index), "search for {path:?} returned {found:?}");
    }
    Ok(())
}

fn nanoseconds_per_comparison<'a>(
    passes: usize,
    pairs: impl Iterator<Item = (&'a Arc<RelPath>, &'a Arc<RelPath>)> + Clone,
) -> f64 {
    let (mut sum, mut comparisons) = (0i64, 0usize);
    let start = Instant::now();
    for _ in 0..passes {
        for (left, right) in pairs.clone() {
            sum += black_box(&**left).cmp(black_box(&**right)) as i64;
            comparisons += 1;
        }
    }
    let elapsed = start.elapsed();
    black_box(sum);
    elapsed.as_nanos() as f64 / comparisons as f64
}

fn nanoseconds_per_search(data: &Data) -> f64 {
    let mut sum = 0usize;
    let start = Instant::now();
    for target in &data.shuffled {
        let target = black_box(&**target);
        let found = data.sorted.binary_search_by(|probe| (**probe).cmp(target));
        sum = sum.wrapping_add(found.unwrap_or(usize::MAX));
    }
    let elapsed = start.elapsed();
    black_box(sum);
    elapsed.as_nanos() as f64 / data.shuffled.len() as f64
}

fn milliseconds_to_sort(data: &Data) -> Result<f64> {
    let mut paths = data.shuffled.clone();
    let start = Instant::now();
    paths.sort();
    let elapsed = start.elapsed();
    ensure!(paths == data.sorted, "sorting gave a different order");
    Ok(elapsed.as_secs_f64() * 1e3)
}

const WORKLOADS: &[(&str, &str, fn(&Data) -> Result<f64>)] = &[
    ("replay", "ns", |data| {
        let passes = (2_000_000 / data.scan_pairs.len()).max(1);
        Ok(nanoseconds_per_comparison(passes, scan_pairs(data)))
    }),
    ("neighbours", "ns", |data| {
        let pairs = data.sorted.iter().zip(data.sorted.iter().skip(1));
        Ok(nanoseconds_per_comparison(data.passes, pairs))
    }),
    ("equal", "ns", |data| {
        let pairs = data.sorted.iter().zip(&data.copies);
        Ok(nanoseconds_per_comparison(data.passes, pairs))
    }),
    ("random", "ns", |data| {
        let pairs = data.pairs.iter().map(|(left, right)| (left, right));
        Ok(nanoseconds_per_comparison(data.passes, pairs))
    }),
    ("search", "ns", |data| Ok(nanoseconds_per_search(data))),
    ("sort", "ms", milliseconds_to_sort),
];

fn main() -> Result<()> {
    let mut arguments = std::env::args().skip(1);
    let (Some(list), Some(repetitions), Some(scan)) =
        (arguments.next(), arguments.next(), arguments.next())
    else {
        bail!("usage: bench_rel_path_cmp <paths.txt> <repetitions> <scan.pairs>");
    };
    let repetitions: usize = repetitions.parse().context("parsing repetitions")?;
    ensure!(repetitions > 0, "repetitions must be positive");
    let bytes = std::fs::read(&list).with_context(|| format!("reading {list}"))?;
    let text = String::from_utf8_lossy(&bytes);
    let lines = text.lines().filter(|line| !line.is_empty());

    // One allocation per path, in list order, like the scanner's entries.
    let paths: Vec<Arc<RelPath>> = lines
        .clone()
        .filter_map(|line| RelPath::from_unix_str(line).ok())
        .map(RelPath::into_arc)
        .collect();
    let skipped = lines.count() - paths.len();
    let mut sorted = paths.clone();
    sorted.sort_by(|left, right| by_component(left, right));
    sorted.dedup();
    ensure!(sorted.len() > 1, "{list} has fewer than two paths");

    let mut random = Random(0x9E37_79B9_7F4A_7C15);
    let copies = sorted.iter().map(|path| path.into_arc()).collect();
    let mut shuffled = sorted.clone();
    random.shuffle(&mut shuffled);
    let mut pick = || sorted[random.below(sorted.len())].clone();
    let pairs = (0..sorted.len()).map(|_| (pick(), pick())).collect();
    let passes = (2_000_000 / sorted.len()).max(1);
    let total_bytes: usize = sorted.iter().map(|path| path.as_unix_str().len()).sum();
    println!(
        "paths\t{}\t{:.1}\t{skipped}",
        sorted.len(),
        total_bytes as f64 / sorted.len() as f64
    );
    // Allocated after the list's paths, in the order the scan first compared them.
    let (scan_paths, scan_pairs) = read_scan_pairs(&scan)?;
    println!("scan\t{}\t{}", scan_pairs.len(), scan_paths.len());
    let data = Data {
        sorted,
        copies,
        shuffled,
        pairs,
        passes,
        scan_paths,
        scan_pairs,
    };
    verify(&data)?;

    let mut samples = vec![Vec::with_capacity(repetitions); WORKLOADS.len()];
    for _ in 0..repetitions {
        for ((_, _, workload), values) in WORKLOADS.iter().zip(&mut samples) {
            values.push(workload(&data)?);
        }
    }
    for ((name, unit, _), values) in WORKLOADS.iter().zip(&mut samples) {
        values.sort_by(f64::total_cmp);
        let median = values[values.len() / 2];
        println!("{name}\t{unit}\t{median:.4}\t{:.4}", values[0]);
    }
    Ok(())
}
