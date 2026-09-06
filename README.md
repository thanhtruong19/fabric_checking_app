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
python -m unittest discover -p "test_*.py"
```

---

## ⚠️ Lưu ý bảo mật
- Không push `config.json` (chứa Token Telegram) và thư mục `request/` (chứa session Chrome).
- Thư mục ảnh nặng (`output/`, `textures*/`) đã được tự động bỏ qua trong `.gitignore`.
