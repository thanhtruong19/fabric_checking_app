import datetime
import html
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


APP_TITLE = "VEO3 Auto Pipeline"
PROJECT_DIR_ENV = "VEO3_PROJECT_DIR"
EMBEDDED_WORKER_ENV = "VEO3_EMBEDDED_WORKER"
EMBEDDED_WORKER_FLAG = "--embedded-worker"
EMBEDDED_GDOWN_FLAG = "--embedded-gdown"
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
MAX_LOG_CHARS = 200_000
SUPPORTED_IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
}
QUALITY_QC_FILENAMES = {
    "qc_offset50.png",
    "qc_tile_3x3.png",
    "qc_tile_15x15.png",
}

@dataclass(frozen=True)
class FlowStep:
    key: str
    label: str
    script: str


FLOW_STEPS = (
    FlowStep("import", "Import Google Drive", "import_google_drive.py"),
    FlowStep("crop", "Crop cố định", "crop_textures.py"),
    FlowStep(
        "seamless",
        "Tạo seamless (01_TEXTURE)",
        "run_chatgpt_texture_grouped_batch.py",
    ),
    FlowStep(
        "fabric",
        "Tạo swatch vải (02_FABRIC)",
        "run_chatgpt_fabric_grouped_batch.py",
    ),
    FlowStep("package", "Đóng gói theo SKU", "package_seamless_textures.py"),
)


