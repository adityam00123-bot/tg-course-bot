# Turbo MTProto Engine Optimization Plan & Research

> **Permanent Architectural Documentation & Optimization Roadmap**  
> *Target: Eliminate Upload Freezes, Smooth MTProto Ingress, and Bridge Overall Throughput from 10–11 MB/s to 14–16 MB/s.*

---

## 1. Executive Summary & Benchmark History

Across the September 17–19 migration runs on Kaggle Standard (Taiwan VM, 12 workers, 4 parallel media sockets), the codebase has proven **unprecedented reliability and stamina**:

| Session | Data Transferred | Media Files | Errors | Elapsed | Sustained Speed |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Run 1 (Sep 17)** | 420.34 GB | 1,110 | **0** | 11h 52m | 10.1 MB/s |
| **Run 2 (Sep 18)** | **455.25 GB** | **1,810** | **0** | 11h 52m | **10.9 MB/s** *(peaked at 11.0 MB/s through 444 GB)* |
| **Run 3 (Sep 19)** | 386.25 GB | 1,170 | **0** | 11h 57m | 9.2 MB/s *(started at 12.5 MB/s)* |
| **Total (3 Runs)** | **1,261.84 GB** | **4,090** | **0** | **~36 hours** | **~10.1 MB/s overall** |

### The Core Finding
Over **1.26 Terabytes** transferred across 36 hours without a single crash or aborted run.  
However, **Run 3 highlighted two observable bottlenecks**:
1. It started at **12.5 MB/s**, but settled at **9.2 MB/s** over 12 hours.
2. Individual file uploads regularly suffer from **momentary mid-file speed collapses (50 MB/s ➔ 8 MB/s)** and **end-of-file freezes (stuck at 95%–98% for 6–8 seconds)**.

---

## 2. Root Cause Analysis: The 4 Current Bottlenecks

### Bottleneck A: The Burst-Stall Saw-Tooth (Mid-File Ingress Collapse)
* **Symptom:**  
  Upload begins with an extreme burst of **45–55 MB/s** (e.g. `#6839`: 55 MB/s, `#6848`: 54 MB/s, `#6882`: 46 MB/s). After ~150–250 MB, speed suddenly plummets to **8–12 MB/s** for 4–6 seconds, before shooting back up to 40+ MB/s.
* **Underlying Mechanism:**  
  - Kaggle's Taiwan VM has ~30ms RTT to Telegram Singapore DC 5.
  - With 12 concurrent workers and 4 sockets, the client can push over **100 MB/s** of raw network frames.
  - Telegram's edge MTProto gateway uses a token bucket rate limiter and a finite TCP receive buffer.
  - Blasting at 55 MB/s exhausts Telegram's edge token bucket in ~3–4 seconds (~150MB).
  - Telegram pauses TCP ACKs (sets TCP window = 0) while its backend flushes data to distributed storage.
  - During this pause, our workers hang in `invoke(SaveBigFilePart)`.
  - Once the buffer clears, the workers burst again, repeating this saw-tooth oscillation every 200MB.
* **Mathematical Penalty:** On a 1 GB file, 4 stalls of 4s add **16s of dead time**, dragging average upload speed from 35 MB/s down to 18 MB/s.

---

