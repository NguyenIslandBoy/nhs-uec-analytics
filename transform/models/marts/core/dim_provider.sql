{{ config(materialized='table') }}

-- Type 2 provider dimension.
--
-- Validity comes from the ODS Operational date entry rather than from observed change
-- over time: ODS publishes no historical extracts, so a dbt snapshot started today would
-- yield no history at all (ADR-0002). Every record carries an Operational date, and the
-- presence of an End matches Status='Inactive' exactly across the corpus.
--
-- Each organisation currently produces exactly one version row. The surrogate key is
-- built on (ods_code, valid_from) so that additional versions -- from the supplementary
-- snapshot capturing undated drift -- slot in without a redesign.

with organisation as (

    select * from {{ ref('stg_ods__organisation') }}

),

lineage_summary as (

    select
        ods_code,
        max(is_ambiguous)                                            as is_ambiguous,
        min(case when not is_ambiguous then terminal_code end)       as terminal_code
    from {{ ref('int_provider_lineage') }}
    group by 1

)

select
    {{ dbt_utils.generate_surrogate_key(['organisation.ods_code', 'organisation.valid_from']) }}
        as provider_sk,

    organisation.ods_code,
    organisation.org_name,
    organisation.org_type,
    organisation.status,
    organisation.primary_role_id,
    organisation.is_nhs_trust,
    organisation.is_foundation_trust,
    organisation.is_ref_only,

    organisation.valid_from,
    organisation.valid_to,
    organisation.is_current,

    organisation.post_code,
    organisation.town,
    organisation.county,
    organisation.country,

    -- Lineage summary. Full edges live in bridge_provider_lineage; these columns answer
    -- "was this provider superseded, and is the successor unambiguous" without a join.
    lineage_summary.ods_code is not null                    as has_successor,
    coalesce(lineage_summary.is_ambiguous, false)           as has_ambiguous_lineage,
    lineage_summary.terminal_code                           as terminal_successor_code,

    organisation.last_change_date

from organisation
left join lineage_summary
    on lineage_summary.ods_code = organisation.ods_code