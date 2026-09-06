# Luồng import Drive, crop và đóng gói seamless texture

## 1. Cấu hình link Google Drive

Thư mục phải mở được trong cửa sổ ẩn danh với quyền `Anyone with the link - Viewer`.
Điền link chia sẻ vào `config.json` và bật luồng:

```json
"google_drive": {
  "enabled": true,
  "share_url": "https://drive.google.com/drive/folders/FOLDER_ID?usp=sharing",
  "destination_dir": "textures_raw",
  "recursive": false
}
```

Tên ảnh trong Drive phải là `<SKU>.<đuôi>`, ví dụ `SP1M29.jpg`. Mặc định chỉ ảnh ở
cấp đầu tiên của thư mục được import. Ảnh đã có cùng tên trong `textures_raw` sẽ được
bỏ qua. Nếu cùng SKU nhưng khác đuôi file, chương trình báo xung đột và không tải.

Chạy `setup.bat` một lần để cài thêm `gdown`, sau đó xem trước và import:

```powershell
.\run_import_google_drive.bat --dry-run
.\run_import_google_drive.bat
```

Có thể giới hạn hoặc chọn một SKU:

```powershell
.\run_import_google_drive.bat --limit 10
.\run_import_google_drive.bat --sku SP1M29
```

## 2. Crop cố định

Ảnh gốc nằm trong `textures_raw`. Ảnh crop được lưu thành PNG trong
`textures_cropped`; ảnh gốc không bị sửa hoặc xóa.

Khung crop mặc định trong `config.json`:

```json
"crop": {
  "source_dir": "textures_raw",
  "output_dir": "textures_cropped",
  "mode": "normalized",
  "box": [0.02, 0.02, 0.65, 0.47]
}
```

Thứ tự của `box` là `[left, top, right, bottom]`, tính theo tỷ lệ từ `0` đến `1`.
Xem trước danh sách hoặc crop:

```powershell
.\run_crop_textures.bat --dry-run
.\run_crop_textures.bat --limit 10
.\run_crop_textures.bat
```

Nếu ảnh crop đã tồn tại nhưng ảnh nguồn hoặc khung crop đã đổi, chương trình giữ bản
cũ và báo `stale`. Kiểm tra lại khung rồi cho phép thay thế:

```powershell
.\run_crop_textures.bat --sku SP1M29 --force
```

Các flow raw-to-texture đã được cấu hình đọc ảnh từ `textures_cropped`.

## 3. Đóng gói seamless texture theo SKU

Seamless texture canonical vẫn được giữ theo quy tắc cũ:

```text
textures\texture_SP1M29.png
```

Sau khi tạo và kiểm tra thành công, các flow seamless tự sao chép thêm một bản vào:

```text
output\chatgpt\SP1M29\seamless_texture.png
```

Các ảnh vải do flow cũ tạo tiếp tục nằm cùng thư mục:

```text
output\chatgpt\SP1M29\
  seamless_texture.png
  image_1.png
  image_2.png
  metadata.json
```

Để đóng gói lại những seamless texture đã có từ trước:

```powershell
.\run_package_seamless.bat --dry-run
.\run_package_seamless.bat
```

Nếu bản đã đóng gói khác nội dung, chương trình không tự ghi đè. Sau khi xác nhận:

```powershell
.\run_package_seamless.bat --sku SP1M29 --force
```

## 4. Thứ tự chạy đề xuất

```powershell
.\run_import_google_drive.bat
.\run_crop_textures.bat
.\run_chatgpt_texture_grouped.bat --dry-run
.\run_chatgpt_texture_grouped.bat
```

Các checkpoint tương ứng:

```text
status_google_drive_import.json
status_crop.json
status_chatgpt_texture_grouped.json
```
