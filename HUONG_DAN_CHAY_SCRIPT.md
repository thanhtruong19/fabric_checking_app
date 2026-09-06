# Hướng dẫn chạy script VEO3/Flow và ChatGPT

Tất cả lệnh dưới đây được chạy bằng PowerShell tại thư mục project:

```powershell
cd D:\T4T\AI\VEO3_AUTO
```

## 1. Cài đặt môi trường

Chạy một lần trên mỗi máy hoặc mỗi phiên bản Python mới:

```powershell
.\setup.bat
```

Kiểm tra phiên bản Python đang được dùng:

```powershell
python --version
```

`run.bat`, `run_chatgpt.bat`, `run_chatgpt_texture.bat` và `run_flow_texture.bat` tự tìm Python đã cài, nên có thể dùng trên máy chạy Python 3.13 hoặc 3.14.

## 2. Chuẩn bị ảnh texture

Đặt ảnh đầu vào trong thư mục `textures` và đặt tên theo mẫu:

```text
textures\texture_101308.png
textures\texture_ABC123.jpg
```

Phần nằm sau `texture_` và trước phần mở rộng là SKU. Ví dụ, file `texture_101308.png` có SKU là `101308`.

Nếu bắt đầu từ ảnh scan chưa xử lý, đặt ảnh vào `textures_raw` và giữ SKU làm tên file:

```text
textures_raw\101301.jpg
textures_raw\ABC123.png
```

Batch tiền xử lý ChatGPT sẽ tạo `textures\texture_101301.png` và `textures\texture_ABC123.png`.

## 3. Khởi động Chrome automation

```powershell
.\start_chrome.bat
```

Script mở một Chrome profile riêng trên port `9333`. Giữ cửa sổ này mở trong suốt quá trình chạy.

- Khi dùng VEO3/Google Flow: đăng nhập Google và mở Google Flow trong cửa sổ này.
- Khi dùng ChatGPT: mở `https://chatgpt.com/` và đăng nhập trong chính cửa sổ này.

Không chạy batch VEO3 và ChatGPT cùng lúc vì cả hai dùng chung Chrome automation và file khóa.

## 4. Tạo seamless texture từ ảnh raw bằng ChatGPT

### Kiểm tra kết nối Chrome

```powershell
.\run_chatgpt_texture.bat --check-browser
```

### Xem trước các SKU sẽ xử lý

```powershell
.\run_chatgpt_texture.bat --dry-run
```

### Chạy tất cả ảnh raw chưa có texture hợp lệ

```powershell
.\run_chatgpt_texture.bat
```

### Chạy một SKU hoặc giới hạn số lượng

```powershell
.\run_chatgpt_texture.bat --sku 101301
.\run_chatgpt_texture.bat --limit 3
```

Mặc định, texture PNG 2048×2048 hợp lệ đã tồn tại sẽ được bỏ qua. Chỉ dùng `--force` khi thực sự muốn tạo lại và thay thế file hiện có:

```powershell
.\run_chatgpt_texture.bat --sku 101301 --force
```

Prompt được đọc từ `prompts\scanned_to_texture_prompt.md`. Kết quả được lưu thành `textures\texture_<SKU>.png`; trạng thái nằm trong `status_chatgpt_texture.json`; log và ảnh tải về không hợp lệ nằm trong `logs\chatgpt_texture`.

ChatGPT Web hiện có thể trả ảnh vuông 1254×1254 dù prompt yêu cầu 2048×2048. Script chấp nhận ảnh vuông từ 1024×1024 trở lên và upscale đồng đều bằng Lanczos thành PNG 2048×2048; không kéo giãn riêng trục X/Y. Ảnh sai tỷ lệ hoặc nhỏ hơn ngưỡng vẫn bị từ chối. Khi chạy lại, một bản tải trước đó trong thư mục `invalid` nếu đạt các điều kiện mới sẽ được phục hồi cục bộ, không tốn thêm lượt tạo ảnh.

Sau mỗi lần tạo, script chỉ xóa đúng chat automation đã ghi nhận URL. Chat được xóa sau khi PNG đã được lưu và xác thực. Nếu xóa thất bại, batch dừng và ưu tiên dọn chat đó khi chạy lại trước khi tạo chat mới.

## 5. Tạo seamless texture từ ảnh raw bằng Google Flow

### Kiểm tra kết nối Chrome

```powershell
.\run_flow_texture.bat --check-browser
```

### Xem trước hoặc chạy batch

```powershell
.\run_flow_texture.bat --dry-run
.\run_flow_texture.bat
```

### Chạy một SKU, giới hạn hoặc tạo lại

