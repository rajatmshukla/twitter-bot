#!/usr/bin/env python3
"""Regression test for DEAD_MOVE_RE and reply_guy_direct.gate().

Validates that:
1. Idiom / dead-move takes (TEXTS_A) are rejected by both DEAD_MOVE_RE and gate().
2. Legitimate takes (TEXTS_B) pass gate().
3. Technical engineering replies with bare 'work' and substantive 'the work' (TEXTS_C) pass gate().
4. A clean engineering corpus of >= 30 replies (including work-stealing,
   worker-pool, query-planner, and GC-thread phrasing) has a 0% false positive rate.

Zero external dependencies, no network, no browser, no side effects on logs or state.
"""
import os
import sys

BOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BOT_DIR not in sys.path:
    sys.path.insert(0, BOT_DIR)

import reply_guy as rg
import reply_guy_direct as rgd

ESCAPE_VARIANTS = [
    'that phrase is doing some work.',
    'the word "efficient" is doing so much work here.',
    'the phrase "state of the art" is doing a ton of work.',
    'that claim is doing a huge amount of work.',
    'that framing is doing an enormous amount of work.',
]

TEXTS_A = [
    '"first attempt" is doing a lot of work there. Still, very cool to see the "in-context" part scale to physical space like this.',
    '"Could actually help humanity in practice" is doing a lot of heavy lifting.',
    'That "over 5 times faster" is doing a lot of work there.',
    '"rumors" doing a lot of heavy lifting here.',
    'the word "nerfed" is doing a lot of work.',
    "'at least for now' has been doing heavy lifting for a year",
    'the byte-stable prefix is the real trick. the cache does the heavy lifting.',
    '"game changer" is doing a lot of work for a pricing announcement.',
    'the ellipses are doing a lot of emotional heavy lifting there',
    "claude cowork is the honest name. like a coworker, it does the work while you're in another meeting, and you find out when it's too late to object.",
    'the worker is doing the work of two threads',
] + ESCAPE_VARIANTS

TEXTS_B = [
    '$100 a seat, and the $20 plan turns out to have been the demo. that is the whole announcement.',
    'same bytes every call means the prefix cache hits every time. that is the entire cost story, and most people are still benchmarking the model.',
    'the evals are real. what is missing is a deployment: how many places, for how long, at what error rate.',
    'show me the eval set and the config and i will stop asking.',
    'if the for-now keeps winning through the next release, the pricing page moves. happy to be wrong before then.',
    'a benchmark account hyping a model is a new one. a tenth of a point is inside run-to-run noise on most of these.',
]

SUBSTANTIVE_TAKES = [
]

TEXTS_C = [
    'how does work stealing perform under NUMA topology with 64 cores?',
    'the worker does work in batches to minimize mutex contention.',
    'speculative decoding does work on draft tokens that get rejected 30% of the time.',
    'the team is doing work on formal verification for the crypto primitives.',
    'the runtime has done work to stabilize thread affinity across NUMA nodes.',
] + SUBSTANTIVE_TAKES

