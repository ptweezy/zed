//! Usage: cmpbench <paths.txt> [rounds] [variant,variant,...]
//!
//! Times candidate `RelPath::cmp` implementations on a real path list.
//! Each round runs every variant once, rotating which goes first.

mod variants;

use std::{cmp::Ordering, hint::black_box, sync::Arc, time::Instant};

type Cmp = fn(&str, &str) -> Ordering;

const ALL: &[(&str, Cmp, bool)] = &[
    // name, function, orders paths correctly
    ("touch", variants::touch, false),
    ("bytes", variants::bytes, false),
    ("components", variants::components, true),
    ("base", variants::base, true),
    ("pr", variants::pr, true),
    ("pr_u64", variants::pr_u64, true),
    #[cfg(target_arch = "x86_64")]
    ("sse2", variants::sse2, true),
    ("short8", variants::short8, true),
    ("short4", variants::short4, true),
    ("ptr_eq", variants::ptr_eq, true),
    ("unroll", variants::unroll, true),
    ("bytes_ret", variants::bytes_ret, true),
    ("bytes_ret8", variants::bytes_ret8, true),
    ("mask", variants::mask, true),
    ("mask_a", variants::mask_a, true),
    ("mask_b", variants::mask_b, true),
    ("halves", variants::halves, true),
    ("xor_t", variants::xor_t, true),
    ("eq_pos", variants::eq_pos, true),
    ("mask_c", variants::mask_c, true),
    ("mask_d", variants::mask_d, true),
    ("xor_d", variants::xor_d, true),
    ("mask4", variants::mask4, true),
    ("xor4w", variants::xor4w, true),
];

struct Rng(u64);

impl Rng {
    fn next(&mut self) -> u64 {
        self.0 ^= self.0 >> 12;
        self.0 ^= self.0 << 25;
        self.0 ^= self.0 >> 27;
        self.0.wrapping_mul(0x2545_F491_4F6C_DD1D)
    }

    fn below(&mut self, bound: usize) -> usize {
        (self.next() % bound as u64) as usize
    }

    fn shuffle<T>(&mut self, items: &mut [T]) {
        for index in (1..items.len()).rev() {
            items.swap(index, self.below(index + 1));
        }
    }
}

fn parent(path: &str) -> &str {
    path.rfind('/').map_or("", |index| &path[..index])
}

struct Data<'a> {
    sorted: Vec<&'a str>,
    copies: Vec<&'a str>,
    shuffled: Vec<&'a str>,
    pairs: Vec<(u32, u32)>,
    /// Children of each directory, in shuffled order, as index ranges.
    siblings: Vec<&'a str>,
    sibling_ranges: Vec<(usize, usize)>,
}

fn neighbours(data: &Data, compare: Cmp, passes: usize) -> (f64, i64) {
    let mut sum = 0i64;
    let start = Instant::now();
    for _ in 0..passes {
        for window in data.sorted.windows(2) {
            sum += compare(black_box(window[0]), black_box(window[1])) as i64;
        }
    }
    let calls = passes * (data.sorted.len() - 1);
    (start.elapsed().as_nanos() as f64 / calls as f64, sum)
}

fn equal(data: &Data, compare: Cmp, passes: usize) -> (f64, i64) {
    let mut sum = 0i64;
    let start = Instant::now();
    for _ in 0..passes {
        for (left, right) in data.sorted.iter().zip(&data.copies) {
            sum += compare(black_box(left), black_box(right)) as i64;
        }
    }
    let calls = passes * data.sorted.len();
    (start.elapsed().as_nanos() as f64 / calls as f64, sum)
}

fn random(data: &Data, compare: Cmp, passes: usize) -> (f64, i64) {
    let mut sum = 0i64;
    let start = Instant::now();
    for _ in 0..passes {
        for &(left, right) in &data.pairs {
            sum += compare(
                black_box(data.sorted[left as usize]),
                black_box(data.sorted[right as usize]),
            ) as i64;
        }
    }
    let calls = passes * data.pairs.len();
    (start.elapsed().as_nanos() as f64 / calls as f64, sum)
}

/// Binary search for every path, in shuffled order. Nanoseconds per search.
fn search(data: &Data, compare: Cmp, _passes: usize) -> (f64, i64) {
    let mut sum = 0i64;
    let start = Instant::now();
    for target in &data.shuffled {
        let found = data
            .sorted
            .binary_search_by(|probe| compare(black_box(probe), black_box(target)));
        sum += found.unwrap_or(usize::MAX) as i64;
    }
    (
        start.elapsed().as_nanos() as f64 / data.shuffled.len() as f64,
        sum,
    )
}

