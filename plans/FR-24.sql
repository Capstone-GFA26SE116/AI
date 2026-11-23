-- FR-24 (AI repo): chat AI của nhóm dùng lại ChatConversation / ChatMessage.
-- Backend (Việt) chạy file này trên Supabase. AI KHÔNG tự chạy ALTER (CLAUDE.md §8).
-- Không đổi dữ liệu cũ: mọi chat hiện có vẫn là Project / Task và vẫn có ProjectId.

BEGIN;

-- 1. Cột liên kết tới nhóm; xóa nhóm thì xóa luôn chat của nhóm.
ALTER TABLE "ChatConversation" ADD COLUMN IF NOT EXISTS "GroupId" uuid;
ALTER TABLE "ChatConversation"
    ADD CONSTRAINT "ChatConversation_GroupId_fkey"
    FOREIGN KEY ("GroupId") REFERENCES "WorkGroup" ("Id") ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS "IX_ChatConversation_GroupId" ON "ChatConversation" ("GroupId");

-- 2. Chat nhóm không thuộc dự án cá nhân nào.
ALTER TABLE "ChatConversation" ALTER COLUMN "ProjectId" DROP NOT NULL;

-- 3. Thêm loại chat 'Group' và luật đi kèm (thay 2 CHECK cũ về Scope).
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT conname FROM pg_constraint
             WHERE conrelid = '"ChatConversation"'::regclass AND contype = 'c'
               AND pg_get_constraintdef(oid) LIKE '%Scope%'
    LOOP
        EXECUTE format('ALTER TABLE "ChatConversation" DROP CONSTRAINT %I', r.conname);
    END LOOP;
END $$;

ALTER TABLE "ChatConversation" ADD CONSTRAINT "CK_ChatConversation_Scope"
    CHECK ("Scope" IN ('Project', 'Task', 'Group'));
ALTER TABLE "ChatConversation" ADD CONSTRAINT "CK_ChatConversation_ScopeLinks" CHECK (
       ("Scope" = 'Task'    AND "ProjectId" IS NOT NULL AND "TaskId" IS NOT NULL AND "GroupId" IS NULL)
    OR ("Scope" = 'Project' AND "ProjectId" IS NOT NULL AND "TaskId" IS NULL     AND "GroupId" IS NULL)
    OR ("Scope" = 'Group'   AND "ProjectId" IS NULL     AND "TaskId" IS NULL     AND "GroupId" IS NOT NULL)
);

COMMIT;

-- Kiểm tra (chỉ đọc):
-- SELECT column_name, is_nullable FROM information_schema.columns
--  WHERE table_name = 'ChatConversation' ORDER BY ordinal_position;
-- SELECT pg_get_constraintdef(oid) FROM pg_constraint
--  WHERE conrelid = '"ChatConversation"'::regclass AND contype IN ('c', 'f');
