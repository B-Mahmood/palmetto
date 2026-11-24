"""Repair a listing the disassembler refused because the console's SGPR file is wider than its model.

The retail title's compute programs declare 112 scalar registers (rsrc1 granularity field, wave32), while LLVM's RDNA2
model allows 106. A scalar-memory instruction whose destination lies above that file is rejected: the disassembler skips
its first four bytes, re-anchors four bytes later, and decodes the instruction's second half as an unrelated VOP. The
translator then sees a reachable instruction running into skipped bytes and refuses the whole shader.

This tool rebuilds those lines from the program bytes. It walks the listing against the blob, finds every place where the
emitted encoding is not what sits there, and tries the buffer-load forms whose field layout it derives from
`llvm-mc --show-encoding` on in-range operands. A form that matches is written with its real destination, and listing
lines whose bytes overlap the reconstructed instruction are dropped. A reconstruction is accepted only when every
register it names is inside the declared file, so nothing is guessed.

Usage:
    python -I tools/widen_smem_decode.py <listing.s> --blob <program.code|.fssm> [--out <repaired.s>]
"""
import argparse
import re
import subprocess
import sys

LLVM_MC = r"F:\tools\llvm-mc\llvm-mc.exe"

LINE = re.compile(r"^\t(.*?)\s*;\s*encoding:\s*\[([0-9a-fx, ]+)\]\s*$")
HDR = re.compile(r"code 0x([0-9a-f]+)\.\.0x([0-9a-f]+)")

FORMS = {
    "s_buffer_load_dword": ("s_buffer_load_dword {dst}", 1),
    "s_buffer_load_dwordx2": ("s_buffer_load_dwordx2 {dst}", 2),
    "s_buffer_load_dwordx4": ("s_buffer_load_dwordx4 {dst}", 4),
    "s_buffer_load_dwordx8": ("s_buffer_load_dwordx8 {dst}", 8),
    "s_buffer_load_dwordx16": ("s_buffer_load_dwordx16 {dst}", 16),
}


def sgpr(dst, lanes):
    return "s[%d]" % dst if lanes == 1 else "s[%d:%d]" % (dst, dst + lanes - 1)


def assemble(text, mcpu):
    p = subprocess.run([LLVM_MC, "--triple=amdgcn--amdpal", "--mcpu=" + mcpu, "--show-encoding"],
                       input=text + "\n", capture_output=True, text=True)
    m = re.search(r"encoding:\s*\[([0-9a-fx, ]+)\]", p.stdout)
    return bytes(int(x, 16) for x in m.group(1).split(",")) if m else None


def table(mcpu):
    """(byte2, byte3) -> (form name, lanes), derived from assembling each form with in-range operands."""
    found = {}
    for name, (syntax, lanes) in FORMS.items():
        raw = assemble(syntax.format(dst=sgpr(8, lanes)) + ", s[16:19], s12 offset:0x7800", mcpu)
        if raw:
            found[(raw[2], raw[3])] = (name, lanes)
    return found


def decode(word, lookup, max_sgprs):
    if len(word) < 8:
        return None
    hit = lookup.get((word[2], word[3]))
    if not hit:
        return None
    name, lanes = hit
    dst, sbase = word[1] * 4, word[0] * 2
    offset, soffset = word[4] | (word[5] << 8), word[7] >> 1
    if dst + lanes > max_sgprs or sbase + 4 > max_sgprs or soffset >= max_sgprs:
        return None
    return name, lanes, dst, sbase, soffset, offset


def build(name, lanes, dst, sbase, soffset, offset, word):
    soff = "off" if word[7] & 0x80 else "s%d" % soffset
    text = FORMS[name][0].format(dst=sgpr(dst, lanes))
    line = "%s, s[%d:%d], %s offset:0x%x" % (text, sbase, sbase + 3, soff, offset)
    enc = ", ".join("0x%02x" % b for b in word[:8])
    return "\t%-46s ; encoding: [%s]" % (line, enc)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("listing")
    ap.add_argument("--blob", required=True)
    ap.add_argument("--out")
    ap.add_argument("--mcpu", default="gfx1030")
    ap.add_argument("--max-sgprs", type=int, default=112)
    args = ap.parse_args()

    text = open(args.listing, encoding="utf-8", errors="replace").read().splitlines()
    m = HDR.search(text[0]) if text else None
    if not m:
        sys.exit("no code range header in the listing")
    start = int(m.group(1), 16)
    blob = open(args.blob, "rb").read()
    lookup = table(args.mcpu)
    print("%d buffer-load form(s) identified at %s" % (len(lookup), args.mcpu))

    pending = []
    for line in text[1:]:
        e = LINE.match(line)
        if e:
            pending.append((line, bytes(int(x, 16) for x in e.group(2).split(","))))

    out, pos, repaired = [text[0]], start, 0
    while pending:
        line, data = pending[0]
        if blob[pos:pos + len(data)] == data:
            out.append(line)
            pending.pop(0)
            pos += len(data)
            continue
        d = decode(blob[pos:pos + 8], lookup, args.max_sgprs)
        if d:
            out.append(build(*d, blob[pos:pos + 8]))
            repaired += 1
            end = pos + 8
            while pending:
                at = blob.find(pending[0][1], pos, end + 4)
                if at == -1 or at >= end:
                    break
                pending.pop(0)
            pos = end
            continue
        at = blob.find(data, pos, min(pos + 64, len(blob)))
        if at < 0:
            pending.pop(0)
            continue
        out.append(line)
        pending.pop(0)
        pos = at + len(data)

    target = args.out or args.listing
    with open(target, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out) + "\n")
    print("repaired %d word(s); wrote %s" % (repaired, target))


if __name__ == "__main__":
    main()
