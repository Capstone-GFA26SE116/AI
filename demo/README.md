# Kịch bản demo METANOIA (Web + Mobile)

Cập nhật: 26/11/2026 · Người soạn: Hiếu · Dữ liệu: `01_seed.sql` (tạo) và `02_cleanup.sql` (xóa sạch) · File mẫu: `files/`

Mục tiêu là trình bày đủ các chức năng chính trong khoảng **35–40 phút**, với dữ liệu giống người dùng thật, và lần nào cũng chạy lại y như cũ:

```
01_seed.sql  →  demo theo kịch bản  →  02_cleanup.sql  →  (lần sau) 01_seed.sql …
```

---

## 0. Chuẩn bị (chỉ làm một lần)

| Cần | Ghi chú |
|---|---|
| **3 tài khoản Google riêng cho demo** | Chưa từng đăng nhập Metanoia. Ví dụ `metanoia.demo.an@gmail.com`, `…binh@…`, `…chi@…`. **Không dùng tài khoản thật**: cleanup xóa sạch mọi thứ của 3 email này |
| Sửa 3 email | Ở đầu **cả hai** file `01_seed.sql` và `02_cleanup.sql`, phải giống hệt nhau |
| 1 tài khoản Admin | Tài khoản admin sẵn có của nhóm (cho phần E). Không nằm trong dữ liệu demo |
| Thiết bị | Laptop 1 (Chrome): An · Laptop 2 hoặc cửa sổ ẩn danh: Bình · Điện thoại 1: An (app Mobile) · Điện thoại 2: Chi |
| Bản deploy | Web, Backend, AI service đều là bản mới nhất (AI có PR #58–#62) |

### Ba nhân vật

| Ai | Gói | Có sẵn sau seed | Dùng ở |
|---|---|---|---|
| **An Nguyễn**: freelancer làm web | **Premium** (đã thanh toán PayOS, có hóa đơn) | 2 khách hàng · 3 dự án (1 đã xong: 9 việc có lịch sử bấm giờ, nên AI đã học K) · lịch hôm nay 3 slot · họp với khách ngày mai 14:00–15:00 · Leader nhóm **Bloom Agency** · 3 thông báo | Web + Mobile |
| **Bình Trần**: designer | **Pro** | 1 dự án nhỏ · Member nhóm Bloom Agency · việc "Thiết kế logo Bloom" đang làm · slot nhóm ngày mai 14:00–16:00 · một đề xuất lùi hạn đang chờ An duyệt | Web |
| **Chi Lê**: người mới | **Free** | Chưa có gì, **chưa onboarding** | Mobile (+ Web khi thanh toán) |

Số liệu AI đã học cho An (sau seed): K của Writing ≈ 1,33, Coding ≈ 1,32, Design ≈ 1,24. Nghĩa là An thường cần thêm khoảng 25–35% thời gian so với ước lượng. Giờ hiệu quả của An cao nhất từ 9–11 giờ sáng và 14–15 giờ chiều.

---

## 1. Trước mỗi buổi demo (khoảng 10 phút)

1. **Chạy `01_seed.sql`** trên Supabase (SQL Editor → dán cả file → Run), **10–30 phút trước giờ demo**. Mọi giờ trong dữ liệu tính theo lúc chạy, nên nếu chạy quá sớm thì slot "đang tới giờ" sẽ bị trễ. Bảng kết quả cuối file phải có 3 dòng: An Premium, Bình Pro, Chi Free.
   - Nếu báo *"Đã có user với email demo"*: chạy `02_cleanup.sql` trước, rồi chạy lại seed.
   - Demo đẹp nhất khi chạy trong **8:00–15:00**. Sau 18:00, lịch AI xếp sẽ dời sang hôm sau (vẫn đúng, nhưng kém ấn tượng).
2. **Đăng nhập trước một lần** (bấm "Đăng nhập bằng Google" đúng email):
   - An trên Web (laptop 1) và trên app Mobile (điện thoại 1).
   - Bình trên Web (laptop 2 hoặc cửa sổ ẩn danh).
   - **Chi: KHÔNG đăng nhập trước**, để lúc demo mới thấy màn onboarding.
3. (Tùy chọn) An vào **Hồ sơ → Kết nối Google Calendar**, để lúc duyệt lịch có thể mở Google Calendar khoe sự kiện vừa tạo.
4. Mở sẵn hai file `files/brief-studio-nang.md` và `files/import-du-an.csv` trên laptop 1.

---

## 2. Kịch bản

Ký hiệu: 🗣 = câu nói khi trình bày · ✅ = kết quả phải thấy · *(FR-xx)* = chức năng trong tài liệu.

### A. Web: một ngày làm việc của An (khoảng 15 phút)

**A1. Đăng nhập và "Hôm nay"** *(FR-12)*
- An đăng nhập → mở **Briefing / Hôm nay**.
- ✅ Lịch hôm nay có 3 slot: *Moodboard & bảng màu* (đã xong), *Thiết kế UI trang thực đơn* (đang tới giờ), *Viết nội dung trang Giới thiệu* (ngay sau đó). Có danh sách việc nên làm trước và gợi ý giờ hiệu quả.
- 🗣 "Mỗi sáng Metanoia tóm tắt cho freelancer biết hôm nay làm gì, việc nào sắp trễ hạn. Phần này tính bằng code, không tốn lượt AI."

**A2. Khách hàng, dự án, giai đoạn**
- **Khách hàng** → Bloom Coffee, Studio Nắng. **Dự án** → *Website Bloom Coffee*: 3 giai đoạn, 8 việc, trong đó có việc giờ cố định *Họp duyệt giao diện với khách* (ngày mai 14:00–15:00).
- 🗣 "Việc có 2 loại: linh hoạt (AI xếp giờ, AI học thời gian thật) và cố định (họp, giữ nguyên giờ, AI không học)."

**A3. AI chia nhỏ việc và tìm việc tương tự** *(FR-08a, FR-09)*
- Mở việc **Form đặt bàn online** (4 giờ, chưa có việc con) → **Chat AI / Chia nhỏ** → gõ *"Chia nhỏ việc này giúp mình"*.
- ✅ AI đề xuất 3–5 việc con có ước lượng. AI nhắc tới **việc tương tự đã làm**: *Code form liên hệ gửi email* (ước 240 phút, thực tế 330 phút, ghi chú "SMTP bị chặn").
- Bấm **Áp dụng** → việc con xuất hiện trong việc.
- 🗣 "AI nhớ các việc An đã làm và lần trước làm lâu hơn dự kiến bao nhiêu."

**A4. AI xếp lịch tuần** *(FR-10, 10b, 10c, 10d, 10g)*
- **Lịch** → **Xếp lịch bằng AI**.
- ✅ AI xếp được tất cả việc linh hoạt chưa có lịch của 2 dự án trong 7 ngày tới:
  - không đè lên họp ngày mai 14:00–15:00 và giờ nghỉ trưa;
  - ưu tiên 9–11 giờ sáng;
  - xen kẽ việc của các dự án khác nhau, mỗi khối tối đa 3 giờ;
  - **thời lượng đã nhân K**, ví dụ việc Coding 180 phút được xếp khoảng 240 phút;
  - mỗi slot có một câu lý do;
  - có thể có cảnh báo **"ngày nhiều việc hơn mức bạn thường làm"** (AI học từ lịch sử bấm giờ).
- Bấm **Duyệt** → các slot lên lịch (và lên Google Calendar nếu đã kết nối) *(FR-11, Backend)*.
- 🗣 "Code đặt giờ; AI chỉ giải thích. An ước 3 giờ nhưng thường mất thêm 30%, nên Metanoia giữ sẵn thời gian đó."

**A5. Bấm giờ và quá giờ** *(FR-16b, FR-17)*
- Ở slot **Thiết kế UI trang thực đơn**, bấm **Bắt đầu**. Sau vài giây, bấm **Chưa xong, cần thêm thời gian** → chọn 30 phút.
- ✅ AI đề xuất kéo dài slot và **dời *Viết nội dung trang Giới thiệu*** ra sau (slot liền sau bị chồng). Không tốn lượt AI. Bấm **Đồng ý** → lịch đổi.
- Bấm **Dừng** → lý do **Hoàn thành** → việc chuyển sang Done.
- 🗣 "Quá giờ thì Metanoia không tự dời lịch: nó đề xuất, người dùng quyết định."
- ⚠️ Thời gian thật lúc demo chỉ vài phút so với ước lượng 50 phút, nên AI **không học** từ lần này. Đây là luật bỏ ngoại lệ (lệch hơn 5 lần), không phải lỗi. Muốn cho thấy K thay đổi thì xem A8.

**A6. Chat AI dự án với tài liệu** *(FR-08b-1/2/3/7)*
- **AI Copilot** (hoặc Dự án *Landing page Studio Nắng* → Chat AI) → đính kèm `files/brief-studio-nang.md` → gõ *"Mình vừa nhận brief này, bạn xem giúp"* (chế độ **Bàn bạc**).
- ✅ AI trả checklist gồm **Đã rõ / Còn thiếu / Nên hỏi khách / Rủi ro**, ví dụ: chưa có ảnh album, chưa có tên miền, cần Facebook Pixel.
- Bấm **Lên kế hoạch** → bản nháp giai đoạn, việc và việc con có ước lượng, kèm lý do → **Áp dụng & xếp lịch**.
- ✅ Việc được tạo trong dự án và có slot đề xuất ngay.

**A7. Nhập file thành nhiều dự án** *(FR-08c)*
- **Dự án → Nhập file** → chọn `files/import-du-an.csv`.
- ✅ AI tách thành 2 dự án (*App đặt lịch spa Hoa Sen*, *Blog du lịch Đà Lạt*), xem trước được → **Tạo**. Mỗi dự án có sẵn một chat AI chứa phần dữ liệu của nó.

**A8. Thống kê và giờ hiệu quả** *(FR-20, FR-18, FR-17)*
- **Thống kê** → tỷ lệ xong và xong đúng hạn. Độ lệch ước lượng theo loại việc, ví dụ *"Bạn thường cần thêm khoảng 30% thời gian cho Writing"*. Bảng dự kiến so với thực tế theo dự án.
- **Giờ hiệu quả** → biểu đồ 24 giờ, đậm nhất 9–11 giờ sáng (đã học từ bấm giờ, gói Premium).
- 🗣 "Đây là điểm khác biệt chính: Metanoia học thói quen ước lượng của từng người, theo từng loại việc."

**A9. Thông báo, lịch sử chat, gói**
- **Thông báo**: nhắc slot, việc sắp hạn, có người đề xuất đổi hạn trong nhóm.
- **Lịch sử chat**: chat cũ *"Chia nhỏ: Code trang dự án và bộ lọc"* và các chat vừa tạo. Tìm được theo nội dung.
- **Hồ sơ / Gói**: Premium, còn 25 ngày, có hóa đơn.

### B. Web: làm việc nhóm (khoảng 8 phút; An ở laptop 1, Bình ở laptop 2)

**B1. An (Leader) mở nhóm Bloom Agency**
- ✅ Thấy 2 giai đoạn, 4 việc, tiến độ, nhật ký hoạt động và bình luận trên *Thiết kế logo Bloom*.

**B2. Duyệt đề xuất đổi hạn**
- An mở đề xuất của Bình (lùi hạn *Thiết kế logo Bloom* vì khách muốn thêm phương án màu) → **Duyệt**.
- ✅ Bên Bình nhận thông báo kết quả.

**B3. AI gợi ý người làm** *(FR-23)*
- Việc **Chụp & chỉnh ảnh thực đơn** (chưa giao ai) → **Gợi ý người làm**.
- ✅ AI xếp hạng An và Bình, có lý do kèm số giờ rảnh và khung lịch trống cụ thể. AI đã trừ lịch cá nhân, lịch nhóm và việc nhóm đang ôm. Chọn một người → **Giao**.

**B4. Chat AI nhóm** *(FR-24)*
- Tab **Chat AI** → gõ *"Khách muốn thêm bộ ảnh quảng cáo cho mạng xã hội: 10 ảnh món mới, 3 video ngắn, xong trước khi website ra mắt"* (Bàn bạc) → **Lên kế hoạch**.
- ✅ Bản nháp có giai đoạn và việc; **mỗi việc có sẵn người được gợi ý** kèm lý do. Đổi người ở dropdown nếu muốn → **Áp dụng** → việc xuất hiện trong nhóm.

**B5. Bình (Member) làm việc được giao**
- Bình: **Thông báo** (được giao việc) → **Việc nhóm của tôi** → *Thiết kế logo Bloom* → việc con *Vẽ vector logo đã chọn*.
- ✅ Lịch của Bình có slot nhóm ngày mai 14:00–16:00. Bình bấm **Bắt đầu** ở việc con → **Dừng** → giờ làm được ghi theo người.
- Bình viết bình luận → An thấy ngay.

**B6. (Tùy chọn) Mời Chi vào nhóm**
- An mời email của Chi với vai trò Viewer. Sau phần D, Chi mở Web → **Lời mời** → Chấp nhận.

### C. Mobile: An trên điện thoại (khoảng 6 phút)

**C1. Trang chủ** *(FR-12)* → thẻ **Hôm nay**: lịch, việc nên làm, nhắc hạn (cùng dữ liệu với Web).

**C2. Lịch và bấm giờ** *(FR-16b)* → mở slot *Viết nội dung trang Giới thiệu* → **Bắt đầu** → màn bấm giờ → **cần thêm thời gian** → bảng đề xuất kéo dài hoặc dời lịch → **Dừng**.

**C3. Dự án** → *Landing page Studio Nắng* → việc **Code landing page** → **AI chia nhỏ** → áp dụng *(FR-08a)*. **Copilot** → chat dự án *(FR-08b)*.

**C4. Trợ lý giọng nói** *(FR-22, 22b, 22c; chỉ Premium)* → bấm quả cầu, nói:
1. *"Hôm nay tôi có lịch gì?"* → đọc lịch hôm nay.
2. *"Việc nào sắp trễ hạn?"* → đọc việc có nguy cơ trễ.
3. *"Tôi hay ước lượng sai loại việc nào?"* → *"Writing, thường cần thêm khoảng 30%"*.
4. *"Bắt đầu hẹn giờ việc Code landing page"* → trợ lý **hỏi lại để xác nhận** → nói *"Có"* → bộ hẹn giờ chạy.
5. *"Dừng hẹn giờ"* → xác nhận → dừng.
6. *"Chiều nay từ 3 giờ đến 4 giờ tôi có việc gấp"* (chọn khung giờ đang có slot trên lịch) *(FR-22d)* → thẻ đề xuất dời các slot bị chiếm (chỉ dời slot Metanoia, không đụng sự kiện Google) → *"Đồng ý"* → lịch đổi, có nút **Hoàn tác**.
- 🗣 "Câu hỏi quen thuộc được trả lời ngay trên máy, không tốn lượt AI. Lệnh nào làm thay đổi dữ liệu cũng phải xác nhận."

**C5. Thông báo** → giống Web (nhắc slot, đề xuất của nhóm).

### D. Mobile: Chi, người dùng mới gói Free (khoảng 4 phút)

**D1. Đăng nhập lần đầu → Onboarding**: giờ làm việc, có làm cuối tuần không, buổi làm hiệu quả, thời gian nghỉ giữa việc → vào app trống → tạo dự án đầu tiên và 1–2 việc.

**D2. Giới hạn gói Free** *(plan gates)*:
- Mở chat AI dự án → ✅ thông báo *chat AI có ở gói Pro / Premium* kèm nút nâng cấp.
- Mở trợ lý giọng nói → ✅ báo chỉ có ở gói Premium.
- 🗣 "Gói Free vẫn được học thói quen ước lượng, nhưng chỉ hiện dưới dạng gợi ý."

**D3. Nâng cấp gói** (PayOS): **Bảng giá** → **Pro** → trang PayOS.
- Muốn cho thấy thanh toán thành công: quét mã và trả **2.000đ** (giá Pro hiện tại trong DB) → quay về app → gói Pro → chat AI mở khóa.
- Không muốn trả tiền: bấm **Hủy** trên PayOS → ✅ app báo đã hủy, gói vẫn Free.

### E. Web: Admin (khoảng 3 phút, tài khoản admin của nhóm)

- **Tổng quan / Doanh thu**: có giao dịch Premium 149.000đ của An (5 ngày trước), và giao dịch Pro của Chi nếu D3 đã trả.
- **Người dùng**: 3 user [Demo] với gói của họ.
- **Nhóm**: Bloom Agency, thành viên, tiến độ. Lưu trữ hoặc khôi phục bắt buộc nhập lý do.
- **Nhật ký**: mỗi thao tác admin có lý do, giá trị cũ và mới.

### Chưa có trên app (chỉ demo qua Swagger của AI nếu hội đồng hỏi)

| Chức năng | API (AI service) | Ghi chú |
|---|---|---|
| Gợi ý giờ làm việc nhóm *(FR-25)* | `POST /groups/tasks/{id}/slot-suggestions` | An gọi cho *Viết brand guideline*: AI gợi ý 2 khung cho *Phần màu sắc* và *Phần typography* (phần của Bình không có) |

Cách gọi: mở `<AI_URL>/docs` → **Authorize** → dán access token của An (Web: DevTools → Application → access token) → **Try it out**.

---

## 3. Sau demo

1. **Chạy `02_cleanup.sql`** (cùng 3 email). Tab Messages phải có thông báo `Đã xóa sạch 3 user demo, 1 nhóm` (nếu B4 tạo thêm nhóm thì số nhóm khác đi).
   - Script xóa **mọi thứ** của 3 user demo: dữ liệu seed và mọi thứ tạo thêm trong lúc demo (dự án nhập từ file, chat, lịch, bấm giờ, thanh toán của Chi, lời mời, thông báo, kết nối Google, log AI…).
   - Script tự kiểm tra lại và **tự hoàn tác** nếu còn sót.
2. **Làm tay** (SQL không xóa được):
   - **Google Calendar** của tài khoản An (nếu đã kết nối): xóa các sự kiện Metanoia đã tạo.
   - **File tài liệu dự án**: nếu đã upload file vào *Tài liệu dự án*, file nằm trên ổ đĩa server Backend; cleanup in đường dẫn trong tab Messages. Sau cleanup không ai mở được file đó nữa, xóa trên server khi tiện. File đính kèm thẳng vào chat (như A6) không lưu thành file.
3. Lần sau: chạy lại `01_seed.sql`, mọi thứ y như cũ. Đăng nhập Google lại bình thường, vì cleanup xóa user nên seed tạo lại tài khoản chờ đăng nhập.

---

## 4. Chức năng ↔ bước trong kịch bản

| Chức năng | Web | Mobile |
|---|---|---|
| Đăng nhập Google, onboarding | A1 | D1 |
| Khách hàng, dự án, giai đoạn, việc, việc con | A2 | C3, D1 |
| AI chia nhỏ việc + việc tương tự (FR-08a, FR-09) | A3 | C3 |
| Chat AI dự án, đọc tài liệu, Bàn bạc → Kế hoạch → Áp dụng & xếp lịch (FR-08b) | A6 | C3 |
| Nhập file thành nhiều dự án (FR-08c) | A7 | — |
| AI xếp lịch, nghỉ trưa, rải việc, cảnh báo ngày nặng (FR-10…10g) + duyệt lịch (FR-11) | A4 | — |
| Bấm giờ, quá giờ, đề xuất dời lịch (FR-16b) | A5 | C2 |
| Học K_bias (FR-17) | A8 (kết quả) | C4 câu 3 |
| Briefing "Hôm nay" (FR-12) | A1 | C1 |
| Giờ hiệu quả (FR-18), Thống kê (FR-20) | A8 | — |
| Trợ lý giọng nói + lệnh có xác nhận, chèn việc gấp (FR-22, 22b, 22c, 22d) | — | C4 |
| Nhóm: vai trò, giao việc, bình luận, đổi hạn, bấm giờ nhóm, slot nhóm | B1–B5 | — |
| AI gợi ý người làm (FR-23), chat AI nhóm (FR-24) | B3, B4 | — |
| Gói, giới hạn gói, PayOS, hóa đơn | A9, D3 | D2, D3 |
| Thông báo | A9, B5 | C5 |
| Admin: doanh thu, user, nhóm, nhật ký | E | — |

---

## 5. Sự cố thường gặp

| Hiện tượng | Cách xử lý |
|---|---|
| Seed báo *"Đã có user với email demo"* | Chạy `02_cleanup.sql` rồi chạy lại seed. Nếu cleanup báo **BỎ QUA** một email, đó là tài khoản thật: đổi email demo |
| Đăng nhập báo *"Email này đã được đăng ký bằng phương thức khác"* | Email đó đã có tài khoản thật, không phải tài khoản seed: dùng email demo khác |
| Slot "đang tới giờ" đã trôi qua | Seed chạy quá sớm: chạy cleanup rồi seed lại (khoảng 10 giây) |
| Chi không thấy màn onboarding | Chi đã đăng nhập trước buổi demo: cleanup + seed lại |
| AI trả "AI đang bận" (502) | Gemini lỗi tạm thời, không mất lượt: bấm thử lại. Xếp lịch (A4), quá giờ (A5, C2), briefing, thống kê không cần Gemini |
| An không có trợ lý giọng nói | Kiểm tra gói Premium còn hạn (seed: còn 25 ngày) |
| Kế hoạch AI nhóm hoặc dự án khác lần trước | Bình thường: chữ do AI viết thay đổi mỗi lần. Phần do code tính (lịch, quá giờ, xếp hạng người làm, thống kê) giữ nguyên |
