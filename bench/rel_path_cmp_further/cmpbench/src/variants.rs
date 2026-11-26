//! Candidate implementations of `RelPath::cmp`, as free functions over `&str`.
//! Every variant is `#[inline(never)]` so each is measured as one call.

use std::cmp::Ordering;

#[inline(always)]
fn rank(byte: u8) -> u16 {
    if byte == b'/' { 0 } else { u16::from(byte) + 1 }
}

struct Components<'a>(&'a str);

impl<'a> Iterator for Components<'a> {
    type Item = &'a str;

    fn next(&mut self) -> Option<Self::Item> {
        if let Some(sep_ix) = self.0.find('/') {
            let (head, tail) = self.0.split_at(sep_ix);
            self.0 = &tail[1..];
            Some(head)
        } else if self.0.is_empty() {
            None
        } else {
            let result = self.0;
            self.0 = "";
            Some(result)
        }
    }
}

/// Before #67023: compare component iterators.
#[inline(never)]
pub fn components(a: &str, b: &str) -> Ordering {
    Components(a).cmp(Components(b))
}

/// main after #67023: skip equal 16-byte chunks, then scan bytes.
#[inline(never)]
pub fn base(a: &str, b: &str) -> Ordering {
    const CHUNK: usize = 16;
    let (a, b) = (a.as_bytes(), b.as_bytes());
    let len = a.len().min(b.len());
    let mut offset = 0;
    while offset + CHUNK <= len && a[offset..offset + CHUNK] == b[offset..offset + CHUNK] {
        offset += CHUNK;
    }
    match a[offset..len]
        .iter()
        .zip(&b[offset..len])
        .position(|(a, b)| a != b)
    {
        Some(ix) => rank(a[offset + ix]).cmp(&rank(b[offset + ix])),
        None => a.len().cmp(&b.len()),
    }
}

/// PR #67122 at 7a2820a7fb, verbatim apart from taking `&str`.
#[inline(never)]
pub fn pr(a: &str, b: &str) -> Ordering {
    const CHUNK: usize = size_of::<u128>();
    let first_difference = |left: &[u8; CHUNK], right: &[u8; CHUNK]| {
        let difference = u128::from_le_bytes(*left) ^ u128::from_le_bytes(*right);
        (difference != 0).then(|| difference.trailing_zeros() as usize / 8)
    };
    let (left, right) = (a.as_bytes(), b.as_bytes());
    let common_length = left.len().min(right.len());
    let (left_prefix, right_prefix) = (&left[..common_length], &right[..common_length]);
    let mut difference = None;
    for (chunk_index, (left_chunk, right_chunk)) in left_prefix
        .as_chunks::<CHUNK>()
        .0
        .iter()
        .zip(right_prefix.as_chunks::<CHUNK>().0)
        .enumerate()
    {
        if let Some(index) = first_difference(left_chunk, right_chunk) {
            difference = Some(chunk_index * CHUNK + index);
            break;
        }
    }
    if difference.is_none() && common_length % CHUNK != 0 {
        difference = match (
            left_prefix.last_chunk::<CHUNK>(),
            right_prefix.last_chunk::<CHUNK>(),
        ) {
            (Some(left_chunk), Some(right_chunk)) => first_difference(left_chunk, right_chunk)
                .map(|index| common_length - CHUNK + index),
            _ => left_prefix
                .iter()
                .zip(right_prefix)
                .position(|(left_byte, right_byte)| left_byte != right_byte),
        };
    }
    match difference {
        Some(index) => rank(left[index]).cmp(&rank(right[index])),
        None => left.len().cmp(&right.len()),
    }
}

/// The PR's shape with 8-byte chunks, as the reviewer asked about.
#[inline(never)]
pub fn pr_u64(a: &str, b: &str) -> Ordering {
    const CHUNK: usize = size_of::<u64>();
    let first_difference = |left: &[u8; CHUNK], right: &[u8; CHUNK]| {
        let difference = u64::from_le_bytes(*left) ^ u64::from_le_bytes(*right);
        (difference != 0).then(|| difference.trailing_zeros() as usize / 8)
    };
    let (left, right) = (a.as_bytes(), b.as_bytes());
    let common_length = left.len().min(right.len());
    let (left_prefix, right_prefix) = (&left[..common_length], &right[..common_length]);
    let mut difference = None;
    for (chunk_index, (left_chunk, right_chunk)) in left_prefix
        .as_chunks::<CHUNK>()
        .0
        .iter()
        .zip(right_prefix.as_chunks::<CHUNK>().0)
        .enumerate()
    {
        if let Some(index) = first_difference(left_chunk, right_chunk) {
            difference = Some(chunk_index * CHUNK + index);
            break;
        }
    }
    if difference.is_none() && common_length % CHUNK != 0 {
        difference = match (
            left_prefix.last_chunk::<CHUNK>(),
            right_prefix.last_chunk::<CHUNK>(),
        ) {
            (Some(left_chunk), Some(right_chunk)) => first_difference(left_chunk, right_chunk)
                .map(|index| common_length - CHUNK + index),
            _ => left_prefix
                .iter()
                .zip(right_prefix)
                .position(|(left_byte, right_byte)| left_byte != right_byte),
        };
    }
    match difference {
        Some(index) => rank(left[index]).cmp(&rank(right[index])),
        None => left.len().cmp(&right.len()),
    }
}

