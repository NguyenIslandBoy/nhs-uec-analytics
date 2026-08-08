{% test no_overlapping_scd_ranges(model, natural_key, valid_from, valid_to) %}

-- A Type 2 dimension must partition each natural key's timeline into disjoint intervals.
-- Overlap means a point-in-time lookup returns more than one row, which silently fans out
-- every downstream join. This is the single most important structural test on the model.

with ordered as (

    select
        {{ natural_key }} as natural_key,
        {{ valid_from }}  as valid_from,
        {{ valid_to }}    as valid_to,
        lead({{ valid_from }}) over (
            partition by {{ natural_key }}
            order by {{ valid_from }}
        ) as next_valid_from
    from {{ model }}

)

select *
from ordered
where next_valid_from is not null
  and next_valid_from < valid_to

{% endtest %}