/// Sort the whole shuffled list. Milliseconds.
fn sort_all(data: &Data, compare: Cmp, _passes: usize) -> (f64, i64) {
    let mut paths = data.shuffled.clone();
    let start = Instant::now();
    paths.sort_by(|left, right| compare(left, right));
    let elapsed = start.elapsed().as_nanos() as f64 / 1e6;
    (elapsed, paths[paths.len() / 2].len() as i64)
}

/// Sort each directory's children, as `SumTree::edit` does for one
/// `populate_dir`. Nanoseconds per path.
fn sort_siblings(data: &Data, compare: Cmp, _passes: usize) -> (f64, i64) {
    let mut paths = data.siblings.clone();
    let start = Instant::now();
    for &(from, to) in &data.sibling_ranges {
        paths[from..to].sort_unstable_by(|left, right| compare(left, right));
    }
    let elapsed = start.elapsed().as_nanos() as f64 / paths.len() as f64;
    (elapsed, paths[paths.len() / 2].len() as i64)
}

type Workload = fn(&Data, Cmp, usize) -> (f64, i64);

const WORKLOADS: &[(&str, &str, Workload)] = &[
    ("neighbours", "ns/cmp", neighbours),
    ("equal", "ns/cmp", equal),
    ("random", "ns/cmp", random),
    ("search", "ns/search", search),
    ("sort_all", "ms", sort_all),
    ("sort_siblings", "ns/path", sort_siblings),
];

fn median(values: &mut [f64]) -> f64 {
    values.sort_by(|a, b| a.partial_cmp(b).unwrap());
    values[values.len() / 2]
}

fn replay_main(arguments: &[String]) {
    let file = &arguments[2];
    let rounds: usize = arguments.get(3).map(|r| r.parse().expect("rounds")).unwrap_or(21);
    let selected: Vec<(&str, Cmp, bool)> = select(arguments.get(4));
    let text = std::fs::read_to_string(file).expect("read pairs");
    let mut lines = text.lines();
    let count: usize = lines.next().expect("count").parse().expect("count");
    // One allocation per path, in the order the scan first compared them.
    let owned: Vec<Arc<str>> = lines.by_ref().take(count).map(Arc::from).collect();
    let pairs: Vec<(u32, u32)> = lines
        .map(|line| {
            let (left, right) = line.split_once(' ').expect("pair");
            (left.parse().expect("left"), right.parse().expect("right"))
        })
        .collect();
    drop(text);
    let paths: Vec<&str> = owned.iter().map(|path| &**path).collect();
    for (name, compare, orders_paths) in &selected {
        if !orders_paths {
            continue;
        }
        for &(left, right) in &pairs {
            let (left, right) = (paths[left as usize], paths[right as usize]);
            assert_eq!(compare(left, right), variants::components(left, right), "{name}: {left:?} vs {right:?}");
        }
    }
    println!("# {file}: {} pairs, {} paths, {rounds} rounds", pairs.len(), paths.len());
    let mut results = vec![Vec::with_capacity(rounds); selected.len()];
    let mut checksum = 0i64;
    for round in 0..rounds {
        for step in 0..selected.len() {
            let variant_index = (round + step) % selected.len();
            let compare = selected[variant_index].1;
            let mut sum = 0i64;
            let start = Instant::now();
            for &(left, right) in &pairs {
                sum += compare(black_box(paths[left as usize]), black_box(paths[right as usize])) as i64;
            }
            let elapsed = start.elapsed().as_nanos() as f64 / pairs.len() as f64;
            checksum = checksum.wrapping_add(black_box(sum));
            results[variant_index].push(elapsed);
        }
    }
    for (variant_index, (name, _, _)) in selected.iter().enumerate() {
        let values = &mut results[variant_index];
        let first = values[0];
        let median = median(values);
        println!("{name:<12} replay {median:.3} ns/cmp (min {:.3}, first {first:.3})", values[0]);
        println!("@\t{name}\treplay\t{median}");
    }
    eprintln!("checksum {checksum}");
}

fn select(names: Option<&String>) -> Vec<(&'static str, Cmp, bool)> {
    match names {
        Some(names) => names
            .split(',')
            .map(|name| {
                *ALL.iter()
                    .find(|(candidate, _, _)| *candidate == name)
                    .unwrap_or_else(|| panic!("unknown variant {name}"))
            })
            .collect(),
        None => ALL.to_vec(),
    }
}