/// Hand-written SSE2: a floor for what 16-byte steps can do on x86_64.
/// Reference only, not a proposal.
#[cfg(target_arch = "x86_64")]
#[inline(never)]
pub fn sse2(a: &str, b: &str) -> Ordering {
    use std::arch::x86_64::{__m128i, _mm_cmpeq_epi8, _mm_loadu_si128, _mm_movemask_epi8};
    let (left, right) = (a.as_bytes(), b.as_bytes());
    let common_length = left.len().min(right.len());
    let mismatch = |offset: usize| -> u32 {
        // SAFETY: callers pass `offset + 16 <= common_length`.
        unsafe {
            let x = _mm_loadu_si128(left.as_ptr().add(offset) as *const __m128i);
            let y = _mm_loadu_si128(right.as_ptr().add(offset) as *const __m128i);
            !(_mm_movemask_epi8(_mm_cmpeq_epi8(x, y)) as u32) & 0xFFFF
        }
    };
    let mut offset = 0;
    let mut difference = None;
    while offset + 16 <= common_length {
        let mask = mismatch(offset);
        if mask != 0 {
            difference = Some(offset + mask.trailing_zeros() as usize);
            break;
        }
        offset += 16;
    }
    if difference.is_none() && offset < common_length {
        if common_length >= 16 {
            let mask = mismatch(common_length - 16);
            if mask != 0 {
                difference = Some(common_length - 16 + mask.trailing_zeros() as usize);
            }
        } else {
            difference = left[..common_length]
                .iter()
                .zip(&right[..common_length])
                .position(|(left_byte, right_byte)| left_byte != right_byte);
        }
    }
    match difference {
        // SAFETY: `index < common_length`.
        Some(index) => unsafe {
            rank(*left.get_unchecked(index)).cmp(&rank(*right.get_unchecked(index)))
        },
        None => left.len().cmp(&right.len()),
    }
}

/// Plain byte order via memcmp. Wrong order for paths (`a.b` vs `a/b`), but
/// shows what a comparison costs when no position search is needed.
#[inline(never)]
pub fn bytes(a: &str, b: &str) -> Ordering {
    a.as_bytes().cmp(b.as_bytes())
}

/// Cost of the call and of touching both strings' first bytes.
#[inline(never)]
pub fn touch(a: &str, b: &str) -> Ordering {
    let x = a.as_bytes().first().copied().unwrap_or(0);
    let y = b.as_bytes().first().copied().unwrap_or(0);
    x.cmp(&y)
}

#[inline(always)]
fn xor16(left: &[u8; 16], right: &[u8; 16]) -> Option<usize> {
    let difference = u128::from_le_bytes(*left) ^ u128::from_le_bytes(*right);
    (difference != 0).then(|| difference.trailing_zeros() as usize / 8)
}

#[inline(always)]
fn xor8(left: &[u8; 8], right: &[u8; 8]) -> Option<usize> {
    let difference = u64::from_le_bytes(*left) ^ u64::from_le_bytes(*right);
    (difference != 0).then(|| difference.trailing_zeros() as usize / 8)
}

#[inline(always)]
fn xor4(left: &[u8; 4], right: &[u8; 4]) -> Option<usize> {
    let difference = u32::from_le_bytes(*left) ^ u32::from_le_bytes(*right);
    (difference != 0).then(|| difference.trailing_zeros() as usize / 8)
}

#[inline(always)]
fn finish(left: &[u8], right: &[u8], difference: Option<usize>) -> Ordering {
    match difference {
        Some(index) => rank(left[index]).cmp(&rank(right[index])),
        None => left.len().cmp(&right.len()),
    }
}

