// Scratch harness, not for upstream. Included by bench_blame_rows.rs and bench_blame_rows_allocs.rs,
// which each define `allocation_count()`.
//
// Times `GitBlame::blame_for_rows` for one screen of rows, called the way
// `EditorElement::layout_blame_entries` calls it, on the blame of a real file.
//
// BENCH_FIXTURE: a JSON file written by `dump_blame_fixtures`. BENCH_ROWS: rows on screen.
// BENCH_FRAMES / BENCH_WARMUP_FRAMES: calls to time and to discard. The view moves down one row
// per call and wraps at the end of the file.

use std::{
    path::{Path, PathBuf},
    time::Instant,
};

use editor::GitBlame;
use git::{
    blame::{Blame, BlameEntry},
    repository::{GitRepository as _, RealGitRepository, repo_path},
};
use gpui::{AppContext as _, Entity, TestAppContext};
use language::{Buffer, LineEnding, Point};
use multi_buffer::{MultiBuffer, MultiBufferRow, RowInfo};
use project::{FakeFs, Project};
use serde::{Deserialize, Serialize};
use settings::SettingsStore;
use text::BufferId;
use util::path;

#[derive(Serialize, Deserialize)]
struct Fixture {
    path: String,
    text: String,
    entries: Vec<BlameEntry>,
}

struct SplitMix64(u64);

impl SplitMix64 {
    fn next(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9e37_79b9_7f4a_7c15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
        z ^ (z >> 31)
    }

    fn below(&mut self, bound: u32) -> u32 {
        (self.next() % u64::from(bound)) as u32
    }
}

const FNV_OFFSET: u64 = 0xcbf2_9ce4_8422_2325;

fn fnv1a(hash: u64, bytes: &[u8]) -> u64 {
    bytes.iter().fold(hash, |hash, byte| {
        (hash ^ u64::from(*byte)).wrapping_mul(0x0100_0000_01b3)
    })
}

fn env_usize(name: &str, default: usize) -> usize {
    std::env::var(name)
        .map(|value| value.parse().unwrap_or_else(|_| panic!("invalid {name}")))
        .unwrap_or(default)
}

fn load_fixture() -> Option<Fixture> {
    let fixture_path = std::env::var_os("BENCH_FIXTURE")?;
    let bytes = std::fs::read(&fixture_path).expect("reading BENCH_FIXTURE");
    Some(serde_json::from_slice(&bytes).expect("parsing BENCH_FIXTURE"))
}

fn init(cx: &mut TestAppContext) {
    cx.update(|cx| {
        let store = SettingsStore::test(cx);
        cx.set_global(store);
        theme_settings::init(theme::LoadThemes::JustBase, cx);
        release_channel::init(semver::Version::new(0, 0, 0), cx);
        editor::init(cx);
    });
}

fn percentile(sorted: &[u64], fraction: f64) -> f64 {
    let index = ((sorted.len() - 1) as f64 * fraction).round() as usize;
    sorted[index] as f64 / 1e3
}