INDEX_HTML = r"""<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>VEO3 Auto Pipeline</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #08111f;
      --card: #101c2e;
      --card-hover: #15243b;
      --line: #26364d;
      --text: #e6edf7;
      --muted: #93a4ba;
      --cyan: #38bdf8;
      --blue: #2563eb;
      --green: #34d399;
      --amber: #fbbf24;
      --red: #fb7185;
      --purple: #a855f7;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: radial-gradient(circle at 15% 0, #12233b 0, #08111f 38%);
      color: var(--text);
      font: 14px/1.45 "Segoe UI", Arial, sans-serif;
    }
    .shell { width: min(1280px, calc(100% - 32px)); margin: 20px auto 40px; }
    header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; }
    h1 { margin: 0; font-size: 24px; letter-spacing: .2px; }
    .subtitle { color: var(--muted); margin-top: 3px; }
    .badge {
      border: 1px solid var(--line); border-radius: 999px; padding: 7px 14px;
      background: #0b1728; color: var(--muted); font-weight: 500; font-size: 13px;
    }
    .badge.running { color: var(--cyan); border-color: #155e75; background: rgba(56,189,248,.1); }
    .badge.ok { color: var(--green); border-color: #166534; background: rgba(52,211,153,.1); }
    .badge.pending { color: var(--amber); border-color: #854d0e; background: rgba(251,191,36,.1); }
    .grid { display: grid; grid-template-columns: 1.15fr .85fr; gap: 14px; }
    .card {
      background: rgba(16,28,46,.96); border: 1px solid var(--line); border-radius: 14px;
      padding: 16px; box-shadow: 0 16px 40px rgba(0,0,0,.2);
    }
    .card h2 { margin: 0 0 12px; font-size: 15px; display: flex; align-items: center; gap: 8px; }
    .card-header-flex { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
    .card-header-flex h2 { margin: 0; }
    .wide { grid-column: 1 / -1; }
    label { display: block; color: var(--muted); margin-bottom: 5px; font-size: 13px; }
    input[type=text], input[type=number], select {
      width: 100%; border: 1px solid var(--line); background: #081321;
      color: var(--text); border-radius: 9px; padding: 10px 11px; outline: none; font-size: 13px;
    }
    input[readonly] { color: #cbd5e1; }
    input:focus, select:focus { border-color: var(--cyan); box-shadow: 0 0 0 3px rgba(56,189,248,.15); }
    .row { display: grid; grid-template-columns: 1fr auto; gap: 8px; }
    .fields { display: grid; grid-template-columns: 2fr 1fr 1fr; gap: 10px; margin-top: 12px; }
    button {
      border: 1px solid var(--line); background: #17263b; color: var(--text); border-radius: 9px;
      padding: 9px 13px; cursor: pointer; font-weight: 600; font-size: 13px; transition: all .15s ease;
      display: inline-flex; align-items: center; gap: 6px; justify-content: center;
    }
    button:hover:not(:disabled) { filter: brightness(1.15); transform: translateY(-1px); }
    button:disabled { opacity: .45; cursor: not-allowed; transform: none; }
    .btn-sm { padding: 5px 9px; font-size: 11px; border-radius: 7px; }
    .primary { background: var(--blue); border-color: #3b82f6; }
    .primary:hover:not(:disabled) { background: #1d4ed8; }
    .danger { background: #451826; border-color: #9f1239; color: #fecdd3; }
    .danger:hover:not(:disabled) { background: #881337; }
    .ghost { background: #0b1728; }
    .ghost:hover:not(:disabled) { background: #13243c; }
    .checks { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 12px; }
    .check {
      display: flex; align-items: center; gap: 8px; padding: 9px; border: 1px solid var(--line);
      border-radius: 9px; color: var(--text); background: #0b1728; cursor: pointer; font-size: 13px;
    }
    .check input { accent-color: var(--blue); cursor: pointer; }
    .actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 13px; }
    .hint { color: var(--muted); font-size: 12px; margin-top: 7px; }
    .source-tabs { display: flex; gap: 8px; margin-bottom: 12px; }
    .source-tab {
      display: flex; align-items: center; gap: 7px; border: 1px solid var(--line);
      border-radius: 9px; padding: 8px 12px; background: #0b1728; color: var(--text); cursor: pointer; font-size: 13px;
    }
    .source-tab input { accent-color: var(--blue); }
    .hidden { display: none !important; }
    .env { display: grid; grid-template-columns: 110px 1fr; gap: 7px; color: var(--muted); }
    .env code { color: #cbd5e1; overflow-wrap: anywhere; }

    /* Drive Folders Manager Styling */
    .drive-manager-box {
      border: 1px solid var(--line); border-radius: 11px; background: #081321;
      padding: 10px; margin-top: 6px; display: grid;
      grid-template-rows: repeat(4, 148px); gap: 8px;
    }
    .drive-folder-placeholder { visibility: hidden; pointer-events: none; }
    .drive-list-toolbar { display: flex; gap: 8px; align-items: center; margin-top: 8px; }
    .drive-list-toolbar input { flex: 1; }
    .drive-list-count { color: var(--muted); font-size: 11px; white-space: nowrap; }
    .drive-pagination {
      display: flex; align-items: center; justify-content: center; gap: 8px; padding-top: 4px;
    }
    .drive-page-info { color: var(--muted); font-size: 12px; min-width: 90px; text-align: center; }
    .drive-folder-item {
      border: 1px solid var(--line); border-radius: 9px; background: #0f1c2e;
      padding: 10px 12px; display: flex; flex-direction: column; gap: 6px;
      transition: all .2s ease;
    }
    .drive-folder-item:hover { border-color: rgba(56,189,248,.35); background: #13243c; }
    .drive-folder-item.active { border-color: var(--cyan); box-shadow: 0 0 12px rgba(56,189,248,.2); background: #132845; }
    .df-header { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 6px; }
    .df-title { display: flex; align-items: center; gap: 8px; font-weight: 600; font-size: 13px; }
    .df-name { color: var(--cyan); font-family: Consolas, monospace; font-size: 13px; }
    .df-badge {
      font-size: 11px; font-weight: 600; padding: 3px 8px; border-radius: 99px;
      background: rgba(16,28,46,.8); border: 1px solid var(--line);
    }
    .df-badge.ok { color: var(--green); border-color: rgba(52,211,153,.3); background: rgba(52,211,153,.1); }
    .df-badge.pending { color: var(--amber); border-color: rgba(251,191,36,.3); background: rgba(251,191,36,.1); }
    .df-badge.running { color: var(--cyan); border-color: rgba(56,189,248,.4); background: rgba(56,189,248,.15); animation: pulse 1.2s infinite; }
    @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: .6; } }
    
    .df-url {
      font-size: 11px; color: var(--muted); overflow: hidden; text-overflow: ellipsis;
      white-space: nowrap; max-width: 100%; display: block; text-decoration: none;
    }
    .df-url:hover { color: var(--cyan); text-decoration: underline; }
    
    .df-meta { font-size: 11px; color: var(--muted); display: flex; gap: 10px; flex-wrap: wrap; }
    .df-meta span { display: inline-flex; align-items: center; gap: 3px; }
    
    .df-progress-bar { height: 4px; background: #081321; border-radius: 99px; overflow: hidden; }
    .df-progress-bar span { display: block; height: 100%; background: linear-gradient(90deg, var(--blue), var(--green)); }
    
    .df-actions { display: flex; gap: 6px; justify-content: flex-end; margin-top: 4px; flex-wrap: wrap; }

    .drive-add-row {
      display: grid; grid-template-columns: 1fr 150px auto; gap: 8px; align-items: center; margin-top: 4px;
    }

    /* Timing and Stats Grid */
    .progress-summary {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 9px;
      margin-bottom: 10px;
    }
    .timing-summary {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 9px;
      margin-top: 9px;
    }
    .metric {
      border: 1px solid var(--line); background: #0b1728; border-radius: 10px; padding: 10px 12px;
      position: relative; overflow: hidden;
    }
    .metric.highlight { border-color: rgba(56,189,248,.3); background: rgba(11,23,40,.8); }
    .metric strong { display: block; font-size: 18px; font-weight: 700; color: var(--text); }
    .metric span { color: var(--muted); font-size: 12px; margin-top: 2px; display: block; }
    .metric .sub { font-size: 11px; color: var(--cyan); margin-top: 3px; font-weight: 500; }
    .metric.running-timer strong { color: var(--cyan); }

    .fabric-progress { height: 8px; background: #081321; border-radius: 99px; overflow: hidden; margin: 10px 0; }
    .fabric-progress span {
      display: block; height: 100%; width: 0;
      background: linear-gradient(90deg, var(--blue), var(--green));
      transition: width .3s ease;
    }

    /* Folder Filter Dropdown in Progress Panel */
    .folder-filter-wrap { display: flex; align-items: center; gap: 8px; }
    .folder-filter-select {
      background: #081321; color: var(--text); border: 1px solid var(--line);
      border-radius: 8px; padding: 6px 10px; font-size: 12px; outline: none; min-width: 180px;
    }

    /* SKU Lists with Image Thumbnails */
    .sku-columns { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 12px; }
    .sku-box {
      border: 1px solid var(--line); border-radius: 12px; background: #0b1728; overflow: hidden;
      display: flex; flex-direction: column;
    }
    .sku-title {
      display: flex; justify-content: space-between; align-items: center;
      padding: 10px 12px; border-bottom: 1px solid var(--line); background: rgba(16,28,46,.6);
      gap: 8px;
    }
    .sku-title-left { display: flex; align-items: center; gap: 8px; font-weight: 600; font-size: 13px; }
    .sku-search {
      width: 140px; padding: 4px 8px; font-size: 11px; border-radius: 6px;
      border: 1px solid var(--line); background: #081321; color: var(--text);
    }
    .sku-grid {
      min-height: 120px; max-height: 380px; overflow-y: auto; padding: 10px;
      display: grid; grid-template-columns: repeat(auto-fill, minmax(95px, 1fr));
      gap: 9px; align-content: flex-start;
    }
    .sku-card {
      background: #101c2e; border: 1px solid var(--line); border-radius: 10px; padding: 6px;
      cursor: pointer; text-align: center; transition: all .2s cubic-bezier(0.4, 0, 0.2, 1);
      position: relative; display: flex; flex-direction: column; align-items: center;
    }
    .sku-card:hover {
      border-color: var(--cyan); transform: translateY(-2px);
      background: #15253d; box-shadow: 0 6px 16px rgba(0,0,0,.35);
    }
    .sku-card.created { border-color: rgba(52,211,153,.3); }
    .sku-card.created:hover { border-color: var(--green); box-shadow: 0 6px 18px rgba(52,211,153,.2); }
    .sku-card.pending { border-color: rgba(251,191,36,.25); }
    .sku-card.pending:hover { border-color: var(--amber); box-shadow: 0 6px 18px rgba(251,191,36,.2); }

    .sku-thumb-wrap {
      width: 100%; aspect-ratio: 1 / 1; border-radius: 7px; overflow: hidden;
      background: #081321; position: relative; display: flex; align-items: center; justify-content: center;
    }
    .sku-thumb {
      width: 100%; height: 100%; object-fit: cover; display: block;
      transition: transform .25s ease;
    }
    .sku-card:hover .sku-thumb { transform: scale(1.06); }
    .sku-card-name {
      margin-top: 5px; font: 600 11px/1.2 Consolas, monospace; color: var(--text);
      width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    .sku-dot {
      position: absolute; top: 4px; right: 4px; width: 7px; height: 7px;
      border-radius: 99px; box-shadow: 0 0 4px rgba(0,0,0,.6);
    }
    .sku-dot.ok { background: var(--green); }
    .sku-dot.pending { background: var(--amber); }
    .sku-statuses { width: 100%; display: grid; gap: 3px; margin-top: 5px; }
    .sku-status-pill {
      display: flex; align-items: center; gap: 4px; border-radius: 999px; padding: 3px 6px;
      font-size: 9px; font-weight: 700; line-height: 1; border: 1px solid transparent;
    }
    .sku-status-pill.ok { color: #a7f3d0; background: rgba(16,185,129,.2); border-color: rgba(52,211,153,.3); }
    .sku-status-pill.missing { color: #fecaca; background: rgba(239,68,68,.2); border-color: rgba(248,113,113,.3); }
    .progress-rule {
      margin-top: 12px; padding: 10px 12px; border: 1px solid rgba(56,189,248,.25);
      border-radius: 10px; background: rgba(14,116,144,.08); color: var(--muted); font-size: 11px;
    }
    .progress-rule strong { color: var(--cyan); display: block; margin-bottom: 4px; }

    .paths { color: var(--muted); font-size: 12px; margin-top: 10px; overflow-wrap: anywhere; }
    .log-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 9px; }
    pre {
      height: 350px; margin: 0; overflow: auto; white-space: pre-wrap; background: #050b14;
      border: 1px solid #1e2d41; border-radius: 10px; padding: 13px; color: #d7e2ef;
      font: 12px/1.5 Consolas, "Courier New", monospace;
    }
    .progress { height: 3px; background: #14243a; overflow: hidden; border-radius: 99px; margin-top: 12px; }
    .progress span { display: block; height: 100%; width: 35%; background: var(--cyan); transform: translateX(-120%); }
    .progress.running span { animation: move 1.2s infinite ease-in-out; }
    @keyframes move { to { transform: translateX(320%); } }
    .error { color: var(--red); } .ok { color: var(--green); } .amber { color: var(--amber); }

    /* Modal Popup */
    .modal-backdrop {
      position: fixed; inset: 0; background: rgba(3, 8, 16, 0.85);
      backdrop-filter: blur(8px); z-index: 1000;
      display: flex; align-items: center; justify-content: center; padding: 20px;
    }
    .modal {
      background: #0f1c2e; border: 1px solid #26364d; border-radius: 16px;
      width: min(940px, 100%); max-height: 90vh; display: flex; flex-direction: column;
      box-shadow: 0 25px 60px rgba(0, 0, 0, 0.65); overflow: hidden;
      animation: modalIn .2s ease-out;
    }
    @keyframes modalIn { from { opacity: 0; transform: scale(.96); } to { opacity: 1; transform: scale(1); } }
    .modal-header {
      padding: 14px 18px; border-bottom: 1px solid var(--line); display: flex;
      justify-content: space-between; align-items: center; background: rgba(16,28,46,.9);
    }
    .modal-header h3 { margin: 0; font-size: 17px; display: flex; align-items: center; gap: 10px; }
    .modal-close {
      background: transparent; border: none; font-size: 22px; line-height: 1;
      color: var(--muted); cursor: pointer; padding: 4px 8px; border-radius: 6px;
    }
    .modal-close:hover { color: var(--text); background: rgba(255,255,255,.08); }
    .modal-body { padding: 18px; overflow-y: auto; display: flex; flex-direction: column; gap: 16px; }
    
    .modal-previews { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 14px; }
    .preview-card {
      border: 1px solid var(--line); border-radius: 12px; background: #081321;
      padding: 11px; display: flex; flex-direction: column; gap: 8px;
    }
    .preview-card-title {
      font-size: 12px; font-weight: 600; color: var(--muted); display: flex;
      justify-content: space-between; align-items: flex-start; gap: 8px; min-height: 48px;
    }
    .preview-card-title > span:first-child { flex: 1 1 auto; min-width: 0; line-height: 1.35; }
    .preview-card-title > .badge {
      flex: 0 0 auto; min-width: max-content; white-space: nowrap;
      padding: 6px 10px; font-size: 12px; line-height: 1.2;
    }
    .preview-card-title.preview-result-title { align-items: center; }
    .preview-card-title > .preview-result-status {
      flex: 1 1 auto; width: 100%; min-width: 0; padding: 7px 9px; white-space: nowrap;
      overflow: hidden; text-overflow: ellipsis; text-align: center;
      font-size: clamp(10px, .78vw, 12px); line-height: 1.2;
    }
    .modal-notice {
      padding: 9px 12px; border: 1px solid #854d0e; border-radius: 9px;
      color: var(--amber); background: rgba(251,191,36,.1); font-size: 12px;
    }
    .preview-img-box {
      width: 100%; aspect-ratio: 1 / 1; border-radius: 8px; overflow: hidden;
      background: #050b14; border: 1px solid #1c2b3d; display: flex; align-items: center;
      justify-content: center; position: relative;
    }
    .preview-img-box img { width: 100%; height: 100%; object-fit: contain; }
    .preview-placeholder {
      color: var(--muted); font-size: 12px; text-align: center; padding: 15px;
    }
    .preview-meta {
      font-size: 11px; color: var(--muted); display: flex; flex-direction: column; gap: 4px;
    }
    .preview-meta code { color: #cbd5e1; }

    .modal-info-table {
      border: 1px solid var(--line); border-radius: 10px; background: #0b1728;
      padding: 12px 14px; font-size: 13px; display: grid; grid-template-columns: 140px 1fr; gap: 8px 12px;
    }
    .modal-info-table .label { color: var(--muted); }
    .modal-info-table .val { color: var(--text); overflow-wrap: anywhere; }

    .modal-footer {
      padding: 12px 18px; border-top: 1px solid var(--line); display: flex;
      justify-content: space-between; align-items: center; background: rgba(16,28,46,.9);
      flex-wrap: wrap; gap: 8px;
    }
    /* Main Tab Navigation */
    .tab-nav {
      display: flex; gap: 8px; margin-bottom: 16px; border-bottom: 1px solid var(--line);
      padding-bottom: 10px; flex-wrap: wrap;
    }
    .tab-nav-btn {
      background: #0b1728; border: 1px solid var(--line); border-radius: 10px;
      padding: 10px 18px; color: var(--muted); font-size: 13px; font-weight: 600;
      cursor: pointer; transition: all .2s ease; display: inline-flex; align-items: center; gap: 8px;
    }
    .tab-nav-btn:hover { color: var(--text); background: #13233a; border-color: #38bdf844; }
    .tab-nav-btn.active {
      color: #fff; background: var(--blue); border-color: #3b82f6;
      box-shadow: 0 4px 14px rgba(37,99,235,.35);
    }
    .tab-nav-badge {
      background: rgba(0,0,0,.35); border-radius: 99px; padding: 2px 8px;
      font-size: 11px; font-weight: 700; color: var(--cyan);
    }
    .tab-nav-btn.active .tab-nav-badge { background: rgba(255,255,255,.2); color: #fff; }
    .floating-log-nav {
      position: fixed; top: 50%; right: 18px; z-index: 900;
      transform: translateY(-50%); width: 48px; min-width: 48px; max-width: 190px;
      padding: 11px; overflow: hidden; justify-content: center;
      border-color: rgba(56,189,248,.3); opacity: .42;
      background: rgba(11,23,40,.96); color: #cbd5e1;
      box-shadow: 0 12px 30px rgba(0,0,0,.35), 0 0 0 1px rgba(56,189,248,.08);
      backdrop-filter: blur(10px);
      transition: width .2s ease, opacity .2s ease, color .2s ease, border-color .2s ease, background .2s ease, transform .15s ease;
    }
    .floating-log-nav:hover:not(:disabled), .floating-log-nav:focus-visible {
      width: 190px; justify-content: flex-start; opacity: 1;
      transform: translateY(-50%) translateX(-3px);
      color: #fff; border-color: var(--cyan); background: #13243c;
    }
    .floating-log-nav.active {
      opacity: .72; color: #fff; border-color: #60a5fa; background: var(--blue);
      box-shadow: 0 12px 32px rgba(37,99,235,.35);
    }
    .floating-log-nav.active:hover, .floating-log-nav.active:focus-visible { opacity: 1; }
    .floating-log-nav .floating-log-label {
      max-width: 0; opacity: 0; overflow: hidden; white-space: nowrap;
      line-height: 1.25; text-align: left;
      transition: max-width .2s ease, opacity .15s ease;
    }
    .floating-log-nav:hover .floating-log-label,
    .floating-log-nav:focus-visible .floating-log-label { max-width: 145px; opacity: 1; }
    .log-popup {
      position: fixed; top: 50%; left: 50%; z-index: 950;
      transform: translate(-50%, -50%); width: min(66vw, 1400px);
      height: 66vh; min-height: 360px;
      border: 1px solid rgba(56,189,248,.4); border-radius: 14px;
      background: rgba(8,19,33,.98);
      box-shadow: 0 24px 70px rgba(0,0,0,.55), 0 0 0 1px rgba(56,189,248,.08);
      backdrop-filter: blur(12px); overflow: hidden;
    }
    .log-popup-card { height: 100%; min-height: 0; display: flex; flex-direction: column; padding: 14px; }
    .log-popup .log-head { flex: 0 0 auto; }
    .log-popup #log { flex: 1; min-height: 0; height: auto; margin-bottom: 0; }
    .log-popup-close {
      width: 36px; height: 36px; padding: 0; margin-left: 6px;
      color: #fecdd3; border-color: #9f1239; background: #451826;
      font-size: 18px;
    }

    /* Prompt Sub-Tabs */
    .prompt-subtabs { display: flex; gap: 6px; margin-bottom: 12px; }
    .prompt-subtab-btn {
      flex: 1; padding: 9px 10px; font-size: 12px; font-weight: 600; border-radius: 8px;
      background: #081321; border: 1px solid var(--line); color: var(--muted);
      cursor: pointer; transition: all .15s ease; text-align: center; display: inline-flex;
      align-items: center; justify-content: center; gap: 6px;
    }
    .prompt-subtab-btn:hover { color: var(--text); background: #0e1c2e; }
    .prompt-subtab-btn.active {
      background: rgba(56,189,248,.15); color: var(--cyan); border-color: var(--cyan);
    }
    .prompt-panel {
      background: #081321; border: 1px solid var(--line); border-radius: 12px;
      padding: 14px; display: flex; flex-direction: column; gap: 10px;
    }
    .prompt-panel-title {
      font-size: 13px; font-weight: 700; color: var(--cyan); display: flex;
      justify-content: space-between; align-items: center;
    }
    .prompt-textarea {
      width: 100%; min-height: 135px; resize: vertical; font-family: "Cascadia Code", Consolas, Monaco, monospace;
      font-size: 12px; line-height: 1.45; padding: 10px; background: #050b14; color: var(--text);
      border: 1px solid var(--line); border-radius: 8px; box-sizing: border-box; outline: none;
    }
    .quota-banner {
      background: linear-gradient(135deg, rgba(234, 179, 8, 0.18) 0%, rgba(249, 115, 22, 0.18) 100%);
      border: 1px solid rgba(234, 179, 8, 0.45);
      border-radius: 12px;
      padding: 14px 18px;
      margin-bottom: 16px;
      display: flex;
      align-items: center;
      gap: 16px;
      box-shadow: 0 8px 24px rgba(234, 179, 8, 0.12);
      animation: pulse-border 2.5s infinite ease-in-out;
    }
    @keyframes pulse-border {
      0%, 100% { border-color: rgba(234, 179, 8, 0.45); }
      50% { border-color: rgba(234, 179, 8, 0.85); box-shadow: 0 0 20px rgba(234, 179, 8, 0.25); }
    }
    .quota-banner.hidden { display: none !important; }
    .quota-banner-icon { font-size: 32px; line-height: 1; flex-shrink: 0; }
    .quota-banner-body { flex: 1; }
    .quota-banner-title { color: #facc15; font-weight: 700; font-size: 14px; margin-bottom: 4px; display: flex; align-items: center; gap: 8px; }
    .quota-banner-text { color: #f8fafc; font-size: 13px; line-height: 1.45; }
    .quota-banner-close {
      background: transparent; border: 1px solid rgba(255,255,255,0.15); color: var(--muted);
      font-size: 14px; cursor: pointer; padding: 5px 10px; border-radius: 6px; flex-shrink: 0;
    }
    .quota-banner-close:hover { background: rgba(255,255,255,0.1); color: #fff; border-color: rgba(255,255,255,0.3); }

    /* Audit Drive Tab Styles */
    .audit-card {
      border: 1px solid var(--line); border-radius: 14px; background: rgba(16,28,46,.95);
      padding: 16px; margin-bottom: 14px; box-shadow: 0 8px 24px rgba(0,0,0,.2);
      display: flex; flex-direction: column; gap: 12px;
    }
    .audit-header {
      display: flex; justify-content: space-between; align-items: flex-start;
      border-bottom: 1px solid var(--line); padding-bottom: 12px; flex-wrap: wrap; gap: 10px;
    }
    .audit-title-box { display: flex; flex-direction: column; gap: 4px; }
    .audit-title { font-size: 16px; font-weight: 700; color: var(--text); display: flex; align-items: center; gap: 8px; }
    .audit-subtitle { font-size: 12px; color: var(--muted); display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
    .audit-check-box {
      background: #0b1728; border: 1px solid var(--line); border-radius: 10px;
      padding: 12px 14px; display: flex; flex-direction: column; gap: 10px;
    }
    .audit-check-header {
      display: flex; justify-content: space-between; align-items: center; font-size: 13px; font-weight: 600; flex-wrap: wrap; gap: 8px;
    }
    .audit-progress-bar {
      height: 8px; background: #050b14; border-radius: 99px; overflow: hidden; width: 100%;
    }
    .audit-progress-bar span {
      display: block; height: 100%; background: linear-gradient(90deg, var(--blue), var(--green));
      border-radius: 99px; transition: width .3s ease;
    }
    .audit-chips-container {
      display: flex; flex-wrap: wrap; gap: 6px; max-height: 220px; overflow-y: auto; padding: 6px 0;
    }
    .audit-chip {
      display: inline-flex; align-items: center; gap: 5px; padding: 4px 8px;
      border-radius: 6px; font-size: 11px; font-weight: 600; font-family: Consolas, monospace;
      border: 1px solid var(--line); background: #101c2e; color: var(--text); cursor: default;
    }
    .audit-chip.done {
      border-color: rgba(52, 211, 153, 0.4); background: rgba(52, 211, 153, 0.1); color: #6ee7b7;
    }
    .audit-chip.missing {
      border-color: rgba(251, 113, 133, 0.4); background: rgba(251, 113, 133, 0.1); color: #fda4af;
    }
    .audit-chip.partial {
      border-color: rgba(251, 191, 36, 0.4); background: rgba(251, 191, 36, 0.1); color: #fde68a;
    }
    .audit-actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
    .chip-filter-btn {
      background: transparent; border: 1px solid var(--line); color: var(--muted);
      border-radius: 6px; padding: 3px 8px; font-size: 11px; cursor: pointer;
    }
    .chip-filter-btn.active {
      background: rgba(56,189,248,.2); border-color: var(--cyan); color: #7dd3fc;
    }

    /* Drive Comparison Table & Management Styles */
    .audit-metrics { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 10px; margin-top: 14px; }
    .audit-metrics .metric { min-height: 112px; display: flex; flex-direction: column; justify-content: flex-start; }
    .drive-table-wrap { width: 100%; overflow-x: auto; margin-top: 14px; border: 1px solid var(--line); border-radius: 12px; background: #0b1728; }
    .drive-table { width: 100%; min-width: 1710px; table-layout: fixed; border-collapse: collapse; font-size: 13px; text-align: left; }
    .drive-table th { background: #101c2e; padding: 12px 14px; color: var(--muted); font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: .5px; border-bottom: 1px solid var(--line); white-space: nowrap; }
    .drive-table td { height: 112px; padding: 12px 14px; border-bottom: 1px solid rgba(38,54,77,.4); vertical-align: middle; overflow: hidden; }
    .drive-table tr:last-child td { border-bottom: none; }
    .drive-table tr:hover td { background: rgba(56,189,248,.04); }
    .drive-table-toolbar { display: grid; grid-template-columns: minmax(420px, 1fr) auto; gap: 12px; align-items: center; margin-top: 14px; }
    .dt-toolbar-left { display: grid; grid-template-columns: minmax(220px, 1fr) 240px; gap: 10px; align-items: center; min-width: 0; }
    .dt-toolbar-right { display: flex; gap: 8px; align-items: center; justify-content: flex-end; flex-wrap: nowrap; }
    .dt-toolbar-left input, .dt-toolbar-left select, .dt-toolbar-right button { height: 40px; }
    .dt-toolbar-left input, .dt-toolbar-left select { width: 100%; min-width: 0; }
    .drive-link-box { display: flex; align-items: center; gap: 6px; width: 100%; min-width: 0; }
    .drive-link-box a { color: var(--cyan); text-decoration: none; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; }
    .drive-link-box a:hover { text-decoration: underline; }
    .dt-progress-bar { width: 85px; height: 7px; background: #050b14; border-radius: 99px; overflow: hidden; display: inline-block; vertical-align: middle; margin-right: 6px; }
    .dt-progress-bar span { display: block; height: 100%; background: linear-gradient(90deg, var(--blue), var(--green)); border-radius: 99px; }
    .metric-sub { font-size: 11px; color: var(--muted); margin-top: 2px; }
    .badge-unlinked { border: 1px dashed var(--line); color: var(--muted); background: transparent; padding: 2px 7px; border-radius: 99px; font-size: 11px; }
    .btn-icon { padding: 4px 7px; font-size: 12px; line-height: 1; }
    .dt-folder-stack { height: 100%; display: flex; flex-direction: column; justify-content: center; gap: 7px; }
    .dt-folder-head { display: flex; align-items: center; min-width: 0; }
    .dt-folder-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 14px; color: var(--text); cursor: pointer; }
    .dt-status-badge { width: 112px; min-height: 30px; padding: 4px 8px; display: inline-flex; align-items: center; justify-content: center; text-align: center; line-height: 1.15; border-radius: 99px; font-size: 11px; box-sizing: border-box; }
    .dt-actions { display: grid; grid-template-columns: 78px 126px 86px 46px 46px; gap: 6px; justify-content: end; align-items: center; }
    .dt-actions button { width: 100%; height: 36px; padding: 6px 8px; white-space: nowrap; }
    .dt-action-placeholder { visibility: hidden; pointer-events: none; }

    @media(max-width: 900px) {
      .grid { grid-template-columns: 1fr; }
      .wide { grid-column: auto; }
      .fields { grid-template-columns: 1fr; }
      .checks { grid-template-columns: 1fr; }
      .progress-summary { grid-template-columns: 1fr 1fr; }
      .audit-metrics { grid-template-columns: 1fr 1fr; }
      .drive-table-toolbar { grid-template-columns: 1fr; }
      .dt-toolbar-right { justify-content: flex-start; flex-wrap: wrap; }
      .timing-summary { grid-template-columns: 1fr 1fr; }
      .sku-columns { grid-template-columns: 1fr; }
      .modal-previews { grid-template-columns: 1fr; }
      .drive-add-row { grid-template-columns: 1fr; }
    }
    /* Output quality tab: keep styles isolated from existing screens. */
    #tab-quality { display: grid; gap: 14px; }
    #tab-quality .quality-workspace { display: grid; grid-template-columns: minmax(300px, 3fr) minmax(0, 7fr); gap: 14px; }
    #tab-quality .quality-workspace > .card { height: clamp(420px, 72vh, 900px); min-width: 0; min-height: 0; overflow: hidden; display: flex; flex-direction: column; }
    #tab-quality .quality-workspace .card-header-flex { flex-shrink: 0; }
    #tab-quality #quality-active-folder { flex-shrink: 0; overflow-wrap: anywhere; }
    #tab-quality .quality-browser-frame { width: 100%; flex: 1 1 0; min-height: 0; border: 1px solid var(--line); border-radius: 10px; background: white; }
    #tab-quality .quality-field + .quality-field { margin-top: 14px; }
    #tab-quality .quality-toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
    #tab-quality .quality-folders { display: flex; flex-wrap: wrap; align-items: flex-start; gap: 6px; margin-top: 10px; max-height: 160px; overflow-y: auto; }
    #tab-quality .quality-folder { flex: 0 1 auto; width: auto; max-width: 100%; padding: 5px 9px; font-size: 12px; line-height: 1.4; text-align: left; overflow-wrap: anywhere; }
    #tab-quality .quality-folder.active { color: white; background: var(--blue); border-color: #3b82f6; }
    #tab-quality .quality-gallery {
      min-height: 0; flex: 1 1 0; overflow-y: auto; overflow-x: hidden; overscroll-behavior: contain;
      scrollbar-gutter: stable; border: 1px solid var(--line); border-radius: 10px;
      background: #081321; padding: 12px;
    }
    #tab-quality .quality-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); align-content: start; width: 100%; gap: 12px; }
    #tab-quality .quality-image { position: relative; }
    #tab-quality .quality-pair { grid-column: span 2; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; min-width: 0; }
    #tab-quality .quality-image.failed { border-color: #ef4444; }
    #tab-quality .quality-fail-toggle { position: absolute; top: 12px; right: 12px; z-index: 1; display: grid; place-items: center; width: 28px; height: 28px; padding: 0; border-radius: 5px; background: #081321; border: 1px solid #94a3b8; color: white; font-size: 21px; line-height: 1; }
    #tab-quality .quality-fail-toggle[aria-checked="true"] { background: #dc2626; border-color: #f87171; }
    #quality-rerun-dialog { width: min(70vw, 1000px); height: 70vh; max-width: 94vw; max-height: 90vh; margin: auto; padding: 20px; color: var(--text); background: var(--card); border: 1px solid var(--line); border-radius: 14px; }
    #quality-rerun-dialog::backdrop { background: rgba(0,0,0,.65); }
    .quality-rerun-content { height: 100%; min-height: 0; display: flex; flex-direction: column; gap: 12px; }
    #quality-rerun-list { flex: 1; min-height: 0; overflow-y: auto; }
    .quality-rerun-row { display: flex; align-items: center; gap: 10px; padding: 8px; border-bottom: 1px solid var(--line); overflow-wrap: anywhere; }
    .quality-rerun-row img { width: 56px; height: 56px; object-fit: contain; }
    .quality-rerun-row input { flex-shrink: 0; }
    .quality-rerun-actions { display: flex; justify-content: flex-end; gap: 8px; }
    #tab-quality .quality-image { min-width: 0; margin: 0; padding: 8px; border: 1px solid var(--line); border-radius: 10px; background: var(--card); }
    #tab-quality .quality-image { cursor: pointer; transition: border-color .15s ease, transform .15s ease, box-shadow .15s ease; }
    #tab-quality .quality-image:hover, #tab-quality .quality-image:focus { border-color: var(--cyan); transform: translateY(-2px); outline: none; box-shadow: 0 8px 22px rgba(0,0,0,.25); }
    #tab-quality .quality-image.uploading { opacity: .55; pointer-events: none; }
    #tab-quality .quality-image.selected { border-color: var(--green); box-shadow: 0 0 0 2px rgba(34,197,94,.28), 0 8px 22px rgba(0,0,0,.25); }
    #tab-quality .quality-image img { display: block; width: 100%; aspect-ratio: 1; object-fit: contain; background: #0b1728; border-radius: 6px; }
    #tab-quality .quality-image figcaption { margin-top: 8px; overflow-wrap: anywhere; font-size: 12px; }
    #tab-quality .quality-empty { min-height: 100%; display: grid; place-content: center; text-align: center; color: var(--muted); padding: 20px; }
    #tab-quality .quality-empty strong { color: var(--text); font-size: 15px; margin-bottom: 6px; }
    #tab-quality .quality-errors { max-height: 140px; overflow-y: auto; white-space: pre-wrap; overflow-wrap: anywhere; color: var(--muted); background: #081321; border: 1px solid var(--line); border-radius: 9px; padding: 12px; }
    #tab-quality .quality-errors.has-errors { color: var(--red); border-color: #9f1239; }
    @media(max-width: 480px) {
      #tab-quality .quality-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
      .floating-log-nav,
      .floating-log-nav:hover:not(:disabled),
      .floating-log-nav:focus-visible { right: 8px; width: 46px; min-width: 46px; padding: 11px; justify-content: center; }
      .floating-log-nav .floating-log-label { display: none; }
      .log-popup { width: calc(100vw - 16px); height: 78vh; }
    }
    @media(max-width: 900px) {
      #tab-quality .quality-workspace { grid-template-columns: 1fr; }
      .drive-manager-box { grid-template-rows: repeat(5, minmax(148px, auto)); }
    }
  </style>
</head>
<body>
<main class="shell">
  <header>
    <div><h1>VEO3 Auto Pipeline</h1><div class="subtitle">Drive → Crop → Seamless (01) → Swatch (02) → Package</div></div>
    <div id="status" class="badge">Đang kết nối...</div>
  </header>

  <!-- Main Navigation Tabs -->
  <nav class="tab-nav">
    <button id="nav-tab-dashboard" class="tab-nav-btn active" type="button" onclick="switchTab('dashboard')">
      🚀 <span>1. Điều khiển & Cấu hình</span>
    </button>
    <button id="nav-tab-progress" class="tab-nav-btn" type="button" onclick="switchTab('progress')">
      📊 <span>2. Tiến độ & Danh sách vải</span>
      <span id="nav-progress-badge" class="tab-nav-badge">0%</span>
    </button>
    <button id="nav-tab-audit" class="tab-nav-btn" type="button" onclick="switchTab('audit')">
      📁 <span>3. Quản lý Drive & So sánh vải</span>
      <span id="nav-audit-badge" class="tab-nav-badge">0</span>
    </button>
    <button id="nav-tab-quality" class="tab-nav-btn" type="button" onclick="switchTab('quality')">
      🔍 <span>4. Test vải</span>
    </button>
  </nav>

  <button id="nav-tab-logs" class="floating-log-nav" type="button"
          onclick="openLogPopup()" title="Mở Nhật ký thời gian thực"
          aria-label="Mở Nhật ký thời gian thực" aria-expanded="false">
    📜 <span class="floating-log-label">Nhật ký thời gian thực</span>
  </button>

  <!-- Quota Exhausted Alert Banner -->
  <div id="quota-banner" class="quota-banner hidden">
    <div class="quota-banner-icon">⏳</div>
    <div class="quota-banner-body">
      <div class="quota-banner-title">⚠️ ĐÃ HẾT HẠN MỨC (QUOTA) TẠO ẢNH CỦA CHATGPT</div>
      <div id="quota-banner-text" class="quota-banner-text">Đang tải thông tin hạn mức...</div>
    </div>
    <button type="button" class="quota-banner-close" title="Ẩn thông báo" onclick="dismissQuotaBanner()">✕ Đóng</button>
  </div>

  <!-- TAB 1: Dashboard & Settings -->
  <section id="tab-dashboard" class="tab-content">
    <div class="grid">
      <!-- Left Column: Source & Control -->
      <div style="display:flex; flex-direction:column; gap:14px;">
        <div class="card">
          <h2>📁 Nguồn ảnh vải</h2>
          <div class="source-tabs">
            <label class="source-tab"><input id="source-drive" name="source-mode" type="radio" value="drive" checked> Google Drive</label>
            <label class="source-tab"><input id="source-local" name="source-mode" type="radio" value="local"> Thư mục local</label>
          </div>
          <div id="drive-source">
            <label>Danh sách & Quản lý thư mục Google Drive</label>
            <div class="drive-list-toolbar">
              <input id="drive-folder-search" type="text" placeholder="🔎 Tìm theo tên thư mục..." autocomplete="off">
              <span id="drive-folder-count" class="drive-list-count"></span>
            </div>
            <div id="drive-folders-list" class="drive-manager-box"></div>
            <div id="drive-pagination" class="drive-pagination hidden">
              <button id="drive-page-prev" type="button" class="ghost btn-sm">← Trước</button>
              <span id="drive-page-info" class="drive-page-info"></span>
              <button id="drive-page-next" type="button" class="ghost btn-sm">Sau →</button>
            </div>
            
            <div style="margin-top: 10px;">
              <label>Thêm Link Drive mới</label>
              <div class="drive-add-row">
                <input id="new-drive-url" type="text" placeholder="https://drive.google.com/drive/folders/...">
                <input id="new-drive-folder" type="text" placeholder="Tên thư mục (VD: ABC)">
                <button id="add-drive-btn" type="button" class="ghost">+ Thêm</button>
              </div>
            </div>
            <div class="hint">Mỗi link Drive sẽ được tải và xuất kết quả vào thư mục con mang tên tương ứng.</div>
          </div>
          <div id="local-source" class="hidden">
            <label for="local-folder">Thư mục chứa ảnh vải trên máy</label>
            <div class="row"><input id="local-folder" type="text" readonly placeholder="Chưa chọn thư mục"><button id="browse" type="button">Chọn thư mục</button></div>
            <div class="hint">Khi dùng local, bước Import Drive sẽ được bỏ qua. Ảnh gốc không bị sửa hoặc xóa.</div>
          </div>
          <div class="fields">
            <div><label for="sku">SKU (để trống = tất cả)</label><input id="sku" type="text" placeholder="SP1M29"></div>
            <div><label for="limit">Giới hạn</label><input id="limit" type="number" min="1" placeholder="Tất cả"></div>
            <div><label for="perchat">Ảnh mỗi chat</label><input id="perchat" type="number" min="1" max="20" value="10"></div>
          </div>
          <div class="actions" style="margin-top: 12px;"><button id="save" class="ghost">💾 Lưu cấu hình</button></div>
        </div>

        <div class="card">
          <h2>⚡ Chọn luồng & Điều hành (ChatGPT Pipeline)</h2>
          <div class="checks">
            <label class="check"><input id="flow-import" type="checkbox" checked> 1. Import Drive</label>
            <label class="check"><input id="flow-crop" type="checkbox" checked> 2. Crop cố định</label>
            <label class="check"><input id="flow-seamless" type="checkbox" checked> 3. Tạo seamless (01_TEXTURE)</label>
            <div style="margin-left: 22px; margin-top: -4px; margin-bottom: 4px; font-size: 12px; color: var(--green); grid-column: 1 / -1;">
              ✨ Tự động hậu kỳ vá mép ảnh ChatGPT bằng thuật toán Efros-Freeman
            </div>
            <label class="check"><input id="flow-fabric" type="checkbox" checked> 4. Tạo swatch vải (02_FABRIC)</label>
            <label class="check"><input id="flow-package" type="checkbox" checked> 5. Đóng gói SKU</label>
            <label class="check"><input id="dry" type="checkbox"> Chỉ xem trước</label>
            <label class="check"><input id="force" type="checkbox"> Cho phép thay thế</label>
            <label class="check" style="color:var(--cyan); grid-column: 1 / -1;"><input id="auto-retry" type="checkbox" checked> 🔄 Tự động thử lại khi lỗi (Auto-Restart)</label>
          </div>
          <div class="fields" style="margin-top: 8px;">
            <div><label for="retry-delay">Chờ thử lại (giây)</label><input id="retry-delay" type="number" min="5" max="3600" value="120" placeholder="120s"></div>
            <div><label for="retry-max">Số lần thử lại tối đa</label><input id="retry-max" type="number" min="0" max="100" value="10" placeholder="10 (0 = ∞)"></div>
          </div>
          <div class="actions" style="display:flex; flex-wrap:wrap; gap:8px;">
            <button id="run" class="primary">▶ Chạy ChatGPT Pipeline</button>
            <button id="run-algo" class="primary" style="background:linear-gradient(135deg, #059669 0%, #047857 100%);" title="Quét và sửa lỗi đường nối cho các texture ChatGPT đã tạo">🔧 Vá lỗi Seamless (Fix Seams)</button>
            <button id="run-flow" class="primary" style="background:linear-gradient(135deg, #0284c7 0%, #0369a1 100%);">🌊 Chạy Google Flow</button>
            <button id="stop" class="danger" disabled>⏹ Dừng</button>
            <button id="chrome" class="ghost">🌐 Mở Chrome</button>
            <button id="check" class="ghost">🔍 Check ChatGPT</button>
            <button id="check-flow" class="ghost">🔍 Check Flow</button>
          </div>
          <div id="progress" class="progress"><span></span></div>
        </div>
      </div>

      <!-- Right Column: Prompt Settings & Environment -->
      <div style="display:flex; flex-direction:column; gap:14px;">
        <div class="card">
          <div class="card-header-flex">
            <h2>⚙️ Cấu hình Prompt</h2>
            <div class="hint" style="margin:0;">Đính kèm file hoặc Nhập thủ công</div>
          </div>

          <!-- Prompt Sub-tabs -->
          <div class="prompt-subtabs">
            <button id="prompt-subtab-tex" type="button" class="prompt-subtab-btn active" onclick="switchPromptSubTab('texture')">
              🎨 3. Seamless (01)
            </button>
            <button id="prompt-subtab-fab" type="button" class="prompt-subtab-btn" onclick="switchPromptSubTab('fabric')">
              👗 4. Swatch vải (02)
            </button>
            <button id="prompt-subtab-flow" type="button" class="prompt-subtab-btn" onclick="switchPromptSubTab('flow')">
              🌊 Google Flow Prompt
            </button>
          </div>

          <!-- Sub-panel: Seamless Texture -->
          <div id="prompt-panel-tex" class="prompt-panel">
            <div class="prompt-panel-title">
              <span>Cấu hình: Seamless Texture (01_TEXTURE)</span>
              <span id="tex-prompt-badge" class="badge ok">Đính kèm file</span>
            </div>
            <div class="source-tabs" style="margin: 4px 0;">
              <label class="source-tab"><input id="tex-prompt-mode-attach" name="tex-prompt-mode" type="radio" value="attachment" checked onchange="updatePromptUI()"> 📎 Đính kèm file</label>
              <label class="source-tab"><input id="tex-prompt-mode-manual" name="tex-prompt-mode" type="radio" value="manual" onchange="updatePromptUI()"> 📝 Nhập thủ công</label>
            </div>
            <div id="tex-attachment-box">
              <label for="tex-prompt-file">Đường dẫn file Master Prompt</label>
              <div class="row">
                <input id="tex-prompt-file" type="text" placeholder="prompts/prompts_attachments/01_TEXTURE_SEAMLESS_MASTER.md">
                <button id="browse-tex-prompt" type="button" class="ghost">📁 Chọn file</button>
              </div>
              <div class="hint">File tài liệu sẽ được đính kèm vào ChatGPT cùng ảnh ở lượt đầu tiên.</div>
            </div>
            <div id="tex-manual-box" class="hidden">
              <label for="tex-prompt-text">Nội dung Master Prompt thủ công</label>
              <textarea id="tex-prompt-text" class="prompt-textarea" rows="7" placeholder="Nhập nội dung prompt cho Seamless Texture..." oninput="updatePromptCounts()"></textarea>
              <div class="prompt-action-bar">
                <button id="load-default-tex-prompt" type="button" class="ghost btn-sm">🔄 Tải từ file mẫu</button>
                <span id="tex-prompt-count" class="hint">0 ký tự</span>
              </div>
            </div>
          </div>

          <!-- Sub-panel: Fabric Swatch -->
          <div id="prompt-panel-fab" class="prompt-panel hidden">
            <div class="prompt-panel-title">
              <span>Cấu hình: Swatch vải (02_FABRIC)</span>
              <span id="fab-prompt-badge" class="badge ok">Đính kèm file</span>
            </div>
            <div class="source-tabs" style="margin: 4px 0;">
              <label class="source-tab"><input id="fab-prompt-mode-attach" name="fab-prompt-mode" type="radio" value="attachment" checked onchange="updatePromptUI()"> 📎 Đính kèm file</label>
              <label class="source-tab"><input id="fab-prompt-mode-manual" name="fab-prompt-mode" type="radio" value="manual" onchange="updatePromptUI()"> 📝 Nhập thủ công</label>
            </div>
            <div id="fab-attachment-box">
              <label for="fab-prompt-file">Đường dẫn file Master Prompt</label>
              <div class="row">
                <input id="fab-prompt-file" type="text" placeholder="prompts/prompts_attachments/02_FABRIC_SWATCH_MASTER.md">
                <button id="browse-fab-prompt" type="button" class="ghost">📁 Chọn file</button>
              </div>
              <div class="hint">File tài liệu sẽ được đính kèm vào ChatGPT cùng ảnh ở lượt đầu tiên.</div>
            </div>
            <div id="fab-manual-box" class="hidden">
              <label for="fab-prompt-text">Nội dung Master Prompt thủ công</label>
              <textarea id="fab-prompt-text" class="prompt-textarea" rows="7" placeholder="Nhập nội dung prompt cho Fabric Swatch..." oninput="updatePromptCounts()"></textarea>
              <div class="prompt-action-bar">
                <button id="load-default-fab-prompt" type="button" class="ghost btn-sm">🔄 Tải từ file mẫu</button>
                <span id="fab-prompt-count" class="hint">0 ký tự</span>
              </div>
            </div>
          </div>

          <!-- Sub-panel: Google Flow Texture -->
          <div id="prompt-panel-flow" class="prompt-panel hidden">
            <div class="prompt-panel-title">
              <span>Cấu hình: Texture Google Flow</span>
              <span id="flow-prompt-badge" class="badge ok">Đính kèm file</span>
            </div>
            <div class="source-tabs" style="margin: 4px 0;">
              <label class="source-tab"><input id="flow-prompt-mode-attach" name="flow-prompt-mode" type="radio" value="attachment" checked onchange="updatePromptUI()"> 📎 Đính kèm file</label>
              <label class="source-tab"><input id="flow-prompt-mode-manual" name="flow-prompt-mode" type="radio" value="manual" onchange="updatePromptUI()"> 📝 Nhập thủ công</label>
            </div>
            <div id="flow-attachment-box">
              <label for="flow-prompt-file">Đường dẫn file Master Prompt</label>
              <div class="row">
                <input id="flow-prompt-file" type="text" placeholder="prompts/scanned_to_texture_prompt.md">
                <button id="browse-flow-prompt" type="button" class="ghost">📁 Chọn file</button>
              </div>
              <div class="hint">File tài liệu sẽ được áp dụng cho Google Flow khi tạo texture.</div>
            </div>
            <div id="flow-manual-box" class="hidden">
              <label for="flow-prompt-text">Nội dung Master Prompt thủ công</label>
              <textarea id="flow-prompt-text" class="prompt-textarea" rows="7" placeholder="Nhập nội dung prompt cho Google Flow..." oninput="updatePromptCounts()"></textarea>
              <div class="prompt-action-bar">
                <button id="load-default-flow-prompt" type="button" class="ghost btn-sm">🔄 Tải từ file mẫu</button>
                <span id="flow-prompt-count" class="hint">0 ký tự</span>
              </div>
            </div>
          </div>
        </div>

        <!-- Telegram Notifications Card -->
        <div class="card">
          <div class="card-header-flex">
            <h2>📱 Thông báo Telegram Bot</h2>
            <label class="check" style="margin:0; padding:4px 8px; font-size:12px; border-color:var(--cyan);"><input id="tele-enabled" type="checkbox" onchange="toggleTelegramInputs()"> Bật thông báo</label>
          </div>
          <div id="tele-config-box">
            <div class="fields" style="grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 0;">
              <div>
                <label for="tele-token">Bot Token</label>
                <input id="tele-token" type="text" placeholder="123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ">
              </div>
              <div>
                <label for="tele-chat-id">Chat ID</label>
                <input id="tele-chat-id" type="text" placeholder="123456789 hoặc -100...">
              </div>
            </div>
            <div class="checks" style="margin-top:8px; grid-template-columns: 1fr 1fr;">
              <label class="check"><input id="tele-quota" type="checkbox" checked> ⚠️ Hết Quota (Kèm giờ reset)</label>
              <label class="check"><input id="tele-complete" type="checkbox" checked> ✅ Hoàn thành Pipeline</label>
              <label class="check"><input id="tele-safestop" type="checkbox" checked> 🛑 Captcha / Cần đăng nhập</label>
              <label class="check"><input id="tele-folder" type="checkbox" checked> 📁 Xong từng thư mục Drive</label>
            </div>
            <div class="hint" style="margin-top:6px;">
              💡 <b>Cách lấy:</b> Chat với <code>@BotFather</code> để tạo bot và lấy Token. Chat với <code>@userinfobot</code> hoặc thêm bot vào nhóm để lấy Chat ID.
            </div>
            <div class="actions" style="margin-top:10px; display:flex; justify-content:space-between; align-items:center;">
              <div style="display:flex; gap:8px;">
                <button id="tele-test-btn" type="button" class="ghost btn-sm" onclick="testTelegramConnection()">🧪 Gửi tin nhắn thử (Test)</button>
                <button id="tele-save-btn" type="button" class="ghost btn-sm" onclick="saveTelegramSettings()">💾 Lưu cấu hình</button>
              </div>
              <span id="tele-status-msg" class="hint" style="margin:0; font-size:12px;"></span>
            </div>
          </div>
        </div>

        <div class="card">
          <h2>💻 Môi trường & Hệ thống</h2>
          <div class="env"><span>Project</span><code id="project">-</code><span>Python</span><code id="python">-</code></div>
          <div class="actions" style="margin-top:12px; justify-content:space-between;">
            <button class="ghost" type="button" onclick="switchTab('progress')">📊 Xem tiến độ chi tiết →</button>
            <button id="shutdown-dash" class="ghost" type="button" onclick="requestShutdownApp()">Đóng ứng dụng</button>
          </div>
        </div>
      </div>
    </div>
  </section>

  <!-- TAB 2: Progress & Fabric SKU Monitor -->
  <section id="tab-progress" class="tab-content hidden">
    <div class="card wide">
      <div class="card-header-flex">
        <h2>Tiến độ danh sách vải & Thời gian tạo ảnh</h2>
        <div style="display:flex; align-items:center; gap:10px; margin-left:auto;">
          <button class="ghost btn-sm" type="button" onclick="switchTab('audit')">📁 Bảng quản lý & đối chiếu Drive →</button>
          <div class="folder-filter-wrap">
            <label for="folder-filter" style="margin:0; font-weight:600; font-size:12px;">📁 Thư mục:</label>
            <select id="folder-filter" class="folder-filter-select" onchange="onFolderFilterChange()"></select>
          </div>
        </div>
      </div>
      <div class="progress-summary">
        <div class="metric"><strong id="fabric-percent">0%</strong><span>Tiến độ</span></div>
        <div class="metric"><strong id="fabric-total">0</strong><span>Tổng SKU</span></div>
        <div class="metric"><strong id="fabric-created" class="ok">0</strong><span>Đã tạo</span><div class="sub">Có đủ Seamless và Swatch</div></div>
        <div class="metric"><strong id="fabric-pending" class="amber">0</strong><span>Chưa tạo</span><div class="sub">Thiếu Seamless hoặc Swatch</div></div>
      </div>
      <div class="fabric-progress"><span id="fabric-progress-bar"></span></div>
      
      <!-- Timing Stats Panel -->
      <div class="timing-summary">
        <div class="metric highlight" id="card-session-time">
          <strong id="session-time">00:00:00</strong>
          <span>Thời gian phiên</span>
          <div id="session-status-sub" class="sub">Sẵn sàng</div>
        </div>
        <div class="metric">
          <strong id="session-count">0 ảnh</strong>
          <span>Tạo trong phiên</span>
          <div id="session-speed-sub" class="sub">-</div>
        </div>
        <div class="metric highlight">
          <strong id="avg-time">--</strong>
          <span>Trung bình / 1 ảnh</span>
          <div id="avg-calc-sub" class="sub">Dựa trên lịch sử ChatGPT</div>
        </div>
        <div class="metric">
          <strong id="eta-time">Theo quy tắc</strong>
          <span>Trạng thái hoàn thiện</span>
          <div class="sub" id="eta-sub">Cần đủ Seamless (01) và Swatch (02)</div>
        </div>
      </div>

      <div class="sku-columns">
        <div class="sku-box">
          <div class="sku-title">
            <div class="sku-title-left"><span class="ok">● Đã tạo</span><strong id="created-count">0</strong></div>
            <input id="search-created" class="sku-search" type="text" placeholder="Tìm SKU..." oninput="filterSkuCards('created')">
          </div>
          <div id="created-list" class="sku-grid"></div>
        </div>
        <div class="sku-box">
          <div class="sku-title">
            <div class="sku-title-left"><span class="amber">● Chưa tạo</span><strong id="pending-count">0</strong></div>
            <input id="search-pending" class="sku-search" type="text" placeholder="Tìm SKU..." oninput="filterSkuCards('pending')">
          </div>
          <div id="pending-list" class="sku-grid"></div>
        </div>
      </div>
      <div class="progress-rule">
        <strong>ℹ Quy tắc tính trạng thái</strong>
        Chỉ những SKU có đủ cả <b>seamless_texture.png</b> và <b>image_1.png</b> mới được tính là “Đã tạo”. Thiếu một trong hai hoặc thiếu cả hai sẽ được tính là “Chưa tạo”.
      </div>
      <div id="fabric-paths" class="paths"></div>
    </div>
  </section>

  <!-- Persistent live-log popup -->
  <aside id="tab-logs" class="log-popup hidden" role="dialog" aria-modal="false" aria-labelledby="log-popup-title">
    <div class="log-popup-card">
      <div class="log-head">
        <h2 id="log-popup-title">📜 Nhật ký thời gian thực</h2>
        <div>
          <button class="log-popup-close" type="button" onclick="closeLogPopup()" title="Đóng nhật ký" aria-label="Đóng nhật ký">✕</button>
        </div>
      </div>
      <pre id="log"></pre>
    </div>
  </aside>

  <!-- TAB 4: Google Drive Management & Fabric Folder Comparison -->
  <section id="tab-audit" class="tab-content hidden">
    <div class="card wide">
      <div class="card-header-flex">
        <div>
          <h2>📁 Quản Lý Link Google Drive & Đối Chiếu Thư Mục Vải</h2>
          <div class="hint">Quản lý liên kết Google Drive cho từng thư mục vải, đối chiếu số lượng ảnh và theo dõi tiến độ tạo thành phẩm</div>
        </div>
      </div>

      <!-- Top Metric Cards -->
      <div class="audit-metrics">
        <div class="metric">
          <strong id="dt-total-folders">0</strong>
          <span>Thư mục vải</span>
          <div class="metric-sub"><span id="dt-linked-folders" class="ok">0</span> đã gán link • <span id="dt-unlinked-folders" class="amber">0</span> chưa gán</div>
        </div>
        <div class="metric">
          <strong id="dt-total-skus">0</strong>
          <span>Tổng SKU trên máy</span>
          <div class="metric-sub">Tất cả mẫu vải đã scan/crop</div>
        </div>
        <div class="metric">
          <strong id="dt-total-created" class="ok">0</strong>
          <span>Đã tạo thành phẩm</span>
          <div class="metric-sub">Có Seamless / Swatch</div>
        </div>
        <div class="metric">
          <strong id="dt-total-pending" class="amber">0</strong>
          <span>Còn thiếu (Pending)</span>
          <div class="metric-sub">Chưa hoàn thành</div>
        </div>
        <div class="metric highlight">
          <strong id="dt-overall-percent">0%</strong>
          <span>Tiến độ tổng thể</span>
          <div class="dt-progress-bar" style="width:100%; margin-top:4px;"><span id="dt-progress-bar-span"></span></div>
        </div>
      </div>

      <!-- Action Toolbar -->
      <div class="drive-table-toolbar">
        <div class="dt-toolbar-left">
          <input id="dt-search" type="text" class="sku-search" placeholder="🔍 Tìm kiếm mã thư mục vải..." oninput="filterDriveTable()">
          <select id="dt-status-filter" onchange="filterDriveTable()">
            <option value="all">📁 Tất cả trạng thái</option>
            <option value="completed">✔ Đã hoàn thành (100%)</option>
            <option value="in_progress">⚡ Đang làm dở</option>
            <option value="pending">⚠️ Còn thiếu SKU</option>
            <option value="linked">🔗 Đã có link Drive</option>
            <option value="unlinked">⚠️ Chưa gán link Drive</option>
          </select>
        </div>
        <div class="dt-toolbar-right">
          <button type="button" class="ghost btn-sm" onclick="openAddDriveModal()">➕ Thêm / Gán Link Drive</button>
          <button type="button" class="ghost btn-sm" onclick="openBulkDriveModal()">📋 Ghép nối link hàng loạt</button>
          <button type="button" class="primary btn-sm" id="btn-audit-all" onclick="auditAllDriveLinks()">🔄 Quét & Đối chiếu tất cả link</button>
        </div>
      </div>

      <!-- Main Comparison Table -->
      <div class="drive-table-wrap">
        <table class="drive-table">
          <colgroup>
            <col style="width:155px"><col style="width:145px"><col style="width:300px"><col style="width:140px"><col style="width:145px">
            <col style="width:175px"><col style="width:165px"><col style="width:140px"><col style="width:425px">
          </colgroup>
          <thead>
            <tr>
              <th>📁 Thư mục vải</th>
              <th>● Trạng thái</th>
              <th>🔗 Link Google Drive</th>
              <th>📥 Drive (Ảnh)</th>
              <th>✂️ Đã crop / Raw</th>
              <th>🎨 Đã tạo thành phẩm</th>
              <th>📊 Tiến độ</th>
              <th>⚠️ Còn thiếu</th>
              <th style="text-align:right;">⚡ Thao tác</th>
            </tr>
          </thead>
          <tbody id="drive-table-body">
            <tr><td colspan="9" style="text-align:center; padding:24px;" class="hint">Đang tải danh sách thư mục vải và dữ liệu Drive...</td></tr>
          </tbody>
        </table>
      </div>

      <!-- Loading skeleton / toast -->
      <div id="audit-loading" class="hidden" style="margin-top:14px; text-align:center; padding:14px; background:#0b1728; border-radius:9px; border:1px dashed var(--line);">
        <span class="badge running">⏳ Đang kết nối Google Drive và đối chiếu dữ liệu thư mục... Vui lòng đợi trong giây lát</span>
      </div>
    </div>

    <!-- Container for detailed audit cards if run from custom url -->
    <div id="audit-results-container" style="display:flex; flex-direction:column; gap:14px; margin-top:14px;"></div>
  </section>
  <section id="tab-quality" class="tab-content hidden" aria-label="Kiểm thử chất lượng output vải">
    <div class="card">
      <h2>🔍 Kiểm thử chất lượng output vải</h2>
      <div class="quality-field">
        <label for="quality-website">Link trang web mục tiêu</label>
        <input id="quality-website" type="text" inputmode="url" value="https://dunniotailor.com/3d-custom-outfit/suits.html" aria-describedby="quality-website-hint" readonly>
        <div id="quality-website-hint" class="hint">Website Dunnio được tải qua proxy nội bộ để hiển thị và nhận ảnh ngay trong app.</div>
      </div>
      <div class="quality-field">
        <label>Folder vải</label>
        <div class="quality-toolbar">
          <button id="quality-folder-button" type="button" class="primary" onclick="selectQualityFolder()">📁 Chọn folder vải</button>
          <span class="hint">Chọn folder cha để hiện các folder con bên dưới. Bấm một folder để xem toàn bộ ảnh bên trong, kể cả các cấp con, theo thứ tự tên và số. Folder chỉ chứa ảnh sẽ hiển thị ảnh ngay.</span>
        </div>
        <div id="quality-folders" class="quality-folders" aria-label="Folder đã chọn"></div>
      </div>
    </div>
    <div class="quality-workspace">
      <div class="card">
        <div class="card-header-flex"><h2>Ảnh output mẫu vải</h2><button type="button" class="ghost btn-sm" onclick="openQualityRerun()">ChatGPT</button><span id="quality-count" class="badge" role="status">0 ảnh</span></div>
        <div id="quality-active-folder" class="hint" role="status"></div>
        <div class="quality-gallery" tabindex="0" aria-label="Danh sách ảnh mẫu vải có thể cuộn">
          <div id="quality-empty" class="quality-empty"><strong>Chưa có ảnh mẫu vải</strong><span>Chọn folder vải để xem các ảnh output tại đây.</span></div>
          <div id="quality-grid" class="quality-grid"></div>
        </div>
      </div>
      <div class="card quality-browser-card">
        <div class="card-header-flex"><h2>Trang kiểm thử Dunnio Tailor</h2><button type="button" class="ghost btn-sm" onclick="reloadQualityFrame()">Tải lại</button></div>
        <iframe id="quality-website-frame" class="quality-browser-frame" src="https://dunniotailor.com/3d/suits?key=123456789" title="Dunnio Tailor"></iframe>
      </div>
    </div>
    <div class="card">
      <div class="card-header-flex"><h2>Lỗi</h2><button type="button" class="ghost btn-sm" onclick="clearQualityErrors()">Xóa lỗi</button></div>
      <div id="quality-errors" class="quality-errors" role="log" aria-live="polite">Chưa có lỗi.</div>
    </div>
  </section>
</main>

<dialog id="quality-rerun-dialog" aria-labelledby="quality-rerun-title">
  <div class="quality-rerun-content">
    <h2 id="quality-rerun-title">Tạo lại ảnh fail bằng ChatGPT</h2>
    <div id="quality-rerun-folder" class="hint"></div>
    <div class="hint">Chạy lại crop, seamless và swatch cho các SKU đã chọn. Kết quả lưu vào output/chatgpt/&lt;nhóm&gt;/&lt;SKU&gt; trong thư mục dữ liệu app, thay thế kết quả cùng tên tại đó. Các ảnh cùng SKU chỉ chạy một lần.</div>
    <div><button id="quality-rerun-all" type="button" class="ghost btn-sm" onclick="toggleAllQualityRerun()">Chọn tất cả</button></div>
    <div id="quality-rerun-list"></div>
    <div id="quality-rerun-status" role="status"></div>
    <div class="quality-rerun-actions"><button id="quality-rerun-submit" type="button" class="primary" onclick="submitQualityRerun()">Tạo lại ảnh</button><button type="button" onclick="$('quality-rerun-dialog').close()">Thoát</button></div>
  </div>
</dialog>
<!-- Modal Popup Chi tiết SKU -->
<div id="sku-modal" class="modal-backdrop hidden" onclick="if(event.target===this)closeSkuModal()">
  <div class="modal">
    <div class="modal-header">
      <h3 id="modal-sku-title">Chi tiết SKU: -</h3>
      <button class="modal-close" onclick="closeSkuModal()">&times;</button>
    </div>
    <div class="modal-body">
      <div class="modal-previews">
        <!-- Preview 1: Cropped Texture -->
        <div class="preview-card">
          <div class="preview-card-title">
            <span>1. Cắt nguồn (Cropped)</span>
            <span id="modal-cropped-dim" class="badge ghost">--</span>
          </div>
          <div class="preview-img-box">
            <img id="modal-cropped-img" src="" alt="Cropped" onerror="this.style.display='none'">
            <div id="modal-cropped-empty" class="preview-placeholder hidden">Chưa có ảnh cắt</div>
          </div>
          <div class="preview-meta">
            <span>Dung lượng: <code id="modal-cropped-size">--</code></span>
          </div>
        </div>
        <!-- Preview 2: Seamless Texture 2K -->
        <div class="preview-card">
          <div class="preview-card-title preview-result-title">
            <span id="modal-output-dim" class="preview-result-status badge">Đang kiểm tra Seamless 2K...</span>
          </div>
          <div class="preview-img-box">
            <img id="modal-output-img" src="" alt="Seamless Output" onerror="this.style.display='none'; document.getElementById('modal-output-empty').classList.remove('hidden')">
            <div id="modal-output-empty" class="preview-placeholder hidden">Chưa tạo seamless</div>
          </div>
          <div class="preview-meta">
            <span>Dung lượng: <code id="modal-output-size">--</code></span>
            <span>Thời gian: <code id="modal-output-time">--</code></span>
          </div>
        </div>
        <!-- Preview 3: Fabric Swatch Studio 4:3 -->
        <div class="preview-card">
          <div class="preview-card-title preview-result-title">
            <span id="modal-fabric-dim" class="preview-result-status badge">Đang kiểm tra Swatch 4:3...</span>
          </div>
          <div class="preview-img-box">
            <img id="modal-fabric-img" src="" alt="Fabric Swatch" onerror="this.style.display='none'; document.getElementById('modal-fabric-empty').classList.remove('hidden')">
            <div id="modal-fabric-empty" class="preview-placeholder hidden">Chưa tạo swatch vải</div>
          </div>
          <div class="preview-meta">
            <span>Dung lượng: <code id="modal-fabric-size">--</code></span>
            <span>Thời gian: <code id="modal-fabric-time">--</code></span>
          </div>
        </div>
      </div>
      <div id="modal-notice" class="modal-notice hidden"></div>
      
      <div class="modal-info-table">
        <div class="label">Thư mục Drive:</div><div id="modal-folder" class="val" style="font-weight:600; color:var(--cyan);">-</div>
        <div class="label">Lần tạo gần nhất:</div><div id="modal-completed-at" class="val">-</div>
        <div class="label">Thời lượng tạo:</div><div id="modal-duration" class="val">-</div>
        <div class="label">Thư mục output:</div><div id="modal-output-path" class="val" style="font-family: Consolas, monospace; font-size: 11px;">-</div>
      </div>
    </div>
    <div class="modal-footer">
      <div class="modal-footer-left">
        <button id="modal-open-folder" class="ghost" type="button">📁 Mở thư mục</button>
        <button id="modal-view-full" class="ghost" type="button">🔗 Mở ảnh seamless</button>
        <button id="modal-view-fabric" class="ghost" type="button">🔗 Mở ảnh swatch</button>
      </div>
      <div class="modal-footer-right" style="display:flex; gap:6px;">
        <button id="modal-rerun-algo-sku" class="primary btn-sm" style="background:linear-gradient(135deg, #059669 0%, #047857 100%);" type="button">⚡ Thuật toán (CV)</button>
        <button id="modal-rerun-sku" class="ghost btn-sm" type="button">🤖 ChatGPT</button>
        <button id="modal-rerun-flow-sku" class="ghost btn-sm" style="color:var(--cyan); border-color:var(--cyan);" type="button">🌊 Flow</button>
        <button class="ghost btn-sm" type="button" onclick="closeSkuModal()">Đóng</button>
      </div>
    </div>
  </div>
</div>

<!-- Modal 1: Thêm / Sửa Link Google Drive -->
<div id="modal-edit-drive" class="modal-backdrop hidden" onclick="if(event.target===this)closeEditDriveModal()">
  <div class="modal" style="max-width: 540px;">
    <div class="modal-header">
      <h3 id="modal-edit-drive-title">🔗 Gán Link Google Drive Cho Thư Mục Vải</h3>
      <button class="modal-close" onclick="closeEditDriveModal()">&times;</button>
    </div>
    <div class="modal-body" style="display:flex; flex-direction:column; gap:12px;">
      <div>
        <label for="edit-drive-folder"><b>Thư mục vải (Mã vải)</b></label>
        <div style="display:flex; gap:8px; margin-top:4px;">
          <input id="edit-drive-folder" type="text" placeholder="VD: 1013 hoặc WDTC" style="flex:1;">
          <select id="edit-drive-folder-select" onchange="$('edit-drive-folder').value=this.value" style="background:#081321; border:1px solid var(--line); color:var(--text); border-radius:8px; padding:0 8px;">
            <option value="">-- Chọn thư mục --</option>
          </select>
        </div>
        <div class="hint">Tên thư mục lưu trữ ảnh kết quả (VD: <code>1013</code>, <code>WDTC</code>, <code>BNO</code>).</div>
      </div>
      <div>
        <label for="edit-drive-url"><b>Link Google Drive Folder</b></label>
        <input id="edit-drive-url" type="text" placeholder="https://drive.google.com/drive/folders/..." style="width:100%; margin-top:4px;">
        <div class="hint">Phải là link chia sẻ thư mục Google Drive (chế độ Bất kỳ ai có đường liên kết). Để trống để gỡ link.</div>
      </div>
    </div>
    <div class="modal-footer">
      <button class="ghost btn-sm" type="button" onclick="closeEditDriveModal()">Hủy</button>
      <button class="primary btn-sm" type="button" onclick="saveDriveLinkModal()">💾 Lưu liên kết</button>
    </div>
  </div>
</div>

<!-- Modal 2: Ghép Nối Link Drive Hàng Loạt -->
<div id="modal-bulk-drive" class="modal-backdrop hidden" onclick="if(event.target===this)closeBulkDriveModal()">
  <div class="modal" style="max-width: 640px;">
    <div class="modal-header">
      <h3>📋 Nhập Link Google Drive Hàng Loạt & Tự Động Ghép Nối</h3>
      <button class="modal-close" onclick="closeBulkDriveModal()">&times;</button>
    </div>
    <div class="modal-body" style="display:flex; flex-direction:column; gap:12px;">
      <div class="hint">Dán danh sách các link Google Drive (mỗi dòng 1 link). Hệ thống sẽ tự động quét tiêu đề trên Drive và ghép nối chuẩn xác vào các thư mục vải tương ứng trên máy (ví dụ: link có tên <code>WDTC</code> sẽ tự động gán vào thư mục vải <code>WDTC</code>).</div>
      <textarea id="bulk-drive-urls" rows="6" style="width:100%; background:#081321; border:1px solid var(--line); color:var(--text); border-radius:9px; padding:10px; font-family:monospace; font-size:12px; outline:none; resize:vertical;" placeholder="https://drive.google.com/drive/folders/1_0JYhVvNeI7nTMfVZJibUSvWc5Rm0JgxO&#10;https://drive.google.com/drive/folders/1TJId1ttIVJm2iqub1ghPNUkCVtvUcjfn"></textarea>
      
      <div id="bulk-match-loading" class="hidden" style="text-align:center; padding:12px; background:#0b1728; border-radius:8px; border:1px dashed var(--line);">
        <span class="badge running">⏳ Đang đọc tiêu đề Drive và tự động ghép nối...</span>
      </div>

      <div id="bulk-match-results" class="hidden" style="max-height:200px; overflow-y:auto; display:flex; flex-direction:column; gap:6px; font-size:12px;"></div>
    </div>
    <div class="modal-footer">
      <button class="ghost btn-sm" type="button" onclick="closeBulkDriveModal()">Đóng</button>
      <button class="primary btn-sm" id="btn-submit-bulk-match" type="button" onclick="submitBulkDriveMatch()">⚡ Quét & Tự Động Ghép Nối</button>
    </div>
  </div>
</div>

<!-- Modal 3: Chi Tiết Đối Chiếu SKU Thư Mục Vải -->
<div id="modal-folder-audit" class="modal-backdrop hidden" onclick="if(event.target===this)closeFolderSkuAuditModal()">
  <div class="modal" style="max-width: 780px;">
    <div class="modal-header">
      <div>
        <h3 id="mfa-title">Đối Chiếu SKU: -</h3>
        <div id="mfa-subtitle" class="hint" style="margin-top:2px;">-</div>
      </div>
      <button class="modal-close" onclick="closeFolderSkuAuditModal()">&times;</button>
    </div>
    <div class="modal-body" style="display:flex; flex-direction:column; gap:14px;">
      <!-- Progress Bar & Summary -->
      <div style="background:#0b1728; padding:12px 14px; border-radius:10px; border:1px solid var(--line);">
        <div style="display:flex; justify-content:space-between; font-weight:600; font-size:13px; margin-bottom:8px;">
          <span>Tiến độ tạo ảnh thành phẩm:</span>
          <span id="mfa-counts">0 / 0</span>
        </div>
        <div class="audit-progress-bar"><span id="mfa-progress-bar"></span></div>
      </div>

      <!-- Action Row & Filters -->
      <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
        <div style="display:flex; gap:6px;">
          <button id="mfa-filter-all" class="chip-filter-btn active" type="button" onclick="filterFolderAuditChips('all', this)">Tất cả (0)</button>
          <button id="mfa-filter-done" class="chip-filter-btn" type="button" onclick="filterFolderAuditChips('done', this)">✔ Đã tạo (0)</button>
          <button id="mfa-filter-missing" class="chip-filter-btn" type="button" onclick="filterFolderAuditChips('missing', this)">⚠️ Chưa tạo (0)</button>
        </div>
        <div style="display:flex; gap:8px;">
          <button id="mfa-copy-missing-btn" class="ghost btn-sm" type="button" onclick="copyCurrentFolderMissingSkus()">📋 Copy SKU còn thiếu</button>
          <button id="mfa-run-missing-btn" class="primary btn-sm" type="button" onclick="sendMissingSkusToPipeline()">▶ Nạp vào chạy tiếp</button>
          <button id="mfa-rescan-btn" class="ghost btn-sm" type="button" onclick="reScanCurrentFolderAudit()">🔄 Quét lại Drive</button>
        </div>
      </div>

      <!-- Loading skeleton -->
      <div id="mfa-loading" class="hidden" style="text-align:center; padding:24px; background:#0b1728; border-radius:8px; border:1px dashed var(--line);">
        <span class="badge running">⏳ Đang kết nối Google Drive và quét danh sách SKU...</span>
      </div>

      <!-- Chips Container -->
      <div id="mfa-chips-container" class="audit-chips-container" style="max-height:320px; background:#081321; border-radius:9px; border:1px solid var(--line); padding:10px;"></div>
    </div>
    <div class="modal-footer">
      <button class="ghost btn-sm" type="button" onclick="closeFolderSkuAuditModal()">Đóng</button>
    </div>
  </div>
</div>

<script>
const $ = id => document.getElementById(id);
let loaded = false;
let localLog = '';
let currentModalSku = null;
let currentModalFolder = null;
let lastCreatedList = [];
let lastPendingList = [];
let driveUrlsQueue = [];
let driveFoldersStats = [];
let currentSelectedFolder = 'all';
let driveFolderSearch = '';
let driveFolderPage = 1;
const DRIVE_FOLDERS_PER_PAGE = 4;

function fillDriveFolderSlots(container, usedSlots) {
  for (let index = usedSlots; index < DRIVE_FOLDERS_PER_PAGE; index += 1) {
    const placeholder = document.createElement('div');
    placeholder.className = 'drive-folder-item drive-folder-placeholder';
    placeholder.setAttribute('aria-hidden', 'true');
    container.appendChild(placeholder);
  }
}

function driveFolderId(url) {
  const match = String(url || '').match(/\/folders\/([^/?#]+)/i);
  return match ? match[1] : '';
}

function renderDriveFolders(statsList, queueList) {
  const container = $('drive-folders-list');
  container.replaceChildren();

  // Combine queue with stats
  const items = (statsList && statsList.length > 0) ? statsList : queueList.map(q => ({
    folder: q.folder || '',
    folder_display: q.folder || 'Mặc định',
    url: q.url || '',
    modified_at: q.modified_at || '',
    modified_ts: Date.parse(q.modified_at || '') || 0,
    total: 0,
    created_count: 0,
    pending_count: 0,
    percent: 0,
    is_active: false
  }));

  const sortedItems = [...items].sort((a, b) => {
    const aModified = Number(a.modified_ts || Date.parse(a.modified_at || '') || 0);
    const bModified = Number(b.modified_ts || Date.parse(b.modified_at || '') || 0);
    return bModified - aModified || String(a.folder_display || a.folder || '').localeCompare(String(b.folder_display || b.folder || ''), 'vi');
  });
  const query = driveFolderSearch.trim().toLocaleLowerCase('vi');
  const filteredItems = query
    ? sortedItems.filter(item => String(item.folder_display || item.folder || '').toLocaleLowerCase('vi').includes(query))
    : sortedItems;
  const totalPages = Math.max(1, Math.ceil(filteredItems.length / DRIVE_FOLDERS_PER_PAGE));
  driveFolderPage = Math.min(Math.max(1, driveFolderPage), totalPages);
  const pageStart = (driveFolderPage - 1) * DRIVE_FOLDERS_PER_PAGE;
  const pageItems = filteredItems.slice(pageStart, pageStart + DRIVE_FOLDERS_PER_PAGE);

  $('drive-folder-count').textContent = query
    ? `${filteredItems.length}/${items.length} thư mục`
    : `${items.length} thư mục`;
  $('drive-pagination').classList.toggle('hidden', filteredItems.length <= DRIVE_FOLDERS_PER_PAGE);
  $('drive-page-info').textContent = `Trang ${driveFolderPage}/${totalPages}`;
  $('drive-page-prev').disabled = driveFolderPage <= 1;
  $('drive-page-next').disabled = driveFolderPage >= totalPages;

  if (!items.length || !pageItems.length) {
    const empty = document.createElement('div');
    empty.className = 'hint';
    empty.style.padding = '12px';
    empty.style.textAlign = 'center';
    empty.textContent = items.length
      ? `Không tìm thấy thư mục khớp với “${driveFolderSearch.trim()}”.`
      : 'Chưa có link Google Drive nào. Hãy thêm link bên dưới.';
    container.appendChild(empty);
    fillDriveFolderSlots(container, 1);
    return;
  }

  pageItems.forEach((item) => {
    const card = document.createElement('div');
    card.className = 'drive-folder-item' + (item.is_active ? ' active' : '');

    const header = document.createElement('div');
    header.className = 'df-header';

    const titleBox = document.createElement('div');
    titleBox.className = 'df-title';
    titleBox.innerHTML = `📁 <span class="df-name">${escapeHtml(item.folder_display || item.folder || 'Mặc định')}</span>`;

    const badge = document.createElement('span');
    if (item.is_active) {
      badge.className = 'df-badge running';
      badge.textContent = '⚡ Đang xử lý...';
    } else if (item.total > 0 && item.created_count >= item.total) {
      badge.className = 'df-badge ok';
      badge.textContent = `✔ Đã xong ${item.created_count}/${item.total}`;
    } else if (item.created_count > 0) {
      badge.className = 'df-badge pending';
      badge.textContent = `● ${item.created_count}/${item.total} (${item.percent}%)`;
    } else {
      badge.className = 'df-badge';
      badge.textContent = item.total > 0 ? `Chưa chạy (${item.total} SKU)` : 'Chưa tải ảnh';
    }

    header.appendChild(titleBox);
    header.appendChild(badge);
    card.appendChild(header);

    if (item.url) {
      const urlLink = document.createElement('a');
      urlLink.className = 'df-url';
      urlLink.href = item.url;
      urlLink.target = '_blank';
      urlLink.title = item.url;
      urlLink.textContent = '🔗 ' + item.url;
      card.appendChild(urlLink);
    }

    const meta = document.createElement('div');
    meta.className = 'df-meta';
    let syncInfoHtml = '';
    if (item.drive_total !== undefined && item.drive_total !== null) {
      syncInfoHtml += `<span>📥 Drive: <b>${item.drive_total}</b></span>`;
    }
    if (item.last_sync_at) {
      const timeOnly = item.last_sync_at.split(' ')[1] || item.last_sync_at;
      syncInfoHtml += `<span title="Lần fetch gần nhất: ${escapeHtml(item.last_sync_at)}">🕒 Sync: <b>${escapeHtml(timeOnly)}</b></span>`;
    }
    meta.innerHTML = `
      <span>📦 Tổng SKU: <b>${item.total || 0}</b></span>
      ${syncInfoHtml}
      <span>✂ Đã crop: <b>${item.cropped_count || 0}</b></span>
      <span>🎨 Seamless: <b>${item.seamless_count || 0}</b></span>
      <span>👗 Swatch: <b>${item.fabric_count || 0}</b></span>
    `;
    card.appendChild(meta);

    const progWrap = document.createElement('div');
    progWrap.className = 'df-progress-bar';
    const progBar = document.createElement('span');
    progBar.style.width = (item.percent || 0) + '%';
    progWrap.appendChild(progBar);
    card.appendChild(progWrap);

    const actions = document.createElement('div');
    actions.className = 'df-actions';

    const runChatGptBtn = document.createElement('button');
    runChatGptBtn.type = 'button';
    runChatGptBtn.className = 'primary btn-sm';
    runChatGptBtn.innerHTML = '▶ Chạy ChatGPT';
    runChatGptBtn.onclick = () => runSingleFolder(item.folder, item.url, 'chatgpt');

    const runAlgoBtn = document.createElement('button');
    runAlgoBtn.type = 'button';
    runAlgoBtn.className = 'ghost btn-sm';
    runAlgoBtn.style.color = 'var(--green)';
    runAlgoBtn.style.borderColor = 'var(--green)';
    runAlgoBtn.innerHTML = '⚡ Thuật toán';
    runAlgoBtn.onclick = () => runSingleFolder(item.folder, item.url, 'algo');

    const runFlowBtn = document.createElement('button');
    runFlowBtn.type = 'button';
    runFlowBtn.className = 'ghost btn-sm';
    runFlowBtn.style.color = 'var(--cyan)';
    runFlowBtn.style.borderColor = 'var(--cyan)';
    runFlowBtn.innerHTML = '🌊 Chạy Flow';
    runFlowBtn.onclick = () => runSingleFolder(item.folder, item.url, 'flow');

    const openBtn = document.createElement('button');
    openBtn.type = 'button';
    openBtn.className = 'ghost btn-sm';
    openBtn.innerHTML = '📁 Mở output';
    openBtn.onclick = () => openFolderDirectory(item.folder);

    const viewBtn = document.createElement('button');
    viewBtn.type = 'button';
    viewBtn.className = 'ghost btn-sm';
    viewBtn.innerHTML = '🔍 Xem SKU';
    viewBtn.onclick = () => selectFolderFilter(item.folder);

    const delBtn = document.createElement('button');
    delBtn.type = 'button';
    delBtn.className = 'danger btn-sm';
    delBtn.innerHTML = '✕';
    delBtn.title = 'Xóa link Drive này';
    delBtn.onclick = () => removeDriveFolder(item.folder, item.url);

    actions.appendChild(runChatGptBtn);
    actions.appendChild(runAlgoBtn);
    actions.appendChild(runFlowBtn);
    actions.appendChild(openBtn);
    actions.appendChild(viewBtn);
    actions.appendChild(delBtn);
    card.appendChild(actions);

    container.appendChild(card);
  });
  fillDriveFolderSlots(container, pageItems.length);
}

$('drive-folder-search').addEventListener('input', event => {
  driveFolderSearch = event.target.value || '';
  driveFolderPage = 1;
  renderDriveFolders(driveFoldersStats, driveUrlsQueue);
});
$('drive-page-prev').onclick = () => {
  if (driveFolderPage > 1) driveFolderPage -= 1;
  renderDriveFolders(driveFoldersStats, driveUrlsQueue);
};
$('drive-page-next').onclick = () => {
  driveFolderPage += 1;
  renderDriveFolders(driveFoldersStats, driveUrlsQueue);
};

function escapeHtml(str) {
  return String(str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function updateFolderFilterSelect(statsList) {
  const select = $('folder-filter');
  const previousVal = select.value || currentSelectedFolder || 'all';
  select.replaceChildren();

  const allOpt = document.createElement('option');
  allOpt.value = 'all';
  allOpt.textContent = '📁 Tất cả thư mục';
  select.appendChild(allOpt);

  if (statsList && statsList.length) {
    statsList.forEach(item => {
      const opt = document.createElement('option');
      opt.value = item.folder || '';
      const display = item.folder_display || item.folder || 'Mặc định';
      opt.textContent = `📁 ${display} (${item.created_count || 0}/${item.total || 0} - ${item.percent || 0}%)`;
      select.appendChild(opt);
    });
  }

  // Restore previous selection if exists
  select.value = previousVal;
}

function selectFolderFilter(folderName) {
  const select = $('folder-filter');
  select.value = folderName || '';
  currentSelectedFolder = folderName || '';
  onFolderFilterChange();
  switchTab('progress');
  $('fabric-percent').scrollIntoView({ behavior: 'smooth' });
}

function onFolderFilterChange() {
  currentSelectedFolder = $('folder-filter').value;
  // Trigger immediate state fetch with folder filter
  fetchState();
}

async function runSingleFolder(folder, url, engine = 'chatgpt') {
  const label = engine === 'algo' ? 'Thuật toán CV' : engine === 'flow' ? 'Google Flow' : 'ChatGPT';
  if (!confirm(`Chạy pipeline ${label} cho riêng thư mục "${folder || 'Mặc định'}"?`)) return;
  try {
    const pl = payload();
    pl.folder = folder || '';
    pl.url = url || '';
    pl.engine = engine;
    if (engine === 'chatgpt' && !(await prepareSwatchPrerequisites(pl))) return;
    await api('/api/run-folder', pl);
    window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
  } catch (e) {
    toastError(e);
  }
}

async function openFolderDirectory(folder) {
  try {
    await api('/api/open-folder', { folder: folder || '' });
  } catch (e) {
    toastError(e);
  }
}

async function removeDriveFolder(folder, url) {
  const name = folder || 'Mặc định';
  if (!confirm(`Bạn có chắc muốn xóa thư mục/link Drive "${name}"?\n(Dữ liệu tải về và kết quả của thư mục này cũng sẽ được dọn dẹp).`)) return;
  try {
    await api('/api/delete-drive-folder', { folder: folder || '', url: url || '', delete_files: true });
    driveUrlsQueue = driveUrlsQueue.filter(item => !(item.folder === folder && item.url === url));
    await fetchState();
  } catch (e) {
    toastError(e);
  }
}

$('add-drive-btn').onclick = async () => {
  const urlInput = $('new-drive-url');
  const folderInput = $('new-drive-folder');
  const url = urlInput.value.trim();
  const folder = folderInput.value.trim();

  if (!url) {
    alert('Vui lòng nhập link Google Drive.');
    return;
  }
  if (!url.includes('drive.google.com') || !url.includes('/folders/')) {
    alert('Link Drive phải có định dạng https://drive.google.com/drive/folders/...');
    return;
  }

  // Check if duplicate
  const folderId = driveFolderId(url);
  const exists = driveUrlsQueue.some(i => driveFolderId(i.url) === folderId);
  if (exists) {
    alert('Folder ID này đã có trong danh sách. Không thể thêm bản ghi trùng.');
    return;
  }

  driveUrlsQueue.push({ url, folder, modified_at: new Date().toISOString() });
  driveFolderSearch = '';
  driveFolderPage = 1;
  $('drive-folder-search').value = '';
  urlInput.value = '';
  folderInput.value = '';

  try {
    await api('/api/save', payload());
    await fetchState();
  } catch (e) {
    toastError(e);
  }
};

function toastError(err) {
  const msg = err && err.message ? err.message : String(err || 'Đã xảy ra lỗi không xác định.');
  alert('⚠️ Lỗi: ' + msg);
}

async function api(path, body) {
  try {
    const r = await fetch(path, {
      method: body ? 'POST' : 'GET',
      headers: { 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined
    });
    let j = {};
    try {
      j = await r.json();
    } catch(err) {
      j = { error: `Máy chủ phản hồi mã ${r.status}` };
    }
    if (!r.ok) throw new Error(j.error || `Lỗi máy chủ (${r.status})`);
    return j;
  } catch (err) {
    if (err && err.name === 'TypeError' && String(err.message).toLowerCase().includes('fetch')) {
      throw new Error('Mất kết nối tới VEO3_AUTO_APP. Hãy đảm bảo ứng dụng đang mở.');
    }
    throw err;
  }
}

function sourceMode() { return $('source-local').checked ? 'local' : 'drive'; }
function texPromptMode() { return $('tex-prompt-mode-manual').checked ? 'manual' : 'attachment'; }
function fabPromptMode() { return $('fab-prompt-mode-manual').checked ? 'manual' : 'attachment'; }
function flowPromptMode() { return $('flow-prompt-mode-manual').checked ? 'manual' : 'attachment'; }

const qualityImages = new Map();
let qualityRerunFolder = '';
function openQualityRerun() {
  const folder = qualityFolderGroups[activeQualityFolderIndex];
  if (!folder) { addQualityError('Hãy chọn folder vải trước.'); return; }
  qualityRerunFolder = folder.path;
  $('quality-rerun-folder').textContent = folder.path;
  $('quality-rerun-list').replaceChildren();
  const failures = folder.images.filter(image => image.failed);
  for (const file of failures) {
    const row = document.createElement('label');
    row.className = 'quality-rerun-row';
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.value = file.path;
    checkbox.onchange = updateQualityRerunSelection;
    const img = document.createElement('img');
    img.src = '/api/quality-image?path=' + encodeURIComponent(file.path);
    img.alt = ''; img.loading = 'lazy';
    const name = document.createElement('span');
    name.textContent = file.relative_path || file.name;
    name.title = file.path;
    row.append(checkbox, img, name);
    $('quality-rerun-list').append(row);
  }
  $('quality-rerun-status').textContent = failures.length ? 'Chọn ảnh cần tạo lại.' : 'Folder này không có ảnh được đánh dấu fail.';
  updateQualityRerunSelection();
  $('quality-rerun-dialog').showModal();
}
function updateQualityRerunSelection() {
  const all = [...$('quality-rerun-list').querySelectorAll('input')];
  const count = all.filter(input => input.checked).length;
  $('quality-rerun-submit').disabled = count === 0;
  $('quality-rerun-all').disabled = all.length === 0;
  $('quality-rerun-all').textContent = count === all.length && count ? 'Bỏ chọn tất cả' : 'Chọn tất cả';
}
function toggleAllQualityRerun() {
  const all = [...$('quality-rerun-list').querySelectorAll('input')];
  const checked = !all.every(input => input.checked);
  all.forEach(input => input.checked = checked);
  updateQualityRerunSelection();
}
async function submitQualityRerun() {
  const inputs = [...$('quality-rerun-list').querySelectorAll('input')];
  const paths = inputs.filter(input => input.checked).map(input => input.value);
  if (!paths.length || $('quality-rerun-submit').disabled) return;
  $('quality-rerun-submit').disabled = true;
  $('quality-rerun-all').disabled = true;
  inputs.forEach(input => input.disabled = true);
  try {
    const result = await api('/api/quality-rerun', {folder: qualityRerunFolder, image_paths: paths});
    $('quality-rerun-dialog').close();
    $('quality-active-folder').textContent = `Đã bắt đầu tạo lại ${result.sku_count} SKU qua ChatGPT. Xem tiến trình trong nhật ký.`;
  } catch (error) {
    $('quality-rerun-status').textContent = error.message;
  } finally {
    inputs.forEach(input => input.disabled = false);
    updateQualityRerunSelection();
  }
}
const qualityErrors = [];
let qualityUploading = false;
let selectedQualityItem = null;
let qualityFolderGroups = [];
let activeQualityFolderIndex = -1;
function addQualityError(message) {
  qualityErrors.push(message);
  if (qualityErrors.length > 100) qualityErrors.shift();
  $('quality-errors').textContent = qualityErrors.join('\n');
  $('quality-errors').classList.add('has-errors');
}
function clearQualityErrors() {
  qualityErrors.length = 0;
  $('quality-errors').textContent = 'Chưa có lỗi.';
  $('quality-errors').classList.remove('has-errors');
}
function validateQualityWebsite() {
  const input = $('quality-website');
  const value = input.value.trim();
  try {
    if (value && !['http:', 'https:'].includes(new URL(value).protocol)) throw new Error();
    input.removeAttribute('aria-invalid');
    return true;
  } catch (_) {
    input.setAttribute('aria-invalid', 'true');
    addQualityError('Link trang web không hợp lệ. Vui lòng nhập địa chỉ bắt đầu bằng http:// hoặc https://.');
    return false;
  }
}
function updateQualitySummary() {
  $('quality-count').textContent = `${qualityImages.size} ảnh`;
  $('quality-empty').classList.toggle('hidden', qualityImages.size > 0);
  const folder = qualityFolderGroups[activeQualityFolderIndex];
  $('quality-empty').querySelector('strong').textContent = folder ? 'Folder này chưa có ảnh' : 'Chưa có ảnh mẫu vải';
  $('quality-empty').querySelector('span').textContent = folder ? `Không có file ảnh được hỗ trợ trong ${folder.name}. Chọn folder khác bên trên để xem ảnh.` : 'Chọn folder vải để xem các ảnh output tại đây.';
}
async function selectQualityFolder() {
  const button = $('quality-folder-button');
  button.disabled = true;
  try {
    const result = await api('/api/select-quality-folder', {});
    if (!result.path) return;
    qualityFolderGroups = result.folders || [];
    activeQualityFolderIndex = -1;
    renderQualityFolderButtons();
    if (qualityFolderGroups.length) showQualityFolder(0);
    else {
      clearQualityImages();
      addQualityError('Folder đã chọn không chứa file ảnh được hỗ trợ.');
    }
  } catch (error) {
    addQualityError(error.message);
  } finally {
    button.disabled = false;
  }
}
function clearQualityImages() {
  $('quality-active-folder').textContent = '';
  qualityImages.clear();
  selectedQualityItem = null;
  $('quality-grid').replaceChildren();
  updateQualitySummary();
}
function renderQualityFolderButtons() {
  const container = $('quality-folders');
  container.replaceChildren();
  qualityFolderGroups.forEach((folder, index) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'ghost btn-sm quality-folder';
    button.textContent = `📁 ${folder.name} (${folder.images.length})`;
    button.setAttribute('aria-pressed', 'false');
    button.title = folder.path;
    button.onclick = () => showQualityFolder(index);
    folder.button = button;
    container.append(button);
  });
}
function showQualityFolder(index) {
  if (index < 0 || index >= qualityFolderGroups.length) return;
  activeQualityFolderIndex = index;
  qualityFolderGroups.forEach((folder, position) => {
    folder.button?.classList.toggle('active', position === index);
    folder.button?.setAttribute('aria-pressed', String(position === index));
  });
  clearQualityImages();
  const folder = qualityFolderGroups[index];
  $('quality-active-folder').textContent = `Nhóm vải: ${folder.name}`;
  $('quality-grid').closest('.quality-gallery').scrollTop = 0;
  const images = folder.images || [];
  const fragment = document.createDocumentFragment();
  const renderedNodes = new Map();
  for (const file of images) {
    const path = file.relative_path || file.name;
    const key = file.path;
    if (qualityImages.has(key)) continue;
    try {
      const node = document.createElement('figure');
      node.className = 'quality-image';
      node.tabIndex = 0;
      node.role = 'button';
      node.title = 'Bấm để gửi ảnh này lên trang web mục tiêu';
      const img = document.createElement('img');
      img.alt = file.name;
      img.loading = 'lazy';
      img.decoding = 'async';
      img.onerror = () => {
        if (!qualityImages.has(key)) return;
        addQualityError(`Không thể hiển thị ảnh: ${path}. File có thể bị hỏng hoặc định dạng không được trình duyệt hỗ trợ.`);
      };
      img.src = '/api/quality-image?path=' + encodeURIComponent(file.path);
      const caption = document.createElement('figcaption');
      caption.textContent = path;
      node.append(img, caption);
      const item = {path: file.path, name: file.name, folder: folder.path, node};
      const failToggle = document.createElement('button');
      failToggle.type = 'button';
      failToggle.className = 'quality-fail-toggle';
      failToggle.setAttribute('role', 'checkbox');
      const renderFailure = () => {
        node.classList.toggle('failed', !!file.failed);
        failToggle.setAttribute('aria-checked', String(!!file.failed));
        failToggle.textContent = file.failed ? '×' : '';
        failToggle.title = file.failed ? 'Bỏ đánh dấu ảnh fail' : 'Đánh dấu ảnh fail';
        failToggle.setAttribute('aria-label', `${failToggle.title}: ${path}`);
      };
      renderFailure();
      failToggle.onkeydown = event => event.stopPropagation();
      failToggle.onclick = async event => {
        event.stopPropagation();
        failToggle.disabled = true;
        try {
          const result = await api('/api/quality-image-failure', {image_path: file.path, failed: !file.failed});
          file.failed = result.failed;
          // Also update a newly rendered copy if the user switched folders while saving.
          qualityFolderGroups.forEach(group => group.images.forEach(image => {
            if (image.path === file.path) image.failed = result.failed;
          }));
          renderFailure();
          qualityImages.get(file.path)?.renderFailure?.();
        } catch (error) {
          addQualityError(`Không lưu được đánh dấu fail: ${error.message}`);
        } finally {
          failToggle.disabled = false;
        }
      };
      item.renderFailure = renderFailure;
      node.append(failToggle);
      node.onclick = () => uploadQualityImage(item);
      node.onkeydown = event => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); uploadQualityImage(item); }
      };
      qualityImages.set(key, item);
      renderedNodes.set(key, node);
    } catch (error) {
      addQualityError(`Không thể đọc ảnh ${path}: ${error.message}`);
    }
  }
  const pairName = name => {
    const lower = (name || '').toLowerCase();
    return lower === 'seamless_texture.png' ? 'final' :
      lower === 'seamless_texture_chatgpt_raw.png' ? 'raw' : '';
  };
  const parentKey = file => (file.path || '').replace(/\\/g, '/').replace(/\/[^/]*$/, '').toLowerCase();
  const pairs = new Map();
  for (const file of images) {
    const kind = pairName(file.name);
    if (!kind) continue;
    const key = parentKey(file);
    const pair = pairs.get(key) || {};
    pair[kind] = file;
    pairs.set(key, pair);
  }
  const appended = new Set();
  for (const file of images) {
    if (appended.has(file.path) || !renderedNodes.has(file.path)) continue;
    const pair = pairs.get(parentKey(file));
    if (pair?.final && pair?.raw && pairName(file.name)) {
      const wrapper = document.createElement('div');
      wrapper.className = 'quality-pair';
      wrapper.append(renderedNodes.get(pair.final.path), renderedNodes.get(pair.raw.path));
      fragment.append(wrapper);
      appended.add(pair.final.path);
      appended.add(pair.raw.path);
    } else {
      fragment.append(renderedNodes.get(file.path));
      appended.add(file.path);
    }
  }
  $('quality-grid').append(fragment);
  updateQualitySummary();
}
async function uploadQualityImage(item) {
  if (qualityUploading) return;
  if (selectedQualityItem === item) return;
  clearQualityErrors();
  if (!validateQualityWebsite()) return;
  if (selectedQualityItem) selectedQualityItem.node.classList.remove('selected');
  selectedQualityItem = item;
  item.node.classList.add('selected');
  qualityUploading = true;
  item.node.classList.add('uploading');
  $('quality-count').textContent = 'Đang gửi ảnh...';
  try {
    const frame = $('quality-website-frame');
    const response = await fetch('/api/quality-image?path=' + encodeURIComponent(item.path), {cache: 'no-store'});
    if (!response.ok) throw new Error('Không đọc được file ảnh đã chọn.');
    const blob = await response.blob();
    const bytes = await blob.arrayBuffer();
    const requestId = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
    const targetOrigin = 'https://dunniotailor.com';
    await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        window.removeEventListener('message', receiveResult);
        reject(new Error('Dunnio Tailor không phản hồi yêu cầu nhận ảnh.'));
      }, 8000);
      function receiveResult(event) {
        if (event.origin !== targetOrigin || event.source !== frame.contentWindow) return;
        const message = event.data;
        if (message?.type !== 'DUNNIO_ATTACH_IMAGE_RESULT' || message.requestId !== requestId) return;
        clearTimeout(timeout);
        window.removeEventListener('message', receiveResult);
        message.success ? resolve(message) : reject(new Error(message.error || 'Dunnio Tailor không nhận được ảnh.'));
      }
      window.addEventListener('message', receiveResult);
      frame.contentWindow.postMessage({
        type: 'DUNNIO_ATTACH_IMAGE',
        requestId,
        payload: {
          name: item.name,
          mime: blob.type || 'application/octet-stream',
          bytes
        }
      }, targetOrigin, [bytes]);
    });
    $('quality-count').textContent = `Đã gửi ${item.name}`;
  } catch (error) {
    addQualityError(error.message);
    updateQualitySummary();
  } finally {
    qualityUploading = false;
    item.node.classList.remove('uploading');
  }
}
function reloadQualityFrame() {
  const frame = $('quality-website-frame');
  const url = new URL(frame.src); url.searchParams.set('reload', Date.now()); frame.src = url.href;
}

let activeTab = 'dashboard';
let activePromptSubTab = 'texture';

function switchTab(tabId) {
  if (tabId === 'logs') {
    openLogPopup();
    return;
  }
  activeTab = tabId;
  try { sessionStorage.setItem('veo3_active_tab', tabId); } catch(e) {}

  $('tab-dashboard').classList.toggle('hidden', tabId !== 'dashboard');
  $('tab-progress').classList.toggle('hidden', tabId !== 'progress');
  $('tab-audit').classList.toggle('hidden', tabId !== 'audit');
  $('tab-quality').classList.toggle('hidden', tabId !== 'quality');

  $('nav-tab-dashboard').classList.toggle('active', tabId === 'dashboard');
  $('nav-tab-progress').classList.toggle('active', tabId === 'progress');
  $('nav-tab-audit').classList.toggle('active', tabId === 'audit');
  $('nav-tab-quality').classList.toggle('active', tabId === 'quality');
}

function openLogPopup() {
  $('tab-logs').classList.remove('hidden');
  $('nav-tab-logs').classList.add('active');
  $('nav-tab-logs').setAttribute('aria-expanded', 'true');
  $('log').scrollTop = $('log').scrollHeight;
}

function closeLogPopup() {
  $('tab-logs').classList.add('hidden');
  $('nav-tab-logs').classList.remove('active');
  $('nav-tab-logs').setAttribute('aria-expanded', 'false');
}

function switchPromptSubTab(subTabId) {
  activePromptSubTab = subTabId;
  $('prompt-subtab-tex').classList.toggle('active', subTabId === 'texture');
  $('prompt-subtab-fab').classList.toggle('active', subTabId === 'fabric');
  $('prompt-subtab-flow').classList.toggle('active', subTabId === 'flow');

  $('prompt-panel-tex').classList.toggle('hidden', subTabId !== 'texture');
  $('prompt-panel-fab').classList.toggle('hidden', subTabId !== 'fabric');
  $('prompt-panel-flow').classList.toggle('hidden', subTabId !== 'flow');
}

function requestShutdownApp() {
  if (confirm('Đóng VEO3 Auto Pipeline?')) {
    api('/api/shutdown', {}).then(() => window.close()).catch(toastError);
  }
}

function payload() {
  const local = sourceMode() === 'local';
  return {
    source_mode: sourceMode(),
    local_source_dir: $('local-folder').value.trim(),
    drive_urls: driveUrlsQueue.filter(i => i.url),
    sku: $('sku').value.trim(),
    limit: $('limit').value.trim(),
    images_per_chat: Number($('perchat').value || 10),
    auto_retry_enabled: $('auto-retry').checked,
    auto_retry_delay_seconds: Number($('retry-delay').value || 120),
    auto_retry_max_attempts: Number($('retry-max').value || 10),
    dry_run: $('dry').checked,
    force: $('force').checked,
    seamless_engine: (!$('seamless-engine-chatgpt') || $('seamless-engine-chatgpt').checked) ? 'chatgpt' : 'algo',
    texture_prompt_mode: texPromptMode(),
    texture_prompt_file: $('tex-prompt-file').value.trim(),
    texture_prompt_text: $('tex-prompt-text').value,
    fabric_prompt_mode: fabPromptMode(),
    fabric_prompt_file: $('fab-prompt-file').value.trim(),
    fabric_prompt_text: $('fab-prompt-text').value,
    flow_prompt_mode: flowPromptMode(),
    flow_prompt_file: $('flow-prompt-file').value.trim(),
    flow_prompt_text: $('flow-prompt-text').value,
    telegram: {
      enabled: $('tele-enabled').checked,
      bot_token: $('tele-token').value.trim(),
      chat_id: $('tele-chat-id').value.trim(),
      notify_on_quota: $('tele-quota').checked,
      notify_on_complete: $('tele-complete').checked,
      notify_on_safe_stop: $('tele-safestop').checked,
      notify_on_folder_complete: $('tele-folder').checked
    },
    flows: {
      import: !local && $('flow-import').checked,
      crop: $('flow-crop').checked,
      seamless: $('flow-seamless').checked,
      fabric: $('flow-fabric').checked,
      package: $('flow-package').checked
    }
  };
}

async function prepareSwatchPrerequisites(pl) {
  if (!pl?.flows?.fabric || pl.flows.seamless) return true;
  const check = await api('/api/check-swatch-prerequisites', pl);
  if (check.local_fallback) pl.flows.import = false;
  if (!check.missing_count) return true;
  const preview = (check.missing_skus || []).slice(0, 8).join(', ');
  const more = check.missing_count > 8 ? ` và ${check.missing_count - 8} SKU khác` : '';
  const reason = `Có ${check.missing_count} SKU chưa có seamless_texture.png${preview ? `: ${preview}${more}` : ''}.`;
  const accepted = confirm(
    reason + '\n\n' +
    'Tự động tạo seamless cho các SKU còn thiếu, sau đó tạo swatch tương ứng?'
  );
  if (!accepted) return false;
  pl.flows.crop = true;
  pl.flows.seamless = true;
  pl.flows.package = true;
  pl.seamless_missing_only = true;
  pl.seamless_engine = 'chatgpt';
  return true;
}

function toastError(e) { alert(e.message || e); }

function updateSourceUI() {
  const local = sourceMode() === 'local';
  $('drive-source').classList.toggle('hidden', local);
  $('local-source').classList.toggle('hidden', !local);
  $('flow-import').disabled = local;
  if (local) $('flow-import').checked = false;
}

function updatePromptCounts() {
  const texLen = ($('tex-prompt-text').value || '').length;
  const fabLen = ($('fab-prompt-text').value || '').length;
  const flowLen = ($('flow-prompt-text').value || '').length;
  $('tex-prompt-count').textContent = texLen.toLocaleString() + ' ký tự';
  $('fab-prompt-count').textContent = fabLen.toLocaleString() + ' ký tự';
  $('flow-prompt-count').textContent = flowLen.toLocaleString() + ' ký tự';
}

function toggleTelegramInputs() {
  const enabled = $('tele-enabled').checked;
  const box = $('tele-config-box');
  if (box) {
    box.style.opacity = enabled ? '1' : '0.6';
  }
}

async function testTelegramConnection() {
  const btn = $('tele-test-btn');
  const msg = $('tele-status-msg');
  const token = $('tele-token').value.trim();
  const chatId = $('tele-chat-id').value.trim();
  if (!token || !chatId) {
    toastError('Vui lòng nhập Bot Token và Chat ID trước khi test.');
    return;
  }
  btn.disabled = true;
  btn.textContent = '⏳ Đang gửi...';
  msg.textContent = '';
  try {
    const res = await api('/api/test-telegram', { bot_token: token, chat_id: chatId });
    if (res.ok) {
      msg.innerHTML = '<span style="color:var(--green)">✅ ' + escapeHtml(res.message || 'Kết nối thành công!') + '</span>';
    } else {
      msg.innerHTML = '<span style="color:var(--red)">❌ ' + escapeHtml(res.error || 'Lỗi gửi tin') + '</span>';
    }
  } catch(e) {
    msg.innerHTML = '<span style="color:var(--red)">❌ ' + escapeHtml(e.message) + '</span>';
  } finally {
    btn.disabled = false;
    btn.textContent = '🧪 Gửi tin nhắn thử (Test)';
  }
}

async function saveTelegramSettings() {
  const p = payload();
  const msg = $('tele-status-msg');
  try {
    await api('/api/save', p);
    msg.innerHTML = '<span style="color:var(--green)">💾 Đã lưu cấu hình Telegram!</span>';
    setTimeout(() => { if (msg) msg.textContent = ''; }, 3500);
  } catch(e) {
    toastError('Lỗi lưu cấu hình Telegram: ' + e.message);
  }
}

function updatePromptUI() {
  const texManual = texPromptMode() === 'manual';
  $('tex-attachment-box').classList.toggle('hidden', texManual);
  $('tex-manual-box').classList.toggle('hidden', !texManual);
  $('tex-prompt-badge').textContent = texManual ? 'Nhập thủ công' : 'Đính kèm file';
  $('tex-prompt-badge').className = 'badge ' + (texManual ? 'pending' : 'ok');

  const fabManual = fabPromptMode() === 'manual';
  $('fab-attachment-box').classList.toggle('hidden', fabManual);
  $('fab-manual-box').classList.toggle('hidden', !fabManual);
  $('fab-prompt-badge').textContent = fabManual ? 'Nhập thủ công' : 'Đính kèm file';
  $('fab-prompt-badge').className = 'badge ' + (fabManual ? 'pending' : 'ok');

  const flowManual = flowPromptMode() === 'manual';
  $('flow-attachment-box').classList.toggle('hidden', flowManual);
  $('flow-manual-box').classList.toggle('hidden', !flowManual);
  $('flow-prompt-badge').textContent = flowManual ? 'Nhập thủ công' : 'Đính kèm file';
  $('flow-prompt-badge').className = 'badge ' + (flowManual ? 'pending' : 'ok');

  updatePromptCounts();
}

function formatSecToMin(sec) {
  if (!sec || isNaN(sec) || sec <= 0) return '0s';
  sec = Math.round(sec);
  if (sec < 60) return sec + 's';
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return m + 'm ' + (s > 0 ? (s < 10 ? '0' + s : s) + 's' : '');
}

function formatStopwatch(sec) {
  if (!sec || isNaN(sec) || sec < 0) return '00:00:00';
  sec = Math.floor(sec);
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  return (h < 10 ? '0' + h : h) + ':' + (m < 10 ? '0' + m : m) + ':' + (s < 10 ? '0' + s : s);
}

function renderSkuGrid(containerId, items, kind) {
  const box = $(containerId);
  box.replaceChildren();
  if (!items.length) {
    const empty = document.createElement('div');
    empty.className = 'hint';
    empty.style.padding = '20px';
    empty.style.gridColumn = '1 / -1';
    empty.style.textAlign = 'center';
    empty.textContent = 'Không có SKU nào';
    box.appendChild(empty);
    return;
  }
  for (const rawItem of items) {
    const item = typeof rawItem === 'string'
      ? { sku: rawItem, folder: '', has_seamless: kind === 'created', has_fabric: kind === 'created' }
      : rawItem;
    const sku = item.sku;
    const itemFolder = item.folder || (currentSelectedFolder !== 'all' ? currentSelectedFolder : '');
    const card = document.createElement('div');
    card.className = 'sku-card ' + kind;
    card.setAttribute('data-sku', sku);
    card.onclick = () => openSkuModal(sku, itemFolder || null);

    const wrap = document.createElement('div');
    wrap.className = 'sku-thumb-wrap';

    const img = document.createElement('img');
    img.className = 'sku-thumb';
    img.loading = 'lazy';
    img.alt = sku;
    const folderParam = itemFolder ? ('&folder=' + encodeURIComponent(itemFolder)) : '';
    if (item.has_seamless) {
      img.src = '/api/image?sku=' + encodeURIComponent(sku) + '&kind=seamless' + folderParam;
      img.onerror = () => { img.src = '/api/image?sku=' + encodeURIComponent(sku) + '&kind=cropped' + folderParam; };
    } else {
      img.src = '/api/image?sku=' + encodeURIComponent(sku) + '&kind=cropped' + folderParam;
      img.onerror = () => { img.src = '/api/image?sku=' + encodeURIComponent(sku) + '&kind=raw' + folderParam; };
    }

    const dot = document.createElement('span');
    dot.className = 'sku-dot ' + (kind === 'created' ? 'ok' : 'pending');

    wrap.appendChild(img);
    wrap.appendChild(dot);

    const name = document.createElement('div');
    name.className = 'sku-card-name';
    name.textContent = sku;
    name.title = sku;

    const statuses = document.createElement('div');
    statuses.className = 'sku-statuses';
    const seamlessStatus = document.createElement('span');
    seamlessStatus.className = 'sku-status-pill ' + (item.has_seamless ? 'ok' : 'missing');
    seamlessStatus.textContent = (item.has_seamless ? '✓ ' : '− ') + 'Seamless';
    const fabricStatus = document.createElement('span');
    fabricStatus.className = 'sku-status-pill ' + (item.has_fabric ? 'ok' : 'missing');
    fabricStatus.textContent = (item.has_fabric ? '✓ ' : '− ') + 'Swatch';
    statuses.appendChild(seamlessStatus);
    statuses.appendChild(fabricStatus);

    card.appendChild(wrap);
    card.appendChild(statuses);
    card.appendChild(name);
    box.appendChild(card);
  }
}

function filterSkuCards(kind) {
  const searchInput = kind === 'created' ? $('search-created') : $('search-pending');
  const term = (searchInput.value || '').trim().toLowerCase();
  const box = kind === 'created' ? $('created-list') : $('pending-list');
  const cards = box.querySelectorAll('.sku-card');
  for (const card of cards) {
    const sku = (card.getAttribute('data-sku') || '').toLowerCase();
    card.style.display = (!term || sku.includes(term)) ? 'flex' : 'none';
  }
}

function arraysEqual(a, b) {
  if (a === b) return true;
  if (!a || !b || a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (JSON.stringify(a[i]) !== JSON.stringify(b[i])) return false;
  }
  return true;
}

function renderFabricProgress(p, timing) {
  p = p || {};
  timing = timing || {};
  const percentText = (p.percent || 0) + '%';
  $('fabric-percent').textContent = percentText;
  const navBadge = $('nav-progress-badge');
  if (navBadge) navBadge.textContent = percentText;
  $('fabric-total').textContent = p.total || 0;
  $('fabric-created').textContent = p.created_count || 0;
  $('fabric-pending').textContent = p.pending_count || 0;
  $('created-count').textContent = p.created_count || 0;
  $('pending-count').textContent = p.pending_count || 0;
  $('fabric-progress-bar').style.width = (p.percent || 0) + '%';

  const newCreated = p.created || [];
  const newPending = p.pending || [];
  if (!arraysEqual(lastCreatedList, newCreated)) {
    lastCreatedList = newCreated;
    renderSkuGrid('created-list', newCreated, 'created');
    filterSkuCards('created');
  }
  if (!arraysEqual(lastPendingList, newPending)) {
    lastPendingList = newPending;
    renderSkuGrid('pending-list', newPending, 'pending');
    filterSkuCards('pending');
  }

  $('fabric-paths').textContent = 'Nguồn: ' + (p.source_dir || '-') + '  •  Kết quả: ' + (p.output_dir || '-') + (p.source_exists === false ? '  •  Thư mục nguồn không tồn tại' : '');

  // Timing metrics
  const running = Boolean(timing.session_running);
  const sessionSec = timing.session_duration_seconds || 0;
  $('session-time').textContent = formatStopwatch(sessionSec);
  const cardSession = $('card-session-time');
  if (running) {
    cardSession.classList.add('running-timer');
    $('session-status-sub').textContent = 'Đang chạy pipeline...';
  } else {
    cardSession.classList.remove('running-timer');
    $('session-status-sub').textContent = sessionSec > 0 ? 'Phiên vừa hoàn tất' : 'Sẵn sàng';
  }

  const createdInSession = timing.session_created_count || 0;
  $('session-count').textContent = createdInSession + ' ảnh';
  if (running && sessionSec > 0 && createdInSession > 0) {
    $('session-speed-sub').textContent = formatSecToMin(sessionSec / createdInSession) + ' / ảnh phiên này';
  } else {
    $('session-speed-sub').textContent = createdInSession > 0 ? (formatSecToMin(timing.session_avg_seconds) + ' / ảnh') : '-';
  }

  const avgSec = timing.effective_avg_seconds || timing.historical_avg_seconds || 0;
  $('avg-time').textContent = avgSec > 0 ? formatSecToMin(avgSec) : '--';
  $('avg-calc-sub').textContent = timing.session_avg_seconds > 0 && createdInSession >= 2 ? 'Trung bình theo phiên hiện tại' : 'Dựa trên lịch sử ChatGPT';

  const etaSec = timing.eta_seconds || 0;
  if (running && (p.pending_count || 0) > 0 && avgSec > 0) {
    $('eta-time').textContent = '~' + formatSecToMin(etaSec);
    $('eta-sub').textContent = 'Còn ' + (p.pending_count || 0) + ' SKU thiếu Seamless hoặc Swatch';
  } else if ((p.pending_count || 0) === 0) {
    $('eta-time').textContent = 'Đã hoàn thành';
    $('eta-sub').textContent = 'Tất cả SKU có đủ Seamless và Swatch';
  } else {
    $('eta-time').textContent = 'Chưa hoàn thành';
    $('eta-sub').textContent = (p.pending_count || 0) + ' SKU thiếu Seamless hoặc Swatch';
  }
}

// Modal logic
async function openSkuModal(sku, folder) {
  currentModalSku = sku;
  currentModalFolder = folder || (currentSelectedFolder !== 'all' ? currentSelectedFolder : null);
  $('modal-sku-title').textContent = 'Mã SKU: ' + sku;
  $('sku-modal').classList.remove('hidden');

  const folderParam = currentModalFolder ? ('&folder=' + encodeURIComponent(currentModalFolder)) : '';

  $('modal-cropped-img').style.display = 'block';
  $('modal-cropped-img').src = '/api/image?sku=' + encodeURIComponent(sku) + '&kind=cropped' + folderParam + '&t=' + Date.now();
  $('modal-cropped-empty').classList.add('hidden');

  $('modal-output-img').style.display = 'block';
  $('modal-output-img').src = '/api/image?sku=' + encodeURIComponent(sku) + '&kind=final_seamless' + folderParam + '&t=' + Date.now();
  $('modal-output-empty').classList.add('hidden');

  $('modal-fabric-img').style.display = 'block';
  $('modal-fabric-img').src = '/api/image?sku=' + encodeURIComponent(sku) + '&kind=fabric' + folderParam + '&t=' + Date.now();
  $('modal-fabric-empty').classList.add('hidden');

  $('modal-cropped-dim').textContent = '--';
  $('modal-cropped-size').textContent = '--';
  $('modal-output-dim').textContent = 'Đang kiểm tra Seamless 2K...';
  $('modal-output-dim').className = 'preview-result-status badge';
  $('modal-output-size').textContent = '--';
  $('modal-output-time').textContent = '--';
  $('modal-fabric-dim').textContent = 'Đang kiểm tra Swatch 4:3...';
  $('modal-fabric-dim').className = 'preview-result-status badge';
  $('modal-fabric-size').textContent = '--';
  $('modal-fabric-time').textContent = '--';
  $('modal-notice').textContent = '';
  $('modal-notice').classList.add('hidden');
  $('modal-folder').textContent = currentModalFolder || 'Mặc định / Tự phát hiện';
  $('modal-completed-at').textContent = '--';
  $('modal-duration').textContent = '--';
  $('modal-output-path').textContent = '--';

  try {
    const info = await api('/api/sku-info?sku=' + encodeURIComponent(sku) + folderParam);
    if (info.folder) {
      $('modal-folder').textContent = info.folder;
      currentModalFolder = info.folder;
    }
    if (info.cropped) {
      $('modal-cropped-dim').textContent = info.cropped.width ? (info.cropped.width + ' × ' + info.cropped.height + ' px') : 'Ảnh cắt';
      $('modal-cropped-size').textContent = info.cropped.size_formatted || '--';
    } else {
      $('modal-cropped-img').style.display = 'none';
      $('modal-cropped-empty').classList.remove('hidden');
    }

    if (info.output) {
      const outputSize = info.output.width ? ` (${info.output.width} × ${info.output.height} px)` : '';
      $('modal-output-dim').textContent = 'Đã tạo Seamless 2K' + outputSize;
      $('modal-output-dim').className = 'preview-result-status badge ok';
      $('modal-output-size').textContent = info.output.size_formatted || '--';
      $('modal-output-time').textContent = info.duration_text || '--';
      $('modal-output-path').textContent = info.output.path || '--';
    } else {
      $('modal-output-img').style.display = 'none';
      $('modal-output-empty').classList.remove('hidden');
      $('modal-output-dim').textContent = 'Chưa tạo Seamless 2K';
      $('modal-output-dim').className = 'preview-result-status badge pending';
    }

    if (info.fabric) {
      const fabricSize = info.fabric.width ? ` (${info.fabric.width} × ${info.fabric.height} px)` : '';
      $('modal-fabric-dim').textContent = 'Đã tạo swatch 4:3' + fabricSize;
      $('modal-fabric-dim').className = 'preview-result-status badge ok';
      $('modal-fabric-size').textContent = info.fabric.size_formatted || '--';
      $('modal-fabric-time').textContent = info.fabric_duration_text || '--';
    } else {
      $('modal-fabric-img').style.display = 'none';
      $('modal-fabric-empty').classList.remove('hidden');
      $('modal-fabric-dim').textContent = 'Chưa tạo swatch 4:3';
      $('modal-fabric-dim').className = 'preview-result-status badge pending';
    }

    if (info.quota_info) {
      $('modal-notice').textContent = '⏳ Chờ quota: ' + info.quota_info;
      $('modal-notice').classList.remove('hidden');
    }
    if (!info.output) {
      $('modal-output-path').textContent = (currentModalFolder ? ('output/chatgpt/' + currentModalFolder + '/') : 'output/chatgpt/') + sku + '/';
    }

    if (info.status_record) {
      $('modal-completed-at').textContent = info.status_record.completed_at || info.status_record.started_at || '--';
      $('modal-duration').textContent = info.duration_text || '--';
    }
  } catch (e) {
    $('modal-notice').textContent = 'Lỗi đọc chi tiết: ' + e.message;
    $('modal-notice').classList.remove('hidden');
  }
}

function closeSkuModal() {
  $('sku-modal').classList.add('hidden');
  currentModalSku = null;
}

$('modal-rerun-sku').onclick = async () => {
  if (!currentModalSku) return;
  const sku = currentModalSku;
  const folder = currentModalFolder || '';
  if (!confirm(`Chạy tạo lại ảnh qua ChatGPT cho riêng SKU: ${sku}${folder ? ' trong thư mục ' + folder : ''}?`)) return;
  try {
    const result = await api('/api/run-sku', { sku: sku, folder: folder, images_per_chat: 1, engine: 'chatgpt' });
    if (result.started === false) {
      alert(result.message || 'SKU đã có đủ Seamless và Swatch.');
      return;
    }
    closeSkuModal();
    window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
  } catch (e) {
    toastError(e);
  }
};

$('modal-rerun-algo-sku').onclick = async () => {
  if (!currentModalSku) return;
  const sku = currentModalSku;
  const folder = currentModalFolder || '';
  if (!confirm(`Tạo texture bằng Thuật toán Offline cho riêng SKU: ${sku}${folder ? ' trong thư mục ' + folder : ''}?`)) return;
  try {
    closeSkuModal();
    await api('/api/run-sku', { sku: sku, folder: folder, force: true, engine: 'algo' });
    window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
  } catch (e) {
    toastError(e);
  }
};

$('modal-rerun-flow-sku').onclick = async () => {
  if (!currentModalSku) return;
  const sku = currentModalSku;
  const folder = currentModalFolder || '';
  if (!confirm(`Chạy tạo lại ảnh qua Google Flow cho riêng SKU: ${sku}${folder ? ' trong thư mục ' + folder : ''}?`)) return;
  try {
    closeSkuModal();
    await api('/api/run-sku', { sku: sku, folder: folder, force: true, engine: 'flow' });
    window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
  } catch (e) {
    toastError(e);
  }
};

$('modal-open-folder').onclick = async () => {
  if (!currentModalSku) return;
  try {
    await api('/api/open-folder', { sku: currentModalSku, folder: currentModalFolder || '' });
  } catch (e) {
    toastError(e);
  }
};

$('modal-view-full').onclick = () => {
  if (!currentModalSku) return;
  const folderParam = currentModalFolder ? ('&folder=' + encodeURIComponent(currentModalFolder)) : '';
  window.open('/api/image?sku=' + encodeURIComponent(currentModalSku) + '&kind=final_seamless' + folderParam, '_blank');
};

$('modal-view-fabric').onclick = () => {
  if (!currentModalSku) return;
  const folderParam = currentModalFolder ? ('&folder=' + encodeURIComponent(currentModalFolder)) : '';
  window.open('/api/image?sku=' + encodeURIComponent(currentModalSku) + '&kind=fabric' + folderParam, '_blank');
};

document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && !$('sku-modal').classList.contains('hidden')) {
    closeSkuModal();
  }
});

$('source-drive').onchange = updateSourceUI;
$('source-local').onchange = updateSourceUI;
$('browse').onclick = async () => {
  try {
    const result = await api('/api/select-folder', { initial_dir: $('local-folder').value.trim() });
    if (result.path) {
      $('local-folder').value = result.path;
      $('source-local').checked = true;
      updateSourceUI();
    }
  } catch (e) { toastError(e); }
};

$('browse-tex-prompt').onclick = async () => {
  try {
    const result = await api('/api/select-prompt-file', { initial_path: $('tex-prompt-file').value.trim() });
    if (result.path) {
      $('tex-prompt-file').value = result.path;
      $('tex-prompt-mode-attach').checked = true;
      updatePromptUI();
    }
  } catch (e) { toastError(e); }
};

$('browse-fab-prompt').onclick = async () => {
  try {
    const result = await api('/api/select-prompt-file', { initial_path: $('fab-prompt-file').value.trim() });
    if (result.path) {
      $('fab-prompt-file').value = result.path;
      $('fab-prompt-mode-attach').checked = true;
      updatePromptUI();
    }
  } catch (e) { toastError(e); }
};

$('browse-flow-prompt').onclick = async () => {
  try {
    const result = await api('/api/select-prompt-file', { initial_path: $('flow-prompt-file').value.trim() });
    if (result.path) {
      $('flow-prompt-file').value = result.path;
      $('flow-prompt-mode-attach').checked = true;
      updatePromptUI();
    }
  } catch (e) { toastError(e); }
};

$('load-default-tex-prompt').onclick = async () => {
  try {
    const result = await api('/api/load-default-prompt?flow=texture');
    if (result.prompt_text) {
      $('tex-prompt-text').value = result.prompt_text;
      updatePromptCounts();
      alert('Đã tải nội dung từ file mẫu 01_TEXTURE_SEAMLESS_MASTER.md!');
    }
  } catch (e) { toastError(e); }
};

$('load-default-fab-prompt').onclick = async () => {
  try {
    const result = await api('/api/load-default-prompt?flow=fabric');
    if (result.prompt_text) {
      $('fab-prompt-text').value = result.prompt_text;
      updatePromptCounts();
      alert('Đã tải nội dung từ file mẫu 02_FABRIC_SWATCH_MASTER.md!');
    }
  } catch (e) { toastError(e); }
};

$('load-default-flow-prompt').onclick = async () => {
  try {
    const result = await api('/api/load-default-prompt?flow=flow_texture');
    if (result.prompt_text) {
      $('flow-prompt-text').value = result.prompt_text;
      updatePromptCounts();
      alert('Đã tải nội dung từ file mẫu scanned_to_texture_prompt.md!');
    }
  } catch (e) { toastError(e); }
};

$('save').onclick = () => api('/api/save', payload()).then(() => alert('Đã lưu cấu hình thành công!')).catch(toastError);
$('run').onclick = async () => {
  try {
    const pl = payload();
    pl.engine = 'chatgpt';
    if (!(await prepareSwatchPrerequisites(pl))) return;
    await api('/api/run', pl);
  } catch (error) {
    toastError(error);
  }
};
$('run-algo').onclick = () => {
  const pl = payload();
  pl.engine = 'algo';
  api('/api/run', pl).catch(toastError);
};
$('run-flow').onclick = () => {
  const pl = payload();
  pl.engine = 'flow';
  api('/api/run', pl).catch(toastError);
};
$('stop').onclick = () => {
  if (confirm('Dừng tiến trình hiện tại? Checkpoint vẫn được giữ.')) api('/api/stop', {}).catch(toastError);
};
$('chrome').onclick = () => api('/api/chrome', {}).catch(toastError);
$('check').onclick = () => api('/api/check-browser', {}).catch(toastError);
$('check-flow').onclick = () => api('/api/check-flow-browser', {}).catch(toastError);
async function fetchState() {
  try {
    const filterParam = '?folder=' + encodeURIComponent(currentSelectedFolder || 'all');
    const s = await api('/api/state' + filterParam);
    if (s.drive_urls && Array.isArray(s.drive_urls)) {
      driveUrlsQueue = s.drive_urls;
    } else if (s.drive_url) {
      driveUrlsQueue = [{ url: s.drive_url, folder: '' }];
    } else {
      driveUrlsQueue = [];
    }

    if (!loaded) {
      $('local-folder').value = s.local_source_dir || '';
      $('source-local').checked = s.source_mode === 'local';
      $('source-drive').checked = s.source_mode !== 'local';
      $('perchat').value = s.images_per_chat || 10;
      $('auto-retry').checked = s.auto_retry_enabled !== false;
      $('retry-delay').value = s.auto_retry_delay_seconds || 120;
      $('retry-max').value = s.auto_retry_max_attempts !== undefined ? s.auto_retry_max_attempts : 10;

      // Initialize prompt settings
      $('tex-prompt-mode-manual').checked = (s.texture_prompt_mode === 'manual');
      $('tex-prompt-mode-attach').checked = (s.texture_prompt_mode !== 'manual');
      $('tex-prompt-file').value = s.texture_prompt_file || 'prompts/prompts_attachments/01_TEXTURE_SEAMLESS_MASTER.md';
      $('tex-prompt-text').value = s.texture_prompt_text || '';

      $('fab-prompt-mode-manual').checked = (s.fabric_prompt_mode === 'manual');
      $('fab-prompt-mode-attach').checked = (s.fabric_prompt_mode !== 'manual');
      $('fab-prompt-file').value = s.fabric_prompt_file || 'prompts/prompts_attachments/02_FABRIC_SWATCH_MASTER.md';
      $('fab-prompt-text').value = s.fabric_prompt_text || '';

      $('flow-prompt-mode-manual').checked = (s.flow_prompt_mode === 'manual');
      $('flow-prompt-mode-attach').checked = (s.flow_prompt_mode !== 'manual');
      $('flow-prompt-file').value = s.flow_prompt_file || 'prompts/scanned_to_texture_prompt.md';
      $('flow-prompt-text').value = s.flow_prompt_text || '';

      if (s.telegram) {
        $('tele-enabled').checked = Boolean(s.telegram.enabled);
        $('tele-token').value = s.telegram.bot_token || '';
        $('tele-chat-id').value = s.telegram.chat_id || '';
        $('tele-quota').checked = (s.telegram.notify_on_quota !== false);
        $('tele-complete').checked = (s.telegram.notify_on_complete !== false);
        $('tele-safestop').checked = (s.telegram.notify_on_safe_stop !== false);
        $('tele-folder').checked = (s.telegram.notify_on_folder_complete !== false);
        toggleTelegramInputs();
      }

      updateSourceUI();
      updatePromptUI();

      // Restore active tab from previous session
      try {
        const savedTab = sessionStorage.getItem('veo3_active_tab');
        if (savedTab && ['dashboard', 'progress', 'logs', 'audit', 'quality'].includes(savedTab)) {
          switchTab(savedTab);
        }
      } catch(e) {}

      loaded = true;
    }

    driveFoldersStats = (s.drive_folders_stats && s.drive_folders_stats.folders) || [];
    renderDriveFolders(driveFoldersStats, driveUrlsQueue);
    renderDriveManagementSection(s.drive_folders_stats);
    updateFolderFilterSelect(driveFoldersStats);
    if (s.output_subdirectories) {
      updateAuditTargetChildOptions(s.output_subdirectories);
    }

    $('project').textContent = s.project_dir;
    $('python').textContent = s.python_exe || 'Không tìm thấy Python';
    $('status').textContent = s.status;
    $('status').className = 'badge' + (s.running ? ' running' : '');
    $('run').disabled = s.running;
    if ($('run-algo')) $('run-algo').disabled = s.running;
    if ($('run-flow')) $('run-flow').disabled = s.running;
    $('stop').disabled = !s.running;
    $('chrome').disabled = s.running;
    $('check').disabled = s.running;
    $('save').disabled = s.running;
    $('browse').disabled = s.running;
    $('progress').className = 'progress' + (s.running ? ' running' : '');

    renderFabricProgress(s.fabric_progress, s.timing_stats);

    if (s.quota_alert) {
      $('quota-banner').classList.remove('hidden');
      $('quota-banner-text').textContent = s.quota_alert.message || s.quota_alert;
    } else {
      $('quota-banner').classList.add('hidden');
    }

    if (s.log !== localLog) {
      localLog = s.log;
      $('log').textContent = localLog;
      $('log').scrollTop = $('log').scrollHeight;
    }
  } catch (e) {
    $('status').textContent = 'Mất kết nối';
    $('status').className = 'badge error';
  }
}

async function dismissQuotaBanner() {
  $('quota-banner').classList.add('hidden');
  try {
    await api('/api/dismiss-quota-alert', {});
  } catch(e) {}
}

let auditResultsData = [];
let auditSubdirsLoaded = false;

function updateAuditTargetChildOptions(subdirs) {
  if (auditSubdirsLoaded || !Array.isArray(subdirs)) return;
  const sel = $('audit-target-child');
  if (!sel) return;
  const cur = sel.value;
  sel.replaceChildren();

  const allOpt = document.createElement('option');
  allOpt.value = 'all';
  allOpt.textContent = 'Tất cả thư mục (' + subdirs.join(', ') + ')';
  sel.appendChild(allOpt);

  subdirs.forEach(d => {
    const opt = document.createElement('option');
    opt.value = d;
    opt.textContent = 'output/' + d;
    if (d === 'chatgpt') opt.selected = true;
    sel.appendChild(opt);
  });
  if (cur && Array.from(sel.options).some(o => o.value === cur)) {
    sel.value = cur;
  }
  auditSubdirsLoaded = true;
}

async function runDriveAudit() {
  const urlsText = ($('audit-urls').value || '').trim();
  if (!urlsText) {
    alert('Vui lòng nhập ít nhất một link Google Drive vào ô văn bản.');
    $('audit-urls').focus();
    return;
  }
  const urls = urlsText.split(/[\r\n,]+/).map(u => u.trim()).filter(u => u.length > 0);
  if (!urls.length) {
    alert('Không tìm thấy link Google Drive hợp lệ.');
    return;
  }
  const targetChild = $('audit-target-child').value;

  $('btn-run-audit').disabled = true;
  $('audit-loading').classList.remove('hidden');
  $('audit-results-container').replaceChildren();

  try {
    const res = await api('/api/audit-drive-folders', {
      urls: urls,
      target_child: targetChild
    });
    if (res && res.results) {
      auditResultsData = res.results;
      renderAuditResults(res.results);
    } else {
      alert('Không nhận được dữ liệu kiểm tra từ máy chủ.');
    }
  } catch (err) {
    alert('Lỗi kiểm tra Drive: ' + (err.message || err));
  } finally {
    $('btn-run-audit').disabled = false;
    $('audit-loading').classList.add('hidden');
  }
}

function renderAuditResults(results) {
  const container = $('audit-results-container');
  container.replaceChildren();

  if (!results || !results.length) {
    const empty = document.createElement('div');
    empty.className = 'hint';
    empty.style.textAlign = 'center';
    empty.style.padding = '20px';
    empty.textContent = 'Không có kết quả kiểm tra nào.';
    container.appendChild(empty);
    return;
  }

  results.forEach((item, rIdx) => {
    const card = document.createElement('div');
    card.className = 'audit-card';

    // Header
    const header = document.createElement('div');
    header.className = 'audit-header';

    const titleBox = document.createElement('div');
    titleBox.className = 'audit-title-box';

    const title = document.createElement('div');
    title.className = 'audit-title';
    title.innerHTML = `📁 <span>${escapeHtml(item.title || 'Thư mục Drive')}</span>`;

    const subtitle = document.createElement('div');
    subtitle.className = 'audit-subtitle';
    subtitle.innerHTML = `
      <span>Mã chính: <strong style="color:var(--cyan); font-family:monospace;">${escapeHtml(item.base_code || '--')}</strong></span>
      <span>|</span>
      <span>📦 Drive: <strong>${item.drive_total || 0} ảnh</strong></span>
      <span>|</span>
      <a href="${item.url}" target="_blank" style="color:var(--cyan); text-decoration:none;">🔗 Mở link Drive</a>
    `;

    titleBox.appendChild(title);
    titleBox.appendChild(subtitle);
    header.appendChild(titleBox);
    card.appendChild(header);

    // Error case
    if (item.error) {
      const errBox = document.createElement('div');
      errBox.style.padding = '10px 14px';
      errBox.style.borderRadius = '8px';
      errBox.style.border = '1px solid #9f1239';
      errBox.style.background = 'rgba(159,18,57,.15)';
      errBox.style.color = '#fda4af';
      errBox.style.fontSize = '13px';
      errBox.innerHTML = `⚠️ <b>Lỗi:</b> ${escapeHtml(item.error)}`;
      card.appendChild(errBox);
      container.appendChild(card);
      return;
    }

    // Checks per child directory
    (item.checks || []).forEach((chk, cIdx) => {
      const chkBox = document.createElement('div');
      chkBox.className = 'audit-check-box';

      const chkHeader = document.createElement('div');
      chkHeader.className = 'audit-check-header';

      const pathInfo = document.createElement('div');
      pathInfo.style.display = 'flex';
      pathInfo.style.alignItems = 'center';
      pathInfo.style.gap = '8px';

      const statusBadge = document.createElement('span');
      if (chk.folder_exists) {
        statusBadge.className = 'badge ok';
        statusBadge.textContent = '✔ Đã có thư mục';
      } else {
        statusBadge.className = 'badge pending';
        statusBadge.textContent = '⚠️ Chưa có thư mục';
      }

      pathInfo.appendChild(statusBadge);
      const pathText = document.createElement('span');
      pathText.innerHTML = `Thư mục đối chiếu: <code style="color:var(--cyan); font-weight:bold;">${escapeHtml(chk.folder_path)}</code>`;
      pathInfo.appendChild(pathText);

      const countInfo = document.createElement('div');
      countInfo.innerHTML = `Đã tạo: <b style="color:var(--green);">${chk.created_count}/${chk.total}</b> (${chk.percent}%) &nbsp;|&nbsp; Còn thiếu: <b style="color:var(--red);">${chk.missing_count}</b>`;

      chkHeader.appendChild(pathInfo);
      chkHeader.appendChild(countInfo);
      chkBox.appendChild(chkHeader);

      // Progress Bar
      const progWrap = document.createElement('div');
      progWrap.className = 'audit-progress-bar';
      const progSpan = document.createElement('span');
      progSpan.style.width = chk.percent + '%';
      progWrap.appendChild(progSpan);
      chkBox.appendChild(progWrap);

      // Actions row
      const actionsRow = document.createElement('div');
      actionsRow.className = 'audit-actions';

      const missingSkus = (chk.skus || []).filter(s => s.status !== 'done').map(s => s.sku);

      if (missingSkus.length > 0) {
        const copyBtn = document.createElement('button');
        copyBtn.type = 'button';
        copyBtn.className = 'ghost btn-sm';
        copyBtn.innerHTML = `📋 Copy ${missingSkus.length} SKU chưa tạo`;
        copyBtn.onclick = () => copyMissingSkus(missingSkus.join(', '));
        actionsRow.appendChild(copyBtn);
      }

      const addQueueBtn = document.createElement('button');
      addQueueBtn.type = 'button';
      addQueueBtn.className = 'primary btn-sm';
      addQueueBtn.innerHTML = '➕ Thêm vào hàng đợi Pipeline';
      addQueueBtn.onclick = () => addAuditedFolderToPipeline(item.url, chk.matched_folder_name || item.base_code);
      actionsRow.appendChild(addQueueBtn);

      // Filter buttons for SKU chips
      const filterWrap = document.createElement('div');
      filterWrap.style.marginLeft = 'auto';
      filterWrap.style.display = 'flex';
      filterWrap.style.gap = '4px';

      const chipsId = `audit-chips-${rIdx}-${cIdx}`;

      const fAll = document.createElement('button');
      fAll.className = 'chip-filter-btn active';
      fAll.textContent = `Tất cả (${chk.total})`;
      fAll.onclick = () => filterAuditChips(chipsId, 'all', fAll);

      const fDone = document.createElement('button');
      fDone.className = 'chip-filter-btn';
      fDone.textContent = `Đã tạo (${chk.created_count})`;
      fDone.onclick = () => filterAuditChips(chipsId, 'done', fDone);

      const fMissing = document.createElement('button');
      fMissing.className = 'chip-filter-btn';
      fMissing.textContent = `Chưa tạo (${chk.missing_count})`;
      fMissing.onclick = () => filterAuditChips(chipsId, 'missing', fMissing);

      filterWrap.appendChild(fAll);
      filterWrap.appendChild(fDone);
      filterWrap.appendChild(fMissing);
      actionsRow.appendChild(filterWrap);

      chkBox.appendChild(actionsRow);

      // Chips Container
      const chipsBox = document.createElement('div');
      chipsBox.id = chipsId;
      chipsBox.className = 'audit-chips-container';

      (chk.skus || []).forEach(s => {
        const chip = document.createElement('span');
        chip.className = `audit-chip ${s.status}`;
        chip.dataset.status = s.status;
        const icon = s.status === 'done' ? '✔' : '⏳';
        const tip = s.status === 'done'
          ? `Seamless: ${s.has_seamless ? 'Có' : 'Không'} | Swatch: ${s.has_fabric ? 'Có' : 'Không'}`
          : 'Chưa tạo thành phẩm';
        chip.title = tip;
        chip.textContent = `${s.sku} ${icon}`;
        chipsBox.appendChild(chip);
      });

      chkBox.appendChild(chipsBox);
      card.appendChild(chkBox);
    });

    container.appendChild(card);
  });
}

function filterAuditChips(containerId, filter, btn) {
  const container = $(containerId);
  if (!container) return;
  const parent = btn.parentElement;
  if (parent) {
    Array.from(parent.children).forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
  }
  const chips = container.querySelectorAll('.audit-chip');
  chips.forEach(c => {
    if (filter === 'all') {
      c.style.display = 'inline-flex';
    } else {
      c.style.display = (c.dataset.status === filter) ? 'inline-flex' : 'none';
    }
  });
}

function copyMissingSkus(skuListStr) {
  if (!skuListStr) {
    alert('Không có SKU nào chưa tạo.');
    return;
  }
  navigator.clipboard.writeText(skuListStr).then(() => {
    alert(`Đã sao chép ${skuListStr.split(',').length} SKU vào clipboard!`);
  }).catch(() => {
    prompt('Sao chép danh sách SKU:', skuListStr);
  });
}

async function addAuditedFolderToPipeline(url, folder) {
  folder = (folder || '').trim();
  url = (url || '').trim();
  if (!url) return;
  const folderId = driveFolderId(url);
  const exists = driveUrlsQueue.some(item => driveFolderId(item.url) === folderId);
  if (!exists) {
    driveUrlsQueue.push({ url: url, folder: folder, modified_at: new Date().toISOString() });
    driveFolderSearch = '';
    driveFolderPage = 1;
    $('drive-folder-search').value = '';
    renderDriveFolders(driveFoldersStats, driveUrlsQueue);
    try {
      await api('/api/save', payload());
      await fetchState();
    } catch(e) {
      toastError(e);
    }
  }
  switchTab('dashboard');
  const target = $('drive-folders-list');
  if (target) {
    target.scrollIntoView({ behavior: 'smooth' });
  }
}

let currentAuditFolderData = null;
let currentDriveTableFolders = [];

function renderDriveManagementSection(statsData) {
  if (!statsData) return;
  const folders = statsData.folders || [];
  currentDriveTableFolders = folders;

  // 1. Update badges & metrics
  if ($('dt-total-folders')) $('dt-total-folders').textContent = statsData.total_folders || 0;
  if ($('dt-linked-folders')) $('dt-linked-folders').textContent = statsData.linked_folders || 0;
  if ($('dt-unlinked-folders')) $('dt-unlinked-folders').textContent = statsData.unlinked_folders || 0;
  if ($('dt-total-skus')) $('dt-total-skus').textContent = statsData.total_skus || 0;
  if ($('dt-total-created')) $('dt-total-created').textContent = statsData.total_created || 0;
  if ($('dt-total-pending')) $('dt-total-pending').textContent = statsData.total_pending || 0;
  if ($('dt-overall-percent')) $('dt-overall-percent').textContent = (statsData.overall_percent || 0) + '%';
  if ($('dt-progress-bar-span')) {
    $('dt-progress-bar-span').style.width = (statsData.overall_percent || 0) + '%';
  }
  if ($('nav-audit-badge')) {
    $('nav-audit-badge').textContent = `${statsData.linked_folders || 0}/${statsData.total_folders || 0}`;
  }

  // 2. Populate folder select in modal
  const sel = $('edit-drive-folder-select');
  if (sel) {
    const curVal = sel.value;
    sel.replaceChildren();
    const defOpt = document.createElement('option');
    defOpt.value = '';
    defOpt.textContent = '-- Chọn thư mục có sẵn --';
    sel.appendChild(defOpt);
    folders.forEach(f => {
      const opt = document.createElement('option');
      opt.value = f.folder;
      opt.textContent = `${f.folder} (${f.total} SKU${f.url ? ' - Đã có link' : ''})`;
      sel.appendChild(opt);
    });
    if (curVal) sel.value = curVal;
  }

  // 3. Render Table rows
  filterDriveTable();
}

function filterDriveTable() {
  const tbody = $('drive-table-body');
  if (!tbody) return;
  tbody.replaceChildren();

  const query = ($('dt-search') ? $('dt-search').value : '').trim().toLowerCase();
  const filter = ($('dt-status-filter') ? $('dt-status-filter').value : 'all');

  const filtered = currentDriveTableFolders.filter(f => {
    if (query && !f.folder.toLowerCase().includes(query) && !(f.url || '').toLowerCase().includes(query)) {
      return false;
    }
    if (filter === 'completed') return f.status === 'completed';
    if (filter === 'in_progress') return f.status === 'in_progress';
    if (filter === 'pending') return f.pending_count > 0;
    if (filter === 'linked') return !!f.has_drive_url;
    if (filter === 'unlinked') return !f.has_drive_url;
    return true;
  });

  if (!filtered.length) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td colspan="9" style="text-align:center; padding:32px;" class="hint">Không tìm thấy thư mục vải nào phù hợp với bộ lọc.</td>`;
    tbody.appendChild(tr);
    return;
  }

  filtered.forEach(item => {
    const tr = document.createElement('tr');

    // 1. Folder Name + Status Badge
    const tdFolder = document.createElement('td');
    let badgeHtml = '';
    if (item.status === 'completed') {
      badgeHtml = `<span class="badge ok dt-status-badge">✔ Xong 100%</span>`;
    } else if (item.status === 'in_progress') {
      badgeHtml = `<span class="badge dt-status-badge" style="border-color:var(--cyan); color:var(--cyan);">⚡ Đang làm (${item.percent}%)</span>`;
    } else if (!item.has_drive_url) {
      badgeHtml = `<span class="badge-unlinked dt-status-badge">⚠️ Chưa có Drive</span>`;
    } else {
      badgeHtml = `<span class="badge pending dt-status-badge">⏳ Chưa tạo</span>`;
    }
    tdFolder.innerHTML = `
      <div class="dt-folder-stack">
        <div class="dt-folder-head">
          <b class="dt-folder-name" title="Bấm để lọc SKU thư mục này" onclick="selectFolderFilter('${escapeHtml(item.folder)}')">📁 ${escapeHtml(item.folder)}</b>
        </div>
        <span class="hint" style="font-size:11px;">${item.total || 0} SKU trên máy</span>
      </div>
    `;
    tr.appendChild(tdFolder);

    // 2. Folder Status
    const tdStatus = document.createElement('td');
    tdStatus.innerHTML = badgeHtml;
    tr.appendChild(tdStatus);

    // 3. Google Drive Link
    const tdUrl = document.createElement('td');
    if (item.url) {
      tdUrl.innerHTML = `
        <div class="drive-link-box">
          <a href="${escapeHtml(item.url)}" target="_blank" title="${escapeHtml(item.url)}">🔗 ${escapeHtml(item.url)}</a>
          <button type="button" class="ghost btn-icon" title="Sửa link Drive" onclick="openEditDriveModal('${escapeHtml(item.folder)}', '${escapeHtml(item.url)}')">✏️</button>
        </div>
      `;
    } else {
      tdUrl.innerHTML = `
        <button type="button" class="ghost btn-sm" style="border-color:var(--cyan); color:var(--cyan); font-size:11px;" onclick="openEditDriveModal('${escapeHtml(item.folder)}', '')">+ Gán link Drive</button>
      `;
    }
    tr.appendChild(tdUrl);

    // 4. Drive Images Count
    const tdDrive = document.createElement('td');
    if (item.drive_total !== undefined && item.drive_total !== null) {
      const timeStr = item.last_sync_at ? `<div class="metric-sub">🕒 ${escapeHtml(item.last_sync_at.split(' ')[1] || item.last_sync_at)}</div>` : '';
      tdDrive.innerHTML = `<b>📥 ${item.drive_total}</b> ảnh${timeStr}`;
    } else if (item.url) {
      tdDrive.innerHTML = `
        <span class="hint" style="font-size:12px;">Chưa quét</span>
        <button type="button" class="ghost btn-icon" title="Quét Drive ngay" onclick="openFolderSkuAudit('${escapeHtml(item.folder)}', '${escapeHtml(item.url)}')">🔍</button>
      `;
    } else {
      tdDrive.innerHTML = `<span class="hint">-</span>`;
    }
    tr.appendChild(tdDrive);

    // 5. Raw & Cropped
    const tdRaw = document.createElement('td');
    tdRaw.innerHTML = `
      <div>✂ Crop: <b>${item.cropped_count || 0}</b></div>
      <div class="metric-sub">Raw: <b>${item.raw_count || 0}</b></div>
    `;
    tr.appendChild(tdRaw);

    // 6. Created Artifacts
    const tdCreated = document.createElement('td');
    tdCreated.innerHTML = `
      <div>🎨 Seamless: <b class="ok">${item.seamless_count || 0}</b></div>
      <div class="metric-sub">👗 Swatch: <b class="ok">${item.fabric_count || 0}</b></div>
    `;
    tr.appendChild(tdCreated);

    // 7. Progress
    const tdProg = document.createElement('td');
    tdProg.innerHTML = `
      <div style="display:flex; align-items:center;">
        <div class="dt-progress-bar"><span style="width:${item.percent || 0}%;"></span></div>
        <b>${item.percent || 0}%</b>
      </div>
      <div class="metric-sub">${item.created_count || 0} / ${item.total || 0} SKU</div>
    `;
    tr.appendChild(tdProg);

    // 8. Missing
    const tdMissing = document.createElement('td');
    if (item.pending_count > 0) {
      tdMissing.innerHTML = `<b class="amber" style="font-size:13px;">Thiếu ${item.pending_count} SKU</b>`;
    } else if (item.total > 0) {
      tdMissing.innerHTML = `<span class="ok" style="font-weight:600;">✔ Đủ</span>`;
    } else {
      tdMissing.innerHTML = `<span class="hint">0 SKU</span>`;
    }
    tr.appendChild(tdMissing);

    // 9. Actions
    const tdActions = document.createElement('td');
    tdActions.style.textAlign = 'right';
    tdActions.style.whiteSpace = 'nowrap';
    tdActions.innerHTML = `
      <div class="dt-actions">
        <button type="button" class="ghost btn-sm" title="Đối chiếu chi tiết từng SKU" onclick="openFolderSkuAudit('${escapeHtml(item.folder)}', '${escapeHtml(item.url || '')}')">🔍 SKU</button>
        <button type="button" class="ghost btn-sm" style="color:var(--green); border-color:var(--green);" title="Chạy Thuật toán Seamless cho thư mục này" onclick="runSingleFolder('${escapeHtml(item.folder)}', '${escapeHtml(item.url || '')}', 'algo')">⚡ Thuật toán</button>
        <button type="button" class="primary btn-sm" title="Chạy ChatGPT cho thư mục này" onclick="runSingleFolder('${escapeHtml(item.folder)}', '${escapeHtml(item.url || '')}', 'chatgpt')">▶ Chạy</button>
        <button type="button" class="ghost btn-sm" title="Mở thư mục output trên máy" onclick="openFolderDirectory('${escapeHtml(item.folder)}')">📁</button>
        ${item.url ? `<button type="button" class="danger btn-sm" title="Gỡ link Drive" onclick="unlinkDriveFolder('${escapeHtml(item.folder)}')">🗑️</button>` : `<button type="button" class="danger btn-sm dt-action-placeholder" tabindex="-1" aria-hidden="true">🗑️</button>`}
      </div>
    `;
    tr.appendChild(tdActions);

    tbody.appendChild(tr);
  });
}

function openAddDriveModal() {
  openEditDriveModal('', '');
}

function openEditDriveModal(folder, url) {
  $('edit-drive-folder').value = folder || '';
  $('edit-drive-url').value = url || '';
  const sel = $('edit-drive-folder-select');
  if (sel) sel.value = folder || '';
  $('modal-edit-drive-title').textContent = folder ? `🔗 Sửa Link Drive Cho Thư Mục "${folder}"` : '➕ Thêm / Gán Link Google Drive Mới';
  $('modal-edit-drive').classList.remove('hidden');
}

function closeEditDriveModal() {
  $('modal-edit-drive').classList.add('hidden');
}

async function saveDriveLinkModal() {
  const folder = $('edit-drive-folder').value.trim();
  const url = $('edit-drive-url').value.trim();
  if (!folder) {
    alert('Vui lòng nhập hoặc chọn tên thư mục vải.');
    $('edit-drive-folder').focus();
    return;
  }
  try {
    await api('/api/save-drive-link', { folder, url });
    closeEditDriveModal();
    await fetchState();
  } catch (err) {
    alert('Lỗi lưu link Drive: ' + (err.message || err));
  }
}

async function unlinkDriveFolder(folder) {
  if (!confirm(`Bạn có chắc muốn gỡ link Google Drive của thư mục "${folder}"?\n(Các file kết quả trên máy vẫn được giữ nguyên).`)) return;
  try {
    await api('/api/save-drive-link', { folder, url: '' });
    await fetchState();
  } catch (err) {
    alert('Lỗi gỡ link: ' + (err.message || err));
  }
}

function openBulkDriveModal() {
  $('bulk-drive-urls').value = '';
  $('bulk-match-results').replaceChildren();
  $('bulk-match-results').classList.add('hidden');
  $('modal-bulk-drive').classList.remove('hidden');
}

function closeBulkDriveModal() {
  $('modal-bulk-drive').classList.add('hidden');
}

async function submitBulkDriveMatch() {
  const urlsText = ($('bulk-drive-urls').value || '').trim();
  if (!urlsText) {
    alert('Vui lòng dán ít nhất 1 link Google Drive.');
    return;
  }
  const urls = urlsText.split(/[\r\n,]+/).map(u => u.trim()).filter(u => u.length > 0);
  if (!urls.length) {
    alert('Không tìm thấy link hợp lệ.');
    return;
  }

  $('btn-submit-bulk-match').disabled = true;
  $('bulk-match-loading').classList.remove('hidden');
  $('bulk-match-results').classList.add('hidden');
  $('bulk-match-results').replaceChildren();

  try {
    const res = await api('/api/auto-match-drive-urls', { urls });
    $('bulk-match-loading').classList.add('hidden');
    $('bulk-match-results').classList.remove('hidden');

    if (res.matched && res.matched.length) {
      const matchBox = document.createElement('div');
      matchBox.innerHTML = `<b class="ok">✔ Đã ghép nối thành công ${res.matched.length} thư mục:</b>`;
      res.matched.forEach(m => {
        const item = document.createElement('div');
        item.style.padding = '4px 8px';
        item.style.background = 'rgba(52,211,153,.1)';
        item.style.borderRadius = '6px';
        item.innerHTML = `📁 <b>${escapeHtml(m.folder)}</b> ← <span style="font-family:monospace; color:var(--cyan);">${escapeHtml(m.url)}</span>`;
        matchBox.appendChild(item);
      });
      $('bulk-match-results').appendChild(matchBox);
    }

    if (res.unmatched && res.unmatched.length) {
      const unmatchBox = document.createElement('div');
      unmatchBox.innerHTML = `<b class="amber" style="margin-top:6px; display:block;">⚠️ ${res.unmatched.length} link chưa tìm thấy thư mục khớp:</b>`;
      res.unmatched.forEach(u => {
        const item = document.createElement('div');
        item.style.padding = '4px 8px';
        item.style.background = 'rgba(251,191,36,.1)';
        item.style.borderRadius = '6px';
        item.innerHTML = `<span style="font-family:monospace;">${escapeHtml(u.url)}</span>: ${escapeHtml(u.reason || 'Chưa ghép')}`;
        unmatchBox.appendChild(item);
      });
      $('bulk-match-results').appendChild(unmatchBox);
    }

    await fetchState();
  } catch (err) {
    alert('Lỗi ghép nối link Drive: ' + (err.message || err));
  } finally {
    $('btn-submit-bulk-match').disabled = false;
    $('bulk-match-loading').classList.add('hidden');
  }
}

async function openFolderSkuAudit(folder, url) {
  if (!url) {
    openEditDriveModal(folder, '');
    return;
  }
  currentAuditFolderData = { folder, url, skus: [] };
  $('mfa-title').textContent = `Đối Chiếu SKU: ${folder}`;
  $('mfa-subtitle').textContent = `Link Drive: ${url}`;
  $('mfa-counts').textContent = 'Đang quét...';
  $('mfa-chips-container').replaceChildren();
  $('modal-folder-audit').classList.remove('hidden');
  $('mfa-loading').classList.remove('hidden');

  try {
    const res = await api('/api/audit-single-folder', { folder, url });
    currentAuditFolderData = res;
    currentAuditFolderData.folder = folder;
    currentAuditFolderData.url = url;
    renderFolderAuditModalContent(res);
  } catch (err) {
    $('mfa-loading').classList.add('hidden');
    $('mfa-chips-container').innerHTML = `<div style="color:var(--red); padding:16px;">⚠️ Lỗi quét Drive: ${escapeHtml(err.message || err)}</div>`;
  }
}

function renderFolderAuditModalContent(data) {
  $('mfa-loading').classList.add('hidden');
  const check = (data.checks && data.checks[0]) || {};
  const total = check.total || data.drive_total || 0;
  const created = check.created_count || 0;
  const missing = check.missing_count || 0;
  const percent = check.percent || 0;
  const skus = check.skus || [];

  currentAuditFolderData.skus = skus;
  currentAuditFolderData.missingSkus = skus.filter(s => s.status !== 'done').map(s => s.sku);

  $('mfa-counts').innerHTML = `Đã tạo: <b class="ok">${created} / ${total}</b> (${percent}%) • Còn thiếu: <b class="amber">${missing}</b>`;
  $('mfa-progress-bar').style.width = percent + '%';

  $('mfa-filter-all').textContent = `Tất cả (${total})`;
  $('mfa-filter-done').textContent = `✔ Đã tạo (${created})`;
  $('mfa-filter-missing').textContent = `⚠️ Chưa tạo (${missing})`;

  renderFolderAuditChips('all');
}

function filterFolderAuditChips(filter, btn) {
  if (btn) {
    const parent = btn.parentElement;
    Array.from(parent.children).forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
  }
  renderFolderAuditChips(filter);
}

function renderFolderAuditChips(filter) {
  const container = $('mfa-chips-container');
  container.replaceChildren();
  const skus = (currentAuditFolderData && currentAuditFolderData.skus) || [];

  const filtered = skus.filter(s => {
    if (filter === 'done') return s.status === 'done';
    if (filter === 'missing') return s.status !== 'done';
    return true;
  });

  if (!filtered.length) {
    const empty = document.createElement('div');
    empty.className = 'hint';
    empty.style.padding = '16px';
    empty.textContent = 'Không có SKU nào phù hợp với bộ lọc này.';
    container.appendChild(empty);
    return;
  }

  filtered.forEach(s => {
    const chip = document.createElement('span');
    chip.className = `audit-chip ${s.status}`;
    const icon = s.status === 'done' ? '✔' : '⏳';
    chip.textContent = `${s.sku} ${icon}`;
    chip.title = s.status === 'done' ? `Seamless: ${s.has_seamless ? 'Có' : 'Không'} | Swatch: ${s.has_fabric ? 'Có' : 'Không'}` : 'Chưa tạo ảnh thành phẩm';
    container.appendChild(chip);
  });
}

function closeFolderSkuAuditModal() {
  $('modal-folder-audit').classList.add('hidden');
}

function copyCurrentFolderMissingSkus() {
  const missing = (currentAuditFolderData && currentAuditFolderData.missingSkus) || [];
  if (!missing.length) {
    alert('Thư mục này đã hoàn thành 100%! Không có SKU nào còn thiếu.');
    return;
  }
  const text = missing.join(', ');
  navigator.clipboard.writeText(text).then(() => {
    alert(`Đã sao chép ${missing.length} SKU còn thiếu vào Clipboard!`);
  }).catch(() => {
    prompt('Sao chép danh sách SKU thiếu:', text);
  });
}

function sendMissingSkusToPipeline() {
  const missing = (currentAuditFolderData && currentAuditFolderData.missingSkus) || [];
  if (!missing.length) {
    alert('Thư mục này đã tạo đủ tất cả SKU!');
    return;
  }
  const folder = (currentAuditFolderData && currentAuditFolderData.folder) || '';
  $('sku').value = missing.join(', ');
  closeFolderSkuAuditModal();
  switchTab('dashboard');
  if (folder) {
    const select = $('folder-filter');
    if (select) select.value = folder;
  }
  $('sku').scrollIntoView({ behavior: 'smooth' });
}

async function reScanCurrentFolderAudit() {
  if (!currentAuditFolderData || !currentAuditFolderData.folder) return;
  openFolderSkuAudit(currentAuditFolderData.folder, currentAuditFolderData.url);
}

async function auditAllDriveLinks() {
  const foldersWithUrls = currentDriveTableFolders.filter(f => f.url);
  if (!foldersWithUrls.length) {
    alert('Chưa có thư mục nào được gán link Google Drive. Hãy thêm link Drive trước.');
    return;
  }
  const btn = $('btn-audit-all');
  btn.disabled = true;
  btn.textContent = '⏳ Đang quét tất cả...';
  $('audit-loading').classList.remove('hidden');

  try {
    for (const item of foldersWithUrls) {
      try {
        await api('/api/audit-single-folder', { folder: item.folder, url: item.url });
      } catch (err) {
        console.error('Audit failed for folder', item.folder, err);
      }
    }
    await fetchState();
    alert(`Đã hoàn tất quét và đối chiếu ${foldersWithUrls.length} thư mục Google Drive!`);
  } catch (err) {
    alert('Lỗi quét đối chiếu: ' + (err.message || err));
  } finally {
    btn.disabled = false;
    btn.textContent = '🔄 Quét & Đối chiếu tất cả link';
    $('audit-loading').classList.add('hidden');
  }
}

async function poll() {
  await fetchState();
  setTimeout(poll, 1000);
}
poll();
</script>
</body></html>"""


