import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

import run_algorithm_seamless_batch as algo
import veo3_auto_app as app


class AlgorithmSeamlessTests(unittest.TestCase):
    def test_srgb_linear_roundtrip(self):
        val = np.array([[[0.0, 0.5, 1.0]]], dtype=np.float32)
        lin = algo.srgb_to_linear(val)
        back = algo.linear_to_srgb(lin)
        np.testing.assert_allclose(val, back, atol=1e-3)

    def test_delight_reduces_lighting_gradient(self):
        # Create an image with a severe linear lighting gradient across it
        h, w = 128, 128
        x = np.linspace(0.2, 1.0, w, dtype=np.float32)
        gradient = np.tile(x[None, :, None], (h, 1, 3))
        # Add micro texture
        noise = np.random.RandomState(42).uniform(-0.05, 0.05, (h, w, 3)).astype(np.float32)
        test_img = np.clip(gradient + noise, 0.0, 1.0)

        delighted = algo.delight(test_img, sigma_frac=0.20, strength=1.0)
        # The left-to-right mean difference should be significantly reduced
        grad_orig = float(np.mean(test_img[:, -10:]) - np.mean(test_img[:, :10]))
        grad_delight = float(np.mean(delighted[:, -10:]) - np.mean(delighted[:, :10]))
        self.assertLess(abs(grad_delight), abs(grad_orig) * 0.4)

    def test_detect_period_finds_correct_frequency(self):
        # Create synthetic periodic stripes with exact period 16 px
        w, h = 256, 256
        period = 16
        pattern = (np.arange(w) % period < (period // 2)).astype(np.float32)
        rgb = np.tile(pattern[None, :, None], (h, 1, 3)) * 0.8 + 0.1

        detected, score = algo.detect_period(rgb, axis=1, min_period=8, max_frac=0.45)
        self.assertIsNotNone(detected)
        self.assertEqual(detected, period)
        self.assertLess(score, 0.2)

    def test_make_seamless_improves_seam_score(self):
        # Create non-seamless synthetic image
        np.random.seed(42)
        rgb = np.random.uniform(0.2, 0.8, (128, 128, 3)).astype(np.float32)
        s0 = algo.seam_score(rgb)

        seamless = algo.make_seamless(rgb, overlap=32, mode="cut", feather=2)
        s1 = algo.seam_score(seamless)

        # After wrapping, wrap difference should be close to internal neighbor difference
        self.assertLess(s1[0], max(1.5, s0[0]))
        self.assertLess(s1[1], max(1.5, s0[1]))

    def test_resize_wrapped_preserves_dimensions(self):
        rgb = np.ones((100, 100, 3), dtype=np.float32) * 0.5
        resized = algo.resize_wrapped(rgb, 64)
        self.assertEqual(resized.shape, (64, 64, 3))

    def test_process_single_sku_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            crop_dir = project_dir / "textures_cropped" / "DEMO_FOLDER"
            crop_dir.mkdir(parents=True)

            # Create test image
            im = Image.new("RGB", (128, 128), color=(120, 140, 180))
            src_file = crop_dir / "SKU_TEST_01.png"
            im.save(src_file)

            res = algo.process_single_sku(
                sku="SKU_TEST_01",
                src_path=src_file,
                folder="DEMO_FOLDER",
                project_dir=project_dir,
                size=128,
                overlap=16,
                feather=2,
                blend="cut",
                do_delight=True,
                detect_period_enabled=False,
                force=True,
                dry_run=False,
            )

            self.assertEqual(res["status"], "success")
            self.assertTrue(Path(res["path"]).is_file())

            # Verify QC files are generated
            sku_out = project_dir / "output" / "chatgpt" / "DEMO_FOLDER" / "SKU_TEST_01"
            self.assertTrue((sku_out / "QC_offset50.png").is_file())
            self.assertTrue((sku_out / "QC_tile_3x3.png").is_file())
            self.assertTrue((sku_out / "QC_tile_15x15.png").is_file())

    def test_veo3_auto_app_build_steps_for_algo_engine(self):
        controller = app.PipelineController()
        payload = {
            "engine": "algo",
            "source_mode": "local",
            "sku": "SKU999",
            "limit": "5",
            "force": True,
            "flows": {
                "import": False,
                "crop": True,
                "seamless": True,
                "package": True,
            },
        }

        steps = controller.build_steps(payload, folder="TEST_FOLDER")
        step_scripts = [step.script for step, _ in steps]

        self.assertIn("run_algorithm_seamless_batch.py", step_scripts)
        self.assertIn("package_seamless_textures.py", step_scripts)

        # Check arguments
        algo_step_idx = step_scripts.index("run_algorithm_seamless_batch.py")
        algo_args = steps[algo_step_idx][1]
        self.assertIn("--source-mode", algo_args)
        self.assertIn("chatgpt", algo_args)
        self.assertIn("--sku", algo_args)
        self.assertIn("SKU999", algo_args)
        self.assertIn("--force", algo_args)
        self.assertIn("--folder", algo_args)
        self.assertIn("TEST_FOLDER", algo_args)

    def test_refine_chatgpt_texture_seamless_fixes_seams_and_backups(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            sku = "SKU_GPT_01"
            folder = "GPT_FOLDER"
            sku_out = project_dir / "output" / "chatgpt" / folder / sku
            sku_out.mkdir(parents=True)

            # Create a non-seamless synthetic ChatGPT output
            np.random.seed(42)
            arr = np.random.uniform(0.1, 0.9, (128, 128, 3))
            # Intentionally make left and right borders very different
            arr[:, :10] += 0.4
            arr[:, -10:] -= 0.4
            arr = (np.clip(arr, 0.0, 1.0) * 255).astype(np.uint8)
            Image.fromarray(arr).save(sku_out / "seamless_texture.png")

            res = algo.refine_chatgpt_texture_seamless(
                sku=sku,
                folder=folder,
                project_dir=project_dir,
                size=128,
                overlap=16,
                feather=2,
                force=True,
                verbose=False,
            )

            self.assertEqual(res["status"], "refined")
            # Raw backup should exist
            raw_backup = sku_out / "seamless_texture_chatgpt_raw.png"
            self.assertTrue(raw_backup.is_file())
            # Output texture should exist and have better seam score
            self.assertTrue((sku_out / "seamless_texture.png").is_file())
            self.assertLess(res["seam_score_after"][0], res["seam_score_before"][0])

            # Second run without force should detect it is already seamless
            res2 = algo.refine_chatgpt_texture_seamless(
                sku=sku,
                folder=folder,
                project_dir=project_dir,
                size=128,
                overlap=16,
                force=False,
                verbose=False,
            )
            self.assertEqual(res2["status"], "already_seamless")

    def test_veo3_auto_app_embedded_worker_dispatch_for_algo(self):
        with (
            patch.object(app, "restore_worker_streams"),
            patch.object(app, "ensure_runtime_layout"),
            patch.object(algo, "main", return_value=0),
        ):
            code = app.run_embedded_worker("run_algorithm_seamless_batch.py", ["--dry-run"])
            self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