#[gpui::test]
async fn bench_blame_rows(cx: &mut TestAppContext) {
    let Some(fixture) = load_fixture() else {
        println!("BENCH skipped: BENCH_FIXTURE is not set");
        return;
    };
    let visible_rows = env_usize("BENCH_ROWS", 60);
    let warmup_frames = env_usize("BENCH_WARMUP_FRAMES", 200);
    let frames = env_usize("BENCH_FRAMES", 2000);
    let hunks = fixture.entries.len();
    let name = fixture.path.clone();

    init(cx);
    let fs = FakeFs::new(cx.executor());
    fs.insert_tree(
        path!("/repo"),
        serde_json::json!({ ".git": {}, "file.rs": fixture.text }),
    )
    .await;
    fs.set_blame_for_repo(
        Path::new(path!("/repo/.git")),
        vec![(
            repo_path("file.rs"),
            Blame {
                entries: fixture.entries,
                ..Default::default()
            },
        )],
    );
    let project = Project::test(fs, [path!("/repo").as_ref()], cx).await;
    let buffer = project
        .update(cx, |project, cx| {
            project.open_local_buffer(path!("/repo/file.rs"), cx)
        })
        .await
        .unwrap();
    let multi_buffer = cx.new(|cx| MultiBuffer::singleton(buffer, cx));
    let git_blame = cx.new(|cx| GitBlame::new(multi_buffer.clone(), project, false, true, cx));
    cx.executor().run_until_parked();
    assert!(
        git_blame.read_with(cx, |blame, _| blame.has_generated_entries()),
        "the blame did not load"
    );

    cx.update(|cx| {
        let snapshot = multi_buffer.read(cx).snapshot(cx);
        let line_count = snapshot.max_row().0 + 1;
        let start_rows = line_count.saturating_sub(visible_rows as u32) + 1;
        let rows_for_frame = |frame: usize| -> Vec<RowInfo> {
            snapshot
                .row_infos(MultiBufferRow(frame as u32 % start_rows))
                .take(visible_rows)
                .collect()
        };

        let mut samples = Vec::with_capacity(frames);
        for frame in 0..warmup_frames + frames {
            let rows = rows_for_frame(frame);
            let start = Instant::now();
            let blamed: Vec<_> =
                git_blame.update(cx, |blame, cx| blame.blame_for_rows(&rows, cx).collect());
            let elapsed = start.elapsed();
            if frame >= warmup_frames {
                samples.push(elapsed.as_nanos() as u64);
            }
            drop(blamed);
        }

        let mut hash = FNV_OFFSET;
        let mut row_total = 0;
        let mut blamed_total = 0;
        let mut allocations = 0;
        for frame in warmup_frames..warmup_frames + frames {
            let rows = rows_for_frame(frame);
            let allocations_before = allocation_count();
            let blamed: Vec<_> =
                git_blame.update(cx, |blame, cx| blame.blame_for_rows(&rows, cx).collect());
            allocations += allocation_count() - allocations_before;
            row_total += rows.len();
            blamed_total += blamed.iter().flatten().count();
            hash = fnv1a(hash, format!("{blamed:?}").as_bytes());
        }

        samples.sort_unstable();
        let mean = samples.iter().sum::<u64>() as f64 / samples.len() as f64 / 1e3;
        println!(
            "BENCH file={name} hunks={hunks} lines={line_count} rows={:.1} blamed_rows={:.1} \
             frames={frames} median_us={:.3} mean_us={mean:.3} p10_us={:.3} p90_us={:.3} \
             min_us={:.3} allocations={:.1} hash={hash:016x}",
            row_total as f64 / frames as f64,
            blamed_total as f64 / frames as f64,
            percentile(&samples, 0.5),
            percentile(&samples, 0.1),
            percentile(&samples, 0.9),
            samples[0] as f64 / 1e3,
            allocations as f64 / frames as f64,
        );
    });
}

fn randomly_edit(buffer: &Entity<Buffer>, rng: &mut SplitMix64, cx: &mut TestAppContext) {
    buffer.update(cx, |buffer, cx| {
        for _ in 0..1 + rng.below(3) {
            let max_row = buffer.max_point().row;
            let row = rng.below(max_row + 1);
            match rng.below(5) {
                0 => buffer.edit([(Point::new(row, 0)..Point::new(row, 0), "x")], None, cx),
                1 => {
                    let end = Point::new(row, buffer.line_len(row));
                    buffer.edit([(end..end, "y")], None, cx)
                }
                2 => buffer.edit([(Point::new(row, 0)..Point::new(row, 0), "\n")], None, cx),
                3 => {
                    let end = Point::new(row, buffer.line_len(row));
                    buffer.edit([(end..end, "\nz\n")], None, cx)
                }
                _ => {
                    let end_row = (row + 1 + rng.below(3)).min(max_row);
                    buffer.edit([(Point::new(row, 0)..Point::new(end_row, 0), "")], None, cx)
                }
            };
        }
    });
}

