
    #[gpui::test]
    async fn bench_branches(cx: &mut TestAppContext) {
        cx.executor().allow_parking();
        let repositories = std::env::var_os("BENCH_REPOS").unwrap();
        for repository_path in std::env::split_paths(&repositories) {
            let repository = RealGitRepository::new(
                &repository_path.join(".git"),
                None,
                Some("git".into()),
                cx.executor(),
            )
            .unwrap();
            for _ in 0..5 {
                let start = std::time::Instant::now();
                let scan = repository.branches().await.unwrap();
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