#[inline(always)]
fn chunk_loop(left_prefix: &[u8], right_prefix: &[u8]) -> Option<usize> {
    for (chunk_index, (left_chunk, right_chunk)) in left_prefix
        .as_chunks::<16>()
        .0
        .iter()
        .zip(right_prefix.as_chunks::<16>().0)
        .enumerate()
    {
        if let Some(index) = xor16(left_chunk, right_chunk) {
            return Some(chunk_index * 16 + index);
        }
    }
    None
}

#[inline(always)]
fn byte_loop(left_prefix: &[u8], right_prefix: &[u8]) -> Option<usize> {
    left_prefix
        .iter()
        .zip(right_prefix)
        .position(|(left_byte, right_byte)| left_byte != right_byte)
}

/// PR, but common lengths of 8-15 bytes use two overlapping 8-byte windows.
#[inline(never)]
pub fn short8(a: &str, b: &str) -> Ordering {
    let (left, right) = (a.as_bytes(), b.as_bytes());
    let common_length = left.len().min(right.len());
    let (left_prefix, right_prefix) = (&left[..common_length], &right[..common_length]);
    let mut difference = chunk_loop(left_prefix, right_prefix);
    if difference.is_none() && common_length % 16 != 0 {
        difference = if let (Some(l), Some(r)) =
            (left_prefix.last_chunk::<16>(), right_prefix.last_chunk::<16>())
        {
            xor16(l, r).map(|index| common_length - 16 + index)
        } else if let (Some(lf), Some(rf), Some(ll), Some(rl)) = (
            left_prefix.first_chunk::<8>(),
            right_prefix.first_chunk::<8>(),
            left_prefix.last_chunk::<8>(),
            right_prefix.last_chunk::<8>(),
        ) {
            xor8(lf, rf).or_else(|| xor8(ll, rl).map(|index| common_length - 8 + index))
        } else {
            byte_loop(left_prefix, right_prefix)
        };
    }
    finish(left, right, difference)
}

/// short8, plus two overlapping 4-byte windows for 4-7 bytes.
#[inline(never)]
pub fn short4(a: &str, b: &str) -> Ordering {
    let (left, right) = (a.as_bytes(), b.as_bytes());
    let common_length = left.len().min(right.len());
    let (left_prefix, right_prefix) = (&left[..common_length], &right[..common_length]);
    let mut difference = chunk_loop(left_prefix, right_prefix);
    if difference.is_none() && common_length % 16 != 0 {
        difference = if let (Some(l), Some(r)) =
            (left_prefix.last_chunk::<16>(), right_prefix.last_chunk::<16>())
        {
            xor16(l, r).map(|index| common_length - 16 + index)
        } else if let (Some(lf), Some(rf), Some(ll), Some(rl)) = (
            left_prefix.first_chunk::<8>(),
            right_prefix.first_chunk::<8>(),
            left_prefix.last_chunk::<8>(),
            right_prefix.last_chunk::<8>(),
        ) {
            xor8(lf, rf).or_else(|| xor8(ll, rl).map(|index| common_length - 8 + index))
        } else if let (Some(lf), Some(rf), Some(ll), Some(rl)) = (
            left_prefix.first_chunk::<4>(),
            right_prefix.first_chunk::<4>(),
            left_prefix.last_chunk::<4>(),
            right_prefix.last_chunk::<4>(),
        ) {
            xor4(lf, rf).or_else(|| xor4(ll, rl).map(|index| common_length - 4 + index))
        } else {
            byte_loop(left_prefix, right_prefix)
        };
    }
    finish(left, right, difference)
}

/// PR with a same-pointer check first.
#[inline(never)]
pub fn ptr_eq(a: &str, b: &str) -> Ordering {
    if std::ptr::eq(a, b) {
        return Ordering::Equal;
    }
    let (left, right) = (a.as_bytes(), b.as_bytes());
    let common_length = left.len().min(right.len());
    let (left_prefix, right_prefix) = (&left[..common_length], &right[..common_length]);
    let mut difference = chunk_loop(left_prefix, right_prefix);
    if difference.is_none() && common_length % 16 != 0 {
        difference = match (left_prefix.last_chunk::<16>(), right_prefix.last_chunk::<16>()) {
            (Some(l), Some(r)) => xor16(l, r).map(|index| common_length - 16 + index),
            _ => byte_loop(left_prefix, right_prefix),
        };
    }
    finish(left, right, difference)
}