def is_frozen():
    return bool(getattr(sys, "frozen", False))


def bundled_resource_dir():
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return bundle_root / "runtime_assets"


def copy_missing_tree(source, destination):
    """Copy bundled defaults without overwriting files edited by the user."""
    if not safe_is_dir(source):
        return
    for source_path in source.rglob("*"):
        if not source_path.is_file():
            continue
        relative = source_path.relative_to(source)
        destination_path = destination / relative
        if destination_path.exists():
            continue
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination_path)


def ensure_runtime_layout(project_dir):
    """Create the writable first-run files needed by a standalone EXE."""
    project_dir = Path(project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)
    if not is_frozen():
        return

    resources = bundled_resource_dir()
    config_path = project_dir / "config.json"
    default_config = resources / "config.default.json"
    if not config_path.exists():
        if not default_config.is_file():
            raise FileNotFoundError(
                "Bản EXE thiếu cấu hình mặc định runtime_assets/config.default.json."
            )
        shutil.copy2(default_config, config_path)

    copy_missing_tree(resources / "prompts", project_dir / "prompts")
    for name in (
        "logs",
        "output",
        "textures",
        "textures_raw",
        "textures_cropped",
    ):
        (project_dir / name).mkdir(parents=True, exist_ok=True)


def valid_project_dir(path):
    path = Path(path)
    if is_frozen():
        return path.is_dir() and (path / "config.json").is_file()
    required = (
        "config.json",
        "import_google_drive.py",
        "crop_textures.py",
        "run_chatgpt_texture_grouped_batch.py",
        "run_chatgpt_fabric_grouped_batch.py",
        "package_seamless_textures.py",
    )
    try:
        return path.is_dir() and all((path / name).is_file() for name in required)
    except OSError:
        return False