// Prints a hash of what `blame_for_rows` returns for rows that switch between two buffers, with
// edits to both buffers between calls. The two variants must print the same hash.
#[gpui::test]
async fn check_interleaved_buffers(cx: &mut TestAppContext) {
    let Some(fixture) = load_fixture() else {
        println!("CHECK skipped: BENCH_FIXTURE is not set");
        return;
    };
    let rounds = env_usize("BENCH_CHECK_ROUNDS", 200);

    init(cx);
    let fs = FakeFs::new(cx.executor());
    fs.insert_tree(
        path!("/repo"),
        serde_json::json!({ ".git": {}, "a.rs": fixture.text, "b.rs": fixture.text }),
    )
    .await;
    let blame = Blame {
        entries: fixture.entries,
        ..Default::default()
    };
    fs.set_blame_for_repo(
        Path::new(path!("/repo/.git")),
        vec![(repo_path("a.rs"), blame.clone()), (repo_path("b.rs"), blame)],
    );
    let project = Project::test(fs, [path!("/repo").as_ref()], cx).await;
    let mut buffers = Vec::new();
    for file in [path!("/repo/a.rs"), path!("/repo/b.rs")] {
        let buffer = project
            .update(cx, |project, cx| project.open_local_buffer(file, cx))
            .await
            .unwrap();
        buffers.push(buffer);
    }
    let buffer_ids: Vec<BufferId> = buffers
        .iter()
        .map(|buffer| buffer.read_with(cx, |buffer, _| buffer.remote_id()))
        .collect();
    let multi_buffer = cx.new(|cx| {
        let mut multi_buffer = MultiBuffer::new(language::Capability::ReadWrite);
        for buffer in &buffers {
            multi_buffer.set_excerpts_for_buffer(
                buffer.clone(),
                [Point::zero()..buffer.read(cx).max_point()],
                0,
                cx,
            );
        }
        multi_buffer
    });
    let git_blame = cx.new(|cx| GitBlame::new(multi_buffer.clone(), project, false, true, cx));
    cx.executor().run_until_parked();
    assert!(
        git_blame.read_with(cx, |blame, _| blame.has_generated_entries()),
        "the blame did not load"
    );

    let mut rng = SplitMix64(0x5eed);
    let mut hash = FNV_OFFSET;
    let mut blamed_total = 0;
    let mut unblamed_total = 0;
    for _ in 0..rounds {
        for buffer in &buffers {
            if rng.below(3) > 0 {
                randomly_edit(buffer, &mut rng, cx);
            }
        }
        let max_rows: Vec<u32> = buffers
            .iter()
            .map(|buffer| buffer.read_with(cx, |buffer, _| buffer.max_point().row))
            .collect();
        let mut rows = Vec::new();
        for _ in 0..1 + rng.below(8) {
            let buffer_index = rng.below(2) as usize;
            let start_row = rng.below(max_rows[buffer_index] + 1);
            for offset in 0..1 + rng.below(40) {
                rows.push(match rng.below(20) {
                    0 => RowInfo::default(),
                    1 => RowInfo {
                        buffer_id: Some(buffer_ids[buffer_index]),
                        ..Default::default()
                    },
                    _ => RowInfo {
                        buffer_id: Some(buffer_ids[buffer_index]),
                        buffer_row: Some(start_row + offset),
                        ..Default::default()
                    },
                });
            }
        }
        let blamed: Vec<_> =
            git_blame.update(cx, |blame, cx| blame.blame_for_rows(&rows, cx).collect());
        blamed_total += blamed.iter().flatten().count();
        unblamed_total += blamed.iter().filter(|entry| entry.is_none()).count();
        hash = fnv1a(hash, format!("{blamed:?}").as_bytes());
    }
    println!(
        "CHECK file={} rounds={rounds} blamed_rows={blamed_total} unblamed_rows={unblamed_total} \
         hash={hash:016x}",
        fixture.path
    );
}

// Writes one fixture per path in BENCH_DUMP_PATHS (comma-separated, relative to BENCH_REPO_ROOT)
// into BENCH_DUMP_DIR, using the same `git blame` call and parser as the app.
#[gpui::test]
async fn dump_blame_fixtures(cx: &mut TestAppContext) {
    let Some(output_dir) = std::env::var_os("BENCH_DUMP_DIR").map(PathBuf::from) else {
        println!("DUMP skipped: BENCH_DUMP_DIR is not set");
        return;
    };
    let repo_root = PathBuf::from(std::env::var_os("BENCH_REPO_ROOT").expect("BENCH_REPO_ROOT"));
    let paths = std::env::var("BENCH_DUMP_PATHS").expect("BENCH_DUMP_PATHS");
    let git_binary = std::env::var_os("BENCH_GIT").map_or_else(|| "git".into(), PathBuf::from);

    cx.executor().allow_parking();
    let repository = RealGitRepository::new(
        &repo_root.join(".git"),
        None,
        Some(git_binary),
        cx.executor(),
    )
    .unwrap();
    std::fs::create_dir_all(&output_dir).unwrap();
    for path in paths.split(',') {
        let text = std::fs::read_to_string(repo_root.join(path)).unwrap();
        let blame = repository
            .blame(repo_path(path), text.as_str().into(), LineEnding::Unix)
            .await
            .unwrap();
        let fixture = Fixture {
            path: path.to_string(),
            text,
            entries: blame.entries,
        };
        let file_name = format!("{}.json", path.replace('/', "__"));
        std::fs::write(
            output_dir.join(&file_name),
            serde_json::to_vec(&fixture).unwrap(),
        )
        .unwrap();
        println!("DUMP {path} hunks={} -> {file_name}", fixture.entries.len());
    }
}
