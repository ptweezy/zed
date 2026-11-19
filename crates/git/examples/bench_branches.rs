use git::repository::{GitRepository, RealGitRepository};
use std::time::Instant;

fn main() {
    let executor = gpui_platform::background_executor();
    let repositories = std::env::var_os("BENCH_REPOS").expect("BENCH_REPOS is not set");
    for repository_path in std::env::split_paths(&repositories) {
        for _ in 0..5 {
            // A new repository remembers no head, as on the first listing after opening a project,
            // so its first listing reads the head after the listing and its second one alongside.
            let repository = RealGitRepository::new(
                &repository_path.join(".git"),
                None,
                Some("git".into()),
                executor.clone(),
            )
            .expect("failed to open repository");
            let mut nanoseconds = Vec::new();
            let mut summary = None;
            for _ in 0..2 {
                let start = Instant::now();
                let scan =
                    futures::executor::block_on(repository.branches()).expect("branches failed");
                nanoseconds.push(start.elapsed().as_nanos());
                assert!(scan.error.is_none(), "{:?}", scan.error);
                let head = scan.branches.iter().find(|branch| branch.is_head);
                summary = Some((
                    scan.branches.len(),
                    format!(
                        "{:?}",
                        head.map(|head| head.upstream.as_ref().map(|upstream| upstream.tracking))
                    ),
                ));
            }
            let (branch_count, head) = summary.expect("no listing ran");
            println!(
                "BENCH\t{}\t{}\t{}\t{}\t{}",
                repository_path.display(),
                branch_count,
                nanoseconds[0],
                nanoseconds[1],
                head
            );
        }
    }
}
