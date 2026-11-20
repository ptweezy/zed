
#[cfg(test)]
mod spinner_cpu_bench {
    use super::*;
    use crate::{prelude::*, LoadingLabel};
    use gpui::{AnyElement, TestAppContext};
    use std::{cell::Cell, rc::Rc, time::Instant};

    // Stands in for the views above the spinner (thread view, panel, workspace).
    struct Tree {
        renders: Rc<Cell<usize>>,
        rows: usize,
        label: fn() -> AnyElement,
    }

    impl Render for Tree {
        fn render(&mut self, _: &mut Window, _: &mut Context<Self>) -> impl IntoElement {
            self.renders.set(self.renders.get() + 1);
            v_flex()
                .size_full()
                .children((0..self.rows).map(|ix| {
                    h_flex()
                        .gap_2()
                        .p_1()
                        .border_1()
                        .child(Label::new(format!("Row {ix}")))
                        .child(Label::new("Some message text in the thread").color(Color::Muted))
                        .child(div().size_4().rounded_sm())
                }))
                .child((self.label)())
        }
    }

    #[gpui::test]
    #[ignore]
    fn bench_spinner_cpu(cx: &mut TestAppContext) {
        cx.update(|cx| {
            let settings_store = settings::SettingsStore::test(cx);
            cx.set_global(settings_store);
            theme_settings::init(theme::LoadThemes::JustBase, cx);
        });
        let hz: u64 = std::env::var("BENCH_HZ").ok().and_then(|v| v.parse().ok()).unwrap_or(120);
        let seconds: u64 = std::env::var("BENCH_SECONDS").ok().and_then(|v| v.parse().ok()).unwrap_or(10);
        let rows: usize = std::env::var("BENCH_ROWS").ok().and_then(|v| v.parse().ok()).unwrap_or(300);
        let variant = std::env::var("BENCH_LABEL").unwrap_or_else(|_| "dots".into());
        let label: fn() -> AnyElement = match variant.as_str() {
            "sand" => || SpinnerLabel::sand().into_any_element(),
            "loading" => || LoadingLabel::new("Awaiting Confirmation").into_any_element(),
            _ => || SpinnerLabel::dots().into_any_element(),
        };
        let renders = Rc::new(Cell::new(0));
        let (_, cx) = cx.add_window_view({
            let renders = renders.clone();
            move |_, _| Tree { renders, rows, label }
        });
        cx.run_until_parked();
        let frame = Duration::from_nanos(1_000_000_000 / hz);
        let mut step = |frames: u64| {
            for _ in 0..frames {
                cx.executor().advance_clock(frame);
                cx.update(|window, cx| window.simulate_next_frame(cx));
                cx.run_until_parked();
            }
        };
        step(hz); // warm up, and skip LoadingLabel's reveal
        let start_renders = renders.get();
        let start = Instant::now();
        step(hz * seconds);
        let wall = start.elapsed();
        let renders = renders.get() - start_renders;
        println!(
            "BENCH label={variant} hz={hz} rows={rows} renders_per_s={:.1} cpu_ms_per_s={:.2} us_per_render={:.1}",
            renders as f64 / seconds as f64,
            wall.as_secs_f64() * 1e3 / seconds as f64,
            wall.as_secs_f64() * 1e6 / renders.max(1) as f64,
        );
    }
}
