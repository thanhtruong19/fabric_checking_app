# Fabric Drape Prompt Guide

> Bộ prompt chuyển ảnh scan vải phẳng thành ảnh chụp vải gợn tự nhiên, ưu tiên tuyệt đối việc giữ nguyên màu sắc, hoa văn, tỷ lệ họa tiết và cấu trúc sợi.

---

## 1. Mục đích

Tài liệu này dùng cho các model chỉnh sửa ảnh như Gemini/Nano Banana, ChatGPT Image hoặc GPT Image API.

Đầu vào chính là **một ảnh scan vải phẳng**. Model chỉ được thay đổi hình học bề mặt để tạo độ gợn; không được thiết kế lại chất liệu.

Mỗi ảnh đầu ra được tạo bằng công thức:

```text
BASE PROMPT + ONE FOLD CONTEXT
```

Trong đó:

- `BASE PROMPT`: cố định cho tất cả ảnh.
- `FOLD CONTEXT`: chỉ chọn **một** ngữ cảnh trong danh sách ở Phần 5.
- Không ghép nhiều `FOLD CONTEXT` trong một lần tạo ảnh.

---

## 2. Chuẩn bị ảnh đầu vào

### Cách ổn định nhất

Chỉ tải lên ảnh scan vải và gọi nó là `Image 1`.

### Khi cần sử dụng ảnh tham chiếu nếp gợn

- `Image 1`: nguồn duy nhất cho màu, hoa văn, tỷ lệ họa tiết, kiểu dệt và mặt sợi.
- `Image 2` trở đi: chỉ tham khảo hình học nếp gợn, ánh sáng hoặc bố cục.
- Không dùng nhiều ảnh tham chiếu có các nếp song song rõ rệt.
- Nên crop ảnh tham chiếu để chỉ còn một hoặc hai nếp cần học.

Nếu model thường xuyên tạo quá nhiều sóng, hãy bỏ toàn bộ ảnh tham chiếu và chỉ điều khiển nếp bằng `FOLD CONTEXT` dạng text.

---

## 3. Quy tắc dành cho người vận hành và AI

1. Luôn dùng đầy đủ `BASE PROMPT`.
2. Chọn đúng một `FOLD CONTEXT` theo `context_id`.
3. Thay `{{FOLD_CONTEXT}}` trong base prompt bằng nội dung trường `Prompt` của context đã chọn.
4. Không đưa tiêu đề tiếng Việt hoặc phần giải thích vào prompt tạo ảnh.
5. Không tự kết hợp, mở rộng hoặc sáng tạo thêm nếp ngoài context được chọn.
6. Nếu độ chân thực xung đột với độ chính xác texture, ưu tiên texture.
7. Với xử lý hàng loạt, lưu `context_id` cùng tên file đầu ra để có thể tái tạo kết quả.

### Quy tắc máy đọc

```yaml
assembly:
  required_blocks:
    - BASE_PROMPT
    - exactly_one_FOLD_CONTEXT
  placeholder: "{{FOLD_CONTEXT}}"
  context_selection: exactly_one
  context_combination: forbidden
  primary_input: Image 1
  primary_priority: texture_fidelity
  permitted_change: surface_geometry_and_physical_lighting_only
```

---

## 4. Base Prompt

Copy toàn bộ prompt bên dưới, sau đó thay `{{FOLD_CONTEXT}}` bằng một context ở Phần 5.

