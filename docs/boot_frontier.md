# Boot frontier

**Status: measured** on the retail 1.0.2.1 package, Windows 11, RTX 4090, `LEONIDA_D3D12=vulkan`, with the shader corpus
in `LEONIDA_SHADERS` (26,555 game and XboxBvhBuilder modules plus the FSPX set that translates) and
`LEONIDA_SKIP_INT3=1` while the allocator assertion below is unmodelled.

This note records how far the boot currently gets and what stops it, so the next attempt starts from a measurement
rather than a guess. Counts, arities and error classes only; no package bytes or addresses.

## Where the run stops

| # | Stop | Class | Measured |
|---:|---|---|---|
| 1 | device slot 61 forwarded to PC `CreateMetaCommand` | wrong arity | fixed: refuses with E_NOTIMPL, logs both arguments |
| 2 | device slot 67 `DeserializeComputePipelineStateX` refused, 42 of 42 | missing translation | **live**: the 42 pipelines need FSPX programs among the 11 of 29 that do not translate yet |
| 3 | device slot 108 forwarded to the PC table | unknown shape | **current stop**: called with two arguments (an output pointer and a structure pointer) from 29 sites |

The run reaches **2,238 D3D12 calls** and stops at the third row.

Two facts about the first two rows matter for planning:

* The title does **not** survive a failed compute pipeline. Full slot tracing shows the run reaching device slot 62 once
  more after the 42 refusals and then calling through a null pointer (`access execute` at 0x0), so the FSPX gap stops
  the boot and is not merely a rendering problem. An earlier reading of the same run - that the title continued past
  the failures - came from the slot log without full tracing and was wrong.
* The lookup key is the SHA-256 of the kind-6 program bytes the title passes in the pipeline description, so a pipeline
  can only be created when that exact program has a translated module.
* The shader library loads `<dir>/index.json` and then the `fspx/` and `bvh/` indexes beneath it, so the builder's
  subdirectories are found as written. An earlier note here claimed the opposite; it was wrong.

## Outstanding assertion

With `LEONIDA_SKIP_INT3` unset the run stops earlier, on the title's own check that a computed size is at most
2 MiB - 1 (`cmp rax, 0x1fffff; jbe ...; int3`), in the same allocator region as the earlier memory stops. It is a soft
assertion: skipping it lets the boot run on into pipeline creation, so it is hiding progress rather than blocking it.

## Method notes

* A console slot's arity is measured from the title's own call site: scan `.text` for `ff 90 <slot*8 as disp32>` and
  disassemble the window before the call. Register probing alone misleads, because arguments the console does not pass
  are whatever the registers happened to hold.
* The console device vtable is longer than any PC one (the title calls slot 127, a one-argument method, from 45 sites).
  Slots past the PC table are console extensions: they are stubbed with S_OK / 0 and named in the log.
* A console heap address is a 64 KiB-aligned title VA; any other value where an extended description would carry one
  is a field that happens to sit there, and treating it as an address takes the heap out of the arena.

## The FSPX translation gap, measured

The 29 FidelityFX programs unpack and disassemble, and 18 translate. The 11 that do not are refused by the translator
with `fall-through into undecodable words`: a reachable instruction runs into words the disassembler skipped, so the
path's later stores and exports would be dropped without a word.

| program | bytes | undecodable words |
|---|---:|---:|
| 03 | 10,156 | 9 |
| 04 | 16,696 | 9 |
| 05 | 17,040 | 27 |
| 06 | 41,300 | 155 |
| 07 | 21,620 | **1** |
| 08 | 25,508 | 10 |
| 09 | 25,972 | 32 |
| 12 | 8,656 | 8 |
| 15 | 22,272 | 8 |
| 18 | 8,600 | 8 |
| 28 | 13,724 | 55 |

The skipped words sit in the listing as gaps: program 07 has one, at +0x4EE4 (`08 19 2c f4`); programs 12 and 18 have
eight each, all of the form `xx 50 16 cc` (`19 50 16 cc`, `1a 50 16 cc`, `21 50 16 cc`, ...). LLVM's AMDGPU disassembler
refuses every one of them as `invalid instruction encoding` for `gfx1030`, `gfx1013`, `gfx1010`, `gfx1011` and `gfx1012`,
which points at an alignment problem in the listing rather than a missing opcode family: one mis-sized instruction
earlier in the stream would leave exactly this kind of short, repeated remnant.

That is the next thing to fix, and it is the only thing standing between the runtime and the compute pipelines the title
requires before it draws.
