# **DUNNIO MASTER TEXTURE — PRODUCTION PROMPT**

Khi tôi cung cấp **một hình ảnh vải** (ảnh chụp, ảnh scan, ảnh crop, ảnh screenshot hoặc ảnh texture), hãy xử lý nó để tạo ra **01 file MASTER TEXTURE duy nhất**, dùng làm **vật liệu kỹ thuật cho CLO3D, Blender, Website 3D và các hệ thống dựng vật liệu số**.

## **MỤC TIÊU ĐẦU RA**

Tạo ra **một seamless fabric base color texture chuẩn kỹ thuật**, có thể dùng để **tile lặp lại nhiều lần trên bề mặt 3D** mà:

- không thấy đường nối;
- không thấy ô lặp;
- không lệch sọc/kẻ;
- không bị ghosting;
- không có vùng sáng tối lặp chu kỳ;
- không làm sai màu, sai pattern, sai weave hoặc sai tỷ lệ vật liệu.

Đây là **production asset for 3D material mapping**, **không phải ảnh vải đẹp**, **không phải ảnh swatch**, **không phải ảnh có nếp sóng**, **không phải ảnh marketing**.

## **1) ẢNH NGUỒN LÀ DỮ LIỆU BẮT BUỘC**

Ảnh tôi cung cấp là **authoritative source**.

Phải giữ nguyên tối đa các thuộc tính sau:

- màu nền;
- màu sợi;
- màu sọc/kẻ;
- hue;
- saturation tương đối;
- value / độ sáng tương đối;
- weave structure;
- hướng dệt;
- cấu trúc sợi;
- mật độ sợi;
- micro texture;
- độ thô/mịn;
- pattern;
- pattern scale;
- stripe width;
- line spacing;
- check size;
- repeat rhythm;
- hướng pattern;
- đặc tính thị giác tự nhiên của mặt vải.

**Không được thiết kế lại textile.  
****Không được tạo một loại vải mới lấy cảm hứng từ ảnh gốc.  
****Không được "làm đẹp" bằng cách đổi màu, tăng tương phản hoặc tăng độ nét quá mức.**

## **2) MỤC TIÊU KỸ THUẬT CHÍNH**

Texture đầu ra phải đáp ứng đồng thời các tiêu chí sau:

### **A. Seamless Continuity**

- Mép trái phải nối liên tục với mép phải.
- Mép trên phải nối liên tục với mép dưới.
- Khi tile lặp nhiều lần, pattern phải tiếp tục liền mạch qua boundary.

### **B. Exact Pattern Periodicity**

- Phải xác định đúng chu kỳ lặp thực tế của pattern theo cả trục X và Y.
- Với pinstripe, chalk stripe, check, plaid, windowpane, herringbone, twill, birdseye hoặc micro-pattern, phải giữ đúng nhịp lặp và khoảng cách.

### **C. Texture Fidelity**

- Giữ đúng cấu trúc vật liệu thực tế.
- Không thêm chi tiết dệt mới.
- Không thay đổi scale giữa sợi và pattern.

### **D. Lighting Neutrality**

- Kết quả cuối phải là **flat technical texture**.
- Không còn gradient ánh sáng môi trường.
- Không có shadow.
- Không có highlight.
- Không có chiều sâu giả.

## **3) QUY TRÌNH XỬ LÝ BẮT BUỘC**

### **Bước 1 — Chọn vùng vật liệu chuẩn**

- Chỉ sử dụng vùng vải rõ nhất, sạch nhất, hữu ích nhất.
- Loại bỏ các yếu tố không thuộc bản thân textile như:
  - mép vải;
  - nền;
  - tay người;
  - tem;
  - chữ;
  - logo;
  - watermark;
  - bụi;
  - nếp gấp;
  - vật thể lạ;
  - bóng đổ do chụp;
  - giao diện màn hình;
  - con trỏ;
  - viền screenshot.

### **Bước 2 — Perspective correction**

- Hiệu chỉnh để bề mặt vải vuông góc hoàn toàn với camera.
- Nếu có pattern sọc/kẻ:
  - sọc ngang phải song song trục X;
  - sọc dọc phải song song trục Y.
- Không để pattern nghiêng sai do lỗi chụp.

### **Bước 3 — Lighting normalization**

- Loại bỏ ánh sáng không đều trên ảnh nguồn.
- Loại bỏ:
  - hotspot;
  - vignette;
  - color cast;
  - bóng đổ;
  - phản xạ;
  - glare;
  - gradient sáng tối;
  - ánh sáng môi trường không đồng nhất.
- Tuy nhiên phải giữ nguyên micro-detail của vật liệu.

### **Bước 4 — Color fidelity**

- Giữ màu bám sát ảnh nguồn nhất có thể.
- Không tăng saturation.
- Không tăng contrast chỉ để ảnh đẹp hơn.
- Không làm navy thành royal blue.
- Không làm charcoal thành black.
- Không làm wool matte thành shiny fabric.

### **Bước 5 — Preserve material structure**

- Giữ đúng:
  - weave structure;
  - yarn appearance;
  - fiber density;
  - surface grain;
  - micro irregularity tự nhiên.
- Không được:
  - thêm nếp;
  - thêm sóng vải;
  - thêm shadow 3D;
  - thêm chiều sâu giả;
  - blur mạnh;
  - sharpen quá mức;
  - tạo bề mặt nhựa hóa.

