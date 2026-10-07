Functional Requirements
A. Tài khoản & Gói dịch vụ
FR-01 Đăng nhập & Onboarding: Đăng nhập bằng Google, kết nối Google Calendar. Onboarding hỏi: giờ làm việc trong ngày, loại việc hay làm, khung giờ làm hiệu quả nhất, mức độ hay làm lố giờ, tạo thử 1 việc.
FR-02 Gói & Thanh toán: 3 gói Miễn phí / Tiêu chuẩn / Cao cấp. Tài khoản mới tự vào gói Miễn phí. Thanh toán qua PayOS / VNPay, trả từng kỳ, không tự gia hạn, có hóa đơn. Hết kỳ không gia hạn thì về gói Miễn phí.
Quota AI đếm riêng từng loại: lượt xếp lịch, tin chat, lướt tài liệu. Tối đa 50 lượt AI mỗi ngày để chống spam. Hết quota thì khóa chức năng AI, các chức năng thủ công vẫn dùng bình thường.
B. Quản lý dự án
FR-03 Khách hàng, Dự án & Milestone:
Danh sách khách hàng (tên, liên hệ, ghi chú).
Dự án gắn với khách hàng. Có sẵn dự án "Cá nhân" cho việc không thuộc khách nào.
Dự án chia được thành milestone: Đang làm → Đã giao → Khách yêu cầu sửa → Khách duyệt (đếm số lần sửa).
Xem tiến độ và giờ dự kiến so với thực tế của cả dự án.
FR-04 Kho tài liệu: Mỗi dự án có kho riêng chứa file, link, tag, tìm theo tên.
C. Task & Lịch
FR-05 Task: Task nằm trong dự án, có thể gắn vào một milestone. Flexible bắt buộc có deadline và thời gian ước lượng. Fixed bắt buộc có giờ bắt đầu và kết thúc. Xem dạng List hoặc Kanban.
FR-06 Loại công việc: Chọn từ danh sách có sẵn (Coding, Design, Writing…), không gõ tự do, để tránh "coding" và "Coding" bị tính thành 2 loại khác nhau.
FR-07 Lịch & Log thay đổi: Lịch tuần/tháng, kéo thả, đồng bộ 2 chiều với Google Calendar. Mọi thay đổi lịch đều ghi log kèm nguồn: người dùng kéo thả, AI xếp, hay sửa từ Google.
D. Trợ lý AI
FR-08 Chat AI (2 mức):
Mức dự án: người dùng dán brief hoặc đính kèm tài liệu từ kho. AI hỏi lại cho rõ (phạm vi, số lần sửa…), rồi đề xuất milestone và task, ghi cả thời gian người dùng tự đoán lẫn thời gian đã chỉnh. Người dùng sửa qua chat, bấm Áp dụng thì mới tạo thật.
Mức task: chia task lớn thành task nhỏ. AI tự gợi ý chia khi task Flexible dài hơn 2 giờ.
FR-09 Lấy ngữ cảnh: Trước khi xếp lịch, hệ thống lấy giờ rảnh trên Google Calendar, khối lượng việc hiện tại, và các task tương tự trước đây.
FR-10 Đề xuất lịch: Code xếp giờ, AI chỉ giải thích lý do. Thứ tự xếp:
Giữ nguyên các task Fixed.
Task Flexible xếp theo deadline gần nhất trước.
Mỗi task dài bằng thời gian đã chỉnh (T_proposed).
Ưu tiên khung giờ làm hiệu quả.
Cảnh báo khi không đủ thời gian trước deadline.
FR-11 Duyệt lịch: Người dùng duyệt, sửa hoặc từ chối từng slot. Khi bấm duyệt, hệ thống kiểm tra lại Google Calendar lần cuối. Slot nào bị trùng thì báo, không ghi.
FR-12 Tóm tắt "Hôm nay": Lịch hôm nay, 3 việc nên làm trước kèm lý do, deadline đang nguy hiểm, cảnh báo từ các task tương tự trước đây. Chỉ dạng chữ.
FR-13 So sánh 3 mức cá nhân hóa (nghiên cứu): Làm sau. Từ cùng một danh sách task, tạo 3 lịch ẩn tên để người dùng chọn.
E. Theo dõi thực thi
FR-14 Nhắc & Thông báo: Nhắc trước 15 phút (đổi được). Thông báo lưu trong app, có trạng thái đã đọc / chưa đọc.
FR-15 Focus mode: Bấm Start khi bắt đầu, Stop khi xong hoặc bị ngắt, chọn lý do dừng. Một task có thể làm nhiều lần, giờ thực tế bằng tổng các lần. Có nhạc tập trung. Mỗi người chỉ chạy 1 timer cùng lúc.
FR-16 Hỏi giờ thực tế:
Hết giờ slot mà chưa bấm Stop: hỏi "Xong / Chưa xong, cần thêm bao lâu / Không làm được".
Quên bấm timer: chọn nhanh "Đúng dự kiến / +15' / +30' / Khác".
Không trả lời: đánh dấu UNKNOWN, không dùng để học.
Timer chạy quá 3 lần thời gian dự kiến thì tự dừng và hỏi lại.
Task chưa xong mà cần thêm giờ: hệ thống đề xuất dời các task sau, người dùng duyệt mới dời (xem tab Issue).
F. Học & Thống kê
FR-17 Học độ lệch (K_bias): Khi task Flexible xong, cập nhật K_bias của loại công việc đó. Mỗi lần cập nhật đều ghi log.
FR-18 Học khung giờ hiệu quả: Bắt đầu từ câu trả lời onboarding, sau đó học dần từ dữ liệu timer.
FR-19 Bộ nhớ dài hạn: Lưu tên task, lý do dừng, lý do trễ để lần sau tìm lại task tương tự. Chạy nền, không bắt người dùng chờ.
FR-20 Dashboard: Tỷ lệ hoàn thành, độ lệch theo từng loại công việc qua thời gian, giờ dự kiến so với thực tế theo task, dự án và khách hàng. Viết bằng lời dễ hiểu, ví dụ "Bạn thường cần thêm 30% thời gian cho Writing".
G. Quản trị
FR-21 Admin: Quản lý gói, giá, quota AI, người dùng, giao dịch, chi phí AI, thư viện nhạc, bật chế độ nghiên cứu.
