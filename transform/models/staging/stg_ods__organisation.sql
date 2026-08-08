with source as (

    select * from {{ source('ods', 'organisation') }}

),

typed as (

    select
        upper(trim(ods_code))                                as ods_code,
        nullif(trim(org_name), '')                           as org_name,
        status,
        org_record_class,
        is_ref_only,
        primary_role_id,
        role_ids,
        is_nhs_trust,
        is_foundation_trust,
        cast(valid_from as date)                             as valid_from,
        cast(valid_to as date)                               as valid_to,
        is_current,
        cast(legal_start_date as date)                       as legal_start_date,
        cast(legal_end_date as date)                         as legal_end_date,
        nullif(trim(post_code), '')                          as post_code,
        nullif(trim(town), '')                               as town,
        nullif(trim(county), '')                             as county,
        nullif(trim(country), '')                            as country,
        cast(last_change_date as date)                        as last_change_date

    from source

),

classified as (

    select
        *,
        case
            when is_foundation_trust then 'NHS Foundation Trust'
            when is_nhs_trust        then 'NHS Trust'
            when primary_role_id = 'RO107' then 'Care Trust'
            when primary_role_id = 'RO111' then 'Directly Managed Unit'
            when primary_role_id = 'RO179' then 'Primary Care Trust'
            else 'Other'
        end as org_type
    from typed

)

select * from classified