"""Create a safe first-run configuration for the standalone build."""

import argparse
import copy
import json
from pathlib import Path


def create_default_config(source):
    config = copy.deepcopy(source)

    app_ui = config.setdefault("app_ui", {})
    app_ui["source_mode"] = "drive"
    app_ui.pop("local_source_dir", None)

    config.setdefault("telegram", {}).update(
        {"enabled": False, "bot_token": "", "chat_id": ""}
    )
    config.setdefault("google_drive", {}).update(
        {
            "enabled": False,
            "share_url": "",
            "destination_dir": "textures_raw",
            "urls": [],
        }
    )
    config.setdefault("paths", {}).update(
        {
            "textures_dir": "textures",
            "output_dir": "output\\chatgpt",
            "base_prompt": "prompts/base_prompt.md",
            "fold_contexts": "prompts/fold_context.json",
            "status_file": "status.json",
        }
    )
    config.setdefault("crop", {}).update(
        {"source_dir": "textures_raw", "output_dir": "textures_cropped"}
    )
    config.setdefault("seamless_package", {})["output_dir"] = "output\\chatgpt"
    config.setdefault("chatgpt", {})["output_dir"] = "output\\chatgpt"
    config.setdefault("chatgpt_texture", {})["raw_dir"] = "textures_cropped"
    config.setdefault("chatgpt_texture_grouped", {})["raw_dir"] = "textures_cropped"
    config.setdefault("chatgpt_fabric_grouped", {}).update(
        {"raw_dir": "textures_cropped", "output_dir": "output\\chatgpt"}
    )
    config.setdefault("flow_texture", {}).update(
        {"raw_dir": "textures_cropped", "status_file": "status_flow_texture.json"}
    )
    config.setdefault("chatgpt_project", {})["project_url"] = ""
    return config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()

    source = json.loads(args.source.read_text(encoding="utf-8"))
    if not isinstance(source, dict):
        raise ValueError("Source config must contain a JSON object.")
    result = create_default_config(source)
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    args.destination.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
