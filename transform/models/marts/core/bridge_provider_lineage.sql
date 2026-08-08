{{ config(materialized='table') }}

-- Many-to-many map from a historic provider to the entity its activity rolls up to.
--
-- Deliberately not a one-to-one lookup. 23 predecessors have multiple terminal successors
-- (ODS-13), and two of those split across an identical successor pair, so no single-parent
-- assignment is correct. Marts should filter `is_ambiguous = false` when producing national
-- totals; including ambiguous rows double-counts historic activity across both successors.
--
-- Every provider maps to itself at hops = 0, so a mart can join unconditionally rather than
-- coalescing between "has lineage" and "does not".

with dim as (

    select provider_sk, ods_code from {{ ref('dim_provider') }}

),

lineage as (

    select * from {{ ref('int_provider_lineage') }}

),

self_edges as (

    select
        dim.provider_sk,
        dim.ods_code,
        dim.ods_code    as reporting_entity_code,
        0               as hops,
        false           as is_ambiguous
    from dim

),

successor_edges as (

    select
        dim.provider_sk,
        dim.ods_code,
        lineage.terminal_code   as reporting_entity_code,
        lineage.hops,
        lineage.is_ambiguous
    from lineage
    inner join dim on dim.ods_code = lineage.ods_code

),

combined as (

    select * from self_edges
    union all
    select * from successor_edges

)

select
    combined.provider_sk,
    combined.ods_code,
    combined.reporting_entity_code,
    reporting.provider_sk       as reporting_entity_sk,
    reporting.org_name          as reporting_entity_name,
    combined.hops,
    combined.is_ambiguous
from combined
left join {{ ref('dim_provider') }} as reporting
    on reporting.ods_code = combined.reporting_entity_code