/// PR, comparing two 16-byte chunks per step.
#[inline(never)]
pub fn unroll(a: &str, b: &str) -> Ordering {
    let (left, right) = (a.as_bytes(), b.as_bytes());
    let common_length = left.len().min(right.len());
    let (left_prefix, right_prefix) = (&left[..common_length], &right[..common_length]);
    let (left_chunks, right_chunks) = (
        left_prefix.as_chunks::<16>().0,
        right_prefix.as_chunks::<16>().0,
    );
    let word = |chunk: &[u8; 16]| u128::from_le_bytes(*chunk);
    let mut difference = None;
    let mut chunk_index = 0;
    while chunk_index + 2 <= left_chunks.len() && chunk_index + 2 <= right_chunks.len() {
        let first = word(&left_chunks[chunk_index]) ^ word(&right_chunks[chunk_index]);
        let second = word(&left_chunks[chunk_index + 1]) ^ word(&right_chunks[chunk_index + 1]);
        if first | second != 0 {
            difference = Some(if first != 0 {
                chunk_index * 16 + first.trailing_zeros() as usize / 8
            } else {
                chunk_index * 16 + 16 + second.trailing_zeros() as usize / 8
            });
            break;
        }
        chunk_index += 2;
    }
    if difference.is_none() && chunk_index < left_chunks.len() && chunk_index < right_chunks.len() {
        difference = xor16(&left_chunks[chunk_index], &right_chunks[chunk_index])
            .map(|index| chunk_index * 16 + index);
    }
    if difference.is_none() && common_length % 16 != 0 {
        difference = match (left_prefix.last_chunk::<16>(), right_prefix.last_chunk::<16>()) {
            (Some(l), Some(r)) => xor16(l, r).map(|index| common_length - 16 + index),
            _ => byte_loop(left_prefix, right_prefix),
        };
    }
    finish(left, right, difference)
}

/// PR, but the chunk search returns the first differing bytes instead of an
/// index, so there is no index math and no bounds check.
#[inline(never)]
pub fn bytes_ret(a: &str, b: &str) -> Ordering {
    const CHUNK: usize = size_of::<u128>();
    let first_difference = |left: &[u8; CHUNK], right: &[u8; CHUNK]| {
        let difference = u128::from_le_bytes(*left) ^ u128::from_le_bytes(*right);
        let index = difference.trailing_zeros() as usize / 8 % CHUNK;
        (difference != 0).then(|| (left[index], right[index]))
    };
    let (left, right) = (a.as_bytes(), b.as_bytes());
    let common_length = left.len().min(right.len());
    let (left_prefix, right_prefix) = (&left[..common_length], &right[..common_length]);
    let mut difference = left_prefix
        .as_chunks::<CHUNK>()
        .0
        .iter()
        .zip(right_prefix.as_chunks::<CHUNK>().0)
        .find_map(|(left_chunk, right_chunk)| first_difference(left_chunk, right_chunk));
    if difference.is_none() && common_length % CHUNK != 0 {
        difference = match (
            left_prefix.last_chunk::<CHUNK>(),
            right_prefix.last_chunk::<CHUNK>(),
        ) {
            (Some(left_chunk), Some(right_chunk)) => first_difference(left_chunk, right_chunk),
            _ => left_prefix
                .iter()
                .zip(right_prefix)
                .find(|(left_byte, right_byte)| left_byte != right_byte)
                .map(|(left_byte, right_byte)| (*left_byte, *right_byte)),
        };
    }
    match difference {
        Some((left_byte, right_byte)) => rank(left_byte).cmp(&rank(right_byte)),
        None => left.len().cmp(&right.len()),
    }
}

