use client::{Client, UserStore};
use fs::RealFs;
use gpui::AppContext as _;
use http_client::{BlockedHttpClient, HttpClientWithUrl};
use language::LanguageRegistry;
use project::{
    LocalProjectFlags, Project,
    search::{SearchQuery, SearchResult},
};
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Instant;
use util::paths::PathMatcher;

#[cfg(unix)]
const RUSAGE_SELF: libc::c_int = libc::RUSAGE_SELF;
#[cfg(unix)]
const RUSAGE_CHILDREN: libc::c_int = libc::RUSAGE_CHILDREN;
#[cfg(not(unix))]
const RUSAGE_SELF: i32 = 0;
#[cfg(not(unix))]
const RUSAGE_CHILDREN: i32 = 1;

fn cpu_seconds() -> f64 {
    usage_seconds(RUSAGE_SELF)
}

fn child_cpu_seconds() -> f64 {
    usage_seconds(RUSAGE_CHILDREN)
}

#[cfg(unix)]
fn usage_seconds(who: libc::c_int) -> f64 {
    let mut usage: libc::rusage = unsafe { std::mem::zeroed() };
    unsafe { libc::getrusage(who, &mut usage) };
    let seconds = |time: libc::timeval| time.tv_sec as f64 + time.tv_usec as f64 / 1e6;
    seconds(usage.ru_utime) + seconds(usage.ru_stime)
}

#[cfg(not(unix))]
fn usage_seconds(_who: i32) -> f64 {
    f64::NAN
}

fn build_query(kind: &str, text: &str) -> SearchQuery {
    match kind {
        "text" => SearchQuery::text(
            text,
            false,
            true,
            false,
            PathMatcher::default(),
            PathMatcher::default(),
            false,
            None,
        ),
        "itext" => SearchQuery::text(
            text,
            false,
            false,
            false,
            PathMatcher::default(),
            PathMatcher::default(),
            false,
            None,
        ),
        "regex" => SearchQuery::regex(
            text,
            false,
            true,
            false,
            false,
            PathMatcher::default(),
            PathMatcher::default(),
            false,
            None,
        ),
        _ => panic!("unknown query kind {kind}"),
    }
    .expect("valid query")
}

