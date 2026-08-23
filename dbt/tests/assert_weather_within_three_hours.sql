select
    match_id,
    kickoff_at,
    weather_observed_at
from {{ ref('match_insights') }}
where weather_observed_at is not null
  and abs(
      unix_timestamp(weather_observed_at)
      - unix_timestamp(kickoff_at)
  ) > 10800