### **Bước 6 — Identify actual repeat**

- Phân tích chính xác chu kỳ lặp thật của textile theo X và Y.
- Không cắt ngẫu nhiên ở giữa chu kỳ hoa văn.
- Với sọc/kẻ/họa tiết hình học, phải xác định đúng:
  - khoảng cách lặp;
  - phase bắt đầu;
  - phase kết thúc.

### **Bước 7 — Pattern alignment**

- Chọn ranh giới tile tại vị trí có logic chu kỳ.
- Đảm bảo pattern đi qua mép tile mà vẫn liên tục về khoảng cách, hướng và nhịp lặp.
- Không để stripe cuối mép trái bị lệch khoảng cách so với stripe đầu mép phải của tile kế tiếp.

### **Bước 8 — 50% offset validation**

- Thực hiện kiểm tra offset 50% theo X và 50% theo Y.
- Đưa seam vào giữa ảnh để kiểm tra và sửa.
- Nếu có seam, chỉ sửa bằng phương pháp hòa trộn giữ nguyên cấu trúc vật liệu.
- Không dùng blur mạnh để che seam.

### **Bước 9 — Eliminate seam artifacts**

- Không xuất hiện:
  - sọc đôi;
  - đường mờ;
  - vùng stretch/compress;
  - vùng lệch mật độ sợi;
  - patch khác texture;
  - lặp ô dễ nhận biết.

### **Bước 10 — Periodic consistency**

- Texture phải có tính tuần hoàn ổn định theo cả hai trục.
- Pattern spacing không được thay đổi khi vượt qua boundary.
- Không xuất hiện "ô vuông lặp" khi nhân bản tile.

### **Bước 11 — Repeat validation**

- Kiểm tra texture ở:
  - texture đơn;
  - offset 50%;
  - preview repeat 3×3;
  - preview repeat 15×15.
- Chỉ xuất file khi ở cả nhìn gần và nhìn xa đều không thấy:
  - seam line;
  - grid artifact;
  - checkerboard effect;
  - periodic lighting;
  - repeating flaw quá rõ;
  - lệch stripe/check.

### **Bước 12 — No reinterpretation**

- Không thay đổi:
  - số lượng sọc;
  - độ rộng sọc;
  - khoảng cách sọc;
  - kích thước ô;
  - góc dệt;
  - scale weave.
- Nếu ảnh nguồn không đủ dữ liệu để xác định chính xác, **không được tự sáng tạo**.

## **4) NHỮNG GÌ TUYỆT ĐỐI KHÔNG ĐƯỢC XUẤT HIỆN**

- Không nếp vải
- Không sóng vải
- Không bóng đổ
- Không highlight
- Không ánh sáng kịch tính
- Không chiều sâu giả
- Không background
- Không mép vải
- Không tay người
- Không mannequin
- Không sản phẩm may mặc hoàn chỉnh
- Không chữ
- Không logo
- Không watermark
- Không border
- Không artifacts do JPEG/compression
- Không reinterpret textile
- Không beautified fabric photo

## **5) ĐẦU RA BẮT BUỘC**

Xuất **01 MASTER TEXTURE chính** với tiêu chuẩn:

- **Base Color / Diffuse Texture only**
- **Square 1:1**
- **2048 × 2048 px**
- **sRGB**
- **PNG lossless**
- không watermark
- không chữ
- không border
- không shadow
- không nếp

Nếu hệ thống hỗ trợ, xuất thêm để kiểm tra chất lượng:

- **Preview Repeat 3×3**
- **Preview Repeat 15×15**

Nhưng asset chính vẫn là:  
**01 file MASTER TEXTURE duy nhất**.

## **6) THỨ TỰ ƯU TIÊN**

Khi có xung đột giữa tính đẹp và tính đúng, luôn ưu tiên:

**Original Fabric Fidelity  
→ Exact Pattern Periodicity  
→ Seamless Continuity  
→ 15×15 Repeat Stability  
→ Weave Accuracy  
→ Color Accuracy  
→ Lighting Neutrality  
→ Visual Enhancement**

Không được hy sinh độ chính xác kỹ thuật để tạo ra một hình ảnh đẹp hơn.

## **7) QUY TẮC AN TOÀN DỮ LIỆU**

Nếu ảnh nguồn:

- quá mờ;
- quá nhỏ;
- pattern không đủ nhìn rõ;
- weave không thể xác định;
- ảnh bị che khuất nhiều;
- không đủ dữ liệu để xác lập repeat chính xác;

thì **không được tự tưởng tượng để lấp chỗ thiếu**.

Thay vào đó:

- giữ tối đa dữ liệu thực có;
- tránh bịa texture;
- ưu tiên độ trung thực hơn độ đẹp.

## **8) CRITICAL ENGLISH INSTRUCTION**

**The uploaded fabric image is the authoritative source. Reconstruct a technically seamless base color texture from the actual textile, not a new fabric inspired by it. Preserve the original color relationships, yarn appearance, weave structure, pattern scale, line spacing, repeat logic, and surface character as faithfully as possible. Correct perspective, remove photographic lighting gradients, eliminate non-textile artifacts, and produce a mathematically believable tileable texture intended for 3D material mapping. Do not add folds, shadows, highlights, depth, drapery, or any beautified swatch-photography effects. The result must function as a production-ready seamless master texture for CLO3D, Blender, and 3D web visualization.**