def safe_is_file(path):
    try:
        return Path(path).is_file()
    except OSError:
        return False


def safe_is_dir(path):
    try:
        return Path(path).is_dir()
    except OSError:
        return False


def resolve_project_path(project_dir, value):
    """Resolve a configurable path without requiring it to exist."""
    expanded = Path(os.path.expandvars(str(value).strip()))
    return expanded if expanded.is_absolute() else Path(project_dir) / expanded


def source_settings(project_dir, config):
    """Return the source mode and folders used by the desktop UI."""
    drive = config.get("google_drive", {})
    crop = config.get("crop", {})
    app_ui = config.get("app_ui", {})
    mode = str(app_ui.get("source_mode", "drive")).strip().lower()
    if mode not in {"drive", "local"}:
        mode = "drive"

    drive_dir = resolve_project_path(
        project_dir, drive.get("destination_dir", "textures_raw")
    )
    local_value = str(app_ui.get("local_source_dir", "")).strip()
    local_dir = resolve_project_path(project_dir, local_value) if local_value else None
    active_dir = local_dir if mode == "local" and local_dir else drive_dir
    crop_dir = resolve_project_path(
        project_dir, crop.get("output_dir", "textures_cropped")
    )
    return mode, local_dir, active_dir, crop_dir


def parse_iso_or_custom_time(timestr):
    if not timestr:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.datetime.strptime(timestr, fmt)
        except ValueError:
            pass
    return None


