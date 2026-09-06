# VEO3 Auto Fabrics & Seamless Texture Pipeline 🧵✨

Hệ thống tự động hóa toàn diện cho quy trình xử lý, tạo và đóng gói chất liệu vải 3D (**Seamless Textures & Fabric Swatches**) phục vụ thiết kế thời trang kỹ thuật số (CLO3D, Marvelous Designer, Blender, Maya, Unreal Engine).

Tích hợp **ChatGPT Web Automation** (điều khiển Chrome qua giao thức CDP) và **Thuật toán xử lý biên Seamless ngoại tuyến** chất lượng cao (Equal-Power Wrap Blending & Illumination Flat-Fielding).

---

## 🌟 Tính năng nổi bật

- **🔧 Thuật toán vá Seamless Offline (Không đốm sáng, không ranh giới nối):**
  - **Multi-Scale Illumination Flat-Fielding:** Khử sạch 100% hiện tượng lệch sáng, góc tối (vignetting), đảm bảo 4 góc và tâm của mỗi tile có độ sáng đồng nhất tuyệt đối. Triệt tiêu hoàn toàn lỗi đốm sáng lặp ô cờ khi xếp ngói (tile) lên mô hình 3D.
  - **Equal-Power Cosine Wrap Blending:** Hòa trộn biên mượt mà (dải 15% canvas) với cơ chế bù phương sai tần số cao $\text{detail}_{blended} / \sqrt{(1-w)^2 + w^2}$, bảo toàn 100% độ sắc nét sợi dệt, không làm mờ, không tạo vết nẹp hay nếp gấp.
  - **Bộ kiểm định chất lượng (QC Previews):** Tự động xuất ảnh lệch tâm 50% (`QC_offset50.png`), xem trước xếp ngói 3×3 (`QC_tile_3x3.png`) và 15×15 (`QC_tile_15x15.png`), kèm chỉ số kiểm tra đường biên (**Seam Score < 1.0**).

- **🤖 Tự động hoá ChatGPT Web (Browser Automation):**
  - Kết nối trực tiếp vào Chrome profile cá nhân qua cổng CDP `9333`.
  - Tự động tải ảnh vải mẫu, áp dụng prompt master chuyên biệt cho chất liệu may mặc (`01_TEXTURE_SEAMLESS_MASTER.md` & `02_FABRIC_SWATCH_MASTER.md`).
  - Tự động tải về ảnh render 2K, phục hồi ảnh lỗi, dọn dẹp chat theo chu kỳ, tự động thử lại (retry) thông minh khi gặp lỗi mạng.
  - Tích hợp thông báo tiến độ, cảnh báo hạn mức (quota) qua **Telegram Bot**.

- **🖥️ Giao diện Desktop chuyên nghiệp (GUI Desktop App):**
  - Quản lý pipeline 4 bước: **Import Google Drive ➔ Crop chuẩn hóa ➔ Tạo ChatGPT/Thuật toán ➔ Đóng gói SKU**.
  - Bảng điều khiển đối chiếu Google Drive: Quét toàn bộ thư mục vải, đối chiếu số lượng ảnh gốc với ảnh hoàn thành, tính toán % tiến độ và phát hiện SKU còn thiếu.
  - Nút bấm 1-click để **Vá lỗi mép Seamless** cho từng SKU hoặc toàn bộ thư mục.

---

## 📁 Cấu trúc thư mục dự án

```text
VEO3_AUTO/
├── veo3_auto_app.py                    # Giao diện chính Desktop App (GUI)
├── run_algorithm_seamless_batch.py     # Thuật toán vá Seamless & Flat-fielding ngoại tuyến
├── run_chatgpt_texture_grouped_batch.py# Pipeline tự động ChatGPT tạo Texture theo nhóm thư mục
├── run_chatgpt_fabric_grouped_batch.py # Pipeline tự động ChatGPT tạo Swatch rủ vải
├── crop_textures.py                    # Tool cắt ảnh vải mẫu chuẩn hóa
├── import_google_drive.py              # Tool đồng bộ và tải ảnh từ Google Drive
├── seamless_packaging.py               # Module đóng gói chuẩn SKU và xuất báo cáo QC
├── veo3_runtime.py                     # Tiện ích môi trường runtime (hỗ trợ đóng gói .EXE)
│
├── prompts/                            # Tài liệu & Prompt master cho ChatGPT
│   ├── base_prompt.md
│   ├── fold_context.json
│   └── prompts_attachments/            # File hướng dẫn gắn kèm ChatGPT Project
│       ├── 01_TEXTURE_SEAMLESS_MASTER.md
│       └── 02_FABRIC_SWATCH_MASTER.md
│
├── dashboard/                          # Giao diện Web Dashboard hiển thị kết quả
│   ├── index.html
│   ├── styles.css
│   └── dashboard.js
│
├── tests/                              # Bộ kiểm thử Unit Test tự động (47 tests)
│   ├── test_algorithm_seamless.py
│   ├── test_veo3_auto_app.py
│   ├── test_crop_textures.py
│   └── test_import_google_drive.py
│
├── config.example.json                 # File cấu hình mẫu (an toàn, đã che token)
├── .gitignore                          # Cấu hình loại trừ file ảnh nặng & bảo mật
└── build_app.bat                       # Script đóng gói dự án thành file .EXE độc lập
```

