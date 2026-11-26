-- =====================================================================================================================
-- METANOIA demo: XÓA SẠCH DỮ LIỆU DEMO (Supabase → SQL Editor → dán cả file → Run)
-- =====================================================================================================================
-- Xóa 3 tài khoản demo và MỌI thứ gắn với họ: dữ liệu do 01_seed.sql tạo VÀ mọi thứ tạo thêm trong lúc demo
-- (dự án, việc, chat AI, lịch, bấm giờ, nhóm và mọi thứ trong nhóm do họ làm chủ, lời mời, thanh toán, hóa đơn,
-- thông báo, kết nối Google, token đăng nhập, log AI…).
--
-- An toàn:
--   - Chỉ xóa user có email ở mục CẤU HÌNH **và** FullName bắt đầu bằng '[Demo]' (do 01_seed.sql tạo).
--   - Một transaction: lỗi ở đâu thì không xóa gì.
--   - Cuối file đếm lại: mọi bảng phải còn 0 dòng của user demo, không thì báo lỗi và hoàn tác.
--
-- Không xóa được bằng SQL (làm tay nếu có dùng trong lúc demo):
--   - File đã upload vào kho tài liệu dự án (lưu trên ổ đĩa server Backend, LocalFileStorage): file này in ra đường dẫn
--     trong tab "Messages"/"Notices"; file mồ côi không ai mở được nữa, xóa trên server khi tiện.
--   - Sự kiện Metanoia đã đẩy lên Google Calendar của tài khoản demo: vào Google Calendar xóa, hoặc dùng lịch riêng cho demo.
--   - Job Hangfire đã hẹn cho slot demo: tự bỏ qua khi chạy vì slot không còn.
-- =====================================================================================================================

-- ---------- CẤU HÌNH: 3 email giống hệt trong 01_seed.sql ----------
CREATE TEMP TABLE IF NOT EXISTS demo_cfg (key text PRIMARY KEY, email text NOT NULL);
TRUNCATE demo_cfg;
INSERT INTO demo_cfg VALUES
    ('an',   'metanoia.demo.an@gmail.com'),
    ('binh', 'metanoia.demo.binh@gmail.com'),
    ('chi',  'metanoia.demo.chi@gmail.com');

BEGIN;

CREATE TEMP TABLE demo_u ON COMMIT DROP AS
SELECT u."Id" FROM "User" u JOIN demo_cfg c ON lower(c.email) = lower(u."Email")
WHERE u."FullName" LIKE '[Demo]%';

DO $$
DECLARE
    skipped text := (SELECT string_agg(u."Email", ', ') FROM "User" u JOIN demo_cfg c ON lower(c.email) = lower(u."Email")
                     WHERE u."FullName" NOT LIKE '[Demo]%');
    paths text := (SELECT string_agg(d."StoragePath", E'\n  ') FROM "ProjectDocument" d
                   JOIN "Project" p ON p."Id" = d."ProjectId"
                   WHERE p."UserId" IN (SELECT "Id" FROM demo_u) AND d."StoragePath" IS NOT NULL);
BEGIN
    IF skipped IS NOT NULL THEN
        RAISE NOTICE 'BỎ QUA (không phải user demo, FullName không bắt đầu bằng [Demo]): %', skipped;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM demo_u) THEN
        RAISE NOTICE 'Không có user demo nào để xóa.';
    END IF;
    IF paths IS NOT NULL THEN
        RAISE NOTICE E'File còn trên ổ đĩa server Backend (xóa tay khi tiện):\n  %', paths;
    END IF;
END $$;

-- Nhóm do user demo làm chủ (xóa nhóm = xóa thành viên, lời mời, giai đoạn, việc, việc con, bình luận, đề xuất,
-- bấm giờ nhóm, slot nhóm, nhật ký, chat AI nhóm)
CREATE TEMP TABLE demo_g ON COMMIT DROP AS
SELECT "Id" FROM "WorkGroup" WHERE "OwnerId" IN (SELECT "Id" FROM demo_u);
-- Gói, thanh toán, việc, hồ sơ K
CREATE TEMP TABLE demo_sub ON COMMIT DROP AS
SELECT "Id" FROM "UserSubscription" WHERE "UserId" IN (SELECT "Id" FROM demo_u);
CREATE TEMP TABLE demo_pay ON COMMIT DROP AS
SELECT "Id" FROM "PaymentTransaction" WHERE "UserId" IN (SELECT "Id" FROM demo_u);
CREATE TEMP TABLE demo_task ON COMMIT DROP AS
SELECT t."Id" FROM "Task" t JOIN "Project" p ON p."Id" = t."ProjectId" WHERE p."UserId" IN (SELECT "Id" FROM demo_u);

-- 1. Nhật ký admin về user / nhóm demo, thông báo của user demo
DELETE FROM "AdminActionLog"
WHERE "AdminId" IN (SELECT "Id" FROM demo_u)
   OR "TargetId" IN (SELECT "Id" FROM demo_u) OR "TargetId" IN (SELECT "Id" FROM demo_g);
DELETE FROM "Notification" WHERE "UserId" IN (SELECT "Id" FROM demo_u);

