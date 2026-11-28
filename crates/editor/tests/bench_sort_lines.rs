//! Times one sort-lines action on a generated buffer with every line selected.
//!
//! BENCH_ACTION: case_insensitive or by_length. BENCH_TEXT: ascii, german, cyrillic, cjk, or
//! short (0 to 4 letters per line). BENCH_LINES: line count. BENCH_PRESORTED: if set, the lines
//! start sorted by the action's key.
use std::time::Instant;

use editor::{
    Editor, EditorMode,
    actions::{SelectAll, SortLinesByLength, SortLinesCaseInsensitive},
};
use gpui::TestAppContext;
use multi_buffer::MultiBuffer;
use settings::SettingsStore;

const ASCII_WORDS: &[&str] = &[
    "the", "quick", "brown", "fox", "jumps", "over", "lazy", "dog", "lorem", "ipsum", "dolor",
    "sit", "amet", "consectetur", "adipiscing", "elit", "sed", "eiusmod", "tempor", "incididunt",
    "labore", "dolore", "magna", "aliqua", "enim", "minim", "veniam", "quis", "nostrud",
    "exercitation", "ullamco", "laboris", "nisi", "aliquip", "commodo", "consequat", "duis",
    "aute", "irure", "reprehenderit", "voluptate", "velit", "esse", "cillum", "fugiat", "nulla",
    "pariatur", "excepteur", "sint", "occaecat", "cupidatat", "proident", "sunt", "culpa",
    "officia", "deserunt", "mollit", "anim", "laborum",
];

const GERMAN_WORDS: &[&str] = &[
    "über", "straße", "größe", "schön", "mädchen", "fährt", "bäume", "groß", "weiß", "fußball",
    "käse", "brötchen", "öl", "übung", "äpfel", "müde", "hören", "tür", "glück", "früh", "spät",
    "schlüssel", "gemüse", "prüfung", "der", "die", "das", "und", "ist", "nicht", "mit", "auf",
    "für", "von", "haus", "stadt", "wasser", "zeit", "jahr", "welt",
];

const CYRILLIC_WORDS: &[&str] = &[
    "привет", "мир", "дом", "москва", "книга", "вода", "город", "человек", "время", "жизнь",
    "день", "рука", "работа", "слово", "место", "лицо", "друг", "глаз", "вопрос", "сторона",
    "страна", "земля", "конец", "голова", "система", "образ", "война", "отец", "женщина",
    "деньги", "машина", "история", "и", "в", "не", "на", "что", "это", "как", "по",
];

const CJK_WORDS: &[&str] = &[
    "你好", "世界", "中国", "北京", "上海", "我们", "他们", "学习", "工作", "时间", "朋友",
    "电脑", "手机", "东京", "日本語", "漢字", "こんにちは", "ありがとう", "ひらがな", "カタカナ",
    "한국어", "서울", "사랑", "감사합니다", "的", "是", "在", "有", "人", "这", "大", "来", "上",
    "国", "个", "到", "说", "们", "为", "子",
];

struct SplitMix64(u64);

impl SplitMix64 {
    fn next(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9e37_79b9_7f4a_7c15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
        z ^ (z >> 31)
    }

    fn below(&mut self, bound: usize) -> usize {
        (self.next() % bound as u64) as usize
    }
}

fn generate_lines(kind: &str, line_count: usize) -> Vec<String> {
    let words = match kind {
        "ascii" => ASCII_WORDS,
        "german" => GERMAN_WORDS,
        "cyrillic" => CYRILLIC_WORDS,
        "cjk" => CJK_WORDS,
        "short" => &[],
        other => panic!("unknown BENCH_TEXT {other}"),
    };
    let mut rng = SplitMix64(0x5eed);
    (0..line_count)
        .map(|_| {
            let mut line = String::new();
            if words.is_empty() {
                const LETTERS: &[u8] = b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ";
                for _ in 0..rng.below(5) {
                    line.push(char::from(LETTERS[rng.below(LETTERS.len())]));
                }
                return line;
            }
            for word_index in 0..4 + rng.below(9) {
                if word_index > 0 {
                    line.push(' ');
                }
                let word = words[rng.below(words.len())];
                let mut chars = word.chars();
                match chars.next() {
                    Some(first) if rng.below(4) == 0 => {
                        line.extend(first.to_uppercase());
                        line.push_str(chars.as_str());
                    }
                    _ => line.push_str(word),
                }
            }
            line
        })
        .collect()
}

fn sort_reference(lines: &mut [&str], action: &str) {
    match action {
        "case_insensitive" => lines.sort_by_cached_key(|line| line.to_lowercase()),
        "by_length" => lines.sort_by_cached_key(|line| line.chars().count()),
        other => panic!("unknown BENCH_ACTION {other}"),
    }
}

fn fnv1a(bytes: &[u8]) -> u64 {
    bytes.iter().fold(0xcbf2_9ce4_8422_2325, |hash, byte| {
        (hash ^ u64::from(*byte)).wrapping_mul(0x0100_0000_01b3)
    })
}

#[gpui::test]
fn bench_sort_lines(cx: &mut TestAppContext) {
    let action = std::env::var("BENCH_ACTION").unwrap_or_else(|_| "case_insensitive".into());
    let kind = std::env::var("BENCH_TEXT").unwrap_or_else(|_| "ascii".into());
    let line_count = std::env::var("BENCH_LINES")
        .map(|value| value.parse::<usize>().expect("BENCH_LINES"))
        .unwrap_or(100_000);
    let presorted = std::env::var_os("BENCH_PRESORTED").is_some();

    let lines = generate_lines(&kind, line_count);
    let mut line_refs: Vec<&str> = lines.iter().map(String::as_str).collect();
    if presorted {
        sort_reference(&mut line_refs, &action);
    }
    let text = line_refs.join("\n") + "\n";
    sort_reference(&mut line_refs, &action);
    let expected = line_refs.join("\n") + "\n";

    cx.update(|cx| {
        assets::Assets.load_test_fonts(cx);
        let store = SettingsStore::test(cx);
        cx.set_global(store);
        theme_settings::init(theme::LoadThemes::JustBase, cx);
        release_channel::init(semver::Version::new(0, 0, 0), cx);
        editor::init(cx);
    });
    let buffer = cx.update(|cx| MultiBuffer::build_simple(&text, cx));
    let window =
        cx.add_window(|window, cx| Editor::new(EditorMode::full(), buffer, None, window, cx));
    window
        .update(cx, |editor, window, cx| {
            editor.select_all(&SelectAll, window, cx)
        })
        .unwrap();
    cx.run_until_parked();

    let start = Instant::now();
    window
        .update(cx, |editor, window, cx| match action.as_str() {
            "case_insensitive" => {
                editor.sort_lines_case_insensitive(&SortLinesCaseInsensitive, window, cx)
            }
            "by_length" => editor.sort_lines_by_length(&SortLinesByLength, window, cx),
            other => panic!("unknown BENCH_ACTION {other}"),
        })
        .unwrap();
    let elapsed = start.elapsed();
    cx.run_until_parked();

    let sorted = window.update(cx, |editor, _, cx| editor.text(cx)).unwrap();
    assert!(sorted == expected, "the editor's result is not the stable sort");
    println!(
        "BENCH action={action} text={kind}{} lines={line_count} bytes={} ms={:.3} hash={:016x}",
        if presorted { "-sorted" } else { "" },
        text.len(),
        elapsed.as_secs_f64() * 1e3,
        fnv1a(sorted.as_bytes()),
    );
}