/// bytes_ret, plus two overlapping 8-byte windows for common lengths of 8-15.
#[inline(never)]
pub fn bytes_ret8(a: &str, b: &str) -> Ordering {
    const CHUNK: usize = size_of::<u128>();
    let first_difference = |left: &[u8; CHUNK], right: &[u8; CHUNK]| {
        let difference = u128::from_le_bytes(*left) ^ u128::from_le_bytes(*right);
        let index = difference.trailing_zeros() as usize / 8 % CHUNK;
        (difference != 0).then(|| (left[index], right[index]))
    };
    let first_difference_8 = |left: &[u8; 8], right: &[u8; 8]| {
        let difference = u64::from_le_bytes(*left) ^ u64::from_le_bytes(*right);
        let index = difference.trailing_zeros() as usize / 8 % 8;
        (difference != 0).then(|| (left[index], right[index]))
    };
    let (left, right) = (a.as_bytes(), b.as_bytes());
    let common_length = left.len().min(right.len());
    let (left_prefix, right_prefix) = (&left[..common_length], &right[..common_length]);
    let mut difference = left_prefix
        .as_chunks::<CHUNK>()
        .0
        .iter()
        .zip(right_prefix.as_chunks::<CHUNK>().0)
        .find_map(|(left_chunk, right_chunk)| first_difference(left_chunk, right_chunk));
    if difference.is_none() && common_length % CHUNK != 0 {
        difference = if let (Some(l), Some(r)) =
            (left_prefix.last_chunk::<CHUNK>(), right_prefix.last_chunk::<CHUNK>())
        {
            first_difference(l, r)
        } else if let (Some(lf), Some(rf), Some(ll), Some(rl)) = (
            left_prefix.first_chunk::<8>(),
            right_prefix.first_chunk::<8>(),
            left_prefix.last_chunk::<8>(),
            right_prefix.last_chunk::<8>(),
        ) {
            first_difference_8(lf, rf).or_else(|| first_difference_8(ll, rl))
        } else {
            left_prefix
                .iter()
                .zip(right_prefix)
                .find(|(left_byte, right_byte)| left_byte != right_byte)
                .map(|(left_byte, right_byte)| (*left_byte, *right_byte))
        };
    }
    match difference {
        Some((left_byte, right_byte)) => rank(left_byte).cmp(&rank(right_byte)),
        None => left.len().cmp(&right.len()),
    }
}

/// Portable bitmask of differing bytes, hoping LLVM emits pcmpeqb/pmovmskb.
#[inline(never)]
pub fn mask(a: &str, b: &str) -> Ordering {
    const CHUNK: usize = 16;
    let first_difference = |left: &[u8; CHUNK], right: &[u8; CHUNK]| {
        let mut mask = 0u32;
        for index in 0..CHUNK {
            mask |= u32::from(left[index] != right[index]) << index;
        }
        let index = mask.trailing_zeros() as usize % CHUNK;
        (mask != 0).then(|| (left[index], right[index]))
    };
    let (left, right) = (a.as_bytes(), b.as_bytes());
    let common_length = left.len().min(right.len());
    let (left_prefix, right_prefix) = (&left[..common_length], &right[..common_length]);
    let mut difference = left_prefix
        .as_chunks::<CHUNK>()
        .0
        .iter()
        .zip(right_prefix.as_chunks::<CHUNK>().0)
        .find_map(|(left_chunk, right_chunk)| first_difference(left_chunk, right_chunk));
    if difference.is_none() && common_length % CHUNK != 0 {
        difference = match (
            left_prefix.last_chunk::<CHUNK>(),
            right_prefix.last_chunk::<CHUNK>(),
        ) {
            (Some(left_chunk), Some(right_chunk)) => first_difference(left_chunk, right_chunk),
            _ => left_prefix
                .iter()
                .zip(right_prefix)
                .find(|(left_byte, right_byte)| left_byte != right_byte)
                .map(|(left_byte, right_byte)| (*left_byte, *right_byte)),
        };
    }
    match difference {
        Some((left_byte, right_byte)) => rank(left_byte).cmp(&rank(right_byte)),
        None => left.len().cmp(&right.len()),
    }
}

#[inline(always)]
fn template(
    a: &str,
    b: &str,
    first_difference: impl Fn(&[u8; 16], &[u8; 16]) -> Option<(u8, u8)>,
) -> Ordering {
    let (left, right) = (a.as_bytes(), b.as_bytes());
    let common_length = left.len().min(right.len());
    let (left_prefix, right_prefix) = (&left[..common_length], &right[..common_length]);
    let mut difference = left_prefix
        .as_chunks::<16>()
        .0
        .iter()
        .zip(right_prefix.as_chunks::<16>().0)
        .find_map(|(left_chunk, right_chunk)| first_difference(left_chunk, right_chunk));
    if difference.is_none() && common_length % 16 != 0 {
        difference = if let (Some(l), Some(r)) =
            (left_prefix.last_chunk::<16>(), right_prefix.last_chunk::<16>())
        {
            first_difference(l, r)
        } else if let (Some(lf), Some(rf), Some(ll), Some(rl)) = (
            left_prefix.first_chunk::<8>(),
            right_prefix.first_chunk::<8>(),
            left_prefix.last_chunk::<8>(),
            right_prefix.last_chunk::<8>(),
        ) {
            let first_difference_8 = |left: &[u8; 8], right: &[u8; 8]| {
                let difference = u64::from_le_bytes(*left) ^ u64::from_le_bytes(*right);
                let index = difference.trailing_zeros() as usize / 8 % 8;
                (difference != 0).then(|| (left[index], right[index]))
            };
            first_difference_8(lf, rf).or_else(|| first_difference_8(ll, rl))
        } else {
            left_prefix
                .iter()
                .zip(right_prefix)
                .find(|(left_byte, right_byte)| left_byte != right_byte)
                .map(|(left_byte, right_byte)| (*left_byte, *right_byte))
        };
    }
    match difference {
        Some((left_byte, right_byte)) => rank(left_byte).cmp(&rank(right_byte)),
        None => left.len().cmp(&right.len()),
    }
}

