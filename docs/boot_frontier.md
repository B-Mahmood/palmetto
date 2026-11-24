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

* The title **continues** when a compute pipeline cannot be created, so the FSPX gap breaks rendering rather than the
  boot; it is not the current stop.
* The runtime's shader library scans only its top directory. The FSPX and XboxBvhBuilder sets must sit beside
  `index.json`, not in the `bvh/` and `fspx/` subdirectories the builder writes, or every lookup misses.

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
