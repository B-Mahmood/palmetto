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

### What the skipped words actually are

Program 07's single gap is the clearest case, and it settles the shape of the problem. The four bytes at the gap are the
**first half of an eight-byte instruction**; LLVM rejects them on their own and then re-anchors four bytes later, so the
second half is decoded as a different, unrelated instruction (`v_mul_hi_u32_u24_e32`). The translator then finds a
reachable instruction that runs into the skipped first half and refuses the whole shader.

The eight bytes together are still rejected, and the reason is a register-alignment rule rather than a missing opcode:

```
s_buffer_load_dwordx8 s[25:32], s[16:19], s92 offset:0x7800   -> error: invalid register alignment
s_buffer_load_dwordx8 s[92:99], s[16:19], s92 offset:0x7800   -> assembles, and matches an earlier instruction in the same program
```

Sweeping that field settles which reading is right. The byte selects the destination, and every value the disassembler
accepts lands inside the register file while the rejected ones do not:

| value | destination | result |
|---|---|---|
| 0x17 | `s[92:99]` | decodes |
| 0x18 | `s[96:103]` | decodes |
| **0x19** | `s[100:107]` | **invalid** |
| **0x1A** | `s[104:111]` | **invalid** |
| 0x1B..0x1D | `ttmp[0:15]` | decodes |

The rejected values name registers past the scalar-register file on every RDNA2 target LLVM models (gfx1010, gfx1011,
gfx1012, gfx1013, gfx1030 all refuse them), so these words are **not an instruction on any of those targets**. The
disassembler is right to reject them; the control-flow walk is wrong to reach them.

That points at the walk rather than at the decoder, and specifically at where the stream first drifts: a word the title
executes cannot sit there as data, so an earlier instruction must have been decoded at the wrong length or as the wrong
operation, leaving the walk falling through into what is actually a literal or padding word. Program 07 has exactly one
such word, which makes it the cheapest place to find that first divergence.

Either way the work is in the translator and its disassembly, not in the D3D12 layer: the runtime cannot create these
compute pipelines until the programs translate, and the title stops on a null pipeline when they do not. That is the only
thing between the runtime and a first frame, and it is a translator project rather than a wiring change.

### The fall-through site in program 07

The translator re-anchors every instruction to its true offset in the program blob when a listing contains undecodable
words, then translates only what is reachable from the entry. Doing that for program 07 puts the skipped word in a
precise place:

```
re-anchoring skipped 1 range: 0x4F08..0x4F0C (4 bytes)

0x4EF4  s_clause
0x4EF8  s_buffer_load_dwordx8
0x4F00  s_buffer_load_dwordx8      <- ends at 0x4F08
0x4F08  (skipped word)             <- the path falls in here
0x4F0C  28 2c 14 1a                <- code resumes here
```

Nothing branches to 0x4F08, so the path arrives by fall-through from the scalar load at 0x4F00, and the two loads before
it carry `s_clause`, whose whole purpose is to bundle consecutive scalar loads: the stream really does look like a run of
loads, with one member of the run undecodable.

An earlier version of this note claimed the listing and the blob disagreed about that neighbourhood, because a load
encoding seen nearby was not at that offset in the blob. That was wrong: the encoding occurs exactly once in the blob, at
0x509C, as an ordinary instruction elsewhere in the same program. The listing and the blob agree; only the one word at
0x4F08 is undecodable.

The program's own metadata says it is a wave32 compute program with 64 VGPRs, 14 user SGPRs and 4 KiB of LDS. The
destination the undecodable word names would be `s[100:107]`, past the scalar-register file that every RDNA2 target LLVM
models allows, which is why it decodes as invalid while its neighbours decode.

### The answer: the console's register file is larger than LLVM's model

The programs settle it themselves. Their compute metadata declares the register counts, and decoding the granularity
field of `rsrc1` (`0x402c0347`) gives **112 SGPRs** for a wave32 program; the same value appears in programs 07, 12 and
18. LLVM's RDNA2 model allows **106** scalar registers at most, and it says so plainly when asked to assemble the
instruction the word encodes:

```
s_buffer_load_dwordx8 s[100:107], s[16:19], s12 offset:0x7800   -> error: register index is out of range
s_buffer_load_dwordx8 s[96:103],  s[16:19], s12 offset:0x7800   -> assembles, encoding 08 18 2c f4 00 78 00 18
```

So the skipped word is a valid instruction on this console whose destination register LLVM's register file does not
contain, and the same will be true of every other reference to a register above 103 in these programs. The disassembler
is not wrong about the encoding; it is modelling a smaller machine.

**What that means for the fix.** No runtime change helps and no LLVM flag helps: the disassembler has to be told the
console's register file is bigger, or the pipeline has to decode these words itself. The pipeline route is available
without rebuilding LLVM: assemble the same instruction forms with destinations inside LLVM's range under each candidate
`mcpu`, derive the bit fields, and emit the listing line for the out-of-range cases from the bytes. The ground truth for
that derivation comes from `llvm-mc --show-encoding`, so nothing is guessed.

Program 07 is the trial case: one word, one instruction form, and a shader that already translates in every other
respect.