```powershell
.\run_flow_texture.bat --sku 101337
.\run_flow_texture.bat --limit 3
.\run_flow_texture.bat --sku 101337 --force
```

Batch upload từng ảnh từ `textures_raw` vào Google Flow, ép request sinh ảnh thành tỷ lệ `1:1`, dùng prompt `prompts\scanned_to_texture_prompt.md`, tải bản `2K`, rồi lưu `textures\texture_<SKU>.png`. Trạng thái nằm trong `status_flow_texture.json`; log nằm trong `logs\flow_texture`.

Flow không tạo chat riêng như ChatGPT. Sau khi hoàn thành một SKU, script quay lại project Flow đang dùng rồi upload SKU tiếp theo. Các media tile trong project Flow được giữ lại; script không tự xóa asset trên Flow.

Texture hợp lệ đã có trong `textures` được bỏ qua bất kể nó được tạo bằng ChatGPT hay Flow. Dùng `--force` nếu muốn thay thế bằng phiên bản Flow.

## 6. Các lệnh chạy VEO3/Google Flow

### Chạy tất cả SKU chưa hoàn thành

```powershell
.\run.bat
```

Hoặc chạy trực tiếp bằng Python:

```powershell
python -u .\run_batch.py
```

### Chạy một SKU cụ thể

```powershell
python -u .\run_batch.py --sku 101308
```

### Chạy tối đa một số lượng SKU

Ví dụ, chỉ xử lý tối đa 3 SKU chưa hoàn thành:

```powershell
python -u .\run_batch.py --limit 3
```

### Xem trước các SKU sẽ chạy

Lệnh này không mở hoặc điều khiển Chrome:

```powershell
python -u .\run_batch.py --dry-run
```

Xem trước một SKU cụ thể:

```powershell
python -u .\run_batch.py --sku 101308 --dry-run
```

Xem trước tối đa 3 SKU:

```powershell
python -u .\run_batch.py --limit 3 --dry-run
```

### Xem trợ giúp lệnh VEO3

```powershell
python .\run_batch.py --help
```

### Kết quả VEO3

```text
output\<SKU>\image_1.png
output\<SKU>\image_2.png
output\<SKU>\metadata.json
```

Trạng thái được lưu trong `status.json`; log được lưu trong thư mục `logs`.

## 7. Các lệnh chạy ChatGPT

### Kiểm tra kết nối với Chrome trước khi chạy

```powershell
.\run_chatgpt.bat --check-browser
```

Hoặc:

```powershell
python -u .\run_chatgpt_batch.py --check-browser
```

Nếu kết nối thành công, console sẽ hiển thị `Browser check passed`.

### Chạy tất cả SKU chưa hoàn thành

```powershell
.\run_chatgpt.bat
```

Hoặc chạy trực tiếp bằng Python:

```powershell
python -u .\run_chatgpt_batch.py
```

### Chạy một SKU cụ thể

```powershell
.\run_chatgpt.bat --sku 101308
```

Hoặc:

```powershell
python -u .\run_chatgpt_batch.py --sku 101308
```

### Chạy tối đa một số lượng SKU

Ví dụ, chỉ xử lý tối đa 3 SKU chưa hoàn thành:

```powershell
.\run_chatgpt.bat --limit 3
```

Hoặc:

```powershell
python -u .\run_chatgpt_batch.py --limit 3
```

### Xem trước các SKU sẽ chạy

Lệnh này không mở hoặc điều khiển Chrome:

```powershell
.\run_chatgpt.bat --dry-run
```

Xem trước một SKU cụ thể:

```powershell
.\run_chatgpt.bat --sku 101308 --dry-run
```

Xem trước tối đa 3 SKU:

```powershell
.\run_chatgpt.bat --limit 3 --dry-run
```

### Xem trợ giúp lệnh ChatGPT

```powershell
.\run_chatgpt.bat --help
```

Hoặc:

```powershell
python .\run_chatgpt_batch.py --help
```

### Kết quả ChatGPT

```text
output\chatgpt\<SKU>\image_1.png
output\chatgpt\<SKU>\image_2.png
output\chatgpt\<SKU>\metadata.json
```

Trạng thái được lưu trong `status_chatgpt.json`; log được lưu trong `logs\chatgpt`.

## 8. Quy trình chạy đề xuất

### Chuẩn hóa raw texture bằng ChatGPT

```powershell
cd D:\T4T\AI\VEO3_AUTO
.\start_chrome.bat
.\run_chatgpt_texture.bat --check-browser
.\run_chatgpt_texture.bat --dry-run
.\run_chatgpt_texture.bat --sku 101301
```

### Chuẩn hóa raw texture bằng Google Flow

