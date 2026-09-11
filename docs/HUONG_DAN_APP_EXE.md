# Hướng dẫn VEO3 Auto Pipeline EXE

## Bản EXE độc lập

`VEO3_AUTO_APP.exe` đã chứa Python, Playwright, Pillow, gdown và toàn bộ mã pipeline.
Máy sử dụng không cần cài Python và không cần giữ các file `.py` hoặc `.bat`.
App vẫn sử dụng Google Chrome đã cài trên máy để giữ dung lượng EXE nhỏ và dùng
profile đăng nhập automation ổn định.

File chạy:

```text
dist\VEO3_AUTO_APP.exe
```

Có thể chép riêng file EXE sang một thư mục mới. Trong lần mở đầu tiên, app tự tạo
`config.json`, các prompt mặc định và những thư mục dữ liệu cạnh EXE. Không đặt EXE
trong thư mục chỉ đọc như `Program Files`; nên đặt trong một thư mục riêng có quyền ghi.

Khi mở EXE, Chrome hiển thị một cửa sổ app riêng. Backend chỉ lắng nghe trên
`127.0.0.1` của máy hiện tại; giao diện không được publish ra Internet.

## Quy trình sử dụng

1. Chọn nguồn ảnh vải:
   - `Google Drive`: dán link thư mục Drive public vào ô link.
   - `Thư mục local`: nhấn `Chọn thư mục` và chọn thư mục chứa ảnh trên máy.
2. Nhấn `Lưu cấu hình`.
3. Nhấn `Mở Chrome automation`, đăng nhập ChatGPT trong cửa sổ Chrome đó.
4. Nhấn `Kiểm tra ChatGPT`.
5. Chọn các luồng cần chạy.
6. Nên bật `Chỉ xem trước` trong lần chạy đầu tiên.
7. Bỏ `Chỉ xem trước`, sau đó nhấn `Chạy các luồng`.

Khi nguồn là thư mục local, app tự bỏ qua `Import Drive` và dùng trực tiếp ảnh trong thư mục
đã chọn làm nguồn cho bước Crop. App không sửa hoặc xóa ảnh gốc trong thư mục local.

Các luồng chạy theo thứ tự:

```text
Import Drive -> Crop cố định -> Tạo seamless -> Đóng gói theo SKU
```

Ô `SKU` cho phép chạy một mã cụ thể. Ô `Giới hạn` giới hạn số lượng ảnh. Tùy chọn
`Cho phép thay thế` tương đương `--force` và không áp dụng cho bước import Drive.

Nút `Dừng` gửi tín hiệu dừng tới tiến trình hiện tại. Checkpoint đã lưu vẫn được
giữ để tiếp tục ở lần chạy sau.

Nếu tab hoặc cửa sổ Chrome automation bị đóng trong lúc đang tạo ảnh, app sẽ giữ SKU
đang dở ở trạng thái chờ, mở lại/kết nối lại Chrome và thử lại SKU đó bằng một chat mới.
Lỗi mất trình duyệt không làm tăng số lần retry của SKU. Nếu app không thể khôi phục sau
3 lần liên tiếp, hãy mở lại Chrome automation, kiểm tra đăng nhập ChatGPT rồi chạy lại.

## Quản lý Link Google Drive & Đối chiếu thư mục vải (Modal)

Nhấn icon folder `📁` nằm phía trên nút **Nhật ký thời gian thực** để mở modal **Quản lý Drive & So sánh vải**. Modal chiếm khoảng 3/4 màn hình và cho phép quản lý danh bạ link Google Drive, đồng thời đối chiếu chi tiết với toàn bộ thư mục vải đã có trên máy tính:

1. **Tự động quét thư mục vải:**
   - App tự động tìm thấy toàn bộ các thư mục vải hiện có trong `output/chatgpt/`, `textures_raw/`, `textures_cropped/` và `textures/` (kể cả những thư mục chưa từng gán link Drive).
   - Hiển thị tổng số thư mục, số lượng đã gán link Drive (`Đã ghép link`) và số lượng thư mục nội bộ (`Chưa gán link`).

2. **Gán và cập nhật Link Drive:**
   - Bấm nút **"+ Thêm/Gán Link Drive"** hoặc biểu tượng cây bút `✏️` trên từng dòng để gán/thay đổi link Google Drive của thư mục vải tương ứng.
   - Khi cần gỡ liên kết, chỉ cần xóa trắng link hoặc bấm nút gỡ.

3. **Ghép nối link hàng loạt (Bulk Match):**
   - Nhấn nút **"📋 Ghép nối link hàng loạt"**.
   - Dán một danh sách nhiều link Google Drive (mỗi link một dòng).
   - Hệ thống tự động đọc tiêu đề thư mục Drive, trích xuất mã vải gốc (VD: `1013 (Làm trước)` -> `1013`) và tự động đối chiếu ghép với thư mục vải trên máy, lưu trực tiếp vào `config.json`.