CLEAN_ENGINEERING_CORPUS = [
    # Work-stealing phrasing
    'how does work stealing perform under NUMA topology with 64 cores?',
    'work-stealing schedulers balance queue depths across executor threads.',
    'our work-stealing pool keeps all cores saturated during tree search.',
    'a deque per thread allows work stealing with minimal atomic CAS operations.',
    # Worker-pool phrasing
    'the worker pool does work only when new tasks are dequeued.',
    'our worker pool dispatches work across pinned hardware threads.',
    'sizing the worker pool to physical cores eliminates context switch overhead.',
    'the worker pool flushes pending writes to disk before shutdown.',
    # Query-planner phrasing
    'the query planner does work ahead of time to cache execution plans.',
    'a cost-based query planner does work to eliminate redundant joins.',
    'the query planner estimates cardinalities from column histograms.',
    'pushdown predicates let the query planner prune partitions early.',
    # GC-thread phrasing
    'the GC thread does work concurrently without stopping the world.',
    'incremental GC threads do work during low allocation phases.',
    'pinning GC threads to separate cores avoids latency spikes in request handlers.',
    'generational GC threads do work on young gen objects without global locks.',
    # General systems, compiler, hardware, networking & AI engineering prose
    'the worker does work in batches to minimize mutex contention.',
    'speculative decoding does work on draft tokens that get rejected 30% of the time.',
    'the team is doing work on formal verification for the crypto primitives.',
    'the runtime has done work to stabilize thread affinity across NUMA nodes.',
    'does work distribution across shards handle hot keys gracefully?',
    'our background thread does work only when the ring buffer is half full.',
    'we let the GPU kernel do work asynchronously while host memory is pinned.',
    'we are doing work to reduce the cold start time below 50ms.',
    'daemons that do work in user space avoid costly syscall context switches.',
    'the prefix cache hits on identical token prefixes, but KV compression destroys that guarantee.',
    'p99 latency is dominated by queue depth, not the model forward pass itself.',
    'speculative decoding only saves wall-clock time if verification runs in parallel on SRAM.',
    'batch size 1 gives lowest latency, but batch size 16 gives 8x higher token throughput per dollar.',
    'their benchmark compares FP16 baseline against INT4 with dynamic activation quantization.',
    'the kernel bypass driver saves 4 microseconds per packet by avoiding sk_buff allocation.',
    'running continuous batching requires pre-allocating KV blocks or memory fragmentation will kill you.',
    'context distillation loses fine-grained reasoning across multi-step proofs.',
    'the bottleneck here is PCIe bandwidth between host RAM and GPU memory, not compute.',
    'if you look at the roofline model, this kernel is purely memory-bandwidth bound on H100.',
    'the evals don\'t show prompt injection resistance or jailbreak transferability.',
    'checkpointing every 100 steps on NFS was causing metadata lock contention on the cluster.',
    'distributed sharding with tensor parallelism requires NVLink; Infiniband is too slow for intra-node.',
    'the token vocabulary size directly impacts the final softmax projection layer latency.',
    'page table walks during TLB misses account for nearly 12% of cycles in this trace.',
]


def run_tests():
    """Run regression tests validating DEAD_MOVE_RE and reply_guy_direct.gate().

    Checks dead-move idiom rejection, legitimate text acceptance, and false positive rates.
    """
    errors = []

    # 1. Assert every TEXTS_A item is rejected by both DEAD_MOVE_RE and gate()
    for idx, text in enumerate(TEXTS_A, 1):
        if not rg.DEAD_MOVE_RE.search(text):
            errors.append(f"TEXTS_A[{idx}] was NOT matched by DEAD_MOVE_RE: {text!r}")
        gated = rgd.gate(text)
        if gated is not None:
            errors.append(f"TEXTS_A[{idx}] was NOT rejected by gate() (returned {gated!r}): {text!r}")

    # 2. Assert every TEXTS_B item passes gate()
    for idx, text in enumerate(TEXTS_B, 1):
        if rg.DEAD_MOVE_RE.search(text):
            errors.append(f"TEXTS_B[{idx}] was wrongly matched by DEAD_MOVE_RE: {text!r}")
        gated = rgd.gate(text)
        if gated is None:
            errors.append(f"TEXTS_B[{idx}] failed gate(): {text!r}")

    # 3. Assert every TEXTS_C item passes gate()
    for idx, text in enumerate(TEXTS_C, 1):
        if rg.DEAD_MOVE_RE.search(text):
            errors.append(f"TEXTS_C[{idx}] was wrongly matched by DEAD_MOVE_RE: {text!r}")
        gated = rgd.gate(text)
        if gated is None:
            errors.append(f"TEXTS_C[{idx}] failed gate(): {text!r}")

    # 4. Self-check clean engineering corpus (at least 30 replies)
    if len(CLEAN_ENGINEERING_CORPUS) < 30:
        errors.append(f"Clean engineering corpus must contain at least 30 items, found {len(CLEAN_ENGINEERING_CORPUS)}")

    fp_count = 0
    for idx, text in enumerate(CLEAN_ENGINEERING_CORPUS, 1):
        m = rg.DEAD_MOVE_RE.search(text)
        gated = rgd.gate(text)
        if m or gated is None:
            fp_count += 1
            errors.append(f"Clean corpus #{idx} false positive (DEAD_MOVE_RE={bool(m)}, gate={gated is not None}): {text!r}")

    if errors:
        for err in errors:
            print(f"FAIL: {err}", file=sys.stderr)
        print(f"FAIL: {len(errors)} error(s) found", file=sys.stderr)
        return 1

    print(
        f"PASS: all {len(TEXTS_A)} TEXTS_A rejected by DEAD_MOVE_RE & gate(), "
        f"all {len(TEXTS_B)} TEXTS_B passed gate(), "
        f"all {len(TEXTS_C)} TEXTS_C passed gate(), "
        f"clean corpus {len(CLEAN_ENGINEERING_CORPUS)} replies FP rate 0.0% (0/{len(CLEAN_ENGINEERING_CORPUS)})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(run_tests())