```text
Edit Image 1 into an ultra-photorealistic close-up textile product photograph of the exact same fabric. Image 1 is the exclusive and immutable source for the fabric material, base colour, accent colours, pattern, pattern scale, weave and yarn detail.

SOURCE PRIORITY:
If additional reference images are supplied, use them only as loose references for fold geometry, lighting or photographic presentation. Never copy their colour, pattern, weave, yarn texture or material appearance. When any reference conflicts with Image 1, always follow Image 1.

ABSOLUTE TEXTURE FIDELITY — HIGHEST PRIORITY:
Treat Image 1 as an immutable, UV-locked base-colour texture applied to a physically deformed fabric surface. Preserve exactly:

- The original base colour, accent colours and tonal relationships.
- Every check, stripe, line and pattern intersection.
- The original line colour, line thickness, spacing and position relative to the weave.
- The exact size and proportions of every pattern repeat.
- The original weave structure, yarn direction, thread thickness, weave density and fine textile detail.

Do not redraw, regenerate, replace, beautify, clean up, sharpen, blur, simplify or reinterpret the textile. Do not invent additional fibres, grain, checks, stripes, noise or surface detail. Do not make the weave coarser, finer, smoother, sharper or more pronounced. Do not change the pattern scale. Do not remove, duplicate, break, shift or misalign any pattern element.

Only deform the original texture geometrically so it follows the three-dimensional surface. Pattern lines may bend smoothly over the selected deformation, but they must remain continuous and correctly connected. Their real physical spacing measured along the fabric surface must remain constant. Any apparent compression must result only from perspective and surface curvature.

SELECTED FOLD GEOMETRY:
{{FOLD_CONTEXT}}

GLOBAL FOLD GUARDRAILS:
Use only the selected fold geometry. Do not add any other fold arrangement. Keep 75–90% of the visible fabric approximately flat unless the selected context states otherwise. All deformations must be broad, shallow, smooth, asymmetrical and physically believable.

Do not create evenly spaced folds, repeated parallel ridges, accordion-like waves, rhythmic drapery, decorative pleats, sharp creases, narrow folds, pinched areas, crushed fabric, dramatic valleys or pointed peaks. Unless the selected context explicitly requests two folds, create only one primary deformation.

LIGHTING:
Use a large, soft, diffused studio light from the upper left. Add a gentle highlight over raised areas and soft, transparent shadows in shallow valleys. Avoid dark dramatic shadows, hard light, cinematic contrast and artificial colour grading. Use neutral daylight-balanced illumination and preserve the true colour of Image 1. The material has a matte premium suiting-fabric finish with only a subtle natural fibre sheen—never glossy, silky, metallic, plastic or velvet-like.

CAMERA AND COMPOSITION:
Use a near-overhead camera angle with minimal perspective distortion, equivalent to a 90–100 mm macro lens. Keep the weave and pattern clearly resolved across most of the image. Avoid excessive shallow depth of field that would hide or blur the pattern.

The fabric fills the entire frame and continues beyond all four edges. Preserve the source image's aspect ratio when possible. No fabric edge, selvedge, cut edge, background, table, hands, objects, labels, text, logos or watermark.

FINAL PRIORITY:
The result must look like the physical fabric from Image 1 after receiving only the selected gentle surface deformation. It must not look like a newly generated or redesigned textile. If visual drama conflicts with realism or texture accuracy, choose the flatter, quieter and more texture-faithful result.
```

---

## 5. Fold Context Library

> Chỉ chọn một context cho mỗi ảnh. Copy riêng phần `Prompt` của context và thay vào `{{FOLD_CONTEXT}}`.

### Context 01 — Classic Low Diagonal

```yaml
context_id: FD-01
intensity: subtle
fold_count: 1
flat_surface_target: 85_percent
```

**Mô tả:** Một gợn chéo thấp, an toàn và phù hợp với hầu hết loại vải.

**Prompt:**

```text
Create one broad, very shallow diagonal ripple extending from the lower-left toward the upper-right. Give it one softly rounded crest and an extremely subtle adjacent valley. Allow the ripple to fade gradually before reaching the corners. Keep approximately 85% of the visible fabric nearly flat.
```

### Context 02 — Reversed Low Diagonal

```yaml
context_id: FD-02
intensity: subtle
fold_count: 1
flat_surface_target: 85_percent
```

**Mô tả:** Gợn chéo theo hướng ngược lại để thay đổi nhịp bố cục.

**Prompt:**

```text
Create one broad, low diagonal ripple extending from the upper-left toward the lower-right. Keep it slightly off-centre and asymmetrical, with approximately 85% of the surrounding fabric remaining nearly flat. Let both ends fade smoothly into the surface.
```

### Context 03 — Gentle Curved Diagonal