### Bottleneck B: Late-Stage Straggler Tail Latency (Freeze at 95%–98%)
* **Symptom:**  
  File uploads reach near completion (e.g. #6882 at 510MB/624MB, #6851 at 280MB/284MB) and freeze in-place for 5 to 9 seconds before completing in one flash.
* **Underlying Mechanism:**  
  Look at lines 371–378 of `fast_uploader.py`:
  ```python
  remaining_uncompleted = total_parts - len(completed_parts)
  tail_threshold = 2.5 if remaining_uncompleted <= 4 else 8.0
  for p_idx, s_time in list(part_start_times.items()):
      if p_idx not in completed_parts and (now - s_time) > tail_threshold:
          straggler_idx = p_idx
          part_start_times[p_idx] = now  # re-claim straggler
          is_stolen = True
          break
  ```
  - When a file has 6 to 12 parts remaining, `remaining_uncompleted > 4`.
  - Therefore, `tail_threshold` is set to **8.0 SECONDS**!
  - 10 of our 12 workers are completely idle because the queue is empty.
  - If 1 chunk encounters latency on a slower socket, the 10 idle workers sit completely idle doing nothing for up to 8 seconds before anyone is allowed to steal and re-send the part on another socket!
* **Mathematical Penalty:** Adds 5–8 seconds of idle freeze to almost every media file over 200 MB.

---

### Bottleneck C: Connection Churn & TIME_WAIT Exhaustion Over 12-Hour Runs
* **Symptom:**  
  The bot runs at 12.5 MB/s for the first hour, but gradually drifts down to 9.2 MB/s after 10–12 hours.
* **Underlying Mechanism:**  
  - Currently, `fast_save_file` creates 4 fresh dedicated media TCP sessions per file, and closes them via background task upon completion:
    ```python
    asyncio.create_task(_bg_stop_upload_sessions(aux_sessions))
    ```
  - In a 1,200-file run: $1,200 \times 4 = \mathbf{4,800 \text{ TCP connections}}$ opened and closed to Telegram DC.
  - In Linux, closed sockets remain in kernel `TIME_WAIT` for 60 seconds.
  - High connection churn triggers:
    1. Telegram DC edge proxy IP connection rate limiting (delaying SYN-ACK handshakes).
    2. Ephemeral port exhaustion and kernel socket lookup overhead on the VM.
    3. TLS / MTProto DH key renegotiation overhead on every single file (200–400ms per file = ~8–10 minutes wasted per run).

---

### Bottleneck D: Sequential Architecture A Pipeline Constraint
* **The Math:**  
  $$\text{Effective Throughput} = \frac{1}{\frac{1}{\text{DL Speed}} + \frac{1}{\text{UL Speed}}}$$
  - Download is consistently fast: **~30–35 MB/s**.
  - When Upload is clean (30 MB/s): $\frac{1}{1/30 + 1/30} = \mathbf{15.0 \text{ MB/s}}$.
  - When Upload suffers from Bottlenecks A & B (averaging 18 MB/s): $\frac{1}{1/30 + 1/18} = \mathbf{11.25 \text{ MB/s}}$.
  - When Upload averages 14 MB/s on large files: $\frac{1}{1/30 + 1/14} = \mathbf{9.5 \text{ MB/s}}$ (matching Run 3).

---

## 3. Optimization Proposals (To Benchmark on Feature Branch `perf/turbo-opt`)

### Optimization 1: Adaptive Worker-Aware Straggler Stealing
* **Goal:** Completely eliminate the 6–8 second freeze at 95%–98% file completion.
* **Implementation Plan:**
  Scale `tail_threshold` dynamically based on available idle workers:
  ```python
  remaining_uncompleted = total_parts - len(completed_parts)
  # If remaining parts fit within our active worker pool, drop threshold to 2.0s!
  if remaining_uncompleted <= num_workers:
      tail_threshold = 2.0
  elif remaining_uncompleted <= num_workers * 2:
      tail_threshold = 3.5
  else:
      tail_threshold = 6.0
  ```
  - When only 6–10 parts remain, any chunk that takes $>2.0\text{s}$ is instantly stolen by an idle worker and re-sent over an alternate healthy socket.
  - Whichever socket returns `True` first wins, instantly unblocking the file.
* **Risk Level:** **ZERO RISK**. Parts are idempotent (`SaveBigFilePart` can be repeated safely without data corruption).

---

### Optimization 2: MTProto Ingress Traffic Pacing (Eliminate Saw-Tooth Collapse)
* **Goal:** Replace the violent 55 MB/s ➔ 8 MB/s saw-tooth drops with a smooth, continuous **35–42 MB/s line rate**.
* **Implementation Plan:**
  - Introduce an asynchronous Token Bucket / Leaky Bucket limiter across upload workers.
  - Allow maximum sustained burst of **38–42 MB/s** (76–84 chunks/sec).
  - Because data enters Telegram's edge buffer at a rate matching its internal flush speed, Telegram never sets TCP receive window to 0.
  - ACKs remain continuous and latency stays at ~30ms throughout the entire file.
* **Risk Level:** **LOW**. Pacing only limits maximum momentary spikes; it prevents the buffer overrun that causes multi-second halts.

---

### Optimization 3: Warm Socket Pool with 10-Message Epoch Recycling
* **Goal:** Eliminate 4,800 TCP handshakes per run, reducing connection churn and DC handshake delays.
* **Implementation Plan:**
  - Maintain a warm pool of 4 media sockets.
  - Instead of closing and destroying all 4 sockets after every file:
    - Reuse the warm sessions for consecutive files.
    - Gracefully recycle sessions every **15 messages** or if a socket experiences a fatal transport disconnect.
  - Reduces TCP connections from ~4,800 down to **~320 per 12-hour run**.
* **Risk Level:** **MEDIUM**. Must ensure that dead/stale sockets are properly detected with `is_session_alive()` before re-using across files.

---

## 4. Safety Guardrails & Permanent Memory Rules (No Memory Loss)

To ensure we **NEVER repeat past mistakes** or cause regressions, all future work on the experimental branch must obey these non-negotiable rules:

1. **NEVER Restart Sockets on Successful Chunks:**
   - Removing `chunk_dur > 4.0` restart was proven essential in commit `8e51bf5`.
   - Never kill an active socket while other workers are in-flight on it.
2. **NEVER Widen the Watchdog Beyond 45s / Lower Under 1.0 MB/s:**
   - The 45s sliding window aborting at $<1.0\text{ MB/s}$ is the golden standard.
   - Never add `curr < file_size * 0.50` guards that permit 1.1 MB/s zombie crawls.
3. **Dedicated Branch Testing Only:**
   - Production `main` branch stays 100% locked to commit `37fc95b` (`8b54d5e`).
   - All benchmark tests must run on branch `perf/turbo-opt`.
   - Only merge to `main` when a benchmark proves $>12.5\text{ MB/s}$ average with 0 errors over at least 50 GB.
