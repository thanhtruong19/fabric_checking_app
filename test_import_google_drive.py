import unittest
from pathlib import Path, PurePosixPath
from unittest.mock import patch

from import_google_drive import DRIVE_NOT_PUBLIC_MESSAGE, run_gdown, select_images


class RunGdownErrorTests(unittest.TestCase):
    def assert_not_public_error(self, stderr):
        result = type("CompletedProcess", (), {
            "returncode": 1,
            "stderr": stderr,
            "stdout": "",
        })()
        with patch("import_google_drive.subprocess.run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, DRIVE_NOT_PUBLIC_MESSAGE):
                run_gdown(["https://drive.google.com/drive/folders/test", "--folder"], 30)

    def test_reports_clear_message_when_drive_folder_is_not_public(self):
        self.assert_not_public_error(
            "Failed to retrieve folder contents (status code 500). "
            "You may need to change the permission to 'Anyone with the link'."
        )

    def test_reports_clear_message_when_gdown_error_is_hidden_by_cp932(self):
        self.assert_not_public_error(
            "'cp932' codec can't encode character '\\xe0': illegal multibyte sequence"
        )


class SelectImagesTests(unittest.TestCase):
    def test_ignores_duplicate_listing_for_same_drive_file(self):
        entries = [
            {
                "url": "https://drive.google.com/uc?id=file-101301",
                "path": PurePosixPath("101301.jpg"),
            },
            {
                "url": "https://drive.google.com/file/d/file-101301/view",
                "path": PurePosixPath("101301.jpg"),
            },
        ]

        selected, skipped, duplicate_names = select_images(
            entries, {".jpg"}, recursive=False
        )

        self.assertEqual(["101301.jpg"], [entry["name"] for entry in selected])
        self.assertEqual(0, skipped)
        self.assertEqual(0, duplicate_names)

    def test_keeps_first_item_when_drive_has_duplicate_filenames(self):
        entries = [
            {
                "url": "https://drive.google.com/uc?id=first-file",
                "path": PurePosixPath("101301.jpg"),
            },
            {
                "url": "https://drive.google.com/uc?id=second-file",
                "path": PurePosixPath("101301.jpg"),
            },
        ]

        selected, skipped, duplicate_names = select_images(
            entries, {".jpg"}, recursive=False
        )

        self.assertEqual("first-file", selected[0]["url"].split("=")[-1])
        self.assertEqual(0, skipped)
        self.assertEqual(1, duplicate_names)

    def test_rejects_different_drive_files_with_same_sku(self):
        entries = [
            {
                "url": "https://drive.google.com/uc?id=first-file",
                "path": PurePosixPath("101301.jpg"),
            },
            {
                "url": "https://drive.google.com/uc?id=second-file",
                "path": PurePosixPath("101301.png"),
            },
        ]

        with self.assertRaisesRegex(ValueError, "same SKU"):
            select_images(entries, {".jpg", ".png"}, recursive=False)


class DriveSyncTraceTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_resolve_folder_name(self):
        from import_google_drive import resolve_folder_name
        # 1. Explicit arg
        self.assertEqual("WDTC", resolve_folder_name("WDTC", self.base / "textures_raw/WDTC", {}))
        # 2. Inferred from destination path
        config = {"google_drive": {"share_url": "https://drive.google.com/drive/folders/123", "urls": [{"url": "https://drive.google.com/drive/folders/123", "folder": "TEST_FOLDER"}]}}
        self.assertEqual("TEST_FOLDER", resolve_folder_name(None, self.base / "textures_raw", config))

    def test_compute_folder_creation_stats(self):
        from import_google_drive import compute_folder_creation_stats
        out_dir = self.base / "output" / "chatgpt" / "WDTC"
        (out_dir / "SKU1").mkdir(parents=True, exist_ok=True)
        (out_dir / "SKU1" / "seamless_texture.png").write_text("fake")
        (out_dir / "SKU2").mkdir(parents=True, exist_ok=True)
        (out_dir / "SKU2" / "image_1.png").write_text("fake")
        (out_dir / "SKU3").mkdir(parents=True, exist_ok=True) # unfinished

        stats = compute_folder_creation_stats(self.base, "WDTC", ["SKU1", "SKU2", "SKU3", "SKU4"])
        self.assertEqual(2, stats["created_count"])
        self.assertEqual(1, stats["seamless_count"])
        self.assertEqual(1, stats["fabric_count"])

    def test_record_drive_sync_trace(self):
        from import_google_drive import record_drive_sync_trace, load_status
        sync_file = self.base / "status_drive_sync.json"
        creation_stats = {
            "created_count": 5,
            "seamless_count": 5,
            "fabric_count": 4,
            "cropped_count": 10,
        }
        entry = record_drive_sync_trace(
            sync_file_path=sync_file,
            folder_name="WDTC",
            drive_url="https://drive.google.com/drive/folders/test",
            drive_total_images=10,
            new_imported=3,
            already_existed=7,
            download_errors=0,
            local_raw_total=10,
            creation_stats=creation_stats,
            status="success",
            duration_seconds=12.5,
            triggered_by="test",
        )

        self.assertEqual("WDTC", entry["folder"])
        self.assertEqual(10, entry["stats"]["drive_total_images"])
        self.assertEqual(5, entry["stats"]["created_count"])
        self.assertEqual(50.0, entry["stats"]["progress_percent"])

        saved = load_status(sync_file)
        self.assertIn("WDTC", saved["folders"])
        self.assertEqual(10, saved["folders"]["WDTC"]["drive_total_images"])
        self.assertEqual(5, saved["folders"]["WDTC"]["created_count"])
        self.assertEqual(1, len(saved["history"]))


if __name__ == "__main__":
    unittest.main()