```yaml
context_id: FD-03
intensity: subtle
fold_count: 1
flat_surface_target: 82_percent
```

**Mô tả:** Một gợn chéo cong nhẹ, tự nhiên hơn đường chéo thẳng.

**Prompt:**

```text
Create one shallow curved ripple that enters from the lower-left edge, bends gently through the centre and fades toward the upper-right. It must form one continuous relaxed curve, not several waves. Keep the areas on both sides broad and nearly flat.
```

### Context 04 — Soft S-Curve

```yaml
context_id: FD-04
intensity: subtle
fold_count: 1
flat_surface_target: 80_percent
```

**Mô tả:** Một đường cong chữ S dài, tạo cảm giác mềm nhưng không nhiều sóng.

**Prompt:**

```text
Create one extremely subtle, elongated S-shaped surface change across the frame. Use one continuous low crest that changes direction gradually. Keep the curve wide and relaxed. Do not turn the S-curve into multiple folds or parallel waves.
```

### Context 05 — Lower-Third Ripple

```yaml
context_id: FD-05
intensity: subtle
fold_count: 1
flat_surface_target: 85_percent
```

**Mô tả:** Nếp tập trung ở một phần ba phía dưới, phần còn lại gần phẳng.

**Prompt:**

```text
Keep the upper two-thirds of the fabric almost completely flat. Add one broad, shallow horizontal-to-diagonal ripple across the lower third, with an irregular natural curve, one low crest and a very soft valley.
```

### Context 06 — Upper-Third Ripple

```yaml
context_id: FD-06
intensity: subtle
fold_count: 1
flat_surface_target: 85_percent
```

**Mô tả:** Nếp tập trung ở một phần ba phía trên.

**Prompt:**

```text
Keep the lower two-thirds of the fabric nearly flat. Add one low, relaxed ripple across the upper third. Make it slightly diagonal and asymmetrical, then fade it smoothly into the surrounding flat fabric.
```

### Context 07 — Left-Edge Lift

```yaml
context_id: FD-07
intensity: subtle
fold_count: 1
flat_surface_target: 82_percent
```

**Mô tả:** Vải như được nâng nhẹ từ ngoài mép trái.

**Prompt:**

```text
Make the fabric appear to have been lifted very slightly from a point outside the left edge. Create one broad elevation entering from the left, reaching a low rounded crest near the left-centre and fading completely before reaching the right side.
```

### Context 08 — Right-Edge Lift

```yaml
context_id: FD-08
intensity: subtle
fold_count: 1
flat_surface_target: 82_percent
```

**Mô tả:** Vải như được nâng nhẹ từ ngoài mép phải.

**Prompt:**

```text
Make the fabric appear to have been lifted very slightly from a point outside the right edge. Create one shallow curved elevation entering from the right and gradually flattening toward the centre-left. Keep the crest low, wide and understated.
```

### Context 09 — Upper-Corner Relaxed Lift

```yaml
context_id: FD-09
intensity: subtle
fold_count: 1
flat_surface_target: 82_percent
```

**Mô tả:** Nâng rất nhẹ tại góc trên, tạo độ dốc chéo mềm.

**Prompt:**

```text
Lift the fabric very slightly near the upper-right corner, creating one broad diagonal slope that relaxes toward the centre. Keep the lift low and rounded. Do not form a pointed peak, bunching or secondary folds.
```

### Context 10 — Lower-Corner Relaxed Lift

```yaml
context_id: FD-10
intensity: subtle
fold_count: 1
flat_surface_target: 82_percent
```

**Mô tả:** Nâng nhẹ tại góc dưới, đối xứng định hướng với Context 09.

**Prompt:**

```text
Lift the fabric very slightly near the lower-left corner. Create one broad, softly rounded rise that fades diagonally into a mostly flat surface. Avoid bunching, compression, pointed peaks and additional folds around the corner.
```

### Context 11 — Off-Centre Low Mound

```yaml
context_id: FD-11
intensity: subtle
fold_count: 1
flat_surface_target: 80_percent
```