fn main() {
    let arguments: Vec<String> = std::env::args().collect();
    if arguments.get(1).map(String::as_str) == Some("replay") {
        return replay_main(&arguments);
    }
    let Some(file) = arguments.get(1) else {
        eprintln!("usage: cmpbench <paths.txt> [rounds] [variant,variant,...]");
        std::process::exit(2);
    };
    let rounds: usize = arguments
        .get(2)
        .map(|rounds| rounds.parse().expect("rounds"))
        .unwrap_or(31);
    let selected: Vec<(&str, Cmp, bool)> = match arguments.get(3) {
        Some(names) => names
            .split(',')
            .map(|name| {
                *ALL.iter()
                    .find(|(candidate, _, _)| *candidate == name)
                    .unwrap_or_else(|| panic!("unknown variant {name}"))
            })
            .collect(),
        None => ALL.to_vec(),
    };

    let text = std::fs::read_to_string(file).expect("read path list");
    // One allocation per path, in list order, like the scanner's `Arc<RelPath>`s.
    let owned: Vec<Arc<str>> = text
        .lines()
        .filter(|line| !line.is_empty())
        .map(Arc::from)
        .collect();
    let mut sorted: Vec<&str> = owned.iter().map(|path| &**path).collect();
    sorted.sort_by(|left, right| variants::components(left, right));
    sorted.dedup();
    let copies_owned: Vec<Arc<str>> = sorted.iter().map(|path| Arc::from(*path)).collect();
    let copies: Vec<&str> = copies_owned.iter().map(|path| &**path).collect();

    let mut rng = Rng(0x9E37_79B9_7F4A_7C15);
    let mut shuffled = sorted.clone();
    rng.shuffle(&mut shuffled);
    let pairs: Vec<(u32, u32)> = (0..sorted.len())
        .map(|_| {
            (
                rng.below(sorted.len()) as u32,
                rng.below(sorted.len()) as u32,
            )
        })
        .collect();

    let mut siblings = sorted.clone();
    siblings.sort_by(|left, right| {
        variants::components(parent(left), parent(right)).then_with(|| left.cmp(right))
    });
    let mut sibling_ranges = Vec::new();
    let mut from = 0;
    for index in 1..=siblings.len() {
        if index == siblings.len() || parent(siblings[index]) != parent(siblings[from]) {
            sibling_ranges.push((from, index));
            from = index;
        }
    }
    for &(from, to) in &sibling_ranges {
        rng.shuffle(&mut siblings[from..to]);
    }

    let data = Data {
        sorted,
        copies,
        shuffled,
        pairs,
        siblings,
        sibling_ranges,
    };

    // Every ordering variant must agree with the component order.
    for (name, compare, orders_paths) in &selected {
        if !orders_paths {
            continue;
        }
        let check = |left: &str, right: &str| {
            assert_eq!(
                compare(left, right),
                variants::components(left, right),
                "{name}: {left:?} vs {right:?}"
            );
        };
        for window in data.sorted.windows(2) {
            check(window[0], window[1]);
            check(window[1], window[0]);
        }
        for (left, right) in data.sorted.iter().zip(&data.copies) {
            check(left, right);
        }
        for &(left, right) in &data.pairs {
            check(data.sorted[left as usize], data.sorted[right as usize]);
        }
        for path in &data.sorted {
            check(path, parent(path));
            check(parent(path), path);
        }
    }

    let passes = (2_000_000 / data.sorted.len()).max(1);
    let total_bytes: usize = data.sorted.iter().map(|path| path.len()).sum();
    println!(
        "# {file}: {} paths, mean {:.1} bytes, {} directories, {rounds} rounds, {passes} passes",
        data.sorted.len(),
        total_bytes as f64 / data.sorted.len() as f64,
        data.sibling_ranges.len(),
    );

    let mut checksum = 0i64;
    let mut results = vec![vec![Vec::with_capacity(rounds); selected.len()]; WORKLOADS.len()];
    for round in 0..rounds {
        for (workload_index, (_, _, workload)) in WORKLOADS.iter().enumerate() {
            for step in 0..selected.len() {
                let variant_index = (round + step) % selected.len();
                let (value, sum) = workload(&data, selected[variant_index].1, passes);
                checksum = checksum.wrapping_add(black_box(sum));
                results[workload_index][variant_index].push(value);
            }
        }
    }

    print!("{:<12}", "variant");
    for (name, unit, _) in WORKLOADS {
        print!(" {:>24}", format!("{name} ({unit})"));
    }
    println!();
    let mut tsv = Vec::new();
    for (variant_index, (name, _, _)) in selected.iter().enumerate() {
        print!("{name:<12}");
        for workload_results in &mut results {
            let values = &mut workload_results[variant_index];
            let median = median(values);
            let minimum = values[0];
            print!(" {:>24}", format!("{median:.2} (min {minimum:.2})"));
            tsv.push(format!("@\t{name}\t{}\t{median}", WORKLOADS[tsv.len() % WORKLOADS.len()].0));
        }
        println!();
    }
    for line in tsv {
        println!("{line}");
    }
    eprintln!("checksum {checksum}");
}
