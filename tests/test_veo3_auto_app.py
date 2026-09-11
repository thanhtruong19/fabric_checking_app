import json
import subprocess
import tempfile
import time
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import veo3_auto_app as app
import run_chatgpt_texture_grouped_batch as grouped
from tools.build.prepare_build_assets import create_default_config


class VEO3AutoAppTests(unittest.TestCase):
    def test_grouped_chat_cleanup_deletes_conversation_and_clears_status(self):
        import run_chatgpt_fabric_grouped_batch as fabric

        for module in (grouped, fabric):
            with self.subTest(module=module.__name__):
                status = {
                    "SKU001": {
                        "status": "done",
                        "conversation_url": "https://chatgpt.com/c/GROUPED",
                        "chat_cleanup_pending": True,
                    },
                    "SKU002": {
                        "status": "done",
                        "conversation_url": "https://chatgpt.com/c/GROUPED",
                        "chat_cleanup_pending": True,
                    },
                }
                page = Mock()
                with patch.object(module.legacy, "delete_automation_chat") as delete_chat, \
                        patch.object(module, "save_status") as save_status:
                    module.delete_grouped_conversation(
                        page, status, ["SKU001", "SKU002"]
                    )

                delete_chat.assert_called_once_with(
                    page, "SKU001, SKU002", "https://chatgpt.com/c/GROUPED"
                )
                save_status.assert_called_once_with(status)
                for item in status.values():
                    self.assertTrue(item["chat_deleted"])
                    self.assertFalse(item["chat_cleanup_pending"])
                    self.assertNotIn("conversation_url", item)

    def test_grouped_chat_cleanup_refuses_new_chat_without_conversation_url(self):
        with patch.object(grouped, "save_status"):
            with self.assertRaisesRegex(RuntimeError, "conversation URL is missing"):
                grouped.delete_grouped_conversation(
                    Mock(), {"SKU001": {"status": "done"}}, ["SKU001"]
                )

    def test_grouped_startup_cleanup_ignores_historical_conversation_urls(self):
        historical = {
            "OLD": {
                "status": "done",
                "conversation_url": "https://chatgpt.com/c/HISTORICAL",
            },
            "PENDING": {
                "status": "done",
                "conversation_url": "https://chatgpt.com/c/PENDING",
                "chat_cleanup_pending": True,
            },
        }
        page = Mock()
        with patch.object(grouped, "delete_grouped_conversation") as delete_chat:
            grouped.cleanup_pending_grouped_chats(page, historical)

        delete_chat.assert_called_once_with(page, historical, ["PENDING"])

    def test_drive_items_are_deduplicated_by_folder_id(self):
        items = [
            {"url": "https://drive.google.com/drive/folders/ABC?usp=drive_link", "folder": "FIRST"},
            {"url": "https://drive.google.com/drive/folders/ABC?usp=sharing", "folder": "SECOND"},
            {"url": "https://drive.google.com/drive/folders/XYZ", "folder": "OTHER"},
        ]

        unique = app.deduplicate_drive_items(items)

        self.assertEqual([item["folder"] for item in unique], ["FIRST", "OTHER"])

    def test_fabric_worker_discovers_only_final_seamless_as_input(self):
        import run_chatgpt_fabric_grouped_batch as fabric
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sku_dir = root / "ABC1"
            sku_dir.mkdir()
            (sku_dir / "seamless_texture.png").touch()
            (sku_dir / "seamless_texture_chatgpt_raw.png").touch()
            (sku_dir / "QC_tile_3x3.png").touch()
            parser = Mock()
            with (
                patch.object(fabric, "RAW_DIR", root),
                patch.object(fabric, "FALLBACK_RAW_DIR", root),
                patch.object(fabric, "INPUT_MODE", "seamless"),
            ):
                discovered = fabric.discover_input_files(parser)

            self.assertEqual(discovered, [("ABC1", (sku_dir / "seamless_texture.png", None))])

    def test_fabric_worker_preserves_collection_folder_for_nested_seamless(self):
        import run_chatgpt_fabric_grouped_batch as fabric
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sku_dir = root / "DEHQ" / "DEHQ2"
            sku_dir.mkdir(parents=True)
            seamless = sku_dir / "seamless_texture.png"
            seamless.touch()
            parser = Mock()
            with (
                patch.object(fabric, "RAW_DIR", root),
                patch.object(fabric, "FALLBACK_RAW_DIR", root),
                patch.object(fabric, "INPUT_MODE", "seamless"),
            ):
                discovered = fabric.discover_input_files(parser)

            self.assertEqual(discovered, [("DEHQ2", (seamless, "DEHQ"))])
            with patch.object(fabric, "OUTPUT_DIR", root):
                self.assertEqual(
                    fabric.output_path_for("DEHQ2", "DEHQ"),
                    root / "DEHQ" / "DEHQ2" / "image_1.png",
                )

    def test_chatgpt_workers_reuse_one_tab_without_closing_other_tabs(self):
        import run_chatgpt_texture_batch as texture
        context = Mock()
        unrelated = Mock(url="https://example.com")
        unrelated.is_closed.return_value = False
        chat = Mock(url="https://chatgpt.com/c/existing")
        chat.is_closed.return_value = False
        context.pages = [unrelated, chat]
        for _ in range(3):
            self.assertIs(texture.get_automation_chatgpt_page(context), chat)
        context.new_page.assert_not_called()
        unrelated.close.assert_not_called()
        blank = Mock(url="about:blank")
        blank.is_closed.return_value = False
        context.pages = [unrelated, blank]
        self.assertIs(texture.get_automation_chatgpt_page(context), blank)
        context.new_page.assert_not_called()
        chat.is_closed.return_value = True
        context.pages = [unrelated, chat]
        self.assertIs(texture.get_automation_chatgpt_page(context), context.new_page.return_value)
        context.new_page.assert_called_once()

    def test_chatgpt_series_selects_first_new_generated_image(self):
        import run_chatgpt_texture_batch as texture

        old_image = MagicMock()
        old_image.get_attribute.return_value = "old"
        old_image.is_visible.return_value = True
        first_image = MagicMock()
        first_image.get_attribute.return_value = "new-1"
        first_image.is_visible.return_value = True
        second_image = MagicMock()
        second_image.get_attribute.return_value = "new-2"
        second_image.is_visible.return_value = True
        images = MagicMock()
        images.count.return_value = 3
        images.nth.side_effect = [old_image, first_image, second_image]
        page = MagicMock()
        page.locator.return_value = images

        selected = texture.first_new_generated_image(page, {"old"})

        self.assertIs(selected, first_image)
        second_image.get_attribute.assert_not_called()

    def test_chatgpt_comparison_is_skipped_when_visible(self):
        import run_chatgpt_texture_batch as texture

        skip = MagicMock()
        skip.is_visible.return_value = True
        controls = MagicMock()
        controls.count.return_value = 1
        controls.nth.return_value = skip
        page = MagicMock()
        page.get_by_text.return_value = controls

        self.assertTrue(texture.dismiss_image_comparison(page))
        skip.click.assert_called_once()

    def test_chatgpt_download_stages_png_beside_output_for_cross_drive_save(self):
        import run_chatgpt_texture_batch as texture
        import run_chatgpt_fabric_grouped_batch as fabric
        for module, download_name, normalize_name in (
            (texture, "download_and_validate", "normalize_image_to_master"),
            (fabric, "download_and_validate_fabric", "normalize_image_to_fabric"),
        ):
            with self.subTest(module=module.__name__), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                downloads = root / "downloads"
                downloads.mkdir()
                target = root / "external_output" / "SKU1" / "image.png"
                page = MagicMock()
                page.expect_download.return_value.__enter__.return_value.value.save_as.side_effect = lambda path: Path(path).write_bytes(b"download")
                def normalize(source, staged):
                    self.assertEqual(staged.parent, target.parent)
                    self.assertNotEqual(staged.parent, downloads)
                    staged.write_bytes(b"valid png")
                    return {"width": 100, "height": 100}
                with patch.object(module, "DOWNLOAD_DIR", downloads), patch.object(module, normalize_name, side_effect=normalize):
                    getattr(module, download_name)(page, MagicMock(), "SKU1", target)
                self.assertEqual(target.read_bytes(), b"valid png")
                self.assertEqual(list(downloads.iterdir()), [])
                self.assertEqual(list(target.parent.iterdir()), [target])

    def test_chatgpt_download_uses_single_image_menu_when_toolbar_only_opens_menu(self):
        import run_chatgpt_texture_batch as texture

        page = MagicMock()
        toolbar_attempt = MagicMock()
        toolbar_attempt.__enter__.side_effect = texture.PlaywrightTimeoutError("no direct download")
        menu_attempt = MagicMock()
        expected_download = MagicMock()
        menu_attempt.__enter__.return_value.value = expected_download
        page.expect_download.side_effect = [toolbar_attempt, menu_attempt]
        menu_choice = MagicMock()
        page.get_by_text.return_value.last = menu_choice

        result = texture.trigger_generated_image_download(page, MagicMock(), 90000)

        self.assertIs(result, expected_download)
        menu_choice.wait_for.assert_called_once_with(state="visible", timeout=15000)
        menu_choice.click.assert_called_once()

    def test_quality_rerun_uses_selected_skus_and_isolated_external_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            data = root / "external"
            group = data / "output" / "custom_engine" / "CHKK"
            raw = data / "textures_raw" / "CHKK"
            raw.mkdir(parents=True)
            files = []
            for sku in ("CHKK1", "CHKK2"):
                (raw / f"{sku}.jpg").touch()
                (group / sku).mkdir(parents=True)
                for name in ("image_1.png", "seamless_texture.png"):
                    image = group / sku / name
                    image.touch()
                    files.append(image)
            instance = app.PipelineController.__new__(app.PipelineController)
            instance.project_dir = root
            instance.lock = threading.RLock()
            instance.quality_folders = {group.parent}
            instance.worker = None
            instance.stop_requested = threading.Event()
            instance.append_log = Mock()
            original = {"paths": {"textures_dir": "original"}}
            (root / "config.json").write_text(json.dumps(original))
            for image in files:
                instance.set_quality_image_failure({"image_path": str(image), "failed": True})
            payload = {"folder": str(group), "image_paths": [str(files[0]), str(files[2])]}
            with patch.object(app, "valid_project_dir", return_value=True), patch.object(app, "locate_python", return_value=app.sys.executable), patch.object(app.threading, "Thread") as thread:
                result = instance.rerun_quality_images(payload)
                self.assertEqual(result["sku_count"], 2)
                queue, run_payload, run_dir = thread.call_args.kwargs["args"]
                self.assertEqual(Path(run_dir), Path(queue[0]["runtime_dir"]).parent)
                self.assertEqual(run_payload["source_mode"], "local")
                self.assertFalse(run_payload["flows"]["fabric"])
                self.assertEqual(run_payload["images_per_chat"], 2)
                self.assertEqual(run_payload["local_source_dir"], str(raw))
                self.assertNotIn("sku", queue[0])
                self.assertEqual(
                    json.loads(Path(run_payload["sku_file"]).read_text(encoding="utf-8")),
                    ["CHKK1", "CHKK2"],
                )
                settings = json.loads((Path(queue[0]["runtime_dir"]) / "config.json").read_text())
                expected_output = str(root / "output" / "chatgpt" / "CHKK")
                for section in ("chatgpt", "paths", "seamless_package"):
                    self.assertEqual(settings[section]["output_dir"], expected_output)
                self.assertEqual(settings["crop"]["source_dir"], str(raw))
                self.assertEqual(json.loads((root / "config.json").read_text()), original)
                steps = instance.build_steps(run_payload, folder="CHKK")
                self.assertEqual([step.key for step, _ in steps], ["crop", "seamless", "package"])
                for _, arguments in steps:
                    self.assertIn("--sku-file", arguments)
                    self.assertIn(run_payload["sku_file"], arguments)
                    self.assertIn("--force", arguments)
                thread.return_value.start.assert_called_once()
            with self.assertRaises(ValueError):
                instance.prepare_quality_rerun({"folder": str(group / "CHKK2"), "image_paths": [str(files[0])]})
            instance.set_quality_image_failure({"image_path": str(files[0]), "failed": False})
            with self.assertRaises(ValueError):
                instance.prepare_quality_rerun({"folder": str(group), "image_paths": [str(files[0])]})

    def test_quality_rerun_removes_isolated_runtime_after_worker_finishes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            run_dir = root / "failed_image_logs" / "run_20260908_124234_805031"
            (run_dir / "0" / "logs").mkdir(parents=True)
            (run_dir / "0" / "config.json").write_text("{}", encoding="utf-8")
            instance = app.PipelineController.__new__(app.PipelineController)
            instance.project_dir = root
            instance.run_queue = Mock(side_effect=RuntimeError("worker failed"))
            instance.append_log = Mock()

            with self.assertRaisesRegex(RuntimeError, "worker failed"):
                instance.run_quality_queue([], {}, run_dir)

            self.assertFalse(run_dir.exists())

    def test_quality_failure_legacy_file_is_preserved_and_migrated_on_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            image = root / "image.png"
            image.touch()
            legacy = {"version": 1, "images": {"existing": {"image_path": "existing"}}}
            (root / "failed_image_ids.json").write_text(json.dumps(legacy))
            instance = app.PipelineController.__new__(app.PipelineController)
            instance.project_dir = root
            instance.lock = threading.RLock()
            instance.quality_folders = {root}
            instance.set_quality_image_failure({"image_path": str(image), "failed": True})
            self.assertEqual(len(instance.read_quality_failures()["images"]), 2)
            saved = json.loads((root / "failed_image_logs" / "failed_image_ids.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["version"], 2)
            self.assertTrue(all("image_path" not in record for record in saved["images"].values()))

    def test_quality_failures_persist_reload_and_remove_by_exact_image_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            selected = root / "chatgpt"
            images = [selected / "CHKK" / code / "image_1.png" for code in ("CHKK1", "CHKK2")]
            for image in images:
                image.parent.mkdir(parents=True)
                image.touch()
            def controller():
                instance = app.PipelineController.__new__(app.PipelineController)
                instance.project_dir = root
                instance.lock = threading.RLock()
                instance.quality_folders = {selected}
                return instance
            instance = controller()
            for image in images:
                instance.set_quality_image_failure({"image_path": str(image), "failed": True})
            records = json.loads((root / "failed_image_logs" / "failed_image_ids.json").read_text(encoding="utf-8"))["images"]
            self.assertEqual(len(records), 2)
            self.assertEqual(
                {record["relative_path"] for record in records.values()},
                {"chatgpt/CHKK/CHKK1/image_1.png", "chatgpt/CHKK/CHKK2/image_1.png"},
            )
            self.assertTrue(all(not Path(record["relative_path"]).is_absolute() for record in records.values()))
            with patch.object(app, "choose_local_folder", return_value=str(selected)):
                reloaded = controller()
                self.assertTrue(all(image["failed"] for image in reloaded.select_quality_folder()["folders"][0]["images"]))
                reloaded.set_quality_image_failure({"image_path": str(images[0]), "failed": False})
                self.assertEqual([image["failed"] for image in reloaded.select_quality_folder()["folders"][0]["images"]], [False, True])
            self.assertEqual(len(reloaded.read_quality_failures()["images"]), 1)
            outside = root / "outside.png"
            outside.touch()
            with self.assertRaises(ValueError):
                instance.set_quality_image_failure({"image_path": str(outside), "failed": True})
            (root / "failed_image_logs" / "failed_image_ids.json").write_text("broken", encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                instance.set_quality_image_failure({"image_path": str(images[0]), "failed": True})
            self.assertEqual((root / "failed_image_logs" / "failed_image_ids.json").read_text(), "broken")

    def test_quality_failure_v1_absolute_path_matches_same_output_after_repo_move(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            selected = root / "output" / "chatgpt"
            image = selected / "CHKK" / "CHKK1" / "image_1.png"
            image.parent.mkdir(parents=True)
            image.touch()
            legacy = {
                "version": 1,
                "images": {
                    "c:/users/alice/old-repo/output/chatgpt/chkk/chkk1/image_1.png": {
                        "image_path": "C:\\Users\\Alice\\old-repo\\output\\chatgpt\\CHKK\\CHKK1\\image_1.png",
                        "relative_path": "CHKK/CHKK1/image_1.png",
                        "file_name": "image_1.png",
                    }
                },
            }
            (root / "failed_image_ids.json").write_text(json.dumps(legacy), encoding="utf-8")
            instance = app.PipelineController.__new__(app.PipelineController)
            instance.project_dir = root
            instance.lock = threading.RLock()
            instance.quality_folders = {selected}

            normalized = instance.read_quality_failures()

            self.assertIn("data:output/chatgpt/chkk/chkk1/image_1.png", normalized["images"])
            self.assertIn(instance.quality_failure_key(image), normalized["images"])

    def test_quality_chatgpt_groups_collect_code_images_in_numeric_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "chatgpt"
            for group in ("M10HL", "M2HL"):
                for code in ("NH10", "NH2", "NH1"):
                    folder = root / group / code
                    folder.mkdir(parents=True)
                    for name in (
                        "image_10.png", "image_2.png", "seamless_texture.png",
                        "QC_offset50.png", "QC_tile_3x3.png", "QC_tile_15x15.png",
                        "metadata.json",
                    ):
                        (folder / name).touch()
                (root / group / "archive.zip").touch()

            groups = app.list_quality_folder_groups(root)

            self.assertEqual([group["name"] for group in groups], ["M2HL", "M10HL"])
            for group in groups:
                self.assertEqual(
                    [image["relative_path"] for image in group["images"]],
                    [f"{code}/{name}" for code in ("NH1", "NH2", "NH10")
                     for name in ("image_2.png", "image_10.png", "seamless_texture.png")],
                )

    def test_quality_parent_keeps_child_images_separate_and_empty_folders_visible(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "B").mkdir()
            (root / "A" / "nested").mkdir(parents=True)
            (root / "A" / "nested" / "fabric.PNG").touch()
            (root / "parent.jpg").touch()
            (root / "notes.txt").touch()

            groups = app.list_quality_folder_groups(root)

            self.assertEqual([group["name"] for group in groups[:2]], ["A", "B"])
            self.assertEqual([image["relative_path"] for image in groups[0]["images"]], ["nested/fabric.PNG"])
            self.assertEqual(groups[1]["images"], [])
            self.assertEqual([image["name"] for image in groups[2]["images"]], ["parent.jpg"])

    def test_quality_image_only_folder_opens_its_images(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "fabric.jpg").touch()
            (root / "notes.txt").touch()

            groups = app.list_quality_folder_groups(root)

            self.assertEqual(len(groups), 1)
            self.assertEqual(groups[0]["path"], str(root.resolve()))
            self.assertEqual([image["name"] for image in groups[0]["images"]], ["fabric.jpg"])

    def test_standalone_step_relaunches_the_executable_as_worker(self):
        step = app.FlowStep("crop", "Crop", "crop_textures.py")
        with patch.object(app, "is_frozen", return_value=True):
            command = app.build_step_command(
                Path("ignored-python.exe"), Path("ignored-project"), step, ["--dry-run"]
            )

        self.assertEqual(command[1:3], [app.EMBEDDED_WORKER_FLAG, "crop_textures.py"])
        self.assertEqual(command[-1], "--dry-run")

    def test_standalone_first_run_creates_writable_defaults(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / "bundle" / "runtime_assets"
            project = root / "portable-app"
            (bundle / "prompts").mkdir(parents=True)
            (bundle / "config.default.json").write_text(
                json.dumps({"google_drive": {"enabled": False}}), encoding="utf-8"
            )
            (bundle / "prompts" / "sample.md").write_text("prompt", encoding="utf-8")

            with (
                patch.object(app, "is_frozen", return_value=True),
                patch.object(app.sys, "_MEIPASS", str(root / "bundle"), create=True),
            ):
                app.ensure_runtime_layout(project)

            self.assertTrue((project / "config.json").is_file())
            self.assertEqual(
                (project / "prompts" / "sample.md").read_text(encoding="utf-8"),
                "prompt",
            )
            self.assertTrue((project / "output").is_dir())

    def test_build_default_config_removes_private_machine_settings(self):
        source = {
            "telegram": {"enabled": True, "bot_token": "secret", "chat_id": "123"},
            "google_drive": {
                "enabled": True,
                "share_url": "https://drive.google.com/drive/folders/private",
                "urls": [{"url": "private", "folder": "ABC"}],
                "destination_dir": "textures_raw\\ABC",
            },
            "paths": {"textures_dir": "textures\\ABC"},
            "crop": {},
            "seamless_package": {},
            "chatgpt": {},
            "chatgpt_texture": {},
            "chatgpt_texture_grouped": {},
            "chatgpt_fabric_grouped": {},
            "flow_texture": {},
            "chatgpt_project": {"project_url": "private"},
        }

        result = create_default_config(source)

        self.assertFalse(result["telegram"]["enabled"])
        self.assertEqual(result["telegram"]["bot_token"], "")
        self.assertFalse(result["google_drive"]["enabled"])
        self.assertEqual(result["google_drive"]["urls"], [])
        self.assertEqual(result["paths"]["textures_dir"], "textures")
        self.assertEqual(result["chatgpt_project"]["project_url"], "")

    def test_drive_url_validation(self):
        self.assertTrue(
            app.validate_drive_url(
                "https://drive.google.com/drive/folders/ABC123?usp=sharing"
            )
        )
        self.assertFalse(app.validate_drive_url("https://example.com/drive/folders/ABC"))

    def test_pipeline_arguments_and_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = {
                "google_drive": {"enabled": False, "share_url": ""},
                "chatgpt_texture_grouped": {"images_per_chat": 5},
            }
            (root / "config.json").write_text(
                json.dumps(config), encoding="utf-8"
            )
            script_names = (
                "import_google_drive.py",
                "crop_textures.py",
                "run_chatgpt_texture_grouped_batch.py",
                "run_chatgpt_fabric_grouped_batch.py",
                "package_seamless_textures.py",
            )
            for name in script_names:
                (root / name).write_text(
                    "import sys\nprint('TEST_STEP', *sys.argv[1:])\n",
                    encoding="utf-8",
                )

            controller = app.PipelineController()
            controller.project_dir = root
            controller.python_exe = Path(app.sys.executable)
            payload = {
                "drive_url": "",
                "sku": "SP1M29",
                "limit": "2",
                "images_per_chat": 4,
                "dry_run": True,
                "force": False,
                "flows": {
                    "import": False,
                    "crop": True,
                    "seamless": True,
                    "package": True,
                },
            }
            controller.start_pipeline(payload)
            deadline = time.monotonic() + 10
            while controller.is_running() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertFalse(controller.is_running())
            self.assertEqual(controller.status, "Hoàn thành")
            self.assertEqual(controller.log_text.count("TEST_STEP"), 3)
            self.assertIn("--sku SP1M29", controller.log_text)
            saved = json.loads((root / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(
                saved["chatgpt_texture_grouped"]["images_per_chat"], 4
            )

    def test_fabric_progress_lists_created_and_pending_skus(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "textures_raw"
            source.mkdir()
            (source / "SKU001.jpg").write_bytes(b"source-1")
            (source / "SKU002.png").write_bytes(b"source-2")
            (source / "SKU003.png").write_bytes(b"source-3")
            (source / "SKU004.png").write_bytes(b"source-4")
            (source / "notes.txt").write_text("ignore", encoding="utf-8")
            destination = root / "output" / "chatgpt" / "SKU001"
            destination.mkdir(parents=True)
            (destination / "seamless_texture.png").write_bytes(b"output")
            (destination / "image_1.png").write_bytes(b"swatch")
            seamless_only = root / "output" / "chatgpt" / "SKU002"
            seamless_only.mkdir(parents=True)
            (seamless_only / "seamless_texture.png").write_bytes(b"output")
            swatch_only = root / "output" / "chatgpt" / "SKU003"
            swatch_only.mkdir(parents=True)
            (swatch_only / "image_1.png").write_bytes(b"swatch")
            config = {
                "google_drive": {"destination_dir": "textures_raw"},
                "crop": {"output_dir": "textures_cropped"},
                "seamless_package": {
                    "output_dir": "output/chatgpt",
                    "filename": "seamless_texture.png",
                },
            }

            progress = app.fabric_progress(root, config)

            self.assertEqual(progress["total"], 4)
            self.assertEqual(progress["percent"], 25)
            self.assertEqual([item["sku"] for item in progress["created"]], ["SKU001"])
            self.assertEqual([item["sku"] for item in progress["pending"]], ["SKU002", "SKU003", "SKU004"])
            self.assertEqual(
                [(item["has_seamless"], item["has_fabric"]) for item in progress["pending"]],
                [(True, False), (False, True), (False, False)],
            )

    def test_local_source_is_saved_and_drive_import_is_skipped(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            local_source = root / "local fabrics"
            local_source.mkdir()
            config = {
                "google_drive": {
                    "enabled": True,
                    "share_url": "https://drive.google.com/drive/folders/ABC",
                    "destination_dir": "textures_raw",
                },
                "crop": {"source_dir": "textures_raw"},
                "chatgpt_texture_grouped": {"images_per_chat": 5},
            }
            (root / "config.json").write_text(
                json.dumps(config), encoding="utf-8"
            )
            controller = app.PipelineController()
            controller.project_dir = root
            payload = {
                "source_mode": "local",
                "local_source_dir": str(local_source),
                "drive_url": config["google_drive"]["share_url"],
                "images_per_chat": 6,
                "flows": {"import": True, "crop": True},
            }

            controller.save_settings(payload)
            steps = controller.build_steps(payload)
            saved = json.loads((root / "config.json").read_text(encoding="utf-8"))

            self.assertEqual(saved["app_ui"]["source_mode"], "local")
            self.assertEqual(saved["crop"]["source_dir"], str(local_source))
            self.assertEqual([step.key for step, _ in steps], ["crop"])

    def test_apply_folder_config_updates_all_directories(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = {
                "paths": {"textures_dir": "textures", "output_dir": "output"},
                "google_drive": {"enabled": False, "share_url": "", "destination_dir": "textures_raw"},
                "crop": {"source_dir": "textures_raw", "output_dir": "textures_cropped"},
                "seamless_package": {"output_dir": "output/chatgpt"},
                "chatgpt": {"output_dir": "output/chatgpt"},
                "chatgpt_texture_grouped": {"raw_dir": "textures_cropped"},
                "chatgpt_fabric_grouped": {"raw_dir": "textures_cropped", "output_dir": "output/chatgpt"},
            }
            (root / "config.json").write_text(
                json.dumps(config), encoding="utf-8"
            )
            controller = app.PipelineController()
            controller.project_dir = root

            # Test applying folder "ABC"
            controller.apply_folder_config(
                "https://drive.google.com/drive/folders/ABC_LINK", "ABC"
            )

            saved = json.loads((root / "config.json").read_text(encoding="utf-8"))
            self.assertIn("ABC", saved["google_drive"]["destination_dir"])
            self.assertIn("ABC", saved["crop"]["source_dir"])
            self.assertIn("ABC", saved["crop"]["output_dir"])
            self.assertIn("ABC", saved["chatgpt_texture_grouped"]["raw_dir"])
            self.assertIn("ABC", saved["chatgpt_fabric_grouped"]["raw_dir"])
            self.assertIn("ABC", saved["chatgpt_fabric_grouped"]["output_dir"])
            self.assertEqual(saved["chatgpt_fabric_grouped"]["input_mode"], "seamless")
            self.assertIn("ABC", saved["seamless_package"]["output_dir"])
            self.assertIn("ABC", saved["paths"]["textures_dir"])
            self.assertIn("ABC", saved["paths"]["output_dir"])

    def test_swatch_prerequisites_report_only_missing_seamless_skus(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = root / "textures_raw" / "ABC"
            raw.mkdir(parents=True)
            (raw / "ABC1.jpg").touch()
            (raw / "ABC2.jpg").touch()
            (root / "config.json").write_text(json.dumps({"google_drive": {}}), encoding="utf-8")
            ready = root / "output" / "chatgpt" / "ABC" / "ABC1"
            ready.mkdir(parents=True)
            Image.new("RGB", (8, 8), "black").save(ready / "seamless_texture.png")
            controller = app.PipelineController.__new__(app.PipelineController)
            controller.project_dir = root

            result = controller.check_swatch_prerequisites({
                "source_mode": "drive",
                "folder": "ABC",
            })

            self.assertEqual(result["checked_count"], 2)
            self.assertEqual(result["missing_skus"], ["ABC/ABC2"])

    def test_swatch_prerequisites_use_drive_listing_before_import(self):
        import import_google_drive
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "config.json").write_text(json.dumps({"google_drive": {}}), encoding="utf-8")
            controller = app.PipelineController.__new__(app.PipelineController)
            controller.project_dir = root
            remote_images = [
                {"name": "NEW1.jpg"},
                {"name": "NEW2.jpg"},
            ]
            with (
                patch.object(import_google_drive, "list_public_folder", return_value=[]),
                patch.object(import_google_drive, "select_images", return_value=(remote_images, 0, 0)),
            ):
                result = controller.check_swatch_prerequisites({
                    "source_mode": "drive",
                    "folder": "NEW",
                    "url": "https://drive.google.com/drive/folders/test",
                    "flows": {"import": True},
                })

            self.assertEqual(result["checked_count"], 2)
            self.assertEqual(result["missing_skus"], ["NEW/NEW1", "NEW/NEW2"])

    def test_swatch_prerequisites_fall_back_to_unlinked_local_folders(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = root / "textures_raw" / "DEHQ"
            raw.mkdir(parents=True)
            (raw / "DEHQ1.jpg").touch()
            (raw / "DEHQ2.jpg").touch()
            ready = root / "output" / "chatgpt" / "DEHQ" / "DEHQ2"
            ready.mkdir(parents=True)
            Image.new("RGB", (8, 8), "black").save(ready / "seamless_texture.png")
            (root / "config.json").write_text(
                json.dumps({"google_drive": {"urls": []}}), encoding="utf-8"
            )
            controller = app.PipelineController.__new__(app.PipelineController)
            controller.project_dir = root

            result = controller.check_swatch_prerequisites({
                "source_mode": "drive",
                "drive_urls": [],
                "flows": {"import": True},
            })

            self.assertTrue(result["local_fallback"])
            self.assertEqual(result["checked_count"], 2)
            self.assertEqual(result["missing_skus"], ["DEHQ/DEHQ1"])

    def test_single_sku_chatgpt_runs_only_missing_output_stages(self):
        cases = (
            (False, False, {"crop": True, "seamless": True, "fabric": True, "package": True}),
            (True, False, {"crop": False, "seamless": False, "fabric": True, "package": False}),
            (False, True, {"crop": True, "seamless": True, "fabric": False, "package": True}),
        )
        for has_seamless, has_fabric, expected in cases:
            with self.subTest(has_seamless=has_seamless, has_fabric=has_fabric):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    sku_dir = root / "output" / "chatgpt" / "ABC" / "ABC1"
                    sku_dir.mkdir(parents=True)
                    if has_seamless:
                        (sku_dir / "seamless_texture.png").write_bytes(b"seamless")
                    if has_fabric:
                        (sku_dir / "image_1.png").write_bytes(b"swatch")
                    (root / "config.json").write_text(
                        json.dumps({"app_ui": {"source_mode": "drive"}, "google_drive": {}}),
                        encoding="utf-8",
                    )
                    controller = app.PipelineController.__new__(app.PipelineController)
                    controller.project_dir = root
                    controller.append_log = Mock()
                    controller.apply_folder_config = Mock()
                    controller.start_pipeline = Mock()

                    result = controller.run_single_sku({
                        "sku": "ABC1", "folder": "ABC", "engine": "chatgpt", "images_per_chat": 1,
                    })

                    self.assertTrue(result["started"])
                    run_payload = controller.start_pipeline.call_args.args[0]
                    self.assertFalse(controller.start_pipeline.call_args.kwargs["persist_settings"])
                    self.assertFalse(run_payload["flows"]["import"])
                    for key, value in expected.items():
                        self.assertEqual(run_payload["flows"][key], value)

    def test_single_sku_chatgpt_skips_when_both_outputs_exist(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sku_dir = root / "output" / "chatgpt" / "ABC" / "ABC1"
            sku_dir.mkdir(parents=True)
            (sku_dir / "seamless_texture.png").write_bytes(b"seamless")
            (sku_dir / "image_1.png").write_bytes(b"swatch")
            (root / "config.json").write_text(
                json.dumps({"app_ui": {"source_mode": "drive"}, "google_drive": {}}),
                encoding="utf-8",
            )
            controller = app.PipelineController.__new__(app.PipelineController)
            controller.project_dir = root
            controller.append_log = Mock()
            controller.start_pipeline = Mock()

            result = controller.run_single_sku({"sku": "ABC1", "folder": "ABC", "engine": "chatgpt"})

            self.assertFalse(result["started"])
            controller.start_pipeline.assert_not_called()

    def test_transient_pipeline_does_not_persist_ui_settings(self):
        controller = app.PipelineController.__new__(app.PipelineController)
        controller.worker = None
        controller.stop_requested = threading.Event()
        controller.append_log = Mock()
        controller.validate_run = Mock()
        controller.save_settings = Mock()
        worker = Mock()
        with patch.object(app.threading, "Thread", return_value=worker):
            controller.start_pipeline(
                {"source_mode": "local", "images_per_chat": 1},
                persist_settings=False,
            )

        controller.validate_run.assert_called_once()
        controller.save_settings.assert_not_called()
        worker.start.assert_called_once()

    def test_pipeline_uses_selected_folder_queue_without_replacing_saved_links(self):
        controller = app.PipelineController.__new__(app.PipelineController)
        controller.worker = None
        controller.stop_requested = threading.Event()
        controller.append_log = Mock()
        controller.validate_run = Mock()
        controller.save_settings = Mock()
        worker = Mock()
        payload = {
            "source_mode": "drive",
            "drive_urls": [
                {"folder": "FIRST", "url": "https://drive.google.com/drive/folders/FIRST"},
                {"folder": "SECOND", "url": "https://drive.google.com/drive/folders/SECOND"},
            ],
            "pipeline_drive_urls": [
                {"folder": "SECOND", "url": "https://drive.google.com/drive/folders/SECOND"},
            ],
        }

        with patch.object(app.threading, "Thread", return_value=worker) as thread_factory:
            controller.start_pipeline(payload)

        controller.save_settings.assert_called_once_with(payload)
        queue = thread_factory.call_args.kwargs["args"][0]
        self.assertEqual([item["folder"] for item in queue], ["SECOND"])
        worker.start.assert_called_once()

    def test_selected_drive_folder_requires_link_when_import_is_enabled(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "config.json").write_text("{}", encoding="utf-8")
            controller = app.PipelineController.__new__(app.PipelineController)
            controller.project_dir = root
            with patch("veo3_auto_app.valid_project_dir", return_value=True), \
                    patch("veo3_auto_app.locate_python", return_value=Path(app.sys.executable)):
                with self.assertRaisesRegex(ValueError, "NHD"):
                    controller.validate_run({
                        "source_mode": "drive",
                        "engine": "chatgpt",
                        "pipeline_drive_urls": [{"folder": "NHD", "url": ""}],
                        "flows": {"import": True},
                    })

    def test_skipping_active_folder_terminates_it_and_runs_next_folder(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / "second-folder-ran.txt"
            (root / "config.json").write_text("{}", encoding="utf-8")
            (root / "crop_textures.py").write_text(
                "import json, pathlib, time\n"
                "config = json.loads(pathlib.Path('config.json').read_text())\n"
                "folder = pathlib.Path(config['google_drive']['destination_dir']).name\n"
                "if folder == 'FIRST': time.sleep(20)\n"
                f"else: pathlib.Path(r'{marker}').write_text(folder)\n",
                encoding="utf-8",
            )
            controller = app.PipelineController()
            controller.project_dir = root
            controller.python_exe = Path(app.sys.executable)
            controller.validate_run = Mock()
            controller.save_settings = Mock()
            controller.notify_telegram = Mock()
            payload = {
                "source_mode": "drive",
                "pipeline_drive_urls": [
                    {"folder": "FIRST", "url": ""},
                    {"folder": "SECOND", "url": ""},
                ],
                "limit": "1",
                "auto_retry_enabled": False,
                "flows": {"import": False, "crop": True},
            }

            controller.start_pipeline(payload)
            deadline = time.monotonic() + 5
            while controller.active_running_folder != "FIRST" and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual(controller.active_running_folder, "FIRST")

            result = controller.skip_pipeline_folder({"folder": "FIRST"})
            self.assertTrue(result["was_active"])
            deadline = time.monotonic() + 8
            while controller.is_running() and time.monotonic() < deadline:
                time.sleep(0.05)

            self.assertFalse(
                controller.is_running(),
                f"active={controller.active_running_folder!r}\n{controller.log_text}",
            )
            self.assertEqual(marker.read_text(encoding="utf-8"), "SECOND")
            self.assertEqual(controller.status, "Hoàn thành")
            self.assertIn("chuyển sang thư mục tiếp theo", controller.log_text)

    def test_stop_escalates_from_break_to_terminate_and_kill(self):
        controller = app.PipelineController.__new__(app.PipelineController)
        controller.lock = threading.RLock()
        controller.append_log = Mock()
        controller.stop_escalation_thread = None
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [
            subprocess.TimeoutExpired("worker", 3),
            subprocess.TimeoutExpired("worker", 2),
            0,
        ]

        controller._stop_process_with_escalation(process)

        process.send_signal.assert_called_once_with(app.signal.CTRL_BREAK_EVENT)
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        self.assertEqual(
            [call.kwargs["timeout"] for call in process.wait.call_args_list],
            [3, 2, 2],
        )

    def test_compute_drive_folders_stats_and_filter(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            # Create folder ABC with 2 SKUs (1 created, 1 pending)
            raw_abc = root / "textures_raw" / "ABC"
            raw_abc.mkdir(parents=True)
            (raw_abc / "SKU_A1.jpg").write_bytes(b"raw-a1")
            (raw_abc / "SKU_A2.jpg").write_bytes(b"raw-a2")

            out_abc = root / "output" / "chatgpt" / "ABC" / "SKU_A1"
            out_abc.mkdir(parents=True)
            (out_abc / "seamless_texture.png").write_bytes(b"png-data")
            (out_abc / "image_1.png").write_bytes(b"fabric-data")

            # Create folder XYZ with 1 SKU (1 created)
            raw_xyz = root / "textures_raw" / "XYZ"
            raw_xyz.mkdir(parents=True)
            (raw_xyz / "SKU_X1.jpg").write_bytes(b"raw-x1")

            out_xyz = root / "output" / "chatgpt" / "XYZ" / "SKU_X1"
            out_xyz.mkdir(parents=True)
            (out_xyz / "seamless_texture.png").write_bytes(b"png-data")

            config = {
                "google_drive": {
                    "urls": [
                        {"url": "https://drive.google.com/drive/folders/ABC_URL", "folder": "ABC"},
                        {"url": "https://drive.google.com/drive/folders/XYZ_URL", "folder": "XYZ"},
                    ]
                },
                "seamless_package": {"output_dir": "output/chatgpt"},
                "chatgpt": {"output_dir": "output/chatgpt"},
            }

            stats = app.compute_drive_folders_stats(root, config)
            self.assertEqual(stats["total_folders"], 2)
            self.assertEqual(stats["total_skus"], 3)
            self.assertEqual(stats["total_created"], 1)

            abc_stat = next(s for s in stats["folders"] if s["folder"] == "ABC")
            self.assertEqual(abc_stat["total"], 2)
            self.assertEqual(abc_stat["created_count"], 1)
            self.assertEqual(abc_stat["pending_count"], 1)
            self.assertEqual(abc_stat["percent"], 50)

            xyz_stat = next(s for s in stats["folders"] if s["folder"] == "XYZ")
            self.assertEqual(xyz_stat["total"], 1)
            self.assertEqual(xyz_stat["created_count"], 0)
            self.assertEqual(xyz_stat["pending_count"], 1)
            self.assertEqual(xyz_stat["percent"], 0)

            # Test fabric_progress with folder_filter
            prog_abc = app.fabric_progress(root, config, folder_filter="ABC")
            self.assertEqual(prog_abc["total"], 2)
            self.assertEqual([item["sku"] for item in prog_abc["created"]], ["SKU_A1"])
            self.assertEqual([item["sku"] for item in prog_abc["pending"]], ["SKU_A2"])

            prog_xyz = app.fabric_progress(root, config, folder_filter="XYZ")
            self.assertEqual(prog_xyz["total"], 1)
            self.assertEqual(prog_xyz["created"], [])
            self.assertEqual([item["sku"] for item in prog_xyz["pending"]], ["SKU_X1"])
            self.assertTrue(prog_xyz["pending"][0]["has_seamless"])
            self.assertFalse(prog_xyz["pending"][0]["has_fabric"])

    def test_grouped_texture_browser_close_keeps_sku_pending_without_retry_cost(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "SKU001.png"
            source.write_bytes(b"source")
            target = Path(temporary) / "texture_SKU001.png"
            page = Mock()
            page.is_closed.return_value = True
            status = {}
            closed_error = RuntimeError(
                "Target page, context or browser has been closed"
            )

            with (
                patch.object(
                    grouped, "activate_create_image_mode", side_effect=closed_error
                ),
                patch.object(grouped, "save_status"),
            ):
                result = grouped.process_turn(
                    page,
                    "SKU001",
                    source,
                    target,
                    "prompt",
                    "full_master",
                    1,
                    5,
                    status,
                    "master-hash",
                )

            self.assertEqual(result, "RECONNECT")
            self.assertEqual(status["SKU001"]["status"], "pending")
            self.assertEqual(status["SKU001"]["error_type"], "browser_closed")
            self.assertEqual(status["SKU001"]["retries"], 0)

    def test_get_image_file_and_sku_details(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cropped_dir = root / "textures_cropped" / "ABC"
            cropped_dir.mkdir(parents=True)
            (cropped_dir / "SKU100.png").write_bytes(b"png-data")
            output_dir = root / "output" / "chatgpt" / "ABC" / "SKU100"
            output_dir.mkdir(parents=True)
            (output_dir / "seamless_texture.png").write_bytes(b"output-data")
            (output_dir / "image_1.png").write_bytes(b"fabric-data")
            status_file = root / "status_chatgpt_texture_grouped.json"
            status_file.write_text(
                json.dumps({
                    "SKU100": {
                        "status": "done",
                        "started_at": "2026-08-30 08:00:00",
                        "completed_at": "2026-08-30 08:01:25",
                    }
                }),
                encoding="utf-8",
            )
            config = {
                "google_drive": {"destination_dir": "textures_raw"},
                "crop": {"output_dir": "textures_cropped"},
                "seamless_package": {
                    "output_dir": "output/chatgpt",
                    "filename": "seamless_texture.png",
                },
            }
            (root / "config.json").write_text(
                json.dumps(config), encoding="utf-8"
            )

            # Test image lookups with and without folder
            img_output_explicit = app.get_image_file(root, "SKU100", "output", folder="ABC")
            img_output_auto = app.get_image_file(root, "SKU100", "output")
            img_cropped = app.get_image_file(root, "SKU100", "cropped", folder="ABC")
            img_fabric = app.get_image_file(root, "SKU100", "fabric", folder="ABC")
            self.assertIsNotNone(img_output_explicit)
            self.assertIsNotNone(img_output_auto)
            self.assertIsNotNone(img_cropped)
            self.assertIsNotNone(img_fabric)

            # Test SKU details
            details = app.get_sku_details(root, "SKU100")
            self.assertEqual(details["sku"], "SKU100")
            self.assertTrue(details["is_created"])
            self.assertEqual(details["folder"], "ABC")
            self.assertEqual(details["duration_text"], "1m 25s")

            # Test timing stats calculation
            stats = app.compute_historical_timing_stats(root)
            self.assertEqual(stats["completed_count"], 1)
            self.assertEqual(stats["avg_duration_seconds"], 85.0)

    def test_sku_details_do_not_use_swatch_or_intermediate_texture_as_seamless(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sku_dir = root / "output" / "chatgpt" / "DEHQ" / "DEHQ2"
            sku_dir.mkdir(parents=True)
            (sku_dir / "image_1.png").write_bytes(b"swatch")
            texture_dir = root / "textures" / "DEHQ"
            texture_dir.mkdir(parents=True)
            (texture_dir / "texture_DEHQ2.png").write_bytes(b"intermediate")
            (root / "config.json").write_text(
                json.dumps({"seamless_package": {"filename": "seamless_texture.png"}}),
                encoding="utf-8",
            )

            details = app.get_sku_details(root, "DEHQ2", folder="DEHQ")

            self.assertIsNone(app.get_image_file(root, "DEHQ2", "final_seamless", folder="DEHQ"))
            self.assertIsNone(details["output"])
            self.assertIsNone(details["seamless"])
            self.assertIsNotNone(details["fabric"])
            self.assertFalse(details["is_created"])

    def test_seamless_packaging_and_organization_for_drive_folders(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            from PIL import Image
            import seamless_packaging as sp

            # Setup raw, textures, and output directories
            raw_dir = root / "textures_raw" / "COLLECTION_A"
            raw_dir.mkdir(parents=True)
            (raw_dir / "FABRIC_01.jpg").write_bytes(b"raw-bytes")

            tex_dir = root / "textures"
            tex_dir.mkdir(parents=True)
            img = Image.new("RGB", (2048, 2048), color=(255, 0, 0))
            img.save(tex_dir / "texture_FABRIC_01.png", format="PNG")

            config = {
                "paths": {"textures_dir": str(tex_dir), "output_dir": str(root / "output")},
                "chatgpt": {"output_dir": str(root / "output" / "chatgpt")},
                "seamless_package": {
                    "enabled": True,
                    "output_dir": str(root / "output" / "chatgpt"),
                    "filename": "seamless_texture.png",
                },
            }
            (root / "config.json").write_text(json.dumps(config), encoding="utf-8")
            # 1. Test discover_textures auto-detects folder and copies to textures/COLLECTION_A/
            items = sp.discover_textures(tex_dir)
            self.assertEqual(len(items), 1)
            sku, source_path, folder = items[0]
            self.assertEqual(sku, "FABRIC_01")
            self.assertEqual(folder, "COLLECTION_A")

            # 2. Package texture
            res = sp.package_seamless_texture(sku, source_path, folder=folder, config=config)
            self.assertEqual(res["status"], "packaged")
            expected_out = root / "output" / "chatgpt" / "COLLECTION_A" / "FABRIC_01" / "seamless_texture.png"
            self.assertTrue(expected_out.exists())
            self.assertTrue((tex_dir / "COLLECTION_A" / "texture_FABRIC_01.png").exists())

            # 3. Test organize_existing_folder_outputs in app
            app.organize_existing_folder_outputs(root)
            self.assertTrue(expected_out.exists())

    def test_auto_retry_settings_save_and_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = {"app_ui": {}}
            (root / "config.json").write_text(json.dumps(config), encoding="utf-8")
            controller = app.PipelineController()
            controller.project_dir = root

            payload = {
                "source_mode": "local",
                "local_source_dir": str(root),
                "images_per_chat": 5,
                "auto_retry_enabled": True,
                "auto_retry_delay_seconds": 45,
                "auto_retry_max_attempts": 3,
            }
            controller.save_settings(payload)
            state = controller.state()
            self.assertTrue(state["auto_retry_enabled"])
            self.assertEqual(state["auto_retry_delay_seconds"], 45)
            self.assertEqual(state["auto_retry_max_attempts"], 3)

    def test_auto_retry_on_step_failure_and_recovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_dir = root / "textures_raw"
            raw_dir.mkdir(parents=True)
            (raw_dir / "SKU_RETRY.jpg").write_bytes(b"raw-bytes")

            config = {
                "google_drive": {"enabled": False, "share_url": ""},
                "chatgpt_texture_grouped": {"images_per_chat": 5},
                "app_ui": {
                    "auto_retry_enabled": True,
                    "auto_retry_delay_seconds": 1,
                    "auto_retry_max_attempts": 3,
                },
            }
            (root / "config.json").write_text(json.dumps(config), encoding="utf-8")

            # Script fails on 1st run, creates flag and succeeds on 2nd run
            flag_file = root / "ran_once.flag"
            script_code = (
                "import sys, pathlib\n"
                f"flag = pathlib.Path(r'{flag_file}')\n"
                "if not flag.exists():\n"
                "    flag.write_text('done')\n"
                "    sys.exit(1)\n"
                "print('RETRY_SUCCESS')\n"
                "sys.exit(0)\n"
            )
            for name in ("import_google_drive.py", "crop_textures.py", "run_chatgpt_texture_grouped_batch.py", "run_chatgpt_fabric_grouped_batch.py", "package_seamless_textures.py"):
                (root / name).write_text(script_code, encoding="utf-8")

            controller = app.PipelineController()
            controller.project_dir = root
            controller.python_exe = Path(app.sys.executable)
            controller.notify_telegram = Mock()

            payload = {
                "source_mode": "local",
                "local_source_dir": str(raw_dir),
                "sku": "SKU_RETRY",
                "images_per_chat": 5,
                "auto_retry_enabled": True,
                "auto_retry_delay_seconds": 1,
                "auto_retry_max_attempts": 3,
                "flows": {
                    "import": False,
                    "crop": True,
                    "seamless": False,
                    "fabric": False,
                    "package": False,
                },
            }
            controller.start_pipeline(payload)
            deadline = time.monotonic() + 10
            while controller.is_running() and time.monotonic() < deadline:
                time.sleep(0.05)

            self.assertFalse(controller.is_running())
            self.assertEqual(controller.status, "Hoàn thành")
            self.assertIn("[AUTO-RETRY]", controller.log_text)
            self.assertIn("RETRY_SUCCESS", controller.log_text)
            events = [call.args[0] for call in controller.notify_telegram.call_args_list]
            self.assertEqual(events, ["pipeline_start", "pipeline_complete"])
            self.assertEqual(controller.telegram_noncritical_errors, [])

    def test_auto_retry_stops_when_max_attempts_exceeded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_dir = root / "textures_raw"
            raw_dir.mkdir(parents=True)
            (raw_dir / "SKU_FAIL.jpg").write_bytes(b"raw-bytes")

            config = {
                "google_drive": {"enabled": False, "share_url": ""},
                "chatgpt_texture_grouped": {"images_per_chat": 5},
                "app_ui": {
                    "auto_retry_enabled": True,
                    "auto_retry_delay_seconds": 1,
                    "auto_retry_max_attempts": 2,
                },
            }
            (root / "config.json").write_text(json.dumps(config), encoding="utf-8")

            script_code = "import sys\nprint('FAIL_STEP')\nsys.exit(1)\n"
            for name in ("import_google_drive.py", "crop_textures.py", "run_chatgpt_texture_grouped_batch.py", "run_chatgpt_fabric_grouped_batch.py", "package_seamless_textures.py"):
                (root / name).write_text(script_code, encoding="utf-8")

            controller = app.PipelineController()
            controller.project_dir = root
            controller.python_exe = Path(app.sys.executable)
            controller.notify_telegram = Mock()

            payload = {
                "source_mode": "local",
                "local_source_dir": str(raw_dir),
                "sku": "SKU_FAIL",
                "images_per_chat": 5,
                "auto_retry_enabled": True,
                "auto_retry_delay_seconds": 1,
                "auto_retry_max_attempts": 2,
                "flows": {
                    "import": False,
                    "crop": True,
                    "seamless": False,
                    "fabric": False,
                    "package": False,
                },
            }
            controller.start_pipeline(payload)
            deadline = time.monotonic() + 10
            while controller.is_running() and time.monotonic() < deadline:
                time.sleep(0.05)

            self.assertFalse(controller.is_running())
            self.assertEqual(controller.status, "Lỗi")
            self.assertIn("Đã đạt giới hạn số lần thử lại tối đa", controller.log_text)
            events = [call.args[0] for call in controller.notify_telegram.call_args_list]
            self.assertEqual(events, ["pipeline_start", "pipeline_failed"])
            self.assertTrue(all(item["state"] == "failed" for item in controller.telegram_noncritical_errors))

    def test_auto_retry_stops_on_user_stop_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw_dir = root / "textures_raw"
            raw_dir.mkdir(parents=True)
            (raw_dir / "SKU_STOP.jpg").write_bytes(b"raw-bytes")

            config = {
                "google_drive": {"enabled": False, "share_url": ""},
                "chatgpt_texture_grouped": {"images_per_chat": 5},
                "app_ui": {
                    "auto_retry_enabled": True,
                    "auto_retry_delay_seconds": 10,
                    "auto_retry_max_attempts": 5,
                },
            }
            (root / "config.json").write_text(json.dumps(config), encoding="utf-8")

            script_code = "import sys\nprint('FAIL_ONCE')\nsys.exit(1)\n"
            script_names = (
                "import_google_drive.py",
                "crop_textures.py",
                "run_chatgpt_texture_grouped_batch.py",
                "run_chatgpt_fabric_grouped_batch.py",
                "run_flow_texture_batch.py",
                "package_seamless_textures.py",
            )
            for name in script_names:
                (root / name).write_text(script_code, encoding="utf-8")

            controller = app.PipelineController()
            controller.project_dir = root
            controller.python_exe = Path(app.sys.executable)

            payload = {
                "source_mode": "local",
                "local_source_dir": str(raw_dir),
                "sku": "SKU_STOP",
                "images_per_chat": 5,
                "auto_retry_enabled": True,
                "auto_retry_delay_seconds": 10,
                "auto_retry_max_attempts": 5,
                "flows": {
                    "import": False,
                    "crop": True,
                    "seamless": False,
                    "fabric": False,
                    "flow_texture": False,
                    "package": False,
                },
            }
            controller.start_pipeline(payload)
            time.sleep(0.5)
            # Send stop command while it is in retry delay
            controller.stop()
            deadline = time.monotonic() + 5
            while controller.is_running() and time.monotonic() < deadline:
                time.sleep(0.05)

            self.assertFalse(controller.is_running())
            self.assertEqual(controller.status, "Đã dừng")

    def test_prompt_settings_saving_and_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = {
                "google_drive": {"enabled": False, "share_url": ""},
                "chatgpt_texture_grouped": {
                    "images_per_chat": 5,
                    "prompt_mode": "attachment",
                    "master_prompt_file": "prompts/prompts_attachments/01_TEXTURE_SEAMLESS_MASTER.md",
                    "prompt_text": "",
                },
                "chatgpt_fabric_grouped": {
                    "images_per_chat": 5,
                    "prompt_mode": "attachment",
                    "master_prompt_file": "prompts/prompts_attachments/02_FABRIC_SWATCH_MASTER.md",
                    "prompt_text": "",
                },
                "flow_texture": {
                    "prompt_mode": "attachment",
                    "prompt_file": "prompts/scanned_to_texture_prompt.md",
                    "prompt_text": "",
                },
            }
            (root / "config.json").write_text(json.dumps(config), encoding="utf-8")

            controller = app.PipelineController()
            controller.project_dir = root

            # Save manual mode for texture, attachment for fabric, manual for flow
            payload = {
                "source_mode": "drive",
                "images_per_chat": 5,
                "texture_prompt_mode": "manual",
                "texture_prompt_file": "custom_tex.md",
                "texture_prompt_text": "Custom manual texture prompt content",
                "fabric_prompt_mode": "attachment",
                "fabric_prompt_file": "custom_fab.md",
                "fabric_prompt_text": "",
                "flow_prompt_mode": "manual",
                "flow_prompt_file": "custom_flow.md",
                "flow_prompt_text": "Custom manual flow prompt content",
            }
            controller.save_settings(payload)

            saved = json.loads((root / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["chatgpt_texture_grouped"]["prompt_mode"], "manual")
            self.assertEqual(saved["chatgpt_texture_grouped"]["master_prompt_file"], "custom_tex.md")
            self.assertEqual(saved["chatgpt_texture_grouped"]["prompt_text"], "Custom manual texture prompt content")
            self.assertEqual(saved["chatgpt_fabric_grouped"]["prompt_mode"], "attachment")
            self.assertEqual(saved["chatgpt_fabric_grouped"]["master_prompt_file"], "custom_fab.md")
            self.assertEqual(saved["flow_texture"]["prompt_mode"], "manual")
            self.assertEqual(saved["flow_texture"]["prompt_file"], "custom_flow.md")
            self.assertEqual(saved["flow_texture"]["prompt_text"], "Custom manual flow prompt content")

            state = controller.state()
            self.assertEqual(state["texture_prompt_mode"], "manual")
            self.assertEqual(state["texture_prompt_file"], "custom_tex.md")
            self.assertEqual(state["texture_prompt_text"], "Custom manual texture prompt content")
            self.assertEqual(state["fabric_prompt_mode"], "attachment")
            self.assertEqual(state["fabric_prompt_file"], "custom_fab.md")
            self.assertEqual(state["flow_prompt_mode"], "manual")
            self.assertEqual(state["flow_prompt_file"], "custom_flow.md")
            self.assertEqual(state["flow_prompt_text"], "Custom manual flow prompt content")

    def test_build_steps_includes_prompt_args(self):
        controller = app.PipelineController()
        # 1. ChatGPT pipeline steps
        payload_chatgpt = {
            "engine": "chatgpt",
            "source_mode": "drive",
            "images_per_chat": 6,
            "texture_prompt_mode": "manual",
            "texture_prompt_file": "prompts/tex.md",
            "texture_prompt_text": "My Custom Text",
            "fabric_prompt_mode": "attachment",
            "fabric_prompt_file": "prompts/fab.md",
            "flows": {
                "import": False,
                "crop": False,
                "seamless": True,
                "fabric": True,
                "package": False,
            },
        }
        steps = controller.build_steps(payload_chatgpt)
        self.assertEqual(len(steps), 2)
        seamless_step, seamless_args = steps[0]
        self.assertEqual(seamless_step.key, "seamless")
        self.assertIn("--prompt-mode", seamless_args)
        self.assertIn("manual", seamless_args)
        self.assertIn("--prompt-file", seamless_args)
        self.assertIn("prompts/tex.md", seamless_args)
        self.assertIn("--prompt-text", seamless_args)
        self.assertIn("My Custom Text", seamless_args)

        fabric_step, fabric_args = steps[1]
        self.assertEqual(fabric_step.key, "fabric")
        self.assertIn("--prompt-mode", fabric_args)
        self.assertIn("attachment", fabric_args)
        self.assertIn("--prompt-file", fabric_args)
        self.assertIn("prompts/fab.md", fabric_args)

        overwrite_payload = {
            "engine": "chatgpt",
            "source_mode": "drive",
            "images_per_chat": 4,
            "force": True,
            "flows": {
                "import": True,
                "crop": True,
                "seamless": True,
                "fabric": True,
                "package": True,
            },
        }
        overwrite_steps = controller.build_steps(overwrite_payload, folder="NHC")
        self.assertEqual(
            [step.key for step, _ in overwrite_steps],
            ["import", "crop", "seamless", "fabric", "package"],
        )
        for _, arguments in overwrite_steps:
            self.assertIn("--force", arguments)
        import_arguments = overwrite_steps[0][1]
        self.assertIn("--write-sku-file", import_arguments)
        manifest = import_arguments[import_arguments.index("--write-sku-file") + 1]
        for _, arguments in overwrite_steps[1:]:
            self.assertIn("--sku-file", arguments)
            self.assertEqual(arguments[arguments.index("--sku-file") + 1], manifest)

        # 2. Google Flow runner steps
        payload_flow = {
            "engine": "flow",
            "source_mode": "drive",
            "flow_prompt_mode": "manual",
            "flow_prompt_file": "prompts/flow.md",
            "flow_prompt_text": "My Custom Flow Text",
            "sku": "SKU123",
            "limit": "5",
            "dry_run": True,
            "force": True,
        }
        flow_steps = controller.build_steps(payload_flow, folder="NHDM")
        self.assertEqual(len(flow_steps), 4)
        self.assertEqual([s[0].key for s in flow_steps], ["import", "crop", "flow_texture", "package"])

        import_step, import_args = flow_steps[0]
        self.assertEqual(import_step.key, "import")

        crop_step, crop_args = flow_steps[1]
        self.assertEqual(crop_step.key, "crop")
        self.assertIn("--force", crop_args)

        flow_step, flow_args = flow_steps[2]
        self.assertEqual(flow_step.key, "flow_texture")
        self.assertIn("--sku", flow_args)
        self.assertIn("SKU123", flow_args)
        self.assertIn("--limit", flow_args)
        self.assertIn("5", flow_args)
        self.assertIn("--dry-run", flow_args)
        self.assertIn("--force", flow_args)
        self.assertIn("--prompt-file", flow_args)
        self.assertIn("prompts/flow.md", flow_args)
        self.assertIn("--prompt-text", flow_args)
        self.assertIn("My Custom Flow Text", flow_args)

        package_step, package_args = flow_steps[3]
        self.assertEqual(package_step.key, "package")
        self.assertIn("--folder", package_args)
        self.assertIn("NHDM", package_args)
        self.assertIn("--force", package_args)

    def test_get_default_prompt_text(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            p_dir = root / "prompts" / "prompts_attachments"
            p_dir.mkdir(parents=True)
            (p_dir / "01_TEXTURE_SEAMLESS_MASTER.md").write_text("TEXTURE MASTER PROMPT", encoding="utf-8")
            (p_dir / "02_FABRIC_SWATCH_MASTER.md").write_text("FABRIC MASTER PROMPT", encoding="utf-8")
            (root / "prompts" / "scanned_to_texture_prompt.md").write_text("GOOGLE FLOW PROMPT", encoding="utf-8")

            tex_text = app.get_default_prompt_text(root, "texture")
            self.assertEqual(tex_text, "TEXTURE MASTER PROMPT")

            fab_text = app.get_default_prompt_text(root, "fabric")
            self.assertEqual(fab_text, "FABRIC MASTER PROMPT")

            flow_text = app.get_default_prompt_text(root, "flow_texture")
            self.assertEqual(flow_text, "GOOGLE FLOW PROMPT")

    def test_dismiss_pipeline_folder_keeps_drive_management_and_disk_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = {
                "google_drive": {
                    "urls": [
                        {"url": "https://drive.google.com/drive/folders/ABC", "folder": "ABC"},
                        {"url": "https://drive.google.com/drive/folders/XYZ", "folder": "XYZ"},
                    ]
                }
            }
            (root / "config.json").write_text(json.dumps(config), encoding="utf-8")
            (root / "status_drive_sync.json").write_text(
                json.dumps({
                    "folders": {
                        "ABC": {"folder": "ABC", "drive_total_images": 1},
                        "XYZ": {"folder": "XYZ", "drive_total_images": 2},
                    },
                    "history": [
                        {"folder": "ABC", "status": "success"},
                        {"folder": "XYZ", "status": "success"},
                    ],
                }),
                encoding="utf-8",
            )

            # Create mock disk directories for folder ABC
            raw_abc = root / "textures_raw" / "ABC"
            raw_abc.mkdir(parents=True)
            (raw_abc / "test.jpg").write_bytes(b"test-bytes")

            controller = app.PipelineController()
            controller.project_dir = root

            # Legacy clients may still send delete_files=True. Removing a Drive
            # entry must never delete downloaded files or generated outputs.
            res = controller.delete_drive_folder({"folder": "ABC", "delete_files": True})
            self.assertTrue(res["ok"])

            # Verify config updated
            saved = json.loads((root / "config.json").read_text(encoding="utf-8"))
            saved_urls = saved["google_drive"]["urls"]
            self.assertEqual(len(saved_urls), 2)
            self.assertEqual([item["folder"] for item in saved_urls], ["ABC", "XYZ"])
            self.assertEqual(saved["google_drive"]["hidden_folders"], [])

            # Sync state and management records remain intact.
            sync_data = json.loads((root / "status_drive_sync.json").read_text(encoding="utf-8"))
            self.assertIn("ABC", sync_data["folders"])
            self.assertIn("XYZ", sync_data["folders"])
            self.assertEqual([item["folder"] for item in sync_data["history"]], ["ABC", "XYZ"])

            # Verify local data is preserved.
            self.assertTrue(raw_abc.exists())
            self.assertEqual((raw_abc / "test.jpg").read_bytes(), b"test-bytes")

            # Both folders remain visible in Drive management.
            stats = app.compute_drive_folders_stats(root, saved)
            folders = [f["folder"] for f in stats["folders"]]
            self.assertIn("XYZ", folders)
            self.assertIn("ABC", folders)

    def test_save_drive_link_restores_hidden_management_card(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "config.json").write_text(json.dumps({
                "google_drive": {
                    "urls": [{
                        "url": "https://drive.google.com/drive/folders/ABC",
                        "folder": "ABC",
                    }],
                    "hidden_folders": ["ABC"],
                }
            }), encoding="utf-8")
            controller = app.PipelineController()
            controller.project_dir = root

            controller.save_drive_link({
                "folder": "ABC",
                "url": "https://drive.google.com/drive/folders/ABC",
            })

            saved = json.loads((root / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["google_drive"]["hidden_folders"], [])
            stats = app.compute_drive_folders_stats(root, saved)
            self.assertEqual([item["folder"] for item in stats["folders"]], ["ABC"])

    def test_save_drive_link_rejects_duplicate_drive_id_for_another_folder(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            url = "https://drive.google.com/drive/folders/SAME_ID?usp=sharing"
            (root / "config.json").write_text(json.dumps({
                "google_drive": {
                    "urls": [{"url": url, "folder": "OLD"}],
                    "hidden_folders": ["OLD"],
                }
            }), encoding="utf-8")
            controller = app.PipelineController()
            controller.project_dir = root

            with self.assertRaisesRegex(ValueError, "đã được gán"):
                controller.save_drive_link({"folder": "NEW", "url": url})

            saved = json.loads((root / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(len(saved["google_drive"]["urls"]), 1)
            self.assertEqual(saved["google_drive"]["urls"][0]["folder"], "OLD")
            stats = app.compute_drive_folders_stats(root, saved)
            self.assertEqual(stats["folders"][0]["folder"], "OLD")
            self.assertEqual(stats["folders"][0]["url"], url)

    def test_unlinked_folder_does_not_restore_url_from_sync_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stale_url = "https://drive.google.com/drive/folders/NHC_OLD"
            (root / "config.json").write_text(json.dumps({
                "google_drive": {"urls": [], "share_url": ""}
            }), encoding="utf-8")
            (root / "status_drive_sync.json").write_text(json.dumps({
                "folders": {
                    "NHC": {"folder": "NHC", "drive_url": stale_url, "drive_total_images": 8}
                }
            }), encoding="utf-8")
            (root / "textures_raw" / "NHC").mkdir(parents=True)

            config = json.loads((root / "config.json").read_text(encoding="utf-8"))
            stats = app.compute_drive_folders_stats(root, config)

            nhc = next(item for item in stats["folders"] if item["folder"] == "NHC")
            self.assertEqual(nhc["url"], "")
            self.assertFalse(nhc["has_drive_url"])

    def test_extract_chatgpt_quota_info_formats(self):
        # 1. English card with absolute time
        text1 = "You're out of images\nYou're out of image creations for now. Upgrade your plan to continue, or wait for more at 4:17 PM."
        is_q1, reset1, msg1 = app.extract_chatgpt_quota_info(text1)
        self.assertTrue(is_q1)
        self.assertIn("4:17 PM", reset1)
        self.assertIn("4:17 PM", msg1)

        # 2. Vietnamese assistant response with relative duration
        text2 = "Không thể tạo ảnh vì hạn mức tạo ảnh đã hết. Hạn mức sẽ được đặt lại sau khoảng 6 giờ 44 phút."
        is_q2, reset2, msg2 = app.extract_chatgpt_quota_info(text2)
        self.assertTrue(is_q2)
        self.assertIn("6 giờ 44 phút", reset2)
        self.assertIn("6 giờ 44 phút", msg2)

        # 3. Combined text
        text3 = text1 + "\n\n" + text2
        is_q3, reset3, msg3 = app.extract_chatgpt_quota_info(text3)
        self.assertTrue(is_q3)
        self.assertIn("6 giờ 44 phút", reset3)
        self.assertIn("4:17 PM", reset3)

        # 4. English relative duration
        text4 = "You've reached your image generation limit. Please try again in 45 minutes."
        is_q4, reset4, msg4 = app.extract_chatgpt_quota_info(text4)
        self.assertTrue(is_q4)
        self.assertIn("45 minutes", reset4)

        # 5. Non-quota normal text
        text5 = "Here is your generated seamless texture of linen fabric."
        is_q5, reset5, msg5 = app.extract_chatgpt_quota_info(text5)
        self.assertFalse(is_q5)
        self.assertIsNone(reset5)

    def test_send_telegram_message_validation_and_mock(self):
        # Missing token or chat id
        ok, err = app.send_telegram_message("", "12345", "test")
        self.assertFalse(ok)
        self.assertIn("Thiếu", err)

        ok2, err2 = app.send_telegram_message("token123", "", "test")
        self.assertFalse(ok2)
        self.assertIn("Thiếu", err2)

        # Mock urllib.request.urlopen success
        from unittest.mock import patch, MagicMock
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"ok": True, "result": {"message_id": 100}}).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None

        with patch("urllib.request.urlopen", return_value=mock_resp):
            ok3, err3 = app.send_telegram_message("test_token", "test_chat", "Hello <b>World</b>")
            self.assertTrue(ok3)
            self.assertIsNone(err3)

    def test_telegram_settings_saving_and_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = {"app_ui": {}}
            (root / "config.json").write_text(json.dumps(config), encoding="utf-8")

            controller = app.PipelineController()
            controller.project_dir = root

            payload = {
                "source_mode": "drive",
                "drive_urls": [{"url": "https://drive.google.com/drive/folders/ABC", "folder": "ABC"}],
                "telegram": {
                    "enabled": True,
                    "bot_token": "123:ABC",
                    "chat_id": "99999",
                    "notify_on_complete": False,
                    "notify_on_start": True,
                    "notify_periodic_progress": True,
                    "notify_on_failure": True,
                }
            }
            controller.save_settings(payload)

            saved = json.loads((root / "config.json").read_text(encoding="utf-8"))
            tele = saved.get("telegram", {})
            self.assertTrue(tele.get("enabled"))
            self.assertEqual(tele.get("bot_token"), "123:ABC")
            self.assertEqual(tele.get("chat_id"), "99999")
            self.assertFalse(tele.get("notify_on_complete"))
            self.assertTrue(tele.get("notify_on_start"))
            self.assertTrue(tele.get("notify_periodic_progress"))
            self.assertTrue(tele.get("notify_on_failure"))

            st = controller.state()
            self.assertTrue(st["telegram"]["enabled"])
            self.assertEqual(st["telegram"]["bot_token"], "123:ABC")

    def test_telegram_status_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = {
                "google_drive": {
                    "urls": [{"url": "http://x", "folder": "TEST_FOLDER"}]
                }
            }
            (root / "config.json").write_text(json.dumps(config), encoding="utf-8")
            
            controller = app.PipelineController()
            controller.project_dir = root
            controller.active_running_folder = "TEST_FOLDER"
            controller.session_running = True
            
            from unittest.mock import patch, MagicMock
            with patch.object(app, "send_telegram_message") as mock_send:
                with patch.object(controller, "is_running", return_value=True):
                    controller._send_telegram_status_report("fake_token", "fake_chat")
                
                mock_send.assert_called_once()
                args, kwargs = mock_send.call_args
                self.assertEqual(args[0], "fake_token")
                self.assertEqual(args[1], "fake_chat")
                
                text = args[2]
                self.assertIn("BÁO CÁO TIẾN ĐỘ HIỆN TẠI", text)
                self.assertIn("Đang chạy ⚡", text)
                self.assertIn("Đang xử lý:</b> <code>TEST_FOLDER</code>", text)
                self.assertIn("Đã hoàn thành:", text)
                self.assertIn("Đang chờ:", text)

    def test_telegram_progress_counts_only_folders_in_current_queue(self):
        controller = app.PipelineController.__new__(app.PipelineController)
        controller.project_dir = Path(".").resolve()
        controller.lock = threading.RLock()
        controller.session_running = False
        controller.active_running_folder = None
        controller.active_running_step = None
        controller.active_running_sku = None
        controller.telegram_queue_folders = ["DEHQ"]
        controller.telegram_completed_folders = {"DEHQ"}
        stats = {
            "folders": [
                {"folder_display": "DEHQ", "status": "completed", "total": 3, "created_count": 3, "is_active": False},
                {"folder_display": "OTHER", "status": "not_started", "total": 10, "created_count": 0, "is_active": False},
            ],
            "total_folders": 2,
            "total_skus": 13,
            "total_created": 3,
            "overall_percent": 23,
        }

        with patch.object(app, "compute_drive_folders_stats", return_value=stats):
            summary = controller.telegram_progress_summary({}, True)

        self.assertIn("Folder:</b> 1/1 (100%)", summary)
        self.assertIn("SKU:</b> 3/3 (100%)", summary)
        self.assertNotIn("13", summary)

    def test_state_exposes_only_unfinished_pipeline_folders(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "config.json").write_text(json.dumps({}), encoding="utf-8")
            controller = app.PipelineController()
            controller.project_dir = root
            controller.worker = MagicMock()
            controller.worker.is_alive.return_value = True
            controller.telegram_queue_folders = ["DONE", "ACTIVE", "WAITING"]
            controller.telegram_completed_folders = {"DONE"}

            state = controller.state()

            self.assertEqual(state["pipeline_folders"], ["ACTIVE", "WAITING"])
            self.assertEqual(state["active_pipeline_folders"], ["ACTIVE", "WAITING"])

    def test_state_preserves_pipeline_folders_after_worker_stops(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "config.json").write_text(json.dumps({}), encoding="utf-8")
            controller = app.PipelineController()
            controller.project_dir = root
            controller.telegram_queue_folders = ["CHKK"]

            state = controller.state()

            self.assertFalse(state["running"])
            self.assertEqual(state["pipeline_folders"], ["CHKK"])
            self.assertEqual(state["active_pipeline_folders"], [])

    def test_pipeline_folder_postcondition_rejects_missing_output(self):
        controller = app.PipelineController.__new__(app.PipelineController)
        controller.project_dir = Path(".").resolve()
        stats = {
            "folders": [{
                "folder_display": "test1",
                "drive_total": 2,
                "total": 2,
                "created_count": 1,
                "seamless_count": 1,
                "cropped_count": 2,
                "raw_count": 2,
            }]
        }
        payload = {
            "limit": "",
            "sku": "",
            "flows": {"import": True, "crop": True, "seamless": True, "fabric": True},
        }

        with patch.object(app, "load_json", return_value={}), patch.object(
            app, "compute_drive_folders_stats", return_value=stats
        ):
            issue = controller.pipeline_folder_completion_issue("test1", payload)

        self.assertIn("còn thiếu 1/2 swatch hoàn chỉnh", issue)
        stats["folders"][0]["created_count"] = 2
        with patch.object(app, "load_json", return_value={}), patch.object(
            app, "compute_drive_folders_stats", return_value=stats
        ):
            self.assertEqual(controller.pipeline_folder_completion_issue("test1", payload), "")

    def test_telegram_pipeline_start_message_contains_queue_current_folder_and_time(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "config.json").write_text(json.dumps({
                "telegram": {
                    "enabled": True,
                    "bot_token": "123:ABC",
                    "chat_id": "99999",
                    "notify_on_start": True,
                }
            }), encoding="utf-8")
            controller = app.PipelineController.__new__(app.PipelineController)
            controller.project_dir = root

            with patch.object(app.threading, "Thread") as thread:
                controller.notify_telegram(
                    "pipeline_start",
                    folders=["test", "test1"],
                    current_folder="test",
                    started_at="16:20:45",
                )

            message = thread.call_args.kwargs["args"][2]
            self.assertIn("Các folder trong hàng đợi:</b> test, test1", message)
            self.assertIn("Folder hiện tại đang được xử lý:</b> <code>test</code>", message)
            self.assertIn("Thời điểm bắt đầu:</b> 16:20:45", message)
            self.assertNotIn("10/09/2026", message)
            self.assertNotIn("Tổng folder", message)

    def test_telegram_poller_starts_silently_and_replaces_previous_instance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "config.json").write_text(json.dumps({
                "telegram": {
                    "enabled": True,
                    "bot_token": "123:ABC",
                    "chat_id": "99999",
                }
            }), encoding="utf-8")
            controller = app.PipelineController.__new__(app.PipelineController)
            controller.project_dir = root
            controller.lock = threading.RLock()
            controller.telegram_polling_active = True
            controller.telegram_poller_thread = None
            previous_stop = threading.Event()
            controller.telegram_poller_stop = previous_stop
            controller.telegram_offset = 0

            with patch.object(threading, "Thread") as thread:
                controller.start_telegram_poller()

            self.assertTrue(previous_stop.is_set())
            thread.return_value.start.assert_called_once()
            poller_args = thread.call_args.kwargs["args"]
            self.assertEqual(poller_args[:2], ("123:ABC", "99999"))
            self.assertIs(poller_args[2], controller.telegram_poller_stop)

            controller.telegram_poller_stop.set()
            with patch("urllib.request.urlopen") as urlopen:
                controller._telegram_poller_loop("123:ABC", "99999", controller.telegram_poller_stop)
            urlopen.assert_not_called()

    def test_extract_base_folder_code(self):
        self.assertEqual(app.extract_base_folder_code("1013 (Làm trước)"), "1013")
        self.assertEqual(app.extract_base_folder_code("1013 (làm trước)"), "1013")
        self.assertEqual(app.extract_base_folder_code("BNO - Đợt 1"), "BNO")
        self.assertEqual(app.extract_base_folder_code("WDTC"), "WDTC")
        self.assertEqual(app.extract_base_folder_code("SKU_999 [Priority]"), "SKU")
        self.assertEqual(app.extract_base_folder_code(""), "")

    def test_audit_single_drive_url_matching_and_progress(self):
        from unittest.mock import patch
        from pathlib import PurePosixPath

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            out_chatgpt = root / "output" / "chatgpt" / "1013"
            (out_chatgpt / "101301").mkdir(parents=True)
            ((out_chatgpt / "101301") / "seamless_texture.png").write_bytes(b"data")

            (out_chatgpt / "101302").mkdir(parents=True)
            ((out_chatgpt / "101302") / "image_1.png").write_bytes(b"data")

            (out_chatgpt / "101303").mkdir(parents=True)  # partial / empty

            # 101304 does not exist at all

            mock_entries = [
                {"url": "http://x/1", "path": PurePosixPath("1013 (Làm trước)/101301.jpg")},
                {"url": "http://x/2", "path": PurePosixPath("1013 (Làm trước)/101302.jpg")},
                {"url": "http://x/3", "path": PurePosixPath("1013 (Làm trước)/101303.jpg")},
                {"url": "http://x/4", "path": PurePosixPath("1013 (Làm trước)/101304.jpg")},
            ]

            with patch("veo3_auto_app.fetch_drive_folder_title", return_value="1013 (Làm trước)"):
                with patch("import_google_drive.list_public_folder", return_value=mock_entries):
                    res = app.audit_single_drive_url(
                        root,
                        "https://drive.google.com/drive/folders/1234567890abcdef",
                        target_child="chatgpt"
                    )

            self.assertEqual(res["title"], "1013 (Làm trước)")
            self.assertEqual(res["base_code"], "1013")
            self.assertEqual(res["drive_total"], 4)
            self.assertEqual(len(res["checks"]), 1)

            chk = res["checks"][0]
            self.assertTrue(chk["folder_exists"])
            self.assertEqual(chk["matched_folder_name"], "1013")
            self.assertEqual(chk["total"], 4)
            self.assertEqual(chk["created_count"], 2)
            self.assertEqual(chk["missing_count"], 2)
            self.assertEqual(chk["percent"], 50.0)

            sku_map = {s["sku"]: s for s in chk["skus"]}
            self.assertEqual(sku_map["101301"]["status"], "done")
            self.assertTrue(sku_map["101301"]["has_seamless"])
            self.assertEqual(sku_map["101302"]["status"], "done")
            self.assertTrue(sku_map["101302"]["has_fabric"])
            self.assertEqual(sku_map["101303"]["status"], "partial")
            self.assertEqual(sku_map["101304"]["status"], "missing")

    def test_pipeline_controller_audit_drive_folders(self):
        from unittest.mock import patch

        controller = app.PipelineController()
        controller.project_dir = Path(".")
        dummy_audit = {
            "url": "https://drive.google.com/drive/folders/test",
            "title": "1013 (Làm trước)",
            "base_code": "1013",
            "drive_total": 5,
            "checks": []
        }
        with patch("veo3_auto_app.audit_single_drive_url", return_value=dummy_audit):
            res = controller.audit_drive_folders({
                "urls": ["https://drive.google.com/drive/folders/test"],
                "target_child": "all"
            })
            self.assertIn("results", res)
            self.assertEqual(len(res["results"]), 1)
    def test_compute_drive_folders_stats_with_unlinked_folders(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cfg = {
                "google_drive": {
                    "urls": [
                        {"url": "https://drive.google.com/drive/folders/linked_folder", "folder": "LINKED"}
                    ]
                }
            }
            # Create local unlinked fabric folder with created SKUs
            out_chatgpt = root / "output" / "chatgpt" / "UNLINKED_FABRIC"
            (out_chatgpt / "SKU001").mkdir(parents=True)
            ((out_chatgpt / "SKU001") / "seamless_texture.png").write_bytes(b"data")
            ((out_chatgpt / "SKU001") / "image_1.png").write_bytes(b"data")

            # Create another local raw folder
            (root / "textures_raw" / "RAW_ONLY").mkdir(parents=True)
            ((root / "textures_raw" / "RAW_ONLY") / "RAW01.jpg").write_bytes(b"data")

            res = app.compute_drive_folders_stats(root, cfg, include_unlinked=True)

            self.assertEqual(res["total_folders"], 3)
            self.assertEqual(res["linked_folders"], 1)
            self.assertEqual(res["unlinked_folders"], 2)

            f_map = {f["folder"]: f for f in res["folders"]}
            self.assertIn("LINKED", f_map)
            self.assertTrue(f_map["LINKED"]["has_drive_url"])

            self.assertIn("UNLINKED_FABRIC", f_map)
            self.assertFalse(f_map["UNLINKED_FABRIC"]["has_drive_url"])
            self.assertEqual(f_map["UNLINKED_FABRIC"]["created_count"], 1)
            self.assertEqual(f_map["UNLINKED_FABRIC"]["seamless_count"], 1)
            self.assertEqual(f_map["UNLINKED_FABRIC"]["fabric_count"], 1)

            self.assertIn("RAW_ONLY", f_map)
            self.assertFalse(f_map["RAW_ONLY"]["has_drive_url"])
            self.assertEqual(f_map["RAW_ONLY"]["raw_count"], 1)

    def test_pipeline_controller_save_drive_link(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_file = root / "config.json"
            config_file.write_text(json.dumps({"google_drive": {"urls": []}}), encoding="utf-8")

            controller = app.PipelineController()
            controller.project_dir = root

            # 1. Add link for folder
            res = controller.save_drive_link({
                "folder": "FABRIC_1013",
                "url": "https://drive.google.com/drive/folders/abcdef123456",
                "sku": "SP1M29",
                "limit": "5",
                "images_per_chat": 4,
            })
            self.assertTrue(res["ok"])
            self.assertEqual(res["folder"], "FABRIC_1013")

            saved_cfg = json.loads(config_file.read_text(encoding="utf-8"))
            self.assertEqual(len(saved_cfg["google_drive"]["urls"]), 1)
            self.assertEqual(saved_cfg["google_drive"]["urls"][0]["folder"], "FABRIC_1013")
            self.assertEqual(saved_cfg["google_drive"]["urls"][0]["url"], "https://drive.google.com/drive/folders/abcdef123456")
            self.assertEqual(saved_cfg["google_drive"]["urls"][0]["images_per_chat"], 4)
            self.assertEqual(saved_cfg["google_drive"]["urls"][0]["sku"], "SP1M29")
            self.assertEqual(saved_cfg["google_drive"]["urls"][0]["limit"], "5")
            self.assertEqual(saved_cfg["google_drive"]["share_url"], "https://drive.google.com/drive/folders/abcdef123456")

            # 2. Update existing link
            res_update = controller.save_drive_link({
                "folder": "FABRIC_RENAMED",
                "url": "https://drive.google.com/drive/folders/new_link_999",
                "original_folder": "FABRIC_1013",
                "original_drive_id": "abcdef123456",
                "images_per_chat": 7,
            })
            self.assertTrue(res_update["ok"])
            saved_cfg = json.loads(config_file.read_text(encoding="utf-8"))
            self.assertEqual(len(saved_cfg["google_drive"]["urls"]), 1)
            self.assertEqual(saved_cfg["google_drive"]["urls"][0]["folder"], "FABRIC_RENAMED")
            self.assertEqual(saved_cfg["google_drive"]["urls"][0]["url"], "https://drive.google.com/drive/folders/new_link_999")
            self.assertEqual(saved_cfg["google_drive"]["urls"][0]["images_per_chat"], 7)

            # 3. Unlink folder (empty url)
            res_unlink = controller.save_drive_link({
                "folder": "FABRIC_RENAMED",
                "url": ""
            })
            self.assertTrue(res_unlink["ok"])
            saved_cfg = json.loads(config_file.read_text(encoding="utf-8"))
            self.assertEqual(len(saved_cfg["google_drive"]["urls"]), 0)
            self.assertEqual(saved_cfg["google_drive"]["share_url"], "")

    def test_pipeline_controller_auto_match_drive_urls(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_file = root / "config.json"
            config_file.write_text(json.dumps({"google_drive": {"urls": []}}), encoding="utf-8")

            # Create existing folders on disk
            (root / "output" / "chatgpt" / "1013").mkdir(parents=True)
            (root / "output" / "chatgpt" / "BNO").mkdir(parents=True)

            controller = app.PipelineController()
            controller.project_dir = root

            def mock_title_fetch(url, timeout=6):
                if "1111" in url:
                    return "1013 (Làm trước)"
                if "2222" in url:
                    return "BNO - Đợt 1"
                return "UNKNOWN_FOLDER"

            with patch("veo3_auto_app.fetch_drive_folder_title", side_effect=mock_title_fetch):
                res = controller.auto_match_drive_urls({
                    "urls": [
                        "https://drive.google.com/drive/folders/1111",
                        "https://drive.google.com/drive/folders/2222",
                        "https://drive.google.com/drive/folders/3333",
                    ]
                })

            self.assertTrue(res["ok"])
            self.assertEqual(res["total_matched"], 2)
            self.assertEqual(res["total_unmatched"], 1)

            saved_cfg = json.loads(config_file.read_text(encoding="utf-8"))
            matched_folders = [item["folder"] for item in saved_cfg["google_drive"]["urls"]]
            self.assertIn("1013", matched_folders)
            self.assertIn("BNO", matched_folders)

    def test_pipeline_controller_audit_single_folder(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_file = root / "config.json"
            config_file.write_text(
                json.dumps({
                    "google_drive": {
                        "urls": [
                            {"folder": "1013", "url": "https://drive.google.com/drive/folders/1013_mock"}
                        ]
                    }
                }),
                encoding="utf-8"
            )

            controller = app.PipelineController()
            controller.project_dir = root

            dummy_audit = {
                "url": "https://drive.google.com/drive/folders/1013_mock",
                "title": "1013",
                "base_code": "1013",
                "drive_total": 10,
                "checks": [
                    {
                        "engine": "chatgpt",
                        "created_count": 8,
                        "missing_count": 2,
                        "percent": 80.0,
                    }
                ]
            }

            with patch("veo3_auto_app.audit_single_drive_url", return_value=dummy_audit):
                res = controller.audit_single_folder({"folder": "1013"})

            self.assertEqual(res["drive_total"], 10)
            self.assertEqual(res["checks"][0]["percent"], 80.0)

            # Verify status_drive_sync.json was written
            sync_file = root / "status_drive_sync.json"
            self.assertTrue(sync_file.is_file())
            sync_data = json.loads(sync_file.read_text(encoding="utf-8"))
            self.assertIn("1013", sync_data["folders"])
            self.assertEqual(sync_data["folders"]["1013"]["drive_total_images"], 10)
            self.assertEqual(sync_data["folders"]["1013"]["created_count"], 8)


if __name__ == "__main__":
    unittest.main()
