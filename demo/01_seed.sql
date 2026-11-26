-- =====================================================================================================================
-- METANOIA demo: TẠO DỮ LIỆU DEMO (Supabase → SQL Editor → dán cả file → Run)
-- =====================================================================================================================
-- Tạo 3 tài khoản demo và toàn bộ dữ liệu cho kịch bản trong demo/README.md.
-- Giờ giấc tính theo LÚC CHẠY (giờ Việt Nam) → chạy file này 10–30 phút TRƯỚC khi demo, tốt nhất trong 8:00–15:00.
--
-- 1. Sửa 3 email ở mục CẤU HÌNH: 3 tài khoản Google dành riêng cho demo, CHƯA từng đăng nhập Metanoia.
-- 2. Chạy cả file. Lỗi ở bất kỳ đâu → không tạo gì (một transaction).
-- 3. Demo xong: chạy demo/02_cleanup.sql (cùng 3 email) → xóa sạch. Lần sau chạy lại file này là có y như cũ.
--
-- Đăng nhập: bấm "Đăng nhập bằng Google" đúng email bên dưới. Backend nhận tài khoản tạo sẵn
-- (GoogleSubjectId 'pending:…', giống Admin tạo user) và gắn vào Google của người đó.
-- Dấu hiệu dữ liệu demo: FullName bắt đầu bằng '[Demo]'. Cleanup chỉ xóa user có dấu này → ĐỪNG đổi tên trong Hồ sơ.
-- =====================================================================================================================

-- ---------- CẤU HÌNH: sửa 3 email (giống hệt trong 02_cleanup.sql) ----------
CREATE TEMP TABLE IF NOT EXISTS demo_cfg (key text PRIMARY KEY, email text NOT NULL);
TRUNCATE demo_cfg;
INSERT INTO demo_cfg VALUES
    ('an',   'metanoia.demo.an@gmail.com'),    -- Freelancer chính, Premium, Leader nhóm (Web + Mobile)
    ('binh', 'metanoia.demo.binh@gmail.com'),  -- Thành viên nhóm, Pro (Web)
    ('chi',  'metanoia.demo.chi@gmail.com');   -- Người dùng mới, Free, chưa onboarding (Mobile + Web)

-- Ngày Việt Nam hôm nay + d
CREATE OR REPLACE FUNCTION pg_temp.demo_day(d int) RETURNS date
LANGUAGE sql STABLE AS $$ SELECT (now() AT TIME ZONE 'Asia/Ho_Chi_Minh')::date + d $$;
-- Hôm nay + d ngày, lúc h:m giờ Việt Nam
CREATE OR REPLACE FUNCTION pg_temp.demo_ts(d int, h int, m int DEFAULT 0) RETURNS timestamptz
LANGUAGE sql STABLE AS $$
    SELECT ((pg_temp.demo_day(d) + make_time(h, m, 0)) AT TIME ZONE 'Asia/Ho_Chi_Minh')
$$;
-- Bây giờ (làm tròn xuống 5 phút) + phút
CREATE OR REPLACE FUNCTION pg_temp.demo_now(minutes int) RETURNS timestamptz
LANGUAGE sql STABLE AS $$
    SELECT date_trunc('hour', now())
           + ((floor(extract(minute FROM now()) / 5) * 5)::int + minutes) * interval '1 minute'
$$;

BEGIN;

DO $$
DECLARE
    r_freelancer uuid := (SELECT "Id" FROM "Role" WHERE "Name" = 'Freelancer');
    p_free    uuid := (SELECT "Id" FROM "SubscriptionPlan" WHERE "Name" = 'Free');
    p_pro     uuid := (SELECT "Id" FROM "SubscriptionPlan" WHERE "Name" = 'Pro');
    p_premium uuid := (SELECT "Id" FROM "SubscriptionPlan" WHERE "Name" = 'Premium');
    d_coding  uuid := (SELECT "Id" FROM "Domain" WHERE lower("Name") = 'coding');
    d_design  uuid := (SELECT "Id" FROM "Domain" WHERE lower("Name") = 'design');
    d_writing uuid := (SELECT "Id" FROM "Domain" WHERE lower("Name") = 'writing');
    e_an   text := (SELECT email FROM demo_cfg WHERE key = 'an');
    e_binh text := (SELECT email FROM demo_cfg WHERE key = 'binh');
    e_chi  text := (SELECT email FROM demo_cfg WHERE key = 'chi');
    u_an uuid := gen_random_uuid();
    u_binh uuid := gen_random_uuid();
    u_chi uuid := gen_random_uuid();
    pay_an uuid := gen_random_uuid();
    c_bloom uuid := gen_random_uuid();
    c_nang uuid := gen_random_uuid();
    p_old uuid := gen_random_uuid();
    p_bloom uuid := gen_random_uuid();
    p_nang uuid := gen_random_uuid();
    p_moc uuid := gen_random_uuid();
    m1 uuid := gen_random_uuid();
    m2 uuid := gen_random_uuid();
    m3 uuid := gen_random_uuid();
    t_wire uuid := gen_random_uuid();
    t_mood uuid := gen_random_uuid();
    t_ui uuid := gen_random_uuid();
    t_about uuid := gen_random_uuid();
    t_home uuid := gen_random_uuid();
    t_form uuid := gen_random_uuid();
    t_seo uuid := gen_random_uuid();
    t_meet uuid := gen_random_uuid();
    prop uuid := gen_random_uuid();
    s_done uuid := gen_random_uuid();
    s_now uuid := gen_random_uuid();
    s_next uuid := gen_random_uuid();
    conv uuid := gen_random_uuid();
    g uuid := gen_random_uuid();
    gm1 uuid := gen_random_uuid();
    gm2 uuid := gen_random_uuid();
    g_logo uuid := gen_random_uuid();
    g_guide uuid := gen_random_uuid();
    g_photo uuid := gen_random_uuid();
    g_kick uuid := gen_random_uuid();
    gs_sketch uuid := gen_random_uuid();
    gs_vector uuid := gen_random_uuid();
    gslot_done uuid := gen_random_uuid();
    rec record;
    dom uuid;
    prof uuid;
    k numeric;
    k_new numeric;
    n int;
    t_id uuid;