def format_duration(seconds):
    if seconds is None:
        return "-"
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    rem = seconds % 60
    if minutes < 60:
        return f"{minutes}m {rem:02d}s" if rem else f"{minutes}m"
    hours = minutes // 60
    rem_min = minutes % 60
    return f"{hours}h {rem_min:02d}m {rem:02d}s"


def format_file_size(bytes_count):
    if bytes_count < 1024:
        return f"{bytes_count} B"
    if bytes_count < 1024 * 1024:
        return f"{bytes_count / 1024:.1f} KB"
    return f"{bytes_count / (1024 * 1024):.2f} MB"


def compute_historical_timing_stats(project_dir):
    status_files = (
        Path(project_dir) / "status_chatgpt_texture_grouped.json",
        Path(project_dir) / "status_chatgpt_fabric_grouped.json",
    )
    durations = []
    for sfile in status_files:
        if safe_is_file(sfile):
            try:
                data = load_json(sfile)
                for item in data.values():
                    if isinstance(item, dict) and item.get("status") == "done":
                        start = parse_iso_or_custom_time(item.get("started_at"))
                        end = parse_iso_or_custom_time(item.get("completed_at"))
                        if start and end and end >= start:
                            sec = (end - start).total_seconds()
                            if 10 <= sec <= 600:
                                durations.append(sec)
            except Exception:
                pass

    if durations:
        avg_sec = sum(durations) / len(durations)
        return {
            "avg_duration_seconds": round(avg_sec, 1),
            "completed_count": len(durations),
            "last_duration_seconds": round(durations[-1], 1) if durations else None,
        }
    return {"avg_duration_seconds": 80.0, "completed_count": 0}


def fetch_drive_folder_title(url, timeout=6):
    """Fetch public Google Drive folder title using urllib."""
    try:
        req = urllib.request.Request(
            str(url or "").strip(),
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read().decode("utf-8", errors="replace")
            m = re.search(r"<title>(.*?)\s*-\s*Google Drive</title>", content, re.IGNORECASE)
            if m:
                return m.group(1).strip()
            m_og = re.search(
                r'<meta\s+property=[\"\']og:title[\"\']\s+content=[\"\'](.*?)[\"\']',
                content,
                re.IGNORECASE,
            )
            if m_og:
                return m_og.group(1).strip()
    except Exception:
        pass
    return None


def extract_base_folder_code(title):
    """Extract primary code from folder title (e.g. '1013 (Làm trước)' -> '1013')."""
    if not title:
        return ""
    cleaned = str(title).strip()
    parts = re.split(r"[\(\[\s_\-]", cleaned)
    candidate = parts[0].strip() if parts else cleaned
    return candidate or cleaned


def audit_single_drive_url(project_dir, url, target_child="all", timeout=60):
    """Audit one Google Drive folder URL against the project's output child directories."""
    url = str(url or "").strip()
    if not validate_drive_url(url):
        return {
            "url": url,
            "title": "URL không hợp lệ",
            "base_code": "",
            "error": "URL Google Drive không hợp lệ. Phải có dạng https://drive.google.com/drive/folders/...",
            "checks": [],
        }

    title = fetch_drive_folder_title(url, timeout=6)

    import import_google_drive
    try:
        entries = import_google_drive.list_public_folder(url, timeout)
    except Exception as exc:
        return {
            "url": url,
            "title": title or "Thư mục Drive",
            "base_code": extract_base_folder_code(title or ""),
            "error": f"Không thể lấy danh sách ảnh từ Drive: {exc}",
            "checks": [],
        }

    if not title and entries:
        first_parts = [entry["path"].parts[0] for entry in entries if entry["path"].parts]
        has_nested = any(len(entry["path"].parts) > 1 for entry in entries)
        common_root = (
            first_parts[0]
            if has_nested and first_parts and all(p == first_parts[0] for p in first_parts)
            else None
        )
        if common_root:
            title = common_root

    base_code = extract_base_folder_code(title or "")

    images, _, _ = import_google_drive.select_images(
        entries, SUPPORTED_IMAGE_EXTENSIONS, recursive=False
    )
    drive_skus = [Path(e["name"]).stem for e in images]

    out_base = resolve_project_path(project_dir, "output")
    available_children = []
    if safe_is_dir(out_base):
        for sub in sorted(out_base.iterdir()):
            if sub.is_dir():
                available_children.append(sub.name)

    if not available_children:
        available_children = ["chatgpt"]

    if target_child and target_child != "all":
        target_children = [c for c in available_children if c.casefold() == target_child.casefold()]
        if not target_children:
            target_children = [target_child]
    else:
        target_children = available_children

    output_checks = []
    output_name = "seamless_texture.png"
    fabric_name = "image_1.png"

    for child in target_children:
        child_dir = out_base / child
        folder_found = None
        matched_name = None

        if safe_is_dir(child_dir):
            existing_subdirs = {d.name.casefold(): d for d in child_dir.iterdir() if d.is_dir()}
            candidates = [n for n in [title, base_code] if n]
            for cand in candidates:
                if cand.casefold() in existing_subdirs:
                    folder_found = existing_subdirs[cand.casefold()]
                    matched_name = folder_found.name
                    break
            if not folder_found and base_code:
                for sname, sdir in existing_subdirs.items():
                    if sname.startswith(base_code.casefold()):
                        folder_found = sdir
                        matched_name = sdir.name
                        break

        sku_results = []
        created_count = 0
        missing_count = 0

        if folder_found and safe_is_dir(folder_found):
            for sku in drive_skus:
                sku_sub = folder_found / sku
                has_seamless = safe_is_file(sku_sub / output_name)
                has_fabric = safe_is_file(sku_sub / fabric_name)
                if has_seamless or has_fabric:
                    created_count += 1
                    status = "done"
                elif safe_is_dir(sku_sub):
                    status = "partial"
                    missing_count += 1
                else:
                    status = "missing"
                    missing_count += 1

                sku_results.append({
                    "sku": sku,
                    "status": status,
                    "has_seamless": has_seamless,
                    "has_fabric": has_fabric,
                })
        else:
            missing_count = len(drive_skus)
            for sku in drive_skus:
                sku_results.append({
                    "sku": sku,
                    "status": "missing",
                    "has_seamless": False,
                    "has_fabric": False,
                })

        total = len(drive_skus)
        percent = round(created_count * 100 / total, 1) if total > 0 else 0.0

        output_checks.append({
            "engine": child,
            "folder_exists": bool(folder_found),
            "matched_folder_name": matched_name or base_code or title or "chưa có",
            "folder_path": f"output/{child}/{matched_name or base_code or title}",
            "total": total,
            "created_count": created_count,
            "missing_count": missing_count,
            "percent": percent,
            "skus": sku_results,
        })

    return {
        "url": url,
        "title": title or base_code or "Drive Folder",
        "base_code": base_code,
        "drive_total": len(drive_skus),
        "drive_skus": drive_skus,
        "checks": output_checks,
    }


def compute_drive_folders_stats(project_dir, config, current_running_folder=None, include_unlinked=True):
    """Scan and compute real-time image progress for each Drive / base_sku folder."""
    try:
        organize_existing_folder_outputs(project_dir)
    except Exception:
        pass

    drive = config.get("google_drive", {})
    configured_urls = deduplicate_drive_items(drive.get("urls", []))
    if not configured_urls and drive.get("share_url"):
        configured_urls = [{"url": drive.get("share_url"), "folder": ""}]

    raw_root = resolve_project_path(project_dir, "textures_raw")
    crop_root = resolve_project_path(project_dir, "textures_cropped")
    tex_root = resolve_project_path(project_dir, "textures")
    out_root = resolve_project_path(project_dir, "output/chatgpt")

    sync_path = resolve_project_path(
        project_dir, drive.get("sync_status_file", "status_drive_sync.json")
    )
    sync_data = load_json(sync_path) if safe_is_file(sync_path) else {}
    sync_folders = sync_data.get("folders", {}) if isinstance(sync_data, dict) else {}

    # Collect known folder names from config
    folders_map = {}
    folder_modified_at = {}
    for item in configured_urls:
        f_name = str(item.get("folder", "")).strip()
        if f_name:
            folders_map[f_name] = str(item.get("url", "")).strip()
            folder_modified_at[f_name] = str(item.get("modified_at", "")).strip()

    # Also collect from sync_folders if not already mapped
    for f_name, f_info in sync_folders.items():
        if f_name and f_name not in folders_map and isinstance(f_info, dict):
            folders_map[f_name] = str(f_info.get("drive_url", "")).strip()

    # Also discover all existing local fabric folders on disk
    if include_unlinked:
        for root_dir in (out_root, raw_root, crop_root, tex_root):
            if safe_is_dir(root_dir):
                for sub in sorted(root_dir.iterdir()):
                    if sub.is_dir() and sub.name not in folders_map:
                        folders_map[sub.name] = ""

    # Pre-map all created SKUs across output/chatgpt (supports both <base_sku>/<sku>/ and flat <sku>/)
    output_name = "seamless_texture.png"
    done_sku_keys = set()
    sku_to_out_folder = {}
    if safe_is_dir(out_root):
        for f in out_root.iterdir():
            if f.is_dir():
                if safe_is_file(f / output_name) and safe_is_file(f / "image_1.png"):
                    sku_to_out_folder[f.name] = ""
                    done_sku_keys.add(("", f.name))
                for sub in f.iterdir():
                    if sub.is_dir():
                        sname = sub.name
                        sku_to_out_folder[sname] = f.name
                        has_seamless = safe_is_file(sub / output_name)
                        has_fabric = safe_is_file(sub / "image_1.png")
                        if has_seamless and has_fabric:
                            done_sku_keys.add((f.name, sname))

    stats_list = []
    total_all_skus = 0
    total_all_created = 0
    total_all_pending = 0

    for folder_name, url in folders_map.items():
        if not folder_name:
            continue
        folder_display = folder_name
        raw_dir = raw_root / folder_name
        crop_dir = crop_root / folder_name
        tex_dir = tex_root / folder_name
        folder_out = out_root / folder_name

        skus = set()
        raw_count = 0
        cropped_count = 0
        seamless_count = 0
        fabric_count = 0

        # 1. Output folder subdirs
        if safe_is_dir(folder_out):
            for sub in folder_out.iterdir():
                if sub.is_dir():
                    sku_name = sub.name
                    skus.add(sku_name)
                    if safe_is_file(sub / output_name):
                        seamless_count += 1
                    if safe_is_file(sub / "image_1.png"):
                        fabric_count += 1

        # 2. Raw folder + top-level raw matching prefix
        if safe_is_dir(raw_dir):
            for path in raw_dir.iterdir():
                if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
                    skus.add(path.stem)
                    raw_count += 1
        if safe_is_dir(raw_root):
            for path in raw_root.iterdir():
                if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
                    if sku_to_out_folder.get(path.stem) == folder_name or path.stem.upper().startswith(folder_name.upper()):
                        skus.add(path.stem)
                        raw_count += 1

        # 3. Crop folder + top-level crop matching prefix
        if safe_is_dir(crop_dir):
            for path in crop_dir.iterdir():
                if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
                    skus.add(path.stem)
                    cropped_count += 1
        if safe_is_dir(crop_root):
            for path in crop_root.iterdir():
                if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
                    if sku_to_out_folder.get(path.stem) == folder_name or path.stem.upper().startswith(folder_name.upper()):
                        skus.add(path.stem)
                        cropped_count += 1

        # 4. Textures folder
        if safe_is_dir(tex_dir):
            for path in tex_dir.iterdir():
                if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS and path.name.startswith("texture_"):
                    if seamless_count == 0:
                        seamless_count += 1
                    stem = path.stem[8:] if path.stem.startswith("texture_") else path.stem
                    skus.add(stem)

        done_skus = set()
        for s in skus:
            if (folder_name, s) in done_sku_keys:
                done_skus.add(s)

        total_skus = len(skus)
        created_count = len(done_skus)
        pending_count = max(0, total_skus - created_count)
        percent = round(created_count * 100 / total_skus) if total_skus > 0 else 0

        is_active = (current_running_folder == folder_name)

        sync_info = sync_folders.get(folder_name, {})
        drive_total_images = sync_info.get("drive_total_images")
        last_sync_at = sync_info.get("last_sync_at")
        last_imported_count = sync_info.get("last_imported_count")

        modified_candidates = []
        configured_modified = folder_modified_at.get(folder_name, "")
        if configured_modified:
            try:
                modified_candidates.append(datetime.datetime.fromisoformat(configured_modified.replace("Z", "+00:00")).timestamp())
            except (TypeError, ValueError):
                pass
        if last_sync_at:
            try:
                modified_candidates.append(datetime.datetime.fromisoformat(str(last_sync_at)).timestamp())
            except (TypeError, ValueError):
                pass
        for directory in (raw_dir, crop_dir, tex_dir, folder_out):
            if safe_is_dir(directory):
                try:
                    modified_candidates.append(directory.stat().st_mtime)
                except OSError:
                    pass
        modified_ts = max(modified_candidates, default=0.0)

        has_drive_url = bool(url)
        status_label = (
            "completed"
            if total_skus > 0 and created_count >= total_skus
            else ("in_progress" if created_count > 0 else ("not_started" if total_skus > 0 else "empty"))
        )

        stats_list.append({
            "folder": folder_name,
            "folder_display": folder_display,
            "url": url,
            "has_drive_url": has_drive_url,
            "status": status_label,
            "total": total_skus,
            "drive_total": drive_total_images,
            "last_sync_at": last_sync_at,
            "last_imported_count": last_imported_count,
            "modified_at": configured_modified,
            "modified_ts": modified_ts,
            "raw_count": raw_count,
            "cropped_count": cropped_count,
            "seamless_count": seamless_count,
            "fabric_count": fabric_count,
            "created_count": created_count,
            "pending_count": pending_count,
            "percent": percent,
            "is_active": is_active,
            "output_path": str(folder_out),
        })

        total_all_skus += total_skus
        total_all_created += created_count
        total_all_pending += pending_count

    stats_list.sort(key=lambda x: (0 if x["url"] else 1, x["folder_display"].casefold()))

    return {
        "folders": stats_list,
        "total_folders": len(stats_list),
        "linked_folders": sum(1 for s in stats_list if s.get("has_drive_url")),
        "unlinked_folders": sum(1 for s in stats_list if not s.get("has_drive_url")),
        "total_skus": total_all_skus,
        "total_created": total_all_created,
        "total_pending": total_all_pending,
        "overall_percent": round(total_all_created * 100 / total_all_skus) if total_all_skus > 0 else 0,
        "recent_sync_history": (
            sync_data.get("history", [])[-10:]
            if isinstance(sync_data, dict) and isinstance(sync_data.get("history"), list)
            else []
        ),
    }


def organize_existing_folder_outputs(project_dir):
    """Ensure any intermediate textures in textures/ are organized into their base_sku folder."""
    tex_base = resolve_project_path(project_dir, "textures")
    out_base = resolve_project_path(project_dir, "output/chatgpt")

    if not safe_is_dir(out_base) or not safe_is_dir(tex_base):
        return

    sku_to_folder = {}
    for f in out_base.iterdir():
        if f.is_dir():
            for sub in f.iterdir():
                if sub.is_dir():
                    sku_to_folder[sub.name] = f.name

    for sku, folder in sku_to_folder.items():
        root_tex = tex_base / f"texture_{sku}.png"
        folder_tex = tex_base / folder / f"texture_{sku}.png"
        if safe_is_file(root_tex):
            (tex_base / folder).mkdir(parents=True, exist_ok=True)
            if not safe_is_file(folder_tex):
                try:
                    shutil.copy2(root_tex, folder_tex)
                except Exception:
                    pass


def get_image_file(project_dir, sku, kind, folder=None):
    if not re.match(r"^[A-Za-z0-9_.\-]+$", sku):
        return None
    config = load_json(Path(project_dir) / "config.json")
    package = config.get("seamless_package", {})
    output_name = str(package.get("filename", "seamless_texture.png")).strip()

    out_base = resolve_project_path(project_dir, "output/chatgpt")
    crop_base = resolve_project_path(project_dir, "textures_cropped")
    raw_base = resolve_project_path(project_dir, "textures_raw")
    tex_base = resolve_project_path(project_dir, "textures")

    if kind == "output":
        if folder:
            target = out_base / folder / sku / output_name
            if safe_is_file(target):
                return target
            target_alt = out_base / folder / sku / "image_1.png"
            if safe_is_file(target_alt):
                return target_alt
        target = out_base / sku / output_name
        if safe_is_file(target):
            return target
        if safe_is_dir(out_base):
            for candidate in out_base.glob(f"*/{sku}/{output_name}"):
                if safe_is_file(candidate):
                    return candidate
            for candidate in out_base.glob(f"*/{sku}/image_1.png"):
                if safe_is_file(candidate):
                    return candidate
        return None

    elif kind == "final_seamless":
        if folder:
            target = out_base / folder / sku / output_name
            return target if safe_is_file(target) else None
        target = out_base / sku / output_name
        if safe_is_file(target):
            return target
        if safe_is_dir(out_base):
            matches = [candidate for candidate in out_base.glob(f"*/{sku}/{output_name}") if safe_is_file(candidate)]
            return matches[0] if len(matches) == 1 else None
        return None

    elif kind in {"seamless", "texture"}:
        if folder:
            target = tex_base / folder / f"texture_{sku}.png"
            if safe_is_file(target):
                return target
            target_out = out_base / folder / sku / output_name
            if safe_is_file(target_out):
                return target_out
        target = tex_base / f"texture_{sku}.png"
        if safe_is_file(target):
            return target
        if safe_is_dir(tex_base):
            for candidate in tex_base.glob(f"*/texture_{sku}.png"):
                if safe_is_file(candidate):
                    return candidate
        if safe_is_dir(out_base):
            for candidate in out_base.glob(f"*/{sku}/{output_name}"):
                if safe_is_file(candidate):
                    return candidate
        return None

    elif kind == "fabric":
        if folder:
            target = out_base / folder / sku / "image_1.png"
            if safe_is_file(target):
                return target
        target = out_base / sku / "image_1.png"
        if safe_is_file(target):
            return target
        if safe_is_dir(out_base):
            for candidate in out_base.glob(f"*/{sku}/image_1.png"):
                if safe_is_file(candidate):
                    return candidate
        alt_target = resolve_project_path(project_dir, "output/chatgpt_project_fabric") / sku / "image_1.png"
        if safe_is_file(alt_target):
            return alt_target
        return None

    elif kind == "cropped":
        if folder:
            target = crop_base / folder / f"{sku}.png"
            if safe_is_file(target):
                return target
        target = crop_base / f"{sku}.png"
        if safe_is_file(target):
            return target
        if safe_is_dir(crop_base):
            for candidate in crop_base.glob(f"*/{sku}.png"):
                if safe_is_file(candidate):
                    return candidate
        return None

    elif kind in {"raw", "source"}:
        if folder:
            f_raw = raw_base / folder
            if safe_is_dir(f_raw):
                for ext in SUPPORTED_IMAGE_EXTENSIONS:
                    candidate = f_raw / f"{sku}{ext}"
                    if safe_is_file(candidate):
                        return candidate
        if safe_is_dir(raw_base):
            for ext in SUPPORTED_IMAGE_EXTENSIONS:
                candidate = raw_base / f"{sku}{ext}"
                if safe_is_file(candidate):
                    return candidate
            for ext in SUPPORTED_IMAGE_EXTENSIONS:
                for candidate in raw_base.glob(f"*/{sku}{ext}"):
                    if safe_is_file(candidate):
                        return candidate
        target = crop_base / f"{sku}.png"
        if safe_is_file(target):
            return target
        return None

    return None


def get_sku_details(project_dir, sku, folder=None):
    if not re.match(r"^[A-Za-z0-9_.\-]+$", sku):
        raise ValueError("Mã SKU không hợp lệ.")

    config = load_json(Path(project_dir) / "config.json")
    output_name = str(config.get("seamless_package", {}).get("filename", "seamless_texture.png")).strip()
    out_base = resolve_project_path(project_dir, "output/chatgpt")
    detected_output_folder = str(folder or "").strip()
    if detected_output_folder:
        sku_output_dir = out_base / detected_output_folder / sku
    else:
        direct_dir = out_base / sku
        candidates = (
            sorted(path for path in out_base.glob(f"*/{sku}") if path.is_dir())
            if safe_is_dir(out_base)
            else []
        )
        if safe_is_dir(direct_dir):
            sku_output_dir = direct_dir
        elif len(candidates) == 1:
            sku_output_dir = candidates[0]
            detected_output_folder = candidates[0].parent.name
        else:
            sku_output_dir = direct_dir

    # Modal status must be based only on the two canonical final files.
    output_file = sku_output_dir / output_name
    output_file = output_file if safe_is_file(output_file) else None
    seamless_file = output_file
    fabric_file = sku_output_dir / "image_1.png"
    fabric_file = fabric_file if safe_is_file(fabric_file) else None
    cropped_file = get_image_file(project_dir, sku, "cropped", folder=folder)
    raw_file = get_image_file(project_dir, sku, "raw", folder=folder)

    # Detect folder if not explicitly provided
    detected_folder = folder or detected_output_folder
    if not detected_folder and output_file:
        try:
            parts = output_file.parts
            idx = parts.index("chatgpt") if "chatgpt" in parts else -1
            if idx != -1 and len(parts) >= idx + 3 and parts[idx + 2] == sku:
                detected_folder = parts[idx + 1]
        except Exception:
            pass
    if not detected_folder and cropped_file:
        try:
            parts = cropped_file.parts
            idx = parts.index("textures_cropped") if "textures_cropped" in parts else -1
            if idx != -1 and len(parts) >= idx + 2 and parts[idx + 1] != f"{sku}.png":
                detected_folder = parts[idx + 1]
        except Exception:
            pass

    status_file = Path(project_dir) / "status_chatgpt_texture_grouped.json"
    status_entry = None
    if safe_is_file(status_file):
        try:
            status_data = load_json(status_file)
            status_entry = status_data.get(sku)
        except Exception:
            pass

    fabric_status_file = Path(project_dir) / "status_chatgpt_fabric_grouped.json"
    fabric_status_entry = None
    if safe_is_file(fabric_status_file):
        try:
            f_data = load_json(fabric_status_file)
            fabric_status_entry = f_data.get(sku)
        except Exception:
            pass

    def file_metadata(path):
        if not path or not safe_is_file(path):
            return None
        stat = path.stat()
        meta = {
            "path": str(path.resolve()),
            "size_bytes": stat.st_size,
            "size_formatted": format_file_size(stat.st_size),
            "modified_at": time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)
            ),
            "width": None,
            "height": None,
        }
        try:
            from PIL import Image

            with Image.open(path) as img:
                meta["width"], meta["height"] = img.size
        except Exception:
            pass
        return meta

    output_meta = file_metadata(output_file)
    seamless_meta = file_metadata(seamless_file)
    fabric_meta = file_metadata(fabric_file)
    cropped_meta = file_metadata(cropped_file)
    raw_meta = file_metadata(raw_file)

    duration_text = None
    if status_entry:
        start = parse_iso_or_custom_time(status_entry.get("started_at"))
        end = parse_iso_or_custom_time(status_entry.get("completed_at"))
        if start and end and end >= start:
            sec = int((end - start).total_seconds())
            duration_text = format_duration(sec)

    fabric_duration_text = None
    if fabric_status_entry:
        start = parse_iso_or_custom_time(fabric_status_entry.get("started_at"))
        end = parse_iso_or_custom_time(fabric_status_entry.get("completed_at"))
        if start and end and end >= start:
            sec = int((end - start).total_seconds())
            fabric_duration_text = format_duration(sec)

    is_created = bool(output_meta is not None and fabric_meta is not None)
    status_label = "done" if is_created else (status_entry.get("status") if status_entry else "pending")
    quota_info = None
    if not is_created:
        if status_entry and status_entry.get("error_type") == "quota_limit":
            status_label = "quota_limit"
            quota_info = status_entry.get("quota_message") or status_entry.get("quota_reset_info")
        elif fabric_status_entry and fabric_status_entry.get("error_type") == "quota_limit":
            status_label = "quota_limit"
            quota_info = fabric_status_entry.get("quota_message") or fabric_status_entry.get("quota_reset_info")

    return {
        "sku": sku,
        "folder": detected_folder,
        "is_created": is_created,
        "status": status_label,
        "quota_info": quota_info,
        "duration_text": duration_text,
        "fabric_duration_text": fabric_duration_text,
        "output": output_meta,
        "seamless": seamless_meta,
        "fabric": fabric_meta,
        "cropped": cropped_meta,
        "raw": raw_meta,
        "status_record": status_entry,
        "fabric_status_record": fabric_status_entry,
    }