**Mô tả:** Một vùng nâng thấp lệch tâm, giống có vật rất mỏng nằm dưới vải.

**Prompt:**

```text
Create one wide, low and irregular elevation positioned away from the centre. It should resemble a gentle mound beneath relaxed fabric, with no circular outline, no pointed peak and no surrounding ring-shaped folds. Blend all slopes gradually into the flat surface.
```

### Context 12 — Shallow Crescent

```yaml
context_id: FD-12
intensity: subtle
fold_count: 1
flat_surface_target: 80_percent
```

**Mô tả:** Một gợn hình cung không hoàn chỉnh chạy dọc một bên ảnh.

**Prompt:**

```text
Create one broad crescent-shaped ripple along one side of the frame. The crescent must be incomplete, asymmetrical and very shallow, gradually disappearing into the flat fabric at both ends. Do not create a circular or ring-shaped fold.
```

### Context 13 — Fading Diagonal Ridge

```yaml
context_id: FD-13
intensity: subtle
fold_count: 1
flat_surface_target: 83_percent
```

**Mô tả:** Một nếp đi từ mép vào rồi biến mất trước khi chạy hết ảnh.

**Prompt:**

```text
Create one low diagonal ridge entering from one edge and gradually losing height until it disappears around two-thirds of the way across the image. Keep the ridge broad and rounded. It must not continue from edge to edge or generate a second ridge.
```

### Context 14 — Subtle Valley

```yaml
context_id: FD-14
intensity: subtle
fold_count: 1
flat_surface_target: 82_percent
```

**Mô tả:** Tạo một vùng lõm nông thay vì một nếp nổi rõ.

**Prompt:**

```text
Instead of a prominent raised fold, create one broad, shallow diagonal depression with softly sloping sides. Keep the valley light, open and gradual, with no dark crease and no raised parallel ridges beside it.
```

### Context 15 — Relaxed Saddle Surface

```yaml
context_id: FD-15
intensity: subtle_to_moderate
fold_count: 1_continuous_surface
flat_surface_target: 78_percent
```

**Mô tả:** Một bên nâng thấp và một bên lõm nông, nối bằng bề mặt chuyển tiếp mềm.

**Prompt:**

```text
Create a very subtle saddle-like surface: one broad low rise on one side and one shallow depression on the opposite side. Join them through one smooth continuous transition. Keep the deformation understated and most of the fabric nearly flat.
```

### Context 16 — Two Unequal Natural Ripples

```yaml
context_id: FD-16
intensity: moderate
fold_count: 2
flat_surface_target: 75_percent
```

**Mô tả:** Hai gợn không đều; một chính, một phụ rất mờ. Dùng ít hơn các context một nếp.

**Prompt:**

```text
Create two widely separated, shallow and unequal ripples. One ripple is broad and clearly dominant; the other is faint, shorter and partially fades into the flat surface. They must not be parallel, evenly spaced or visually symmetrical. Keep at least 75% of the fabric approximately flat.
```

### Context 17 — Single Ripple with Soft Branch

```yaml
context_id: FD-17
intensity: moderate
fold_count: 1_with_short_branch
flat_surface_target: 77_percent
```

**Mô tả:** Một nếp chính tách rất nhẹ gần mép ảnh.

**Prompt:**

```text
Create one dominant low diagonal ripple that separates very subtly into two fading slopes only near one outer edge. The branch must be short, shallow and barely visible. Do not create two full-length folds or a Y-shaped dramatic crease.
```

### Context 18 — Light Side Compression

```yaml
context_id: FD-18
intensity: moderate
fold_count: 2_maximum
flat_surface_target: 75_percent
```

**Mô tả:** Vải được đẩy nhẹ từ ngoài khung hình, tạo một nếp chính và một nếp phụ ngắn.

**Prompt:**

```text
Make the fabric appear to have been pushed very gently from one point outside the frame. Create one broad primary ripple and one much smaller incomplete secondary ripple near the same edge. Both must remain irregular and fade quickly, leaving the centre and opposite side mostly flat.
```

---

## 6. Phân phối context khi xử lý hàng loạt