fn main() {
    let mut args = std::env::args().skip(1);
    let path: PathBuf = args.next().expect("root path").into();
    let kind = args.next().expect("query kind");
    let text = args.next().expect("query text");
    let iterations: usize = args.next().and_then(|v| v.parse().ok()).unwrap_or(5);
    let label = args.next().unwrap_or_default();

    gpui_platform::headless().run(move |cx| {
        settings::init(cx);
        release_channel::init(semver::Version::new(0, 0, 0), cx);
        let http = Arc::new(HttpClientWithUrl::new(
            Arc::new(BlockedHttpClient::new()),
            "http://localhost",
            None,
        ));
        let client = Client::new(Arc::new(clock::RealSystemClock), http, cx);
        Project::init(&client, cx);
        let user_store = cx.new(|cx| UserStore::new(client.clone(), cx));
        let languages = Arc::new(LanguageRegistry::new(cx.background_executor().clone()));
        let fs: Arc<dyn fs::Fs> = RealFs::new(None, cx.background_executor().clone());
        let project = Project::local(
            client,
            node_runtime::NodeRuntime::unavailable(),
            user_store,
            languages,
            fs,
            None,
            LocalProjectFlags {
                init_worktree_trust: false,
                watch_global_configs: false,
            },
            cx,
        );
        cx.spawn(async move |cx| {
            let scan_start = Instant::now();
            let (tree, _) = project
                .update(cx, |project, cx| project.find_or_create_worktree(&path, true, cx))
                .await
                .expect("worktree");
            tree.read_with(cx, |tree, _| tree.as_local().unwrap().scan_complete())
                .await;
            let file_count = tree.read_with(cx, |tree, _| tree.file_count());
            eprintln!(
                "scan: {:.0} ms, {file_count} files",
                scan_start.elapsed().as_secs_f64() * 1000.0
            );

            if kind == "diffs" || kind == "unstaged" {
                let count: usize = text.parse().expect("file count");
                let worktree_id = tree.read_with(cx, |tree, _| tree.id());
                let paths = tree.read_with(cx, |tree, _| {
                    tree.files(false, 0)
                        .take(count)
                        .map(|entry| entry.path.clone())
                        .collect::<Vec<_>>()
                });
                let open_start = Instant::now();
                let buffer_tasks = project.update(cx, |project, cx| {
                    paths
                        .iter()
                        .map(|path| project.open_buffer((worktree_id, path.clone()), cx))
                        .collect::<Vec<_>>()
                });
                let buffers = futures::future::join_all(buffer_tasks)
                    .await
                    .into_iter()
                    .filter_map(Result::ok)
                    .collect::<Vec<_>>();
                eprintln!(
                    "opened {} buffers in {:.0} ms",
                    buffers.len(),
                    open_start.elapsed().as_secs_f64() * 1000.0
                );
                let settle_ms: u64 = std::env::var("BENCH_SETTLE_MS")
                    .ok()
                    .and_then(|value| value.parse().ok())
                    .unwrap_or(5000);
                cx.background_executor()
                    .timer(std::time::Duration::from_millis(settle_ms))
                    .await;
                let cpu_start = cpu_seconds();
                let child_cpu_start = child_cpu_seconds();
                let start = Instant::now();
                let diff_tasks = project.update(cx, |project, cx| {
                    buffers
                        .iter()
                        .map(|buffer| {
                            if kind == "unstaged" {
                                project.open_unstaged_diff(buffer.clone(), cx)
                            } else {
                                project.open_uncommitted_diff(buffer.clone(), cx)
                            }
                        })
                        .collect::<Vec<_>>()
                });
                let diffs = futures::future::join_all(diff_tasks).await;
                let wall = start.elapsed().as_secs_f64() * 1000.0;
                let cpu = (cpu_seconds() - cpu_start) * 1000.0;
                let child_cpu = (child_cpu_seconds() - child_cpu_start) * 1000.0;
                let loaded = diffs.iter().filter(|diff| diff.is_ok()).count();
                let first_error = diffs.iter().find_map(|diff| diff.as_ref().err().map(|e| format!("{e:#}")));
                println!(
                    "{label}: {loaded}/{} {kind} diffs in {wall:.1} ms (Zed CPU {cpu:.1} ms, git CPU {child_cpu:.1} ms), first error {first_error:?}",
                    buffers.len()
                );
                drop(diffs);
                drop(buffers);
                cx.update(|cx| cx.quit());
                return;
            }

            let mut walls = Vec::new();
            for iteration in 0..iterations {
                let query = build_query(&kind, &text);
                let cpu_start = cpu_seconds();
                let start = Instant::now();
                let results = project.update(cx, |project, cx| project.search(query, cx));
                let mut first_result = None;
                let mut buffers = 0usize;
                let mut ranges = 0usize;
                let mut limit_reached = false;
                let mut held = Vec::new();
                while let Ok(result) = results.rx.recv().await {
                    match result {
                        SearchResult::Buffer { buffer, ranges: found } => {
                            first_result.get_or_insert_with(|| start.elapsed());
                            buffers += 1;
                            ranges += found.len();
                            held.push(buffer);
                        }
                        SearchResult::LimitReached => limit_reached = true,
                        SearchResult::WaitingForScan | SearchResult::Searching => {}
                    }
                }
                let wall = start.elapsed().as_secs_f64() * 1000.0;
                let cpu = (cpu_seconds() - cpu_start) * 1000.0;
                drop(results);
                drop(held);
                println!(
                    "{label} iter {iteration}: wall {wall:.1} ms, cpu {cpu:.1} ms, first {:.1} ms, buffers {buffers}, ranges {ranges}, limit {limit_reached}",
                    first_result.map_or(f64::NAN, |d| d.as_secs_f64() * 1000.0)
                );
                if iteration > 0 {
                    walls.push(wall);
                }
                cx.background_executor()
                    .timer(std::time::Duration::from_millis(200))
                    .await;
            }
            walls.sort_by(|a, b| a.partial_cmp(b).unwrap());
            if !walls.is_empty() {
                println!("{label} median wall (excluding first): {:.1} ms", walls[walls.len() / 2]);
            }
            cx.update(|cx| cx.quit());
        })
        .detach();
    });
}