BEGIN
    -- ---------- 0. Kiểm tra ----------
    IF r_freelancer IS NULL OR p_free IS NULL OR p_pro IS NULL OR p_premium IS NULL
       OR d_coding IS NULL OR d_design IS NULL OR d_writing IS NULL THEN
        RAISE EXCEPTION 'Thiếu dữ liệu gốc (Role Freelancer, gói Free/Pro/Premium, Domain Coding/Design/Writing).';
    END IF;
    IF EXISTS (SELECT 1 FROM "User" u JOIN demo_cfg c ON lower(u."Email") = lower(c.email)) THEN
        RAISE EXCEPTION 'Đã có user với email demo (%). Chạy demo/02_cleanup.sql trước; nếu đó là tài khoản THẬT thì đổi email demo.',
            (SELECT string_agg(u."Email", ', ') FROM "User" u JOIN demo_cfg c ON lower(u."Email") = lower(c.email));
    END IF;

    -- ---------- 1. Người dùng (chờ đăng nhập Google lần đầu, giống Admin tạo sẵn) ----------
    INSERT INTO "User" ("Id", "RoleId", "Email", "FullName", "GoogleSubjectId", "TimeZone", "WorkDayStart", "WorkDayEnd",
                        "WorksOnWeekend", "DefaultBufferMinutes", "ReminderLeadMinutes", "OnboardingCompleted",
                        "CreatedAt", "ProductiveBlocks")
    VALUES
        -- Làm cả cuối tuần: ngày nào demo cũng có lịch
        (u_an, r_freelancer, e_an, '[Demo] An Nguyễn', 'pending:demo-' || replace(gen_random_uuid()::text, '-', ''),
         'Asia/Ho_Chi_Minh', '08:00', '18:00', true, 15, 15, true, now() - interval '35 days',
         ARRAY['morning', 'afternoon']),
        (u_binh, r_freelancer, e_binh, '[Demo] Bình Trần', 'pending:demo-' || replace(gen_random_uuid()::text, '-', ''),
         'Asia/Ho_Chi_Minh', '08:00', '18:00', true, 15, 15, true, now() - interval '10 days', ARRAY['afternoon']),
        (u_chi, r_freelancer, e_chi, '[Demo] Chi Lê', 'pending:demo-' || replace(gen_random_uuid()::text, '-', ''),
         'Asia/Ho_Chi_Minh', '08:00', '18:00', false, 15, 15, false, now(), NULL);

    -- ---------- 2. Gói ----------
    -- An: Free (đã hủy) → mua Premium qua PayOS 5 ngày trước, có hóa đơn
    INSERT INTO "PaymentTransaction" ("Id", "UserId", "PlanId", "OrderCode", "Gateway", "Amount", "Status",
                                      "CreatedAt", "ExpiresAt", "PaidAt", "WebhookReceivedAt")
    VALUES (pay_an, u_an, p_premium, 900000000000000 + (random() * 99999999999)::bigint, 'PayOS', 149000,
            'Succeeded', now() - interval '5 days', now() - interval '5 days' + interval '15 minutes',
            now() - interval '5 days' + interval '2 minutes', now() - interval '5 days' + interval '2 minutes');
    INSERT INTO "Invoice" ("PaymentTransactionId", "InvoiceNumber", "PlanName", "Amount", "BillingName", "BillingEmail",
                           "PeriodStart", "PeriodEnd", "IssuedAt")
    VALUES (pay_an, 'DEMO-' || to_char(now(), 'YYYYMMDD') || '-' || substr(replace(pay_an::text, '-', ''), 1, 8),
            'Premium', 149000, '[Demo] An Nguyễn', e_an, pg_temp.demo_day(-5), pg_temp.demo_day(25),
            now() - interval '5 days' + interval '2 minutes');
    INSERT INTO "UserSubscription" ("UserId", "PlanId", "PaymentTransactionId", "StartAt", "EndAt", "Status",
                                    "AiCallsUsed", "QuotaResetAt", "CreatedAt")
    VALUES
        (u_an, p_free, NULL, now() - interval '35 days', now() - interval '5 days', 'Cancelled', 9,
         now() - interval '35 days', now() - interval '35 days'),
        (u_an, p_premium, pay_an, now() - interval '5 days', now() + interval '25 days', 'Active', 14,
         now() - interval '5 days', now() - interval '5 days'),
        (u_binh, p_pro, NULL, now() - interval '10 days', now() + interval '20 days', 'Active', 3,
         now() - interval '10 days', now() - interval '10 days'),
        (u_chi, p_free, NULL, now(), NULL, 'Active', 0, now(), now());  -- như tài khoản vừa tạo

    -- ---------- 3. Giờ hiệu quả (24 dòng / người) ----------
    -- An (Premium) đã học từ timer: cao nhất 9–11 giờ sáng và 14–15 giờ chiều
    INSERT INTO "UserCircadianProfile" ("UserId", "HourOfDay", "Score", "Source", "PriorScore", "UpdatedAt")
    SELECT u_an, h,
           CASE WHEN h IN (9, 10) THEN 0.880 WHEN h = 11 THEN 0.760 WHEN h IN (14, 15) THEN 0.720
                WHEN h IN (8, 13, 16) THEN 0.560 WHEN h = 17 THEN 0.450
                WHEN h BETWEEN 6 AND 21 THEN 0.350 ELSE 0.150 END,
           CASE WHEN h BETWEEN 8 AND 17 THEN 'Learned' ELSE 'Onboarding' END,
           CASE WHEN h BETWEEN 8 AND 16 THEN 0.700 ELSE 0.500 END,
           now() - interval '1 day'
    FROM generate_series(0, 23) h;
    INSERT INTO "UserCircadianProfile" ("UserId", "HourOfDay", "Score", "Source", "PriorScore", "UpdatedAt")
    SELECT u, h, 0.500, 'Onboarding', 0.500, now() FROM unnest(ARRAY[u_binh, u_chi]) u, generate_series(0, 23) h;

    -- ---------- 4. Khách hàng ----------
    INSERT INTO "Client" ("Id", "UserId", "Name", "ContactEmail", "Phone", "Notes", "CreatedAt") VALUES
        (c_bloom, u_an, 'Bloom Coffee', 'owner@bloomcoffee.example', '0901000111',
         'Quán cà phê 2 chi nhánh, muốn website giới thiệu + đặt bàn online.', now() - interval '4 days'),
        (c_nang, u_an, 'Studio Nắng', 'hello@studionang.example', NULL,
         'Studio ảnh cưới, cần landing page chạy quảng cáo.', now() - interval '2 days');

    -- ---------- 5. Lịch sử: dự án cũ đã xong (AI học thói quen ước lượng, có "việc tương tự") ----------
    INSERT INTO "Project" ("Id", "UserId", "ClientId", "Name", "Description", "StartDate", "Deadline", "Status", "CreatedAt")
    VALUES (p_old, u_an, NULL, 'Portfolio cá nhân', 'Website portfolio và blog cá nhân (đã xong).',
            pg_temp.demo_day(-32), pg_temp.demo_day(-8), 'Completed', now() - interval '32 days');

    FOR rec IN
        SELECT * FROM (VALUES
            -- Mỗi việc làm 2 buổi sáng liền nhau (~3–4 giờ/ngày): AI học được sức làm mỗi ngày (FR-10d)
            ('Thiết kế wireframe portfolio',  d_design,  240, 270, 26, 'Đổi bố cục 1 lần theo góp ý.'),
            ('Code trang chủ portfolio',      d_coding,  300, 410, 24, 'Phần responsive mất nhiều thời gian hơn dự kiến.'),
            ('Viết bài giới thiệu bản thân',  d_writing, 180, 250, 22, 'Viết lại phần mở đầu 2 lần.'),
            ('Code trang dự án và bộ lọc',    d_coding,  360, 480, 19, 'Bộ lọc theo tag bị lỗi trên Safari.'),
            ('Thiết kế trang blog',           d_design,  300, 320, 16, NULL),
            ('Viết 2 bài blog kỹ thuật',      d_writing, 300, 420, 13, 'Tìm tài liệu tham khảo khá lâu.'),
            ('Code form liên hệ gửi email',   d_coding,  240, 330, 11, 'Cấu hình SMTP bị chặn, phải đổi dịch vụ.'),
            ('Viết mô tả 5 dự án',            d_writing, 240, 310,  8, NULL),
            ('Tối ưu tốc độ tải trang',       d_coding,  180, 230,  5, 'Nén ảnh và lazy-load.')
        ) AS v(title, domain, est, act, ago, note)
    LOOP
        t_id := gen_random_uuid();
        INSERT INTO "Task" ("Id", "ProjectId", "DomainId", "Title", "TaskType", "Status", "CreatedAt", "CompletedAt",
                            "Deadline", "EstimatedMinutes")
        VALUES (t_id, p_old, rec.domain, rec.title, 'Flexible', 'Done', now() - (rec.ago + 3) * interval '1 day',
                pg_temp.demo_ts(-rec.ago, 13), pg_temp.demo_ts(-rec.ago + 1, 18), rec.est);
        -- 2 phiên bấm giờ từ 8:30 (hôm trước + hôm xong), tổng = thời gian thực tế
        INSERT INTO "FocusSession" ("TaskId", "StartedAt", "EndedAt", "StopReason", "Note", "CaptureMethod", "IsConfirmed")
        VALUES (t_id, pg_temp.demo_ts(-rec.ago - 1, 8, 30),
                pg_temp.demo_ts(-rec.ago - 1, 8, 30) + (rec.act / 2) * interval '1 minute', 'Interrupted', NULL,
                'Timer', true),
               (t_id, pg_temp.demo_ts(-rec.ago, 8, 30),
                pg_temp.demo_ts(-rec.ago, 8, 30) + (rec.act - rec.act / 2) * interval '1 minute', 'Completed', rec.note,
                'Timer', true);
        -- Bộ nhớ việc (FR-19): "việc tương tự" tìm bằng chữ ngay; vector do AI tạo khi service khởi động lại
        INSERT INTO "TaskExecutionMemory" ("TaskId", "Content", "Outcome", "EstimatedMinutes", "ActualMinutes",
                                           "EmbeddingStatus", "CreatedAt")
        VALUES (t_id,
                'title: ' || rec.title || ' | text: ' || (SELECT "Name" FROM "Domain" WHERE "Id" = rec.domain)
                    || '. Done. Interrupted 1 time.' || COALESCE(' Notes: ' || rec.note, ''),
                'Completed', rec.est, rec.act, 'Pending', pg_temp.demo_ts(-rec.ago, 13));
    END LOOP;

    -- Một chat AI cũ (cho trang "Lịch sử chat")
    INSERT INTO "ChatConversation" ("Id", "ProjectId", "TaskId", "Scope", "Title", "Status", "CreatedAt")
    SELECT conv, p_old, t."Id", 'Task', 'Chia nhỏ: Code trang dự án và bộ lọc', 'Closed', now() - interval '21 days'
    FROM "Task" t WHERE t."ProjectId" = p_old AND t."Title" = 'Code trang dự án và bộ lọc';
    INSERT INTO "ChatMessage" ("ConversationId", "Sender", "Content", "CreatedAt") VALUES
        (conv, 'User', 'Việc này 3 tiếng, chia nhỏ giúp mình với.', now() - interval '21 days'),
        (conv, 'AI', 'Mình đề xuất 3 bước: dựng danh sách dự án (60 phút), bộ lọc theo tag (60 phút), '
                     || 'kiểm tra trên điện thoại và Safari (60 phút).', now() - interval '21 days' + interval '8 seconds');

    -- ---------- 6. Dự án đang làm: Website Bloom Coffee ----------
    INSERT INTO "Project" ("Id", "UserId", "ClientId", "Name", "Description", "StartDate", "Deadline", "Status",
                           "CreatedAt", "AllowAiDocumentAccess")
    VALUES (p_bloom, u_an, c_bloom, 'Website Bloom Coffee',
            'Website giới thiệu quán, thực đơn, đặt bàn online. Khách duyệt giao diện trước khi code.',
            pg_temp.demo_day(-4), pg_temp.demo_day(14), 'Active', now() - interval '4 days', true);
    INSERT INTO "Milestone" ("Id", "ProjectId", "Name", "OrderIndex", "DueDate", "Status") VALUES
        (m1, p_bloom, 'Thiết kế giao diện', 1, pg_temp.demo_day(3), 'InProgress'),
        (m2, p_bloom, 'Lập trình website', 2, pg_temp.demo_day(10), 'Planned'),
        (m3, p_bloom, 'Bàn giao & hướng dẫn', 3, pg_temp.demo_day(14), 'Planned');

    INSERT INTO "Task" ("Id", "ProjectId", "MilestoneId", "DomainId", "Title", "Description", "TaskType", "Status",
                        "CreatedAt", "CompletedAt", "Deadline", "EstimatedMinutes", "FixedStart", "FixedEnd")
    VALUES
        (t_wire, p_bloom, m1, d_design, 'Wireframe trang chủ', NULL, 'Flexible', 'Done',
         now() - interval '4 days', pg_temp.demo_ts(-2, 11), pg_temp.demo_ts(-1, 18), 120, NULL, NULL),
        (t_mood, p_bloom, m1, d_design, 'Moodboard & bảng màu', NULL, 'Flexible', 'Done',
         now() - interval '3 days', pg_temp.demo_now(-120), pg_temp.demo_ts(1, 18), 45, NULL, NULL),
        -- Slot đang tới giờ: demo bấm giờ + "cần thêm thời gian"
        (t_ui, p_bloom, m1, d_design, 'Thiết kế UI trang thực đơn', 'Theo moodboard đã duyệt, tông nâu – kem.',
         'Flexible', 'Scheduled', now() - interval '3 days', NULL, pg_temp.demo_ts(2, 18), 50, NULL, NULL),
        (t_about, p_bloom, m1, d_writing, 'Viết nội dung trang Giới thiệu', 'Câu chuyện quán, 2 chi nhánh, giờ mở cửa.',
         'Flexible', 'Scheduled', now() - interval '3 days', NULL, pg_temp.demo_ts(1, 17), 60, NULL, NULL),
        -- Chưa xếp lịch: demo "Xếp lịch" AI
        (t_home, p_bloom, m2, d_coding, 'Code trang chủ responsive', NULL, 'Flexible', 'ToDo',
         now() - interval '2 days', NULL, pg_temp.demo_ts(4, 18), 180, NULL, NULL),
        -- Việc lớn chưa chia: demo chat AI chia nhỏ
        (t_form, p_bloom, m2, d_coding, 'Form đặt bàn online',
         'Chọn chi nhánh, ngày giờ, số người; gửi email xác nhận cho khách và cho quán.',
         'Flexible', 'ToDo', now() - interval '2 days', NULL, pg_temp.demo_ts(6, 18), 240, NULL, NULL),
        (t_seo, p_bloom, m2, d_writing, 'Tối ưu SEO cơ bản', NULL, 'Flexible', 'ToDo',
         now() - interval '2 days', NULL, pg_temp.demo_ts(8, 18), 90, NULL, NULL),
        -- Việc giờ cố định
        (t_meet, p_bloom, m1, d_design, 'Họp duyệt giao diện với khách', 'Google Meet với chủ quán.', 'Fixed', 'ToDo',
         now() - interval '3 days', NULL, NULL, NULL, pg_temp.demo_ts(1, 14), pg_temp.demo_ts(1, 15));
    INSERT INTO "SubTask" ("TaskId", "Title", "EstimatedMinutes", "OrderIndex", "Status") VALUES
        (t_home, 'Header & menu', 60, 1, 'ToDo'),
        (t_home, 'Banner + giới thiệu ngắn', 60, 2, 'ToDo'),
        (t_home, 'Footer & kiểm tra trên điện thoại', 60, 3, 'ToDo');

    -- Lịch đã duyệt hôm nay: 1 slot đã xong, 1 slot đang tới giờ, 1 slot ngay sau (sát nhau để demo dời lịch)
    INSERT INTO "ScheduleProposal" ("Id", "UserId", "Level", "Status", "IsFeasible", "Explanation", "CreatedAt", "Warnings")
    VALUES (prop, u_an, 3, 'Approved', true, 'Đã xếp 3 việc của dự án Website Bloom Coffee.',
            now() - interval '1 day', '[]'::jsonb);
    INSERT INTO "ScheduleSlot" ("Id", "TaskId", "ScheduleProposalId", "StartTime", "EndTime", "ProposedMinutes",
                                "KBiasUsed", "Explanation", "Status")
    VALUES
        (s_done, t_mood, prop, pg_temp.demo_now(-180), pg_temp.demo_now(-120), 60, 1.300,
         'Buổi sáng là giờ bạn làm tập trung nhất.', 'Done'),
        (s_now, t_ui, prop, pg_temp.demo_now(-5), pg_temp.demo_now(45), 50, 1.000,
         'Làm ngay sau moodboard khi ý tưởng còn mới.', 'Approved'),
        (s_next, t_about, prop, pg_temp.demo_now(55), pg_temp.demo_now(115), 60, 1.000,
         'Hạn ngày mai, xếp hôm nay cho chắc.', 'Approved');
    INSERT INTO "ScheduleChangeLog" ("ScheduleSlotId", "ChangeType", "Source", "NewStart", "NewEnd", "ChangedAt")
    SELECT "Id", 'Created', 'AI', "StartTime", "EndTime", now() - interval '1 day'
    FROM "ScheduleSlot" WHERE "Id" IN (s_done, s_now, s_next);
    INSERT INTO "FocusSession" ("TaskId", "ScheduleSlotId", "StartedAt", "EndedAt", "StopReason", "CaptureMethod", "IsConfirmed")
    VALUES (t_wire, NULL, pg_temp.demo_ts(-2, 8, 30), pg_temp.demo_ts(-2, 10, 50), 'Completed', 'Timer', true),
           (t_mood, s_done, pg_temp.demo_now(-180), pg_temp.demo_now(-120), 'Completed', 'Timer', true);

    -- ---------- 7. Dự án thứ 2 (để AI xếp xen kẽ dự án) ----------
    INSERT INTO "Project" ("Id", "UserId", "ClientId", "Name", "Description", "StartDate", "Deadline", "Status", "CreatedAt")
    VALUES (p_nang, u_an, c_nang, 'Landing page Studio Nắng', 'Landing page chạy quảng cáo gói chụp ảnh cưới mùa xuân.',
            pg_temp.demo_day(-2), pg_temp.demo_day(20), 'Active', now() - interval '2 days');
    INSERT INTO "Task" ("ProjectId", "DomainId", "Title", "TaskType", "Status", "CreatedAt", "Deadline", "EstimatedMinutes")
    VALUES
        (p_nang, d_writing, 'Viết copy quảng cáo', 'Flexible', 'ToDo', now() - interval '2 days', pg_temp.demo_ts(7, 18), 90),
        (p_nang, d_design, 'Thiết kế landing page', 'Flexible', 'ToDo', now() - interval '2 days', pg_temp.demo_ts(9, 18), 180),
        (p_nang, d_coding, 'Code landing page', 'Flexible', 'ToDo', now() - interval '2 days', pg_temp.demo_ts(12, 18), 240);

    -- ---------- 8. K_bias học từ các việc đã xong (công thức FR-17: α = 0.2, K đầu = 1.3, kẹp [0.5, 3.0]) ----------
    FOR dom IN SELECT DISTINCT t."DomainId" FROM "Task" t JOIN "Project" p ON p."Id" = t."ProjectId"
               WHERE p."UserId" = u_an AND t."Status" = 'Done' AND t."TaskType" = 'Flexible'
    LOOP
        prof := gen_random_uuid();
        INSERT INTO "UserDomainProfile" ("Id", "UserId", "DomainId", "KBias", "SampleCount", "UpdatedAt")
        VALUES (prof, u_an, dom, 1.300, 0, now());
        k := 1.300;
        n := 0;
        FOR rec IN
            SELECT t."Id", t."EstimatedMinutes" AS est, t."CompletedAt",
                   round(sum(extract(epoch FROM fs."EndedAt" - fs."StartedAt")) / 60)::int AS act
            FROM "Task" t JOIN "Project" p ON p."Id" = t."ProjectId"
            JOIN "FocusSession" fs ON fs."TaskId" = t."Id"
            WHERE p."UserId" = u_an AND t."DomainId" = dom AND t."Status" = 'Done' AND t."TaskType" = 'Flexible'
            GROUP BY t."Id", t."EstimatedMinutes", t."CompletedAt"
            ORDER BY t."CompletedAt"
        LOOP
            n := n + 1;
            k_new := least(3.0, greatest(0.5, round(0.2 * rec.act / rec.est + 0.8 * k, 3)));
            INSERT INTO "KBiasUpdateLog" ("UserDomainProfileId", "TaskId", "OldKBias", "NewKBias", "Alpha",
                                          "EstimatedMinutes", "ActualMinutes", "ObservedRatio", "CaptureMethod",
                                          "ConfidenceTier", "SampleIndex", "CreatedAt")
            VALUES (prof, rec."Id", k, k_new, 0.20, rec.est, rec.act, round(rec.act::numeric / rec.est, 3), 'Timer',
                    'High', n, rec."CompletedAt");
            k := k_new;
        END LOOP;
        UPDATE "UserDomainProfile" SET "KBias" = k, "SampleCount" = n WHERE "Id" = prof;
    END LOOP;

    -- ---------- 9. Thông báo của An ----------
    INSERT INTO "Notification" ("UserId", "ScheduleSlotId", "Type", "Title", "Message", "ScheduledAt", "SentAt", "IsRead", "ReadAt")
    VALUES
        (u_an, s_done, 'SlotReminder', 'Sắp đến giờ làm việc', 'Moodboard & bảng màu bắt đầu sau 15 phút.',
         pg_temp.demo_now(-195), pg_temp.demo_now(-195), true, pg_temp.demo_now(-190)),
        (u_an, s_now, 'SlotReminder', 'Sắp đến giờ làm việc', 'Thiết kế UI trang thực đơn bắt đầu sau 15 phút.',
         pg_temp.demo_now(-20), pg_temp.demo_now(-20), false, NULL),
        (u_an, NULL, 'DeadlineRisk', 'Việc sắp đến hạn', 'Viết nội dung trang Giới thiệu đến hạn ngày mai 17:00.',
         now() - interval '1 hour', now() - interval '1 hour', false, NULL);

    -- ---------- 10. Bình: một dự án cá nhân nhỏ ----------
    INSERT INTO "Project" ("Id", "UserId", "Name", "Description", "StartDate", "Deadline", "Status", "CreatedAt")
    VALUES (p_moc, u_binh, 'Bộ nhận diện Café Mộc', 'Logo, danh thiếp, menu.', pg_temp.demo_day(-6), pg_temp.demo_day(10),
            'Active', now() - interval '6 days');
    INSERT INTO "Task" ("ProjectId", "DomainId", "Title", "TaskType", "Status", "CreatedAt", "Deadline", "EstimatedMinutes")
    VALUES (p_moc, d_design, 'Thiết kế danh thiếp', 'Flexible', 'ToDo', now() - interval '6 days', pg_temp.demo_ts(5, 18), 90),
           (p_moc, d_design, 'Thiết kế menu A4', 'Flexible', 'ToDo', now() - interval '6 days', pg_temp.demo_ts(8, 18), 120);

    -- ---------- 11. Nhóm: Bloom Agency (An = Leader, Bình = Member) ----------
    INSERT INTO "WorkGroup" ("Id", "Name", "Description", "OwnerId", "StartDate", "Deadline", "Status", "CreatedAt")
    VALUES (g, 'Bloom Agency', 'Nhóm làm trọn gói nhận diện + website cho Bloom Coffee.', u_an,
            pg_temp.demo_day(-2), pg_temp.demo_day(14), 'Active', now() - interval '2 days');
    INSERT INTO "GroupMember" ("GroupId", "UserId", "Role", "JoinedAt") VALUES
        (g, u_an, 'Leader', now() - interval '2 days'),
        (g, u_binh, 'Member', now() - interval '2 days' + interval '1 hour');
    INSERT INTO "GroupMilestone" ("Id", "GroupId", "Name", "OrderIndex", "DueDate", "Status") VALUES
        (gm1, g, 'Nhận diện thương hiệu', 1, pg_temp.demo_day(5), 'InProgress'),
        (gm2, g, 'Website & hình ảnh', 2, pg_temp.demo_day(14), 'Planned');
    INSERT INTO "GroupTask" ("Id", "GroupId", "MilestoneId", "AssigneeId", "Title", "Description", "Priority", "Status",
                             "Deadline", "EstimatedMinutes", "CreatedAt", "FixedStart", "FixedEnd")
    VALUES
        (g_logo, g, gm1, u_binh, 'Thiết kế logo Bloom', 'Tông nâu – kem, dùng được trên ly và bảng hiệu.', 'High',
         'InProgress', pg_temp.demo_ts(3, 18), 240, now() - interval '2 days', NULL, NULL),
        -- Việc của An có 2 việc con của An + 1 của Bình: demo "Gợi ý giờ" cho việc nhóm
        (g_guide, g, gm1, u_an, 'Viết brand guideline', NULL, 'Medium', 'ToDo', pg_temp.demo_ts(5, 18), 180,
         now() - interval '2 days', NULL, NULL),
        -- Chưa giao ai: demo "Gợi ý người làm"
        (g_photo, g, gm2, NULL, 'Chụp & chỉnh ảnh thực đơn', '20 món, nền gỗ, ánh sáng tự nhiên.', 'Medium', 'ToDo',
         pg_temp.demo_ts(6, 18), 120, now() - interval '1 day', NULL, NULL),
        (g_kick, g, gm1, u_an, 'Họp kick-off với khách', NULL, 'High', 'ToDo', NULL, 60, now() - interval '2 days',
         pg_temp.demo_ts(1, 9), pg_temp.demo_ts(1, 10));
    INSERT INTO "GroupSubTask" ("Id", "GroupTaskId", "AssigneeId", "Title", "EstimatedMinutes", "OrderIndex", "Status",
                                "CompletedById", "CompletedAt")
    VALUES
        (gs_sketch, g_logo, u_binh, 'Phác thảo 3 hướng logo', 90, 1, 'Done', u_binh, pg_temp.demo_ts(-1, 15, 30)),
        (gs_vector, g_logo, u_binh, 'Vẽ vector logo đã chọn', 120, 2, 'InProgress', NULL, NULL),
        (gen_random_uuid(), g_logo, u_binh, 'Chọn bộ màu & font', 30, 3, 'ToDo', NULL, NULL),
        (gen_random_uuid(), g_guide, u_an, 'Phần màu sắc', 60, 1, 'ToDo', NULL, NULL),
        (gen_random_uuid(), g_guide, u_an, 'Phần typography', 60, 2, 'ToDo', NULL, NULL),
        (gen_random_uuid(), g_guide, u_binh, 'Phần ứng dụng trên ly và menu', 60, 3, 'ToDo', NULL, NULL);
    -- Bình đã làm hôm qua (có slot + bấm giờ) và đã xếp lịch ngày mai
    INSERT INTO "ScheduleSlot" ("Id", "UserId", "GroupTaskId", "GroupSubTaskId", "StartTime", "EndTime", "ProposedMinutes",
                                "IsManualOverride", "Explanation", "Status")
    VALUES
        (gslot_done, u_binh, g_logo, gs_sketch, pg_temp.demo_ts(-1, 14), pg_temp.demo_ts(-1, 15, 30), 90, true, NULL, 'Done'),
        (gen_random_uuid(), u_binh, g_logo, gs_vector, pg_temp.demo_ts(1, 14), pg_temp.demo_ts(1, 16), 120, true, NULL,
         'Approved');
    INSERT INTO "GroupFocusSession" ("GroupTaskId", "GroupSubTaskId", "UserId", "StartedAt", "EndedAt", "StopReason",
                                     "CaptureMethod", "ScheduleSlotId")
    VALUES (g_logo, gs_sketch, u_binh, pg_temp.demo_ts(-1, 14), pg_temp.demo_ts(-1, 15, 30), 'Completed', 'Timer', gslot_done);
    INSERT INTO "GroupTaskComment" ("GroupTaskId", "AuthorId", "Content", "CreatedAt") VALUES
        (g_logo, u_an, 'Bình gửi 3 hướng phác thảo trước thứ Sáu nhé, khách thích tông nâu – kem.', now() - interval '2 days'),
        (g_logo, u_binh, 'Em gửi 3 bản phác thảo rồi, khách chọn hướng 2.', now() - interval '20 hours');
    -- Bình xin lùi hạn: An duyệt / từ chối trong demo
    INSERT INTO "GroupTaskChangeRequest" ("GroupTaskId", "RequestedById", "NewDeadline", "Reason", "Status", "CreatedAt")
    VALUES (g_logo, u_binh, pg_temp.demo_ts(5, 18), 'Khách muốn xem thêm 1 phương án màu xanh rêu.', 'Pending',
            now() - interval '3 hours');
    INSERT INTO "GroupActivityLog" ("GroupId", "ActorId", "EntityType", "EntityId", "Action", "OldValue", "NewValue", "CreatedAt")
    VALUES
        (g, u_an, 'Group', g, 'Created', NULL, 'Bloom Agency', now() - interval '2 days'),
        (g, u_binh, 'Member', u_binh, 'MemberJoined', NULL, 'Member', now() - interval '2 days' + interval '1 hour'),
        (g, u_an, 'Task', g_logo, 'Created', NULL, 'Thiết kế logo Bloom', now() - interval '2 days' + interval '2 hours'),
        (g, u_an, 'Task', g_logo, 'Assigned', NULL, '[Demo] Bình Trần', now() - interval '2 days' + interval '2 hours'),
        (g, u_binh, 'SubTask', gs_sketch, 'StatusChanged', 'InProgress', 'Done', pg_temp.demo_ts(-1, 15, 30)),
        (g, u_binh, 'ChangeRequest', g_logo, 'Created', NULL, 'Xin lùi hạn', now() - interval '3 hours');
    INSERT INTO "Notification" ("UserId", "Type", "Title", "Message", "ScheduledAt", "SentAt", "IsRead", "ReadAt",
                                "GroupId", "GroupTaskId")
    VALUES
        (u_binh, 'GroupAssigned', 'Bạn được giao việc', 'An giao cho bạn: Thiết kế logo Bloom.',
         now() - interval '2 days', now() - interval '2 days', true, now() - interval '2 days', g, g_logo),
        (u_an, 'GroupChangeRequested', 'Có đề xuất đổi hạn', 'Bình đề xuất lùi hạn việc Thiết kế logo Bloom.',
         now() - interval '3 hours', now() - interval '3 hours', false, NULL, g, g_logo);

    RAISE NOTICE 'Đã tạo dữ liệu demo cho: %, %, %', e_an, e_binh, e_chi;
END $$;

COMMIT;

-- Kiểm tra nhanh (chỉ đọc)
SELECT u."FullName", u."Email", sp."Name" AS "Gói",
       (SELECT count(*) FROM "Project" p WHERE p."UserId" = u."Id") AS "Dự án",
       (SELECT count(*) FROM "Task" t JOIN "Project" p ON p."Id" = t."ProjectId" WHERE p."UserId" = u."Id") AS "Việc",
       (SELECT string_agg(d."Name" || ' ' || udp."KBias", ', ') FROM "UserDomainProfile" udp
          JOIN "Domain" d ON d."Id" = udp."DomainId" WHERE udp."UserId" = u."Id") AS "K đã học"
FROM "User" u
JOIN demo_cfg c ON lower(c.email) = lower(u."Email")
JOIN "UserSubscription" us ON us."UserId" = u."Id" AND us."Status" = 'Active'
JOIN "SubscriptionPlan" sp ON sp."Id" = us."PlanId"
ORDER BY u."CreatedAt";