def fabric_progress(project_dir, config, folder_filter=None):
    """Return folder-aware SKU progress; complete means both seamless and swatch exist."""
    mode, local_dir, _, _ = source_settings(project_dir, config)
    output_name = str(config.get("seamless_package", {}).get("filename", "seamless_texture.png")).strip()
    raw_root = resolve_project_path(project_dir, "textures_raw")
    crop_root = resolve_project_path(project_dir, "textures_cropped")
    tex_root = resolve_project_path(project_dir, "textures")
    # Never reuse the mutable, folder-scoped output_dir written by apply_folder_config().
    out_root = resolve_project_path(project_dir, "output/chatgpt")
    is_all = not folder_filter or folder_filter in {"all", "Mặc định", ""}
    selected_folder = "" if is_all else str(folder_filter).strip().replace("/", "\\")
    sku_keys = set()

    def add_source_file(path, root, texture_prefix=False):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
            return
        sku = path.stem
        if texture_prefix and sku.startswith("texture_"):
            sku = sku[8:]
        try:
            relative_parent = path.parent.relative_to(root)
            folder = "" if str(relative_parent) == "." else relative_parent.parts[0]
        except ValueError:
            folder = ""
        if not is_all and folder.casefold() != selected_folder.casefold():
            return
        sku_keys.add((folder, sku))

    if mode == "local" and local_dir:
        if safe_is_dir(local_dir):
            for path in local_dir.rglob("*"):
                if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
                    sku_keys.add(("", path.stem))
        raw_dir = local_dir
        out_dir = out_root
    else:
        for root, texture_prefix in ((raw_root, False), (crop_root, False), (tex_root, True)):
            if safe_is_dir(root):
                for path in root.rglob("*"):
                    add_source_file(path, root, texture_prefix=texture_prefix)
        raw_dir = raw_root if is_all else raw_root / selected_folder
        out_dir = out_root if is_all else out_root / selected_folder

    if safe_is_dir(out_root):
        for first in out_root.iterdir():
            if not first.is_dir():
                continue
            if safe_is_file(first / output_name) or safe_is_file(first / "image_1.png"):
                if is_all:
                    sku_keys.add(("", first.name))
            for sku_dir in first.iterdir():
                if not sku_dir.is_dir():
                    continue
                if is_all or first.name.casefold() == selected_folder.casefold():
                    sku_keys.add((first.name, sku_dir.name))

    created = []
    pending = []
    for folder, sku in sorted(sku_keys, key=lambda value: (value[0].casefold(), value[1].casefold())):
        sku_dir = out_root / folder / sku if folder else out_root / sku
        has_seamless = safe_is_file(sku_dir / output_name)
        has_fabric = safe_is_file(sku_dir / "image_1.png")
        item = {
            "sku": sku,
            "folder": folder,
            "has_seamless": has_seamless,
            "has_fabric": has_fabric,
        }
        if has_seamless and has_fabric:
            created.append(item)
        else:
            pending.append(item)

    total = len(created) + len(pending)
    percent = round(len(created) * 100 / total) if total else 0
    return {
        "source_mode": mode,
        "local_source_dir": str(local_dir) if local_dir else "",
        "source_dir": str(raw_dir),
        "output_dir": str(out_dir),
        "folder_filter": folder_filter or "all",
        "source_exists": safe_is_dir(raw_dir),
        "total": total,
        "created_count": len(created),
        "pending_count": len(pending),
        "percent": percent,
        "created": created,
        "pending": pending,
    }


def choose_local_folder(initial_dir=None):
    """Open a modern Explorer-style picker with address bar and search."""
    initial = str(Path(initial_dir).resolve()) if initial_dir and safe_is_dir(initial_dir) else ""
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$owner=New-Object System.Windows.Forms.Form;"
        "$owner.ShowInTaskbar=$false;"
        "$owner.TopMost=$true;"
        "$owner.StartPosition='CenterScreen';"
        "$owner.Size=New-Object System.Drawing.Size(1,1);"
        "$owner.Opacity=0;"
        "$owner.Show();$owner.Activate();"
        "$dialog=New-Object System.Windows.Forms.OpenFileDialog;"
        "$dialog.Title='Chọn thư mục ảnh vải';"
        "$dialog.Filter='Thư mục|*.folder';"
        "$dialog.CheckFileExists=$false;$dialog.CheckPathExists=$true;"
        "$dialog.ValidateNames=$false;$dialog.DereferenceLinks=$true;"
        "$dialog.FileName='__Chọn thư mục này__';"
        "if($args[0]){$dialog.InitialDirectory=$args[0]};"
        "try{if($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK){"
        "$selected=$dialog.FileName;"
        "if(-not [IO.Directory]::Exists($selected)){$selected=[IO.Path]::GetDirectoryName($selected)};"
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8;Write-Output $selected}}"
        "finally{$owner.Close();$owner.Dispose()}"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-STA", "-Command", script, initial],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "Không mở được cửa sổ chọn folder.")
    selected = completed.stdout.strip()
    return str(Path(selected).resolve()) if selected else ""


def quality_path_sort_key(path):
    """Keep each fabric code together and sort numeric suffixes naturally."""
    return tuple(
        tuple((1, int(part)) if part.isdigit() else (0, part.casefold())
              for part in re.split(r"(\d+)", component))
        for component in Path(path).parts
    )


def list_quality_images(folder):
    """Return supported images below a user-selected quality-test folder."""
    root = Path(folder).resolve()
    if not safe_is_dir(root):
        raise ValueError("Folder vải không tồn tại hoặc không thể đọc.")
    images = []
    for path in sorted(root.rglob("*"), key=lambda item: quality_path_sort_key(item.relative_to(root))):
        if (
            path.is_file()
            and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
            and path.name.casefold() not in QUALITY_QC_FILENAMES
        ):
            images.append(
                {
                    "name": path.name,
                    "path": str(path.resolve()),
                    "relative_path": str(path.relative_to(root)).replace("\\", "/"),
                }
            )
    return images


def list_quality_folder_groups(folder):
    """List immediate child folders and the images belonging to each child."""
    root = Path(folder).resolve()
    if not safe_is_dir(root):
        raise ValueError("Folder vải không tồn tại hoặc không thể đọc.")
    children = sorted(
        (path for path in root.iterdir() if path.is_dir()),
        key=lambda item: quality_path_sort_key(item.name),
    )
    groups = []
    for child in children:
        images = list_quality_images(child)
        groups.append({"name": child.name, "path": str(child.resolve()), "images": images})
    if children:
        images = [
            {"name": path.name, "path": str(path.resolve()), "relative_path": path.name}
            for path in sorted(root.iterdir(), key=lambda item: quality_path_sort_key(item.name))
            if (
                path.is_file()
                and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
                and path.name.casefold() not in QUALITY_QC_FILENAMES
            )
        ]
        if images:
            groups.append({"name": root.name + " (ảnh trực tiếp)", "path": str(root), "images": images})
    else:
        images = list_quality_images(root)
        if images:
            groups.append({"name": root.name, "path": str(root), "images": images})
    return groups


def choose_prompt_file(initial_path=None):
    """Open the native file picker for selecting a markdown/text prompt document."""
    initial_dir = ""
    if initial_path:
        path = Path(initial_path)
        if safe_is_file(path):
            initial_dir = str(path.resolve().parent)
        elif safe_is_dir(path):
            initial_dir = str(path.resolve())
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$owner=New-Object System.Windows.Forms.Form;"
        "$owner.ShowInTaskbar=$false;"
        "$owner.TopMost=$true;"
        "$owner.StartPosition='CenterScreen';"
        "$owner.Size=New-Object System.Drawing.Size(1,1);"
        "$owner.Opacity=0;"
        "$owner.Show();$owner.Activate();"
        "$dialog=New-Object System.Windows.Forms.OpenFileDialog;"
        "$dialog.Title='Chọn file Master Prompt';"
        "$dialog.Filter='Markdown and Text (*.md;*.txt;*.markdown)|*.md;*.txt;*.markdown|All files (*.*)|*.*';"
        "if($args[0]){$dialog.InitialDirectory=$args[0]};"
        "try{if($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK){"
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8;Write-Output $dialog.FileName}}"
        "finally{$owner.Close();$owner.Dispose()}"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-STA", "-Command", script, initial_dir],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "Không mở được cửa sổ chọn file prompt.")
    selected = completed.stdout.strip()
    return str(Path(selected).resolve()) if selected else ""


def get_default_prompt_text(project_dir, flow_key):
    """Read default master prompt content from template files."""
    flow = str(flow_key).strip().lower()
    if flow in {"seamless", "texture"}:
        rel = "prompts/prompts_attachments/01_TEXTURE_SEAMLESS_MASTER.md"
    elif flow in {"fabric", "swatch"}:
        rel = "prompts/prompts_attachments/02_FABRIC_SWATCH_MASTER.md"
    elif flow in {"flow", "google_flow", "flow_texture"}:
        rel = "prompts/scanned_to_texture_prompt.md"
    else:
        raise ValueError(f"Không hỗ trợ luồng: {flow_key}")
    target = resolve_project_path(project_dir, rel)
    if not safe_is_file(target):
        raise ValueError(f"File prompt mẫu không tồn tại: {target}")
    return target.read_text(encoding="utf-8").strip()


def locate_project_dir():
    if is_frozen():
        executable_dir = Path(sys.executable).resolve().parent
        if (executable_dir / "config.json").is_file():
            return executable_dir
        for parent in (executable_dir, *executable_dir.parents):
            if (
                (parent / "config.json").is_file()
                and (parent / "veo3_auto_app.py").is_file()
            ):
                return parent
        return executable_dir
    else:
        candidates = (Path(__file__).resolve().parent, Path.cwd())
    for candidate in candidates:
        if valid_project_dir(candidate):
            return candidate.resolve()
    return Path(candidates[0]).resolve()


def locate_python(project_dir):
    if is_frozen():
        return Path(sys.executable).resolve()
    if not is_frozen() and Path(sys.executable).is_file():
        return Path(sys.executable).resolve()
    candidates = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        root = Path(local_app_data) / "Programs" / "Python"
        if safe_is_dir(root):
            candidates.extend(
                sorted(
                    root.glob("Python3*/python.exe"),
                    key=lambda path: path.parent.name,
                    reverse=True,
                )
            )
    project_dir = Path(project_dir)
    candidates.extend(
        (
            project_dir / ".venv" / "Scripts" / "python.exe",
            project_dir / "venv" / "Scripts" / "python.exe",
        )
    )
    path_python = shutil.which("python.exe") or shutil.which("python")
    if path_python:
        candidates.append(Path(path_python))
    return next((Path(path).resolve() for path in candidates if safe_is_file(path)), None)


def build_step_command(python_exe, project_dir, step, arguments):
    if is_frozen():
        return [
            str(Path(sys.executable).resolve()),
            EMBEDDED_WORKER_FLAG,
            step.script,
            *arguments,
        ]
    return [
        str(python_exe),
        "-u",
        str(Path(project_dir) / step.script),
        *arguments,
    ]


def restore_worker_streams():
    """Reconnect stdout/stderr when a windowed EXE is launched with pipes."""
    for attribute, descriptor in (("stdout", 1), ("stderr", 2)):
        if getattr(sys, attribute) is not None:
            continue
        try:
            stream = open(
                os.dup(descriptor),
                "w",
                encoding="utf-8",
                errors="replace",
                buffering=1,
                closefd=True,
            )
        except OSError:
            stream = open(os.devnull, "w", encoding="utf-8")
        setattr(sys, attribute, stream)


def run_embedded_worker(script_name, arguments):
    """Run one bundled pipeline module inside a child copy of this EXE."""
    restore_worker_streams()
    os.environ[EMBEDDED_WORKER_ENV] = "1"
    project_dir = Path(
        os.environ.get(PROJECT_DIR_ENV, "") or locate_project_dir()
    ).resolve()
    os.environ[PROJECT_DIR_ENV] = str(project_dir)
    ensure_runtime_layout(project_dir)
    sys.argv = [script_name, *arguments]
    script_name = Path(script_name).name

    if script_name == "import_google_drive.py":
        import import_google_drive as worker_module
    elif script_name == "crop_textures.py":
        import crop_textures as worker_module
    elif script_name == "run_chatgpt_texture_grouped_batch.py":
        import run_chatgpt_texture_grouped_batch as worker_module
    elif script_name == "run_chatgpt_fabric_grouped_batch.py":
        import run_chatgpt_fabric_grouped_batch as worker_module
    elif script_name == "run_flow_texture_batch.py":
        import run_flow_texture_batch as worker_module
    elif script_name == "package_seamless_textures.py":
        import package_seamless_textures as worker_module
    elif script_name == "run_algorithm_seamless_batch.py":
        import run_algorithm_seamless_batch as worker_module
    else:
        raise ValueError(f"Unsupported embedded worker: {script_name}")
    result = worker_module.main()
    return int(result) if isinstance(result, int) else 0


def run_embedded_gdown(arguments):
    restore_worker_streams()
    from gdown.__main__ import main as gdown_main

    sys.argv = ["gdown", *arguments]
    result = gdown_main()
    return int(result) if isinstance(result, int) else 0


def load_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"File phải chứa JSON object: {path}")
    return value