4. **Đối chiếu số lượng & Tiến độ hoàn thành:**
   - Bảng so sánh 8 cột trực quan:
     - **Tên thư mục vải:** Kèm huy hiệu trạng thái (Hoàn thành / Đang làm / Chưa làm / Trống).
     - **Link Drive:** Nhấp mở trực tiếp Drive trên trình duyệt.
     - **Ảnh Drive:** Số lượng ảnh gốc trên Google Drive (sau khi quét audit).
     - **Raw / Crop:** Số ảnh đã tải về máy và đã crop.
     - **Ảnh đã tạo:** Số lượng ảnh Seamless (01) và ảnh Swatch (02) đã hoàn thành.
     - **Tiến độ (%):** Thanh tiến độ màu trực quan theo tỷ lệ hoàn thành.
     - **Còn thiếu:** Số lượng SKU còn thiếu so với nguồn.
     - **Hành động:** Xem bảng kiểm kê chi tiết SKU (`Kiểm kê SKU`), Chạy riêng thư mục với ChatGPT (`▶ Chạy ChatGPT`), Mở thư mục trên máy (`📂 Mở folder`), hoặc ẩn thư mục khỏi danh sách quản lý (`✕`). Thao tác ẩn vẫn giữ nguyên link, lịch sử đồng bộ, thư mục gốc trên Google Drive và dữ liệu đã tải/tạo trên máy.

5. **Kiểm tra chi tiết SKU từng thư mục:**
   - Bấm nút `Kiểm kê SKU` để mở modal chi tiết:
     - Thống kê chi tiết từng SKU (Đã xong, Còn thiếu, Đang dở).
     - Nút **"📋 Copy SKU còn thiếu"** để copy nhanh danh sách mã chưa tạo.
     - Nút **"🚀 Chạy các SKU còn thiếu"** để nạp thẳng các SKU này vào pipeline chạy ngay lập tức.

## Kết quả

```text
textures_raw\<SKU>.<ext>
textures_cropped\<SKU>.png
textures\texture_<SKU>.png
output\chatgpt\<SKU>\seamless_texture.png
```

Các ảnh vải sau đó tiếp tục được lưu cùng thư mục SKU dưới tên `image_1.png`,
`image_2.png` và `metadata.json`.

## Đóng và build lại app

Dùng nút `Đóng ứng dụng` trong giao diện. App cũng tự tắt sau 30 phút không có
trình duyệt kết nối và không có pipeline đang chạy.

### 1. Bản Dev chạy siêu tốc (Khuyên dùng khi lập trình & kiểm thử)

Khi đang phát triển, sửa code liên tục:

- **Cách 1 (Nhanh nhất - 0s build time):** Mở trực tiếp file:
  ```text
  dist\VEO3_AUTO_APP_DEV.exe
  ```
  hoặc nháy đúp file `run_app_dev.bat` ở thư mục gốc.
  * **Cơ chế:** Nạp trực tiếp mã nguồn mới nhất từ thư mục dự án (`veo3_auto_app.py`, các script pipeline, prompt...).
  * **Ưu điểm:** Mỗi lần sửa code Python xong, **KHÔNG CẦN BUILD LẠI**, mở lên là ăn ngay code mới 100%!
  * **Console Debug:** Cửa sổ Console tự động mở để xem print log, traceback lỗi và HTTP request thời gian thực.

- **Cách 2 (Tạo lại file dev hoặc đóng gói PyInstaller Onedir):**
  Chạy script:
  ```powershell
  .\build_app_dev.bat
  ```
  * Tự động tạo file `dist\VEO3_AUTO_APP_DEV.exe` trong 1 giây.
  * Có tùy chọn đóng gói PyInstaller Onedir (`dist\dev\VEO3_AUTO_APP_DEV\VEO3_AUTO_APP_DEV.exe`) nếu cần test môi trường đóng gói độc lập.

### 2. Bản Final Release (Đóng gói phân phối)

Khi code đã hoàn thiện và muốn tạo 1 file `.exe` duy nhất để gửi cho người dùng:

```powershell
.\build_app.bat
```

- **Vị trí file:** `dist\VEO3_AUTO_APP.exe` (1 file duy nhất, ẩn console).
- Lệnh build tự cài/cập nhật các dependency, tạo cấu hình khởi đầu an toàn (đã loại bỏ link Drive/Telegram cá nhân), rồi nén toàn bộ pipeline vào 1 file EXE duy nhất.

Nếu chạy EXE ngay trong `dist` của project cũ, app tự nhận thư mục project cha để giữ
nguyên cấu hình và dữ liệu hiện có. Khi chép EXE sang nơi khác, thư mục chứa EXE trở
thành thư mục dữ liệu mới.