```powershell
cd D:\T4T\AI\VEO3_AUTO
.\start_chrome.bat
.\run_flow_texture.bat --check-browser
.\run_flow_texture.bat --dry-run
.\run_flow_texture.bat --sku 101337
```

### VEO3/Google Flow

```powershell
cd D:\T4T\AI\VEO3_AUTO
.\start_chrome.bat
python -u .\run_batch.py --dry-run
python -u .\run_batch.py --sku 101308
```

### ChatGPT

```powershell
cd D:\T4T\AI\VEO3_AUTO
.\start_chrome.bat
.\run_chatgpt.bat --check-browser
.\run_chatgpt.bat --dry-run
.\run_chatgpt.bat --sku 101308
```

## 9. Cấu hình quan trọng

Các thiết lập nằm trong `config.json`:

- `paths.textures_dir`: thư mục ảnh đầu vào.
- `paths.output_dir`: thư mục kết quả VEO3.
- `download.resolution`: độ phân giải tải từ Flow (`1K`, `2K`, hoặc `4K`).
- `generation.aspect_ratio`: tỷ lệ ảnh (`1:1`, `3:4`, `4:3`, `9:16`, hoặc `16:9`).
- `retry.max_retries`: số lần thử lại tối đa.
- `chatgpt.output_dir`: thư mục kết quả ChatGPT.
- `chatgpt.delete_chat_after_done`: đặt `true` để xóa chat sau khi lưu đủ kết quả, hoặc `false` để giữ lại.
- `chatgpt_texture.raw_dir`: thư mục ảnh scan đầu vào.
- `chatgpt_texture.prompt_file`: prompt chuyển scan thành seamless texture.
- `chatgpt_texture.status_file`: checkpoint riêng của bước tiền xử lý.
- `chatgpt_texture.target_width` và `target_height`: kích thước PNG bắt buộc.
- `chatgpt_texture.allow_uniform_resize`: cho phép scale đồng đều ảnh vuông đạt ngưỡng lên kích thước đích.
- `chatgpt_texture.minimum_source_width` và `minimum_source_height`: kích thước nguồn nhỏ nhất được chấp nhận.
- `chatgpt_texture.delete_chat_after_done`: bắt buộc là `true` để dọn chat trước SKU kế tiếp.
- `flow_texture.raw_dir`: thư mục ảnh scan đầu vào cho Google Flow.
- `flow_texture.status_file`: checkpoint riêng của Flow raw-to-texture.
- `flow_texture.download_resolution`: độ phân giải tải từ Flow, mặc định `2K`.
- `flow_texture.target_width` và `target_height`: kích thước PNG đích.
- `browser.cdp_port`: port của Chrome automation, mặc định là `9333`.

## 10. Lưu ý khi chạy lại

- SKU có trạng thái `done` sẽ được bỏ qua.
- Ảnh kết quả đã tồn tại sẽ không được tạo lại.
- Với batch raw texture, file hiện có nhưng không phải PNG 2048×2048 sẽ không bị ghi đè nếu thiếu `--force`.
- Chat raw-to-texture còn tồn từ lần chạy lỗi sẽ được dọn trước khi script mở chat mới.
- Batch Flow raw-to-texture dùng chung file khóa với tất cả batch khác và không được chạy song song.
- Khi gặp CAPTCHA, hết phiên đăng nhập hoặc giới hạn tạo ảnh, script sẽ dừng an toàn.
- Sau khi xử lý vấn đề trong Chrome, chạy lại cùng lệnh để tiếp tục.
- Không mở hai batch cùng lúc.
- Nếu đổi phiên bản Python hoặc chuyển sang máy khác, hãy chạy lại `.\setup.bat`.

## 11. Dashboard trạng thái HTML

Khởi động server dashboard cục bộ:

```powershell
.\status_dashboard.bat
```

Trình duyệt sẽ mở `http://127.0.0.1:8765/`. Server chỉ lắng nghe trên máy hiện tại và tự làm mới dữ liệu mỗi 5 giây.

- `Raw → Texture`: đối chiếu `textures_raw`, `textures`, `status_chatgpt_texture.json` và `status_flow_texture.json`.
- `Texture → Fabrics`: đối chiếu `textures`, `output`, `output/chatgpt`, `status.json` và `status_chatgpt.json`.
- Dashboard hiển thị tiến độ tổng, SKU đang chạy, mục cần chú ý, retry, provider và preview ảnh.
- Nhấn `Ctrl+C` trong cửa sổ dashboard để dừng server.

Nếu port `8765` đang được dùng, chọn port khác:

```powershell
.\status_dashboard.bat --port 8877
```