#[inline(always)]
fn mismatch_loop(left: &[u8; 16], right: &[u8; 16]) -> u32 {
    let mut mask = 0;
    for index in 0..16 {
        mask |= u32::from(left[index] != right[index]) << index;
    }
    mask
}

#[inline(never)]
pub fn mask_a(a: &str, b: &str) -> Ordering {
    template(a, b, |left, right| {
        let mask = mismatch_loop(left, right);
        let index = mask.trailing_zeros() as usize % 16;
        (mask != 0).then(|| (left[index], right[index]))
    })
}

#[inline(never)]
pub fn mask_b(a: &str, b: &str) -> Ordering {
    template(a, b, |left, right| {
        let mask = left
            .iter()
            .zip(right)
            .enumerate()
            .fold(0u32, |mask, (index, (l, r))| mask | (u32::from(l == r) << index));
        let index = (!mask).trailing_zeros() as usize % 16;
        (mask != 0xFFFF).then(|| (left[index], right[index]))
    })
}

/// u128 equality to skip chunks, then two u64 halves for the index.
#[inline(never)]
pub fn halves(a: &str, b: &str) -> Ordering {
    template(a, b, |left, right| {
        if left == right {
            return None;
        }
        let (left_low, left_high) = left.split_first_chunk::<8>().unwrap();
        let (right_low, right_high) = right.split_first_chunk::<8>().unwrap();
        let low = u64::from_le_bytes(*left_low) ^ u64::from_le_bytes(*right_low);
        let index = if low != 0 {
            low.trailing_zeros() as usize / 8
        } else {
            let high = u64::from_le_bytes(left_high.try_into().unwrap())
                ^ u64::from_le_bytes(right_high.try_into().unwrap());
            8 + high.trailing_zeros() as usize / 8
        } % 16;
        Some((left[index], right[index]))
    })
}

/// bytes_ret8 through the template, as a control for the template itself.
#[inline(never)]
pub fn xor_t(a: &str, b: &str) -> Ordering {
    template(a, b, |left, right| {
        let difference = u128::from_le_bytes(*left) ^ u128::from_le_bytes(*right);
        let index = difference.trailing_zeros() as usize / 8 % 16;
        (difference != 0).then(|| (left[index], right[index]))
    })
}

/// Finds the byte by position after an equality test.
#[inline(never)]
pub fn eq_pos(a: &str, b: &str) -> Ordering {
    template(a, b, |left, right| {
        if left == right {
            return None;
        }
        left.iter().zip(right).find(|(l, r)| l != r).map(|(l, r)| (*l, *r))
    })
}

#[inline(always)]
fn mask_first_difference(left: &[u8; 16], right: &[u8; 16]) -> Option<(u8, u8)> {
    let equal = left
        .iter()
        .zip(right)
        .enumerate()
        .fold(0u32, |mask, (index, (l, r))| mask | (u32::from(l == r) << index));
    let index = (!equal).trailing_zeros() as usize % 16;
    (equal != 0xFFFF).then(|| (left[index], right[index]))
}

#[inline(never)]
pub fn mask_c(a: &str, b: &str) -> Ordering {
    template(a, b, mask_first_difference)
}

