// Standalone test oracle: include the pinned, unmodified official Rust kernel.
// Not linked into the application. Fetch reference files as documented in
// tools/verify_fsrs7_oracle.py before compiling for wasm32-wasip1.
use std::io::{self, BufRead};
const S_MIN: f32 = 0.0001;
const S_MAX: f32 = 36500.0;
const D_MIN: f32 = 1.0;
const D_MAX: f32 = 10.0;
mod error {
    #[derive(Debug)] pub enum FSRSError { InvalidInput }
    pub type Result<T> = std::result::Result<T, FSRSError>;
}
mod defaults { include!("../build/fsrs7-reference/inference_v7.rs"); }
mod inference {
    pub use crate::defaults::DEFAULT_PARAMETERS;
    pub type Parameters = [f32];
    #[derive(Debug, Clone, Copy)]
    pub struct MemoryState { pub stability: f32, pub difficulty: f32, pub stability_fast: f32 }
}
mod model_v7 { include!("../build/fsrs7-reference/model_v7.rs"); }
fn main() {
    let w = defaults::DEFAULT_PARAMETERS;
    for line in io::stdin().lock().lines() {
        let x: Vec<f32> = line.unwrap().split_whitespace().map(|v| v.parse().unwrap()).collect();
        let rating = x[4] as usize;
        let old = inference::MemoryState { stability:x[0], difficulty:x[1], stability_fast:x[2] };
        let recall = if old.stability == 0.0 { 1.0 } else { model_v7::fsrs7_forgetting_curve_scalar_for_state(&w,x[3],old) };
        let state = if old.stability == 0.0 {
            let s = w[rating-1].clamp(S_MIN,S_MAX);
            inference::MemoryState { stability:s, difficulty: model_v7::init_difficulty_scalar(&w,rating).clamp(D_MIN,D_MAX), stability_fast:(s*0.8).clamp(S_MIN,S_MAX) }
        } else { model_v7::fsrs7_next_state_scalar(&w,old,x[3],rating) };
        let interval = model_v7::fsrs7_next_interval_scalar_for_state(&w,state,x[5]);
        println!("{} {} {} {} {}",state.stability,state.difficulty,state.stability_fast,recall,interval);
    }
}
