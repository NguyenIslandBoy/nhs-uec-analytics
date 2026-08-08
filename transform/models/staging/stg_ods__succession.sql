with source as (

    select * from {{ source('ods', 'succession') }}

)

select
    upper(trim(predecessor_code))       as predecessor_code,
    upper(trim(successor_code))         as successor_code,
    cast(legal_start_date as date)      as legal_start_date,
    upper(trim(source_code))            as source_code,
    source_direction

from source
where predecessor_code is not null
  and successor_code is not null
  and predecessor_code != successor_code