import tempfile
import unittest
from pathlib import Path

from PIL import Image

from crop_textures import crop_signature, select_pending_sources, sha256_file


class CropSelectionTests(unittest.TestCase):
    def test_limit_is_applied_after_current_crops_are_skipped(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_dir = root / "raw"
            output_dir = root / "cropped"
            source_dir.mkdir()
            output_dir.mkdir()
            sources = []
            for sku in ("101301", "101302", "101303"):
                source = source_dir / f"{sku}.png"
                Image.new("RGB", (20, 20), "black").save(source)
                sources.append(source)

            box = (0.02, 0.02, 0.65, 0.47)
            signature = crop_signature(box, "png")
            current_output = output_dir / "101301.png"
            Image.new("RGB", (10, 10), "black").save(current_output)
            status = {
                "101301": {
                    "source_sha256": sha256_file(sources[0]),
                    "crop_signature": signature,
                }
            }

            selected, skipped = select_pending_sources(
                sources,
                output_dir,
                status,
                signature,
                force=False,
                limit=1,
            )

            self.assertEqual(["101302.png"], [path.name for path in selected])
            self.assertEqual(1, skipped)


if __name__ == "__main__":
    unittest.main()