#[inline(never)]
pub fn mask_d(a: &str, b: &str) -> Ordering {
    let first_difference = mask_first_difference;
    let (left, right) = (a.as_bytes(), b.as_bytes());
    let common_length = left.len().min(right.len());
    let (left_prefix, right_prefix) = (&left[..common_length], &right[..common_length]);
    let mut difference = left_prefix
        .as_chunks::<16>()
        .0
        .iter()
        .zip(right_prefix.as_chunks::<16>().0)
        .find_map(|(left_chunk, right_chunk)| first_difference(left_chunk, right_chunk));
    if difference.is_none() && common_length % 16 != 0 {
        difference = if let (Some(l), Some(r)) =
            (left_prefix.last_chunk::<16>(), right_prefix.last_chunk::<16>())
        {
            first_difference(l, r)
        } else if let (Some(lf), Some(rf), Some(ll), Some(rl)) = (
            left_prefix.first_chunk::<8>(),
            right_prefix.first_chunk::<8>(),
            left_prefix.last_chunk::<8>(),
            right_prefix.last_chunk::<8>(),
        ) {
            let first_difference_8 = |left: &[u8; 8], right: &[u8; 8]| {
                let difference = u64::from_le_bytes(*left) ^ u64::from_le_bytes(*right);
                let index = difference.trailing_zeros() as usize / 8 % 8;
                (difference != 0).then(|| (left[index], right[index]))
            };
            first_difference_8(lf, rf).or_else(|| first_difference_8(ll, rl))
        } else {
            left_prefix
                .iter()
                .zip(right_prefix)
                .find(|(left_byte, right_byte)| left_byte != right_byte)
                .map(|(left_byte, right_byte)| (*left_byte, *right_byte))
        };
    }
    match difference {
        Some((left_byte, right_byte)) => rank(left_byte).cmp(&rank(right_byte)),
        None => left.len().cmp(&right.len()),
    }
}


#[inline(never)]
pub fn xor_d(a: &str, b: &str) -> Ordering {
    let first_difference = xor_first_difference;
    let (left, right) = (a.as_bytes(), b.as_bytes());
    let common_length = left.len().min(right.len());
    let (left_prefix, right_prefix) = (&left[..common_length], &right[..common_length]);
    let mut difference = left_prefix
        .as_chunks::<16>()
        .0
        .iter()
        .zip(right_prefix.as_chunks::<16>().0)
        .find_map(|(left_chunk, right_chunk)| first_difference(left_chunk, right_chunk));
    if difference.is_none() && common_length % 16 != 0 {
        difference = if let (Some(l), Some(r)) =
            (left_prefix.last_chunk::<16>(), right_prefix.last_chunk::<16>())
        {
            first_difference(l, r)
        } else if let (Some(lf), Some(rf), Some(ll), Some(rl)) = (
            left_prefix.first_chunk::<8>(),
            right_prefix.first_chunk::<8>(),
            left_prefix.last_chunk::<8>(),
            right_prefix.last_chunk::<8>(),
        ) {
            let first_difference_8 = |left: &[u8; 8], right: &[u8; 8]| {
                let difference = u64::from_le_bytes(*left) ^ u64::from_le_bytes(*right);
                let index = difference.trailing_zeros() as usize / 8 % 8;
                (difference != 0).then(|| (left[index], right[index]))
            };
            first_difference_8(lf, rf).or_else(|| first_difference_8(ll, rl))
        } else {
            left_prefix
                .iter()
                .zip(right_prefix)
                .find(|(left_byte, right_byte)| left_byte != right_byte)
                .map(|(left_byte, right_byte)| (*left_byte, *right_byte))
        };
    }
    match difference {
        Some((left_byte, right_byte)) => rank(left_byte).cmp(&rank(right_byte)),
        None => left.len().cmp(&right.len()),
    }
}


#[inline(always)]
fn xor_first_difference(left: &[u8; 16], right: &[u8; 16]) -> Option<(u8, u8)> {
    let difference = u128::from_le_bytes(*left) ^ u128::from_le_bytes(*right);
    let index = difference.trailing_zeros() as usize / 8 % 16;
    (difference != 0).then(|| (left[index], right[index]))
}