---

## 🚀 Cài đặt & Khởi động nhanh

### 1. Yêu cầu hệ thống
- **Hệ điều hành:** Windows 10/11.
- **Python:** 3.10 trở lên (khuyên dùng Python 3.12 hoặc 3.13).
- **Trình duyệt:** Google Chrome.

### 2. Cài đặt môi trường
Mở PowerShell tại thư mục dự án:
```powershell
# Chạy script cài đặt tự động (tự cài Playwright, Pillow, NumPy, SciPy, gdown...):
.\setup.bat
```
*Hoặc cài đặt thủ công:*
```powershell
pip install playwright pillow numpy scipy gdown
python -m playwright install chromium
```

### 3. Cấu hình ban đầu
Sao chép file cấu hình mẫu và điền các thông số của bạn:
```powershell
copy config.example.json config.json
```
*(Mở `config.json` để điền Token Telegram nếu muốn nhận thông báo qua bot điện thoại)*.

---

## 📖 Hướng dẫn sử dụng

### Cách 1: Sử dụng Giao diện Desktop (Khuyên dùng)
Chạy script khởi động app:
```powershell
.\run_app.bat
```
1. **Khởi động Chrome:** Nhấn nút **"Mở Chrome automation"** trên app (Chrome sẽ mở ở chế độ điều khiển riêng). Đăng nhập vào [ChatGPT](https://chatgpt.com/) trên cửa sổ này.
2. **Chọn nguồn vải:** Chọn thư mục cục bộ chứa ảnh hoặc dán link Google Drive.
3. **Thực thi:** Chọn các luồng muốn chạy và bấm **"Chạy các luồng"**.
4. **Vá lỗi Seamless:** Nếu có ảnh texture AI bị lỗi viền nối hoặc đốm sáng, nhấn **"🔧 Vá lỗi Seamless (Fix Seams)"** để thuật toán tự động làm phẳng và triệt tiêu lỗi lặp.

### Cách 2: Chạy trực tiếp qua dòng lệnh (CLI)

#### 1. Vá lỗi / Làm Seamless texture (Thuật toán ngoại tuyến):
```powershell
# Vá toàn bộ texture trong một thư mục:
python run_algorithm_seamless_batch.py --folder 1013 --force

# Vá cho một SKU cụ thể:
python run_algorithm_seamless_batch.py --sku 101301 --force
```

#### 2. Chạy tự động ChatGPT tạo Texture:
```powershell
# Mở Chrome automation trước:
.\start_chrome.bat

# Chạy tạo Texture theo nhóm thư mục:
python run_chatgpt_texture_grouped_batch.py --folder 1013

# Chạy tạo Swatch rủ vải:
python run_chatgpt_fabric_grouped_batch.py --folder 1013
```

---

## 🧪 Kiểm thử (Testing)

Dự án có bộ kiểm thử tự động toàn diện (47 bài test) bao gồm kiểm thử thuật toán quang sai, phân giải hình học, giao tiếp luồng và giao diện:

```powershell
# Chạy toàn bộ test suite:
python -m unittest discover -p "test_*.py"

# Chạy riêng kiểm thử thuật toán Seamless:
python -m unittest test_algorithm_seamless.py
```

---

## 📦 Đóng gói thành file EXE độc lập

Bạn có thể xuất toàn bộ hệ thống thành 1 file chạy `.EXE` duy nhất không cần cài Python:
```powershell
.\build_app.bat
```
File kết quả sẽ nằm tại `dist\VEO3_AUTO_APP.exe` (tự động đính kèm toàn bộ thư viện, engine thuật toán và giao diện).

---

## 🔒 Quy chuẩn bảo mật & Git Policy
- **Tuyệt đối không commit `config.json`:** File chứa Token bot Telegram và Chat ID thật. Chỉ commit `config.example.json`.
- **Tuyệt đối không commit thư mục `request/`:** Chứa file `.har` lưu traffic mạng và session token thật của trình duyệt.
- **Không commit thư mục dữ liệu ảnh nặng:** `textures/`, `textures_raw/`, `textures_cropped/`, `output/` đã được cấu hình trong `.gitignore` để giữ repo nhẹ và chuẩn Git.
