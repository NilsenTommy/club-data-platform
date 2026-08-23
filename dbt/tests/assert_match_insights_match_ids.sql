with silver_matches as (

    select match_id
    from {{ source('silver', 'matches') }}

),

gold_matches as (

    select match_id
    from {{ ref('match_insights') }}

)

select
    coalesce(silver_matches.match_id, gold_matches.match_id) as match_id
from silver_matches
full outer join gold_matches
    on silver_matches.match_id = gold_matches.match_id
where silver_matches.match_id is null
   or gold_matches.match_id is null