def save_json_atomic(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(temporary, path)


def validate_drive_url(value):
    parsed = urlparse(str(value).strip())
    return (
        parsed.scheme == "https"
        and parsed.netloc.lower() == "drive.google.com"
        and "/folders/" in parsed.path
    )


def drive_folder_id(value):
    """Return the stable Drive folder ID, ignoring query-string variants."""
    parsed = urlparse(str(value or "").strip())
    match = re.search(r"/folders/([^/?#]+)", parsed.path, flags=re.IGNORECASE)
    return match.group(1) if match else ""


def deduplicate_drive_items(items):
    """Keep the first configured item for each Drive folder ID."""
    unique = []
    seen_ids = set()
    for item in items if isinstance(items, list) else []:
        url = str(item.get("url", "")).strip() if isinstance(item, dict) else ""
        folder_id = drive_folder_id(url)
        if not url or not folder_id or folder_id in seen_ids:
            continue
        seen_ids.add(folder_id)
        unique.append(item)
    return unique


def find_chrome():
    candidates = (
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Google/Chrome/Application/chrome.exe",
    )
    return next((path for path in candidates if safe_is_file(path)), None)


def extract_chatgpt_quota_info(text):
    """
    Detect if ChatGPT image creation quota is exhausted and extract wait / reset time if present.
    Returns (is_quota: bool, reset_info: str | None, message: str | None)
    """
    if not text:
        return False, None, None

    lower_text = text.lower()
    quota_markers = (
        "you're out of images",
        "you are out of images",
        "you're out of image creations",
        "you are out of image creations",
        "you've reached your image creation limit",
        "you've reached your image generation limit",
        "you've reached your limit",
        "you have reached your limit",
        "reached the current limit for image",
        "reached your limit for creating images",
        "image generation limit",
        "image creations limit",
        "hạn mức tạo ảnh đã hết",
        "hạn mức sẽ được đặt lại sau",
        "không thể tạo ảnh vì hạn mức",
        "bạn đã dùng hết số lượt tạo ảnh",
        "bạn đã dùng hết hạn mức",
        "bạn đã đạt giới hạn",
        "bạn đã đạt đến hạn mức",
        "đã hết lượt tạo ảnh",
        "upgrade your plan to continue, or wait for more",
    )
    is_quota = any(marker in lower_text for marker in quota_markers)
    if not is_quota:
        return False, None, None

    parts = []

    # 1. Match Vietnamese duration e.g. "sau khoảng 6 giờ 44 phút" / "sau 45 phút"
    vn_dur_match = re.search(
        r'(?:đặt lại sau khoảng|đặt lại sau|thử lại sau khoảng|thử lại sau|sau khoảng|sau)\s*([0-9]+\s*(?:giờ|tiếng|phút|giây|h|m|s)(?:\s*(?:và\s*)?[0-9]+\s*(?:phút|giây|m|s))?)',
        text,
        re.IGNORECASE,
    )
    if vn_dur_match:
        dur_str = vn_dur_match.group(1).strip()
        parts.append(f"sau khoảng {dur_str}")

    # 2. Match English duration e.g. "in 3 hours and 15 minutes" / "in 45 minutes"
    if not vn_dur_match:
        en_dur_match = re.search(
            r'(?:try again in|resets? in|wait for|in)\s+([0-9]+\s*(?:hours?|hrs?|minutes?|mins?|secs?)(?:\s*(?:and\s*)?[0-9]+\s*(?:minutes?|mins?|secs?))?)',
            text,
            re.IGNORECASE,
        )
        if en_dur_match:
            dur_str = en_dur_match.group(1).strip()
            parts.append(f"sau {dur_str}")

    # 3. Match Clock time e.g. "wait for more at 4:17 PM" / "after 12:30 PM" / "resets at 16:30" / "lúc 16:30"
    time_match = re.search(
        r'(?:wait for more at|try again at|resets? at|continue after|after|vào lúc|lúc)\s+([0-9]{1,2}:[0-9]{2}(?:\s*[AP]M)?)',
        text,
        re.IGNORECASE,
    )
    if time_match:
        clock_str = time_match.group(1).strip()
        parts.append(f"lúc {clock_str}")

    if parts:
        reset_info = " (hoặc ".join(parts) + (")" if len(parts) > 1 else "")
        message = f"Hết hạn mức tạo ảnh ChatGPT! Thời gian chờ / đặt lại: {reset_info}."
    else:
        reset_info = None
        message = "Hết hạn mức tạo ảnh ChatGPT! Hãy đợi hệ thống đặt lại hạn mức."

    return True, reset_info, message


def send_telegram_message(bot_token, chat_id, text, parse_mode="HTML"):
    """Send message via Telegram Bot API using Python standard library urllib."""
    if not bot_token or not chat_id:
        return False, "Thiếu Bot Token hoặc Chat ID."
    
    url = f"https://api.telegram.org/bot{bot_token.strip()}/sendMessage"
    payload = {
        "chat_id": str(chat_id).strip(),
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            res = json.loads(body)
            if res.get("ok"):
                return True, None
            return False, res.get("description", "Lỗi không xác định từ Telegram")
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        try:
            err_json = json.loads(err_body)
            return False, err_json.get("description", str(exc))
        except Exception:
            return False, f"HTTP Error {exc.code}: {exc.reason}"
    except Exception as exc:
        return False, str(exc)


class PipelineController:
    def __init__(self):
        self.project_dir = locate_project_dir()
        ensure_runtime_layout(self.project_dir)
        self.python_exe = locate_python(self.project_dir)
        self.lock = threading.RLock()
        self.log_text = ""
        self.status = "Sẵn sàng"
        self.quota_alert = None
        self.worker = None
        self.process = None
        self.stop_escalation_thread = None
        self.stop_requested = threading.Event()
        self.server = None
        self.last_client_at = time.monotonic()
        self.active_running_folder = None
        self.current_folder_filter = "all"
        self.quality_folders = set()

        # Session Metrics
        self.session_running = False
        self.session_started_monotonic = None
        self.session_ended_monotonic = None
        self.session_started_at_str = None
        self.session_ended_at_str = None
        self.session_start_created_count = 0

        self.telegram_polling_active = False
        self.telegram_poller_thread = None
        self.telegram_offset = 0
        self.start_telegram_poller()

    @property
    def config_path(self):
        return self.project_dir / "config.json"

    def append_log(self, text):
        with self.lock:
            self.log_text = (self.log_text + str(text))[-MAX_LOG_CHARS:]
            if "CẢNH BÁO QUOTA CHATGPT" in text or "CHATGPT_QUOTA_EXHAUSTED" in text:
                is_quota, reset_info, quota_msg = extract_chatgpt_quota_info(text)
                if is_quota:
                    self.quota_alert = {
                        "message": quota_msg,
                        "reset_info": reset_info,
                        "detected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                    self.status = f"⚠️ Hết quota ChatGPT ({reset_info or 'Chờ reset'})"

    def set_status(self, value):
        with self.lock:
            self.status = str(value)

    def is_running(self):
        return bool(self.worker and self.worker.is_alive())

    def select_quality_folder(self):
        selected = choose_local_folder()
        if not selected:
            return {"path": "", "folders": []}
        root = Path(selected).resolve()
        folders = list_quality_folder_groups(root)
        with self.lock:
            self.quality_folders.add(root)
            failures = self.read_quality_failures()["images"]
            for group in folders:
                for image in group["images"]:
                    image["failed"] = os.path.normcase(image["path"]) in failures
        return {"path": str(root), "folders": folders}

    def read_quality_failures(self):
        path = self.project_dir / "failed_image_logs" / "failed_image_ids.json"
        if not path.exists():
            path = self.project_dir / "failed_image_ids.json"
        if not path.exists():
            return {"version": 1, "images": {}}
        # Do not overwrite unreadable or incompatible existing records.
        with path.open("r", encoding="utf-8") as stream:
            data = json.load(stream)
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("images"), dict):
            raise ValueError("failed_image_ids.json không đúng định dạng.")
        return data

    def set_quality_image_failure(self, payload):
        if not isinstance(payload.get("failed"), bool):
            raise ValueError("Trạng thái fail phải là true hoặc false.")
        image = self.resolve_quality_image(payload.get("image_path"))
        failed = payload["failed"]
        key = os.path.normcase(str(image))
        with self.lock:
            data = self.read_quality_failures()
            if failed:
                root = max((root for root in self.quality_folders if root in image.parents), key=lambda root: len(root.parts))
                data["images"][key] = {
                    "image_path": str(image),
                    "file_name": image.name,
                    "image_directory": str(image.parent),
                    "selected_root": str(root),
                    "relative_path": image.relative_to(root).as_posix(),
                    "marked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                }
            else:
                data["images"].pop(key, None)
            log_dir = self.project_dir / "failed_image_logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            save_json_atomic(log_dir / "failed_image_ids.json", data)
        return {"image_path": str(image), "failed": failed}

    def prepare_quality_rerun(self, payload):
        folder = Path(str(payload.get("folder", ""))).resolve()
        paths = payload.get("image_paths")
        if not isinstance(paths, list) or not paths:
            raise ValueError("Hãy chọn ít nhất một ảnh fail.")
        failures = self.read_quality_failures()["images"]
        jobs = {}
        for value in paths:
            image = self.resolve_quality_image(value)
            if folder not in image.parents or os.path.normcase(str(image)) not in failures:
                raise ValueError("Chỉ được chọn ảnh fail trong folder đang xem.")
            data_root = next((parent for parent in image.parents if (parent / "textures_raw").is_dir() and (parent / "output") in image.parents), None)
            if data_root is None:
                raise ValueError(f"Không tìm thấy textures_raw tương ứng với {image}. Cần bộ thư mục nguồn và output cùng gốc.")
            relative = image.relative_to(data_root / "output")
            if len(relative.parts) != 4:
                raise ValueError(f"Không xác định được nhóm/SKU từ {image}; cần output/<engine>/<nhóm>/<SKU>/<ảnh>.")
            engine, group, sku, _ = relative.parts
            raw_dir = data_root / "textures_raw" / group
            if not any(path.is_file() and path.stem == sku and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS for path in raw_dir.glob("*")):
                raise ValueError(f"Không có ảnh nguồn cho SKU {sku} trong {raw_dir}.")
            key = str(image.parent)
            jobs[key] = {"folder": group, "sku": sku, "data_root": data_root,
                         "output_dir": self.project_dir.resolve() / "output" / "chatgpt" / group}
        return list(jobs.values())

    def rerun_quality_images(self, payload):
        with self.lock:
            if self.is_running():
                raise ValueError("Một tiến trình đang chạy. Hãy chờ hoàn tất trước khi tạo lại ảnh.")
            jobs = self.prepare_quality_rerun(payload)
            first_job = jobs[0]
            if any(
                job["folder"] != first_job["folder"] or job["data_root"] != first_job["data_root"]
                for job in jobs[1:]
            ):
                raise ValueError("Các ảnh tạo lại phải thuộc cùng một nhóm dữ liệu để dùng chung một chat.")
            run_payload = {"engine": "chatgpt", "source_mode": "local", "force": True,
                           "local_source_dir": str(first_job["data_root"] / "textures_raw" / first_job["folder"]),
                           "images_per_chat": len(jobs), "limit": str(len(jobs)), "auto_retry_enabled": False,
                           "flows": {"import": False, "crop": True, "seamless": True, "fabric": False, "package": True}}
            self.validate_run(run_payload)
            config = load_json(self.config_path)
            run_dir = self.project_dir / "failed_image_logs" / datetime.datetime.now().strftime("run_%Y%m%d_%H%M%S_%f")
            runtime = run_dir / "0"
            runtime.mkdir(parents=True)
            if (self.project_dir / "prompts").is_dir():
                shutil.copytree(self.project_dir / "prompts", runtime / "prompts")
            settings = json.loads(json.dumps(config))
            for section in settings.values():
                if isinstance(section, dict):
                    for name in ("master_prompt_file", "prompt_file", "user_data_dir"):
                        if section.get(name):
                            section[name] = str(resolve_project_path(self.project_dir, os.path.expandvars(section[name])))
            root, group = first_job["data_root"], first_job["folder"]
            raw, cropped = str(root / "textures_raw" / group), str(root / "textures_cropped" / group)
            textures, output = str(root / "textures" / group), str(first_job["output_dir"])
            for section, values in {
                "crop": {"source_dir": raw, "output_dir": cropped},
                "chatgpt_texture_grouped": {"raw_dir": cropped, "textures_dir": textures},
                "chatgpt_texture": {"raw_dir": cropped},
                "paths": {"textures_dir": textures, "output_dir": output},
                "chatgpt": {"output_dir": output},
                "seamless_package": {"output_dir": output},
            }.items():
                settings.setdefault(section, {}).update(values)
            save_json_atomic(runtime / "config.json", settings)
            sku_file = runtime / "selected_skus.json"
            save_json_atomic(sku_file, [job["sku"] for job in jobs])
            run_payload["sku_file"] = str(sku_file)
            queue = [{"folder": group, "runtime_dir": str(runtime)}]
            self.stop_requested.clear()
            self.quota_alert = None
            self.log_text = ""
            self.append_log(f"[Quality] Tạo lại {len(queue)} SKU từ các ảnh fail đã chọn.\n")
            for job in jobs:
                self.append_log(f"[Quality] Output {job['sku']}: {job['output_dir'] / job['sku']}\n")
            self.worker = threading.Thread(
                target=self.run_quality_queue,
                args=(queue, run_payload, run_dir),
                daemon=True,
            )
            self.worker.start()
        return {"ok": True, "sku_count": len(jobs)}

    def run_quality_queue(self, queue, payload, run_dir):
        """Run failed-image jobs and always remove their isolated runtime data."""
        try:
            self.run_queue(queue, payload)
        finally:
            runtime = Path(run_dir).resolve()
            log_root = (self.project_dir / "failed_image_logs").resolve()
            is_quality_runtime = (
                runtime.parent == log_root
                and re.fullmatch(r"run_\d{8}_\d{6}_\d{6}", runtime.name) is not None
            )
            if not is_quality_runtime:
                self.append_log(
                    f"[WARNING] Không dọn runtime Quality ngoài phạm vi an toàn: {runtime}\n"
                )
            else:
                try:
                    shutil.rmtree(runtime)
                    self.append_log(f"[Quality] Đã dọn dữ liệu chạy tạm: {runtime.name}\n")
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    self.append_log(
                        f"[WARNING] Không thể dọn dữ liệu chạy tạm {runtime.name}: {exc}\n"
                    )

    def resolve_quality_image(self, image_path):
        candidate = Path(str(image_path or "")).resolve()
        with self.lock:
            roots = tuple(self.quality_folders)
        if not any(candidate == root or root in candidate.parents for root in roots):
            raise ValueError("Ảnh không thuộc folder vải đã chọn trong phiên hiện tại.")
        if not candidate.is_file() or candidate.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
            raise ValueError("File ảnh không tồn tại hoặc không được hỗ trợ.")
        return candidate

    def upload_quality_image(self, payload):
        target_url = str(payload.get("target_url", "")).strip()
        image = self.resolve_quality_image(payload.get("image_path"))
        from quality_output_uploader import upload_image_to_target

        self.append_log(f"[Quality] Đang gửi {image.name} tới {target_url}\n")
        result = upload_image_to_target(self.project_dir, target_url, image)
        self.append_log(f"[Quality] Đã đưa {image.name} vào Choose File: {result['target_url']}\n")
        return result

    def state(self, folder_filter=None):
        self.last_client_at = time.monotonic()
        drive_url = ""
        drive_urls = []
        images_per_chat = 10
        texture_prompt_mode = "attachment"
        texture_prompt_file = "prompts/prompts_attachments/01_TEXTURE_SEAMLESS_MASTER.md"
        texture_prompt_text = ""
        fabric_prompt_mode = "attachment"
        fabric_prompt_file = "prompts/prompts_attachments/02_FABRIC_SWATCH_MASTER.md"
        fabric_prompt_text = ""
        flow_prompt_mode = "attachment"
        flow_prompt_file = "prompts/scanned_to_texture_prompt.md"
        flow_prompt_text = ""
        try:
            config = load_json(self.config_path)
            drive_url = str(config.get("google_drive", {}).get("share_url", ""))
            drive_urls = deduplicate_drive_items(config.get("google_drive", {}).get("urls", []))
            images_per_chat = int(
                config.get("chatgpt_texture_grouped", {}).get("images_per_chat", 10)
            )
            app_ui = config.get("app_ui", {})
            tex_cfg = config.get("chatgpt_texture_grouped", {})
            fab_cfg = config.get("chatgpt_fabric_grouped", {})
            flow_cfg = config.get("flow_texture", {})
            texture_prompt_mode = str(tex_cfg.get("prompt_mode", "attachment")).lower()
            texture_prompt_file = str(tex_cfg.get("master_prompt_file", "prompts/prompts_attachments/01_TEXTURE_SEAMLESS_MASTER.md"))
            texture_prompt_text = str(tex_cfg.get("prompt_text", ""))
            fabric_prompt_mode = str(fab_cfg.get("prompt_mode", "attachment")).lower()
            fabric_prompt_file = str(fab_cfg.get("master_prompt_file", "prompts/prompts_attachments/02_FABRIC_SWATCH_MASTER.md"))
            fabric_prompt_text = str(fab_cfg.get("prompt_text", ""))
            flow_prompt_mode = str(flow_cfg.get("prompt_mode", "attachment")).lower()
            flow_prompt_file = str(flow_cfg.get("prompt_file", "prompts/scanned_to_texture_prompt.md"))
            flow_prompt_text = str(flow_cfg.get("prompt_text", ""))
        except Exception as exc:
            self.append_log(f"[ERROR] Không đọc được config.json: {exc}\n")
            app_ui = {}

        if folder_filter is not None:
            self.current_folder_filter = folder_filter

        progress = fabric_progress(self.project_dir, config, folder_filter=self.current_folder_filter)
        folders_stats = compute_drive_folders_stats(self.project_dir, config, self.active_running_folder)

        # Timing calculations
        historical_stats = compute_historical_timing_stats(self.project_dir)
        historical_avg = historical_stats.get("avg_duration_seconds", 80.0)

        session_duration = 0.0
        if self.session_started_monotonic is not None:
            if self.session_running:
                session_duration = time.monotonic() - self.session_started_monotonic
            elif self.session_ended_monotonic is not None:
                session_duration = (
                    self.session_ended_monotonic - self.session_started_monotonic
                )

        current_created = progress.get("created_count", 0)
        session_created_count = max(
            0, current_created - self.session_start_created_count
        )
        session_avg = (
            (session_duration / session_created_count)
            if session_created_count > 0
            else 0.0
        )

        effective_avg = (
            session_avg
            if (session_created_count >= 2 and session_avg >= 15)
            else historical_avg
        )
        pending_count = progress.get("pending_count", 0)
        eta_seconds = (
            round(pending_count * effective_avg) if pending_count > 0 else 0
        )

        timing_stats = {
            "session_running": self.session_running,
            "session_duration_seconds": round(session_duration, 1),
            "session_started_at": self.session_started_at_str,
            "session_created_count": session_created_count,
            "session_avg_seconds": round(session_avg, 1),
            "historical_avg_seconds": round(historical_avg, 1),
            "effective_avg_seconds": round(effective_avg, 1),
            "eta_seconds": eta_seconds,
        }

        quota_alert = self.quota_alert
        if not quota_alert:
            for sfile in (
                self.project_dir / "status_chatgpt_texture_grouped.json",
                self.project_dir / "status_chatgpt_fabric_grouped.json",
            ):
                if safe_is_file(sfile):
                    try:
                        sdata = load_json(sfile)
                        for v in sdata.values():
                            if isinstance(v, dict) and v.get("error_type") == "quota_limit":
                                q_msg = v.get("quota_message") or "Đã hết hạn mức tạo ảnh ChatGPT."
                                q_reset = v.get("quota_reset_info")
                                quota_alert = {
                                    "message": q_msg,
                                    "reset_info": q_reset,
                                }
                                break
                        if quota_alert:
                            break
                    except Exception:
                        pass

        tele_cfg = config.get("telegram", {})
        telegram_state = {
            "enabled": bool(tele_cfg.get("enabled", False)),
            "bot_token": str(tele_cfg.get("bot_token", "")),
            "chat_id": str(tele_cfg.get("chat_id", "")),
            "notify_on_quota": bool(tele_cfg.get("notify_on_quota", True)),
            "notify_on_complete": bool(tele_cfg.get("notify_on_complete", True)),
            "notify_on_safe_stop": bool(tele_cfg.get("notify_on_safe_stop", True)),
            "notify_on_folder_complete": bool(tele_cfg.get("notify_on_folder_complete", True)),
        }

        out_root = resolve_project_path(self.project_dir, "output")
        output_children = [d.name for d in out_root.iterdir() if d.is_dir()] if safe_is_dir(out_root) else ["chatgpt"]

        with self.lock:
            return {
                "project_dir": str(self.project_dir),
                "python_exe": str(self.python_exe) if self.python_exe else None,
                "drive_url": drive_url,
                "drive_urls": drive_urls,
                "drive_folders_stats": folders_stats,
                "drive_sync_recent": folders_stats.get("recent_sync_history", []),
                "output_subdirectories": output_children,
                "active_running_folder": self.active_running_folder,
                "images_per_chat": images_per_chat,
                "auto_retry_enabled": bool(app_ui.get("auto_retry_enabled", True)),
                "auto_retry_delay_seconds": int(app_ui.get("auto_retry_delay_seconds", 120)),
                "auto_retry_max_attempts": int(app_ui.get("auto_retry_max_attempts", 10)),
                "texture_prompt_mode": texture_prompt_mode,
                "texture_prompt_file": texture_prompt_file,
                "texture_prompt_text": texture_prompt_text,
                "fabric_prompt_mode": fabric_prompt_mode,
                "fabric_prompt_file": fabric_prompt_file,
                "fabric_prompt_text": fabric_prompt_text,
                "flow_prompt_mode": flow_prompt_mode,
                "flow_prompt_file": flow_prompt_file,
                "flow_prompt_text": flow_prompt_text,
                "source_mode": progress["source_mode"],
                "local_source_dir": progress["local_source_dir"],
                "fabric_progress": progress,
                "timing_stats": timing_stats,
                "quota_alert": quota_alert,
                "telegram": telegram_state,
                "status": self.status,
                "running": self.is_running(),
                "log": self.log_text,
            }

    def save_settings(self, payload):
        source_mode = str(payload.get("source_mode", "drive")).strip().lower()
        if source_mode not in {"drive", "local"}:
            raise ValueError("Nguồn ảnh phải là Google Drive hoặc thư mục local.")
        
        drive_urls = payload.get("drive_urls", [])
        if not isinstance(drive_urls, list):
            drive_urls = []

        existing_config = load_json(self.config_path)
        existing_modified = {
            drive_folder_id(item.get("url", "")): str(item.get("modified_at", "")).strip()
            for item in existing_config.get("google_drive", {}).get("urls", [])
            if isinstance(item, dict) and drive_folder_id(item.get("url", ""))
        }
        
        valid_urls = []
        for item in drive_urls:
            url = str(item.get("url", "")).strip()
            if url:
                if not validate_drive_url(url):
                    raise ValueError("Tất cả link Drive phải có dạng https://drive.google.com/drive/folders/...")
                folder_name = str(item.get("folder", "")).strip().replace("/", "\\")
                modified_at = str(item.get("modified_at", "")).strip()
                if not modified_at:
                    modified_at = existing_modified.get(drive_folder_id(url), "")
                valid_item = {"url": url, "folder": folder_name}
                if modified_at:
                    valid_item["modified_at"] = modified_at
                valid_urls.append(valid_item)
        duplicate_count = len(valid_urls) - len(deduplicate_drive_items(valid_urls))
        valid_urls = deduplicate_drive_items(valid_urls)
        if duplicate_count:
            self.append_log(
                f"[Drive] Đã bỏ qua {duplicate_count} link trùng Folder ID; mỗi thư mục chỉ được lưu một lần.\n"
            )
        
        drive_url = valid_urls[0]["url"] if valid_urls else ""
        local_value = str(payload.get("local_source_dir", "")).strip()
        local_dir = None
        if source_mode == "local":
            if not local_value:
                raise ValueError("Hãy chọn thư mục ảnh vải local.")
            local_dir = resolve_project_path(self.project_dir, local_value).resolve()
            if not safe_is_dir(local_dir):
                raise ValueError(f"Thư mục local không tồn tại: {local_dir}")
        images_per_chat = int(payload.get("images_per_chat", 10))
        if not 1 <= images_per_chat <= 20:
            raise ValueError("Ảnh mỗi chat phải nằm trong khoảng 1..20.")

        auto_retry_enabled = bool(payload.get("auto_retry_enabled", True))
        auto_retry_delay = int(payload.get("auto_retry_delay_seconds", 120))
        auto_retry_max = int(payload.get("auto_retry_max_attempts", 10))
        if auto_retry_delay < 5:
            auto_retry_delay = 5
        if auto_retry_max < 0:
            auto_retry_max = 0

        config = load_json(self.config_path)
        drive = config.setdefault("google_drive", {})
        drive["share_url"] = drive_url
        drive["urls"] = valid_urls
        drive["enabled"] = bool(drive_url)
        app_ui = config.setdefault("app_ui", {})
        app_ui["source_mode"] = source_mode
        app_ui["auto_retry_enabled"] = auto_retry_enabled
        app_ui["auto_retry_delay_seconds"] = auto_retry_delay
        app_ui["auto_retry_max_attempts"] = auto_retry_max
        if local_dir:
            app_ui["local_source_dir"] = str(local_dir)
        elif local_value:
            app_ui["local_source_dir"] = local_value

        crop = config.setdefault("crop", {})
        crop["source_dir"] = (
            str(local_dir)
            if source_mode == "local"
            else str(drive.get("destination_dir", "textures_raw"))
        )

        tex_cfg = config.setdefault("chatgpt_texture_grouped", {})
        tex_cfg["images_per_chat"] = images_per_chat
        tex_mode = str(payload.get("texture_prompt_mode", "attachment")).strip().lower()
        if tex_mode in {"attachment", "manual"}:
            tex_cfg["prompt_mode"] = tex_mode
        if "texture_prompt_file" in payload:
            tex_cfg["master_prompt_file"] = str(payload["texture_prompt_file"]).strip()
        if "texture_prompt_text" in payload:
            tex_cfg["prompt_text"] = str(payload["texture_prompt_text"])

        fab_cfg = config.setdefault("chatgpt_fabric_grouped", {})
        fab_cfg["images_per_chat"] = images_per_chat
        fab_cfg["input_mode"] = "seamless"
        fab_cfg["raw_dir"] = str(
            config.get("paths", {}).get("output_dir", config.get("chatgpt", {}).get("output_dir", "output/chatgpt"))
        )
        fab_mode = str(payload.get("fabric_prompt_mode", "attachment")).strip().lower()
        if fab_mode in {"attachment", "manual"}:
            fab_cfg["prompt_mode"] = fab_mode
        if "fabric_prompt_file" in payload:
            fab_cfg["master_prompt_file"] = str(payload["fabric_prompt_file"]).strip()
        if "fabric_prompt_text" in payload:
            fab_cfg["prompt_text"] = str(payload["fabric_prompt_text"])

        flow_cfg = config.setdefault("flow_texture", {})
        flow_mode = str(payload.get("flow_prompt_mode", "attachment")).strip().lower()
        if flow_mode in {"attachment", "manual"}:
            flow_cfg["prompt_mode"] = flow_mode
        if "flow_prompt_file" in payload:
            flow_cfg["prompt_file"] = str(payload["flow_prompt_file"]).strip()
        if "flow_prompt_text" in payload:
            flow_cfg["prompt_text"] = str(payload["flow_prompt_text"])

        if "telegram" in payload and isinstance(payload["telegram"], dict):
            t_in = payload["telegram"]
            tele_cfg = config.setdefault("telegram", {})
            if "enabled" in t_in:
                tele_cfg["enabled"] = bool(t_in["enabled"])
            if "bot_token" in t_in:
                tele_cfg["bot_token"] = str(t_in["bot_token"]).strip()
            if "chat_id" in t_in:
                tele_cfg["chat_id"] = str(t_in["chat_id"]).strip()
            if "notify_on_quota" in t_in:
                tele_cfg["notify_on_quota"] = bool(t_in["notify_on_quota"])
            if "notify_on_complete" in t_in:
                tele_cfg["notify_on_complete"] = bool(t_in["notify_on_complete"])
            if "notify_on_safe_stop" in t_in:
                tele_cfg["notify_on_safe_stop"] = bool(t_in["notify_on_safe_stop"])
            if "notify_on_folder_complete" in t_in:
                tele_cfg["notify_on_folder_complete"] = bool(t_in["notify_on_folder_complete"])

        save_json_atomic(self.config_path, config)
        self.start_telegram_poller()
        source_label = str(local_dir) if local_dir else f"Google Drive ({len(valid_urls)} link)"
        self.append_log(f"Đã lưu cấu hình nguồn ảnh & prompt: {source_label}.\n")

    def validate_run(self, payload):
        if not valid_project_dir(self.project_dir):
            raise ValueError("Không tìm thấy config.json trong thư mục dữ liệu.")
        self.python_exe = locate_python(self.project_dir)
        if not self.python_exe:
            raise ValueError("Không thể khởi tạo bộ chạy pipeline.")

        engine = str(payload.get("engine", "chatgpt")).strip().lower()
        limit = str(payload.get("limit", "")).strip()
        if limit and (not limit.isdigit() or int(limit) < 1):
            raise ValueError("Giới hạn phải là số nguyên từ 1 trở lên.")

        if engine in {"flow", "google_flow", "algo", "algorithm", "cv_algorithm"}:
            return

        flows = payload.get("flows", {})
        source_mode = str(payload.get("source_mode", "drive")).strip().lower()
        if source_mode not in {"drive", "local"}:
            raise ValueError("Nguồn ảnh phải là Google Drive hoặc thư mục local.")
        selected_steps = [
            step
            for step in FLOW_STEPS
            if bool(flows.get(step.key))
            and not (source_mode == "local" and step.key == "import")
        ]
        if not selected_steps:
            raise ValueError("Hãy chọn ít nhất một luồng.")
        if source_mode == "local":
            local_dir = resolve_project_path(
                self.project_dir, payload.get("local_source_dir", "")
            )
            if not safe_is_dir(local_dir):
                raise ValueError("Hãy chọn một thư mục ảnh vải local đang tồn tại.")
        if source_mode == "drive" and flows.get("import"):
            drive_urls = payload.get("drive_urls", [])
            valid_urls = [i for i in drive_urls if str(i.get("url", "")).strip()]
            if not valid_urls:
                raise ValueError("Hãy nhập ít nhất một link thư mục Google Drive public hợp lệ.")
            for item in valid_urls:
                if not validate_drive_url(str(item.get("url", "")).strip()):
                    raise ValueError("Tất cả link Drive phải có dạng https://drive.google.com/drive/folders/...")

    def build_steps(self, payload, folder=None):
        common = []
        sku = str(payload.get("sku", "")).strip()
        sku_file = str(payload.get("sku_file", "")).strip()
        limit = str(payload.get("limit", "")).strip()
        if sku:
            common.extend(("--sku", sku))
        if sku_file:
            common.extend(("--sku-file", sku_file))
        if limit:
            common.extend(("--limit", limit))
        if bool(payload.get("dry_run")):
            common.append("--dry-run")
        force = bool(payload.get("force"))
        engine = str(payload.get("engine", "chatgpt")).strip().lower()

        if engine in {"algo", "algorithm", "cv_algorithm", "fix_seams"}:
            steps = []
            algo_step = FlowStep("fix_seams", "Vá lỗi Seamless (Efros-Freeman)", "run_algorithm_seamless_batch.py")
            arguments = ["--source-mode", "chatgpt", *common]
            if folder and str(folder).strip():
                arguments.extend(("--folder", str(folder).strip()))
            if force:
                arguments.append("--force")
            steps.append((algo_step, arguments))

            package_args = list(common)
            if folder and str(folder).strip():
                package_args.extend(("--folder", str(folder).strip()))
            if force:
                package_args.append("--force")
            steps.append((FlowStep("package", "Đóng gói SKU", "package_seamless_textures.py"), package_args))

            return steps

        if engine in {"flow", "google_flow"}:
            steps = []
            source_mode = str(payload.get("source_mode", "drive")).strip().lower()
            
            # 1. Import Google Drive if in Drive mode
            if source_mode == "drive":
                import_args = list(common)
                if folder and str(folder).strip():
                    import_args.extend(("--folder", str(folder).strip()))
                steps.append((FlowStep("import", "Import Drive", "import_google_drive.py"), import_args))

            # 2. Crop raw scans into cropped textures
            crop_args = list(common)
            if force:
                crop_args.append("--force")
            steps.append((FlowStep("crop", "Crop cố định", "crop_textures.py"), crop_args))

            # 3. Google Flow texture generation
            flow_step = FlowStep("flow_texture", "Tạo texture Google Flow", "run_flow_texture_batch.py")
            arguments = list(common)
            flow_mode = str(payload.get("flow_prompt_mode", "")).strip().lower()
            flow_file = str(payload.get("flow_prompt_file", "")).strip()
            flow_text = str(payload.get("flow_prompt_text", "")).strip()
            if flow_file:
                arguments.extend(("--prompt-file", flow_file))
            if flow_mode == "manual" and flow_text:
                arguments.extend(("--prompt-text", flow_text))
            if force:
                arguments.append("--force")
            steps.append((flow_step, arguments))

            # 4. Package seamless textures
            package_args = list(common)
            if folder and str(folder).strip():
                package_args.extend(("--folder", str(folder).strip()))
            if force:
                package_args.append("--force")
            steps.append((FlowStep("package", "Đóng gói SKU", "package_seamless_textures.py"), package_args))

            return steps

        flows = payload.get("flows", {})
        source_mode = str(payload.get("source_mode", "drive")).strip().lower()
        steps = []
        for step in FLOW_STEPS:
            if not bool(flows.get(step.key)):
                continue
            if source_mode == "local" and step.key == "import":
                continue
            arguments = list(common)
            if step.key == "import" and folder and str(folder).strip():
                arguments.extend(("--folder", str(folder).strip()))
            if step.key in {"seamless", "fabric"}:
                arguments.extend(
                    ("--images-per-chat", str(int(payload.get("images_per_chat", 10))))
                )
            if step.key == "seamless":
                seamless_engine = str(payload.get("seamless_engine", "")).strip().lower()
                if bool(payload.get("seamless_missing_only")):
                    seamless_engine = "chatgpt"
                if seamless_engine in {"algo", "algorithm", "cv"}:
                    algo_step = FlowStep("algo_seamless", "Tạo seamless Thuật toán", "run_algorithm_seamless_batch.py")
                    algo_args = list(common)
                    if folder and str(folder).strip():
                        algo_args.extend(("--folder", str(folder).strip()))
                    if force:
                        algo_args.append("--force")
                    steps.append((algo_step, algo_args))
                    continue
                tex_mode = str(payload.get("texture_prompt_mode", "")).strip().lower()
                tex_file = str(payload.get("texture_prompt_file", "")).strip()
                tex_text = str(payload.get("texture_prompt_text", "")).strip()
                if tex_mode in {"attachment", "manual"}:
                    arguments.extend(("--prompt-mode", tex_mode))
                if tex_file:
                    arguments.extend(("--prompt-file", tex_file))
                if tex_mode == "manual" and tex_text:
                    arguments.extend(("--prompt-text", tex_text))
            if step.key == "fabric":
                fab_mode = str(payload.get("fabric_prompt_mode", "")).strip().lower()
                fab_file = str(payload.get("fabric_prompt_file", "")).strip()
                fab_text = str(payload.get("fabric_prompt_text", "")).strip()
                if fab_mode in {"attachment", "manual"}:
                    arguments.extend(("--prompt-mode", fab_mode))
                if fab_file:
                    arguments.extend(("--prompt-file", fab_file))
                if fab_mode == "manual" and fab_text:
                    arguments.extend(("--prompt-text", fab_text))
            if step.key == "package" and folder and str(folder).strip():
                arguments.extend(("--folder", str(folder).strip()))
            if (
                force
                and step.key in {"crop", "seamless", "fabric", "package"}
                and not (step.key == "seamless" and bool(payload.get("seamless_missing_only")))
            ):
                arguments.append("--force")
            steps.append((step, arguments))
        return steps

    def apply_folder_config(self, url, folder):
        config = load_json(self.config_path)
        folder = folder.strip().replace("/", "\\") if folder else ""
        if folder:
            raw_dir = f"textures_raw\\{folder}"
            crop_dir = f"textures_cropped\\{folder}"
            texture_dir = f"textures\\{folder}"
            out_dir = f"output\\chatgpt\\{folder}"
        else:
            raw_dir = "textures_raw"
            crop_dir = "textures_cropped"
            texture_dir = "textures"
            out_dir = "output\\chatgpt"

        # 1. Google Drive
        config.setdefault("google_drive", {})
        config["google_drive"]["share_url"] = url
        config["google_drive"]["destination_dir"] = raw_dir
        config["google_drive"]["enabled"] = bool(url)

        # 2. Crop
        config.setdefault("crop", {})
        config["crop"]["source_dir"] = raw_dir
        config["crop"]["output_dir"] = crop_dir

        # 3. ChatGPT texture grouped
        config.setdefault("chatgpt_texture_grouped", {})
        config["chatgpt_texture_grouped"]["raw_dir"] = crop_dir

        # 4. ChatGPT fabric grouped
        config.setdefault("chatgpt_fabric_grouped", {})
        config["chatgpt_fabric_grouped"]["raw_dir"] = out_dir
        config["chatgpt_fabric_grouped"]["input_mode"] = "seamless"
        config["chatgpt_fabric_grouped"]["output_dir"] = out_dir

        # 5. Google Flow texture
        config.setdefault("flow_texture", {})
        config["flow_texture"]["raw_dir"] = crop_dir
        if folder:
            config["flow_texture"]["status_file"] = f"status_flow_texture_{folder}.json"
        else:
            config["flow_texture"]["status_file"] = "status_flow_texture.json"

        # 6. Seamless package
        config.setdefault("seamless_package", {})
        config["seamless_package"]["output_dir"] = out_dir

        # 6. Legacy / paths / general chatgpt
        config.setdefault("chatgpt", {})
        config["chatgpt"]["output_dir"] = out_dir

        config.setdefault("paths", {})
        config["paths"]["textures_dir"] = texture_dir
        config["paths"]["output_dir"] = out_dir

        save_json_atomic(self.config_path, config)

    def start_pipeline(self, payload, persist_settings=True):
        if self.is_running():
            raise RuntimeError("Một tiến trình đang chạy.")
        self.validate_run(payload)
        if persist_settings:
            self.save_settings(payload)
        self.stop_requested.clear()
        self.append_log("\n" + "=" * 72 + "\nBắt đầu pipeline\n")
        
        source_mode = str(payload.get("source_mode", "drive")).strip().lower()
        if source_mode == "drive":
            queue = payload.get("drive_urls", [])
            valid_queue = deduplicate_drive_items(
                [i for i in queue if isinstance(i, dict) and str(i.get("url", "")).strip()]
            )
            if not valid_queue:
                url = str(payload.get("drive_url", "")).strip()
                folder = str(payload.get("folder", "")).strip()
                valid_queue = [{"url": url, "folder": folder}]
        else:
            valid_queue = [{"url": "", "folder": ""}]
            
        self.worker = threading.Thread(
            target=self.run_queue, args=(valid_queue, payload), daemon=True
        )
        self.worker.start()

    def run_queue(self, queue, payload):
        with self.lock:
            self.session_running = True
            self.session_started_monotonic = time.monotonic()
            self.session_ended_monotonic = None
            self.session_started_at_str = time.strftime("%Y-%m-%d %H:%M:%S")
            try:
                config = load_json(self.config_path)
                fp = fabric_progress(self.project_dir, config)
                self.session_start_created_count = fp.get("created_count", 0)
            except Exception:
                self.session_start_created_count = 0

        config = load_json(self.config_path)
        app_ui = config.get("app_ui", {})
        auto_retry_enabled = bool(payload.get("auto_retry_enabled", app_ui.get("auto_retry_enabled", True)))
        auto_retry_delay = int(payload.get("auto_retry_delay_seconds", app_ui.get("auto_retry_delay_seconds", 120)))
        auto_retry_max = int(payload.get("auto_retry_max_attempts", app_ui.get("auto_retry_max_attempts", 10)))
        if auto_retry_delay < 1:
            auto_retry_delay = 1
        if auto_retry_max < 0:
            auto_retry_max = 0

        retry_attempt = 0
        failed = False
        stopped = False
        source_mode = str(payload.get("source_mode", "drive")).strip().lower()

        while True:
            failed = False
            stopped = False

            for i, item in enumerate(queue):
                if self.stop_requested.is_set():
                    stopped = True
                    break
                    
                url = str(item.get("url", "")).strip()
                folder = str(item.get("folder", "")).strip()
                self.active_running_folder = folder
                
                if source_mode == "drive":
                    self.apply_folder_config(url, folder)
                    folder_title = folder if folder else "Mặc định (Root)"
                    self.append_log(f"\n--- Đang xử lý thư mục Drive {i+1}/{len(queue)}: [{folder_title}] ---\n")
                    
                step_payload = {**payload, "sku": item["sku"]} if "sku" in item else payload
                steps = self.build_steps(step_payload, folder=folder)
                
                for index, (step, arguments) in enumerate(steps, start=1):
                    if self.stop_requested.is_set():
                        stopped = True
                        break
                    folder_tag = f" [{folder}]" if folder else ""
                    self.set_status(f"{step.label}{folder_tag} ({index}/{len(steps)})")
                    self.append_log(f"\n>>> {step.label}{folder_tag}\n")
                    command = build_step_command(
                        self.python_exe, self.project_dir, step, arguments
                    )
                    self.append_log("    " + subprocess.list2cmdline(command) + "\n")
                    environment = os.environ.copy()
                    environment["PYTHONIOENCODING"] = "utf-8"
                    environment["PYTHONUTF8"] = "1"
                    environment[PROJECT_DIR_ENV] = item.get("runtime_dir", str(self.project_dir.resolve()))
                    try:
                        self.process = subprocess.Popen(
                            command,
                            cwd=self.project_dir,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            bufsize=1,
                            env=environment,
                            creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
                        )
                        if self.process.stdout:
                            for line in self.process.stdout:
                                self.append_log(line)
                        return_code = self.process.wait()
                    except Exception as exc:
                        self.append_log(f"[ERROR] Không chạy được bước: {exc}\n")
                        failed = True
                        break
                    finally:
                        if self.process and self.process.stdout:
                            self.process.stdout.close()
                        self.process = None
                    if self.stop_requested.is_set():
                        stopped = True
                        break

                    is_quota_stop = (
                        return_code == 42
                        or bool(self.quota_alert)
                        or "CẢNH BÁO QUOTA CHATGPT" in self.log_text
                        or "CHATGPT_QUOTA_EXHAUSTED" in self.log_text
                    )
                    if is_quota_stop:
                        self.append_log(
                            "\n[DỪNG PIPELINE] Đã phát hiện hết quota ChatGPT. "
                            "Dừng toàn bộ pipeline ngay lập tức (không chạy tiếp các bước khác, không tự động thử lại).\n"
                        )
                        reset_info = self.quota_alert.get("reset_info") if self.quota_alert else None
                        self.notify_telegram("quota", reset_info=reset_info)
                        stopped = True
                        failed = False
                        break

                    is_safe_stop = (
                        return_code == 41
                        or any(
                            m in self.log_text
                            for m in (
                                "ChatGPT login has expired",
                                "ChatGPT human-verification challenge detected",
                            )
                        )
                    )
                    if is_safe_stop:
                        self.append_log(
                            "\n[DỪNG PIPELINE] Đã dừng pipeline do yêu cầu can thiệp trình duyệt (login / captcha).\n"
                        )
                        self.notify_telegram("safe_stop", reason="Yêu cầu can thiệp đăng nhập hoặc xác minh Captcha trên Chrome")
                        stopped = True
                        failed = False
                        break

                    if return_code != 0:
                        self.append_log(f"[ERROR] Bước kết thúc với mã lỗi {return_code}.\n")
                        failed = True
                        break
                    self.append_log(f"<<< Hoàn tất {step.label}{folder_tag}\n")
                    
                if failed or stopped:
                    break
                else:
                    if source_mode == "drive" and folder:
                        self.notify_telegram("folder_complete", folder=folder)

            if stopped or self.stop_requested.is_set():
                break

            current_config = load_json(self.config_path)
            fp = fabric_progress(self.project_dir, current_config)
            pending_count = fp.get("pending_count", 0)

            # Check for safe stop markers (captcha, login, quota) in recent logs
            has_fatal_safe_stop = (
                bool(self.quota_alert)
                or "CẢNH BÁO QUOTA CHATGPT" in self.log_text
                or "CHATGPT_QUOTA_EXHAUSTED" in self.log_text
                or any(
                    m in self.log_text
                    for m in (
                        "ChatGPT login has expired",
                        "ChatGPT human-verification challenge detected",
                    )
                )
            )

            if failed and auto_retry_enabled and not has_fatal_safe_stop:
                retry_attempt += 1
                if auto_retry_max == 0 or retry_attempt <= auto_retry_max:
                    max_label = str(auto_retry_max) if auto_retry_max > 0 else "∞"
                    pending_msg = f" (còn {pending_count} SKU chưa tạo)" if pending_count > 0 else ""
                    self.append_log(
                        f"\n[AUTO-RETRY] Phát hiện gián đoạn{pending_msg}. "
                        f"Tự động chờ {auto_retry_delay}s trước khi khởi động lại pipeline (Lần {retry_attempt}/{max_label})...\n"
                    )
                    interrupted = False
                    for sec in range(auto_retry_delay, 0, -1):
                        if self.stop_requested.is_set():
                            interrupted = True
                            break
                        self.set_status(f"⏳ Tự động thử lại sau {sec}s (Lần {retry_attempt}/{max_label})")
                        time.sleep(1)

                    if interrupted or self.stop_requested.is_set():
                        stopped = True
                        break

                    self.append_log(f"\n[AUTO-RETRY] Khởi động lại pipeline ngay bây giờ (Lần {retry_attempt}/{max_label})...\n")
                    continue
                else:
                    self.append_log(
                        f"\n[AUTO-RETRY] Đã đạt giới hạn số lần thử lại tối đa ({auto_retry_max} lần). Dừng pipeline.\n"
                    )
                    break
            else:
                break

        self.active_running_folder = None
        if source_mode == "drive" and queue:
            self.apply_folder_config(queue[0].get("url", ""), queue[0].get("folder", ""))

        with self.lock:
            self.session_running = False
            self.session_ended_monotonic = time.monotonic()
            self.session_ended_at_str = time.strftime("%Y-%m-%d %H:%M:%S")
            if stopped or self.stop_requested.is_set():
                self.status = "Đã dừng"
            elif failed:
                self.status = "Lỗi"
            else:
                self.status = "Hoàn thành"
        if stopped or self.stop_requested.is_set():
            self.append_log("\n[WARNING] Pipeline đã bị dừng bởi người dùng.\n")
        elif failed:
            self.append_log("\n[ERROR] Pipeline dừng do lỗi.\n")
        else:
            self.append_log("\n" + "=" * 72 + "\nPipeline hoàn tất.\n")
            duration_sec = (
                self.session_ended_monotonic - self.session_started_monotonic
                if (self.session_ended_monotonic and self.session_started_monotonic)
                else 0
            )
            self.notify_telegram("pipeline_complete", duration_sec=duration_sec)

    def notify_telegram(self, event_type, **kwargs):
        try:
            config = load_json(self.config_path)
            tele_cfg = config.get("telegram", {})
            if not bool(tele_cfg.get("enabled")):
                return
            bot_token = str(tele_cfg.get("bot_token", "")).strip()
            chat_id = str(tele_cfg.get("chat_id", "")).strip()
            if not bot_token or not chat_id:
                return

            msg = None
            if event_type == "quota" and bool(tele_cfg.get("notify_on_quota", True)):
                reset_info = kwargs.get("reset_info") or "Chưa rõ thời gian"
                msg = (
                    "⚠️ <b>[VEO3 AUTO] HẾT HẠN MỨC (QUOTA) CHATGPT</b>\n\n"
                    f"⏱️ <b>Thời gian đặt lại:</b> {html.escape(str(reset_info))}\n"
                    f"📁 <b>Thư mục hiện tại:</b> <code>{html.escape(str(self.active_running_folder or 'Mặc định'))}</code>\n"
                    "🛑 Tiến trình đã được dừng an toàn để bảo toàn checkpoint."
                )
            elif event_type == "safe_stop" and bool(tele_cfg.get("notify_on_safe_stop", True)):
                reason = kwargs.get("reason", "Yêu cầu can thiệp trình duyệt")
                msg = (
                    "🛑 <b>[VEO3 AUTO] CẦN CAN THIỆP NGƯỜI DÙNG</b>\n\n"
                    f"⚠️ <b>Lý do:</b> {html.escape(str(reason))}\n"
                    f"📁 <b>Thư mục:</b> <code>{html.escape(str(self.active_running_folder or 'Mặc định'))}</code>\n"
                    "👉 Hãy mở Chrome kiểm tra và xử lý (Captcha / Đăng nhập), sau đó bấm chạy lại."
                )
            elif event_type == "folder_complete" and bool(tele_cfg.get("notify_on_folder_complete", True)):
                folder_name = kwargs.get("folder", "") or "Mặc định"
                fp = fabric_progress(self.project_dir, config, folder_filter=folder_name)
                msg = (
                    f"📁 <b>[VEO3 AUTO] HOÀN TẤT THƯ MỤC: {html.escape(str(folder_name))}</b>\n\n"
                    f"✅ <b>Đã tạo:</b> {fp.get('created_count', 0)} / {fp.get('total', 0)} SKU ({fp.get('percent', 0)}%)\n"
                    f"⏳ <b>Còn lại:</b> {fp.get('pending_count', 0)} SKU"
                )
            elif event_type == "pipeline_complete" and bool(tele_cfg.get("notify_on_complete", True)):
                fp = fabric_progress(self.project_dir, config)
                duration_sec = kwargs.get("duration_sec", 0)
                dur_text = format_duration(int(duration_sec)) if duration_sec else "--"
                msg = (
                    "🎉 <b>[VEO3 AUTO] PIPELINE HOÀN TẤT THÀNH CÔNG!</b>\n\n"
                    f"📊 <b>Tổng số SKU:</b> {fp.get('total', 0)}\n"
                    f"✅ <b>Hoàn thành:</b> {fp.get('created_count', 0)} SKU ({fp.get('percent', 0)}%)\n"
                    f"⏱️ <b>Tổng thời gian chạy:</b> {dur_text}"
                )

            if msg:
                threading.Thread(
                    target=self._async_send_telegram,
                    args=(bot_token, chat_id, msg),
                    daemon=True,
                ).start()
        except Exception as exc:
            self.append_log(f"[Telegram Error] {exc}\n")

    def _async_send_telegram(self, bot_token, chat_id, msg):
        ok, err = send_telegram_message(bot_token, chat_id, msg)
        if not ok:
            self.append_log(f"[Telegram Error] Gửi thông báo thất bại: {err}\n")
        else:
            self.append_log("[Telegram] Đã gửi thông báo thành công.\n")

    def test_telegram(self, payload):
        bot_token = str(payload.get("bot_token", "")).strip()
        chat_id = str(payload.get("chat_id", "")).strip()
        if not bot_token or not chat_id:
            config = load_json(self.config_path)
            tele_cfg = config.get("telegram", {})
            bot_token = bot_token or str(tele_cfg.get("bot_token", "")).strip()
            chat_id = chat_id or str(tele_cfg.get("chat_id", "")).strip()
        if not bot_token or not chat_id:
            return {"ok": False, "error": "Vui lòng nhập đầy đủ Bot Token và Chat ID."}
        
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        test_msg = (
            "🚀 <b>[VEO3 AUTO] KIỂM TRA KẾT NỐI TELEGRAM</b>\n\n"
            "✅ <b>Kết nối thành công!</b>\n"
            f"🕒 <b>Thời gian:</b> <code>{now}</code>\n"
            "📌 Hệ thống đã sẵn sàng gửi các thông báo tự động (Quota, Tiến độ, Lỗi, Hoàn thành)."
        )
        ok, err = send_telegram_message(bot_token, chat_id, test_msg)
        if ok:
            self.append_log(f"[Telegram] Gửi tin nhắn thử nghiệm thành công tới Chat ID: {chat_id}.\n")
            return {"ok": True, "message": "Gửi tin nhắn thử nghiệm thành công!"}
        else:
            self.append_log(f"[Telegram Error] Gửi thử thất bại: {err}\n")
            return {"ok": False, "error": err}

    def start_telegram_poller(self):
        with self.lock:
            self.telegram_polling_active = False
            if self.telegram_poller_thread and self.telegram_poller_thread.is_alive():
                pass # Let it die gracefully by setting active to False
            
            config = load_json(self.config_path)
            tele_cfg = config.get("telegram", {})
            if not bool(tele_cfg.get("enabled")):
                return
            
            bot_token = str(tele_cfg.get("bot_token", "")).strip()
            chat_id = str(tele_cfg.get("chat_id", "")).strip()
            if not bot_token or not chat_id:
                return
                
            self.telegram_polling_active = True
            self.telegram_poller_thread = threading.Thread(
                target=self._telegram_poller_loop,
                args=(bot_token, chat_id),
                daemon=True
            )
            self.telegram_poller_thread.start()

    def _telegram_poller_loop(self, bot_token, chat_id):
        url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
        
        # Optionally send a welcome message with a keyboard menu if starting fresh
        welcome_msg = "🤖 <b>VEO3 Auto Bot đã sẵn sàng nhận lệnh.</b>"
        menu_payload = {
            "chat_id": chat_id,
            "text": welcome_msg,
            "parse_mode": "HTML",
            "reply_markup": {
                "keyboard": [
                    [{"text": "📊 Xem tiến độ (Status)"}]
                ],
                "resize_keyboard": True
            }
        }
        try:
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                data=json.dumps(menu_payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=12):
                pass
        except Exception:
            pass

        while self.telegram_polling_active:
            try:
                # Use a long polling timeout of 30 seconds
                req_url = f"{url}?offset={self.telegram_offset}&timeout=30"
                req = urllib.request.Request(req_url)
                with urllib.request.urlopen(req, timeout=40) as resp:
                    body = resp.read().decode("utf-8", errors="replace")
                    data = json.loads(body)
                    
                    if data.get("ok") and data.get("result"):
                        for update in data["result"]:
                            update_id = update["update_id"]
                            self.telegram_offset = update_id + 1
                            
                            msg = update.get("message")
                            if not msg:
                                continue
                                
                            sender_chat_id = str(msg.get("chat", {}).get("id"))
                            # Verify sender is the authorized chat_id
                            if sender_chat_id != chat_id:
                                continue
                                
                            text = msg.get("text", "").strip()
                            if text in {"/status", "📊 Xem tiến độ (Status)"}:
                                # Generate progress report
                                self._send_telegram_status_report(bot_token, chat_id)
                                
            except urllib.error.URLError:
                # Network error or timeout, wait before retrying to prevent rapid loop
                time.sleep(5)
            except Exception:
                time.sleep(5)
                
    def _send_telegram_status_report(self, bot_token, chat_id):
        try:
            config = load_json(self.config_path)
            stats = compute_drive_folders_stats(self.project_dir, config, self.active_running_folder)
            
            timing = compute_historical_timing_stats(self.project_dir)
            total_time = 0
            if self.session_running and self.session_started_monotonic:
                total_time = time.monotonic() - self.session_started_monotonic
            
            time_str = format_duration(int(total_time)) if total_time > 0 else "00:00:00"
            status_text = "Đang chạy ⚡" if self.is_running() else "Nhàn rỗi 💤"
            
            active_folder_info = ""
            queue_info = ""
            
            for s in stats.get("folders", []):
                drive_tag = f" (Drive: {s['drive_total']} ảnh)" if s.get("drive_total") else ""
                if s["is_active"]:
                    eta = "--"
                    if timing["avg_duration_seconds"] > 0 and s["pending_count"] > 0:
                        eta_sec = s["pending_count"] * timing["avg_duration_seconds"]
                        eta = format_duration(int(eta_sec))
                    
                    active_folder_info = (
                        f"📁 <b>Thư mục đang chạy:</b> <code>{html.escape(s['folder_display'])}</code>{drive_tag}\n"
                        f"✅ Đã xong: {s['created_count']}/{s['total']} SKU ({s['percent']}%)\n"
                        f"⏳ Ước tính còn lại: {eta}\n\n"
                    )
                else:
                    queue_info += f"- <code>{html.escape(s['folder_display'])}</code>{drive_tag}: {s['created_count']}/{s['total']} SKU ({s['percent']}%)\n"
                    
            if not queue_info:
                queue_info = "- (Trống)\n"
                
            report = (
                f"📊 <b>BÁO CÁO TIẾN ĐỘ HIỆN TẠI</b>\n"
                f"Trạng thái: {status_text}\n"
                f"Thời gian phiên: {time_str}\n\n"
                f"{active_folder_info}"
                f"📁 <b>Hàng đợi:</b>\n{queue_info}"
            )
            
            send_telegram_message(bot_token, chat_id, report)
        except Exception as exc:
            self.append_log(f"[Telegram Poller Error] {exc}\n")

    def check_swatch_prerequisites(self, payload):
        """Find local source SKUs that do not yet have a usable seamless texture."""
        source_mode = str(payload.get("source_mode", "drive")).strip().lower()
        selected_sku = str(payload.get("sku", "")).strip()
        limit_text = str(payload.get("limit", "")).strip()
        limit = int(limit_text) if limit_text.isdigit() and int(limit_text) > 0 else None
        targets = []
        local_fallback = False

        requested_folder = str(payload.get("folder", "")).strip()
        if source_mode == "drive":
            if requested_folder:
                requested_url = str(payload.get("url", "")).strip()
                if not requested_url:
                    requested_url = next(
                        (
                            str(item.get("url", "")).strip()
                            for item in payload.get("drive_urls", [])
                            if str(item.get("folder", "")).strip() == requested_folder
                        ),
                        "",
                    )
                targets = [(requested_folder, self.project_dir / "textures_raw" / requested_folder, requested_url)]
            else:
                targets = [
                    (
                        str(item.get("folder", "")).strip(),
                        self.project_dir / "textures_raw" / str(item.get("folder", "")).strip(),
                        str(item.get("url", "")).strip(),
                    )
                    for item in deduplicate_drive_items(payload.get("drive_urls", []))
                    if str(item.get("url", "")).strip()
                ]
                if not targets:
                    raw_root = self.project_dir / "textures_raw"
                    if safe_is_dir(raw_root):
                        targets = [
                            (path.name, path, "")
                            for path in sorted(raw_root.iterdir(), key=lambda item: item.name.casefold())
                            if path.is_dir()
                        ]
                        local_fallback = bool(targets)
        else:
            local_dir = resolve_project_path(self.project_dir, payload.get("local_source_dir", ""))
            targets = [(requested_folder, local_dir, "")]

        missing = []
        checked = 0
        config = load_json(self.config_path)
        drive_settings = config.get("google_drive", {})
        for folder, source_dir, drive_url in targets:
            sku_names = []
            if source_mode == "drive" and drive_url and bool(payload.get("flows", {}).get("import")):
                import import_google_drive

                entries = import_google_drive.list_public_folder(
                    import_google_drive.validate_share_url(drive_url),
                    int(drive_settings.get("timeout_seconds", 300)),
                )
                extensions = {
                    str(value).lower() if str(value).startswith(".") else f".{str(value).lower()}"
                    for value in drive_settings.get("extensions", import_google_drive.DEFAULT_EXTENSIONS)
                }
                images, _, _ = import_google_drive.select_images(
                    entries,
                    extensions,
                    bool(drive_settings.get("recursive", False)),
                )
                sku_names = sorted({Path(item["name"]).stem for item in images}, key=str.casefold)
            elif safe_is_dir(source_dir):
                sku_names = sorted(
                    {
                        path.stem
                        for path in source_dir.rglob("*")
                        if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
                    },
                    key=str.casefold,
                )

            folder_checked = 0
            for sku in sku_names:
                if selected_sku and sku.casefold() != selected_sku.casefold():
                    continue
                checked += 1
                folder_checked += 1
                output_dir = self.project_dir / "output" / "chatgpt"
                final_path = output_dir / folder / sku / "seamless_texture.png" if folder else output_dir / sku / "seamless_texture.png"
                valid = final_path.is_file() and final_path.stat().st_size > 0
                if valid:
                    try:
                        from PIL import Image
                        with Image.open(final_path) as image:
                            image.verify()
                    except Exception:
                        valid = False
                if not valid:
                    missing.append(f"{folder}/{sku}" if folder else sku)
                if limit is not None and folder_checked >= limit:
                    break

        return {
            "checked_count": checked,
            "missing_count": len(missing),
            "missing_skus": missing,
            "local_fallback": local_fallback,
        }

    def run_single_folder(self, payload):
        folder = str(payload.get("folder", "")).strip()
        url = str(payload.get("url", "")).strip()
        engine = str(payload.get("engine", "chatgpt")).strip().lower()
        
        config = load_json(self.config_path)
        if not url:
            for item in config.get("google_drive", {}).get("urls", []):
                if str(item.get("folder", "")).strip() == folder:
                    url = str(item.get("url", "")).strip()
                    break
        if not url:
            url = str(config.get("google_drive", {}).get("share_url", "")).strip()
        
        flows = payload.get("flows", {
            "import": True,
            "crop": True,
            "seamless": True,
            "fabric": True,
            "package": True,
        })
        
        run_payload = {
            "engine": engine,
            "seamless_engine": str(payload.get("seamless_engine", "chatgpt")),
            "source_mode": "drive",
            "drive_urls": [{"url": url, "folder": folder}],
            "sku": payload.get("sku", ""),
            "limit": payload.get("limit", ""),
            "images_per_chat": int(payload.get("images_per_chat", 10)),
            "auto_retry_enabled": bool(payload.get("auto_retry_enabled", True)),
            "auto_retry_delay_seconds": int(payload.get("auto_retry_delay_seconds", 120)),
            "auto_retry_max_attempts": int(payload.get("auto_retry_max_attempts", 10)),
            "dry_run": bool(payload.get("dry_run", False)),
            "force": bool(payload.get("force", False)),
            "seamless_missing_only": bool(payload.get("seamless_missing_only", False)),
            "flows": flows,
        }
        engine_label = "Thuật toán CV" if engine in {"algo", "algorithm"} else "Google Flow" if engine in {"flow", "google_flow"} else "ChatGPT"
        self.append_log(f"\n[YÊU CẦU] Chạy riêng thư mục Drive ({engine_label}): [{folder or 'Mặc định'}]\n")
        self.start_pipeline(run_payload)

    def run_single_sku(self, payload):
        sku = str(payload.get("sku", "")).strip()
        folder = str(payload.get("folder", "")).strip()
        if not sku:
            raise ValueError("Chưa chỉ định mã SKU.")
        force = bool(payload.get("force", True))
        images_per_chat = int(payload.get("images_per_chat", 1))
        engine = str(payload.get("engine", "chatgpt")).strip().lower()

        config = load_json(self.config_path)
        mode, _, _, _ = source_settings(self.project_dir, config)

        missing_labels = []
        if engine == "chatgpt":
            output_root = resolve_project_path(self.project_dir, "output/chatgpt")
            sku_output = output_root / folder / sku if folder else output_root / sku
            has_seamless = safe_is_file(sku_output / "seamless_texture.png")
            has_fabric = safe_is_file(sku_output / "image_1.png")
            if has_seamless and has_fabric:
                self.append_log(
                    f"\n[BỎ QUA] SKU {sku} đã có đủ Seamless và Swatch; không gửi prompt ChatGPT.\n"
                )
                return {
                    "ok": True,
                    "started": False,
                    "message": f"SKU {sku} đã có đủ Seamless và Swatch. Không cần tạo lại.",
                }
            flows = {
                "import": False,
                "crop": not has_seamless,
                "seamless": not has_seamless,
                "fabric": not has_fabric,
                "package": not has_seamless,
            }
            # Force is safe here because only missing output stages are enabled.
            force = True
            if not has_seamless:
                missing_labels.append("Seamless")
            if not has_fabric:
                missing_labels.append("Swatch")
        else:
            flows = {
                "import": False,
                "crop": True,
                "seamless": True,
                "fabric": True,
                "package": True,
            }

        if folder:
            self.apply_folder_config(config.get("google_drive", {}).get("share_url", ""), folder)

        run_payload = {
            "engine": engine,
            "source_mode": mode,
            "local_source_dir": config.get("app_ui", {}).get("local_source_dir", ""),
            "drive_urls": [{"url": config.get("google_drive", {}).get("share_url", ""), "folder": folder}],
            "sku": sku,
            "limit": "1",
            "images_per_chat": images_per_chat,
            "dry_run": False,
            "force": force,
            "flows": flows,
        }
        engine_label = "Thuật toán CV" if engine in {"algo", "algorithm"} else "Google Flow" if engine in {"flow", "google_flow"} else "ChatGPT"
        missing_text = f", Chỉ tạo: {', '.join(missing_labels)}" if missing_labels else ""
        self.append_log(f"\n[YÊU CẦU] Chạy riêng SKU ({engine_label}): {sku} (Folder: {folder or 'Mặc định'}, Force: {force}{missing_text})\n")
        self.start_pipeline(run_payload, persist_settings=False)
        return {"ok": True, "started": True, "missing": missing_labels}

    def delete_drive_folder(self, payload):
        folder = str(payload.get("folder", "")).strip().replace("/", "\\")
        url = str(payload.get("url", "")).strip()
        delete_files = bool(payload.get("delete_files", True))

        config = load_json(self.config_path)
        drive = config.setdefault("google_drive", {})
        urls = drive.get("urls", [])
        
        new_urls = []
        for item in urls:
            item_folder = str(item.get("folder", "")).strip().replace("/", "\\")
            item_url = str(item.get("url", "")).strip()
            if folder and item_folder.lower() == folder.lower():
                continue
            if url and item_url == url and not folder:
                continue
            new_urls.append(item)
            
        drive["urls"] = new_urls
        drive["share_url"] = new_urls[0]["url"] if new_urls else ""
        drive["enabled"] = bool(new_urls)
        save_json_atomic(self.config_path, config)

        # Folder cards are assembled from both config.json and the persisted
        # Drive sync snapshot.  Remove the latter as well, otherwise fetchState()
        # immediately recreates a deleted card as "Chưa tải ảnh".
        if folder:
            sync_path = resolve_project_path(
                self.project_dir, drive.get("sync_status_file", "status_drive_sync.json")
            )
            if safe_is_file(sync_path):
                sync_data = load_json(sync_path)
                if isinstance(sync_data, dict):
                    folder_key = folder.casefold()
                    sync_folders = sync_data.get("folders")
                    if isinstance(sync_folders, dict):
                        sync_data["folders"] = {
                            key: value
                            for key, value in sync_folders.items()
                            if str(key).strip().replace("/", "\\").casefold() != folder_key
                        }
                    sync_history = sync_data.get("history")
                    if isinstance(sync_history, list):
                        sync_data["history"] = [
                            item
                            for item in sync_history
                            if not isinstance(item, dict)
                            or str(item.get("folder", "")).strip().replace("/", "\\").casefold()
                            != folder_key
                        ]
                    save_json_atomic(sync_path, sync_data)

        delete_errors = []
        if delete_files and folder and folder not in {".", "..", "/", "\\"}:
            for base_name in ("textures_raw", "textures_cropped", "textures", "output/chatgpt", "output/chatgpt_project_fabric"):
                target_dir = resolve_project_path(self.project_dir, base_name) / folder
                if safe_is_dir(target_dir):
                    try:
                        shutil.rmtree(target_dir)
                    except Exception as exc:
                        delete_errors.append(f"{target_dir}: {exc}")
                        self.append_log(f"[WARNING] Không xóa được thư mục {target_dir}: {exc}\n")

        if delete_errors:
            raise RuntimeError(
                "Đã xóa link và trạng thái Drive nhưng không thể xóa hết dữ liệu local:\n"
                + "\n".join(delete_errors)
            )

        self.append_log(f"Đã xóa thư mục/link Drive [{folder or url}].\n")
        return {"ok": True, "drive_urls": new_urls}

    def dismiss_quota_alert(self):
        with self.lock:
            self.quota_alert = None
        return {"ok": True}

    def open_sku_folder(self, payload):
        sku = str(payload.get("sku", "")).strip()
        folder = str(payload.get("folder", "")).strip()

        base_out = resolve_project_path(self.project_dir, "output/chatgpt")
        
        target_dir = None
        if folder and sku:
            candidate = base_out / folder / sku
            if safe_is_dir(candidate):
                target_dir = candidate
            else:
                candidate = base_out / folder
                if safe_is_dir(candidate):
                    target_dir = candidate
        elif folder:
            candidate = base_out / folder
            target_dir = candidate if safe_is_dir(candidate) else base_out
        elif sku:
            candidate = base_out / sku
            if safe_is_dir(candidate):
                target_dir = candidate
            else:
                for match in base_out.glob(f"*/{sku}"):
                    if safe_is_dir(match):
                        target_dir = match
                        break
        
        if not target_dir:
            target_dir = base_out

        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = str(target_dir.resolve())
        if hasattr(os, "startfile"):
            os.startfile(target_path)
        else:
            subprocess.Popen(["explorer", target_path])
        return {"ok": True, "path": target_path}

    def audit_drive_folders(self, payload):
        urls = payload.get("urls", [])
        if isinstance(urls, str):
            urls = [u.strip() for u in urls.splitlines() if u.strip()]
        target_child = str(payload.get("target_child", "all")).strip()
        results = []
        for url in urls:
            url_str = str(url).strip()
            if not url_str:
                continue
            res = audit_single_drive_url(self.project_dir, url_str, target_child=target_child)
            results.append(res)
        return {"results": results}

    def save_drive_link(self, payload):
        folder = str(payload.get("folder", "")).strip()
        url = str(payload.get("url", "")).strip()
        modified_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        if not folder:
            raise ValueError("Tên thư mục không được để trống.")
        if url and not validate_drive_url(url):
            raise ValueError("Link Google Drive không hợp lệ. Phải có dạng https://drive.google.com/drive/folders/...")
        
        config_path = self.project_dir / "config.json"
        config = load_json(config_path)
        drive = config.setdefault("google_drive", {})
        urls = drive.get("urls", [])
        if not isinstance(urls, list):
            urls = []
        
        found = False
        new_urls = []
        for item in urls:
            item_folder = str(item.get("folder", "")).strip()
            if item_folder.casefold() == folder.casefold():
                if url:
                    new_urls.append({"url": url, "folder": folder, "modified_at": modified_at})
                found = True
            else:
                new_urls.append(item)
        if not found and url:
            new_urls.append({"url": url, "folder": folder, "modified_at": modified_at})
        
        drive["urls"] = new_urls
        if new_urls:
            drive["share_url"] = new_urls[0]["url"]
            drive["enabled"] = True
        else:
            drive["share_url"] = ""
            
        save_json_atomic(config_path, config)
        self.append_log(f"[CONFIG] Đã {'cập nhật' if url else 'gỡ'} link Drive cho thư mục '{folder}'\n")
        return {"ok": True, "folder": folder, "url": url, "drive_urls": new_urls}

    def audit_single_folder(self, payload):
        folder = str(payload.get("folder", "")).strip()
        url = str(payload.get("url", "")).strip()
        config_path = self.project_dir / "config.json"
        config = load_json(config_path) if safe_is_file(config_path) else {}
        
        if not url:
            for item in config.get("google_drive", {}).get("urls", []):
                if str(item.get("folder", "")).strip().casefold() == folder.casefold():
                    url = str(item.get("url", "")).strip()
                    break
        if not url:
            raise ValueError(f"Thư mục '{folder}' chưa được gán link Google Drive.")
        
        res = audit_single_drive_url(self.project_dir, url, target_child="chatgpt")
        
        # Save snapshot into status_drive_sync.json
        try:
            sync_file = self.project_dir / "status_drive_sync.json"
            sync_data = load_json(sync_file) if safe_is_file(sync_file) else {}
            folders_data = sync_data.setdefault("folders", {})
            check = next((c for c in res.get("checks", []) if c.get("engine") == "chatgpt"), None)
            if not check and res.get("checks"):
                check = res["checks"][0]
            
            drive_total = res.get("drive_total", 0)
            created_count = check.get("created_count", 0) if check else 0
            missing_count = check.get("missing_count", 0) if check else drive_total
            percent = check.get("percent", 0.0) if check else 0.0
            
            save_key = folder or res.get("base_code") or "root"
            folders_data[save_key] = {
                "folder": save_key,
                "drive_url": url,
                "last_sync_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "drive_total_images": drive_total,
                "created_count": created_count,
                "pending_count": missing_count,
                "progress_percent": percent,
                "last_status": "audited",
            }
            save_json_atomic(sync_file, sync_data)
        except Exception as exc:
            self.append_log(f"[WARN] Không lưu được status_drive_sync.json: {exc}\n")
            
        return res

    def auto_match_drive_urls(self, payload):
        urls = payload.get("urls", [])
        if isinstance(urls, str):
            urls = [u.strip() for u in re.split(r"[\r\n,]+", urls) if u.strip()]
        
        config_path = self.project_dir / "config.json"
        config = load_json(config_path)
        stats = compute_drive_folders_stats(self.project_dir, config, include_unlinked=True)
        existing_folders = [f["folder"] for f in stats.get("folders", [])]
        existing_map = {f.casefold(): f for f in existing_folders}
        
        matched = []
        unmatched = []
        
        for url in urls:
            url_str = str(url).strip()
            if not url_str or not validate_drive_url(url_str):
                unmatched.append({"url": url_str, "reason": "URL không hợp lệ hoặc không phải folder Drive."})
                continue
            try:
                title = fetch_drive_folder_title(url_str, timeout=6)
                base_code = extract_base_folder_code(title or "")
                
                matched_folder = None
                for cand in [title, base_code]:
                    if cand and cand.casefold() in existing_map:
                        matched_folder = existing_map[cand.casefold()]
                        break
                
                if not matched_folder and base_code:
                    for k, v in existing_map.items():
                        if k.startswith(base_code.casefold()) or base_code.casefold().startswith(k):
                            matched_folder = v
                            break
                            
                if matched_folder:
                    matched.append({
                        "url": url_str,
                        "folder": matched_folder,
                        "title": title or base_code,
                        "base_code": base_code,
                    })
                else:
                    unmatched.append({
                        "url": url_str,
                        "folder": base_code or title or "Chưa nhận diện",
                        "title": title,
                        "base_code": base_code,
                        "reason": "Không tìm thấy thư mục vải tương ứng trên máy.",
                    })
            except Exception as exc:
                unmatched.append({"url": url_str, "reason": str(exc)})
                
        if matched:
            drive = config.setdefault("google_drive", {})
            urls_list = drive.setdefault("urls", [])
            for m in matched:
                folder_name = m["folder"]
                found = False
                for item in urls_list:
                    if str(item.get("folder", "")).strip().casefold() == folder_name.casefold():
                        item["url"] = m["url"]
                        item["modified_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                        found = True
                        break
                if not found:
                    urls_list.append({
                        "url": m["url"],
                        "folder": folder_name,
                        "modified_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    })
            if urls_list:
                drive["share_url"] = urls_list[0]["url"]
                drive["enabled"] = True
            save_json_atomic(config_path, config)
            self.append_log(f"[CONFIG] Đã tự động ghép nối {len(matched)} link Drive vào thư mục vải.\n")
            
        return {
            "ok": True,
            "matched": matched,
            "unmatched": unmatched,
            "total_matched": len(matched),
            "total_unmatched": len(unmatched),
        }

    def check_browser(self):
        if self.is_running():
            raise RuntimeError("Một tiến trình đang chạy.")
        self.python_exe = locate_python(self.project_dir)
        if not self.python_exe:
            raise ValueError("Không thể khởi tạo bộ chạy pipeline.")
        step = FlowStep(
            "browser",
            "Kiểm tra Chrome và ChatGPT",
            "run_chatgpt_texture_grouped_batch.py",
        )
        self.stop_requested.clear()
        self.worker = threading.Thread(
            target=self.run_steps, args=([(step, ["--check-browser"])],), daemon=True
        )
        self.worker.start()

    def check_flow_browser(self):
        if self.is_running():
            raise RuntimeError("Một tiến trình đang chạy.")
        self.python_exe = locate_python(self.project_dir)
        if not self.python_exe:
            raise ValueError("Không thể khởi tạo bộ chạy pipeline.")
        step = FlowStep(
            "browser_flow",
            "Kiểm tra Chrome và Google Flow",
            "run_flow_texture_batch.py",
        )
        self.stop_requested.clear()
        self.worker = threading.Thread(
            target=self.run_steps, args=([(step, ["--check-browser"])],), daemon=True
        )
        self.worker.start()

    def run_steps(self, steps):
        with self.lock:
            self.session_running = True
            self.session_started_monotonic = time.monotonic()
            self.session_ended_monotonic = None
            self.session_started_at_str = time.strftime("%Y-%m-%d %H:%M:%S")
            try:
                config = load_json(self.config_path)
                fp = fabric_progress(self.project_dir, config)
                self.session_start_created_count = fp.get("created_count", 0)
            except Exception:
                self.session_start_created_count = 0

        failed = False
        stopped = False
        for index, (step, arguments) in enumerate(steps, start=1):
            if self.stop_requested.is_set():
                stopped = True
                break
            self.set_status(f"{step.label} ({index}/{len(steps)})")
            self.append_log(f"\n>>> {step.label}\n")
            command = build_step_command(
                self.python_exe, self.project_dir, step, arguments
            )
            self.append_log("    " + subprocess.list2cmdline(command) + "\n")
            environment = os.environ.copy()
            environment["PYTHONIOENCODING"] = "utf-8"
            environment["PYTHONUTF8"] = "1"
            environment[PROJECT_DIR_ENV] = str(self.project_dir.resolve())
            try:
                self.process = subprocess.Popen(
                    command,
                    cwd=self.project_dir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    env=environment,
                    creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
                )
                if self.process.stdout:
                    for line in self.process.stdout:
                        self.append_log(line)
                return_code = self.process.wait()
            except Exception as exc:
                self.append_log(f"[ERROR] Không chạy được bước: {exc}\n")
                failed = True
                break
            finally:
                if self.process and self.process.stdout:
                    self.process.stdout.close()
                self.process = None
            if self.stop_requested.is_set():
                stopped = True
                break
            if return_code != 0:
                self.append_log(f"[ERROR] Bước kết thúc với mã lỗi {return_code}.\n")
                failed = True
                break
            self.append_log(f"<<< Hoàn tất {step.label}\n")

        with self.lock:
            self.session_running = False
            self.session_ended_monotonic = time.monotonic()
            self.session_ended_at_str = time.strftime("%Y-%m-%d %H:%M:%S")

        result = (
            "Đã dừng"
            if stopped
            else "Pipeline gặp lỗi"
            if failed
            else "Hoàn tất pipeline"
        )
        self.set_status(result)
        self.append_log(f"\n{result}\n")

    def _stop_process_with_escalation(self, process):
        """Give the active worker a short graceful exit, then force it to stop."""
        try:
            try:
                process.send_signal(signal.CTRL_BREAK_EVENT)
                self.append_log(
                    "\n[STOP] Đã gửi yêu cầu dừng an toàn; chờ tối đa 3 giây...\n"
                )
                process.wait(timeout=3)
                self.append_log("[STOP] Worker đã dừng an toàn.\n")
                return
            except subprocess.TimeoutExpired:
                pass
            except Exception as exc:
                self.append_log(
                    f"[STOP] Không gửi được yêu cầu dừng an toàn ({exc}); "
                    "chuyển sang terminate.\n"
                )

            if process.poll() is None:
                self.append_log(
                    "[STOP] Worker chưa phản hồi sau 3 giây; đang cưỡng chế terminate...\n"
                )
                try:
                    process.terminate()
                except Exception as exc:
                    self.append_log(f"[STOP] terminate không thành công: {exc}\n")

            try:
                process.wait(timeout=2)
                self.append_log("[STOP] Worker đã được terminate.\n")
                return
            except subprocess.TimeoutExpired:
                pass
            except Exception:
                if process.poll() is not None:
                    return

            if process.poll() is None:
                self.append_log(
                    "[STOP] Worker vẫn còn chạy; đang kill tiến trình worker hiện tại...\n"
                )
                try:
                    process.kill()
                    process.wait(timeout=2)
                    self.append_log("[STOP] Đã cưỡng chế dừng worker.\n")
                except Exception as exc:
                    self.append_log(f"[STOP] Không thể kill worker: {exc}\n")
        finally:
            with self.lock:
                if self.stop_escalation_thread is threading.current_thread():
                    self.stop_escalation_thread = None

    def stop(self):
        self.stop_requested.set()
        self.set_status("Đang dừng...")
        with self.lock:
            process = self.process
            if not process or process.poll() is not None:
                return
            if self.stop_escalation_thread and self.stop_escalation_thread.is_alive():
                return
            self.stop_escalation_thread = threading.Thread(
                target=self._stop_process_with_escalation,
                args=(process,),
                daemon=True,
                name="veo3-stop-escalation",
            )
            self.stop_escalation_thread.start()

    def start_automation_chrome(self):
        config = load_json(self.config_path)
        browser = config.get("browser", {})
        port = int(browser.get("cdp_port", 9333))
        profile = Path(
            os.path.expandvars(
                str(
                    browser.get(
                        "user_data_dir", "%LOCALAPPDATA%\\VEO3_AUTO\\ChromeProfile"
                    )
                )
            )
        )
        chrome = find_chrome()
        if not chrome:
            raise FileNotFoundError("Không tìm thấy Google Chrome.")
        profile.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(
            [
                str(chrome),
                f"--remote-debugging-port={port}",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "https://chatgpt.com/",
            ],
            cwd=self.project_dir,
        )
        self.append_log(f"Đã mở Chrome automation trên port {port}.\n")

    def request_shutdown(self):
        if self.is_running():
            self.stop()
        if self.server:
            threading.Thread(target=self.server.shutdown, daemon=True).start()


class AppServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class AppHandler(BaseHTTPRequestHandler):
    server_version = "VEO3Auto/1.0"

    @property
    def controller(self):
        return self.server.controller

    def log_message(self, format_string, *args):
        return

    def send_bytes(self, body, content_type, status=HTTPStatus.OK, cache_seconds=0):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if cache_seconds > 0:
            self.send_header("Cache-Control", f"public, max-age={cache_seconds}")
        else:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, value, status=HTTPStatus.OK):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_bytes(body, "application/json; charset=utf-8", status)

    def send_placeholder_svg(self, sku, kind):
        label = "CHƯA CÓ ẢNH"
        if kind == "output":
            label = "CHƯA TẠO SEAMLESS"
        elif kind == "fabric":
            label = "CHƯA TẠO SWATCH"
        svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200" viewBox="0 0 200 200">
  <rect width="200" height="200" fill="#081321"/>
  <rect x="8" y="8" width="184" height="184" rx="10" fill="#0b1728" stroke="#1e2d41" stroke-width="2" stroke-dasharray="6,4"/>
  <text x="100" y="95" fill="#64748b" font-family="Segoe UI,Arial,sans-serif" font-size="12" font-weight="bold" text-anchor="middle">{label}</text>
  <text x="100" y="120" fill="#38bdf8" font-family="Consolas,monospace" font-size="13" text-anchor="middle">{sku}</text>
</svg>"""
        return self.send_bytes(svg.encode("utf-8"), "image/svg+xml", cache_seconds=5)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > 1_000_000:
            raise ValueError("Request quá lớn.")
        raw = self.rfile.read(length) if length else b"{}"
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Request phải là JSON object.")
        return value

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            return self.send_bytes(
                INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8"
            )
        if path == "/api/state":
            query = parse_qs(parsed.query)
            folder_filter = query.get("folder", [None])[0]
            return self.send_json(self.controller.state(folder_filter=folder_filter))
        if path == "/api/sku-info":
            query = parse_qs(parsed.query)
            sku = query.get("sku", [""])[0].strip()
            folder = query.get("folder", [""])[0].strip() or None
            if not sku:
                return self.send_json({"error": "Thiếu mã SKU"}, HTTPStatus.BAD_REQUEST)
            try:
                info = get_sku_details(self.controller.project_dir, sku, folder=folder)
                return self.send_json(info)
            except Exception as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        if path == "/api/load-default-prompt":
            query = parse_qs(parsed.query)
            flow = query.get("flow", ["texture"])[0].strip()
            try:
                text = get_default_prompt_text(self.controller.project_dir, flow)
                return self.send_json({"ok": True, "prompt_text": text})
            except Exception as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        if path == "/api/image":
            query = parse_qs(parsed.query)
            sku = query.get("sku", [""])[0].strip()
            kind = query.get("kind", ["output"])[0].strip().lower()
            folder = query.get("folder", [""])[0].strip() or None
            if not sku:
                return self.send_placeholder_svg("?", kind)
            img_path = get_image_file(self.controller.project_dir, sku, kind, folder=folder)
            if not img_path:
                return self.send_placeholder_svg(sku, kind)
            ext = img_path.suffix.lower()
            mime_type = "image/png"
            if ext in {".jpg", ".jpeg"}:
                mime_type = "image/jpeg"
            elif ext == ".webp":
                mime_type = "image/webp"
            elif ext == ".bmp":
                mime_type = "image/bmp"
            try:
                with open(img_path, "rb") as f:
                    body = f.read()
                return self.send_bytes(body, mime_type, cache_seconds=5)
            except Exception:
                return self.send_placeholder_svg(sku, kind)
        if path == "/api/quality-image":
            query = parse_qs(parsed.query)
            try:
                img_path = self.controller.resolve_quality_image(
                    query.get("path", [""])[0]
                )
                mime_types = {
                    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                    ".png": "image/png", ".webp": "image/webp",
                    ".bmp": "image/bmp", ".gif": "image/gif",
                    ".tif": "image/tiff", ".tiff": "image/tiff",
                }
                with open(img_path, "rb") as stream:
                    return self.send_bytes(
                        stream.read(), mime_types.get(img_path.suffix.lower(), "application/octet-stream")
                    )
            except Exception as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        self.send_json({"error": "Không tìm thấy."}, HTTPStatus.NOT_FOUND)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            payload = self.read_json()
            if path == "/api/select-folder":
                selected = choose_local_folder(payload.get("initial_dir"))
                return self.send_json({"path": selected})
            if path == "/api/select-quality-folder":
                return self.send_json(self.controller.select_quality_folder())
            if path == "/api/quality-image-failure":
                return self.send_json(self.controller.set_quality_image_failure(payload))
            if path == "/api/quality-rerun":
                return self.send_json(self.controller.rerun_quality_images(payload))
            if path == "/api/upload-quality-image":
                return self.send_json(self.controller.upload_quality_image(payload))
            if path == "/api/select-prompt-file":
                selected = choose_prompt_file(payload.get("initial_path"))
                return self.send_json({"path": selected})
            if path in {"/api/save", "/api/save-settings"}:
                self.controller.save_settings(payload)
                return self.send_json({"ok": True})
            if path == "/api/test-telegram":
                res = self.controller.test_telegram(payload)
                return self.send_json(res)
            if path == "/api/run":
                self.controller.start_pipeline(payload)
                return self.send_json({"ok": True})
            if path == "/api/check-swatch-prerequisites":
                return self.send_json(self.controller.check_swatch_prerequisites(payload))
            if path == "/api/run-folder":
                self.controller.run_single_folder(payload)
                return self.send_json({"ok": True})
            if path == "/api/run-sku":
                return self.send_json(self.controller.run_single_sku(payload))
            if path == "/api/open-folder":
                res = self.controller.open_sku_folder(payload)
                return self.send_json(res)
            if path == "/api/delete-drive-folder":
                res = self.controller.delete_drive_folder(payload)
                return self.send_json(res)
            if path == "/api/save-drive-link":
                res = self.controller.save_drive_link(payload)
                return self.send_json(res)
            if path == "/api/audit-single-folder":
                res = self.controller.audit_single_folder(payload)
                return self.send_json(res)
            if path == "/api/auto-match-drive-urls":
                res = self.controller.auto_match_drive_urls(payload)
                return self.send_json(res)
            if path == "/api/dismiss-quota-alert":
                res = self.controller.dismiss_quota_alert()
                return self.send_json(res)
            if path == "/api/audit-drive-folders":
                res = self.controller.audit_drive_folders(payload)
                return self.send_json(res)
            if path == "/api/stop":
                self.controller.stop()
                return self.send_json({"ok": True})
            if path == "/api/chrome":
                self.controller.start_automation_chrome()
                return self.send_json({"ok": True})
            if path == "/api/check-browser":
                self.controller.check_browser()
                return self.send_json({"ok": True})
            if path == "/api/check-flow-browser":
                self.controller.check_flow_browser()
                return self.send_json({"ok": True})
            if path == "/api/shutdown":
                self.controller.request_shutdown()
                return self.send_json({"ok": True})
            return self.send_json({"error": "Không tìm thấy."}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.controller.append_log(f"[ERROR] {exc}\n")
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)


def open_app_window(url):
    chrome = find_chrome()
    if chrome:
        try:
            subprocess.Popen([str(chrome), f"--app={url}", "--new-window"])
            return
        except Exception:
            pass
    webbrowser.open(url)


def idle_watchdog(controller, server):
    while True:
        time.sleep(30)
        if getattr(server, "_BaseServer__shutdown_request", False):
            return
        if (
            time.monotonic() - controller.last_client_at > 1800
            and not controller.is_running()
        ):
            controller.append_log("Ứng dụng tự đóng sau 30 phút không sử dụng.\n")
            server.shutdown()
            return


DEFAULT_APP_PORT = 8765


def main(open_browser=True, port=DEFAULT_APP_PORT):
    controller = PipelineController()
    try:
        server = AppServer(("127.0.0.1", int(port)), AppHandler)
    except OSError as exc:
        raise RuntimeError(
            f"Cổng {port} đang được sử dụng. Hãy đóng phiên VEO3 Auto Pipeline cũ rồi mở lại app."
        ) from exc
    server.controller = controller
    controller.server = server
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/"
    if sys.stdout:
        print(f"READY {url}", flush=True)
    controller.append_log(f"VEO3 Auto Pipeline: {url}\n")
    threading.Thread(
        target=idle_watchdog, args=(controller, server), daemon=True
    ).start()
    if open_browser:
        threading.Timer(0.35, open_app_window, args=(url,)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        if controller.is_running():
            controller.stop()
        server.server_close()


if __name__ == "__main__":
    if EMBEDDED_WORKER_FLAG in sys.argv:
        worker_index = sys.argv.index(EMBEDDED_WORKER_FLAG)
        if worker_index + 1 >= len(sys.argv):
            raise SystemExit(f"{EMBEDDED_WORKER_FLAG} requires a script name")
        worker_script = sys.argv[worker_index + 1]
        worker_arguments = sys.argv[worker_index + 2 :]
        raise SystemExit(run_embedded_worker(worker_script, worker_arguments))
    if EMBEDDED_GDOWN_FLAG in sys.argv:
        gdown_index = sys.argv.index(EMBEDDED_GDOWN_FLAG)
        raise SystemExit(run_embedded_gdown(sys.argv[gdown_index + 1 :]))

    no_browser = "--no-browser" in sys.argv
    selected_port = DEFAULT_APP_PORT
    if "--port" in sys.argv:
        port_index = sys.argv.index("--port") + 1
        if port_index >= len(sys.argv):
            raise SystemExit("--port requires an integer")
        selected_port = int(sys.argv[port_index])
    main(open_browser=not no_browser, port=selected_port)
