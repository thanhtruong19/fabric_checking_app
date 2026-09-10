# KẾ HOẠCH PHÁT TRIỂN TOOL TỰ ĐỘNG HÓA TẠO ẢNH GOOGLE FLOW

> **Ngày tạo:** 22/08/2026  
> **Trạng thái:** Chờ lấy mẫu cURL từ Google Flow để viết code hoàn chỉnh.

---

## 1. Mục tiêu Dự án (Project Goal)
* Xây dựng script Python tự động hóa sinh ảnh hàng loạt trên **Google Flow** (`https://labs.google/fx/vi/tools/flow`).
* **Đầu vào (Input):** Thư mục `textures/` chứa hàng trăm ảnh texture (vải, hoa văn, bề mặt...).
* **Xử lý:** 
  1. Duyệt từng file ảnh trong `textures/`, lấy mã `SKU` từ tên file.
  2. Gán ảnh texture làm **ảnh tham chiếu (Reference Image)** trên Google Flow.
  3. Gửi request sinh ảnh theo **Prompt mẫu** (tạo 2 ảnh cho mỗi texture).
* **Đầu ra (Output):** Tự động tải và lưu 2 ảnh kết quả vào thư mục `fabrics/` với định dạng tên:
  * `fabrics_{sku}_1.png`
  * `fabrics_{sku}_2.png`

---

## 2. Kiến trúc & Tính năng Kỹ thuật đã thống nhất

```
[textures/ (SKU.jpg)] 
       │
       ▼ (1. Upload lấy Media ID)
[Google Flow Upload API] 
       │
       ▼ (2. Generate với Prompt + Media ID)
[Google Flow Generate API] 
       │
       ▼ (3. Download tự động)
[fabrics/ (fabrics_SKU_1.png & fabrics_SKU_2.png)]
```

* **Công nghệ:** Python Script độc lập (dùng thư viện `requests`, `Pillow`, `json`, `pathlib`).
* **Cơ chế Checkpoint & Resume:** Nếu bị ngắt giữa chừng (rớt mạng/tắt máy), khi bật lại script sẽ tự động kiểm tra `fabrics/`, SKU nào đã có đủ 2 ảnh thì **bỏ qua**, chỉ xử lý tiếp những SKU còn thiếu.
* **Chống Rate-limit & Checkpoint:** Tích hợp Random Delay (3 – 6 giây giữa các request) và cơ chế Auto-Retry khi gặp lỗi mạng.
* **Cấu hình tách rời:** Cookie, Token, Prompt mẫu, tham số cấu hình để riêng trong file `config.json` dễ tùy chỉnh.

---

## 3. Checklist việc BẠN (User) cần làm khi ngủ dậy ☀️

1. [ ] **Bước 1:** Mở trình duyệt Chrome và truy cập: [https://labs.google/fx/vi/tools/flow](https://labs.google/fx/vi/tools/flow) *(đã đăng nhập tài khoản Google)*.
2. [ ] **Bước 2:** Nhấn phím **`F12`** $\rightarrow$ Chuyển qua tab **Network** $\rightarrow$ Bấm chọn bộ lọc **Fetch/XHR**.
3. [ ] **Bước 3:** Thao tác trên web:
   * Upload 1 ảnh bất kỳ làm ảnh tham chiếu (Reference).
   * Gõ 1 prompt bất kỳ $\rightarrow$ Bấm nút **Generate / Tạo ảnh**.
4. [ ] **Bước 4:** Lấy 2 đoạn cURL trong tab Network:
   * Click chuột phải vào request Upload ảnh $\rightarrow$ **Copy** $\rightarrow$ **Copy as cURL (bash)**.
   * Click chuột phải vào request Generate ảnh $\rightarrow$ **Copy** $\rightarrow$ **Copy as cURL (bash)**.
5. [ ] **Bước 5:** Paste 2 đoạn cURL vào chat và gửi kèm nội dung **Prompt mẫu** bạn muốn dùng.

---

## 4. Checklist việc TÔI (AI) sẽ làm ngay sau đó 🚀

1. [ ] Phân tích cấu trúc Payload, Headers, Token từ 2 đoạn cURL của bạn.
2. [ ] Viết file cấu hình `config.json` (quản lý Cookie, Prompt, Delay, Paths).
3. [ ] Viết script Python hoàn chỉnh `run_batch.py` bao gồm:
   * Module upload ảnh tham chiếu.
   * Module gọi API tạo ảnh.
   * Module tự động download và đổi tên file theo SKU.
   * Cơ chế Checkpoint/Resume & Auto-retry.
4. [ ] Cùng bạn chạy thử nghiệm (Test Run) trên 1-2 SKU mẫu để kiểm tra chất lượng ảnh xuất ra.

---
*Chúc bạn ngủ ngon! Sáng mai gửi mẫu cURL là chúng ta triển khai xong ngay.*
