import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import quality_output_uploader as uploader
import veo3_auto_app as app


class FakeLocator:
    def __init__(self, accept="image/*"):
        self.accept = accept
        self.path = None

    def get_attribute(self, name):
        return self.accept if name == "accept" else None

    def set_input_files(self, path, **_kwargs):
        self.path = path

    def evaluate(self, _script):
        return Path(self.path).name


class FakeLocators:
    def __init__(self, items):
        self.items = items

    def count(self):
        return len(self.items)

    def nth(self, index):
        return self.items[index]


class FakePage:
    def __init__(self, url, inputs=None):
        self.url = url
        self.inputs = inputs or []
        self.front = False

    def is_closed(self):
        return False

    def locator(self, _selector):
        return FakeLocators(self.inputs)

    def bring_to_front(self):
        self.front = True


class QualityOutputTests(unittest.TestCase):
    def test_quality_gallery_keeps_only_one_selected_image(self):
        html = app.INDEX_HTML
        self.assertIn("if (selectedQualityItem === item) return;", html)
        self.assertIn("selectedQualityItem.node.classList.remove('selected')", html)
        self.assertIn("item.node.classList.add('selected')", html)

    def test_quality_gallery_switches_child_folders_and_fills_column(self):
        html = app.INDEX_HTML
        self.assertIn("qualityFolderGroups = result.folders", html)
        self.assertIn("showQualityFolder(0)", html)
        self.assertIn("grid-template-columns: minmax(0, 1fr)", html)
        self.assertIn("min-height: 700px", html)

    def test_quality_folder_groups_default_to_sorted_first_child(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            second = root / "B-folder"
            first = root / "A-folder"
            second.mkdir(); first.mkdir()
            (second / "b.png").write_bytes(b"b")
            (first / "a.png").write_bytes(b"a")
            groups = app.list_quality_folder_groups(root)
        self.assertEqual([group["name"] for group in groups], ["A-folder", "B-folder"])
        self.assertEqual(groups[0]["images"][0]["name"], "a.png")

    def test_quality_upload_targets_same_origin_embed(self):
        html = app.INDEX_HTML
        self.assertIn('src="/embed/3d/suits?key=123456789"', html)
        self.assertIn("frame.contentDocument", html)
        self.assertIn("input.files = transfer.files", html)

    def test_validate_target_url(self):
        self.assertEqual(uploader.validate_target_url("https://example.com/test"), "https://example.com/test")
        for value in ("", "example.com", "file:///tmp/test.html", "javascript:alert(1)"):
            with self.assertRaises(ValueError):
                uploader.validate_target_url(value)

    def test_uses_already_open_target_tab_without_navigation_or_new_page(self):
        wrong = FakePage("https://other.example/", [FakeLocator()])
        target_input = FakeLocator("")
        target = FakePage("https://example.com/editor?draft=1", [target_input])
        context = types.SimpleNamespace(pages=[wrong, target])
        image = Path("fabric.png").resolve()
        result = uploader._attach_image(context, "https://example.com/editor", image)
        self.assertEqual(target_input.path, str(image))
        self.assertTrue(target.front)
        self.assertFalse(wrong.front)
        self.assertEqual(result["target_url"], target.url)

    def test_root_target_selects_latest_open_tab_on_same_site(self):
        first = FakePage("https://example.com/old", [FakeLocator()])
        latest_input = FakeLocator("image/png")
        latest = FakePage("https://example.com/current", [latest_input])
        context = types.SimpleNamespace(pages=[first, latest])
        image = Path("fabric.png").resolve()
        uploader._attach_image(context, "https://example.com", image)
        self.assertEqual(latest_input.path, str(image))

    def test_missing_target_tab_does_not_open_one(self):
        context = types.SimpleNamespace(pages=[])
        with self.assertRaisesRegex(RuntimeError, "Không tìm thấy trang web mục tiêu"):
            uploader._attach_image(context, "https://example.com", Path("fabric.png"))

    def test_missing_file_input_fails_immediately(self):
        page = FakePage("https://example.com/editor")
        context = types.SimpleNamespace(pages=[page])
        with self.assertRaisesRegex(RuntimeError, "không có ô Choose File"):
            uploader._attach_image(context, page.url, Path("fabric.png"))

    def test_upload_delegates_to_persistent_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "fabric.png"
            image.write_bytes(b"image")
            (root / "config.json").write_text('{"browser":{"cdp_port":9333}}', encoding="utf-8")
            worker = types.SimpleNamespace(submit=lambda url, path: {"url": url, "path": path})
            with patch.object(uploader, "_worker_for", return_value=worker) as factory:
                result = uploader.upload_image_to_target(root, "https://example.com", image)
            factory.assert_called_once_with("http://127.0.0.1:9333")
            self.assertEqual(result["path"], image.resolve())

    def test_folder_picker_uses_topmost_owner_window(self):
        completed = types.SimpleNamespace(returncode=0, stdout="", stderr="")
        with patch("veo3_auto_app.subprocess.run", return_value=completed) as run:
            self.assertEqual(app.choose_local_folder(), "")
        command = run.call_args.args[0]
        script = command[command.index("-Command") + 1]
        self.assertIn("$owner.TopMost=$true", script)
        self.assertIn("$dialog.ShowDialog($owner)", script)
        self.assertIn("System.Windows.Forms.OpenFileDialog", script)
        self.assertNotIn("FolderBrowserDialog", script)
        self.assertIn("[IO.Directory]::Exists($selected)", script)
        self.assertIn("[IO.Path]::GetDirectoryName($selected)", script)


if __name__ == "__main__":
    unittest.main()
