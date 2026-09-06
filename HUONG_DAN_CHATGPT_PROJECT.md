# Hướng dẫn ba luồng ChatGPT Project

## Luồng mới: 5 texture trong một chat ChatGPT

Luồng này chạy độc lập với ba luồng Project. Mỗi cuộc trò chuyện mới hoạt động như sau:

1. Ảnh đầu tiên: upload ảnh từ `textures_raw`, dán toàn bộ nội dung `prompts/prompts_attachments/01_TEXTURE_SEAMLESS_MASTER.md`, rồi yêu cầu ChatGPT tạo ảnh texture.
2. Từ ảnh thứ hai: upload ảnh raw mới và gửi yêu cầu tiếp tục tạo texture theo master prompt ở đầu chat.
3. Sau 5 ảnh tạo thành công: chờ một khoảng ngẫu nhiên rồi mở cuộc trò chuyện mới và lặp lại từ bước 1.

Các khoảng nghỉ đều được chọn ngẫu nhiên trong các dải tại `chatgpt_texture_grouped.pacing` của `config.json`. Nếu ChatGPT yêu cầu xác minh người dùng hoặc báo giới hạn tạo ảnh, batch sẽ dừng an toàn thay vì cố vượt qua.

```powershell
.\run_chatgpt_texture_grouped.bat --check-browser
.\run_chatgpt_texture_grouped.bat --dry-run --limit 10
.\run_chatgpt_texture_grouped.bat --sku 101301
```

Có thể đổi số ảnh mỗi chat khi cần, ví dụ:

```powershell
.\run_chatgpt_texture_grouped.bat --images-per-chat 5
```

Đầu ra vẫn được lưu tại `textures/texture_<SKU>.png`; trạng thái riêng nằm trong `status_chatgpt_texture_grouped.json`. Các chat đã tạo không bị xóa tự động.

Ba luồng mới chạy độc lập với các batch cũ. Chúng dùng chung một ChatGPT Project có hai Project sources:

- `01_TEXTURE_SEAMLESS_MASTER.md`
- `02_FABRIC_SWATCH_MASTER.md`

Sao chép nội dung `prompts/chatgpt_project_instructions.md` vào Project instructions. Sau đó mở Project trên ChatGPT Web, sao chép URL dạng `https://chatgpt.com/g/g-p-.../project` và gán vào `chatgpt_project.project_url` trong `config.json`.

## Chuẩn bị Chrome

```powershell
.\start_chrome.bat
```

Giữ cửa sổ Chrome automation mở và đăng nhập ChatGPT trong cửa sổ đó. Ba luồng mới dùng chung port `9333` và file khóa với tất cả batch cũ, nên không chạy song song.

## Luồng 1: raw → texture

Đầu vào: `textures_raw/<SKU>.*`

Đầu ra: `textures/texture_<SKU>.png`, PNG 2048×2048.

```powershell
.\run_chatgpt_project_texture.bat --check-browser
.\run_chatgpt_project_texture.bat --dry-run
.\run_chatgpt_project_texture.bat --sku 101301
```

Texture hợp lệ đã có sẽ được bỏ qua. Dùng `--force` nếu muốn thay thế có chủ ý.

## Luồng 2: texture → một ảnh fabric

Đầu vào: `textures/texture_<SKU>.*`

Đầu ra:

```text
output/chatgpt_project_fabric/<SKU>/image_1.png
output/chatgpt_project_fabric/<SKU>/metadata.json
```

Ảnh được chuẩn hóa thành PNG 2048×1536, tỷ lệ 4:3 landscape.

```powershell
.\run_chatgpt_project_fabric.bat --check-browser
.\run_chatgpt_project_fabric.bat --dry-run
.\run_chatgpt_project_fabric.bat --sku 603104
```

## Luồng 3: texture → hai ảnh fabric

Đầu vào: `textures/texture_<SKU>.*`

Đầu ra:

```text
output/chatgpt_project_fabric_2/<SKU>/image_1.png
output/chatgpt_project_fabric_2/<SKU>/image_2.png
output/chatgpt_project_fabric_2/<SKU>/metadata.json
```

Hai ảnh được tạo bằng hai message độc lập trong cùng chat/SKU. Ảnh nguồn được upload lại cho từng lượt để tránh dùng ảnh sinh trước đó làm material reference.

```powershell
.\run_chatgpt_project_fabric_2.bat --check-browser
.\run_chatgpt_project_fabric_2.bat --dry-run
.\run_chatgpt_project_fabric_2.bat --sku 603104
```

## Tùy chọn chung

- `--sku <SKU>`: chỉ chạy một SKU.
- `--limit <N>`: giới hạn số SKU.
- `--dry-run`: xem trước, không mở Chrome.
- `--force`: tạo lại và thay thế output đã có.
- `--check-browser`: kiểm tra CDP, đăng nhập, Project URL và composer.
- `--project-url <URL>`: dùng URL tạm thời thay cho giá trị trong `config.json`.

Mỗi luồng có status và log riêng:

```text
status_chatgpt_project_texture.json
status_chatgpt_project_fabric.json
status_chatgpt_project_fabric_2.json
logs/chatgpt_project/<flow>/
```

Chat chỉ bị xóa sau khi tất cả output của SKU đã được tải, xác thực và lưu trạng thái. Nếu xóa chat thất bại, batch dừng và ưu tiên dọn chat đó khi chạy lại.
