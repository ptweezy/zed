use git::repository::{GitRepository, RealGitRepository};
use std::time::Instant;

fn main() {
    let executor = gpui_platform::background_executor();
    let repositories = std::env::var_os("BENCH_REPOS").expect("BENCH_REPOS is not set");
    for repository_path in std::env::split_paths(&repositories) {
        let repository = RealGitRepository::new(
            &repository_path.join(".git"),
            None,
            Some("git".into()),
            executor.clone(),
        )
        .expect("failed to open repository");
        for _ in 0..5 {
            let start = Instant::now();
            let scan = futures::executor::block_on(repository.branches()).expect("branches failed");
            let elapsed = start.elapsed();
            assert!(scan.error.is_none(), "{:?}", scan.error);
            let head = scan.branches.iter().find(|branch| branch.is_head);
            println!(
                "BENCH\t{}\t{}\t{}\t{:?}",
                repository_path.display(),
                scan.branches.len(),
                elapsed.as_nanos(),
                head.map(|head| head.upstream.as_ref().map(|upstream| upstream.tracking))
            );
        }
    }
}