-- 2. Dấu vết của user demo trong nhóm của người khác (nếu có)
DELETE FROM "GroupTaskComment" WHERE "AuthorId" IN (SELECT "Id" FROM demo_u);
DELETE FROM "GroupTaskChangeRequest" WHERE "RequestedById" IN (SELECT "Id" FROM demo_u);
DELETE FROM "GroupFocusSession" WHERE "UserId" IN (SELECT "Id" FROM demo_u);
DELETE FROM "ScheduleSlot" WHERE "UserId" IN (SELECT "Id" FROM demo_u);
DELETE FROM "GroupInvitation"
WHERE "InvitedById" IN (SELECT "Id" FROM demo_u) OR lower("Email") IN (SELECT lower(email) FROM demo_cfg);
DELETE FROM "GroupActivityLog" WHERE "ActorId" IN (SELECT "Id" FROM demo_u);
DELETE FROM "GroupMember" WHERE "UserId" IN (SELECT "Id" FROM demo_u);
DELETE FROM "WorkGroup" WHERE "Id" IN (SELECT "Id" FROM demo_g);

-- 3. Việc cá nhân: bản ghi RESTRICT trước, rồi xóa dự án (kéo theo giai đoạn, việc, việc con, chat AI,
--    tài liệu, slot, lịch sử đổi giờ)
DELETE FROM "FocusSession" WHERE "TaskId" IN (SELECT "Id" FROM demo_task);
DELETE FROM "KBiasUpdateLog"
WHERE "TaskId" IN (SELECT "Id" FROM demo_task)
   OR "UserDomainProfileId" IN (SELECT "Id" FROM "UserDomainProfile" WHERE "UserId" IN (SELECT "Id" FROM demo_u));
DELETE FROM "TaskExecutionMemory" WHERE "TaskId" IN (SELECT "Id" FROM demo_task);
DELETE FROM "Project" WHERE "UserId" IN (SELECT "Id" FROM demo_u);
DELETE FROM "ScheduleProposal" WHERE "UserId" IN (SELECT "Id" FROM demo_u);
DELETE FROM "Client" WHERE "UserId" IN (SELECT "Id" FROM demo_u);

-- 4. Hồ sơ, kết nối, đăng nhập
DELETE FROM "GoogleConnection" WHERE "UserId" IN (SELECT "Id" FROM demo_u);  -- kéo theo sự kiện Google đã đồng bộ
DELETE FROM "CircadianUpdateLog"
WHERE "UserCircadianProfileId" IN (SELECT "Id" FROM "UserCircadianProfile" WHERE "UserId" IN (SELECT "Id" FROM demo_u));
DELETE FROM "UserCircadianProfile" WHERE "UserId" IN (SELECT "Id" FROM demo_u);
DELETE FROM "UserDomainProfile" WHERE "UserId" IN (SELECT "Id" FROM demo_u);
DELETE FROM "RefreshToken" WHERE "UserId" IN (SELECT "Id" FROM demo_u);
DELETE FROM "UserPushSubscriptions" WHERE "UserId" IN (SELECT "Id" FROM demo_u);

-- 5. Gói, lượt AI, thanh toán, hóa đơn
DELETE FROM "AIUsageLog" WHERE "UserSubscriptionId" IN (SELECT "Id" FROM demo_sub);
DELETE FROM "UserSubscription" WHERE "Id" IN (SELECT "Id" FROM demo_sub);
DELETE FROM "Invoice" WHERE "PaymentTransactionId" IN (SELECT "Id" FROM demo_pay);
DELETE FROM "PaymentTransaction" WHERE "Id" IN (SELECT "Id" FROM demo_pay);

-- 6. Người dùng
DELETE FROM "User" WHERE "Id" IN (SELECT "Id" FROM demo_u);

-- 7. Kiểm tra: không còn gì của user demo (sai → báo lỗi, hoàn tác toàn bộ)
DO $$
DECLARE
    left_over int;
BEGIN
    SELECT (SELECT count(*) FROM "User" WHERE "Id" IN (SELECT "Id" FROM demo_u))
         + (SELECT count(*) FROM "Project" WHERE "UserId" IN (SELECT "Id" FROM demo_u))
         + (SELECT count(*) FROM "Task" WHERE "Id" IN (SELECT "Id" FROM demo_task))
         + (SELECT count(*) FROM "ScheduleSlot" WHERE "TaskId" IN (SELECT "Id" FROM demo_task)
                                                 OR "UserId" IN (SELECT "Id" FROM demo_u))
         + (SELECT count(*) FROM "WorkGroup" WHERE "Id" IN (SELECT "Id" FROM demo_g))
         + (SELECT count(*) FROM "UserSubscription" WHERE "Id" IN (SELECT "Id" FROM demo_sub))
         + (SELECT count(*) FROM "PaymentTransaction" WHERE "Id" IN (SELECT "Id" FROM demo_pay))
         + (SELECT count(*) FROM "Notification" WHERE "UserId" IN (SELECT "Id" FROM demo_u))
      INTO left_over;
    IF left_over > 0 THEN
        RAISE EXCEPTION 'Còn % dòng của user demo, đã hoàn tác.', left_over;
    END IF;
    RAISE NOTICE 'Đã xóa sạch % user demo, % nhóm.', (SELECT count(*) FROM demo_u), (SELECT count(*) FROM demo_g);
END $$;

COMMIT;
