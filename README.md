# VEO3 Auto Fabrics & Seamless Pipeline 🧵

Công cụ tự động hóa xử lý ảnh vải, tạo texture với ChatGPT và thuật toán vá viền Seamless offline cho thiết kế thời trang 3D (CLO3D, Blender, Marvelous Designer).

---

## ⚡ Cài đặt nhanh

1. Cài đặt môi trường:
```powershell
.\setup.bat
```
*(hoặc: `pip install playwright pillow numpy scipy gdown`)*

2. Tạo file cấu hình:
```powershell
copy config.example.json config.json
```

---

## 🚀 Cách sử dụng

### 1. Giao diện Desktop (Khuyên dùng)
```powershell
.\run_app.bat
```
- Bấm **"Mở Chrome automation"** ➔ đăng nhập ChatGPT trên cửa sổ vừa mở.
- Chọn thư mục vải hoặc dán link Google Drive ➔ bấm **"Chạy các luồng"**.
- Bấm **"🔧 Vá lỗi Seamless"** để khử đốm sáng và triệt tiêu mép nối texture.

### Chế độ phát triển có hot reload
```powershell
.\run_app_dev.bat
```
- Frontend nằm trong `dashboard/app.html`, `dashboard/styles.css` và `dashboard/app.js`;
  lưu bất kỳ file nào cũng sẽ tự reload cửa sổ app.
- Khi một file Python thay đổi, backend tự restart và cửa sổ app tự kết nối lại.
- Không cần build `.exe` trong quá trình phát triển. Chỉ dùng `build_app.bat` khi phát hành.

### 2. Dòng lệnh (CLI)
- **Vá viền Seamless & khử lệch sáng (không cần mạng):**
  ```powershell
  # Vá toàn bộ thư mục:
  python run_algorithm_seamless_batch.py --folder 1013 --force

  # Vá 1 SKU:
  python run_algorithm_seamless_batch.py --sku 101301 --force
  ```

- **Chạy ChatGPT tạo texture:**
  ```powershell
  .\start_chrome.bat
  python run_chatgpt_texture_grouped_batch.py --folder 1013
  ```

---

## 📦 Đóng gói file .EXE
```powershell
.\build_app.bat
```
File chạy xuất ra tại: `dist\VEO3_AUTO_APP.exe`.

---

## 🧪 Kiểm thử
```powershell
python -m unittest discover -s tests -p "test_*.py"
```

## 🗂️ Cấu trúc dự án

```text
fabrics/
├── run_app.bat, run_app_dev.bat   # Chạy ứng dụng
├── build_app.bat                  # Đóng gói bản phát hành
├── veo3_auto_app.py               # Backend/API chính
├── dashboard/                     # HTML, CSS và JavaScript
├── docs/                          # Tài liệu và hướng dẫn
├── tests/                         # Kiểm thử tự động
├── tools/build/                   # Công cụ hỗ trợ PyInstaller
├── tools/diagnostics/             # Công cụ kiểm tra và sửa trạng thái
├── tools/windows/                 # Tích hợp native Windows
└── prompts/                       # Prompt dùng cho pipeline
```

---

## ⚠️ Lưu ý bảo mật
- Không push `config.json` (chứa Token Telegram) và thư mục `request/` (chứa session Chrome).
- Thư mục ảnh nặng (`output/`, `textures*/`) đã được tự động bỏ qua trong `.gitignore`.