macro_rules! chunked {
    ($name:ident, $first_difference:path) => {
        #[inline(never)]
        pub fn $name(a: &str, b: &str) -> Ordering {
            let first_difference = $first_difference;
            let (left, right) = (a.as_bytes(), b.as_bytes());
            let common_length = left.len().min(right.len());
            let (left_prefix, right_prefix) = (&left[..common_length], &right[..common_length]);
            let mut difference = left_prefix
                .as_chunks::<16>()
                .0
                .iter()
                .zip(right_prefix.as_chunks::<16>().0)
                .find_map(|(left_chunk, right_chunk)| first_difference(left_chunk, right_chunk));
            if difference.is_none() && common_length % 16 != 0 {
                difference = if let (Some(l), Some(r)) =
                    (left_prefix.last_chunk::<16>(), right_prefix.last_chunk::<16>())
                {
                    first_difference(l, r)
                } else if let (Some(lf), Some(rf), Some(ll), Some(rl)) = (
                    left_prefix.first_chunk::<8>(),
                    right_prefix.first_chunk::<8>(),
                    left_prefix.last_chunk::<8>(),
                    right_prefix.last_chunk::<8>(),
                ) {
                    let first_difference_8 = |left: &[u8; 8], right: &[u8; 8]| {
                        let difference = u64::from_le_bytes(*left) ^ u64::from_le_bytes(*right);
                        let index = difference.trailing_zeros() as usize / 8 % 8;
                        (difference != 0).then(|| (left[index], right[index]))
                    };
                    first_difference_8(lf, rf).or_else(|| first_difference_8(ll, rl))
                } else {
                    left_prefix
                        .iter()
                        .zip(right_prefix)
                        .find(|(left_byte, right_byte)| left_byte != right_byte)
                        .map(|(left_byte, right_byte)| (*left_byte, *right_byte))
                };
            }
            match difference {
                Some((left_byte, right_byte)) => rank(left_byte).cmp(&rank(right_byte)),
                None => left.len().cmp(&right.len()),
            }
        }
    };
}

#[inline(always)]
fn eqmask_first_difference(left: &[u8; 16], right: &[u8; 16]) -> Option<(u8, u8)> {
    if left == right {
        return None;
    }
    let equal = left
        .iter()
        .zip(right)
        .enumerate()
        .fold(0u32, |mask, (index, (l, r))| mask | (u32::from(l == r) << index));
    let index = (!equal).trailing_zeros() as usize % 16;
    Some((left[index], right[index]))
}
chunked!(eqmask, eqmask_first_difference);

macro_rules! chunked4 {
    ($name:ident, $first_difference:path) => {
        #[inline(never)]
        pub fn $name(a: &str, b: &str) -> Ordering {
            let first_difference = $first_difference;
            let (left, right) = (a.as_bytes(), b.as_bytes());
            let common_length = left.len().min(right.len());
            let (left_prefix, right_prefix) = (&left[..common_length], &right[..common_length]);
            let mut difference = left_prefix
                .as_chunks::<16>()
                .0
                .iter()
                .zip(right_prefix.as_chunks::<16>().0)
                .find_map(|(left_chunk, right_chunk)| first_difference(left_chunk, right_chunk));
            if difference.is_none() && common_length % 16 != 0 {
                difference = if let (Some(l), Some(r)) =
                    (left_prefix.last_chunk::<16>(), right_prefix.last_chunk::<16>())
                {
                    first_difference(l, r)
                } else if let (Some(lf), Some(rf), Some(ll), Some(rl)) = (
                    left_prefix.first_chunk::<8>(),
                    right_prefix.first_chunk::<8>(),
                    left_prefix.last_chunk::<8>(),
                    right_prefix.last_chunk::<8>(),
                ) {
                    let first_difference_8 = |left: &[u8; 8], right: &[u8; 8]| {
                        let difference = u64::from_le_bytes(*left) ^ u64::from_le_bytes(*right);
                        let index = difference.trailing_zeros() as usize / 8 % 8;
                        (difference != 0).then(|| (left[index], right[index]))
                    };
                    first_difference_8(lf, rf).or_else(|| first_difference_8(ll, rl))
                } else if let (Some(lf), Some(rf), Some(ll), Some(rl)) = (
                    left_prefix.first_chunk::<4>(),
                    right_prefix.first_chunk::<4>(),
                    left_prefix.last_chunk::<4>(),
                    right_prefix.last_chunk::<4>(),
                ) {
                    let first_difference_4 = |left: &[u8; 4], right: &[u8; 4]| {
                        let difference = u32::from_le_bytes(*left) ^ u32::from_le_bytes(*right);
                        let index = difference.trailing_zeros() as usize / 8 % 4;
                        (difference != 0).then(|| (left[index], right[index]))
                    };
                    first_difference_4(lf, rf).or_else(|| first_difference_4(ll, rl))
                } else {
                    left_prefix
                        .iter()
                        .zip(right_prefix)
                        .find(|(left_byte, right_byte)| left_byte != right_byte)
                        .map(|(left_byte, right_byte)| (*left_byte, *right_byte))
                };
            }
            match difference {
                Some((left_byte, right_byte)) => rank(left_byte).cmp(&rank(right_byte)),
                None => left.len().cmp(&right.len()),
            }
        }
    };
}


chunked4!(mask4, mask_first_difference);
chunked4!(xor4w, xor_first_difference);
