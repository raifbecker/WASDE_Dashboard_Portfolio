CREATE VIEW IF NOT EXISTS v_world_with_hierarchy AS
WITH RECURSIVE region_tree AS (
    SELECT id, name, parent_id, name AS full_path, 0 AS depth
    FROM regions
    WHERE parent_id IS NULL
    UNION ALL
    SELECT r.id, r.name, r.parent_id,
           rt.full_path || ' > ' || r.name,
           rt.depth + 1
    FROM regions r
    JOIN region_tree rt ON r.parent_id = rt.id
)
SELECT * FROM region_tree;
