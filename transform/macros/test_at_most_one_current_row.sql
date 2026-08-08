{% test at_most_one_current_row(model, natural_key, current_flag) %}

-- At most one current row per natural key.
--
-- Deliberately "at most", not "exactly". An organisation that has closed has zero current
-- rows, which is correct: 391 of 668 corpus records are ref-only historic entities and 420
-- are inactive. Two or more current rows is the real failure -- it fans out every
-- current-state query -- and that is what this catches.

select
    {{ natural_key }} as natural_key,
    sum(case when {{ current_flag }} then 1 else 0 end) as current_rows
from {{ model }}
group by 1
having sum(case when {{ current_flag }} then 1 else 0 end) > 1

{% endtest %}