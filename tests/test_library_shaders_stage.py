"""make_game.py's pipeline-library step (library_shaders): the title's own precompiled compute programs inside
shaders.rpf, unpacked by tools/shader_library.py and translated where the runtime looks for them.

    python -I tests/test_library_shaders_stage.py

Zero game data: the archive and the carve index are a few synthetic bytes, and the tools are mocked the way
tests/test_fspx_shaders_stage.py mocks them.
"""
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import make_game  # noqa: E402


class LibraryStage(unittest.TestCase):
    """Which tools library_shaders() runs. Each mock writes what the real tool writes last."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = os.path.join(self.tmp.name, "work")
        self.game = os.path.join(self.tmp.name, "game")
        os.makedirs(self.game)
        self.rpf = os.path.join(self.game, "shaders.rpf")
        with open(self.rpf, "wb") as f:
            f.write(b"synthetic archive")
        self.carved = os.path.join(self.work, "shaders", "dxbc", "index.json")
        self.write_carve('[{"file": "shader.dxbc"}]')
        self.unpacked = os.path.join(self.work, "shaders", "library")
        self.spirv = os.path.join(self.work, "shaders", "spirv", "library")
        self.calls = []
        self.listings = ["lib_cs_00000", "lib_cs_00001", "lib_cs_00002"]
        self.translations = {"lib_cs_00000": ("c" * 64, "s" * 64, b"module 0"),
                             "lib_cs_00002": ("d" * 64, "t" * 64, b"module 2")}

    def write_carve(self, text):
        os.makedirs(os.path.dirname(self.carved), exist_ok=True)
        with open(self.carved, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)

    def fake_py(self, tool, *args):
        self.calls.append((tool, args))
        if tool == "shader_library.py":
            disasm = os.path.join(self.unpacked, "disasm")
            os.makedirs(disasm, exist_ok=True)
            for name in self.listings:
                with open(os.path.join(disasm, name + ".s"), "w", encoding="utf-8", newline="\n") as f:
                    f.write("\ts_endpgm\n")

    def fake_translate(self, disasm, spirv, jobs, emulate=False, fallbacks=()):
        self.calls.append(("translate", (disasm, spirv, jobs, emulate, tuple(fallbacks))))
        os.makedirs(spirv, exist_ok=True)
        index = {}
        for name, (code, state, module) in self.translations.items():
            index[name] = {"stage": "cs", "fspx_code_sha256": code, "fspx_state_sha256": state}
            with open(os.path.join(spirv, name + ".spv"), "wb") as f:
                f.write(module)
        with open(os.path.join(spirv, "index.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(index, f)

    def run_step(self, signature="llvm-mc 23", emulate=False, fallbacks=(), jobs=4):
        self.calls, self.logged = [], []
        with mock.patch.object(make_game, "py", self.fake_py), \
                mock.patch.object(make_game, "translate", self.fake_translate), \
                mock.patch.object(make_game, "log", self.logged.append):
            make_game.library_shaders(self.game, self.work, signature, jobs, emulate=emulate, fallbacks=fallbacks)
        return [tool for tool, _ in self.calls]

    def test_runs_once_then_nothing(self):
        self.assertEqual(self.run_step(), ["shader_library.py", "translate"])
        (_, unpack), (_, translate) = self.calls
        self.assertEqual(unpack, (self.rpf, "--out", self.unpacked, "--stages", "cs", "--disasm", "--jobs", 4))
        self.assertEqual(translate, (os.path.join(self.unpacked, "disasm"), self.spirv, 4, False, ()))
        self.assertTrue(os.path.isfile(os.path.join(self.spirv, "library_build.json")))
        self.assertIn("shaders: 2 of 3 pipeline-library compute programs translated", "\n".join(self.logged))
        self.assertEqual(self.run_step(), [])

    def test_the_runtime_folder_is_the_one_it_loads(self):
        # runtime/src/shader_library.h loads <shaders>/library/index.json beside fspx/ and bvh/.
        self.run_step()
        self.assertEqual(os.path.relpath(self.spirv, os.path.join(self.work, "shaders", "spirv")), "library")
        with open(os.path.join(self.spirv, "index.json"), encoding="utf-8") as f:
            self.assertEqual(sorted(json.load(f)), ["lib_cs_00000", "lib_cs_00002"])

    def test_each_input_change_runs_it_again(self):
        self.run_step()
        self.assertEqual(self.run_step(signature="llvm-mc 24"), ["shader_library.py", "translate"])
        self.assertEqual(self.run_step(signature="llvm-mc 24", emulate=True), ["shader_library.py", "translate"])
        self.assertTrue(self.calls[1][1][3])
        flags = ("--no-float-controls",)
        self.assertEqual(self.run_step(signature="llvm-mc 24", emulate=True, fallbacks=flags),
                         ["shader_library.py", "translate"])
        self.assertEqual(self.calls[1][1][4], flags)
        self.write_carve('[{"file": "shader.dxbc"}, {"file": "another.dxbc"}]')   # carved again: a new archive
        self.assertEqual(self.run_step(signature="llvm-mc 24", emulate=True, fallbacks=flags),
                         ["shader_library.py", "translate"])
        self.assertEqual(self.run_step(signature="llvm-mc 24", emulate=True, fallbacks=flags), [])
        with mock.patch.object(make_game, "translation_sources_digest", return_value="another translator"):
            self.assertEqual(self.run_step(signature="llvm-mc 24", emulate=True, fallbacks=flags),
                             ["shader_library.py", "translate"])

    def test_missing_cached_module_is_rebuilt(self):
        self.run_step()
        os.remove(os.path.join(self.spirv, "lib_cs_00002.spv"))
        self.assertEqual(self.run_step(), ["shader_library.py", "translate"])
        self.assertEqual(self.run_step(), [])

    def test_old_listings_and_modules_go_before_a_new_run(self):
        self.run_step()
        stale_listing = os.path.join(self.unpacked, "disasm", "lib_cs_09999.s")
        stale_module = os.path.join(self.spirv, "lib_cs_09999.spv")
        for path in (stale_listing, stale_module):
            open(path, "w").close()
        self.run_step(signature="llvm-mc 24")
        self.assertFalse(os.path.exists(stale_listing))
        self.assertFalse(os.path.exists(stale_module))

    def test_no_archive_or_no_carve_no_step_and_no_index_left(self):
        self.run_step()
        os.remove(self.rpf)
        self.assertEqual(self.run_step(), [])
        self.assertFalse(os.path.exists(os.path.join(self.spirv, "index.json")))
        self.assertFalse(os.path.exists(os.path.join(self.spirv, "library_build.json")))
        with open(self.rpf, "wb") as f:
            f.write(b"synthetic archive")
        os.remove(self.carved)
        self.assertEqual(self.run_step(), [])

    def test_same_keys_with_different_modules_fail_the_step(self):
        self.translations = {"lib_cs_00000": ("c" * 64, "s" * 64, b"module A"),
                             "lib_cs_00001": ("c" * 64, "s" * 64, b"module B")}
        with self.assertRaises(SystemExit), mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            self.run_step()
        self.assertIn("lib_cs_00000", err.getvalue())
        self.assertFalse(os.path.exists(os.path.join(self.spirv, "index.json")))
        self.assertFalse(os.path.exists(os.path.join(self.spirv, "library_build.json")))

    def test_a_stopped_translation_leaves_no_marker(self):
        self.run_step()

        def stop(*args, **kw):
            raise KeyboardInterrupt
        with mock.patch.object(make_game, "py", self.fake_py), mock.patch.object(make_game, "translate", stop), \
                mock.patch.object(make_game, "log", lambda *a: None):
            with self.assertRaises(KeyboardInterrupt):
                make_game.library_shaders(self.game, self.work, "llvm-mc 24", 4)
        self.assertFalse(os.path.exists(os.path.join(self.spirv, "index.json")))
        self.assertFalse(os.path.exists(os.path.join(self.spirv, "library_build.json")))
        self.assertEqual(self.run_step(signature="llvm-mc 24"), ["shader_library.py", "translate"])
        self.assertEqual(self.run_step(signature="llvm-mc 24"), [])


class ShadersStageRunsTheLibrary(unittest.TestCase):
    """The whole shaders() stage with shaders.rpf in the game folder: the library step comes after the game's shaders
    and never makes them run again."""

    def test_library_runs_after_the_corpus_and_alone_when_only_its_inputs_change(self):
        with tempfile.TemporaryDirectory() as t:
            work, game = os.path.join(t, "work"), os.path.join(t, "game")
            os.makedirs(game)
            with open(os.path.join(game, "shaders.rpf"), "wb") as f:
                f.write(b"synthetic archive")
            ran = []
            dxbc = os.path.join(work, "shaders", "dxbc")

            def write(path, text):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(text)

            def fake_py(tool, *args):
                ran.append(tool)
                if tool == "shader_carve.py":
                    write(os.path.join(dxbc, "index.json"), '[{"file": "shader.dxbc"}]')
                elif tool == "fssm_unpack.py":
                    write(os.path.join(dxbc, "fssm_index.json"), '[{"name": "shader"}]')
                elif tool == "shader_disasm.py":
                    write(os.path.join(dxbc, "disasm", "shader.s"), "s_endpgm\n")
                elif tool == "shader_library.py":
                    write(os.path.join(args[args.index("--out") + 1], "disasm", "lib_cs_00000.s"), "s_endpgm\n")

            def fake_translate(disasm, spirv, jobs, emulate=False, fallbacks=()):
                ran.append("translate")
                key = {"fspx_code_sha256": "e" * 64, "fspx_state_sha256": "f" * 64}
                name = "lib_cs_00000" if os.path.basename(spirv) == "library" else "shader"
                write(os.path.join(spirv, "index.json"), json.dumps({name: {"stage": "cs", **key}}))
                with open(os.path.join(spirv, name + ".spv"), "wb") as f:
                    f.write(b"synthetic module")

            def stage():
                ran.clear()
                with mock.patch.object(make_game, "py", fake_py), \
                        mock.patch.object(make_game, "need", lambda *a: None), \
                        mock.patch.object(make_game, "translate", fake_translate), \
                        mock.patch.object(make_game, "llvm_mc_signature", return_value="llvm-mc 23"), \
                        mock.patch.object(make_game, "log", lambda *a: None):
                    make_game.shaders(game, work, 2)
                return list(ran)

            self.assertEqual(stage(), ["shader_carve.py", "fssm_unpack.py", "shader_disasm.py", "translate",
                                       "shader_library.py", "translate"])
            self.assertEqual(stage(), [])
            with mock.patch.object(make_game, "translation_sources_digest", return_value="another translator"):
                ran_again = stage()
            self.assertEqual(ran_again[-2:], ["shader_library.py", "translate"])
            self.assertTrue(os.path.isfile(os.path.join(work, "shaders", "spirv", "library", "index.json")))


if __name__ == "__main__":
    unittest.main()
