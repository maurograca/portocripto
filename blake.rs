//use ashmaize::{hash, Rom, RomGenerationType};
use blake2::Blake2bVar;
use blake2::digest::{Update, VariableOutput};
//use ashmaize::{blake2, Rom, RomGenerationType};
use clap::Parser;
use rayon::prelude::*;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;

const NUM_THREADS: usize = 8;
pub const MB: usize = 1024 * 1024;
pub const GB: usize = 1024 * MB;

#[derive(Parser, Debug)]
#[command(author, version, about, long_about = None)]
struct Args {
    #[arg(long)]
    address: String,
    #[arg(long)]
    challenge_id: String,
    #[arg(long)]
    difficulty: String, // This is a hexadecimal string representing the bitmask for the required zero prefix
    #[arg(long)]
    no_pre_mine: String,
    #[arg(long)]
    latest_submission: String,
    #[arg(long)]
    no_pre_mine_hour: String,
}

pub fn hash_structure_good(hash: &[u8], difficulty_mask: u32) -> bool {
    if hash.len() < 4 {
        return false; // Not enough bytes to apply a u32 mask
    }

    let hash_prefix = u32::from_be_bytes([hash[0], hash[1], hash[2], hash[3]]);
    (hash_prefix & !difficulty_mask) == 0
}

//pub fn init_rom(no_pre_mine_hex: &str) -> Rom {
//   Rom::new(
//        no_pre_mine_hex.as_bytes(),
//        RomGenerationType::TwoStep {
//            pre_size: 16 * MB,
//            mixing_numbers: 4,
//        },
//        1 * GB,
//    )
//}

fn main() {
    let args = Args::parse();

    // Initialize AshMaize ROM
    //let rom = init_rom(&args.no_pre_mine);
    //let rom = Arc::new(init_rom(&args.no_pre_mine));

    // Parse difficulty from hex string to u32 mask
    let difficulty_mask = u32::from_str_radix(&args.difficulty, 16).unwrap();

    // Compute suffix once
    let suffix = format!(
        "{}{}{}{}{}{}",
        args.address,
        args.challenge_id,
        args.difficulty,
        args.no_pre_mine,
        args.latest_submission,
        args.no_pre_mine_hour
    );

    // Share ROM across threads (read-only, no mutex needed)
    //let rom = Arc::new(rom);

    let found = Arc::new(AtomicBool::new(false));
    let result_nonce = Arc::new(AtomicU64::new(0));
    let start_nonce = 0u64;

    (0..NUM_THREADS).into_par_iter().for_each(|thread_id| {
        //let rom = Arc::clone(&rom);
        let found = Arc::clone(&found);
        let result_nonce = Arc::clone(&result_nonce);
        let mut local_nonce = start_nonce + thread_id as u64;
        let stride = NUM_THREADS as u64;

        // Reuse preimage buffer across iterations
        let mut preimage = String::with_capacity(16 + suffix.len());

        let mut output = vec![0u8;32];
        while !found.load(Ordering::Acquire) {
            preimage.clear();
            use std::fmt::Write;
            write!(&mut preimage, "{:016x}{}", local_nonce, &suffix).unwrap();

            // Each hash call allocates ~15-20KB temporarily
            //let hash_result = hash(preimage.as_bytes(), &rom, 8, 256);
            let mut hasher = Blake2bVar::new(32).unwrap();
            //    .hash_length(32)
            //   .key(&rom)
            //    .to_state();

            hasher.update(preimage.as_bytes());
            let mut output = vec![0u8;32];
            hasher.finalize_variable(&mut output).unwrap();
            //let hash_result = out;

            if hash_structure_good(&output, difficulty_mask) {
                found.store(true, Ordering::Release);
                result_nonce.store(local_nonce, Ordering::Release);
                break;
            }

            local_nonce += stride;
        }
    });

    if found.load(Ordering::Acquire) {
        println!("{:016x}", result_nonce.load(Ordering::Acquire));
    }
}