Để 100 ảnh không giống nhau nhưng vẫn giữ phong cách nhất quán:

| Nhóm | Context | Tỷ lệ đề xuất | Đặc điểm |
|---|---|---:|---|
| Một gợn cơ bản | FD-01 đến FD-06 | 35% | Ổn định, sạch, dễ giữ hoa văn |
| Gợn phát sinh từ cạnh/góc | FD-07 đến FD-10 | 25% | Tự nhiên, thay đổi vị trí thị giác |
| Hình học đặc biệt nhẹ | FD-11 đến FD-15 | 25% | Đa dạng nhưng vẫn ít nếp |
| Hai gợn hoặc biến thể vừa | FD-16 đến FD-18 | 15% | Dùng làm điểm nhấn, không nên lạm dụng |

### Lựa chọn ngẫu nhiên có kiểm soát

```yaml
batch_selection:
  total_examples: 100
  allocation:
    FD-01_to_FD-06: 35
    FD-07_to_FD-10: 25
    FD-11_to_FD-15: 25
    FD-16_to_FD-18: 15
  rules:
    - do_not_repeat_same_context_more_than_3_times_consecutively
    - do_not_use_FD-16_to_FD-18_more_than_2_times_consecutively
    - preserve_original_image_order_for_file_naming
    - store_context_id_in_output_metadata_or_filename
```

Ví dụ tên file:

```text
original-name__FD-03__v01.png
original-name__FD-14__v02.png
```

---

## 7. Prompt sửa lỗi khi ảnh có quá nhiều sóng

Sử dụng trong lượt chỉnh sửa tiếp theo nếu kết quả xuất hiện nhiều nếp song song:

```text
Flatten the fabric substantially. Remove every secondary fold, repeated ridge and rhythmic wave. Keep only the single selected primary deformation. At least 85% of the visible surface must remain nearly flat. Preserve the existing fabric colour, weave, pattern, pattern scale and every pattern-line position exactly. Change only the surface geometry; do not regenerate or reinterpret the textile.
```

Nếu context được chọn là `FD-16`, `FD-17` hoặc `FD-18`, dùng phiên bản này:

```text
Reduce the fabric deformation substantially. Preserve only the explicitly requested dominant ripple and its permitted faint secondary deformation. Remove all other ridges and repeated waves. Keep at least 75% of the surface approximately flat. Preserve the fabric colour, weave, pattern and pattern scale exactly.
```

---

## 8. Checklist kiểm tra kết quả

### Texture

- [ ] Màu nền không bị đổi.
- [ ] Màu đường kẻ không bị đổi.
- [ ] Tỷ lệ ô hoặc họa tiết không bị phóng to/thu nhỏ.
- [ ] Đường kẻ không bị đứt, nhân đôi hoặc lệch tại nếp gợn.
- [ ] Mật độ và hướng sợi giống ảnh scan.
- [ ] AI không tự thêm grain hoặc làm mặt vải thô hơn.

### Nếp gợn

- [ ] Đúng số nếp được context cho phép.
- [ ] Phần lớn bề mặt vẫn gần phẳng.
- [ ] Không xuất hiện các nếp song song đều nhau.
- [ ] Không có đỉnh nhọn, rãnh đen hoặc nếp gấp kiểu rèm.
- [ ] Hai đầu nếp chuyển dần về bề mặt phẳng.

### Ánh sáng và bố cục

- [ ] Ánh sáng mềm, không làm sai màu thật.
- [ ] Bóng trong rãnh nhẹ và còn nhìn thấy texture.
- [ ] Vải phủ kín khung hình.
- [ ] Không xuất hiện mép vải, nền, vật thể, chữ hoặc watermark.

---

## 9. Ghi chú giới hạn

Model tạo ảnh có thể vẽ lại một phần texture dù prompt yêu cầu giữ nguyên. Prompt này giảm sai lệch nhưng không đảm bảo chính xác từng pixel. Với catalogue yêu cầu hoa văn tuyệt đối chính xác, nên sử dụng cloth simulation hoặc displacement trong phần mềm 3D, sau đó áp ảnh scan làm texture UV cố định.

