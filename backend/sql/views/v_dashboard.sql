-- The existing UI compares annual supply/use estimates. Other dimensions remain
-- available in v_wasde and the SQL analytics views, without arbitrary pivoting.
CREATE VIEW v_dashboard AS
SELECT * FROM v_wasde
WHERE annual_quarter_flag = 'Annual' AND reliability_projection = '';
