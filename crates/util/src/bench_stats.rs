use std::collections::BTreeMap;
use std::sync::Mutex;
use std::time::{Duration, Instant};

static STATS: Mutex<BTreeMap<&'static str, (Duration, u64)>> = Mutex::new(BTreeMap::new());

pub struct Timer {
    name: &'static str,
    start: Instant,
}

impl Drop for Timer {
    fn drop(&mut self) {
        let elapsed = self.start.elapsed();
        let mut stats = STATS.lock().unwrap();
        let entry = stats.entry(self.name).or_default();
        entry.0 += elapsed;
        entry.1 += 1;
    }
}

pub fn timer(name: &'static str) -> Timer {
    Timer {
        name,
        start: Instant::now(),
    }
}

pub fn count(name: &'static str, amount: u64) {
    let mut stats = STATS.lock().unwrap();
    let entry = stats.entry(name).or_default();
    entry.1 += amount;
}

pub fn reset() {
    STATS.lock().unwrap().clear();
}

pub fn report() -> String {
    let stats = STATS.lock().unwrap();
    let mut out = String::new();
    for (name, (total, calls)) in stats.iter() {
        out.push_str(&format!(
            "  {name}: {:.1} ms over {calls} calls\n",
            total.as_secs_f64() * 1000.0
        ));
    }
    out
}
