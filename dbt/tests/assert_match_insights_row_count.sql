with silver_count as (

    select count(*) as row_count
    from {{ source('silver', 'matches') }}

),

gold_count as (

    select count(*) as row_count
    from {{ ref('match_insights') }}

)

select
    silver_count.row_count as silver_rows,
    gold_count.row_count as gold_rows
from silver_count
cross join gold_count
where silver_count.row_count <> gold_count.row_count